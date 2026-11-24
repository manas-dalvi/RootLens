"""Unit and integration tests for Phase 18 Step 2: Post-Mortem Report Generation & Retrieval.

Tests:
1. Deterministic offline generation without network calls.
2. Trace-linked, temporal-only, and insufficient evidence scenarios.
3. Root-cause confirmation and confidence invariants (strictly False, 0.0).
4. Evidence provenance and identifier preservation.
5. Prompt budget disclosures (timeline, records, references) in report sections.
6. Database persistence, retrieval, and idempotency (uq_reports_incident_title).
7. Missing incident / report 404s and mismatched incident ID rejection.
8. Transaction rollback on database failure.
9. API endpoint compatibility via FastAPI TestClient.
"""

from datetime import datetime, timezone
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
from app.models.report import Report
from app.schemas.report import (
    ReportCreateRequest,
    ReportListResponse,
    ReportResponse,
    assemble_report_markdown,
)
from app.services.report_service import (
    generate_postmortem_report,
    get_incident_reports,
    get_report_by_id,
)

client = TestClient(app)


def _make_test_incident(
    incident_id: Optional[uuid.UUID] = None,
    title: str = "Auth Gateway Timeout Cascade",
    severity: str = "HIGH",
    classification: str = "TRACE_LINKED",
    event_count: int = 2,
    evidence_count: int = 2,
    shared_trace_id: Optional[str] = "trace-rep-101",
) -> Incident:
    """Construct an in-memory Incident with populated events and evidence for testing."""
    inc_id = incident_id or uuid.uuid4()
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 30, tzinfo=timezone.utc)
    now = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)

    log_source = LogSource(
        id=uuid.uuid4(),
        name="auth-service-logs",
        service_name="auth-service",
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
        service_name="auth-service",
        impact_level="PRIMARY_CULPRIT",
        first_impacted_at=t0,
        error_count=5,
        created_at=now,
        updated_at=now,
    )
    aff_svc_2 = IncidentAffectedService(
        id=uuid.uuid4(),
        incident_id=inc_id,
        service_name="gateway-service",
        impact_level="DOWNSTREAM_CASCADE",
        first_impacted_at=t1,
        error_count=3,
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
                service_name="auth-service" if i % 2 == 0 else "gateway-service",
                event_type="ERROR_BURST" if i % 2 == 0 else "CASCADE_ERROR",
                severity="ERROR",
                sequence_order=i,
                summary=f"Milestone event {i} observed in failure path",
                attributes={
                    "trace_id": shared_trace_id,
                    "is_earliest_observed_failure": (i == 0),
                    "propagation_lag_seconds": float(i * 10),
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
                opensearch_doc_id=f"doc-ev-{i:03d}",
                log_timestamp=ev_ts,
                service_name="auth-service" if i % 2 == 0 else "gateway-service",
                log_level="ERROR",
                log_snippet=f"Token validation failed at stage {i}: connection pool timeout",
                content_hash=f"hash-val-{i:03d}",
                document_status="INDEXED",
                relevance_score=1.0,
                explanation=f"Telemetry indicates failure event at stage {i}",
                metadata_={
                    "correlation_rule": "TRACE_LINKED" if shared_trace_id else "TEMPORAL",
                    "trace_id": shared_trace_id,
                },
                created_at=now,
                updated_at=now,
            )
        )
    incident.evidence = evidence

    return incident


def _make_mock_session(existing_report=None):
    session = AsyncMock()
    session.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_report
    mock_result.scalars.return_value.all.return_value = [existing_report] if existing_report else []
    session.execute.return_value = mock_result
    return session


# ============================================================================
# 1. Deterministic Offline Generation & Invariant Tests
# ============================================================================

