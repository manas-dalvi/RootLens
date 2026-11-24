"""Operator hypothesis confirmation service for RootLens.

Manages human-in-the-loop review, confirmation, and rejection of AI-generated
RCA hypotheses, maintaining an immutable audit history and strictly enforcing
evidence grounding invariants before confirmation.

Multi-hypothesis state invariant:
- Incident confirmation state is derived directly from the set of active decisions
  across all hypotheses.
- Confirming Hypothesis A while rejecting Hypothesis B leaves Hypothesis A confirmed.
- Row-level database locking (with_for_update) serializes concurrent operator decisions.
"""
import dataclasses
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import OperatorIdentity
from app.models.incident import Incident
from app.models.rca_hypothesis_decision import RCAHypothesisDecision
from app.schemas.incident import (
    HypothesisDecisionEnum,
    RCAHypothesisDecisionCreateRequest,
    RCAHypothesisDecisionResponse,
    RCAHypothesisDecisionsSummaryResponse,
)
from app.services.incident_persistence_service import map_incident_to_detail_response


def map_decision_model_to_response(
    decision: RCAHypothesisDecision,
) -> RCAHypothesisDecisionResponse:
    """Map SQLAlchemy RCAHypothesisDecision model to Pydantic response."""
    return RCAHypothesisDecisionResponse(
        id=str(decision.id),
        incident_id=str(decision.incident_id),
        hypothesis_id=decision.hypothesis_id,
        hypothesis_text=decision.hypothesis_text,
        operator_id=decision.operator_id,
        operator_name=decision.operator_name,
        operator_email=decision.operator_email,
        decision=decision.decision,
        notes=decision.notes,
        evidence_citations=decision.evidence_citations or [],
        previous_decision=decision.previous_decision,
        decided_at=decision.decided_at,
        created_at=decision.created_at,
    )


def recalculate_incident_confirmation_state(
    incident: Incident,
    all_decisions: List[RCAHypothesisDecision],
) -> None:
    """Derive the incident-level confirmation state strictly from active hypothesis decisions.

    Rules:
    1. The active decision for any hypothesis is its latest decision (ordered by decided_at DESC).
    2. If ANY hypothesis has an active decision of CONFIRM, is_root_cause_confirmed = True.
    3. The incident's probable_cause is set to the hypothesis_text of the most recently confirmed
       active hypothesis.
    4. If NO hypothesis has an active decision of CONFIRM, is_root_cause_confirmed = False.
    """
    # Group by hypothesis_id to determine active decision (all_decisions ordered newest first)
    active_by_hyp: Dict[str, RCAHypothesisDecision] = {}
    for d in all_decisions:
        if d.hypothesis_id not in active_by_hyp:
            active_by_hyp[d.hypothesis_id] = d

    # Collect all actively confirmed hypotheses (sorted newest first with stable secondary key)
    active_confirmed = [
        d for d in active_by_hyp.values()
        if d.decision == "CONFIRM"
    ]
    active_confirmed.sort(key=lambda d: (d.decided_at, str(d.id)), reverse=True)

    if active_confirmed:
        latest = active_confirmed[0]
        incident.is_root_cause_confirmed = True
        incident.probable_cause = latest.hypothesis_text
        incident.uncertainty_reason = (
            f"Confirmed by operator {latest.operator_name} ({latest.operator_email}): {latest.notes}"
        )
    else:
        incident.is_root_cause_confirmed = False
        active_rejections = [
            d for d in active_by_hyp.values()
            if d.decision == "REJECT"
        ]
        if active_rejections:
            active_rejections.sort(key=lambda d: (d.decided_at, str(d.id)), reverse=True)
            latest_rej = active_rejections[0]
            incident.uncertainty_reason = (
                f"No hypothesis confirmed. Latest review rejected '{latest_rej.hypothesis_id}' "
                f"by operator {latest_rej.operator_name} ({latest_rej.operator_email}): {latest_rej.notes}"
            )
        else:
            incident.uncertainty_reason = "Diagnostic assessment pending human review; no hypothesis confirmed."


