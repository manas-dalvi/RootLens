from datetime import datetime
import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from opensearchpy import OpenSearch
from opensearchpy.exceptions import ConnectionError, ConnectionTimeout, NotFoundError, RequestError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.opensearch import get_opensearch_client
from app.db.session import get_db
from app.schemas.incident import (
    CorrelatedAffectedServiceResponse,
    CorrelatedEvidenceItemResponse,
    CorrelatedTimelineEventResponse,
    CorrelateEventRequest,
    IncidentGroupingEvaluationResponse,
    HypothesisDecisionEnum,
    IncidentCandidateRequest,
    IncidentCandidateResponse,
    EvidenceAssessmentResponse,
    RCACandidateFindingResponse,
    RCACitationItemResponse,
    RCAClaimMappingResponse,
    RCAHypothesisActionRequest,
    RCAHypothesisDecisionCreateRequest,
    RCAHypothesisDecisionResponse,
    RCAHypothesisDecisionsSummaryResponse,
    RCASynthesisResponse,
    IncidentCandidatesResponse,
    IncidentCreateRequest,
    IncidentDetailResponse,
    IncidentListResponse,
    IncidentStatusUpdateRequest,
    IncidentSummaryItemResponse,
)
from app.schemas.investigation import (
    IncidentExplanationResponse,
    IncidentInvestigationRequest,
    IncidentInvestigationResponse,
)
from app.schemas.recommendation import (
    RecommendationGenerateRequest,
    RecommendationListResponse,
    RecommendationResponse,
)
from app.schemas.report import (
    ReportCreateRequest,
    ReportListResponse,
    ReportResponse,
)
from app.core.auth import (
    OperatorIdentity,
    get_current_operator,
    require_authorized_operator,
)
from app.services.evidence_analysis_service import evaluate_evidence_sufficiency
from app.services.rca_synthesis_service import synthesize_rca_findings
from app.services.hypothesis_decision_service import (
    get_hypothesis_decisions_summary,
    record_hypothesis_decision,
)
from app.services.recommendation_service import (
    generate_incident_recommendations,
    get_incident_recommendations,
    get_recommendation_by_id as get_incident_rec_by_id,
)

from app.services.clustering_service import (
    LogEventItem,
    cluster_log_events,
    parse_opensearch_hit_to_event,
)
from app.services.correlation_service import (
    CorrelatedIncidentCandidate,
    correlate_clusters,
    evaluate_incident_grouping,
)
from app.services.incident_persistence_service import (
    append_correlated_event_to_incident,
    get_incident_by_id,
    list_incident_catalogue,
    map_incident_to_detail_response,
    persist_incident,
    update_incident_status,
)
from app.services.report_service import (
    generate_postmortem_report,
    get_incident_reports,
    get_report_by_id,
)
from app.services.investigation_service import (
    get_read_only_explanation,
    investigate_incident,
)

logger = logging.getLogger("rootlens.incidents")

router = APIRouter()


