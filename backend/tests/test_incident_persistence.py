from datetime import datetime, timezone
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
from app.schemas.incident import (
    CorrelatedAffectedServiceResponse,
    CorrelatedEvidenceItemResponse,
    CorrelatedTimelineEventResponse,
    IncidentCreateRequest,
    IncidentStatusEnum,
    IncidentStatusUpdateRequest,
)
from app.services.incident_persistence_service import (
    ALLOWED_INCIDENT_STATUS_TRANSITIONS,
    compute_deterministic_incident_uuid,
    map_incident_to_detail_response,
    map_incident_to_summary_response,
    persist_incident,
    update_incident_status,
    validate_incident_status_transition,
    validate_timeline_events,
)

client = TestClient(app)


def _make_dummy_incident_orm(
    incident_id: uuid.UUID = None,
    candidate_id: str = "cand-test-1",
) -> Incident:
    """Construct an in-memory SQLAlchemy Incident ORM instance for test responses."""
    inc_id = incident_id or compute_deterministic_incident_uuid(candidate_id)
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
        title="Cross-service failure cascade in payment-service",
        severity="HIGH",
        status="DETECTED",
        analysis_status="PENDING",
        primary_log_source_id=log_source.id,
        started_at=t0,
        ended_at=t1,
        detected_at=now,
        probable_cause=None,
        confidence_score=0.0,
        is_root_cause_confirmed=False,
        uncertainty_reason="Earliest observed failure identified chronologically. Requires Phase 15 verification.",
        created_at=now,
        updated_at=now,
    )
    incident.primary_log_source = log_source

    aff_svc = IncidentAffectedService(
        id=uuid.uuid4(),
        incident_id=inc_id,
        log_source_id=log_source.id,
        service_name="payment-service",
        impact_level="PRIMARY_CULPRIT",
        first_impacted_at=t0,
        error_count=5,
        created_at=now,
        updated_at=now,
    )
    incident.affected_services = [aff_svc]

    ev_id = uuid.uuid4()
    event = IncidentEvent(
        id=ev_id,
        incident_id=inc_id,
        event_timestamp=t0,
        service_name="payment-service",
        event_type="FIRST_ERROR",
        severity="ERROR",
        sequence_order=0,
        summary="Earliest observed failure in payment-service",
        attributes={
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "log-p1",
        },
        created_at=now,
        updated_at=now,
    )
    incident.events = [event]

    ev_item = Evidence(
        id=uuid.uuid4(),
        incident_id=inc_id,
        incident_event_id=ev_id,
        evidence_type="CULPRIT_LOG",
        opensearch_index="rootlens-logs-*",
        opensearch_doc_id="log-p1",
        log_timestamp=t0,
        service_name="payment-service",
        log_level="ERROR",
        log_snippet="Connection to database timed out",
        content_hash="abc123hash",
        document_status="INDEXED",
        relevance_score=1.0,
        explanation="Earliest observed failure event in payment-service",
        metadata_={"correlation_rule": "FIRST_FAILURE"},
        created_at=now,
        updated_at=now,
    )
    incident.evidence = [ev_item]

    return incident


# ============================================================================
# 1. Deterministic UUID & Mapping Tests
# ============================================================================


def test_compute_deterministic_incident_uuid():
    """Verify stable UUID generation from candidate_id string."""
    u1 = compute_deterministic_incident_uuid("cand-payment-20261010140000-abcd")
    u2 = compute_deterministic_incident_uuid("cand-payment-20261010140000-abcd")
    u3 = compute_deterministic_incident_uuid("cand-other-20261010140000-1234")

    assert u1 == u2
    assert u1 != u3
    assert isinstance(u1, uuid.UUID)


def test_map_incident_to_detail_and_summary_response():
    """Verify ORM to Pydantic mapping retains all relationships and truthful flags."""
    inc_orm = _make_dummy_incident_orm()

    detail = map_incident_to_detail_response(inc_orm)
    assert detail.id == str(inc_orm.id)
    assert detail.title == inc_orm.title
    assert detail.is_root_cause_confirmed is False
    assert len(detail.affected_services) == 1
    assert len(detail.timeline_events) == 1
    assert len(detail.evidence_items) == 1

    summary = map_incident_to_summary_response(inc_orm)
    assert summary.id == str(inc_orm.id)
    assert summary.affected_service_count == 1
    assert summary.event_count == 1
    assert summary.evidence_count == 1
    assert summary.primary_service == "payment-service"


# ============================================================================
# 2. Incident Creation API Tests
# ============================================================================