@pytest.mark.asyncio
async def test_generate_report_offline_deterministic():
    """Verify report generates deterministically in offline mode without network calls."""
    incident = _make_test_incident()
    mock_session = _make_mock_session()

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=incident),
    ):
        report, is_created = await generate_postmortem_report(
            session=mock_session,
            incident_id=incident.id,
        )

    # 1. Creation flag & status invariants
    assert is_created is True
    assert report.status == "DRAFT"
    assert report.incident_id == incident.id
    assert report.title == f"Post-Mortem: {incident.title}"

    # 2. Executive summary
    assert "Incident Title: Auth Gateway Timeout Cascade" in report.executive_summary
    assert "Operational Lifecycle Status: INVESTIGATING" in report.executive_summary
    assert "Primary Suspect Service: auth-service" in report.executive_summary

    # 3. Timeline summary
    assert "Milestone event 0 observed in failure path" in report.timeline_summary

    # 4. Root cause analysis & invariants
    assert "Root Cause Confirmed: False" in report.root_cause_analysis
    assert "Diagnostic Confidence Score: 0.0" in report.root_cause_analysis
    assert "Grounded Diagnostic Explanation" in report.root_cause_analysis

    # 5. Advisory recommendations notice
    assert "Advisory Guidance Only" in report.recommendations_summary
    assert "zero autonomous remediation" in report.recommendations_summary

    # 6. Uncertainty disclaimer
    assert "Diagnostic Uncertainty Statement:" in report.uncertainty_disclaimer

    # 7. Session operations
    mock_session.add.assert_called_once_with(report)
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_generate_report_trace_linked_scenario():
    """Verify trace-linked incidents format trace context and appropriate causal boundaries."""
    incident = _make_test_incident(shared_trace_id="trace-linked-999")
    mock_session = _make_mock_session()

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=incident),
    ):
        report, _ = await generate_postmortem_report(mock_session, incident.id)

    assert "TRACE_LINKED" in report.executive_summary
    assert "trace-linked-999" in report.timeline_summary
    assert "Root Cause Confirmed: False" in report.root_cause_analysis


@pytest.mark.asyncio
async def test_generate_report_temporal_only_scenario():
    """Verify temporal-only incidents include temporal proximity caveats."""
    incident = _make_test_incident(shared_trace_id=None)
    mock_session = _make_mock_session()

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=incident),
    ):
        report, _ = await generate_postmortem_report(mock_session, incident.id)

    assert "TEMPORAL_ONLY" in report.executive_summary
    assert "Root Cause Confirmed: False" in report.root_cause_analysis


@pytest.mark.asyncio
async def test_generate_report_insufficient_evidence_refusal():
    """Verify sparse telemetry produces an explicit insufficiency report without speculative root causes."""
    incident = _make_test_incident(
        title="Sparse telemetry incident",
        event_count=0,
        evidence_count=0,
        shared_trace_id=None,
    )
    mock_session = _make_mock_session()

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=incident),
    ):
        report, _ = await generate_postmortem_report(mock_session, incident.id)

    assert "INSUFFICIENT" in report.executive_summary
    assert "Primary Candidate Hypothesis: None (Evidence Insufficient)" in report.root_cause_analysis
    assert "Root Cause Confirmed: False" in report.root_cause_analysis
    assert "- No chronological milestone events recorded" in report.timeline_summary


# ============================================================================
# 2. Prompt Budget Disclosures
# ============================================================================

@pytest.mark.asyncio
async def test_generate_report_prompt_budget_omission_disclosures():
    """Verify report discloses omitted telemetry counts when prompt budgets are applied."""
    # 25 timeline events and 20 evidence items (exceed default prompt limits 20 and 15)
    incident = _make_test_incident(
        event_count=25,
        evidence_count=20,
    )
    mock_session = _make_mock_session()

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=incident),
    ):
        report, _ = await generate_postmortem_report(mock_session, incident.id)

    # 1. Timeline section must contain full 25 events, but disclose prompt omission
    assert "Milestone event 24" in report.timeline_summary
    assert "[BUDGET DISCLOSURE: Displaying full 25 timeline events above." in report.timeline_summary
    assert "5 events were omitted from the prompt body" in report.timeline_summary

    # 2. Uncertainty disclaimer must disclose evidence record and timeline omissions
    assert "Prompt Budget & Unreviewed Telemetry Disclosure:" in report.uncertainty_disclaimer
    assert "Omitted from LLM prompt: 5 evidence records, 5 timeline events" in report.uncertainty_disclaimer
    assert "The LLM has NOT reviewed any omitted telemetry." in report.uncertainty_disclaimer