def evaluate_candidates_from_opensearch(
    req: IncidentCandidateRequest,
    client: Optional[OpenSearch] = None,
) -> IncidentCandidatesResponse:
    """Evaluate candidate incidents in-memory from indexed OpenSearch error logs.

    Read-only operation: does NOT mutate or persist incident records into PostgreSQL.
    """
    # 1. Validate temporal boundaries
    if req.start_time and req.end_time and req.start_time > req.end_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_time must be earlier than or equal to end_time.",
        )

    # 2. Check OpenSearch connectivity
    target_client = client or get_opensearch_client()
    try:
        is_alive = target_client.ping()
    except Exception:
        is_alive = False

    if not is_alive:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )

    # 3. Build OpenSearch query for error & fatal logs (or all logs if sequence anomaly detection requested)
    filter_clauses: List[Dict[str, Any]] = []
    if not req.include_sequence_anomalies:
        filter_clauses.append({"terms": {"log_level": ["ERROR", "FATAL", "CRITICAL"]}})

    if req.start_time or req.end_time:
        range_dict: Dict[str, str] = {}
        if req.start_time:
            range_dict["gte"] = req.start_time.isoformat()
        if req.end_time:
            range_dict["lte"] = req.end_time.isoformat()
        filter_clauses.append({"range": {"timestamp": range_dict}})

    if req.service and req.service.strip():
        filter_clauses.append({"term": {"service_name": req.service.strip()}})

    if req.environment and req.environment.strip():
        filter_clauses.append({"term": {"environment": req.environment.strip()}})

    search_query = {
        "size": req.max_logs,
        "query": {"bool": {"filter": filter_clauses}},
        "sort": [{"timestamp": {"order": "desc"}}],
    }

    target_index = f"{settings.OPENSEARCH_INDEX_PREFIX}-*"

    try:
        search_res = target_client.search(index=target_index, body=search_query)
    except NotFoundError:
        return IncidentCandidatesResponse(
            total_candidates=0, total_affected_services=0, candidates=[]
        )
    except (ConnectionError, ConnectionTimeout):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )
    except RequestError as err:
        logger.warning("Invalid candidate evaluation search parameters: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid search parameters for candidate evaluation.",
        )
    except Exception as exc:
        logger.error("Error querying OpenSearch for incident candidates: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query log telemetry for incident candidate evaluation.",
        )

    hits = search_res.get("hits", {}).get("hits", [])
    if not hits:
        return IncidentCandidatesResponse(
            total_candidates=0, total_affected_services=0, candidates=[]
        )

    # 4. Parse hits into LogEventItem instances
    events = []
    for hit in hits:
        ev = parse_opensearch_hit_to_event(hit)
        if ev:
            events.append(ev)

    if not events:
        return IncidentCandidatesResponse(
            total_candidates=0, total_affected_services=0, candidates=[]
        )

    # 5. Cluster events deterministically by (service, pattern, window)
    clusters = cluster_log_events(events, window_minutes=req.window_minutes)

    # 5b. Evaluate sequence anomalies via LogBERT if requested
    if req.include_sequence_anomalies:
        try:
            from app.services.logbert_service import detect_sequence_anomalies_as_clusters
            anomaly_clusters = detect_sequence_anomalies_as_clusters(
                events, window_minutes=req.window_minutes
            )
            clusters.extend(anomaly_clusters)
        except Exception as exc:
            logger.warning("LogBERT sequence anomaly evaluation skipped: %s", exc)

    if not clusters:
        return IncidentCandidatesResponse(
            total_candidates=0, total_affected_services=0, candidates=[]
        )

    # 6. Correlate clusters across services
    raw_candidates: List[CorrelatedIncidentCandidate] = correlate_clusters(
        clusters, cascade_window_seconds=req.cascade_window_seconds
    )

    # 7. Convert internal dataclasses into response models
    resp_candidates: List[IncidentCandidateResponse] = []
    all_affected_services = set()

    for cand in raw_candidates:
        aff_services = [
            CorrelatedAffectedServiceResponse(
                service_name=s.service_name,
                impact_level=s.impact_level,
                first_impacted_at=s.first_impacted_at,
                error_count=s.error_count,
            )
            for s in cand.affected_services
        ]
        for s in aff_services:
            all_affected_services.add(s.service_name)

        tl_events = [
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
                relationship_type=(e.attributes or {}).get("relationship_type"),
                correlation_confidence=(e.attributes or {}).get("correlation_confidence"),
                attributes=e.attributes,
            )
            for e in cand.timeline_events
        ]

        ev_items = [
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
                relationship_type=getattr(item, "relationship_type", None),
                correlation_confidence=getattr(item, "correlation_confidence", None),
            )
            for item in cand.evidence_items
        ]

        assessment_obj = getattr(cand, "assessment", None)
        if assessment_obj is None:
            assessment_obj = evaluate_evidence_sufficiency(
                evidence_items=ev_items,
                timeline_events=tl_events,
            )
        rca_obj = getattr(cand, "rca_synthesis", None)
        if rca_obj is None:
            rca_obj = synthesize_rca_findings(
                assessment=assessment_obj,
                evidence_items=ev_items,
                timeline_events=tl_events,
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

        resp_candidates.append(
            IncidentCandidateResponse(
                candidate_id=cand.candidate_id,
                title=cand.title,
                severity=cand.severity,
                started_at=cand.started_at,
                ended_at=cand.ended_at,
                correlation_rules_used=cand.correlation_rules_used,
                is_root_cause_confirmed=False,
                uncertainty_reason=cand.uncertainty_reason,
                affected_services=aff_services,
                timeline_events=tl_events,
                evidence_items=ev_items,
                cluster_ids=cand.cluster_ids,
                assessment=assessment_resp,
                rca_synthesis=rca_resp,
            )
        )

    return IncidentCandidatesResponse(
        total_candidates=len(resp_candidates),
        total_affected_services=len(all_affected_services),
        candidates=resp_candidates,
    )


@router.post(
    "/candidates",
    response_model=IncidentCandidatesResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate candidate incidents from indexed log telemetry",
    description=(
        "Evaluates error log clusters and reconstructs cross-service candidate incident "
        "timelines in-memory. Read-only: does not persist records to PostgreSQL."
    ),
)
def evaluate_incident_candidates_post(
    req: IncidentCandidateRequest,
) -> IncidentCandidatesResponse:
    """Evaluate candidate incidents in-memory via POST body."""
    return evaluate_candidates_from_opensearch(req)


@router.get(
    "/candidates",
    response_model=IncidentCandidatesResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate candidate incidents via query parameters",
    description="Convenience GET endpoint to evaluate incident candidates using query parameters.",
)
def evaluate_incident_candidates_get(
    start_time: Optional[datetime] = Query(None, description="Start timestamp filter boundary (ISO-8601)"),
    end_time: Optional[datetime] = Query(None, description="End timestamp filter boundary (ISO-8601)"),
    service: Optional[str] = Query(None, description="Optional service name filter"),
    environment: Optional[str] = Query(None, description="Optional environment filter"),
    window_minutes: int = Query(15, ge=1, le=1440, description="Window size in minutes"),
    cascade_window_seconds: int = Query(120, ge=1, le=3600, description="Cascade correlation threshold in seconds"),
    max_logs: int = Query(1000, ge=1, le=5000, description="Maximum error logs to analyze"),
    include_sequence_anomalies: bool = Query(False, description="Whether to include LogBERT sequence anomaly evaluation"),
) -> IncidentCandidatesResponse:
    """Evaluate candidate incidents in-memory via GET query parameters."""
    req = IncidentCandidateRequest(
        start_time=start_time,
        end_time=end_time,
        service=service,
        environment=environment,
        window_minutes=window_minutes,
        cascade_window_seconds=cascade_window_seconds,
        max_logs=max_logs,
        include_sequence_anomalies=include_sequence_anomalies,
    )
    return evaluate_candidates_from_opensearch(req)


@router.post(
    "",
    response_model=IncidentDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Persist an evaluated incident candidate to PostgreSQL",
    description=(
        "Idempotently persists an incident candidate, affected blast-radius services, "
        "chronological timeline events, and evidence references into PostgreSQL."
    ),
)
async def create_persistent_incident(
    req: IncidentCreateRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> IncidentDetailResponse:
    """Idempotently persist an incident candidate into PostgreSQL."""
    incident, created = await persist_incident(session, req)
    if not created:
        response.status_code = status.HTTP_200_OK
    return map_incident_to_detail_response(incident)


@router.get(
    "",
    response_model=IncidentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List persisted incidents",
    description="Retrieve paginated catalogue of persisted incidents with optional filtering.",
)
async def list_persisted_incidents(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (DETECTED, etc.)"),
    severity: Optional[str] = Query(None, description="Filter by severity (CRITICAL, HIGH, MEDIUM, LOW)"),
    service: Optional[str] = Query(None, description="Filter by affected service name"),
    start_time: Optional[datetime] = Query(None, description="Start timestamp filter boundary (ISO-8601)"),
    end_time: Optional[datetime] = Query(None, description="End timestamp filter boundary (ISO-8601)"),
    page: int = Query(1, ge=1, description="One-based page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size limit"),
    session: AsyncSession = Depends(get_db),
) -> IncidentListResponse:
    """Retrieve paginated persisted incidents."""
    return await list_incident_catalogue(
        session=session,
        status=status_filter,
        severity=severity,
        service=service,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{id}",
    response_model=IncidentDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get persisted incident detail",
    description="Retrieve full record, affected services, chronological timeline, and evidence for an incident.",
)
async def get_persisted_incident_by_id(
    id: str,
    session: AsyncSession = Depends(get_db),
) -> IncidentDetailResponse:
    """Retrieve full persisted incident details by unique UUID."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    incident = await get_incident_by_id(session, incident_uuid)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    return map_incident_to_detail_response(incident)


@router.patch(
    "/{id}/status",
    response_model=IncidentDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update incident operational lifecycle status",
    description=(
        "Transitions an incident's operational lifecycle status following strict transition rules: "
        "DETECTED -> [INVESTIGATING, MITIGATED, RESOLVED, FALSE_POSITIVE], "
        "INVESTIGATING -> [MITIGATED, RESOLVED, FALSE_POSITIVE], "
        "MITIGATED -> [INVESTIGATING, RESOLVED]. "
        "RESOLVED and FALSE_POSITIVE are terminal."
    ),
)
async def patch_incident_status(
    id: str,
    req: IncidentStatusUpdateRequest,
    session: AsyncSession = Depends(get_db),
) -> IncidentDetailResponse:
    """Transition an incident's operational lifecycle status."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    updated_incident = await update_incident_status(
        session=session,
        incident_id=incident_uuid,
        req=req,
    )
    return map_incident_to_detail_response(updated_incident)


@router.post(
    "/{id}/reports",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate or regenerate an evidence-grounded post-mortem report",
    description=(
        "Generates, persists, or idempotently updates a post-mortem report for an incident. "
        "Generated reports strictly begin in DRAFT status and preserve all RootLens diagnostic invariants."
    ),
)
async def create_incident_report(
    id: str,
    response: Response,
    req: Optional[ReportCreateRequest] = None,
    session: AsyncSession = Depends(get_db),
) -> ReportResponse:
    """Generate or idempotently update an incident post-mortem report."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    report, created = await generate_postmortem_report(
        session=session,
        incident_id=incident_uuid,
        req=req,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return ReportResponse.from_orm_report(report)


@router.get(
    "/{id}/reports",
    response_model=ReportListResponse,
    status_code=status.HTTP_200_OK,
    summary="List post-mortem reports for an incident",
    description="Retrieve all persisted post-mortem reports generated for an incident in descending order.",
)
async def list_incident_reports(
    id: str,
    session: AsyncSession = Depends(get_db),
) -> ReportListResponse:
    """List all reports generated for an incident."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    incident = await get_incident_by_id(session, incident_uuid)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    reports = await get_incident_reports(session, incident_uuid)
    return ReportListResponse(
        items=[ReportResponse.from_orm_report(r) for r in reports],
        total=len(reports),
        incident_id=str(incident_uuid),
    )


@router.get(
    "/{id}/reports/{report_id}",
    response_model=ReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get post-mortem report by ID",
    description="Retrieve an individual post-mortem report strictly verifying it belongs to the incident.",
)
async def get_incident_report_by_id(
    id: str,
    report_id: str,
    session: AsyncSession = Depends(get_db),
) -> ReportResponse:
    """Retrieve an individual post-mortem report by incident ID and report ID."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    try:
        report_uuid = uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report '{report_id}' not found for incident '{id}'.",
        )

    report = await get_report_by_id(session, incident_uuid, report_uuid)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report '{report_id}' not found for incident '{id}'.",
        )

    return ReportResponse.from_orm_report(report)


@router.post(
    "/{id}/recommendations",
    response_model=RecommendationListResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate or retrieve troubleshooting recommendations",
    description=(
        "Generates, persists, or idempotently retrieves evidence-grounded troubleshooting recommendations. "
        "Recommendations are strictly advisory; RootLens performs zero autonomous remediation."
    ),
)
async def create_incident_recommendations(
    id: str,
    response: Response,
    req: Optional[RecommendationGenerateRequest] = None,
    session: AsyncSession = Depends(get_db),
) -> RecommendationListResponse:
    """Generate or idempotently retrieve troubleshooting recommendations for an incident."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    incident = await get_incident_by_id(session, incident_uuid)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    recs, created = await generate_incident_recommendations(
        session=session,
        incident_id=incident_uuid,
        req=req,
    )
    if not created:
        response.status_code = status.HTTP_200_OK

    classification_str = (
        incident.analysis_status
        if incident.analysis_status
        else "INSUFFICIENT"
    )

    return RecommendationListResponse(
        items=[RecommendationResponse.from_orm_recommendation(r) for r in recs],
        total=len(recs),
        incident_id=str(incident_uuid),
        telemetry_classification=classification_str,
        is_advisory_only=True,
        is_root_cause_confirmed=False,
    )


