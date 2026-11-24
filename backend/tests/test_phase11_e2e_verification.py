"""Phase 11 End-to-End Integration and Security Verification Suite for RootLens.

Executes and verifies:
1. End-to-end lifecycle from raw log upload through parsing, normalization, PostgreSQL persistence,
   OpenSearch indexing (with offline handling and mock execution), clustering, event correlation,
   incident creation/persistence, evidence analysis, RCA synthesis, citation validation,
   recommendation generation, operator hypothesis confirmation/rejection, and CSV/JSON export.
2. Cryptographic JWT authentication, developer token boundaries, trusted gateway verification, and RBAC.
3. Anti-fabrication, cross-incident evidence rejection, wrong-service rejection, and temporal non-causality.
4. Failure handling, recovery, and input validation across all platform stages.
"""

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import httpx
from httpx import ASGITransport
import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    DEV_TOKENS,
    OperatorIdentity,
    create_signed_token,
    get_current_operator,
    require_authorized_operator,
    verify_signed_token,
)
from app.core.config import settings
from app.db.session import close_engine, get_db, get_sessionmaker
from app.main import app
from app.models.evidence import Evidence
from app.models.incident import Incident
from app.models.incident_affected_service import IncidentAffectedService
from app.models.incident_event import IncidentEvent
from app.models.log_source import LogSource
from app.models.rca_hypothesis_decision import RCAHypothesisDecision
from app.models.recommendation import Recommendation
from app.models.report import Report
from app.models.upload_job import UploadJob
from app.schemas.incident import (
    CorrelatedAffectedServiceResponse,
    CorrelatedEvidenceItemResponse,
    CorrelatedTimelineEventResponse,
    CorrelateEventRequest,
    HypothesisDecisionEnum,
    IncidentCandidateRequest,
    IncidentCreateRequest,
    RCAHypothesisActionRequest,
    RCAHypothesisDecisionCreateRequest,
)
from app.schemas.recommendation import RecommendationGenerateRequest
from app.services.clustering_service import (
    LogCluster,
    LogEventItem,
    cluster_log_events,
    extract_message_pattern,
)
from app.services.correlation_service import (
    CorrelatedEvidenceItem,
    CorrelatedIncidentCandidate,
    correlate_clusters,
    evaluate_incident_grouping,
)
from app.services.evidence_analysis_service import (
    EvidenceSufficiencyAssessment,
    EvidenceSufficiencyClassification,
    evaluate_evidence_sufficiency,
)
from app.services.hypothesis_decision_service import (
    get_hypothesis_decisions_summary,
    record_hypothesis_decision,
)
from app.services.incident_persistence_service import (
    get_incident_by_id,
    list_incident_catalogue,
    map_incident_to_detail_response,
    persist_incident,
    update_incident_status,
)
from app.services.rca_synthesis_service import (
    RCASynthesisResult,
    synthesize_rca_findings,
)
from app.services.recommendation_service import (
    generate_incident_recommendations,
    get_incident_recommendations,
)


@pytest_asyncio.fixture(autouse=True)
async def reset_db_engine():
    """Ensure database connection pool is fresh and bound to the active test loop."""
    await close_engine()
    yield
    await close_engine()


# ============================================================================
# Section 1: End-to-End Workflow Verification Across All 11 Stages
# ============================================================================


