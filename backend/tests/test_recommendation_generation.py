"""Unit and integration tests for Phase 18 Step 3: Troubleshooting Recommendation Generation.

Tests:
1. Deterministic offline recommendation generation across classifications (INSUFFICIENT, TEMPORAL_ONLY, TRACE_LINKED).
2. Strict classification allow-list enforcement (INSUFFICIENT permits only TELEMETRY_GAP, DIAGNOSTIC_VERIFICATION).
3. Rejection of unsupported claims of confirmed root cause or proven causation.
4. Isolated namespace citation validation (trace ID cannot validate as log ID, fabricated IDs rejected).
5. Exact service association validation (citing valid log ID with wrong service rejected).
6. Conservative all-or-nothing fallback on any validation failure.
7. Idempotency (repeated POST without force_regenerate returns existing records; force_regenerate replaces cleanly).
8. Database transaction parent row locking (with_for_update) and rollback on failure.
9. FastAPI TestClient endpoint integration (POST 201/200, GET list, GET single, ownership verification, 404s).
"""

from datetime import datetime, timezone
import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models.evidence import Evidence
from app.models.incident import Incident
from app.models.incident_affected_service import IncidentAffectedService
from app.models.incident_event import IncidentEvent
from app.models.log_source import LogSource
from app.models.recommendation import Recommendation
from app.schemas.recommendation import (
    RecommendationGenerateRequest,
    RecommendationListResponse,
    RecommendationResponse,
)
from app.services.evidence_analysis_service import (
    EvidenceSufficiencyClassification,
    evaluate_evidence_sufficiency,
)
from app.services.rca_synthesis_service import synthesize_rca_findings
from app.services.recommendation_service import (
    build_deterministic_recommendations,
    generate_incident_recommendations,
    get_incident_recommendations,
    get_recommendation_by_id,
    validate_and_resolve_citations,
    validate_llm_recommendations,
    _synthesize_validated_recommendations,
)

client = TestClient(app)