def test_persist_incident_creation_success():
    """Verify POST /api/v1/incidents successfully persists new incident candidate."""
    inc_orm = _make_dummy_incident_orm(candidate_id="cand-new-1")

    payload = {
        "candidate_id": "cand-new-1",
        "title": "Cross-service failure cascade in payment-service",
        "severity": "HIGH",
        "started_at": "2026-10-10T14:00:00Z",
        "ended_at": "2026-10-10T14:00:30Z",
        "correlation_rules_used": ["TRACE_LINKED"],
        "uncertainty_reason": "Earliest observed failure identified chronologically.",
        "affected_services": [
            {
                "service_name": "payment-service",
                "impact_level": "PRIMARY_CULPRIT",
                "first_impacted_at": "2026-10-10T14:00:00Z",
                "error_count": 5,
            }
        ],
        "timeline_events": [
            {
                "sequence_order": 0,
                "event_timestamp": "2026-10-10T14:00:00Z",
                "service_name": "payment-service",
                "event_type": "FIRST_ERROR",
                "severity": "ERROR",
                "summary": "Earliest observed failure",
                "is_earliest_observed_failure": True,
                "propagation_lag_seconds": 0.0,
                "cluster_id": "cl-1",
                "sample_log_id": "log-p1",
            }
        ],
        "evidence_items": [
            {
                "evidence_type": "CULPRIT_LOG",
                "log_id": "log-p1",
                "timestamp": "2026-10-10T14:00:00Z",
                "service_name": "payment-service",
                "log_level": "ERROR",
                "snippet": "Connection timeout",
                "correlation_rule": "FIRST_FAILURE",
                "rationale": "Earliest failure",
                "content_hash": "abc123hash",
            }
        ],
    }

    with patch(
        "app.api.v1.endpoints.incidents.persist_incident",
        new=AsyncMock(return_value=(inc_orm, True)),
    ):
        resp = client.post("/api/v1/incidents", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == str(inc_orm.id)
        assert data["title"] == inc_orm.title
        assert data["is_root_cause_confirmed"] is False
        assert len(data["affected_services"]) == 1
        assert len(data["timeline_events"]) == 1


def test_persist_incident_duplicate_idempotent():
    """Verify POST /api/v1/incidents returns HTTP 200 when candidate was already persisted."""
    inc_orm = _make_dummy_incident_orm(candidate_id="cand-dup-1")

    payload = {
        "candidate_id": "cand-dup-1",
        "title": "Existing Incident",
        "severity": "MEDIUM",
        "started_at": "2026-10-10T14:00:00Z",
    }

    with patch(
        "app.api.v1.endpoints.incidents.persist_incident",
        new=AsyncMock(return_value=(inc_orm, False)),  # created = False indicates existing
    ):
        resp = client.post("/api/v1/incidents", json=payload)
        assert resp.status_code == 200  # Idempotent response
        data = resp.json()
        assert data["id"] == str(inc_orm.id)


def test_persist_incident_validation_error():
    """Verify HTTP 422 when required fields are missing."""
    resp = client.post("/api/v1/incidents", json={"invalid": "payload"})
    assert resp.status_code == 422


# ============================================================================
# 3. Incident Catalogue & Detail Retrieval API Tests
# ============================================================================


def test_list_persisted_incidents_success():
    """Verify GET /api/v1/incidents returns paginated catalogue."""
    inc_orm = _make_dummy_incident_orm()
    summary_item = map_incident_to_summary_response(inc_orm)

    mock_list_response = MagicMock(
        items=[summary_item],
        total=1,
        page=1,
        page_size=20,
        total_pages=1,
    )

    with patch(
        "app.api.v1.endpoints.incidents.list_incident_catalogue",
        new=AsyncMock(return_value=mock_list_response),
    ):
        resp = client.get("/api/v1/incidents?severity=HIGH")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == inc_orm.title


def test_get_persisted_incident_by_id_success():
    """Verify GET /api/v1/incidents/{id} returns full incident details."""
    inc_orm = _make_dummy_incident_orm()

    with patch(
        "app.api.v1.endpoints.incidents.get_incident_by_id",
        new=AsyncMock(return_value=inc_orm),
    ):
        resp = client.get(f"/api/v1/incidents/{inc_orm.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(inc_orm.id)
        assert data["severity"] == "HIGH"
        assert len(data["timeline_events"]) == 1
        assert data["timeline_events"][0]["event_type"] == "FIRST_ERROR"


def test_get_persisted_incident_not_found():
    """Verify GET /api/v1/incidents/{id} returns HTTP 404 when ID does not exist."""
    random_id = str(uuid.uuid4())
    with patch(
        "app.api.v1.endpoints.incidents.get_incident_by_id",
        new=AsyncMock(return_value=None),
    ):
        resp = client.get(f"/api/v1/incidents/{random_id}")
        assert resp.status_code == 404
        assert f"Incident '{random_id}' not found." in resp.json()["detail"]


def test_get_persisted_incident_invalid_uuid():
    """Verify GET /api/v1/incidents/invalid-uuid returns HTTP 404."""
    resp = client.get("/api/v1/incidents/not-a-valid-uuid")
    assert resp.status_code == 404
    assert "Incident 'not-a-valid-uuid' not found." in resp.json()["detail"]


# ============================================================================
# 4. Incident Operational Lifecycle Status Tests (Phase 16, Step 1)
# ============================================================================


def test_lifecycle_allowed_transitions_validator():
    """Verify centralized transition validator permits defined transitions and idempotency."""
    # DETECTED transitions
    for target in ("INVESTIGATING", "MITIGATED", "RESOLVED", "FALSE_POSITIVE"):
        validate_incident_status_transition("DETECTED", target)

    # INVESTIGATING transitions
    for target in ("MITIGATED", "RESOLVED", "FALSE_POSITIVE"):
        validate_incident_status_transition("INVESTIGATING", target)

    # MITIGATED transitions
    for target in ("INVESTIGATING", "RESOLVED"):
        validate_incident_status_transition("MITIGATED", target)

    # Idempotent (same-state) transitions must succeed without error
    for current in ("DETECTED", "INVESTIGATING", "MITIGATED", "RESOLVED", "FALSE_POSITIVE"):
        validate_incident_status_transition(current, current)


def test_lifecycle_disallowed_transitions_raise_409():
    """Verify centralized validator raises HTTP 409 for invalid transitions or terminal states."""
    # Terminal state: RESOLVED cannot transition to any new status
    for target in ("DETECTED", "INVESTIGATING", "MITIGATED", "FALSE_POSITIVE"):
        with pytest.raises(HTTPException) as exc_info:
            validate_incident_status_transition("RESOLVED", target)
        assert exc_info.value.status_code == 409
        assert "Invalid lifecycle transition" in exc_info.value.detail

    # Terminal state: FALSE_POSITIVE cannot transition to any new status
    for target in ("DETECTED", "INVESTIGATING", "MITIGATED", "RESOLVED"):
        with pytest.raises(HTTPException) as exc_info:
            validate_incident_status_transition("FALSE_POSITIVE", target)
        assert exc_info.value.status_code == 409

    # MITIGATED cannot move back to DETECTED or to FALSE_POSITIVE
    with pytest.raises(HTTPException) as exc_info:
        validate_incident_status_transition("MITIGATED", "DETECTED")
    assert exc_info.value.status_code == 409

    with pytest.raises(HTTPException) as exc_info:
        validate_incident_status_transition("MITIGATED", "FALSE_POSITIVE")
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_update_incident_status_detected_to_investigating():
    """Verify DETECTED -> INVESTIGATING updates status, refreshes updated_at, keeps ended_at None."""
    inc = _make_dummy_incident_orm()
    inc.status = "DETECTED"
    inc.ended_at = None
    original_updated_at = inc.updated_at

    mock_session = AsyncMock()
    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=inc),
    ):
        req = IncidentStatusUpdateRequest(status=IncidentStatusEnum.INVESTIGATING)
        result = await update_incident_status(mock_session, inc.id, req)

        assert result.status == "INVESTIGATING"
        assert result.ended_at is None
        assert result.updated_at >= original_updated_at
        assert result.is_root_cause_confirmed is False
        mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_incident_status_investigating_to_resolved_sets_ended_at():
    """Verify INVESTIGATING -> RESOLVED sets ended_at automatically and preserves root cause flag."""
    inc = _make_dummy_incident_orm()
    inc.status = "INVESTIGATING"
    inc.ended_at = None

    mock_session = AsyncMock()
    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=inc),
    ):
        req = IncidentStatusUpdateRequest(status=IncidentStatusEnum.RESOLVED)
        result = await update_incident_status(mock_session, inc.id, req)

        assert result.status == "RESOLVED"
        assert result.ended_at is not None
        assert result.is_root_cause_confirmed is False
        mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_incident_status_detected_to_false_positive():
    """Verify DETECTED -> FALSE_POSITIVE transition succeeds."""
    inc = _make_dummy_incident_orm()
    inc.status = "DETECTED"

    mock_session = AsyncMock()
    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=inc),
    ):
        req = IncidentStatusUpdateRequest(status=IncidentStatusEnum.FALSE_POSITIVE)
        result = await update_incident_status(mock_session, inc.id, req)

        assert result.status == "FALSE_POSITIVE"
        assert result.is_root_cause_confirmed is False
        mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_incident_status_mitigated_to_investigating_clears_ended_at():
    """Verify MITIGATED -> INVESTIGATING clears ended_at because incident is active again."""
    inc = _make_dummy_incident_orm()
    inc.status = "MITIGATED"
    inc.ended_at = datetime(2026, 10, 10, 15, 30, 0, tzinfo=timezone.utc)

    mock_session = AsyncMock()
    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=inc),
    ):
        req = IncidentStatusUpdateRequest(status=IncidentStatusEnum.INVESTIGATING)
        result = await update_incident_status(mock_session, inc.id, req)

        assert result.status == "INVESTIGATING"
        assert result.ended_at is None
        assert result.is_root_cause_confirmed is False
        mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_incident_status_idempotent_no_corruption():
    """Verify idempotent status update does not overwrite existing ended_at or re-commit."""
    inc = _make_dummy_incident_orm()
    inc.status = "RESOLVED"
    preset_ended_at = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    inc.ended_at = preset_ended_at
    preset_updated_at = inc.updated_at

    mock_session = AsyncMock()
    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=inc),
    ):
        req = IncidentStatusUpdateRequest(status=IncidentStatusEnum.RESOLVED)
        result = await update_incident_status(mock_session, inc.id, req)

        assert result.status == "RESOLVED"
        assert result.ended_at == preset_ended_at
        assert result.updated_at == preset_updated_at
        mock_session.commit.assert_not_called()