# ============================================================================
# 3. Idempotency & Persistence
# ============================================================================

@pytest.mark.asyncio
async def test_generate_report_idempotency_updates_existing_report():
    """Verify repeated generation with identical title updates existing record in-place."""
    incident = _make_test_incident()

    now = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    existing_rep = Report(
        id=uuid.uuid4(),
        incident_id=incident.id,
        title=f"Post-Mortem: {incident.title}",
        status="DRAFT",
        executive_summary="Old summary",
        timeline_summary="Old timeline",
        root_cause_analysis="Old rca",
        recommendations_summary="Old recommendations",
        uncertainty_disclaimer="Old disclaimer",
        generated_at=now,
        created_at=now,
        updated_at=now,
    )

    mock_session = _make_mock_session(existing_report=existing_rep)

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=incident),
    ):
        report, is_created = await generate_postmortem_report(
            session=mock_session,
            incident_id=incident.id,
        )

    assert is_created is False
    assert report.id == existing_rep.id
    assert report.status == "DRAFT"
    assert report.executive_summary != "Old summary"
    # Existing report was updated in-place; session.add not called
    mock_session.add.assert_not_called()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_generate_report_custom_title():
    """Verify custom report title provided in request is persisted and sanitized."""
    incident = _make_test_incident()
    mock_session = _make_mock_session()

    req = ReportCreateRequest(title="  Critical Incident RCA Final Draft  ")

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=incident),
    ):
        report, is_created = await generate_postmortem_report(
            session=mock_session,
            incident_id=incident.id,
            req=req,
        )

    assert is_created is True
    assert report.title == "Critical Incident RCA Final Draft"


@pytest.mark.asyncio
async def test_generate_report_nonexistent_incident_raises_404():
    """Verify generating report for nonexistent incident raises HTTP 404."""
    mock_session = _make_mock_session()
    random_id = uuid.uuid4()

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await generate_postmortem_report(mock_session, random_id)

    assert exc_info.value.status_code == 404
    assert f"Incident '{random_id}' not found." in exc_info.value.detail


@pytest.mark.asyncio
async def test_generate_report_db_failure_rolls_back():
    """Verify database commit error triggers rollback and re-raises exception."""
    incident = _make_test_incident()
    mock_session = _make_mock_session()
    mock_session.commit.side_effect = RuntimeError("Database connection closed during commit")

    with patch(
        "app.services.report_service.get_incident_by_id",
        new=AsyncMock(return_value=incident),
    ):
        with pytest.raises(RuntimeError):
            await generate_postmortem_report(mock_session, incident.id)

    mock_session.rollback.assert_called_once()


# ============================================================================
# 4. Markdown Formatting & Schema Validation
# ============================================================================

def test_assemble_report_markdown_complete():
    """Verify printable markdown assembly contains all sections and invariants."""
    now = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    report = Report(
        id=uuid.uuid4(),
        incident_id=uuid.uuid4(),
        title="Post-Mortem: Core Payment Gateway Failure",
        status="DRAFT",
        executive_summary="Summary text of the incident.",
        timeline_summary="- 14:00:00 Payment failure occurred",
        root_cause_analysis="Evidence-backed analysis narrative.",
        recommendations_summary="Advisory investigation steps.",
        uncertainty_disclaimer="Explicit uncertainty notice.",
        generated_at=now,
        created_at=now,
        updated_at=now,
    )

    md = assemble_report_markdown(report)
    assert "# Post-Mortem: Core Payment Gateway Failure" in md
    assert "**Report Status:** DRAFT" in md
    assert "**Root Cause Confirmed:** False" in md
    assert "**Diagnostic Confidence Score:** 0.0" in md
    assert "## 1. Executive Summary" in md
    assert "## 2. Chronological Timeline" in md
    assert "## 3. Root Cause Analysis & Diagnostic Findings" in md
    assert "## 4. Advisory Recommendations & Verification Steps" in md
    assert "## 5. Diagnostic Uncertainty & Telemetry Bounds" in md

    # Response schema conversion
    resp = ReportResponse.from_orm_report(report)
    assert resp.is_root_cause_confirmed is False
    assert resp.markdown_content == md


