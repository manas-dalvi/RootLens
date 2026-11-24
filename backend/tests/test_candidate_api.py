from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from opensearchpy.exceptions import ConnectionError, NotFoundError

from app.main import app
from app.schemas.incident import (
    IncidentCandidateRequest,
    IncidentCandidateResponse,
    IncidentCandidatesResponse,
)

client = TestClient(app)


# ============================================================================
# 1. Pydantic Schema Validation Tests
# ============================================================================


def test_incident_candidate_request_defaults():
    """Verify default values and valid instantiation of candidate request schema."""
    req = IncidentCandidateRequest()
    assert req.window_minutes == 15
    assert req.cascade_window_seconds == 120
    assert req.max_logs == 1000
    assert req.start_time is None
    assert req.service is None


def test_incident_candidate_request_validation_limits():
    """Verify constraint boundaries on window_minutes and max_logs."""
    with pytest.raises(Exception):
        IncidentCandidateRequest(window_minutes=0)

    with pytest.raises(Exception):
        IncidentCandidateRequest(window_minutes=2000)

    with pytest.raises(Exception):
        IncidentCandidateRequest(cascade_window_seconds=0)

    with pytest.raises(Exception):
        IncidentCandidateRequest(max_logs=10000)


# ============================================================================
# 2. Candidate Evaluation API Input Validation & Error Handling
# ============================================================================


def test_candidate_api_start_time_after_end_time():
    """Verify HTTP 400 when start_time is greater than end_time."""
    start = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 10, 13, 0, tzinfo=timezone.utc)

    payload = {
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
    }
    resp = client.post("/api/v1/incidents/candidates", json=payload)
    assert resp.status_code == 400
    assert "start_time must be earlier than or equal to end_time" in resp.json()["detail"]


def test_candidate_api_opensearch_unavailable():
    """Verify HTTP 503 when OpenSearch cluster ping fails."""
    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = False
        mock_get_client.return_value = mock_client

        resp = client.post("/api/v1/incidents/candidates", json={})
        assert resp.status_code == 503
        assert "OpenSearch cluster is currently unavailable" in resp.json()["detail"]


def test_candidate_api_opensearch_connection_error():
    """Verify HTTP 503 when OpenSearch search raises ConnectionError."""
    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.side_effect = ConnectionError("Connection refused")
        mock_get_client.return_value = mock_client

        resp = client.post("/api/v1/incidents/candidates", json={})
        assert resp.status_code == 503
        assert "OpenSearch cluster is currently unavailable" in resp.json()["detail"]