def test_patch_incident_status_endpoint_success():
    """Verify PATCH /api/v1/incidents/{id}/status returns updated IncidentDetailResponse."""
    inc = _make_dummy_incident_orm()
    inc.status = "INVESTIGATING"

    with patch(
        "app.api.v1.endpoints.incidents.update_incident_status",
        new=AsyncMock(return_value=inc),
    ):
        resp = client.patch(
            f"/api/v1/incidents/{inc.id}/status",
            json={"status": "INVESTIGATING"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(inc.id)
        assert data["status"] == "INVESTIGATING"
        assert data["is_root_cause_confirmed"] is False


def test_patch_incident_status_unsupported_value_rejected_422():
    """Verify PATCH /api/v1/incidents/{id}/status rejects unsupported status with HTTP 422."""
    valid_uuid = str(uuid.uuid4())
    resp = client.patch(
        f"/api/v1/incidents/{valid_uuid}/status",
        json={"status": "INVALID_STATUS"},
    )
    assert resp.status_code == 422


def test_patch_incident_status_missing_field_rejected_422():
    """Verify PATCH /api/v1/incidents/{id}/status rejects empty payload with HTTP 422."""
    valid_uuid = str(uuid.uuid4())
    resp = client.patch(
        f"/api/v1/incidents/{valid_uuid}/status",
        json={},
    )
    assert resp.status_code == 422


def test_patch_incident_status_disallowed_transition_returns_409():
    """Verify PATCH /api/v1/incidents/{id}/status returns 409 for disallowed lifecycle transition."""
    valid_uuid = str(uuid.uuid4())
    with patch(
        "app.api.v1.endpoints.incidents.update_incident_status",
        side_effect=HTTPException(
            status_code=409,
            detail="Invalid lifecycle transition from 'RESOLVED' to 'INVESTIGATING'.",
        ),
    ):
        resp = client.patch(
            f"/api/v1/incidents/{valid_uuid}/status",
            json={"status": "INVESTIGATING"},
        )
        assert resp.status_code == 409
        assert "Invalid lifecycle transition" in resp.json()["detail"]


def test_patch_incident_status_not_found_returns_404():
    """Verify PATCH /api/v1/incidents/{id}/status returns 404 for valid UUID not found."""
    valid_uuid = str(uuid.uuid4())
    with patch(
        "app.api.v1.endpoints.incidents.update_incident_status",
        side_effect=HTTPException(
            status_code=404,
            detail=f"Incident '{valid_uuid}' not found.",
        ),
    ):
        resp = client.patch(
            f"/api/v1/incidents/{valid_uuid}/status",
            json={"status": "INVESTIGATING"},
        )
        assert resp.status_code == 404
        assert f"Incident '{valid_uuid}' not found." in resp.json()["detail"]


def test_patch_incident_status_malformed_uuid_returns_404():
    """Verify PATCH /api/v1/incidents/{id}/status returns 404 for malformed UUID string."""
    resp = client.patch(
        "/api/v1/incidents/not-a-valid-uuid/status",
        json={"status": "INVESTIGATING"},
    )
    assert resp.status_code == 404
    assert "Incident 'not-a-valid-uuid' not found." in resp.json()["detail"]


# ============================================================================
# 5. Timeline Integrity Validation Tests (Phase 16, Step 2)
# ============================================================================


def _make_timeline_event(
    sequence_order: int,
    timestamp: datetime,
    service_name: str = "svc-a",
    severity: str = "ERROR",
    is_earliest_observed_failure: bool = False,
    propagation_lag_seconds: float = 0.0,
    sample_log_id: str = "log-1",
) -> CorrelatedTimelineEventResponse:
    """Helper to construct a valid CorrelatedTimelineEventResponse schema."""
    return CorrelatedTimelineEventResponse(
        sequence_order=sequence_order,
        event_timestamp=timestamp,
        service_name=service_name,
        event_type="FIRST_ERROR" if is_earliest_observed_failure else "CASCADING_FAILURE",
        severity=severity,
        summary=f"Event at {sequence_order}",
        is_earliest_observed_failure=is_earliest_observed_failure,
        propagation_lag_seconds=propagation_lag_seconds,
        cluster_id=f"cl-{service_name}",
        sample_log_id=sample_log_id,
        attributes={},
    )


def test_validate_timeline_events_valid_contiguous():
    """Verify valid contiguous timeline sequence passes validation cleanly."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 10, 14, 2, 0, tzinfo=timezone.utc)

    events = [
        _make_timeline_event(0, t0, "svc-a", "ERROR", is_earliest_observed_failure=True, propagation_lag_seconds=0.0),
        _make_timeline_event(1, t1, "svc-b", "ERROR", is_earliest_observed_failure=False, propagation_lag_seconds=60.0),
        _make_timeline_event(2, t2, "svc-c", "INFO", is_earliest_observed_failure=False, propagation_lag_seconds=120.0),
    ]

    # Must pass without raising
    validate_timeline_events(events, started_at=t0, ended_at=t2)


def test_validate_timeline_events_empty_is_valid():
    """Verify empty timeline events list is valid."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    validate_timeline_events([], started_at=t0, ended_at=t0)


def test_validate_timeline_events_duplicate_sequence_order_rejected():
    """Verify duplicate sequence orders are rejected with HTTP 400."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a"),
        _make_timeline_event(0, t0, "svc-b"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400
    assert "Invalid timeline sequence ordering" in exc_info.value.detail


def test_validate_timeline_events_non_contiguous_sequence_order_rejected():
    """Verify non-contiguous sequence numbers are rejected with HTTP 400."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a"),
        _make_timeline_event(2, t1, "svc-b"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400
    assert "Invalid timeline sequence ordering" in exc_info.value.detail


def test_validate_timeline_events_non_zero_based_sequence_order_rejected():
    """Verify non-zero-based sequence numbers are rejected with HTTP 400."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(1, t0, "svc-a"),
        _make_timeline_event(2, t1, "svc-b"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400


def test_validate_timeline_events_timestamp_inversion_rejected():
    """Verify non-monotonic timestamp ordering relative to sequence is rejected with HTTP 400."""
    t0 = datetime(2026, 10, 10, 14, 5, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 2, 0, tzinfo=timezone.utc)  # Inverted!
    events = [
        _make_timeline_event(0, t0, "svc-a"),
        _make_timeline_event(1, t1, "svc-b"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t1, ended_at=t0)
    assert exc_info.value.status_code == 400
    assert "non-decreasing" in exc_info.value.detail


def test_validate_timeline_events_timestamp_before_started_at_rejected():
    """Verify event timestamp preceding incident started_at is rejected with HTTP 400."""
    t_start = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t_event = datetime(2026, 10, 10, 13, 59, 0, tzinfo=timezone.utc)  # Before started_at
    events = [
        _make_timeline_event(0, t_event, "svc-a"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t_start)
    assert exc_info.value.status_code == 400
    assert "preceding incident started_at" in exc_info.value.detail


def test_validate_timeline_events_timestamp_after_ended_at_rejected():
    """Verify event timestamp exceeding incident ended_at is rejected with HTTP 400."""
    t_start = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 10, 10, 14, 10, 0, tzinfo=timezone.utc)
    t_event = datetime(2026, 10, 10, 14, 11, 0, tzinfo=timezone.utc)  # After ended_at
    events = [
        _make_timeline_event(0, t_event, "svc-a"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t_start, ended_at=t_end)
    assert exc_info.value.status_code == 400
    assert "exceeding incident ended_at" in exc_info.value.detail


def test_validate_timeline_events_open_ended_incident_allows_later_events():
    """Verify open-ended incidents (ended_at=None) permit events occurring after started_at."""
    t_start = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t_event = datetime(2026, 10, 10, 14, 30, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t_event, "svc-a"),
    ]

    # Must pass without raising when ended_at is None
    validate_timeline_events(events, started_at=t_start, ended_at=None)


def test_validate_timeline_events_multiple_earliest_failures_rejected():
    """Verify multiple events marked is_earliest_observed_failure are rejected with HTTP 400."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a", is_earliest_observed_failure=True),
        _make_timeline_event(1, t1, "svc-b", is_earliest_observed_failure=True),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400
    assert "Multiple timeline events flagged as is_earliest_observed_failure" in exc_info.value.detail


def test_validate_timeline_events_earliest_failure_non_zero_lag_rejected():
    """Verify earliest observed failure with non-zero propagation lag is rejected with HTTP 400."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a", is_earliest_observed_failure=True, propagation_lag_seconds=5.0),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400
    assert "propagation_lag_seconds" in exc_info.value.detail


@pytest.mark.parametrize("invalid_lag", [float("nan"), float("inf"), float("-inf"), -2.5])
def test_validate_timeline_events_non_finite_or_negative_lag_rejected(invalid_lag):
    """Verify non-finite (NaN, +inf, -inf) or negative propagation lag is rejected with HTTP 400, including non-earliest event."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a", severity="ERROR", is_earliest_observed_failure=True, propagation_lag_seconds=0.0),
        _make_timeline_event(1, t1, "svc-b", severity="ERROR", is_earliest_observed_failure=False, propagation_lag_seconds=invalid_lag),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400
    assert "invalid propagation_lag_seconds" in exc_info.value.detail
    assert "finite, non-negative number" in exc_info.value.detail


