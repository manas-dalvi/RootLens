"""RootLens Evidence-Backed Troubleshooting Recommendation Service.

Generates, validates, and persists evidence-grounded, non-autonomous troubleshooting
recommendations for incident triage and post-incident investigation.

Strict Invariants:
- All recommendations are strictly advisory; zero autonomous execution or infrastructure mutation.
- IMMEDIATE_ACTION is non-destructive (e.g., traffic isolation, telemetry validation, circuit breakers;
  never restarts, rollbacks, or configuration mutations).
- is_root_cause_confirmed is strictly False; TRACE_LINKED never establishes confirmed causation.
- Telemetry classification allow-lists enforced:
  - INSUFFICIENT: only 'TELEMETRY_GAP' and 'DIAGNOSTIC_VERIFICATION' allowed.
  - TEMPORAL_ONLY: only 'TELEMETRY_GAP', 'DIAGNOSTIC_VERIFICATION', 'MITIGATION_ADVISORY' allowed.
  - TRACE_LINKED: 'TELEMETRY_GAP', 'DIAGNOSTIC_VERIFICATION', 'MITIGATION_ADVISORY', 'IMMEDIATE_ACTION'.
- Evidence citations are strictly validated against incident-scoped records and associated services
  across isolated namespaces ('LOG', 'TRACE', 'SPAN').
- Unsupported causal claims in LLM output trigger conservative all-or-nothing deterministic fallback.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.incident import Incident
from app.models.recommendation import Recommendation
from app.schemas.recommendation import RecommendationGenerateRequest
from app.services.evidence_analysis_service import (
    EvidenceSufficiencyAssessment,
    EvidenceSufficiencyClassification,
    evaluate_evidence_sufficiency,
)
from app.services.llm_explanation_service import (
    BaseLLMClient,
    LLMMessage,
    LLMRequest,
    build_grounded_evidence_context,
    get_llm_client,
)
from app.services.rca_synthesis_service import (
    RCASynthesisResult,
    synthesize_rca_findings,
)

logger = logging.getLogger("rootlens.recommendations")

CLASSIFICATION_ACTION_TYPE_ALLOWLIST: Dict[EvidenceSufficiencyClassification, Set[str]] = {
    EvidenceSufficiencyClassification.INSUFFICIENT: {
        "TELEMETRY_GAP",
        "DIAGNOSTIC_VERIFICATION",
    },
    EvidenceSufficiencyClassification.TEMPORAL_ONLY: {
        "TELEMETRY_GAP",
        "DIAGNOSTIC_VERIFICATION",
        "MITIGATION_ADVISORY",
    },
    EvidenceSufficiencyClassification.TRACE_LINKED: {
        "TELEMETRY_GAP",
        "DIAGNOSTIC_VERIFICATION",
        "MITIGATION_ADVISORY",
        "IMMEDIATE_ACTION",
    },
}

PROHIBITED_CAUSAL_PHRASES = [
    "root cause confirmed",
    "root cause is confirmed",
    "confirmed root cause",
    "proven culprit",
    "conclusively proven root cause",
    "conclusively proven",
    "proven root cause",
    "proven causation",
    "confirmed causation",
    "definitively proven",
    "proven beyond doubt",
    "confirmed the defect",
    "is the confirmed cause",
]


@dataclass(frozen=True)
class RecommendationDraft:
    """Internal validated recommendation representation prior to ORM instantiation."""

    action_type: str
    title: str
    description: str
    priority: str
    sequence_order: int
    verification_step: Optional[str]
    caveat: Optional[str]
    evidence_citations: List[Dict[str, Any]] = field(default_factory=list)


def _format_advisory_caveat(classification: EvidenceSufficiencyClassification) -> str:
    """Format standardized advisory caveat according to diagnostic classification."""
    base = (
        "Advisory recommendation only: RootLens performs zero autonomous remediation or infrastructure mutation. "
    )
    if classification == EvidenceSufficiencyClassification.INSUFFICIENT:
        return (
            f"{base}Telemetry is insufficient to substantiate a root cause hypothesis. "
            "Do NOT deploy speculative or cause-specific infrastructure fixes without diagnostic evidence."
        )
    elif classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY:
        return (
            f"{base}Temporal correlation does not establish confirmed causation. "
            "Co-occurring errors across services may be independent simultaneous failures rather than causal cascades."
        )
    else:  # TRACE_LINKED
        return (
            f"{base}Shared distributed trace linkage demonstrates execution flow, not confirmed root cause defect. "
            "Internal service errors may stem from unmonitored external dependencies or shared resource exhaustion."
        )


def _check_unsupported_causal_claims(text: str) -> bool:
    """Check whether text contains prohibited claims of confirmed causation or proven root cause."""
    lowered = text.lower()
    for phrase in PROHIBITED_CAUSAL_PHRASES:
        if phrase in lowered:
            return True
    return False


def validate_and_resolve_citations(
    citations: Any,
    incident: Incident,
    assessment: EvidenceSufficiencyAssessment,
) -> Optional[List[Dict[str, Any]]]:
    """Validate and resolve citations against actual incident evidence and telemetry.

    Strict Grounding Rules:
    - Never persist fabricated, unknown, or unresolved references.
    - Namespace 'LOG': reference_id must match a real opensearch_doc_id or evidence id,
      and service_name must exactly match the evidence record's service.
    - Namespace 'TRACE': reference_id must match a known trace_id in telemetry,
      and service_name must be associated with that trace.
    - Namespace 'SPAN': reference_id must match an event span_id,
      and service_name must match that event's service.

    Returns:
        List of resolved canonical citation dicts if ALL citations are valid and grounded.
        None if ANY citation is invalid, fabricated, or carries a mismatched service.
    """
    if citations is None:
        return []
    if not isinstance(citations, list):
        return None
    if not citations:
        return []

    # Build exact incident-scoped entity record mappings for isolated namespace validation
    log_record_map: Dict[str, str] = {}  # doc_id -> service_name
    for ev in incident.evidence or []:
        doc_id = ev.opensearch_doc_id or str(ev.id)
        if doc_id and ev.service_name:
            log_record_map[doc_id] = ev.service_name

    trace_record_map: Dict[str, Set[str]] = {}  # trace_id -> set of associated services
    for tid in assessment.shared_trace_ids or []:
        if tid not in trace_record_map:
            trace_record_map[tid] = set()

    for ev in incident.evidence or []:
        meta_tid = (ev.metadata_ or {}).get("trace_id")
        if meta_tid and ev.service_name:
            trace_record_map.setdefault(meta_tid, set()).add(ev.service_name)

    for event in incident.events or []:
        ev_tid = (event.attributes or {}).get("trace_id")
        if ev_tid and event.service_name:
            trace_record_map.setdefault(ev_tid, set()).add(event.service_name)

    span_record_map: Dict[str, str] = {}  # span_id -> service_name
    for event in incident.events or []:
        sp_id = (event.attributes or {}).get("span_id")
        if sp_id and event.service_name:
            span_record_map[sp_id] = event.service_name

    resolved_citations: List[Dict[str, Any]] = []

    for cit in citations:
        if isinstance(cit, dict):
            ref_type = str(cit.get("reference_type", "")).strip().upper()
            ref_id = str(cit.get("reference_id", "")).strip()
            svc_name = str(cit.get("service_name", "")).strip()
        else:
            ref_type = str(getattr(cit, "reference_type", "")).strip().upper()
            ref_id = str(getattr(cit, "reference_id", "")).strip()
            svc_name = str(getattr(cit, "service_name", "")).strip()

        if not ref_id or not svc_name:
            logger.warning("Citation rejected: empty reference_id or service_name.")
            return None

        # Namespace: LOG
        if ref_type == "LOG":
            if ref_id not in log_record_map:
                logger.warning("Citation rejected: log_id '%s' not found in incident evidence.", ref_id)
                return None
            if log_record_map[ref_id] != svc_name:
                logger.warning(
                    "Citation rejected: log_id '%s' associated with '%s', but cited as '%s'.",
                    ref_id,
                    log_record_map[ref_id],
                    svc_name,
                )
                return None

        # Namespace: TRACE
        elif ref_type == "TRACE":
            if ref_id not in trace_record_map:
                logger.warning("Citation rejected: trace_id '%s' not found in incident telemetry.", ref_id)
                return None
            if svc_name not in trace_record_map[ref_id]:
                logger.warning(
                    "Citation rejected: trace_id '%s' not associated with service '%s'.",
                    ref_id,
                    svc_name,
                )
                return None

        # Namespace: SPAN
        elif ref_type == "SPAN":
            if ref_id not in span_record_map:
                logger.warning("Citation rejected: span_id '%s' not found in incident events.", ref_id)
                return None
            if span_record_map[ref_id] != svc_name:
                logger.warning(
                    "Citation rejected: span_id '%s' associated with '%s', but cited as '%s'.",
                    ref_id,
                    span_record_map[ref_id],
                    svc_name,
                )
                return None

        else:
            logger.warning("Citation rejected: unknown reference_type '%s'.", ref_type)
            return None

        resolved_citations.append({
            "reference_type": ref_type,
            "reference_id": ref_id,
            "service_name": svc_name,
        })

    return resolved_citations


def build_deterministic_recommendations(
    incident: Incident,
    assessment: EvidenceSufficiencyAssessment,
    rca_synthesis: RCASynthesisResult,
) -> List[RecommendationDraft]:
    """Deterministically assemble 100% grounded advisory recommendations without LLM calls."""
    classification = assessment.classification
    earliest_svc = assessment.earliest_failure_service or (
        incident.primary_log_source.service_name
        if incident.primary_log_source
        else "suspect-service"
    )
    other_svcs = [s for s in assessment.distinct_services if s != earliest_svc]
    caveat = _format_advisory_caveat(classification)

    drafts: List[RecommendationDraft] = []

    if classification == EvidenceSufficiencyClassification.INSUFFICIENT:
        # Recommendation 1: Telemetry Gap - collect missing trace & log signals
        gaps_str = (
            ", ".join(assessment.missing_signals)
            if assessment.missing_signals
            else "Distributed trace headers (W3C/traceparent) and correlated error logs"
        )
        drafts.append(
            RecommendationDraft(
                action_type="TELEMETRY_GAP",
                title="Collect missing distributed tracing and telemetry signals",
                description=(
                    f"Available telemetry is insufficient to substantiate a primary root cause hypothesis. "
                    f"Observed gaps: {gaps_str}. Grounded observation: no correlated trace propagation was found."
                ),
                priority="HIGH",
                sequence_order=0,
                verification_step=(
                    "Verify log ingestion pipelines and ensure distributed trace context headers "
                    "are propagated across all ingress controllers and inter-service HTTP/gRPC clients."
                ),
                caveat=caveat,
            )
        )
        # Recommendation 2: Diagnostic Verification - check baseline metrics
        svcs_str = (
            ", ".join(assessment.distinct_services)
            if assessment.distinct_services
            else earliest_svc
        )
        drafts.append(
            RecommendationDraft(
                action_type="DIAGNOSTIC_VERIFICATION",
                title=f"Review infrastructure metrics across observed services ({svcs_str})",
                description=(
                    f"Inspect baseline telemetry (CPU utilization, memory saturation, container restarts, connection pools) "
                    f"for observed service(s) around the incident initiation window."
                ),
                priority="MEDIUM",
                sequence_order=1,
                verification_step=(
                    "Inspect metrics dashboards and APM monitoring for memory leaks, connection starvation, or thread pool exhaustion."
                ),
                caveat=caveat,
            )
        )

    elif classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY:
        # Recommendation 1: Diagnostic Verification - investigate earliest failure origin
        ts_str = (
            assessment.earliest_failure_timestamp.isoformat()
            if assessment.earliest_failure_timestamp
            else "earliest error window"
        )
        earliest_doc_id = None
        for ev in incident.evidence or []:
            if ev.service_name == earliest_svc and (ev.opensearch_doc_id or str(ev.id)):
                earliest_doc_id = ev.opensearch_doc_id or str(ev.id)
                break

        rec1_citations: List[Dict[str, Any]] = []
        if earliest_doc_id:
            rec1_citations.append({
                "reference_type": "LOG",
                "reference_id": earliest_doc_id,
                "service_name": earliest_svc,
            })

        drafts.append(
            RecommendationDraft(
                action_type="DIAGNOSTIC_VERIFICATION",
                title=f"Inspect error surge initiation in service '{earliest_svc}'",
                description=(
                    f"Earliest observed failure occurred in service '{earliest_svc}' around {ts_str}. "
                    f"Temporal proximity suggests possible initiation, but temporal correlation does not confirm causality without shared trace context."
                ),
                priority="HIGH",
                sequence_order=0,
                verification_step=(
                    f"Inspect raw application logs and internal stack traces for '{earliest_svc}' "
                    f"prior to {ts_str} to identify localized exceptions or timeouts."
                ),
                caveat=caveat,
                evidence_citations=rec1_citations,
            )
        )
        # Recommendation 2: Telemetry Gap - enable tracing between co-occurring services
        downstream_str = ", ".join(other_svcs) if other_svcs else "downstream services"
        drafts.append(
            RecommendationDraft(
                action_type="TELEMETRY_GAP",
                title="Instrument distributed tracing between co-occurring services",
                description=(
                    f"Services '{earliest_svc}' and {downstream_str} exhibited temporal co-occurrence without shared trace IDs. "
                    f"Instrument OpenTelemetry context propagation to verify or refute cascade propagation."
                ),
                priority="MEDIUM",
                sequence_order=1,
                verification_step=(
                    "Validate trace-context propagation headers on inter-service HTTP client calls."
                ),
                caveat=caveat,
                evidence_citations=[],
            )
        )
        # Recommendation 3: Mitigation Advisory - review caller resilience
        drafts.append(
            RecommendationDraft(
                action_type="MITIGATION_ADVISORY",
                title=f"Review circuit breaker and timeout policies for callers of '{earliest_svc}'",
                description=(
                    f"Advisory check: ensure downstream callers ({downstream_str}) have resilient timeouts and circuit breakers "
                    f"to prevent cascade amplification if '{earliest_svc}' experiences transient delays."
                ),
                priority="MEDIUM",
                sequence_order=2,
                verification_step=(
                    "Audit client retry limits, exponential backoff policies, and circuit breaker trip thresholds."
                ),
                caveat=caveat,
                evidence_citations=[],
            )
        )

    else:  # TRACE_LINKED
        shared_tid = (
            assessment.shared_trace_ids[0]
            if assessment.shared_trace_ids
            else "shared-trace"
        )
        # Find sample doc id for earliest service if present
        earliest_doc_id = None
        for ev in incident.evidence or []:
            if ev.service_name == earliest_svc and (ev.opensearch_doc_id or str(ev.id)):
                earliest_doc_id = ev.opensearch_doc_id or str(ev.id)
                break

        # Recommendation 1: Diagnostic Verification - examine execution path along trace
        doc_mention = f" (Evidence doc: {earliest_doc_id})" if earliest_doc_id else ""
        rec1_citations = [
            {
                "reference_type": "TRACE",
                "reference_id": shared_tid,
                "service_name": earliest_svc,
            }
        ]
        if earliest_doc_id:
            rec1_citations.append({
                "reference_type": "LOG",
                "reference_id": earliest_doc_id,
                "service_name": earliest_svc,
            })

        drafts.append(
            RecommendationDraft(
                action_type="DIAGNOSTIC_VERIFICATION",
                title=f"Investigate execution failure in '{earliest_svc}' along trace '{shared_tid}'",
                description=(
                    f"Shared distributed trace context '{shared_tid}' connects failures across services. "
                    f"Earliest observed failure was recorded in service '{earliest_svc}'{doc_mention}. "
                    f"Examine localized stack traces and execution bottlenecks along this transaction path."
                ),
                priority="CRITICAL",
                sequence_order=0,
                verification_step=(
                    f"Review detailed APM spans and error logs for trace '{shared_tid}' in '{earliest_svc}'."
                ),
                caveat=caveat,
                evidence_citations=rec1_citations,
            )
        )
        # Recommendation 2: Mitigation Advisory - non-destructive traffic isolation / throttling
        downstream_str = ", ".join(other_svcs) if other_svcs else "downstream services"
        rec2_citations = [
            {
                "reference_type": "TRACE",
                "reference_id": shared_tid,
                "service_name": svc,
            }
            for svc in other_svcs
        ]
        drafts.append(
            RecommendationDraft(
                action_type="MITIGATION_ADVISORY",
                title=f"Evaluate traffic throttling or connection limits for {downstream_str}",
                description=(
                    f"Requests sharing trace '{shared_tid}' propagated failures to {downstream_str}. "
                    f"Evaluate non-destructive traffic throttling or queue rate-limiting to protect downstream capacity."
                ),
                priority="HIGH",
                sequence_order=1,
                verification_step=(
                    "Check queue saturation and consumer processing latencies before adjusting traffic limits."
                ),
                caveat=caveat,
                evidence_citations=rec2_citations,
            )
        )
        # Recommendation 3: Immediate Action (Advisory & Non-destructive validation)
        rec3_citations = []
        if earliest_doc_id:
            rec3_citations.append({
                "reference_type": "LOG",
                "reference_id": earliest_doc_id,
                "service_name": earliest_svc,
            })
        drafts.append(
            RecommendationDraft(
                action_type="IMMEDIATE_ACTION",
                title=f"Validate upstream dependency and database health for '{earliest_svc}'",
                description=(
                    f"Perform non-destructive health checks against database connection pools and upstream APIs used by '{earliest_svc}'. "
                    f"Upstream database latency or lock contention frequently initiates cascades that manifest in '{earliest_svc}'."
                ),
                priority="MEDIUM",
                sequence_order=2,
                verification_step=(
                    f"Execute synthetic health queries and inspect database connection pool metrics for '{earliest_svc}'."
                ),
                caveat=caveat,
                evidence_citations=rec3_citations,
            )
        )

    return drafts


def validate_llm_recommendations(
    raw_items: Any,
    incident: Incident,
    assessment: EvidenceSufficiencyAssessment,
) -> Optional[List[RecommendationDraft]]:
    """Strictly validate untrusted LLM recommendations against incident ground-truth telemetry.

    Returns:
        List of validated RecommendationDrafts if ALL items pass validation;
        None if ANY item fails validation (triggering all-or-nothing fallback).
    """
    if not isinstance(raw_items, list) or not raw_items:
        return None

    classification = assessment.classification
    allowed_action_types = CLASSIFICATION_ACTION_TYPE_ALLOWLIST.get(classification, set())

    # Build exact incident-scoped entity record mappings for isolated namespace validation
    log_record_map: Dict[str, str] = {}  # doc_id -> service_name
    for ev in incident.evidence or []:
        doc_id = ev.opensearch_doc_id or str(ev.id)
        if doc_id and ev.service_name:
            log_record_map[doc_id] = ev.service_name

    trace_record_map: Dict[str, Set[str]] = {}  # trace_id -> set of associated services
    for tid in assessment.shared_trace_ids or []:
        if tid not in trace_record_map:
            trace_record_map[tid] = set()

    for ev in incident.evidence or []:
        meta_tid = (ev.metadata_ or {}).get("trace_id")
        if meta_tid and ev.service_name:
            trace_record_map.setdefault(meta_tid, set()).add(ev.service_name)

    for event in incident.events or []:
        ev_tid = (event.attributes or {}).get("trace_id")
        if ev_tid and event.service_name:
            trace_record_map.setdefault(ev_tid, set()).add(event.service_name)

    span_record_map: Dict[str, str] = {}  # span_id -> service_name
    for event in incident.events or []:
        sp_id = (event.attributes or {}).get("span_id")
        if sp_id and event.service_name:
            span_record_map[sp_id] = event.service_name

    valid_services: Set[str] = set()
    for s in incident.affected_services or []:
        valid_services.add(s.service_name)
    if incident.primary_log_source and incident.primary_log_source.service_name:
        valid_services.add(incident.primary_log_source.service_name)
    for s in assessment.distinct_services or []:
        valid_services.add(s)

    validated_drafts: List[RecommendationDraft] = []
    caveat = _format_advisory_caveat(classification)

    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            return None

        title = str(item.get("title", "")).strip()
        description = str(item.get("description", "")).strip()
        action_type = str(item.get("action_type", "")).strip()
        priority = str(item.get("priority", "MEDIUM")).strip().upper()
        verification_step = item.get("verification_step")
        if verification_step is not None:
            verification_step = str(verification_step).strip() or None
        citations = item.get("evidence_references", [])

        # 1. Bounds and presence
        if not title or len(title) > 255:
            return None
        if not description:
            return None
        if priority not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            return None

        # 2. Strict classification allow-list validation for action_type
        if action_type not in allowed_action_types:
            logger.warning(
                "LLM recommendation rejected: action_type '%s' not allowed for classification '%s'",
                action_type,
                classification.value,
            )
            return None

        # 3. Check for prohibited claims of confirmed causation or proven root cause
        if (
            _check_unsupported_causal_claims(title)
            or _check_unsupported_causal_claims(description)
            or (verification_step and _check_unsupported_causal_claims(verification_step))
        ):
            logger.warning("LLM recommendation rejected: contains unsupported claim of confirmed causation.")
            return None

        # 4. Strict citation validation against isolated namespaces and exact record ownership
        validated_citations = validate_and_resolve_citations(
            citations,
            incident=incident,
            assessment=assessment,
        )
        if validated_citations is None:
            logger.warning("LLM recommendation rejected: contains invalid or ungrounded evidence citation.")
            return None

        validated_drafts.append(
            RecommendationDraft(
                action_type=action_type,
                title=title,
                description=description,
                priority=priority,
                sequence_order=idx,
                verification_step=verification_step,
                caveat=caveat,
                evidence_citations=validated_citations,
            )
        )

    return validated_drafts


async def _synthesize_validated_recommendations(
    incident: Incident,
    assessment: EvidenceSufficiencyAssessment,
    rca_synthesis: RCASynthesisResult,
    client: Optional[BaseLLMClient] = None,
) -> List[Recommendation]:
    """Synthesize validated Recommendation ORM instances using LLM with deterministic fallback."""
    now = datetime.now(timezone.utc)
    llm_drafts: Optional[List[RecommendationDraft]] = None

    llm_client = client or get_llm_client()

    # Attempt LLM generation if client is available
    if llm_client is not None:
        try:
            context = build_grounded_evidence_context(
                incident=incident,
                assessment=assessment,
                rca_synthesis=rca_synthesis,
                timeline_events=incident.events,
                evidence_items=incident.evidence,
            )

            prompt_instructions = (
                f"\n\nGenerate structured troubleshooting recommendations for incident '{incident.title}' as a JSON array.\n"
                f"Classification: {assessment.classification.value}\n"
                f"Allowed action_type values for this incident: {sorted(list(CLASSIFICATION_ACTION_TYPE_ALLOWLIST[assessment.classification]))}\n"
                f"CRITICAL RULES:\n"
                f"1. Zero autonomous execution: all recommendations must be strictly advisory and non-destructive.\n"
                f"2. Never confirm root cause: RootLens treats all correlations as hypotheses (is_root_cause_confirmed = False).\n"
                f"3. Do NOT make claims of confirmed causation or proven root cause.\n"
                f"4. For citations, use exact known log IDs, trace IDs, and services from the telemetry context in evidence_references.\n"
                f"5. Return ONLY a valid JSON array of objects with keys: action_type, title, description, priority, verification_step, evidence_references.\n"
            )

            req = LLMRequest(
                messages=[
                    LLMMessage(role="system", content=context.system_prompt),
                    LLMMessage(role="user", content=context.user_prompt + prompt_instructions),
                ],
                model=getattr(llm_client, "model_name", "rootlens-llm"),
                temperature=0.0,
            )

            resp = await llm_client.generate(req)
            content = resp.content.strip()

            # Strip markdown code blocks if present
            if content.startswith("```"):
                lines = content.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content = "\n".join(lines).strip()

            parsed_json = json.loads(content)
            llm_drafts = validate_llm_recommendations(
                raw_items=parsed_json,
                incident=incident,
                assessment=assessment,
            )
            if llm_drafts is None:
                logger.info("LLM recommendations failed validation; falling back to deterministic generator.")
        except Exception as exc:
            logger.info("LLM recommendation synthesis unavailable or failed (%s); using deterministic baseline.", exc)
            llm_drafts = None

    # Fall back to 100% grounded deterministic recommendations if LLM failed or was rejected
    final_drafts = llm_drafts or build_deterministic_recommendations(
        incident=incident,
        assessment=assessment,
        rca_synthesis=rca_synthesis,
    )

    orm_recommendations: List[Recommendation] = []
    for d in final_drafts:
        # Strict validation: Ensure any persisted citations resolve to real incident evidence
        raw_citations = getattr(d, "evidence_citations", []) or []
        citations = validate_and_resolve_citations(
            raw_citations,
            incident=incident,
            assessment=assessment,
        )
        if citations is None:
            logger.warning(
                "Recommendation '%s' contained unresolved citations; clearing invalid citations prior to persistence.",
                d.title,
            )
            citations = []

        rec = Recommendation(
            id=uuid.uuid4(),
            incident_id=incident.id,
            action_type=d.action_type,
            title=d.title,
            description=d.description,
            priority=d.priority,
            sequence_order=d.sequence_order,
            verification_step=d.verification_step,
            caveat=d.caveat,
            evidence_citations=citations,
            created_at=now,
            updated_at=now,
        )
        orm_recommendations.append(rec)

    return orm_recommendations


async def generate_incident_recommendations(
    session: AsyncSession,
    incident_id: uuid.UUID,
    req: Optional[RecommendationGenerateRequest] = None,
    client: Optional[BaseLLMClient] = None,
) -> Tuple[List[Recommendation], bool]:
    """Generate, persist, or idempotently retrieve advisory troubleshooting recommendations.

    Returns:
        Tuple of (List[Recommendation], is_created), where is_created is True if new
        recommendations were inserted/replaced, or False if existing recommendations were returned.

    Raises:
        HTTPException(404) if incident does not exist.
    """
    force_regenerate = req.force_regenerate if req else False

    try:
        # 1. Acquire exclusive row lock on parent Incident; serializes concurrent requests in PostgreSQL
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
        res = await session.execute(stmt)
        incident = res.scalar_one_or_none()
        if not incident:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Incident '{incident_id}' not found.",
            )

        # 2. Check existing recommendations under parent row lock
        rec_query = (
            select(Recommendation)
            .where(Recommendation.incident_id == incident_id)
            .order_by(Recommendation.sequence_order.asc())
        )
        existing_recs = list((await session.execute(rec_query)).scalars().all())

        # 3. Idempotent short-circuit: return existing records if not forcing regeneration
        if existing_recs and not force_regenerate:
            return existing_recs, False

        # 4. Deterministic sufficiency assessment and RCA synthesis
        timeline_events = incident.events or []
        evidence_items = incident.evidence or []
        assessment = evaluate_evidence_sufficiency(
            evidence_items=evidence_items,
            timeline_events=timeline_events,
        )
        rca_synthesis = synthesize_rca_findings(
            assessment=assessment,
            evidence_items=evidence_items,
            timeline_events=timeline_events,
        )

        # 5. Synthesize recommendations
        new_recommendation_models = await _synthesize_validated_recommendations(
            incident=incident,
            assessment=assessment,
            rca_synthesis=rca_synthesis,
            client=client,
        )

        # 6. Atomic replacement under row lock
        if existing_recs:
            await session.execute(
                delete(Recommendation).where(Recommendation.incident_id == incident_id)
            )

        session.add_all(new_recommendation_models)
        await session.flush()
        await session.commit()

        # Re-fetch committed records for consistent serialization
        committed_recs = list((await session.execute(rec_query)).scalars().all())
        return committed_recs, True

    except Exception:
        await session.rollback()
        raise


async def get_incident_recommendations(
    session: AsyncSession,
    incident_id: uuid.UUID,
) -> List[Recommendation]:
    """Retrieve all recommendations for an incident ordered by sequence_order ASC."""
    stmt = (
        select(Recommendation)
        .where(Recommendation.incident_id == incident_id)
        .order_by(Recommendation.sequence_order.asc())
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_recommendation_by_id(
    session: AsyncSession,
    incident_id: uuid.UUID,
    recommendation_id: uuid.UUID,
) -> Optional[Recommendation]:
    """Retrieve a single recommendation, strictly enforcing incident ownership."""
    stmt = select(Recommendation).where(
        Recommendation.id == recommendation_id,
        Recommendation.incident_id == incident_id,
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()