@router.get(
    "/{id}/recommendations",
    response_model=RecommendationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List troubleshooting recommendations for an incident",
    description="Retrieve all persisted advisory troubleshooting recommendations for an incident.",
)
async def list_incident_recommendations(
    id: str,
    session: AsyncSession = Depends(get_db),
) -> RecommendationListResponse:
    """List all persisted recommendations for an incident."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    incident = await get_incident_by_id(session, incident_uuid)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    recs = await get_incident_recommendations(session, incident_uuid)
    classification_str = (
        incident.analysis_status
        if incident.analysis_status
        else "INSUFFICIENT"
    )

    return RecommendationListResponse(
        items=[RecommendationResponse.from_orm_recommendation(r) for r in recs],
        total=len(recs),
        incident_id=str(incident_uuid),
        telemetry_classification=classification_str,
        is_advisory_only=True,
        is_root_cause_confirmed=False,
    )


@router.get(
    "/{id}/recommendations/{recommendation_id}",
    response_model=RecommendationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get troubleshooting recommendation by ID",
    description="Retrieve an individual recommendation strictly verifying it belongs to the target incident.",
)
async def get_incident_recommendation_by_id(
    id: str,
    recommendation_id: str,
    session: AsyncSession = Depends(get_db),
) -> RecommendationResponse:
    """Retrieve an individual recommendation by incident ID and recommendation ID."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    try:
        rec_uuid = uuid.UUID(recommendation_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recommendation '{recommendation_id}' not found for incident '{id}'.",
        )

    rec = await get_incident_rec_by_id(session, incident_uuid, rec_uuid)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recommendation '{recommendation_id}' not found for incident '{id}'.",
        )

    return RecommendationResponse.from_orm_recommendation(rec)