def test_validate_timeline_events_negative_propagation_lag_rejected():
    """Verify negative propagation lag seconds is rejected with HTTP 400."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a", is_earliest_observed_failure=False, propagation_lag_seconds=-2.5),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400
    assert "invalid propagation_lag_seconds" in exc_info.value.detail
    assert "finite, non-negative number" in exc_info.value.detail


def test_validate_timeline_events_earliest_failure_preceded_by_earlier_failure_rejected():
    """Verify an earliest observed failure preceded by an earlier error event is rejected with HTTP 400."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 2, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a", severity="ERROR", is_earliest_observed_failure=False),
        _make_timeline_event(1, t1, "svc-b", severity="ERROR", is_earliest_observed_failure=True),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400
    assert "earlier qualifying failure exists" in exc_info.value.detail


def test_validate_timeline_events_earliest_failure_equal_timestamp_preceded_by_failure_rejected():
    """Verify a designated earliest failure is rejected if preceded by another qualifying failure with identical timestamp."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a", severity="ERROR", is_earliest_observed_failure=False),
        _make_timeline_event(1, t0, "svc-b", severity="ERROR", is_earliest_observed_failure=True),
    ]

    with pytest.raises(HTTPException) as exc_info:
        validate_timeline_events(events, started_at=t0)
    assert exc_info.value.status_code == 400
    assert "earlier qualifying failure exists" in exc_info.value.detail


def test_validate_timeline_events_earliest_failure_equal_timestamp_first_event_accepted():
    """Verify the correctly designated first event is accepted as earliest observed failure when timestamps are equal."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a", severity="ERROR", is_earliest_observed_failure=True, propagation_lag_seconds=0.0),
        _make_timeline_event(1, t0, "svc-b", severity="ERROR", is_earliest_observed_failure=False, propagation_lag_seconds=0.0),
    ]

    # Must pass validation without raising
    validate_timeline_events(events, started_at=t0)