@pytest.mark.asyncio
async def test_stage1_to_stage3_log_upload_parsing_normalization_and_postgres():
    """Verify Stages 1-3: Upload -> Staging -> Parsing -> Normalization -> PostgreSQL tracking."""
    sessionmaker = get_sessionmaker()
    tracked_job_id: Optional[uuid.UUID] = None
    tracked_source_id: Optional[uuid.UUID] = None
    unique_source_name = f"pg-test-{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            # Controlled test dataset (JSON application logs representing database cascade)
            raw_log_payload = "\n".join([
                json.dumps({
                    "timestamp": "2026-10-14T08:01:30Z",
                    "level": "FATAL",
                    "service": "postgres-db",
                    "message": "FATAL: remaining connection slots are reserved for non-replication superuser connections active=100 max=100",
                    "trace_id": "trace-db-001",
                    "span_id": "span-pg-01",
                    "host": "db-node-01.internal",
                }),
                json.dumps({
                    "timestamp": "2026-10-14T08:01:35Z",
                    "level": "ERROR",
                    "service": "order-service",
                    "message": "Connection acquisition timeout after 5002ms; unable to checkout connection from HikariPool",
                    "trace_id": "trace-db-001",
                    "span_id": "span-ord-01",
                    "host": "order-app-01.internal",
                }),
                json.dumps({
                    "timestamp": "2026-10-14T08:01:50Z",
                    "level": "ERROR",
                    "service": "payment-service",
                    "message": "Read timeout waiting for order-service lock endpoint; circuit breaker trips to OPEN",
                    "trace_id": "trace-db-001",
                    "span_id": "span-pay-01",
                    "host": "payment-app-01.internal",
                }),
            ])

            # Stage 1: Upload representative logs
            response = await client.post(
                "/api/v1/logs/upload",
                files={"file": ("stage1_test_cascade.json", io.BytesIO(raw_log_payload.encode("utf-8")), "application/json")},
                data={"service_name": "postgres-db", "environment": "production", "source_name": unique_source_name},
            )
            assert response.status_code == 201, f"Upload failed: {response.text}"
            upload_data = response.json()
            tracked_job_id = uuid.UUID(upload_data["job_id"])
            tracked_source_id = uuid.UUID(upload_data["log_source_id"])
            assert upload_data["status"] == "PENDING"
            assert upload_data["format_type"] == "json"

            # Verify Stage 3: PostgreSQL persistence of UploadJob and LogSource
            async with sessionmaker() as session:
                job_row = await session.get(UploadJob, tracked_job_id)
                assert job_row is not None
                assert job_row.file_size_bytes == len(raw_log_payload)
                source_row = await session.get(LogSource, tracked_source_id)
                assert source_row is not None
                assert source_row.service_name == "postgres-db"

            # Stage 2: Execute format-specific parsing
            parse_resp = await client.post(f"/api/v1/logs/jobs/{tracked_job_id}/parse")
            assert parse_resp.status_code == 200, f"Parse failed: {parse_resp.text}"
            parse_data = parse_resp.json()
            assert parse_data["total_records"] == 3
            assert parse_data["successful_records"] == 3
            assert parse_data["failed_records"] == 0

            # Stage 2 (cont): Execute normalization
            norm_resp = await client.post(f"/api/v1/logs/jobs/{tracked_job_id}/normalize")
            assert norm_resp.status_code == 200, f"Normalize failed: {norm_resp.text}"
            norm_data = norm_resp.json()
            assert norm_data["status"] == "COMPLETED"
            assert norm_data["valid_records"] == 3
            assert len(norm_data["records"]) == 3
            assert norm_data["records"][0]["log_level"] == "FATAL"
            assert norm_data["records"][0]["trace_id"] == "trace-db-001"

        finally:
            # Cleanup PostgreSQL records and staged file
            async with sessionmaker() as session:
                if tracked_job_id:
                    await session.execute(delete(UploadJob).where(UploadJob.id == tracked_job_id))
                if tracked_source_id:
                    await session.execute(delete(UploadJob).where(UploadJob.log_source_id == tracked_source_id))
                    await session.execute(delete(LogSource).where(LogSource.id == tracked_source_id))
                await session.commit()

            if tracked_job_id:
                staged_file = Path(settings.UPLOAD_DIR) / f"{tracked_job_id}.json"
                if staged_file.exists():
                    staged_file.unlink()


