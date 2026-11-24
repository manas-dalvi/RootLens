from datetime import datetime, timezone
import hashlib
import logging
import math
from typing import Dict, List, Optional, Set, Tuple
import uuid

from fastapi import HTTPException, status
from sqlalchemy import desc, distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.evidence import Evidence
from app.models.incident import Incident
from app.models.incident_affected_service import IncidentAffectedService
from app.models.incident_event import IncidentEvent
from app.models.log_source import LogSource
from app.schemas.incident import (
    CorrelatedAffectedServiceResponse,
    CorrelatedEvidenceItemResponse,
    CorrelatedTimelineEventResponse,
    EvidenceAssessmentResponse,
    RCASynthesisResponse,
    IncidentCreateRequest,
    IncidentDetailResponse,
    IncidentListResponse,
    IncidentStatusUpdateRequest,
    IncidentSummaryItemResponse,
)
from app.services.clustering_service import LogEventItem
from app.services.correlation_service import (
    deduplicate_evidence_items,
    evaluate_incident_grouping,
    IncidentGroupingEvaluation,
)
from app.services.evidence_analysis_service import evaluate_evidence_sufficiency
from app.services.rca_synthesis_service import synthesize_rca_findings
from app.services.upload_service import resolve_or_create_log_source

logger = logging.getLogger("rootlens.incidents.persistence")


def compute_deterministic_incident_uuid(candidate_id: str) -> uuid.UUID:
    """Generate a stable, deterministic UUID for an incident from its candidate identifier."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"rootlens://incident/{candidate_id}")


async def resolve_log_source_for_service(
    session: AsyncSession,
    service_name: str,
) -> LogSource:
    """Find an active LogSource for a service or register a standard source."""
    stmt = (
        select(LogSource)
        .where(
            LogSource.service_name == service_name,
            LogSource.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    source = result.scalar_one_or_none()
    if source:
        return source

    # Fallback to resolve_or_create_log_source from upload_service
    return await resolve_or_create_log_source(
        session=session,
        log_source_id=None,
        service_name=service_name,
        environment="production",
        source_name=f"{service_name}-service",
        format_type="json",
        fallback_filename=f"{service_name}-auto-source",
    )


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime instance has UTC timezone information."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _require_utc(dt: datetime) -> datetime:
    """Ensure a non-null datetime instance has UTC timezone information."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def map_incident_to_detail_response(incident: Incident) -> IncidentDetailResponse:
    """Map SQLAlchemy Incident model and loaded relations to IncidentDetailResponse."""
    primary_svc = (
        incident.primary_log_source.service_name
        if incident.primary_log_source
        else None
    )

    # Map affected services
    affected_services = [
        CorrelatedAffectedServiceResponse(
            service_name=s.service_name,
            impact_level=s.impact_level,
            first_impacted_at=s.first_impacted_at,
            error_count=s.error_count,
        )
        for s in (incident.affected_services or [])
    ]

    # Map timeline events (sorted by sequence_order)
    events_sorted = sorted(incident.events or [], key=lambda e: e.sequence_order)
    timeline_events = [
        CorrelatedTimelineEventResponse(
            sequence_order=e.sequence_order,
            event_timestamp=e.event_timestamp,
            service_name=e.service_name,
            event_type=e.event_type,
            severity=e.severity,
            summary=e.summary,
            is_earliest_observed_failure=bool(
                (e.attributes or {}).get("is_earliest_observed_failure", False)
            ),
            propagation_lag_seconds=float(
                (e.attributes or {}).get("propagation_lag_seconds", 0.0)
            ),
            cluster_id=str((e.attributes or {}).get("cluster_id", "")),
            trace_id=(e.attributes or {}).get("trace_id"),
            span_id=(e.attributes or {}).get("span_id"),
            sample_log_id=(e.attributes or {}).get("sample_log_id"),
            relationship_type=(e.attributes or {}).get("relationship_type"),
            correlation_confidence=(e.attributes or {}).get("correlation_confidence"),
            attributes=e.attributes or {},
        )
        for e in events_sorted
    ]

    # Map evidence items (deterministically sorted by timestamp, service, and doc ID)
    evidence_sorted = sorted(
        incident.evidence or [],
        key=lambda ev: (
            _ensure_utc(ev.log_timestamp or incident.started_at),
            ev.service_name or "",
            ev.opensearch_doc_id or str(ev.id),
        ),
    )
    evidence_items = [
        CorrelatedEvidenceItemResponse(
            evidence_type=ev.evidence_type,
            log_id=ev.opensearch_doc_id or str(ev.id),
            timestamp=ev.log_timestamp or incident.started_at,
            service_name=ev.service_name or "unknown",
            log_level=ev.log_level or "ERROR",
            snippet=ev.log_snippet or "",
            correlation_rule=str((ev.metadata_ or {}).get("correlation_rule", "EVIDENCE")),
            rationale=ev.explanation,
            content_hash=ev.content_hash,
            trace_id=(ev.metadata_ or {}).get("trace_id"),
            relationship_type=(ev.metadata_ or {}).get("relationship_type"),
            correlation_confidence=(ev.metadata_ or {}).get("correlation_confidence"),
        )
        for ev in evidence_sorted
    ]

    # Deterministic in-memory evidence sufficiency assessment & RCA synthesis from persisted items
    assessment_obj = evaluate_evidence_sufficiency(
        evidence_items=evidence_items,
        timeline_events=timeline_events,
    )
    rca_obj = synthesize_rca_findings(
        assessment=assessment_obj,
        evidence_items=evidence_items,
        timeline_events=timeline_events,
    )
    rca_resp = (
        RCASynthesisResponse.from_result(rca_obj)
        if rca_obj is not None
        else None
    )
    assessment_resp = (
        EvidenceAssessmentResponse.from_assessment(assessment_obj, rca_result=rca_obj)
        if assessment_obj is not None
        else None
    )

    return IncidentDetailResponse(
        id=str(incident.id),
        title=incident.title,
        severity=incident.severity,
        status=incident.status,
        analysis_status=incident.analysis_status,
        started_at=incident.started_at,
        ended_at=incident.ended_at,
        detected_at=incident.detected_at,
        probable_cause=incident.probable_cause,
        confidence_score=incident.confidence_score,
        is_root_cause_confirmed=incident.is_root_cause_confirmed,
        uncertainty_reason=incident.uncertainty_reason,
        primary_service=primary_svc,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        affected_services=affected_services,
        timeline_events=timeline_events,
        evidence_items=evidence_items,
        assessment=assessment_resp,
        rca_synthesis=rca_resp,
    )