def test_validate_timeline_events_earliest_failure_equal_timestamp_preceded_by_info_accepted():
    """Verify earliest observed failure is accepted when preceded by non-failure (e.g. INFO) event with equal timestamp."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    events = [
        _make_timeline_event(0, t0, "svc-a", severity="INFO", is_earliest_observed_failure=False, propagation_lag_seconds=0.0),
        _make_timeline_event(1, t0, "svc-b", severity="ERROR", is_earliest_observed_failure=True, propagation_lag_seconds=0.0),
    ]

    # Must pass validation without raising
    validate_timeline_events(events, started_at=t0)



def test_persist_incident_with_invalid_timeline_returns_400():
    """Verify POST /api/v1/incidents returns HTTP 400 for payload with invalid timeline sequence."""
    payload = {
        "candidate_id": "cand-invalid-seq-1",
        "title": "Invalid Timeline Incident",
        "severity": "HIGH",
        "started_at": "2026-10-10T14:00:00Z",
        "timeline_events": [
            {
                "sequence_order": 0,
                "event_timestamp": "2026-10-10T14:00:00Z",
                "service_name": "payment-service",
                "event_type": "FIRST_ERROR",
                "severity": "ERROR",
                "summary": "First event",
                "is_earliest_observed_failure": True,
                "propagation_lag_seconds": 0.0,
                "cluster_id": "cl-1",
            },
            {
                "sequence_order": 2,  # Non-contiguous gap (expected 1)
                "event_timestamp": "2026-10-10T14:01:00Z",
                "service_name": "payment-service",
                "event_type": "CASCADING_FAILURE",
                "severity": "ERROR",
                "summary": "Second event",
                "is_earliest_observed_failure": False,
                "propagation_lag_seconds": 60.0,
                "cluster_id": "cl-1",
            },
        ],
    }

    resp = client.post("/api/v1/incidents", json=payload)
    assert resp.status_code == 400
    assert "Invalid timeline sequence ordering" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_persist_incident_evidence_linkage_preserved():
    """Verify evidence items remain linked to matching timeline event by sample_log_id."""
    from app.services.incident_persistence_service import persist_incident

    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    req = IncidentCreateRequest(
        candidate_id="cand-ev-linkage-1",
        title="Linkage Test Incident",
        severity="HIGH",
        started_at=t0,
        timeline_events=[
            _make_timeline_event(0, t0, "payment-service", sample_log_id="log-match-123"),
        ],
        evidence_items=[
            CorrelatedEvidenceItemResponse(
                evidence_type="CULPRIT_LOG",
                log_id="log-match-123",
                timestamp=t0,
                service_name="payment-service",
                log_level="ERROR",
                snippet="Timeout occurred",
                correlation_rule="FIRST_FAILURE",
                rationale="Culprit",
            )
        ],
    )

    mock_session = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_log_source = MagicMock()
    mock_log_source.id = uuid.uuid4()
    mock_log_source.service_name = "payment-service"

    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.incident_persistence_service.resolve_log_source_for_service",
        new=AsyncMock(return_value=mock_log_source),
    ):
        incident, created = await persist_incident(mock_session, req)
        assert created is True
        # Verify entities were added to session
        added_objs = [call.args[0] for call in mock_session.add.call_args_list]
        event_objs = [o for o in added_objs if isinstance(o, IncidentEvent)]
        evidence_objs = [o for o in added_objs if isinstance(o, Evidence)]

        assert len(event_objs) == 1
        assert len(evidence_objs) == 1
        assert evidence_objs[0].incident_event_id == event_objs[0].id


def test_map_incident_to_detail_response_deterministic_evidence_ordering():
    """Verify map_incident_to_detail_response deterministically sorts evidence items without mutating the ORM collection."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 10, 14, 2, 0, tzinfo=timezone.utc)

    inc = _make_dummy_incident_orm()
    inc.started_at = t0

    # Create evidence items intentionally inserted in arbitrary / non-chronological order
    ev_c = Evidence(
        id=uuid.uuid4(),
        incident_id=inc.id,
        evidence_type="CORRELATION_SPIKE",
        opensearch_doc_id="doc-z",
        log_timestamp=t2,
        service_name="frontend",
        log_level="ERROR",
        log_snippet="UI error",
        explanation="Third event",
        metadata_={},
    )
    ev_b = Evidence(
        id=uuid.uuid4(),
        incident_id=inc.id,
        evidence_type="CORRELATION_SPIKE",
        opensearch_doc_id="doc-b",
        log_timestamp=t1,
        service_name="auth-service",
        log_level="ERROR",
        log_snippet="Auth error",
        explanation="Second event",
        metadata_={},
    )
    ev_a = Evidence(
        id=uuid.uuid4(),
        incident_id=inc.id,
        evidence_type="CULPRIT_LOG",
        opensearch_doc_id="doc-a",
        log_timestamp=t0,
        service_name="payment-service",
        log_level="ERROR",
        log_snippet="DB error",
        explanation="First event",
        metadata_={},
    )
    ev_null_ts = Evidence(
        id=uuid.uuid4(),
        incident_id=inc.id,
        evidence_type="CORRELATION_SPIKE",
        opensearch_doc_id="doc-0",
        log_timestamp=None,  # Falls back to started_at (t0)
        service_name=None,   # Falls back to ""
        log_level="ERROR",
        log_snippet="Fallback error",
        explanation="Fallback event",
        metadata_={},
    )

    original_evidence_order = [ev_c, ev_b, ev_a, ev_null_ts]
    inc.evidence = list(original_evidence_order)

    detail = map_incident_to_detail_response(inc)

    # 1. ORM collection must NOT be mutated
    assert inc.evidence == original_evidence_order

    # 2. Response evidence must be sorted deterministically:
    # Key: (_ensure_utc(log_timestamp or started_at), service_name or "", opensearch_doc_id or str(id))
    # Comparison order:
    # 1st: ev_null_ts (t0, "", "doc-0")
    # 2nd: ev_a (t0, "payment-service", "doc-a")
    # 3rd: ev_b (t1, "auth-service", "doc-b")
    # 4th: ev_c (t2, "frontend", "doc-z")
    assert len(detail.evidence_items) == 4
    assert detail.evidence_items[0].log_id == "doc-0"
    assert detail.evidence_items[1].log_id == "doc-a"
    assert detail.evidence_items[2].log_id == "doc-b"
    assert detail.evidence_items[3].log_id == "doc-z"