async def _get_incident_with_relations(
    session: AsyncSession, incident_id: uuid.UUID, for_update: bool = False
) -> Optional[Incident]:
    """Fetch incident with all relationships needed for RCA synthesis and concurrency locking."""
    stmt = (
        select(Incident)
        .where(Incident.id == incident_id)
        .options(
            selectinload(Incident.primary_log_source),
            selectinload(Incident.affected_services),
            selectinload(Incident.events),
            selectinload(Incident.evidence),
            selectinload(Incident.hypothesis_decisions),
        )
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _serialize_citation_to_dict(cit: Any) -> Dict[str, Any]:
    """Serialize a citation item (dataclass, Pydantic, or dict) to JSONB-safe format with ISO-8601 strings."""
    data: Dict[str, Any]
    if hasattr(cit, "model_dump"):
        data = cit.model_dump()
    elif dataclasses.is_dataclass(cit) and not isinstance(cit, type):
        data = dataclasses.asdict(cit)
    elif isinstance(cit, dict):
        data = {str(k): v for k, v in cit.items()}
    elif hasattr(cit, "__dict__"):
        data = dict(cit.__dict__)
    else:
        data = {}

    serialized: Dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, datetime):
            serialized[str(k)] = v.isoformat()
        else:
            serialized[str(k)] = v
    return serialized


async def record_hypothesis_decision(
    session: AsyncSession,
    incident_id: uuid.UUID,
    hypothesis_id: str,
    req: RCAHypothesisDecisionCreateRequest,
    operator: OperatorIdentity,
) -> RCAHypothesisDecisionResponse:
    """Record an immutable operator decision (CONFIRM or REJECT) on an RCA hypothesis.

    Strict Invariants:
    1. AI-generated hypotheses never become confirmed root causes automatically.
    2. Confirmation requires the hypothesis to be evidence-supported with valid citations.
    3. Unsupported speculations or ungrounded claims CANNOT be confirmed.
    4. Operator identity is derived strictly from authentication metadata.
    5. Append-only audit history is maintained.
    6. Concurrency safe: Incident row lock (with_for_update) serializes submissions.
    7. Multi-hypothesis consistent: rejecting one hypothesis does not disrupt other confirmed hypotheses.
    """
    # 1. Acquire transaction row lock on incident
    incident = await _get_incident_with_relations(session, incident_id, for_update=True)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    # 2. Synthesize incident RCA and extract candidate claims
    detail_resp = map_incident_to_detail_response(incident)
    rca_synth = (
        detail_resp.assessment.rca_synthesis
        if detail_resp.assessment and detail_resp.assessment.rca_synthesis
        else None
    )

    all_claims = rca_synth.claim_citations if rca_synth else []
    primary_hyp = rca_synth.primary_hypothesis if rca_synth else incident.probable_cause

    # Find matching claim or primary hypothesis
    target_claim = None
    normalized_target_id = hypothesis_id.strip()

    for claim in all_claims:
        if claim.claim_id == normalized_target_id:
            target_claim = claim
            break

    is_primary_alias = normalized_target_id.lower() in {
        "primary",
        "primary-hypothesis",
        "probable-cause",
        "primary_hypothesis",
    }

    if not target_claim and is_primary_alias:
        for claim in all_claims:
            if claim.claim_type == "INFERRED_HYPOTHESIS":
                target_claim = claim
                break

    if not target_claim and not is_primary_alias:
        prev_decision_exists = any(
            d.hypothesis_id == normalized_target_id for d in (incident.hypothesis_decisions or [])
        )
        if not prev_decision_exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Hypothesis '{hypothesis_id}' not found in RCA synthesis for incident '{incident_id}'.",
            )

    # Resolve hypothesis text
    resolved_text = (
        req.hypothesis_text
        or (target_claim.claim_text if target_claim else None)
        or (primary_hyp if is_primary_alias else None)
        or incident.probable_cause
        or "Hypothesis"
    ).strip()

    # Snapshot supporting citations
    citations_snapshot: List[Dict[str, Any]] = []
    if target_claim and target_claim.citations:
        citations_snapshot = [
            _serialize_citation_to_dict(cit)
            for cit in target_claim.citations
        ]
    elif is_primary_alias:
        for c in all_claims:
            if c.claim_type == "INFERRED_HYPOTHESIS" and c.citations:
                citations_snapshot.extend(
                    [
                        _serialize_citation_to_dict(cit)
                        for cit in c.citations
                    ]
                )

    # Strict Confirmation Safety Check
    if req.decision == HypothesisDecisionEnum.CONFIRM:
        if target_claim and not target_claim.is_supported:
            reason = (
                target_claim.unsupported_reason
                or "Hypothesis is unsupported speculation lacking verified telemetry citations."
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot confirm unsupported hypothesis '{normalized_target_id}'. "
                    f"Root causes must be grounded in verified evidence citations. Reason: {reason}"
                ),
            )
        if not citations_snapshot and not detail_resp.evidence_items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot confirm hypothesis '{normalized_target_id}' because incident '{incident_id}' "
                    "lacks verified supporting telemetry citations."
                ),
            )

    # Determine previous decision for this specific hypothesis
    stmt_prev = (
        select(RCAHypothesisDecision)
        .where(
            RCAHypothesisDecision.incident_id == incident_id,
            RCAHypothesisDecision.hypothesis_id == normalized_target_id,
        )
        .order_by(RCAHypothesisDecision.decided_at.desc())
    )
    result_prev = await session.execute(stmt_prev)
    latest_decision = result_prev.scalars().first()
    previous_decision_str = latest_decision.decision if latest_decision else None

    # Idempotency / Debounce check: if duplicate submission with exact same values
    if (
        latest_decision
        and latest_decision.decision == req.decision.value
        and latest_decision.notes == req.notes.strip()
        and latest_decision.operator_id == operator.operator_id
    ):
        return map_decision_model_to_response(latest_decision)

    now = datetime.now(timezone.utc)
    new_record = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident_id,
        hypothesis_id=normalized_target_id,
        hypothesis_text=resolved_text,
        operator_id=operator.operator_id,
        operator_name=operator.operator_name,
        operator_email=operator.operator_email,
        decision=req.decision.value,
        notes=req.notes.strip(),
        evidence_citations=citations_snapshot,
        previous_decision=previous_decision_str,
        decided_at=now,
        created_at=now,
    )
    session.add(new_record)
    await session.flush()

    # Query all decision records for this incident (including the new flushed record)
    stmt_all = (
        select(RCAHypothesisDecision)
        .where(RCAHypothesisDecision.incident_id == incident_id)
        .order_by(RCAHypothesisDecision.decided_at.desc())
    )
    res_all = await session.execute(stmt_all)
    all_decisions = list(res_all.scalars().all())

    # Atomically recalculate incident confirmation state across all active hypothesis decisions
    recalculate_incident_confirmation_state(
        incident=incident,
        all_decisions=all_decisions,
    )

    await session.commit()
    await session.refresh(new_record)
    await session.refresh(incident)

    return map_decision_model_to_response(new_record)