@router.get(
    "/{id}/explanation",
    response_model=IncidentExplanationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get incident diagnostic explanation",
    description="Retrieve a strictly read-only, evidence-grounded diagnostic explanation. Deterministic by default.",
)
async def get_incident_explanation(
    id: str,
    use_llm: bool = Query(False, description="Whether to invoke optional LLM explanation service"),
    session: AsyncSession = Depends(get_db),
) -> IncidentExplanationResponse:
    """Retrieve an evidence-grounded explanation for an incident (deterministic by default, zero DB writes)."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    return await get_read_only_explanation(
        session=session,
        incident_id=incident_uuid,
        use_llm=use_llm,
    )


@router.post(
    "/{id}/investigate",
    response_model=IncidentInvestigationResponse,
    status_code=status.HTTP_200_OK,
    summary="Investigate incident via natural-language inquiry",
    description="Stateless natural-language investigation query about one incident grounded in verified telemetry.",
)
async def post_incident_investigate(
    id: str,
    req: IncidentInvestigationRequest,
    session: AsyncSession = Depends(get_db),
) -> IncidentInvestigationResponse:
    """Execute stateless natural-language inquiry about an incident."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    return await investigate_incident(
        session=session,
        incident_id=incident_uuid,
        req=req,
    )


@router.get(
    "/{id}/hypotheses/decisions",
    response_model=RCAHypothesisDecisionsSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Get operator hypothesis decision status and history",
    description="Retrieve all operator decisions and full audit history for hypotheses on this incident.",
)
async def get_incident_hypothesis_decisions(
    id: str,
    session: AsyncSession = Depends(get_db),
    operator: OperatorIdentity = Depends(get_current_operator),
) -> RCAHypothesisDecisionsSummaryResponse:
    """Retrieve full audit history and active decision status for incident hypotheses."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    return await get_hypothesis_decisions_summary(session=session, incident_id=incident_uuid)


@router.post(
    "/{id}/hypotheses/{hypothesis_id}/decision",
    response_model=RCAHypothesisDecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Record operator decision on an RCA hypothesis",
    description=(
        "Human-in-the-loop workflow: Record an immutable operator review decision (CONFIRM or REJECT). "
        "Strict invariant: AI-generated hypotheses never become confirmed root causes automatically. "
        "Confirmation requires verified evidence citations; unsupported hypotheses cannot be confirmed."
    ),
)
async def post_hypothesis_decision(
    id: str,
    hypothesis_id: str,
    req: RCAHypothesisDecisionCreateRequest,
    session: AsyncSession = Depends(get_db),
    operator: OperatorIdentity = Depends(require_authorized_operator),
) -> RCAHypothesisDecisionResponse:
    """Submit an operator decision on a candidate hypothesis."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    return await record_hypothesis_decision(
        session=session,
        incident_id=incident_uuid,
        hypothesis_id=hypothesis_id,
        req=req,
        operator=operator,
    )