@pytest.mark.asyncio
async def test_candidate_to_persistence_to_retrieval_service_contract_round_trip():
    """Service-level contract and pipeline integration test:
    Exercises candidate generation via correlate_clusters() -> request mapping ->
    timeline validation -> ORM entity staging and evidence linkage -> detail response mapping.

    Note on testing infrastructure:
    RootLens maintains no isolated ephemeral PostgreSQL test database fixture.
    Therefore, database I/O is verified at the service/contract boundary using AsyncSession staging.
    Actual physical PostgreSQL dialect compilation and database constraints remain unverified against live PostgreSQL.
    """
    from app.services.clustering_service import LogCluster, LogEventItem
    from app.services.correlation_service import correlate_clusters
    from app.services.incident_persistence_service import persist_incident

    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 15, tzinfo=timezone.utc)

    # 1. Generate realistic multi-service candidate via correlate_clusters()
    ev_pay = LogEventItem(
        log_id="log-pay-1",
        timestamp=t0,
        service_name="payment-service",
        environment="production",
        log_level="CRITICAL",
        message="Connection pool exhausted: payment DB timed out",
        host="pay-worker-1",
        trace_id="trace-cross-101",
        span_id="span-pay-1",
        raw_log='{"service": "payment-service", "error": "pool_exhausted"}',
    )
    cl_pay = LogCluster(
        cluster_id="cl-pay",
        service_name="payment-service",
        pattern="Connection pool exhausted",
        pattern_hash="hash_pay",
        severity="CRITICAL",
        window_start=t0,
        window_end=t0,
        count=1,
        log_ids=["log-pay-1"],
        sample_messages=["Connection pool exhausted: payment DB timed out"],
        first_seen=t0,
        last_seen=t0,
        trace_ids=["trace-cross-101"],
        span_ids=["span-pay-1"],
        events=[ev_pay],
    )

    ev_gw = LogEventItem(
        log_id="log-gw-1",
        timestamp=t1,
        service_name="api-gateway",
        environment="production",
        log_level="ERROR",
        message="HTTP 504 Gateway Timeout forwarding /checkout",
        host="gw-edge-1",
        trace_id="trace-cross-101",
        span_id="span-gw-1",
        raw_log='{"service": "api-gateway", "error": "504_gateway_timeout"}',
    )
    cl_gw = LogCluster(
        cluster_id="cl-gw",
        service_name="api-gateway",
        pattern="HTTP 504 Gateway Timeout",
        pattern_hash="hash_gw",
        severity="ERROR",
        window_start=t1,
        window_end=t1,
        count=1,
        log_ids=["log-gw-1"],
        sample_messages=["HTTP 504 Gateway Timeout forwarding /checkout"],
        first_seen=t1,
        last_seen=t1,
        trace_ids=["trace-cross-101"],
        span_ids=["span-gw-1"],
        events=[ev_gw],
    )

    candidates = correlate_clusters([cl_pay, cl_gw], cascade_window_seconds=120)
    assert len(candidates) == 1
    cand = candidates[0]

    # 2. Construct IncidentCreateRequest directly from candidate fields
    req = IncidentCreateRequest(
        candidate_id=cand.candidate_id,
        title=cand.title,
        severity=cand.severity,
        started_at=cand.started_at,
        ended_at=cand.ended_at,
        correlation_rules_used=cand.correlation_rules_used,
        uncertainty_reason=cand.uncertainty_reason,
        affected_services=[
            CorrelatedAffectedServiceResponse(
                service_name=s.service_name,
                impact_level=s.impact_level,
                first_impacted_at=s.first_impacted_at,
                error_count=s.error_count,
            )
            for s in cand.affected_services
        ],
        timeline_events=[
            CorrelatedTimelineEventResponse(
                sequence_order=e.sequence_order,
                event_timestamp=e.event_timestamp,
                service_name=e.service_name,
                event_type=e.event_type,
                severity=e.severity,
                summary=e.summary,
                is_earliest_observed_failure=e.is_earliest_observed_failure,
                propagation_lag_seconds=e.propagation_lag_seconds,
                cluster_id=e.cluster_id,
                trace_id=e.trace_id,
                span_id=e.span_id,
                sample_log_id=e.sample_log_id,
                attributes=e.attributes,
            )
            for e in cand.timeline_events
        ],
        evidence_items=[
            CorrelatedEvidenceItemResponse(
                evidence_type=item.evidence_type,
                log_id=item.log_id,
                timestamp=item.timestamp,
                service_name=item.service_name,
                log_level=item.log_level,
                snippet=item.snippet,
                correlation_rule=item.correlation_rule,
                rationale=item.rationale,
                content_hash=item.content_hash,
                trace_id=item.trace_id,
            )
            for item in cand.evidence_items
        ],
        cluster_ids=cand.cluster_ids,
    )

    # 3. Simulate persistence staging
    staged_entities = []
    mock_session = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.rollback = AsyncMock()

    def mock_add(entity):
        staged_entities.append(entity)

    mock_session.add.side_effect = mock_add

    # Return LogSource for services
    log_sources = {
        "payment-service": LogSource(id=uuid.uuid4(), service_name="payment-service", is_active=True),
        "api-gateway": LogSource(id=uuid.uuid4(), service_name="api-gateway", is_active=True),
    }

    async def mock_resolve_log_source(session, service_name):
        return log_sources[service_name]

    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.incident_persistence_service.resolve_log_source_for_service",
        side_effect=mock_resolve_log_source,
    ):
        created_incident, is_new = await persist_incident(mock_session, req)
        assert is_new is True

    # 4. Verify staged entities
    staged_inc = next(e for e in staged_entities if isinstance(e, Incident))
    staged_events = [e for e in staged_entities if isinstance(e, IncidentEvent)]
    staged_evidence = [e for e in staged_entities if isinstance(e, Evidence)]
    staged_aff = [e for e in staged_entities if isinstance(e, IncidentAffectedService)]

    assert len(staged_events) == 2
    assert len(staged_evidence) >= 2
    assert len(staged_aff) == 2

    # Wire relationships as SQLAlchemy would on load
    staged_inc.events = staged_events
    staged_inc.evidence = staged_evidence
    staged_inc.affected_services = staged_aff
    staged_inc.primary_log_source = log_sources["payment-service"]

    # 5. Serialize via map_incident_to_detail_response()
    detail = map_incident_to_detail_response(staged_inc)

    # 6. Comprehensive verifications
    # Timeline sequence ordering and timestamps
    assert len(detail.timeline_events) == 2
    assert detail.timeline_events[0].sequence_order == 0
    assert detail.timeline_events[0].service_name == "payment-service"
    assert detail.timeline_events[0].event_timestamp == t0
    assert detail.timeline_events[0].is_earliest_observed_failure is True
    assert detail.timeline_events[0].propagation_lag_seconds == 0.0

    assert detail.timeline_events[1].sequence_order == 1
    assert detail.timeline_events[1].service_name == "api-gateway"
    assert detail.timeline_events[1].event_timestamp == t1
    assert detail.timeline_events[1].is_earliest_observed_failure is False
    assert detail.timeline_events[1].propagation_lag_seconds == 15.0

    # Evidence ordering is deterministic: timestamps non-decreasing
    for i in range(len(detail.evidence_items) - 1):
        assert detail.evidence_items[i].timestamp <= detail.evidence_items[i + 1].timestamp

    # Evidence linkage: each evidence item's incident_event_id links to expected event
    ev_by_seq = {e.sequence_order: e for e in staged_events}
    for ev_record in staged_evidence:
        if ev_record.opensearch_doc_id == "log-pay-1":
            assert ev_record.incident_event_id == ev_by_seq[0].id
        elif ev_record.opensearch_doc_id == "log-gw-1":
            assert ev_record.incident_event_id == ev_by_seq[1].id

    # Invariants
    assert detail.status == "DETECTED"
    assert detail.analysis_status == "TRACE_LINKED"
    assert detail.probable_cause is None
    assert detail.confidence_score == 0.0
    assert detail.is_root_cause_confirmed is False
    assert detail.primary_service == "payment-service"
    assert "Low-to-Moderate" in detail.uncertainty_reason

    # Structured assessment verification
    assert detail.assessment is not None
    assert detail.assessment.classification == "TRACE_LINKED"
    assert detail.assessment.is_root_cause_confirmed is False
    assert detail.assessment.has_shared_trace is True
    assert detail.assessment.distinct_service_count == 2
    assert len(detail.assessment.observed_facts) >= 1
    assert len(detail.assessment.inferred_hypotheses) >= 1
    assert detail.assessment.inferred_hypotheses[0].startswith("[Hypothesis]")
    assert "Low-to-Moderate" in detail.assessment.uncertainty_statement

    # RCA synthesis verification
    assert detail.rca_synthesis is not None
    assert detail.rca_synthesis.classification == "TRACE_LINKED"
    assert detail.rca_synthesis.is_root_cause_confirmed is False
    assert detail.rca_synthesis.primary_hypothesis is not None
    assert detail.rca_synthesis.primary_hypothesis.startswith("[Hypothesis]")
    assert "trace-cross-101" in detail.rca_synthesis.primary_hypothesis
    assert len(detail.rca_synthesis.findings) == 1
    assert detail.rca_synthesis.findings[0].is_root_cause_confirmed is False
    assert detail.assessment.rca_synthesis is not None
    assert detail.assessment.rca_synthesis.classification == "TRACE_LINKED"