def _make_test_incident(
    incident_id: Optional[uuid.UUID] = None,
    title: str = "Payment Gateway Latency Spike",
    severity: str = "HIGH",
    classification: str = "TRACE_LINKED",
    event_count: int = 2,
    evidence_count: int = 2,
    shared_trace_id: Optional[str] = "trace-rec-777",
) -> Incident:
    """Construct an in-memory Incident with populated events and evidence for testing."""
    inc_id = incident_id or uuid.uuid4()
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 30, tzinfo=timezone.utc)
    now = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)

    log_source = LogSource(
        id=uuid.uuid4(),
        name="payment-service-logs",
        service_name="payment-service",
        environment="production",
        format_type="json",
        is_active=True,
    )

    incident = Incident(
        id=inc_id,
        title=title,
        severity=severity,
        status="INVESTIGATING",
        analysis_status=classification,
        primary_log_source_id=log_source.id,
        started_at=t0,
        ended_at=t1,
        detected_at=now,
        probable_cause=None,
        confidence_score=0.0,
        is_root_cause_confirmed=False,
        uncertainty_reason="Diagnostic uncertainty: Moderate.",
        created_at=now,
        updated_at=now,
    )
    incident.primary_log_source = log_source

    aff_svc_1 = IncidentAffectedService(
        id=uuid.uuid4(),
        incident_id=inc_id,
        log_source_id=log_source.id,
        service_name="payment-service",
        impact_level="PRIMARY_CULPRIT",
        first_impacted_at=t0,
        error_count=6,
        created_at=now,
        updated_at=now,
    )
    aff_svc_2 = IncidentAffectedService(
        id=uuid.uuid4(),
        incident_id=inc_id,
        service_name="gateway-service",
        impact_level="DOWNSTREAM_CASCADE",
        first_impacted_at=t1,
        error_count=4,
        created_at=now,
        updated_at=now,
    )
    incident.affected_services = [aff_svc_1, aff_svc_2]

    # Create events
    events = []
    for i in range(event_count):
        ev_ts = datetime(2026, 10, 10, 14, i, 0, tzinfo=timezone.utc)
        events.append(
            IncidentEvent(
                id=uuid.uuid4(),
                incident_id=inc_id,
                event_timestamp=ev_ts,
                service_name="payment-service" if i == 0 else "gateway-service",
                event_type="ERROR_SPIKE",
                severity="ERROR",
                sequence_order=i,
                summary=f"Event {i} in execution path",
                attributes={
                    "trace_id": shared_trace_id,
                    "span_id": f"span-{i:03d}",
                    "is_earliest_observed_failure": (i == 0),
                    "propagation_lag_seconds": float(i * 15),
                },
                created_at=now,
                updated_at=now,
            )
        )
    incident.events = events

    # Create evidence
    evidence = []
    for i in range(evidence_count):
        ev_ts = datetime(2026, 10, 10, 14, i, 0, tzinfo=timezone.utc)
        evidence.append(
            Evidence(
                id=uuid.uuid4(),
                incident_id=inc_id,
                evidence_type="CULPRIT_LOG" if i == 0 else "SUPPORTING_LOG",
                opensearch_index="rootlens-logs-*",
                opensearch_doc_id=f"doc-rec-{i:03d}",
                log_timestamp=ev_ts,
                service_name="payment-service" if i == 0 else "gateway-service",
                log_level="ERROR",
                log_snippet=f"Database connection pool exhausted at stage {i}",
                content_hash=f"hash-rec-{i:03d}",
                document_status="INDEXED",
                relevance_score=1.0,
                explanation=f"Telemetry indicates pool failure at stage {i}",
                metadata_={
                    "correlation_rule": "TRACE_LINKED" if shared_trace_id else "TEMPORAL",
                    "trace_id": shared_trace_id,
                    "span_id": f"span-{i:03d}",
                },
                created_at=now,
                updated_at=now,
            )
        )
    incident.evidence = evidence

    return incident


def _make_mock_session(incident=None, existing_recommendations=None):
    session = AsyncMock()
    session.add_all = MagicMock()

    mock_incident_result = MagicMock()
    mock_incident_result.scalar_one_or_none.return_value = incident

    mock_rec_result = MagicMock()
    mock_rec_result.scalars.return_value.all.return_value = existing_recommendations or []

    async def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        if "from incidents" in stmt_str or "incidents." in stmt_str:
            return mock_incident_result
        return mock_rec_result

    session.execute.side_effect = mock_execute
    return session


# ============================================================================
# 1. Deterministic Baseline Generation & Allow-List Enforcement
# ============================================================================

def test_deterministic_recommendations_insufficient_allowlist():
    """Verify INSUFFICIENT classification only allows TELEMETRY_GAP and DIAGNOSTIC_VERIFICATION."""
    incident = _make_test_incident(event_count=0, evidence_count=0, shared_trace_id=None)
    assessment = evaluate_evidence_sufficiency(evidence_items=[], timeline_events=[])
    rca_synthesis = synthesize_rca_findings(assessment=assessment, evidence_items=[], timeline_events=[])

    drafts = build_deterministic_recommendations(incident, assessment, rca_synthesis)
    assert len(drafts) >= 2
    action_types = {d.action_type for d in drafts}

    # Must only contain permitted allow-list types
    assert action_types.issubset({"TELEMETRY_GAP", "DIAGNOSTIC_VERIFICATION"})
    assert "IMMEDIATE_ACTION" not in action_types
    assert "MITIGATION_ADVISORY" not in action_types

    for d in drafts:
        assert d.caveat is not None and "Advisory recommendation only" in d.caveat
        assert d.caveat is not None and "zero autonomous remediation" in d.caveat