@router.post(
    "/{id}/hypotheses/{hypothesis_id}/confirm",
    response_model=RCAHypothesisDecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm RCA hypothesis as root cause",
    description="Promotes an evidence-grounded hypothesis to confirmed root cause with mandatory operator notes.",
)
async def post_hypothesis_confirm(
    id: str,
    hypothesis_id: str,
    req: RCAHypothesisActionRequest,
    session: AsyncSession = Depends(get_db),
    operator: OperatorIdentity = Depends(require_authorized_operator),
) -> RCAHypothesisDecisionResponse:
    """Promote an evidence-grounded hypothesis to confirmed root cause."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    full_req = RCAHypothesisDecisionCreateRequest(
        decision=HypothesisDecisionEnum.CONFIRM,
        notes=req.notes,
        hypothesis_text=req.hypothesis_text,
    )

    return await record_hypothesis_decision(
        session=session,
        incident_id=incident_uuid,
        hypothesis_id=hypothesis_id,
        req=full_req,
        operator=operator,
    )


@router.post(
    "/{id}/grouping-check",
    response_model=IncidentGroupingEvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate event correlation to incident",
    description="Deterministically evaluates whether a log event or anomaly belongs to an existing incident.",
)
async def post_incident_grouping_check(
    id: str,
    req: CorrelateEventRequest,
    cascade_window_seconds: int = Query(120, ge=1, le=3600, description="Cascade window threshold in seconds"),
    session: AsyncSession = Depends(get_db),
) -> IncidentGroupingEvaluationResponse:
    """Evaluate event correlation against an existing incident without modifying database state."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    incident = await get_incident_by_id(session, incident_uuid)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    event_item = LogEventItem(
        log_id=req.log_id,
        timestamp=req.timestamp,
        service_name=req.service_name or "unassigned",
        log_level=req.log_level,
        message=req.message,
        trace_id=req.trace_id,
        span_id=req.span_id,
        raw_log=req.message,
        attributes=req.attributes,
    )

    evaluation = evaluate_incident_grouping(
        incident=incident,
        event=event_item,
        cascade_window_seconds=cascade_window_seconds,
    )

    return IncidentGroupingEvaluationResponse(
        matches=evaluation.matches,
        incident_id=str(incident.id),
        match_rule=evaluation.match_rule,
        confidence=evaluation.confidence,
        rationale=evaluation.rationale,
        is_duplicate_log=evaluation.is_duplicate_log,
        relationship_type=evaluation.relationship_type,
    )