@pytest.mark.asyncio
async def test_persist_incident_temporal_only_evidence_assessment():
    """Verify that an incident with temporal correlation but no shared trace stores TEMPORAL_ONLY analysis_status."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 20, tzinfo=timezone.utc)

    req = IncidentCreateRequest(
        candidate_id="cand-temp-persisted-1",
        title="Temporal cascade incident",
        severity="HIGH",
        started_at=t0,
        ended_at=t1,
        correlation_rules_used=["TEMPORAL_CASCADE"],
        uncertainty_reason=None,  # Triggers automatic uncertainty generation
        affected_services=[
            CorrelatedAffectedServiceResponse(
                service_name="auth-service",
                impact_level="PRIMARY_CULPRIT",
                first_impacted_at=t0,
                error_count=2,
            ),
            CorrelatedAffectedServiceResponse(
                service_name="user-service",
                impact_level="DOWNSTREAM_CASCADE",
                first_impacted_at=t1,
                error_count=1,
            ),
        ],
        timeline_events=[
            CorrelatedTimelineEventResponse(
                sequence_order=0,
                event_timestamp=t0,
                service_name="auth-service",
                event_type="FIRST_ERROR",
                severity="ERROR",
                summary="Auth failed",
                is_earliest_observed_failure=True,
                propagation_lag_seconds=0.0,
                cluster_id="cl-1",
                sample_log_id="log-a1",
            ),
            CorrelatedTimelineEventResponse(
                sequence_order=1,
                event_timestamp=t1,
                service_name="user-service",
                event_type="CASCADING_FAILURE",
                severity="ERROR",
                summary="User timeout",
                is_earliest_observed_failure=False,
                propagation_lag_seconds=20.0,
                cluster_id="cl-2",
                sample_log_id="log-u1",
            ),
        ],
        evidence_items=[
            CorrelatedEvidenceItemResponse(
                evidence_type="CULPRIT_LOG",
                log_id="log-a1",
                timestamp=t0,
                service_name="auth-service",
                log_level="ERROR",
                snippet="Auth error snippet",
                correlation_rule="FIRST_FAILURE",
                rationale="Initial failure",
            ),
            CorrelatedEvidenceItemResponse(
                evidence_type="CORRELATION_SPIKE",
                log_id="log-u1",
                timestamp=t1,
                service_name="user-service",
                log_level="ERROR",
                snippet="User error snippet",
                correlation_rule="TEMPORAL_CASCADE",
                rationale="Cascade failure",
            ),
        ],
    )

    staged_entities = []
    mock_session = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.add.side_effect = lambda e: staged_entities.append(e)

    log_sources = {
        "auth-service": LogSource(id=uuid.uuid4(), service_name="auth-service", is_active=True),
        "user-service": LogSource(id=uuid.uuid4(), service_name="user-service", is_active=True),
    }

    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.incident_persistence_service.resolve_log_source_for_service",
        side_effect=lambda session, svc: log_sources[svc],
    ):
        created_inc, is_new = await persist_incident(mock_session, req)
        assert is_new is True

    staged_inc = next(e for e in staged_entities if isinstance(e, Incident))
    assert staged_inc.analysis_status == "TEMPORAL_ONLY"
    assert staged_inc.is_root_cause_confirmed is False
    assert staged_inc.probable_cause is None
    assert staged_inc.confidence_score == 0.0
    assert "Diagnostic uncertainty: Moderate" in staged_inc.uncertainty_reason
    assert "Earliest observed failure identified chronologically" in staged_inc.uncertainty_reason

    # Verify detail retrieval mapping exposes persisted assessment status and uncertainty
    staged_inc.affected_services = [e for e in staged_entities if isinstance(e, IncidentAffectedService)]
    staged_inc.events = [e for e in staged_entities if isinstance(e, IncidentEvent)]
    staged_inc.evidence = [e for e in staged_entities if isinstance(e, Evidence)]
    staged_inc.primary_log_source = log_sources["auth-service"]

    detail = map_incident_to_detail_response(staged_inc)
    assert detail.analysis_status == "TEMPORAL_ONLY"
    assert detail.probable_cause is None
    assert detail.confidence_score == 0.0
    assert detail.is_root_cause_confirmed is False
    assert "Diagnostic uncertainty: Moderate" in detail.uncertainty_reason
    assert "Earliest observed failure identified chronologically" in detail.uncertainty_reason

    # Structured assessment verification
    assert detail.assessment is not None
    assert detail.assessment.classification == "TEMPORAL_ONLY"
    assert detail.assessment.is_root_cause_confirmed is False
    assert detail.assessment.has_shared_trace is False
    assert "Moderate" in detail.assessment.uncertainty_statement

    # RCA synthesis verification
    assert detail.rca_synthesis is not None
    assert detail.rca_synthesis.classification == "TEMPORAL_ONLY"
    assert detail.rca_synthesis.is_root_cause_confirmed is False
    assert detail.rca_synthesis.primary_hypothesis is not None
    assert "causal propagation cannot be confirmed" in detail.rca_synthesis.primary_hypothesis


@pytest.mark.asyncio
async def test_persist_incident_insufficient_evidence_assessment():
    """Verify that an incident with sparse evidence stores INSUFFICIENT analysis_status."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)

    req = IncidentCreateRequest(
        candidate_id="cand-insuf-persisted-1",
        title="Sparse incident",
        severity="MEDIUM",
        started_at=t0,
        ended_at=t0,
        correlation_rules_used=["LOCAL_SERVICE"],
        uncertainty_reason=None,
        affected_services=[
            CorrelatedAffectedServiceResponse(
                service_name="auth-service",
                impact_level="PRIMARY_CULPRIT",
                first_impacted_at=t0,
                error_count=1,
            ),
        ],
        timeline_events=[],  # Sparse: 0 timeline events, 1 evidence record
        evidence_items=[
            CorrelatedEvidenceItemResponse(
                evidence_type="CULPRIT_LOG",
                log_id="log-single",
                timestamp=t0,
                service_name="auth-service",
                log_level="ERROR",
                snippet="Single isolated error",
                correlation_rule="FIRST_FAILURE",
                rationale="Only error",
            ),
        ],
    )

    staged_entities = []
    mock_session = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.add.side_effect = lambda e: staged_entities.append(e)

    log_sources = {
        "auth-service": LogSource(id=uuid.uuid4(), service_name="auth-service", is_active=True),
    }

    with patch(
        "app.services.incident_persistence_service.get_incident_by_id",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.incident_persistence_service.resolve_log_source_for_service",
        side_effect=lambda session, svc: log_sources[svc],
    ):
        created_inc, is_new = await persist_incident(mock_session, req)
        assert is_new is True

    staged_inc = next(e for e in staged_entities if isinstance(e, Incident))
    assert staged_inc.analysis_status == "INSUFFICIENT"
    assert staged_inc.is_root_cause_confirmed is False
    assert staged_inc.probable_cause is None
    assert staged_inc.confidence_score == 0.0
    assert "Diagnostic uncertainty: High" in staged_inc.uncertainty_reason

    # Verify detail retrieval mapping exposes persisted assessment status and uncertainty
    staged_inc.affected_services = [e for e in staged_entities if isinstance(e, IncidentAffectedService)]
    staged_inc.events = [e for e in staged_entities if isinstance(e, IncidentEvent)]
    staged_inc.evidence = [e for e in staged_entities if isinstance(e, Evidence)]
    staged_inc.primary_log_source = log_sources["auth-service"]

    detail = map_incident_to_detail_response(staged_inc)
    assert detail.analysis_status == "INSUFFICIENT"
    assert detail.probable_cause is None
    assert detail.confidence_score == 0.0
    assert detail.is_root_cause_confirmed is False
    assert "Diagnostic uncertainty: High" in detail.uncertainty_reason

    # Structured assessment verification
    assert detail.assessment is not None
    assert detail.assessment.classification == "INSUFFICIENT"
    assert detail.assessment.is_root_cause_confirmed is False
    assert detail.assessment.has_shared_trace is False
    assert detail.assessment.distinct_evidence_count == 1
    assert "High" in detail.assessment.uncertainty_statement

    # RCA synthesis verification
    assert detail.rca_synthesis is not None
    assert detail.rca_synthesis.classification == "INSUFFICIENT"
    assert detail.rca_synthesis.is_root_cause_confirmed is False
    assert detail.rca_synthesis.primary_hypothesis is None