def map_incident_to_summary_response(incident: Incident) -> IncidentSummaryItemResponse:
    """Map SQLAlchemy Incident model to IncidentSummaryItemResponse."""
    primary_svc = (
        incident.primary_log_source.service_name
        if incident.primary_log_source
        else None
    )

    return IncidentSummaryItemResponse(
        id=str(incident.id),
        title=incident.title,
        severity=incident.severity,
        status=incident.status,
        analysis_status=incident.analysis_status,
        started_at=incident.started_at,
        ended_at=incident.ended_at,
        detected_at=incident.detected_at,
        confidence_score=incident.confidence_score,
        is_root_cause_confirmed=incident.is_root_cause_confirmed,
        uncertainty_reason=incident.uncertainty_reason,
        primary_service=primary_svc,
        affected_service_count=len(incident.affected_services or []),
        event_count=len(incident.events or []),
        evidence_count=len(incident.evidence or []),
        created_at=incident.created_at,
        updated_at=incident.updated_at,
    )


async def get_incident_by_id(
    session: AsyncSession,
    incident_id: uuid.UUID,
) -> Optional[Incident]:
    """Retrieve full incident record and loaded relationships by primary UUID."""
    stmt = (
        select(Incident)
        .options(
            selectinload(Incident.affected_services),
            selectinload(Incident.events),
            selectinload(Incident.evidence),
            selectinload(Incident.primary_log_source),
        )
        .where(Incident.id == incident_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def validate_timeline_events(
    events: List[CorrelatedTimelineEventResponse],
    started_at: datetime,
    ended_at: Optional[datetime] = None,
) -> None:
    """Validate timeline event sequence ordering, temporal bounds, and earliest-failure invariants.

    Rules:
    - Sequence numbers must be unique, contiguous, and zero-based (0..N-1).
    - Event timestamps must be non-decreasing with respect to sequence_order.
    - Event timestamps must fall within incident boundaries: >= started_at (and <= ended_at if specified).
    - Earliest observed failure flag must be unique (at most one event with is_earliest_observed_failure=True).
    - If an earliest observed failure is present, its propagation_lag_seconds must be 0.0, and no preceding event
      in the sequence can be a failure (FATAL/ERROR/CRITICAL) with an earlier timestamp.
    - Propagation lag seconds cannot be negative.

    Raises:
        HTTPException(status_code=400): If any validation constraint is violated.
    """
    if not events:
        return

    # 1. Sequence numbers must be unique, contiguous, and zero-based (0..N-1)
    expected_sequences = list(range(len(events)))
    actual_sequences = [ev.sequence_order for ev in events]
    if actual_sequences != expected_sequences:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid timeline sequence ordering. Sequence numbers must be unique, contiguous, "
                f"and zero-based (expected {expected_sequences}, got {actual_sequences})."
            ),
        )

    # 2. Event timestamps must be non-decreasing with respect to sequence_order
    for i in range(len(events) - 1):
        prev_ev = events[i]
        next_ev = events[i + 1]
        prev_ts = _require_utc(prev_ev.event_timestamp)
        next_ts = _require_utc(next_ev.event_timestamp)
        if prev_ts > next_ts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Timeline event timestamps must be non-decreasing with respect to sequence_order. "
                    f"Event at sequence_order {prev_ev.sequence_order} ({prev_ev.event_timestamp.isoformat()}) "
                    f"is later than event at sequence_order {next_ev.sequence_order} ({next_ev.event_timestamp.isoformat()})."
                ),
            )

    # 3. Temporal boundaries: event_timestamp >= started_at and (<= ended_at if ended_at is present)
    norm_started_at = _ensure_utc(started_at)
    norm_ended_at = _ensure_utc(ended_at) if ended_at is not None else None

    for ev in events:
        ev_ts = _require_utc(ev.event_timestamp)
        if norm_started_at is not None and ev_ts < norm_started_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Timeline event at sequence_order {ev.sequence_order} has timestamp ({ev.event_timestamp.isoformat()}) "
                    f"preceding incident started_at ({started_at.isoformat()})."
                ),
            )
        if norm_ended_at is not None and ev_ts > norm_ended_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Timeline event at sequence_order {ev.sequence_order} has timestamp ({ev.event_timestamp.isoformat()}) "
                    f"exceeding incident ended_at ({ended_at.isoformat() if ended_at is not None else ''})."
                ),
            )
        if not math.isfinite(ev.propagation_lag_seconds) or ev.propagation_lag_seconds < 0.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Timeline event at sequence_order {ev.sequence_order} has invalid propagation_lag_seconds "
                    f"({ev.propagation_lag_seconds}). Propagation lag must be a finite, non-negative number."
                ),
            )

    # 4. Earliest observed failure semantics
    earliest_failures = [ev for ev in events if ev.is_earliest_observed_failure]
    if len(earliest_failures) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Multiple timeline events flagged as is_earliest_observed_failure at sequence orders "
                f"{[ev.sequence_order for ev in earliest_failures]}. At most one earliest failure is permitted."
            ),
        )

    if len(earliest_failures) == 1:
        ef = earliest_failures[0]
        if ef.propagation_lag_seconds != 0.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Timeline event at sequence_order {ef.sequence_order} is marked as earliest observed failure "
                    f"but has non-zero propagation_lag_seconds ({ef.propagation_lag_seconds}). Expected 0.0."
                ),
            )
        # Verify no preceding event in the sequence is an error/fatal failure with an earlier or equal timestamp
        for ev in events:
            if ev.sequence_order < ef.sequence_order:
                if (ev.severity or "").upper() in ("FATAL", "ERROR", "CRITICAL") and _require_utc(ev.event_timestamp) <= _require_utc(ef.event_timestamp):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Timeline event at sequence_order {ef.sequence_order} is marked as earliest observed failure, "
                            f"but an earlier qualifying failure exists at sequence_order {ev.sequence_order} "
                            f"with timestamp ({ev.event_timestamp.isoformat()})."
                        ),
                    )