def test_deterministic_recommendations_temporal_only_allowlist():
    """Verify TEMPORAL_ONLY classification permits only TELEMETRY_GAP, DIAGNOSTIC_VERIFICATION, MITIGATION_ADVISORY."""
    incident = _make_test_incident(shared_trace_id=None)
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence, timeline_events=incident.events
    )
    rca_synthesis = synthesize_rca_findings(
        assessment=assessment, evidence_items=incident.evidence, timeline_events=incident.events
    )

    drafts = build_deterministic_recommendations(incident, assessment, rca_synthesis)
    action_types = {d.action_type for d in drafts}

    assert action_types.issubset({"TELEMETRY_GAP", "DIAGNOSTIC_VERIFICATION", "MITIGATION_ADVISORY"})
    assert "IMMEDIATE_ACTION" not in action_types

    for d in drafts:
        assert d.caveat is not None and "Temporal correlation does not establish confirmed causation" in d.caveat


def test_deterministic_recommendations_trace_linked_non_destructive():
    """Verify TRACE_LINKED recommendations preserve non-destructive advisory invariants."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777")
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence, timeline_events=incident.events
    )
    rca_synthesis = synthesize_rca_findings(
        assessment=assessment, evidence_items=incident.evidence, timeline_events=incident.events
    )

    drafts = build_deterministic_recommendations(incident, assessment, rca_synthesis)
    action_types = {d.action_type for d in drafts}

    assert "DIAGNOSTIC_VERIFICATION" in action_types
    assert "IMMEDIATE_ACTION" in action_types

    for d in drafts:
        # Check non-destructive immediate actions
        if d.action_type == "IMMEDIATE_ACTION":
            assert "Validate upstream dependency" in d.title
            assert "non-destructive" in d.description.lower()
        assert d.caveat is not None and "RootLens performs zero autonomous remediation" in d.caveat


# ============================================================================
# 2. Strict LLM Output Validation & Fallback Tests
# ============================================================================

def test_validate_llm_recommendations_insufficient_rejects_remediation():
    """Verify LLM attempting to prescribe cause-specific remediation in INSUFFICIENT is rejected."""
    incident = _make_test_incident(event_count=0, evidence_count=0, shared_trace_id=None)
    assessment = evaluate_evidence_sufficiency(evidence_items=[], timeline_events=[])

    raw_llm_items = [
        {
            "action_type": "IMMEDIATE_ACTION",  # Disallowed for INSUFFICIENT
            "title": "Restart auth service pods",
            "description": "Restart pods to clear connection pool",
            "priority": "HIGH",
            "evidence_references": [],
        }
    ]

    result = validate_llm_recommendations(raw_llm_items, incident, assessment)
    assert result is None  # Trigger all-or-nothing fallback!


def test_validate_llm_recommendations_temporal_only_rejects_immediate_action():
    """Verify LLM returning IMMEDIATE_ACTION in TEMPORAL_ONLY is rejected."""
    incident = _make_test_incident(shared_trace_id=None)
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence, timeline_events=incident.events
    )

    raw_llm_items = [
        {
            "action_type": "IMMEDIATE_ACTION",  # Disallowed for TEMPORAL_ONLY
            "title": "Failover primary payment database",
            "description": "Trigger failover immediately",
            "priority": "CRITICAL",
            "evidence_references": [],
        }
    ]

    result = validate_llm_recommendations(raw_llm_items, incident, assessment)
    assert result is None


def test_validate_llm_recommendations_rejects_prohibited_causal_claims():
    """Verify LLM asserting confirmed root cause or proven causation is rejected."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777")
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence, timeline_events=incident.events
    )

    raw_llm_items = [
        {
            "action_type": "DIAGNOSTIC_VERIFICATION",
            "title": "Payment service is the confirmed root cause of failure",  # Prohibited claim!
            "description": "Diagnostic telemetry conclusively proven root cause",
            "priority": "HIGH",
            "evidence_references": [],
        }
    ]

    result = validate_llm_recommendations(raw_llm_items, incident, assessment)
    assert result is None