def test_incident_detail_response_structured_assessment_contract():
    """Verify that IncidentDetailResponse serializes complete structured EvidenceAssessmentResponse."""
    inc = _make_dummy_incident_orm(candidate_id="cand-contract-test")
    detail = map_incident_to_detail_response(inc)

    # 1. Assert backward compatible fields
    assert detail.id is not None
    assert detail.title is not None
    assert detail.status == "DETECTED"
    assert detail.probable_cause is None
    assert detail.confidence_score == 0.0
    assert detail.is_root_cause_confirmed is False

    # 2. Assert structured assessment schema contract
    assert detail.assessment is not None
    ass = detail.assessment
    assert ass.classification in ("TRACE_LINKED", "TEMPORAL_ONLY", "INSUFFICIENT")
    assert ass.is_root_cause_confirmed is False
    assert isinstance(ass.observed_facts, list)
    assert isinstance(ass.inferred_hypotheses, list)
    assert isinstance(ass.supporting_signals, list)
    assert isinstance(ass.missing_signals, list)
    assert isinstance(ass.trace_ids_found, list)
    assert isinstance(ass.span_ids_found, list)
    assert isinstance(ass.uncertainty_statement, str)

    # Assert RCA synthesis schema contract
    assert detail.rca_synthesis is not None
    assert detail.rca_synthesis.is_root_cause_confirmed is False
    assert detail.assessment.rca_synthesis is not None

    # 3. Test empty evidence edge case
    empty_inc = _make_dummy_incident_orm(candidate_id="cand-empty-test")
    empty_inc.evidence = []
    empty_inc.events = []
    empty_detail = map_incident_to_detail_response(empty_inc)
    assert empty_detail.assessment is not None
    assert empty_detail.assessment.classification == "INSUFFICIENT"
    assert empty_detail.assessment.is_root_cause_confirmed is False
    assert empty_detail.assessment.distinct_evidence_count == 0
    assert empty_detail.rca_synthesis is not None
    assert empty_detail.rca_synthesis.classification == "INSUFFICIENT"
    assert empty_detail.rca_synthesis.is_root_cause_confirmed is False
    assert empty_detail.rca_synthesis.primary_hypothesis is None