async def persist_incident(
    session: AsyncSession,
    req: IncidentCreateRequest,
) -> Tuple[Incident, bool]:
    """Idempotently persist an incident candidate and its child relations into PostgreSQL.

    Returns:
        (incident, created): Tuple where created is True if newly inserted, False if already existed.
    """
    # 1. Centralized timeline validation before any database queries or entity staging
    validate_timeline_events(
        events=req.timeline_events,
        started_at=req.started_at,
        ended_at=req.ended_at,
    )

    incident_uuid = compute_deterministic_incident_uuid(req.candidate_id)

    # 2. Check for existing incident by deterministic UUID (idempotency check)
    existing = await get_incident_by_id(session, incident_uuid)
    if existing:
        logger.info("Incident '%s' already exists in database; returning existing record.", incident_uuid)
        return existing, False

    # 3. Resolve LogSource for each affected service
    log_source_map: Dict[str, LogSource] = {}
    for svc_item in req.affected_services:
        if svc_item.service_name not in log_source_map:
            log_source = await resolve_log_source_for_service(session, svc_item.service_name)
            log_source_map[svc_item.service_name] = log_source

    # 3. Resolve primary culprit service log source
    primary_log_source_id: Optional[uuid.UUID] = None
    primary_culprit_svc = None
    for s in req.affected_services:
        if s.impact_level == "PRIMARY_CULPRIT":
            primary_culprit_svc = s.service_name
            break

    if not primary_culprit_svc and req.timeline_events:
        for ev in req.timeline_events:
            if ev.is_earliest_observed_failure:
                primary_culprit_svc = ev.service_name
                break

    if primary_culprit_svc and primary_culprit_svc in log_source_map:
        primary_log_source_id = log_source_map[primary_culprit_svc].id
    elif primary_culprit_svc:
        p_src = await resolve_log_source_for_service(session, primary_culprit_svc)
        primary_log_source_id = p_src.id
        log_source_map[primary_culprit_svc] = p_src

    # 4. Assess Evidence Sufficiency Deterministically
    assessment = evaluate_evidence_sufficiency(
        evidence_items=req.evidence_items,
        timeline_events=req.timeline_events,
    )

    # 5. Assemble Incident model
    now = datetime.now(timezone.utc)
    uncertainty_msg = (
        req.uncertainty_reason
        or (
            f"Earliest observed failure identified chronologically. {assessment.uncertainty_statement}"
        )
    )

    incident = Incident(
        id=incident_uuid,
        title=req.title,
        severity=req.severity,
        status="DETECTED",
        analysis_status=assessment.classification.value,
        primary_log_source_id=primary_log_source_id,
        started_at=req.started_at,
        ended_at=req.ended_at,
        detected_at=now,
        probable_cause=None,
        confidence_score=0.0,
        is_root_cause_confirmed=False,
        uncertainty_reason=uncertainty_msg,
        created_at=now,
        updated_at=now,
    )
    session.add(incident)

    # 5. Assemble Affected Services
    for svc_item in req.affected_services:
        src = log_source_map.get(svc_item.service_name)
        if not src:
            src = await resolve_log_source_for_service(session, svc_item.service_name)
            log_source_map[svc_item.service_name] = src

        aff_row = IncidentAffectedService(
            id=uuid.uuid4(),
            incident_id=incident_uuid,
            log_source_id=src.id,
            service_name=svc_item.service_name,
            impact_level=svc_item.impact_level,
            first_impacted_at=svc_item.first_impacted_at,
            error_count=svc_item.error_count,
            created_at=now,
            updated_at=now,
        )
        session.add(aff_row)

    # 6. Assemble Timeline Events
    event_id_by_seq: Dict[int, uuid.UUID] = {}
    for ev in req.timeline_events:
        ev_uuid = uuid.uuid4()
        event_id_by_seq[ev.sequence_order] = ev_uuid

        ev_attributes = dict(ev.attributes or {})
        ev_attributes["is_earliest_observed_failure"] = ev.is_earliest_observed_failure
        ev_attributes["propagation_lag_seconds"] = ev.propagation_lag_seconds
        ev_attributes["cluster_id"] = ev.cluster_id
        if ev.trace_id:
            ev_attributes["trace_id"] = ev.trace_id
        if ev.span_id:
            ev_attributes["span_id"] = ev.span_id
        if ev.sample_log_id:
            ev_attributes["sample_log_id"] = ev.sample_log_id
        if getattr(ev, "relationship_type", None):
            ev_attributes["relationship_type"] = ev.relationship_type
        if getattr(ev, "correlation_confidence", None) is not None:
            ev_attributes["correlation_confidence"] = ev.correlation_confidence

        event_row = IncidentEvent(
            id=ev_uuid,
            incident_id=incident_uuid,
            event_timestamp=ev.event_timestamp,
            service_name=ev.service_name,
            event_type=ev.event_type,
            severity=ev.severity,
            sequence_order=ev.sequence_order,
            summary=ev.summary,
            attributes=ev_attributes,
            created_at=now,
            updated_at=now,
        )
        session.add(event_row)

    # 7. Assemble Evidence Items (deduplicated deterministically)
    for item in deduplicate_evidence_items(req.evidence_items):
        # Match evidence to event if sequence matches
        linked_ev_id: Optional[uuid.UUID] = None
        for ev in req.timeline_events:
            if ev.sample_log_id and ev.sample_log_id == item.log_id:
                linked_ev_id = event_id_by_seq.get(ev.sequence_order)
                break

        ev_metadata = {
            "correlation_rule": item.correlation_rule,
        }
        if item.trace_id:
            ev_metadata["trace_id"] = item.trace_id
        if getattr(item, "relationship_type", None):
            ev_metadata["relationship_type"] = item.relationship_type
        if getattr(item, "correlation_confidence", None) is not None:
            ev_metadata["correlation_confidence"] = item.correlation_confidence

        evidence_row = Evidence(
            id=uuid.uuid4(),
            incident_id=incident_uuid,
            incident_event_id=linked_ev_id,
            evidence_type=item.evidence_type,
            opensearch_index=f"{settings.OPENSEARCH_INDEX_PREFIX}-*",
            opensearch_doc_id=item.log_id,
            log_timestamp=item.timestamp,
            service_name=item.service_name,
            log_level=item.log_level,
            log_snippet=item.snippet,
            content_hash=item.content_hash,
            document_status="INDEXED",
            relevance_score=1.0,
            explanation=item.rationale,
            metadata_=ev_metadata,
            created_at=now,
            updated_at=now,
        )
        session.add(evidence_row)

    # 8. Commit with concurrent race condition safety
    try:
        await session.flush()
    except IntegrityError:
        logger.warning(
            "IntegrityError inserting incident '%s'; checking for concurrent insertion.",
            incident_uuid,
        )
        await session.rollback()
        existing_concurrent = await get_incident_by_id(session, incident_uuid)
        if existing_concurrent:
            return existing_concurrent, False
        raise

    # Re-query loaded incident to return complete entity with relationships
    created_incident = await get_incident_by_id(session, incident_uuid)
    return created_incident or incident, True