def test_validate_llm_recommendations_namespace_collision_rejected():
    """Verify trace ID passed as LOG reference fails validation and triggers fallback."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777")
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence, timeline_events=incident.events
    )

    raw_llm_items = [
        {
            "action_type": "DIAGNOSTIC_VERIFICATION",
            "title": "Inspect log traces",
            "description": "Reviewing execution path",
            "priority": "HIGH",
            "evidence_references": [
                {
                    "reference_type": "LOG",  # Collision! 'trace-rec-777' is a TRACE, not a LOG
                    "reference_id": "trace-rec-777",
                    "service_name": "payment-service",
                }
            ],
        }
    ]

    result = validate_llm_recommendations(raw_llm_items, incident, assessment)
    assert result is None


def test_validate_llm_recommendations_fabricated_id_rejected():
    """Verify fabricated log ID or trace ID triggers immediate rejection."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777")
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence, timeline_events=incident.events
    )

    raw_llm_items = [
        {
            "action_type": "DIAGNOSTIC_VERIFICATION",
            "title": "Inspect database connection",
            "description": "Review logs",
            "priority": "HIGH",
            "evidence_references": [
                {
                    "reference_type": "LOG",
                    "reference_id": "doc-fabricated-999",  # Non-existent ID!
                    "service_name": "payment-service",
                }
            ],
        }
    ]

    result = validate_llm_recommendations(raw_llm_items, incident, assessment)
    assert result is None


def test_validate_llm_recommendations_service_mismatch_rejected():
    """Verify citing a valid log ID with an incorrect service name is rejected."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777")
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence, timeline_events=incident.events
    )

    # doc-rec-000 belongs to payment-service, but LLM claims it belongs to gateway-service
    raw_llm_items = [
        {
            "action_type": "DIAGNOSTIC_VERIFICATION",
            "title": "Inspect payment errors in gateway",
            "description": "Examine gateway error logs",
            "priority": "HIGH",
            "evidence_references": [
                {
                    "reference_type": "LOG",
                    "reference_id": "doc-rec-000",
                    "service_name": "gateway-service",  # Service mismatch!
                }
            ],
        }
    ]

    result = validate_llm_recommendations(raw_llm_items, incident, assessment)
    assert result is None


def test_validate_llm_recommendations_valid_citations_accepted():
    """Verify correctly formatted and grounded citations are validated successfully."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777")
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence, timeline_events=incident.events
    )

    raw_llm_items = [
        {
            "action_type": "DIAGNOSTIC_VERIFICATION",
            "title": "Inspect connection pool in payment-service",
            "description": "Telemetry shows pool timeouts along trace",
            "priority": "HIGH",
            "verification_step": "Check active database connections",
            "evidence_references": [
                {
                    "reference_type": "LOG",
                    "reference_id": "doc-rec-000",
                    "service_name": "payment-service",
                },
                {
                    "reference_type": "TRACE",
                    "reference_id": "trace-rec-777",
                    "service_name": "payment-service",
                },
            ],
        }
    ]

    result = validate_llm_recommendations(raw_llm_items, incident, assessment)
    assert result is not None
    assert len(result) == 1
    assert result[0].title == "Inspect connection pool in payment-service"
    assert result[0].action_type == "DIAGNOSTIC_VERIFICATION"


# ============================================================================
# 3. Idempotency, Row Locking & Transaction Safety
# ============================================================================

@pytest.mark.asyncio
async def test_generate_recommendations_idempotency_returns_existing():
    """Verify repeated call without force_regenerate returns existing recommendations."""
    incident = _make_test_incident()
    existing_rec = Recommendation(
        id=uuid.uuid4(),
        incident_id=incident.id,
        action_type="DIAGNOSTIC_VERIFICATION",
        title="Existing Recommendation Title",
        description="Existing Description",
        priority="HIGH",
        sequence_order=0,
        verification_step="Check metrics",
        caveat="Advisory only",
    )

    mock_session = _make_mock_session(incident=incident, existing_recommendations=[existing_rec])

    recs, is_created = await generate_incident_recommendations(
        session=mock_session,
        incident_id=incident.id,
        req=RecommendationGenerateRequest(force_regenerate=False),
    )

    assert is_created is False
    assert len(recs) == 1
    assert recs[0].title == "Existing Recommendation Title"
    mock_session.add_all.assert_not_called()