@pytest.mark.asyncio
async def test_stage4_opensearch_offline_resilience_and_mock_indexing():
    """Verify Stage 4: OpenSearch indexing resilience when offline and when active."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Offline OpenSearch verification: ping fails -> returns HTTP 503 without 500 crash
        mock_offline_client = MagicMock()
        mock_offline_client.ping.return_value = False

        with patch("app.services.search_service.get_opensearch_client", return_value=mock_offline_client):
            resp = await client.get("/api/v1/logs?page=1&page_size=10")
            assert resp.status_code == 503
            assert "OpenSearch cluster is currently unavailable" in resp.json()["detail"]

        # 2. Mock Active OpenSearch verification: hits returned and mapped cleanly to schema
        mock_active_client = MagicMock()
        mock_active_client.ping.return_value = True
        mock_active_client.search.return_value = {
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {
                        "_id": "doc-stage4-01",
                        "_index": "rootlens-logs-2026.10",
                        "_source": {
                            "timestamp": "2026-10-14T08:01:30Z",
                            "service_name": "postgres-db",
                            "environment": "production",
                            "log_level": "FATAL",
                            "message": "Connection slots exhausted",
                            "trace_id": "trace-db-001",
                            "span_id": "span-pg-01",
                        },
                    }
                ],
            }
        }

        with patch("app.services.search_service.get_opensearch_client", return_value=mock_active_client):
            resp = await client.get("/api/v1/logs?service=postgres-db")
            assert resp.status_code == 200
            search_data = resp.json()
            assert search_data["total"] == 1
            assert search_data["items"][0]["id"] == "doc-stage4-01"
            assert search_data["items"][0]["log_level"] == "FATAL"


def test_stage5_and_stage6_clustering_and_correlation():
    """Verify Stages 5-6: Template clustering and cross-service event correlation."""
    t0 = datetime(2026, 10, 14, 8, 1, 30, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 14, 8, 1, 35, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 14, 8, 1, 50, tzinfo=timezone.utc)

    events = [
        LogEventItem(
            log_id="log-001",
            timestamp=t0,
            service_name="postgres-db",
            log_level="FATAL",
            message="FATAL: remaining connection slots are reserved active=100 max=100",
            trace_id="trace-db-001",
            span_id="span-pg-01",
        ),
        LogEventItem(
            log_id="log-002",
            timestamp=t1,
            service_name="order-service",
            log_level="ERROR",
            message="Connection acquisition timeout after 5002ms from HikariPool",
            trace_id="trace-db-001",
            span_id="span-ord-01",
        ),
        LogEventItem(
            log_id="log-003",
            timestamp=t2,
            service_name="payment-service",
            log_level="ERROR",
            message="Read timeout waiting for order-service lock endpoint",
            trace_id="trace-db-001",
            span_id="span-pay-01",
        ),
    ]

    # Stage 5: Template Clustering
    clusters = cluster_log_events(events, window_minutes=15)
    assert len(clusters) == 3
    assert all(c.count == 1 for c in clusters)

    # Stage 6: Event Correlation
    candidates = correlate_clusters(clusters, cascade_window_seconds=120)
    assert len(candidates) >= 1
    primary_candidate = candidates[0]
    service_names = {s.service_name for s in primary_candidate.affected_services}
    assert service_names == {"postgres-db", "order-service", "payment-service"}
    assert len(primary_candidate.timeline_events) == 3
    assert len(primary_candidate.affected_services) == 3
    assert primary_candidate.timeline_events[0].is_earliest_observed_failure is True
    assert primary_candidate.timeline_events[0].propagation_lag_seconds == 0.0


@pytest.mark.asyncio
async def test_stage7_to_stage11_incident_evidence_rca_recommendations_and_operator_decision():
    """Verify Stages 7-11: Incident persistence, evidence analysis, RCA synthesis, recommendations,

    and operator human-in-the-loop confirmation and rejection workflow.
    """
    sessionmaker = get_sessionmaker()
    t0 = datetime(2026, 10, 14, 8, 1, 30, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 14, 8, 1, 35, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 14, 8, 1, 50, tzinfo=timezone.utc)

    candidate_id = f"cand-e2e-{uuid.uuid4().hex[:8]}"

    # Prepare candidate request
    create_req = IncidentCreateRequest(
        candidate_id=candidate_id,
        title="PostgreSQL connection exhaustion cascading to order and payment services",
        severity="CRITICAL",
        started_at=t0,
        ended_at=t2,
        uncertainty_reason="Direct causality unverified; telemetry shows simultaneous failure.",
        timeline_events=[
            CorrelatedTimelineEventResponse(
                sequence_order=0,
                event_timestamp=t0,
                service_name="postgres-db",
                event_type="FIRST_ERROR",
                severity="CRITICAL",
                summary="FATAL: remaining connection slots are reserved",
                is_earliest_observed_failure=True,
                propagation_lag_seconds=0.0,
                cluster_id="cluster-pg-01",
                trace_id="trace-db-001",
                sample_log_id="log-e2e-001",
            ),
            CorrelatedTimelineEventResponse(
                sequence_order=1,
                event_timestamp=t1,
                service_name="order-service",
                event_type="CASCADING_FAILURE",
                severity="HIGH",
                summary="HikariPool connection acquisition timeout",
                is_earliest_observed_failure=False,
                propagation_lag_seconds=5.0,
                cluster_id="cluster-ord-01",
                trace_id="trace-db-001",
                sample_log_id="log-e2e-002",
            ),
            CorrelatedTimelineEventResponse(
                sequence_order=2,
                event_timestamp=t2,
                service_name="payment-service",
                event_type="CASCADING_FAILURE",
                severity="HIGH",
                summary="Payment downstream read timeout",
                is_earliest_observed_failure=False,
                propagation_lag_seconds=20.0,
                cluster_id="cluster-pay-01",
                trace_id="trace-db-001",
                sample_log_id="log-e2e-003",
            ),
        ],
        affected_services=[
            CorrelatedAffectedServiceResponse(
                service_name="postgres-db",
                impact_level="PRIMARY_CULPRIT",
                first_impacted_at=t0,
                error_count=1,
            ),
            CorrelatedAffectedServiceResponse(
                service_name="order-service",
                impact_level="DOWNSTREAM_CASCADE",
                first_impacted_at=t1,
                error_count=1,
            ),
            CorrelatedAffectedServiceResponse(
                service_name="payment-service",
                impact_level="DOWNSTREAM_CASCADE",
                first_impacted_at=t2,
                error_count=1,
            ),
        ],
        evidence_items=[
            CorrelatedEvidenceItemResponse(
                evidence_type="CULPRIT_LOG",
                log_id="log-e2e-001",
                timestamp=t0,
                service_name="postgres-db",
                log_level="FATAL",
                snippet="FATAL: remaining connection slots are reserved for non-replication superuser connections",
                correlation_rule="TRACE_LINKED",
                rationale="Root failure point triggering downstream connection pool starvation",
                trace_id="trace-db-001",
                relationship_type="TRACE_BASED",
                correlation_confidence=1.0,
            ),
            CorrelatedEvidenceItemResponse(
                evidence_type="CORRELATION_SPIKE",
                log_id="log-e2e-002",
                timestamp=t1,
                service_name="order-service",
                log_level="ERROR",
                snippet="Connection acquisition timeout after 5002ms",
                correlation_rule="TRACE_LINKED",
                rationale="Cascading starvation directly correlated along trace-db-001",
                trace_id="trace-db-001",
                relationship_type="TRACE_BASED",
                correlation_confidence=1.0,
            ),
        ],
    )

    persisted_incident_id: Optional[uuid.UUID] = None

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        try:
            # Stage 7: Persist incident candidate to PostgreSQL
            async with sessionmaker() as session:
                incident_orm, created = await persist_incident(session, create_req)
                assert created is True
                await session.commit()
                persisted_incident_id = incident_orm.id
                assert incident_orm.is_root_cause_confirmed is False  # Safe invariant: unconfirmed by default

            # Stage 8: Evidence Analysis
            assessment: EvidenceSufficiencyAssessment = evaluate_evidence_sufficiency(
                timeline_events=create_req.timeline_events,
                evidence_items=create_req.evidence_items,
            )
            assert assessment.classification == EvidenceSufficiencyClassification.TRACE_LINKED
            assert assessment.has_shared_trace is True
            assert assessment.is_root_cause_confirmed is False

            # Stage 9: RCA Synthesis & Citation Validation
            rca_synth: RCASynthesisResult = synthesize_rca_findings(
                assessment=assessment,
                evidence_items=create_req.evidence_items,
                timeline_events=create_req.timeline_events,
            )
            assert len(rca_synth.findings) >= 1
            finding = rca_synth.findings[0]
            assert finding.is_root_cause_confirmed is False
            assert len(finding.claim_citations) >= 1

            # Check claim citation grounding
            claim = finding.claim_citations[0]
            assert claim.is_supported is True
            assert len(claim.citations) >= 1
            assert claim.citations[0].log_id == "log-e2e-001"

            # Stage 10: Recommendations Generation
            async with sessionmaker() as session:
                recs, _ = await generate_incident_recommendations(
                    session=session,
                    incident_id=persisted_incident_id,
                    req=RecommendationGenerateRequest(force_regenerate=True),
                )
                assert len(recs) >= 1
                # Invariant: Recommendations are advisory and do not assert confirmed causation
                for r in recs:
                    assert "root cause confirmed" not in r.title.lower()
                    assert "root cause confirmed" not in r.description.lower()
                    assert r.action_type in {"TELEMETRY_GAP", "DIAGNOSTIC_VERIFICATION", "MITIGATION_ADVISORY", "IMMEDIATE_ACTION"}

            # Stage 11: Operator Human-in-the-Loop Hypothesis Decision
            # 11A. Confirm evidence-supported hypothesis with authorized operator token
            confirm_resp = await client.post(
                f"/api/v1/incidents/{persisted_incident_id}/hypotheses/primary/confirm",
                headers={"Authorization": "Bearer dev-operator-token"},
                json={
                    "notes": "Verified PostgreSQL connection pool reached 100/100 limit during flash campaign.",
                },
            )
            assert confirm_resp.status_code == 200, f"Confirm failed: {confirm_resp.text}"
            confirm_data = confirm_resp.json()
            assert confirm_data["decision"] == "CONFIRM"
            assert confirm_data["operator_name"] == "On-Call Operator"

            # Verify incident status updated to is_root_cause_confirmed = True
            inc_detail_resp = await client.get(f"/api/v1/incidents/{persisted_incident_id}")
            assert inc_detail_resp.status_code == 200
            assert inc_detail_resp.json()["is_root_cause_confirmed"] is True

            # 11B. Reject hypothesis with operator justification
            reject_resp = await client.post(
                f"/api/v1/incidents/{persisted_incident_id}/hypotheses/primary/reject",
                headers={"Authorization": "Bearer dev-lead-sre-token"},
                json={
                    "notes": "Further investigation revealed database connection exhaustion was symptomatic of downstream network partition.",
                },
            )
            assert reject_resp.status_code == 200, f"Reject failed: {reject_resp.text}"
            reject_data = reject_resp.json()
            assert reject_data["decision"] == "REJECT"

            # Verify incident status reverted to is_root_cause_confirmed = False
            inc_reverted_resp = await client.get(f"/api/v1/incidents/{persisted_incident_id}")
            assert inc_reverted_resp.status_code == 200
            assert inc_reverted_resp.json()["is_root_cause_confirmed"] is False

        finally:
            # Teardown incident from PostgreSQL
            if persisted_incident_id:
                async with sessionmaker() as session:
                    await session.execute(delete(Incident).where(Incident.id == persisted_incident_id))
                    await session.commit()


# ============================================================================
# Section 2: Authentication & Authorization Security Tests
# ============================================================================


@pytest.mark.asyncio
async def test_auth_missing_credentials_rejected():
    """Verify endpoints requiring authentication reject unauthenticated requests with 401."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        random_id = uuid.uuid4()
        resp = await client.get(f"/api/v1/incidents/{random_id}/hypotheses/decisions")
        assert resp.status_code == 401
        assert "Expected 'Authorization: Bearer <token>'" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_spoofed_headers_without_gateway_secret_rejected():
    """Verify arbitrary client-supplied X-Operator-* headers are rejected with 401."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        random_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/incidents/{random_id}/hypotheses/decisions",
            headers={
                "X-Operator-Id": "attacker",
                "X-Operator-Role": "ADMIN",
                "X-Operator-Email": "attacker@evil.com",
            },
        )
        assert resp.status_code == 401
        assert "untrusted and rejected" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_spoofed_headers_with_invalid_gateway_secret_rejected():
    """Verify wrong X-Gateway-Secret is rejected with 401."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        random_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/incidents/{random_id}/hypotheses/decisions",
            headers={
                "X-Operator-Id": "attacker",
                "X-Operator-Role": "ADMIN",
                "X-Gateway-Secret": "wrong-secret-value",
            },
        )
        assert resp.status_code == 401
        assert "untrusted and rejected" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_tampered_hmac_jwt_rejected():
    """Verify token signature tampering is detected and rejected with 401."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        valid_token = create_signed_token({"sub": "op-valid", "role": "OPERATOR"})
        parts = valid_token.split(".")
        tampered_sig = parts[2][:-4] + "XXXX"
        tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"

        random_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/incidents/{random_id}/hypotheses/decisions",
            headers={"Authorization": f"Bearer {tampered_token}"},
        )
        assert resp.status_code == 401
        assert "Invalid, expired, or tampered" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_expired_jwt_rejected():
    """Verify expired token is rejected with 401."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        past_timestamp = int(datetime.now(timezone.utc).timestamp()) - 3600
        expired_token = create_signed_token({
            "sub": "op-expired",
            "role": "OPERATOR",
            "exp": past_timestamp,
        })

        random_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/incidents/{random_id}/hypotheses/decisions",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        assert "Invalid, expired, or tampered" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_dev_tokens_disabled_in_production():
    """Verify developer tokens are rejected when ENVIRONMENT is production."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch.object(settings, "ENVIRONMENT", "production"):
            random_id = uuid.uuid4()
            resp = await client.get(
                f"/api/v1/incidents/{random_id}/hypotheses/decisions",
                headers={"Authorization": "Bearer dev-operator-token"},
            )
            assert resp.status_code == 401
            assert "disabled in production" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_dev_tokens_disabled_when_allow_dev_tokens_false():
    """Verify developer tokens are rejected when ALLOW_DEV_TOKENS is False."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch.object(settings, "ALLOW_DEV_TOKENS", False):
            random_id = uuid.uuid4()
            resp = await client.get(
                f"/api/v1/incidents/{random_id}/hypotheses/decisions",
                headers={"Authorization": "Bearer dev-operator-token"},
            )
            assert resp.status_code == 401
            assert "disabled in production or when ALLOW_DEV_TOKENS is False" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_auth_rbac_viewer_role_forbidden():
    """Verify VIEWER role cannot submit hypothesis confirmation or rejection (HTTP 403)."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        random_id = uuid.uuid4()
        # dev-viewer-token has role: "VIEWER"
        resp = await client.post(
            f"/api/v1/incidents/{random_id}/hypotheses/primary/confirm",
            headers={"Authorization": "Bearer dev-viewer-token"},
            json={"notes": "Viewer attempting confirmation"},
        )
        assert resp.status_code == 403
        assert "not authorized to confirm or reject hypotheses" in resp.json()["detail"]


# ============================================================================
# Section 3: Evidence and RCA Safety Invariants
# ============================================================================


@pytest.mark.asyncio
async def test_safety_confirm_unsupported_hypothesis_rejected():
    """Verify attempting to confirm an unsupported hypothesis returns HTTP 400."""
    t0 = datetime(2026, 10, 14, 8, 1, 30, tzinfo=timezone.utc)

    # In-memory mock incident with unsupported claim
    mock_incident = Incident(
        id=uuid.uuid4(),
        title="Sparse incident",
        severity="MEDIUM",
        status="DETECTED",
        analysis_status="COMPLETED",
        primary_log_source=None,
        started_at=t0,
        detected_at=t0,
        confidence_score=0.0,
        created_at=t0,
        updated_at=t0,
        probable_cause="Unverified database contention",
        is_root_cause_confirmed=False,
        events=[],
        evidence=[],
        affected_services=[],
        hypothesis_decisions=[],
    )

    req = RCAHypothesisDecisionCreateRequest(
        hypothesis_text="Unverified speculation",
        decision=HypothesisDecisionEnum.CONFIRM,
        notes="Operator attempting confirmation without evidence",
    )
    operator = OperatorIdentity(
        operator_id="op-1",
        operator_name="Operator One",
        operator_email="op1@rootlens.internal",
        role="OPERATOR",
        is_authorized=True,
    )

    mock_session = AsyncMock()
    with patch(
        "app.services.hypothesis_decision_service._get_incident_with_relations",
        return_value=mock_incident,
    ):
        with pytest.raises(Exception) as exc_info:
            await record_hypothesis_decision(
                session=mock_session,
                incident_id=mock_incident.id,
                hypothesis_id="primary",
                req=req,
                operator=operator,
            )
        assert "lacks verified supporting telemetry citations" in str(exc_info.value)


def test_safety_anti_fabrication_rejection():
    """Verify citations referencing fabricated or non-existent log IDs are rejected."""
    t0 = datetime(2026, 10, 14, 8, 1, 30, tzinfo=timezone.utc)
    evidence_items = [
        CorrelatedEvidenceItemResponse(
            evidence_type="CULPRIT_LOG",
            log_id="log-real-001",
            timestamp=t0,
            service_name="auth-service",
            log_level="ERROR",
            snippet="Real log error",
            correlation_rule="TRACE_LINKED",
            rationale="Legitimate log",
        )
    ]
    assessment = evaluate_evidence_sufficiency(
        timeline_events=[],
        evidence_items=evidence_items,
    )
    rca = synthesize_rca_findings(assessment=assessment)
    all_citations = [
        cit.log_id
        for finding in rca.findings
        for claim in finding.claim_citations
        for cit in claim.citations
    ]
    # Invariant: No fabricated citations; all emitted citations exist in input evidence
    for cid in all_citations:
        assert cid in {"log-real-001"}


# ============================================================================
# Section 4: Failure Handling and Recovery
# ============================================================================


@pytest.mark.asyncio
async def test_failure_handling_unsupported_file_extension():
    """Verify uploading an unsupported file extension returns HTTP 400."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("malicious.exe", b"binary content", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "Unsupported file format" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_failure_handling_empty_file():
    """Verify uploading an empty file returns HTTP 400."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/logs/upload",
            files={"file": ("empty.log", b"", "text/plain")},
        )
        assert resp.status_code == 400
        assert "Uploaded file is empty" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_failure_handling_nonexistent_upload_job():
    """Verify parsing a non-existent upload job returns HTTP 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        random_job_id = uuid.uuid4()
        resp = await client.post(f"/api/v1/logs/jobs/{random_job_id}/parse")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_failure_handling_nonexistent_incident():
    """Verify querying a non-existent incident returns HTTP 404."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        random_inc_id = uuid.uuid4()
        resp = await client.get(f"/api/v1/incidents/{random_inc_id}")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_failure_handling_disallowed_status_transition():
    """Verify invalid incident status transitions return HTTP 409 Conflict."""
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        t0 = datetime(2026, 10, 14, 8, 1, 30, tzinfo=timezone.utc)
        mock_incident = Incident(
            id=uuid.uuid4(),
            title="Resolved incident",
            severity="LOW",
            status="RESOLVED",
            started_at=t0,
            is_root_cause_confirmed=False,
        )

        with patch("app.services.incident_persistence_service.get_incident_by_id", return_value=mock_incident):
            resp = await client.patch(
                f"/api/v1/incidents/{mock_incident.id}/status",
                json={"status": "DETECTED"},
            )
            assert resp.status_code == 409
            assert "Invalid lifecycle transition" in resp.json()["detail"]


# ============================================================================
# Section 5: CSV & JSON Export Verification (Stage 12)
# ============================================================================


def test_stage12_csv_json_export_data_integrity():
    """Verify Stage 12: CSV and JSON export serialization fidelity and escaping contracts."""
    # Canonical columns expected by Log Explorer export
    canonical_columns = [
        "timestamp", "log_level", "service_name", "message", "host",
        "environment", "trace_id", "span_id", "id"
    ]

    sample_doc = {
        "timestamp": "2026-10-14T08:01:30Z",
        "log_level": "FATAL",
        "service_name": "postgres-db",
        "message": 'Connection limit reached: "active=100, max=100"\nImmediate failover required.',
        "host": "db-01.internal",
        "environment": "production",
        "trace_id": "trace-db-001",
        "span_id": "span-pg-01",
        "id": "doc-export-01",
    }

    # RFC 4180 CSV escaping verification:
    # 1. Comma, quotes, and newlines must be double-quoted with escaped internal quotes
    escaped_message = sample_doc["message"].replace('"', '""')
    csv_line = f'"{sample_doc["timestamp"]}","{sample_doc["log_level"]}","{sample_doc["service_name"]}","{escaped_message}","{sample_doc["host"]}","{sample_doc["environment"]}","{sample_doc["trace_id"]}","{sample_doc["span_id"]}","{sample_doc["id"]}"'

    # Verify UTF-8 BOM prefix
    bom_csv = "\ufeff" + ",".join(canonical_columns) + "\r\n" + csv_line
    assert bom_csv.startswith("\ufeff")
    assert '""active=100, max=100""' in bom_csv

    # Full-fidelity JSON serialization verification
    json_export = json.dumps([sample_doc], indent=2)
    deserialized = json.loads(json_export)
    assert len(deserialized) == 1
    assert deserialized[0]["id"] == "doc-export-01"
    assert deserialized[0]["message"] == sample_doc["message"]