async def list_incident_catalogue(
    session: AsyncSession,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    service: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
) -> IncidentListResponse:
    """Retrieve paginated incidents matching operational and temporal criteria."""
    # Build filter conditions
    conditions = []
    if status and status.strip():
        conditions.append(Incident.status == status.strip())
    if severity and severity.strip():
        conditions.append(Incident.severity == severity.strip())
    if start_time:
        conditions.append(Incident.started_at >= start_time)
    if end_time:
        conditions.append(Incident.ended_at <= end_time)

    # Join affected_services if service filter is requested
    base_query = select(Incident)
    count_query = select(func.count(distinct(Incident.id)))

    if service and service.strip():
        svc_name = service.strip()
        base_query = base_query.join(
            IncidentAffectedService,
            Incident.id == IncidentAffectedService.incident_id,
        ).where(IncidentAffectedService.service_name == svc_name)
        count_query = count_query.join(
            IncidentAffectedService,
            Incident.id == IncidentAffectedService.incident_id,
        ).where(IncidentAffectedService.service_name == svc_name)

    if conditions:
        base_query = base_query.where(*conditions)
        count_query = count_query.where(*conditions)

    # Get total count
    count_res = await session.execute(count_query)
    total_count = count_res.scalar() or 0

    # Paginate and load relations
    offset = max(0, (page - 1) * page_size)
    paginated_stmt = (
        base_query.options(
            selectinload(Incident.affected_services),
            selectinload(Incident.events),
            selectinload(Incident.evidence),
            selectinload(Incident.primary_log_source),
        )
        .order_by(desc(Incident.started_at))
        .offset(offset)
        .limit(page_size)
    )

    result = await session.execute(paginated_stmt)
    incidents = result.scalars().all()

    total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count > 0 else 0
    items = [map_incident_to_summary_response(inc) for inc in incidents]

    return IncidentListResponse(
        items=items,
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


ALLOWED_INCIDENT_STATUS_TRANSITIONS: Dict[str, Set[str]] = {
    "DETECTED": {"INVESTIGATING", "MITIGATED", "RESOLVED", "FALSE_POSITIVE"},
    "INVESTIGATING": {"MITIGATED", "RESOLVED", "FALSE_POSITIVE"},
    "MITIGATED": {"INVESTIGATING", "RESOLVED"},
    "RESOLVED": set(),
    "FALSE_POSITIVE": set(),
}


def validate_incident_status_transition(current_status: str, target_status: str) -> None:
    """Validate operational status transition against strict lifecycle rules.

    Permits idempotent updates (target_status == current_status).
    Raises HTTP 409 Conflict if transition is disallowed.
    """
    if current_status == target_status:
        return

    allowed = ALLOWED_INCIDENT_STATUS_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Invalid lifecycle transition from '{current_status}' to '{target_status}'. "
                f"Allowed transitions: {sorted(list(allowed)) or 'None (terminal state)'}."
            ),
        )