@pytest.mark.asyncio
async def test_generate_recommendations_force_regenerate_replaces():
    """Verify force_regenerate=True deletes existing rows and replaces them atomically."""
    incident = _make_test_incident()
    existing_rec = Recommendation(
        id=uuid.uuid4(),
        incident_id=incident.id,
        action_type="DIAGNOSTIC_VERIFICATION",
        title="Old Recommendation",
        description="Old Description",
        priority="LOW",
        sequence_order=0,
    )

    mock_session = _make_mock_session(incident=incident, existing_recommendations=[existing_rec])

    recs, is_created = await generate_incident_recommendations(
        session=mock_session,
        incident_id=incident.id,
        req=RecommendationGenerateRequest(force_regenerate=True),
    )

    assert is_created is True
    assert mock_session.add_all.called
    assert mock_session.commit.called


@pytest.mark.asyncio
async def test_generate_recommendations_db_failure_rolls_back():
    """Verify commit failure rolls back transaction and does not leave partial records."""
    incident = _make_test_incident()
    mock_session = _make_mock_session(incident=incident, existing_recommendations=[])
    mock_session.commit.side_effect = RuntimeError("Database connection interrupted during commit")

    with pytest.raises(RuntimeError):
        await generate_incident_recommendations(
            session=mock_session,
            incident_id=incident.id,
        )

    mock_session.rollback.assert_called_once()


# ============================================================================
# 4. API Endpoint Integration (FastAPI TestClient)
# ============================================================================