@router.post(
    "/{id}/events",
    response_model=IncidentDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Append correlated event to incident",
    description="Correlates and appends a new event and verifiable evidence record to an existing incident.",
)
async def post_append_event_to_incident(
    id: str,
    req: CorrelateEventRequest,
    correlation_rule: Optional[str] = Query(None, description="Optional override for correlation rule"),
    rationale: Optional[str] = Query(None, description="Optional custom rationale for correlation"),
    cascade_window_seconds: int = Query(120, ge=1, le=3600, description="Cascade window threshold in seconds"),
    session: AsyncSession = Depends(get_db),
    operator: OperatorIdentity = Depends(require_authorized_operator),
) -> IncidentDetailResponse:
    """Correlate and safely append an event to an existing incident."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    event_item = LogEventItem(
        log_id=req.log_id,
        timestamp=req.timestamp,
        service_name=req.service_name or "unassigned",
        log_level=req.log_level,
        message=req.message,
        trace_id=req.trace_id,
        span_id=req.span_id,
        raw_log=req.message,
        attributes=req.attributes,
    )

    await append_correlated_event_to_incident(
        session=session,
        incident_id=incident_uuid,
        event=event_item,
        correlation_rule=correlation_rule,
        rationale=rationale,
        cascade_window_seconds=cascade_window_seconds,
    )

    # Return refreshed incident detail response
    reloaded = await get_incident_by_id(session, incident_uuid)
    if not reloaded:
        raise HTTPException(status_code=404, detail="Incident not found after appending event.")
    return map_incident_to_detail_response(reloaded)


@router.post(
    "/{id}/hypotheses/{hypothesis_id}/reject",
    response_model=RCAHypothesisDecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Reject RCA hypothesis",
    description="Rejects a hypothesis with mandatory operator rationale and maintains immutable audit history.",
)
async def post_hypothesis_reject(
    id: str,
    hypothesis_id: str,
    req: RCAHypothesisActionRequest,
    session: AsyncSession = Depends(get_db),
    operator: OperatorIdentity = Depends(require_authorized_operator),
) -> RCAHypothesisDecisionResponse:
    """Reject a hypothesis with operator justification."""
    try:
        incident_uuid = uuid.UUID(id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{id}' not found.",
        )

    full_req = RCAHypothesisDecisionCreateRequest(
        decision=HypothesisDecisionEnum.REJECT,
        notes=req.notes,
        hypothesis_text=req.hypothesis_text,
    )

    return await record_hypothesis_decision(
        session=session,
        incident_id=incident_uuid,
        hypothesis_id=hypothesis_id,
        req=full_req,
        operator=operator,
    )