async def get_hypothesis_decisions_summary(
    session: AsyncSession, incident_id: uuid.UUID
) -> RCAHypothesisDecisionsSummaryResponse:
    """Retrieve the full decision history and active review status for an incident."""
    stmt_inc = select(Incident).where(Incident.id == incident_id)
    res_inc = await session.execute(stmt_inc)
    incident = res_inc.scalar_one_or_none()
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    stmt_dec = (
        select(RCAHypothesisDecision)
        .where(RCAHypothesisDecision.incident_id == incident_id)
        .order_by(RCAHypothesisDecision.decided_at.desc())
    )
    res_dec = await session.execute(stmt_dec)
    all_decisions = list(res_dec.scalars().all())

    active_decisions: Dict[str, RCAHypothesisDecisionResponse] = {}
    for d in all_decisions:
        resp = map_decision_model_to_response(d)
        if d.hypothesis_id not in active_decisions:
            active_decisions[d.hypothesis_id] = resp

    # Derive active confirmed hypothesis (newest first with stable secondary key)
    active_confirmed = [
        d for d in active_decisions.values()
        if d.decision == "CONFIRM"
    ]
    active_confirmed.sort(key=lambda d: (d.decided_at, str(d.id)), reverse=True)

    is_confirmed = len(active_confirmed) > 0
    confirmed_hyp_id: Optional[str] = active_confirmed[0].hypothesis_id if is_confirmed else None
    confirmed_text: Optional[str] = active_confirmed[0].hypothesis_text if is_confirmed else None

    history_responses = [map_decision_model_to_response(d) for d in all_decisions]

    return RCAHypothesisDecisionsSummaryResponse(
        incident_id=str(incident.id),
        is_root_cause_confirmed=is_confirmed,
        confirmed_root_cause=confirmed_text,
        confirmed_hypothesis_id=confirmed_hyp_id,
        active_decisions=active_decisions,
        history=history_responses,
    )