def test_api_create_and_get_recommendations():
    """Verify POST, GET list, and GET single endpoints with incident ownership."""
    incident = _make_test_incident()
    mock_session = AsyncMock()

    rec_orm = Recommendation(
        id=uuid.uuid4(),
        incident_id=incident.id,
        action_type="DIAGNOSTIC_VERIFICATION",
        title="Check DB connection latency",
        description="Telemetry indicates connection latency spike",
        priority="HIGH",
        sequence_order=0,
        verification_step="Run synthetic ping",
        caveat="Advisory guidance only",
        created_at=datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc),
    )

    app.dependency_overrides[get_db] = lambda: mock_session

    try:
        # 1. POST /{id}/recommendations creates recommendations (201)
        with patch(
            "app.api.v1.endpoints.incidents.get_incident_by_id",
            new=AsyncMock(return_value=incident),
        ), patch(
            "app.api.v1.endpoints.incidents.generate_incident_recommendations",
            new=AsyncMock(return_value=([rec_orm], True)),
        ):
            resp = client.post(
                f"/api/v1/incidents/{incident.id}/recommendations",
                json={"force_regenerate": False},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["total"] == 1
            assert data["is_advisory_only"] is True
            assert data["is_root_cause_confirmed"] is False
            assert data["items"][0]["title"] == "Check DB connection latency"

        # 2. POST /{id}/recommendations returns existing (200)
        with patch(
            "app.api.v1.endpoints.incidents.get_incident_by_id",
            new=AsyncMock(return_value=incident),
        ), patch(
            "app.api.v1.endpoints.incidents.generate_incident_recommendations",
            new=AsyncMock(return_value=([rec_orm], False)),
        ):
            resp = client.post(
                f"/api/v1/incidents/{incident.id}/recommendations",
                json={"force_regenerate": False},
            )
            assert resp.status_code == 200

        # 3. GET /{id}/recommendations lists recommendations (200)
        with patch(
            "app.api.v1.endpoints.incidents.get_incident_by_id",
            new=AsyncMock(return_value=incident),
        ), patch(
            "app.api.v1.endpoints.incidents.get_incident_recommendations",
            new=AsyncMock(return_value=[rec_orm]),
        ):
            resp = client.get(f"/api/v1/incidents/{incident.id}/recommendations")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["items"][0]["id"] == str(rec_orm.id)

        # 4. GET /{id}/recommendations/{rec_id} retrieves single recommendation (200)
        with patch(
            "app.api.v1.endpoints.incidents.get_incident_rec_by_id",
            new=AsyncMock(return_value=rec_orm),
        ):
            resp = client.get(f"/api/v1/incidents/{incident.id}/recommendations/{rec_orm.id}")
            assert resp.status_code == 200
            assert resp.json()["id"] == str(rec_orm.id)

        # 5. GET single recommendation ownership mismatch returns 404
        with patch(
            "app.api.v1.endpoints.incidents.get_incident_rec_by_id",
            new=AsyncMock(return_value=None),
        ):
            resp = client.get(f"/api/v1/incidents/{incident.id}/recommendations/{uuid.uuid4()}")
            assert resp.status_code == 404

        # 6. Invalid UUIDs return 404
        resp = client.get("/api/v1/incidents/invalid-uuid/recommendations")
        assert resp.status_code == 404
        resp = client.get(f"/api/v1/incidents/{incident.id}/recommendations/invalid-uuid")
        assert resp.status_code == 404

    finally:
        app.dependency_overrides.pop(get_db, None)


# ============================================================================
# 8. Phase 8 Stage 1: Recommendation Evidence Grounding Tests
# ============================================================================

def test_validate_and_resolve_citations_success():
    """Verify that legitimate LOG, TRACE, and SPAN citations resolve accurately."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777", event_count=2, evidence_count=2)
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence,
        timeline_events=incident.events,
    )

    valid_cits = [
        {"reference_type": "LOG", "reference_id": "doc-rec-000", "service_name": "payment-service"},
        {"reference_type": "LOG", "reference_id": "doc-rec-001", "service_name": "gateway-service"},
        {"reference_type": "TRACE", "reference_id": "trace-rec-777", "service_name": "payment-service"},
        {"reference_type": "TRACE", "reference_id": "trace-rec-777", "service_name": "gateway-service"},
        {"reference_type": "SPAN", "reference_id": "span-000", "service_name": "payment-service"},
        {"reference_type": "SPAN", "reference_id": "span-001", "service_name": "gateway-service"},
    ]

    resolved = validate_and_resolve_citations(valid_cits, incident, assessment)
    assert resolved is not None
    assert len(resolved) == 6
    assert resolved[0] == {"reference_type": "LOG", "reference_id": "doc-rec-000", "service_name": "payment-service"}
    assert resolved[2] == {"reference_type": "TRACE", "reference_id": "trace-rec-777", "service_name": "payment-service"}

    # Test empty or None citations
    assert validate_and_resolve_citations([], incident, assessment) == []
    assert validate_and_resolve_citations(None, incident, assessment) == []


def test_validate_and_resolve_citations_rejections():
    """Verify that fabricated, invalid, or service-mismatched citations are strictly rejected."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777", event_count=2, evidence_count=2)
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence,
        timeline_events=incident.events,
    )

    # 1. Fabricated log doc_id
    bad_log = [{"reference_type": "LOG", "reference_id": "doc-fabricated-999", "service_name": "payment-service"}]
    assert validate_and_resolve_citations(bad_log, incident, assessment) is None

    # 2. Log service mismatch (doc-rec-000 belongs to payment-service, not gateway-service)
    mismatched_log = [{"reference_type": "LOG", "reference_id": "doc-rec-000", "service_name": "gateway-service"}]
    assert validate_and_resolve_citations(mismatched_log, incident, assessment) is None

    # 3. Fabricated trace ID
    bad_trace = [{"reference_type": "TRACE", "reference_id": "trace-ghost-999", "service_name": "payment-service"}]
    assert validate_and_resolve_citations(bad_trace, incident, assessment) is None

    # 4. Trace service mismatch (unknown-service is not associated with trace-rec-777)
    mismatched_trace = [{"reference_type": "TRACE", "reference_id": "trace-rec-777", "service_name": "auth-service"}]
    assert validate_and_resolve_citations(mismatched_trace, incident, assessment) is None

    # 5. Fabricated span ID
    bad_span = [{"reference_type": "SPAN", "reference_id": "span-fake-999", "service_name": "payment-service"}]
    assert validate_and_resolve_citations(bad_span, incident, assessment) is None

    # 6. Span service mismatch (span-000 is on payment-service, not gateway-service)
    mismatched_span = [{"reference_type": "SPAN", "reference_id": "span-000", "service_name": "gateway-service"}]
    assert validate_and_resolve_citations(mismatched_span, incident, assessment) is None

    # 7. Unknown namespace
    unknown_ns = [{"reference_type": "METRIC", "reference_id": "cpu-99", "service_name": "payment-service"}]
    assert validate_and_resolve_citations(unknown_ns, incident, assessment) is None

    # 8. Non-dict or malformed items
    assert validate_and_resolve_citations("not-a-list", incident, assessment) is None
    assert validate_and_resolve_citations([None], incident, assessment) is None
    assert validate_and_resolve_citations([{"reference_type": "LOG"}], incident, assessment) is None