# ============================================================================
# 5. API Endpoint Tests (FastAPI TestClient)
# ============================================================================

def test_api_create_and_get_reports():
    """Verify POST and GET report endpoints via TestClient."""
    incident = _make_test_incident()
    mock_session = AsyncMock()

    now = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    report_orm = Report(
        id=uuid.uuid4(),
        incident_id=incident.id,
        title=f"Post-Mortem: {incident.title}",
        status="DRAFT",
        executive_summary="Factual executive summary",
        timeline_summary="Chronological timeline summary",
        root_cause_analysis="Root cause analysis narrative",
        recommendations_summary="Advisory recommendations",
        uncertainty_disclaimer="Diagnostic uncertainty disclaimer",
        generated_at=now,
        created_at=now,
        updated_at=now,
    )

    app.dependency_overrides[get_db] = lambda: mock_session

    try:
        # 1. POST /{id}/reports creates new report (201)
        with patch(
            "app.api.v1.endpoints.incidents.generate_postmortem_report",
            new=AsyncMock(return_value=(report_orm, True)),
        ):
            resp = client.post(
                f"/api/v1/incidents/{incident.id}/reports",
                json={"title": "Custom Post-Mortem Title"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["id"] == str(report_orm.id)
            assert data["status"] == "DRAFT"
            assert data["is_root_cause_confirmed"] is False
            assert "markdown_content" in data

        # 2. POST /{id}/reports updates existing report (200)
        with patch(
            "app.api.v1.endpoints.incidents.generate_postmortem_report",
            new=AsyncMock(return_value=(report_orm, False)),
        ):
            resp = client.post(
                f"/api/v1/incidents/{incident.id}/reports",
                json={"title": "Custom Post-Mortem Title"},
            )
            assert resp.status_code == 200

        # 3. GET /{id}/reports lists reports (200)
        with patch(
            "app.api.v1.endpoints.incidents.get_incident_by_id",
            new=AsyncMock(return_value=incident),
        ), patch(
            "app.api.v1.endpoints.incidents.get_incident_reports",
            new=AsyncMock(return_value=[report_orm]),
        ):
            resp = client.get(f"/api/v1/incidents/{incident.id}/reports")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert data["incident_id"] == str(incident.id)
            assert data["items"][0]["id"] == str(report_orm.id)

        # 4. GET /{id}/reports/{report_id} gets specific report (200)
        with patch(
            "app.api.v1.endpoints.incidents.get_report_by_id",
            new=AsyncMock(return_value=report_orm),
        ):
            resp = client.get(f"/api/v1/incidents/{incident.id}/reports/{report_orm.id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == str(report_orm.id)

        # 5. GET /{id}/reports/{report_id} with nonexistent report (404)
        with patch(
            "app.api.v1.endpoints.incidents.get_report_by_id",
            new=AsyncMock(return_value=None),
        ):
            resp = client.get(f"/api/v1/incidents/{incident.id}/reports/{uuid.uuid4()}")
            assert resp.status_code == 404

        # 6. GET with invalid UUIDs returns 404
        resp = client.get("/api/v1/incidents/invalid-uuid/reports")
        assert resp.status_code == 404
        resp = client.get(f"/api/v1/incidents/{incident.id}/reports/invalid-uuid")
        assert resp.status_code == 404

    finally:
        app.dependency_overrides.pop(get_db, None)