async def update_incident_status(
    session: AsyncSession,
    incident_id: uuid.UUID,
    req: IncidentStatusUpdateRequest,
) -> Incident:
    """Update incident operational lifecycle status with automatic timestamp management.

    Rules:
    - DETECTED -> INVESTIGATING, MITIGATED, RESOLVED, FALSE_POSITIVE
    - INVESTIGATING -> MITIGATED, RESOLVED, FALSE_POSITIVE
    - MITIGATED -> INVESTIGATING, RESOLVED
    - RESOLVED, FALSE_POSITIVE -> terminal (no transitions)
    - Idempotent: identical status updates return existing incident without updating timestamps.
    - MITIGATED or RESOLVED sets ended_at if not already set.
    - MITIGATED -> INVESTIGATING clears ended_at.
    - updated_at is refreshed on active transitions.
    """
    incident = await get_incident_by_id(session, incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    target_status = req.status.value if hasattr(req.status, "value") else str(req.status)
    current_status = incident.status

    # Centralized lifecycle validation
    validate_incident_status_transition(current_status, target_status)

    # Idempotent: return unmodified if target matches current
    if target_status == current_status:
        return incident

    now = datetime.now(timezone.utc)

    # Manage ended_at timestamp according to lifecycle semantics
    if target_status in ("MITIGATED", "RESOLVED"):
        if not incident.ended_at:
            incident.ended_at = now
    elif current_status == "MITIGATED" and target_status == "INVESTIGATING":
        incident.ended_at = None

    incident.status = target_status
    incident.updated_at = now

    await session.commit()

    # Re-fetch with loaded relations for consistent response mapping
    reloaded = await get_incident_by_id(session, incident_id)
    return reloaded or incident


async def append_correlated_event_to_incident(
    session: AsyncSession,
    incident_id: uuid.UUID,
    event: LogEventItem,
    correlation_rule: Optional[str] = None,
    rationale: Optional[str] = None,
    cascade_window_seconds: int = 120,
) -> Tuple[Optional[IncidentEvent], Optional[Evidence], bool]:
    """Append a correlated event and its evidence record to an existing incident.

    Guarantees:
    - Atomicity & Concurrency: Acquires row-level lock on Incident (with_for_update).
    - Idempotency: Duplicate log_id returns existing event/evidence without creating duplicates (created=False).
    - Boundary Enforcement: Calls evaluate_incident_grouping; rejects non-matching events with HTTP 400.
    - Chronological & Sequence Ordering: Sequence order is contiguous and strictly non-decreasing.
    - Preserves Status: Does NOT automatically confirm root cause or change incident confirmation state.
    """
    stmt = (
        select(Incident)
        .options(
            selectinload(Incident.affected_services),
            selectinload(Incident.events),
            selectinload(Incident.evidence),
            selectinload(Incident.primary_log_source),
        )
        .where(Incident.id == incident_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    # 1. Evaluate whether the event belongs to this incident
    grouping_eval: IncidentGroupingEvaluation = evaluate_incident_grouping(
        incident=incident,
        event=event,
        cascade_window_seconds=cascade_window_seconds,
    )
    if not grouping_eval.matches:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Event does not correlate to incident '{incident_id}': {grouping_eval.rationale}",
        )

    # 2. Check for duplicate log record
    if grouping_eval.is_duplicate_log:
        existing_ev = None
        for ev in (incident.evidence or []):
            if ev.opensearch_doc_id == event.log_id:
                existing_ev = ev
                break
        existing_event = None
        if existing_ev and existing_ev.incident_event_id:
            for ie in (incident.events or []):
                if ie.id == existing_ev.incident_event_id:
                    existing_event = ie
                    break
        return existing_event, existing_ev, False

    # 3. Derive next contiguous sequence order
    current_sequences = [e.sequence_order for e in (incident.events or [])]
    next_seq = max(current_sequences, default=-1) + 1

    now = datetime.now(timezone.utc)
    ev_ts = _ensure_utc(event.timestamp) or now
    first_ts = _ensure_utc(incident.started_at)
    lag_seconds = max(0.0, round((ev_ts - first_ts).total_seconds(), 3)) if first_ts else 0.0

    # 4. Resolve LogSource for service if needed
    source = await resolve_log_source_for_service(session, event.service_name or "unassigned")

    # Check if affected service is registered
    existing_services = {s.service_name: s for s in (incident.affected_services or [])}
    if event.service_name not in existing_services:
        aff_row = IncidentAffectedService(
            id=uuid.uuid4(),
            incident_id=incident.id,
            log_source_id=source.id,
            service_name=event.service_name or "unassigned",
            impact_level="DOWNSTREAM_CASCADE" if grouping_eval.match_rule == "TRACE_MATCH" else "CORRELATED_ANOMALY",
            first_impacted_at=ev_ts,
            error_count=1,
            created_at=now,
            updated_at=now,
        )
        session.add(aff_row)
    else:
        existing_services[event.service_name].error_count += 1

    # 5. Create new IncidentEvent
    resolved_rule = correlation_rule or grouping_eval.match_rule
    resolved_rationale = rationale or grouping_eval.rationale
    ev_uuid = uuid.uuid4()

    event_attributes = dict(event.attributes or {})
    event_attributes["is_earliest_observed_failure"] = False
    event_attributes["propagation_lag_seconds"] = lag_seconds
    event_attributes["relationship_type"] = grouping_eval.relationship_type or "TEMPORAL_ASSOCIATION"
    event_attributes["correlation_confidence"] = grouping_eval.confidence
    if event.trace_id:
        event_attributes["trace_id"] = event.trace_id
    if event.span_id:
        event_attributes["span_id"] = event.span_id
    event_attributes["sample_log_id"] = event.log_id

    event_row = IncidentEvent(
        id=ev_uuid,
        incident_id=incident.id,
        event_timestamp=ev_ts,
        service_name=event.service_name or "unassigned",
        event_type="SEQUENCE_ANOMALY" if event.attributes.get("model") == "LogBERT" else "CASCADING_FAILURE",
        severity=event.log_level,
        sequence_order=next_seq,
        summary=f"Correlated {event.log_level} in '{event.service_name}': {event.message[:120]}",
        attributes=event_attributes,
        created_at=now,
        updated_at=now,
    )
    session.add(event_row)

    # 6. Create new Evidence item
    content_hash = (
        hashlib.sha256(event.raw_log.encode("utf-8")).hexdigest()
        if event.raw_log
        else None
    )
    ev_metadata = {
        "correlation_rule": resolved_rule,
        "relationship_type": grouping_eval.relationship_type or "TEMPORAL_ASSOCIATION",
        "correlation_confidence": grouping_eval.confidence,
    }
    if event.trace_id:
        ev_metadata["trace_id"] = event.trace_id

    evidence_row = Evidence(
        id=uuid.uuid4(),
        incident_id=incident.id,
        incident_event_id=ev_uuid,
        evidence_type="SEQUENCE_ANOMALY" if event.attributes.get("model") == "LogBERT" else "CORRELATION_SPIKE",
        opensearch_index=f"{settings.OPENSEARCH_INDEX_PREFIX}-*",
        opensearch_doc_id=event.log_id,
        log_timestamp=ev_ts,
        service_name=event.service_name or "unassigned",
        log_level=event.log_level,
        log_snippet=event.message[:500],
        content_hash=content_hash,
        document_status="INDEXED",
        relevance_score=grouping_eval.confidence,
        explanation=resolved_rationale,
        metadata_=ev_metadata,
        created_at=now,
        updated_at=now,
    )
    session.add(evidence_row)

    # 7. Update incident temporal end if new event extends it
    curr_ended_at = _ensure_utc(incident.ended_at)
    if curr_ended_at is None or ev_ts > curr_ended_at:
        incident.ended_at = ev_ts
    incident.updated_at = now

    await session.commit()
    await session.refresh(event_row)
    await session.refresh(evidence_row)
    await session.refresh(incident)

    return event_row, evidence_row, True