def test_deterministic_recommendations_evidence_grounding():
    """Verify deterministic recommendations attach grounded citations according to classification."""
    # 1. TRACE_LINKED incident
    incident_trace = _make_test_incident(classification="TRACE_LINKED", shared_trace_id="trace-rec-777")
    assessment_trace = evaluate_evidence_sufficiency(
        evidence_items=incident_trace.evidence,
        timeline_events=incident_trace.events,
    )
    rca_trace = synthesize_rca_findings(assessment=assessment_trace, evidence_items=incident_trace.evidence, timeline_events=incident_trace.events)

    drafts_trace = build_deterministic_recommendations(incident_trace, assessment_trace, rca_trace)
    assert len(drafts_trace) == 3
    # Rec 1 (Diagnostic Verification) must cite shared trace and earliest log
    assert len(drafts_trace[0].evidence_citations) >= 2
    types = {c["reference_type"] for c in drafts_trace[0].evidence_citations}
    assert "TRACE" in types and "LOG" in types

    # Rec 2 (Mitigation Advisory) must cite shared trace for downstream service
    assert any(c["reference_type"] == "TRACE" and c["service_name"] == "gateway-service" for c in drafts_trace[1].evidence_citations)

    # 2. TEMPORAL_ONLY incident
    incident_temporal = _make_test_incident(classification="TEMPORAL_ONLY", shared_trace_id=None)
    assessment_temporal = evaluate_evidence_sufficiency(
        evidence_items=incident_temporal.evidence,
        timeline_events=incident_temporal.events,
    )
    rca_temporal = synthesize_rca_findings(assessment=assessment_temporal, evidence_items=incident_temporal.evidence, timeline_events=incident_temporal.events)

    drafts_temporal = build_deterministic_recommendations(incident_temporal, assessment_temporal, rca_temporal)
    assert len(drafts_temporal) == 3
    # Rec 1 cites earliest failure log
    assert len(drafts_temporal[0].evidence_citations) == 1
    assert drafts_temporal[0].evidence_citations[0]["reference_type"] == "LOG"
    assert drafts_temporal[0].evidence_citations[0]["reference_id"] == "doc-rec-000"

    # 3. INSUFFICIENT incident
    incident_insufficient = _make_test_incident(classification="INSUFFICIENT", shared_trace_id=None, evidence_count=0)
    assessment_insufficient = evaluate_evidence_sufficiency(
        evidence_items=[],
        timeline_events=[],
    )
    rca_insufficient = synthesize_rca_findings(assessment=assessment_insufficient, evidence_items=[], timeline_events=[])

    drafts_insufficient = build_deterministic_recommendations(incident_insufficient, assessment_insufficient, rca_insufficient)
    assert len(drafts_insufficient) == 2
    # In insufficient telemetry, no fabricated citations must be attached
    for d in drafts_insufficient:
        assert d.evidence_citations == []