def test_candidate_api_not_found_index_returns_empty():
    """Verify HTTP 200 with zero candidates when log index does not exist yet."""
    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.side_effect = NotFoundError(404, "index_not_found_exception")
        mock_get_client.return_value = mock_client

        resp = client.post("/api/v1/incidents/candidates", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_candidates"] == 0
        assert data["candidates"] == []


def test_candidate_api_empty_hits_returns_empty():
    """Verify HTTP 200 with zero candidates when no error logs match."""
    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.return_value = {"hits": {"hits": []}}
        mock_get_client.return_value = mock_client

        resp = client.post("/api/v1/incidents/candidates", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_candidates"] == 0
        assert data["candidates"] == []


# ============================================================================
# 3. Candidate Evaluation Execution & Telemetry Correlation Tests
# ============================================================================


def test_candidate_api_evaluates_trace_linked_incident():
    """Verify genuine candidate reconstruction from simulated multi-service trace-linked logs."""
    mock_hits = [
        # Upstream database error in payment-service at 14:00:00
        {
            "_id": "log-p1",
            "_source": {
                "timestamp": "2026-10-10T14:00:00Z",
                "service_name": "payment-service",
                "environment": "production",
                "log_level": "ERROR",
                "message": "Connection to PostgreSQL database timed out after 30s",
                "host": "payment-host-1",
                "trace_id": "trace-shared-100",
                "span_id": "span-p1",
                "raw_log": '{"service": "payment-service", "error": "timeout"}',
            },
        },
        # Downstream cascade in api-gateway at 14:00:15
        {
            "_id": "log-g1",
            "_source": {
                "timestamp": "2026-10-10T14:00:15Z",
                "service_name": "api-gateway",
                "environment": "production",
                "log_level": "ERROR",
                "message": "HTTP 504 Gateway Timeout forwarding /checkout",
                "host": "gateway-edge-1",
                "trace_id": "trace-shared-100",
                "span_id": "span-g1",
                "raw_log": '{"service": "api-gateway", "status": 504}',
            },
        },
    ]

    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.return_value = {"hits": {"hits": mock_hits}}
        mock_get_client.return_value = mock_client

        resp = client.post(
            "/api/v1/incidents/candidates",
            json={"cascade_window_seconds": 120, "window_minutes": 15},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_candidates"] == 1
        assert data["total_affected_services"] == 2

        cand = data["candidates"][0]
        assert "payment-service" in cand["title"]
        assert "TRACE_LINKED" in cand["correlation_rules_used"]
        assert cand["is_root_cause_confirmed"] is False
        assert "earliest observed failure" in cand["uncertainty_reason"].lower()

        # Verify dynamic, evidence-grounded uncertainty statement
        assert "Diagnostic uncertainty: Low-to-Moderate" in cand["uncertainty_reason"]
        assert "Shared trace identifiers connect the involved services/events" in cand["uncertainty_reason"]
        assert "Earliest observed failure identified chronologically in service 'payment-service'" in cand["uncertainty_reason"]
        assert "chronological earliest failure is not confirmed root cause" in cand["uncertainty_reason"]

        # Verify structured assessment exposure
        assert cand["assessment"] is not None
        ass = cand["assessment"]
        assert ass["classification"] == "TRACE_LINKED"
        assert ass["is_root_cause_confirmed"] is False
        assert ass["has_shared_trace"] is True
        assert "trace-shared-100" in ass["trace_ids_found"]
        assert ass["distinct_service_count"] == 2
        assert ass["is_timeline_temporally_consistent"] is True
        assert len(ass["observed_facts"]) >= 1
        assert len(ass["inferred_hypotheses"]) >= 1
        assert ass["inferred_hypotheses"][0].startswith("[Hypothesis]")
        assert len(ass["supporting_signals"]) >= 1
        assert "Low-to-Moderate" in ass["uncertainty_statement"]

        # Check chronological timeline
        assert len(cand["timeline_events"]) == 2
        ev1 = cand["timeline_events"][0]
        assert ev1["service_name"] == "payment-service"
        assert ev1["is_earliest_observed_failure"] is True
        assert ev1["propagation_lag_seconds"] == 0.0
        assert ev1["event_type"] == "FIRST_ERROR"

        ev2 = cand["timeline_events"][1]
        assert ev2["service_name"] == "api-gateway"
        assert ev2["is_earliest_observed_failure"] is False
        assert ev2["propagation_lag_seconds"] == 15.0
        assert ev2["event_type"] == "CASCADING_FAILURE"

        # Check evidence provenance
        assert len(cand["evidence_items"]) >= 2
        log_ids = [e["log_id"] for e in cand["evidence_items"]]
        assert "log-p1" in log_ids
        assert "log-g1" in log_ids


def test_candidate_api_evaluates_temporal_cascade_incident():
    """Verify candidate reconstruction from simulated cross-service cascade without shared traces."""
    mock_hits = [
        # Upstream database error in payment-service at 14:00:00 (no trace_id)
        {
            "_id": "log-p2",
            "_source": {
                "timestamp": "2026-10-10T14:00:00Z",
                "service_name": "payment-service",
                "environment": "production",
                "log_level": "ERROR",
                "message": "Connection pool exhausted",
                "host": "payment-host-1",
                "trace_id": None,
                "span_id": None,
                "raw_log": '{"service": "payment-service", "error": "pool_exhausted"}',
            },
        },
        # Downstream cascade in api-gateway at 14:00:20 (no trace_id)
        {
            "_id": "log-g2",
            "_source": {
                "timestamp": "2026-10-10T14:00:20Z",
                "service_name": "api-gateway",
                "environment": "production",
                "log_level": "ERROR",
                "message": "Upstream service error during payment checkout",
                "host": "gateway-edge-1",
                "trace_id": None,
                "span_id": None,
                "raw_log": '{"service": "api-gateway", "status": 502}',
            },
        },
    ]

    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.return_value = {"hits": {"hits": mock_hits}}
        mock_get_client.return_value = mock_client

        resp = client.post(
            "/api/v1/incidents/candidates",
            json={"cascade_window_seconds": 120, "window_minutes": 15},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_candidates"] == 1
        assert data["total_affected_services"] == 2

        cand = data["candidates"][0]
        assert "payment-service" in cand["title"]
        assert "TEMPORAL_CASCADE" in cand["correlation_rules_used"]
        assert cand["is_root_cause_confirmed"] is False

        # Verify dynamic, evidence-grounded uncertainty statement retaining required chronological wording
        assert "Earliest observed failure identified chronologically in service 'payment-service'" in cand["uncertainty_reason"]
        assert "Diagnostic uncertainty: Moderate" in cand["uncertainty_reason"]
        assert "lacks shared distributed trace context" in cand["uncertainty_reason"]
        assert "cannot be conclusively proven as causal cascades" in cand["uncertainty_reason"]

        # Verify structured assessment exposure
        assert cand["assessment"] is not None
        ass = cand["assessment"]
        assert ass["classification"] == "TEMPORAL_ONLY"
        assert ass["is_root_cause_confirmed"] is False
        assert ass["has_shared_trace"] is False
        assert ass["distinct_service_count"] == 2
        assert ass["is_timeline_temporally_consistent"] is True
        assert len(ass["inferred_hypotheses"]) >= 1
        assert ass["inferred_hypotheses"][0].startswith("[Hypothesis]")
        assert "Moderate" in ass["uncertainty_statement"]

        assert len(cand["timeline_events"]) == 2
        assert len(cand["evidence_items"]) >= 2


def test_candidate_api_evaluates_sparse_evidence_incident():
    """Verify candidate reconstruction with isolated single error log results in high uncertainty."""
    mock_hits = [
        # Single isolated error log in auth-service
        {
            "_id": "log-s1",
            "_source": {
                "timestamp": "2026-10-10T14:00:00Z",
                "service_name": "auth-service",
                "environment": "production",
                "log_level": "ERROR",
                "message": "Token signature verification failed",
                "host": "auth-host-1",
                "trace_id": None,
                "span_id": None,
                "raw_log": '{"service": "auth-service", "error": "invalid_signature"}',
            },
        },
    ]

    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.return_value = {"hits": {"hits": mock_hits}}
        mock_get_client.return_value = mock_client

        resp = client.post(
            "/api/v1/incidents/candidates",
            json={"cascade_window_seconds": 120, "window_minutes": 15},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_candidates"] == 1
        assert data["total_affected_services"] == 1

        cand = data["candidates"][0]
        assert "auth-service" in cand["title"]
        assert "LOCAL_SERVICE" in cand["correlation_rules_used"]
        assert cand["is_root_cause_confirmed"] is False

        # Verify dynamic uncertainty grounded in sparse evidence evaluation
        assert "Earliest observed failure identified chronologically in service 'auth-service'" in cand["uncertainty_reason"]
        assert "Diagnostic uncertainty: High" in cand["uncertainty_reason"]
        assert "fewer than 2 corroborating evidence records or timeline milestones available" in cand["uncertainty_reason"]

        # Verify structured assessment exposure
        assert cand["assessment"] is not None
        ass = cand["assessment"]
        assert ass["classification"] == "INSUFFICIENT"
        assert ass["is_root_cause_confirmed"] is False
        assert ass["has_shared_trace"] is False
        assert ass["distinct_evidence_count"] == 1
        assert ass["distinct_service_count"] == 1
        assert "High" in ass["uncertainty_statement"]

        # Verify RCA synthesis for sparse / insufficient evidence
        assert cand["rca_synthesis"] is not None
        rca = cand["rca_synthesis"]
        assert rca["classification"] == "INSUFFICIENT"
        assert rca["is_root_cause_confirmed"] is False
        assert rca["primary_hypothesis"] is None
        assert "[Insufficient Telemetry]" in rca["findings"][0]["explanation"]

        assert len(cand["timeline_events"]) == 1
        assert len(cand["evidence_items"]) == 1


def test_candidate_api_get_convenience_endpoint():
    """Verify GET /api/v1/incidents/candidates returns valid candidate response."""
    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.return_value = {"hits": {"hits": []}}
        mock_get_client.return_value = mock_client

        resp = client.get("/api/v1/incidents/candidates?window_minutes=30&service=auth-service")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_candidates"] == 0
        assert data["candidates"] == []


def test_candidate_api_structured_assessment_contract():
    """Verify that candidate endpoint serializes the complete structured EvidenceAssessmentResponse."""
    mock_hits = [
        {
            "_id": "log-tr-1",
            "_source": {
                "timestamp": "2026-10-10T14:00:00Z",
                "service_name": "checkout-service",
                "environment": "production",
                "log_level": "ERROR",
                "message": "Cart persistence failure",
                "host": "checkout-host",
                "trace_id": "trace-cart-999",
                "span_id": "span-cart-1",
                "raw_log": '{"service": "checkout-service", "error": "db_down"}',
            },
        },
        {
            "_id": "log-tr-2",
            "_source": {
                "timestamp": "2026-10-10T14:00:10Z",
                "service_name": "order-service",
                "environment": "production",
                "log_level": "ERROR",
                "message": "Order creation failed due to cart error",
                "host": "order-host",
                "trace_id": "trace-cart-999",
                "span_id": "span-order-1",
                "raw_log": '{"service": "order-service", "error": "upstream"}',
            },
        },
    ]

    with patch("app.api.v1.endpoints.incidents.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.return_value = {"hits": {"hits": mock_hits}}
        mock_get_client.return_value = mock_client

        resp = client.post(
            "/api/v1/incidents/candidates",
            json={"cascade_window_seconds": 120, "window_minutes": 15},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_candidates"] == 1

        cand = data["candidates"][0]

        # 1. Backward compatibility: verify all established fields remain intact
        assert "candidate_id" in cand
        assert "title" in cand
        assert "severity" in cand
        assert "started_at" in cand
        assert "ended_at" in cand
        assert "correlation_rules_used" in cand
        assert "uncertainty_reason" in cand
        assert "affected_services" in cand
        assert "timeline_events" in cand
        assert "evidence_items" in cand
        assert "cluster_ids" in cand
        assert cand["is_root_cause_confirmed"] is False

        # 2. Structured assessment schema verification
        assert "assessment" in cand
        ass_dict = cand["assessment"]
        assert ass_dict is not None

        # Verify all 12 requested fields are present and typed correctly
        assert ass_dict["classification"] == "TRACE_LINKED"
        assert ass_dict["is_root_cause_confirmed"] is False
        assert ass_dict["distinct_evidence_count"] >= 2
        assert ass_dict["distinct_service_count"] == 2
        assert ass_dict["has_shared_trace"] is True
        assert "trace-cart-999" in ass_dict["trace_ids_found"]
        assert isinstance(ass_dict["span_ids_found"], list)
        assert ass_dict["is_timeline_temporally_consistent"] is True
        assert isinstance(ass_dict["observed_facts"], list)
        assert len(ass_dict["observed_facts"]) >= 1
        assert isinstance(ass_dict["inferred_hypotheses"], list)
        assert len(ass_dict["inferred_hypotheses"]) >= 1
        assert ass_dict["inferred_hypotheses"][0].startswith("[Hypothesis]")
        assert "Low-to-Moderate" in ass_dict["uncertainty_statement"]

        # 3. RCA synthesis schema verification
        assert "rca_synthesis" in cand
        rca_dict = cand["rca_synthesis"]
        assert rca_dict is not None
        assert rca_dict["classification"] == "TRACE_LINKED"
        assert rca_dict["is_root_cause_confirmed"] is False
        assert rca_dict["primary_hypothesis"] is not None
        assert rca_dict["primary_hypothesis"].startswith("[Hypothesis]")
        assert "trace-cart-999" in rca_dict["primary_hypothesis"]
        assert len(rca_dict["findings"]) == 1
        finding = rca_dict["findings"][0]
        assert finding["is_root_cause_confirmed"] is False
        assert finding["explanation"] == rca_dict["primary_hypothesis"]
        assert len(finding["supporting_evidence_references"]) == 2
        assert finding["supporting_evidence_references"][0]["log_id"] == "log-tr-1"
        assert finding["supporting_evidence_references"][1]["log_id"] == "log-tr-2"
        assert finding["contradictory_evidence_references"] == []
        assert finding["contradictory_signals"] == []
        assert "Low-to-Moderate" in finding["limitations_and_uncertainty"]
        # Embedded rca_synthesis inside assessment is also verified
        assert "rca_synthesis" in ass_dict
        assert ass_dict["rca_synthesis"] is not None
        assert ass_dict["rca_synthesis"]["classification"] == "TRACE_LINKED"