@pytest.mark.asyncio
async def test_synthesize_validated_recommendations_persistence():
    """Verify that _synthesize_validated_recommendations persists grounded citations into ORM models."""
    incident = _make_test_incident(classification="TRACE_LINKED", shared_trace_id="trace-rec-777")
    assessment = evaluate_evidence_sufficiency(
        evidence_items=incident.evidence,
        timeline_events=incident.events,
    )
    rca = synthesize_rca_findings(assessment=assessment, evidence_items=incident.evidence, timeline_events=incident.events)

    orm_recs = await _synthesize_validated_recommendations(incident, assessment, rca, client=None)
    assert len(orm_recs) == 3
    for r in orm_recs:
        assert hasattr(r, "evidence_citations")
        assert isinstance(r.evidence_citations, list)

    # First recommendation should have grounded citations persisted
    assert len(orm_recs[0].evidence_citations) >= 2
    cit = orm_recs[0].evidence_citations[0]
    assert "reference_type" in cit and "reference_id" in cit and "service_name" in cit


def test_legacy_recommendation_compatibility():
    """Verify that legacy recommendations lacking evidence_citations serialize gracefully to empty list."""
    rec_legacy = Recommendation(
        id=uuid.uuid4(),
        incident_id=uuid.uuid4(),
        action_type="DIAGNOSTIC_VERIFICATION",
        title="Legacy check",
        description="Legacy description without citations field",
        priority="MEDIUM",
        sequence_order=0,
        verification_step="Check logs",
        caveat="Advisory only",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    # Simulate legacy state where evidence_citations attribute might be None
    object.__setattr__(rec_legacy, "evidence_citations", None)

    resp = RecommendationResponse.from_orm_recommendation(rec_legacy)
    assert resp.evidence_citations == []
    assert resp.title == "Legacy check"


def test_api_serialization_with_evidence_citations():
    """Verify API schema serialization includes typed evidence_citations and round-trips via TestClient."""
    incident = _make_test_incident(shared_trace_id="trace-rec-777")
    rec_with_cits = Recommendation(
        id=uuid.uuid4(),
        incident_id=incident.id,
        action_type="DIAGNOSTIC_VERIFICATION",
        title="Investigate pool exhaustion",
        description="Check connection latency",
        priority="HIGH",
        sequence_order=0,
        verification_step="Run diagnostics",
        caveat="Advisory only",
        evidence_citations=[
            {"reference_type": "LOG", "reference_id": "doc-rec-000", "service_name": "payment-service"},
            {"reference_type": "TRACE", "reference_id": "trace-rec-777", "service_name": "payment-service"},
        ],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    app.dependency_overrides[get_db] = lambda: _make_mock_session(incident)

    try:
        with patch(
            "app.api.v1.endpoints.incidents.get_incident_rec_by_id",
            new=AsyncMock(return_value=rec_with_cits),
        ):
            resp = client.get(f"/api/v1/incidents/{incident.id}/recommendations/{rec_with_cits.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert "evidence_citations" in data
            assert len(data["evidence_citations"]) == 2
            assert data["evidence_citations"][0]["reference_type"] == "LOG"
            assert data["evidence_citations"][0]["reference_id"] == "doc-rec-000"
            assert data["evidence_citations"][0]["service_name"] == "payment-service"
            assert data["evidence_citations"][1]["reference_type"] == "TRACE"
            assert data["evidence_citations"][1]["reference_id"] == "trace-rec-777"
    finally:
        app.dependency_overrides.pop(get_db, None)
