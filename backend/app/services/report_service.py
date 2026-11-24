"""RootLens Evidence-Grounded Post-Mortem Report Service.

Provides deterministic post-mortem report generation, idempotency handling,
database persistence, and retrieval for incident investigations.

Safety Invariants:
- Newly generated reports always start in status 'DRAFT'.
- is_root_cause_confirmed is strictly False (correlation is not confirmed causation).
- Confidence score is strictly 0.0 (no invented confidence percentages).
- Never invents metadata, trace IDs, log IDs, citations, or metrics.
- Respects prompt budget boundaries and discloses unreviewed telemetry omissions.
- Recommendations remain advisory diagnostic guidance; zero autonomous remediation.
"""

from datetime import datetime, timezone
import logging
from typing import Any, List, Optional, Tuple
import uuid

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.incident import Incident
from app.models.report import Report
from app.schemas.report import ReportCreateRequest
from app.services.evidence_analysis_service import evaluate_evidence_sufficiency
from app.services.incident_persistence_service import get_incident_by_id
from app.services.llm_explanation_service import (
    BaseLLMClient,
    GroundedExplanationResult,
    generate_grounded_explanation,
)
from app.services.rca_synthesis_service import synthesize_rca_findings

logger = logging.getLogger("rootlens.reports")


def _format_executive_summary(
    incident: Incident,
    assessment: Any,
    rca_synthesis: Any,
) -> str:
    """Format high-level executive summary section for post-mortem report."""
    primary_svc = (
        incident.primary_log_source.service_name
        if incident.primary_log_source
        else "Unidentified"
    )

    affected_list = []
    for svc in incident.affected_services or []:
        impact = getattr(svc, "impact_level", "AFFECTED")
        name = getattr(svc, "service_name", "unknown")
        affected_list.append(f"{name} ({impact})")
    affected_str = ", ".join(affected_list) if affected_list else "None recorded"

    started_str = incident.started_at.isoformat() if incident.started_at else "Unknown"
    ended_str = incident.ended_at.isoformat() if incident.ended_at else "Ongoing / Not resolved"
    detected_str = incident.detected_at.isoformat() if incident.detected_at else "Unknown"

    class_val = (
        assessment.classification.value
        if hasattr(assessment.classification, "value")
        else str(assessment.classification)
    )

    lines = [
        f"Incident Title: {incident.title}",
        f"Incident ID: {incident.id}",
        f"Severity: {incident.severity}",
        f"Operational Lifecycle Status: {incident.status}",
        f"Telemetry Analysis Classification: {class_val}",
        f"Primary Suspect Service: {primary_svc}",
        f"Blast Radius Affected Services: {affected_str}",
        f"Timeline Window: Started {started_str} | Ended {ended_str} | Registered {detected_str}",
        "",
        "Overview:",
        str(rca_synthesis.summary or "Automated telemetry assessment completed under standard diagnostic constraints."),
    ]
    return "\n".join(lines)


def _format_timeline_summary(
    incident: Incident,
    explanation_res: GroundedExplanationResult,
) -> str:
    """Format chronological timeline milestone narrative with omission disclosure."""
    lines = []
    events = incident.events or []
    events_sorted = sorted(
        events,
        key=lambda e: (
            getattr(e, "sequence_order", 0) or 0,
            getattr(e, "event_timestamp", datetime.min.replace(tzinfo=timezone.utc)),
        ),
    )

    if events_sorted:
        for ev in events_sorted:
            ts_val = getattr(ev, "event_timestamp", None)
            ts_str = ts_val.isoformat() if ts_val else "N/A"
            svc = getattr(ev, "service_name", "unknown")
            etype = getattr(ev, "event_type", "EVENT")
            summary = getattr(ev, "summary", "")
            trace_id = (getattr(ev, "attributes", {}) or {}).get("trace_id")
            tr_str = f" [trace_id: {trace_id}]" if trace_id else ""
            lines.append(f"- [{ts_str}] [{svc}] {etype}: {summary}{tr_str}")
    else:
        lines.append("- No chronological milestone events recorded for this incident.")

    # Disclose prompt budget omissions if timeline was bounded during LLM review
    ctx = explanation_res.context
    if ctx.omitted_timeline_events_count > 0:
        lines.append("")
        lines.append(
            f"[BUDGET DISCLOSURE: Displaying full {ctx.total_timeline_events_count} timeline events above. "
            f"The LLM diagnostic prompt was bounded to the earliest {ctx.total_timeline_events_count - ctx.omitted_timeline_events_count} events; "
            f"{ctx.omitted_timeline_events_count} events were omitted from the prompt body to respect context budget limits. "
            f"Omitted events were NOT reviewed by the LLM.]"
        )

    return "\n".join(lines)


def _format_root_cause_analysis(
    assessment: Any,
    rca_synthesis: Any,
    explanation_res: GroundedExplanationResult,
) -> str:
    """Format evidence-backed findings and grounded diagnostic narrative."""
    lines = [
        "### Grounded Diagnostic Explanation",
        explanation_res.explanation.strip(),
        "",
        "### Evaluated Hypotheses & Diagnostic Boundaries",
        f"Primary Candidate Hypothesis: {rca_synthesis.primary_hypothesis or 'None (Evidence Insufficient)'}",
        "",
        "Observed Factual Findings:",
    ]

    for fact in assessment.observed_facts or []:
        lines.append(f"- {fact}")

    if assessment.supporting_signals:
        lines.append("")
        lines.append("Supporting Diagnostic Signals:")
        for sig in assessment.supporting_signals:
            lines.append(f"- {sig}")

    # Gather contradictory signals from synthesis findings
    contradictory_signals = []
    for finding in getattr(rca_synthesis, "findings", []) or []:
        for sig in getattr(finding, "contradictory_signals", []) or []:
            if sig not in contradictory_signals:
                contradictory_signals.append(sig)

    if contradictory_signals:
        lines.append("")
        lines.append("Contradictory / Discrepant Signals:")
        for sig in contradictory_signals:
            lines.append(f"- {sig}")

    lines.extend([
        "",
        "Confirmation Status & Bounds:",
        "- Root Cause Confirmed: False (Strict invariant: RootLens treats all correlations as hypotheses; correlation does not establish causation).",
        "- Diagnostic Confidence Score: 0.0 (Strict invariant: RootLens does not invent confidence percentages).",
    ])

    return "\n".join(lines)


def _format_recommendations_summary(
    assessment: Any,
    rca_synthesis: Any,
) -> str:
    """Format advisory recommendations and missing telemetry verification steps."""
    lines = [
        "Advisory Investigation & Verification Steps:",
    ]

    if assessment.missing_signals:
        lines.append("")
        lines.append("Telemetry Gaps to Address:")
        for sig in assessment.missing_signals:
            lines.append(f"- Collect missing signal: {sig}")

    # Pull candidate finding verification steps
    verification_steps = []
    for finding in getattr(rca_synthesis, "findings", []) or []:
        for step in getattr(finding, "missing_telemetry", []) or []:
            if step not in verification_steps:
                verification_steps.append(step)

    if verification_steps:
        lines.append("")
        lines.append("Targeted Verification Recommendations:")
        for step in verification_steps:
            lines.append(f"- {step}")

    lines.extend([
        "",
        "Advisory Guidance Only:",
        "All recommendations are non-autonomous diagnostic recommendations for engineering review. "
        "RootLens performs zero autonomous remediation or modification of infrastructure.",
    ])

    return "\n".join(lines)


def _format_uncertainty_disclaimer(
    assessment: Any,
    explanation_res: GroundedExplanationResult,
) -> str:
    """Format explicit diagnostic uncertainty and prompt-budget omission disclosures."""
    lines = [
        f"Diagnostic Uncertainty Statement: {assessment.uncertainty_statement}",
    ]

    ctx = explanation_res.context
    has_omissions = (
        ctx.omitted_evidence_records_count > 0
        or ctx.omitted_references_count > 0
        or ctx.omitted_timeline_events_count > 0
        or ctx.has_truncated_snippets
    )

    if has_omissions:
        lines.append("")
        lines.append("Prompt Budget & Unreviewed Telemetry Disclosure:")
        lines.append(
            f"- Bounded telemetry budget limits applied during analysis. "
            f"Omitted from LLM prompt: {ctx.omitted_evidence_records_count} evidence records, "
            f"{ctx.omitted_timeline_events_count} timeline events, "
            f"{ctx.omitted_references_count} provenance references. "
            f"The LLM has NOT reviewed any omitted telemetry. "
            f"Do NOT infer or claim that unreviewed telemetry supports any conclusion. "
            f"All available evidence references remain preserved in RootLens persistent storage."
        )

    return "\n".join(lines)


async def generate_postmortem_report(
    session: AsyncSession,
    incident_id: uuid.UUID,
    req: Optional[ReportCreateRequest] = None,
    client: Optional[BaseLLMClient] = None,
) -> Tuple[Report, bool]:
    """Generate, persist, or idempotently update an evidence-grounded post-mortem report.

    Returns:
        Tuple of (Report, is_created), where is_created is True if a new report was inserted,
        or False if an existing report with matching (incident_id, title) was updated in-place.

    Raises:
        HTTPException(404) if incident does not exist.
    """
    # 1. Validate and load incident with full relations
    incident = await get_incident_by_id(session, incident_id)
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found.",
        )

    # 2. Determine deterministic report title
    target_title = (
        req.title.strip()
        if req and req.title and req.title.strip()
        else f"Post-Mortem: {incident.title}"
    )[:255]

    # 3. Extract telemetry collections
    timeline_events = incident.events or []
    evidence_items = incident.evidence or []

    # 4. Deterministic sufficiency assessment and RCA synthesis
    assessment = evaluate_evidence_sufficiency(
        evidence_items=evidence_items,
        timeline_events=timeline_events,
    )
    rca_synthesis = synthesize_rca_findings(
        assessment=assessment,
        evidence_items=evidence_items,
        timeline_events=timeline_events,
    )

    # 5. Call grounded explanation engine
    explanation_res = await generate_grounded_explanation(
        incident=incident,
        assessment=assessment,
        rca_synthesis=rca_synthesis,
        timeline_events=timeline_events,
        evidence_items=evidence_items,
        client=client,
    )

    # 6. Populate structured report sections
    executive_summary = _format_executive_summary(incident, assessment, rca_synthesis)
    timeline_summary = _format_timeline_summary(incident, explanation_res)
    root_cause_analysis = _format_root_cause_analysis(assessment, rca_synthesis, explanation_res)
    recommendations_summary = _format_recommendations_summary(assessment, rca_synthesis)
    uncertainty_disclaimer = _format_uncertainty_disclaimer(assessment, explanation_res)

    now = datetime.now(timezone.utc)

    # 7. Check for existing report to honor uq_reports_incident_title
    stmt = select(Report).where(
        Report.incident_id == incident.id,
        Report.title == target_title,
    )
    res = await session.execute(stmt)
    existing_report = res.scalar_one_or_none()

    if existing_report:
        existing_report.status = "DRAFT"  # Invariant: reports start/regenerate in DRAFT
        existing_report.executive_summary = executive_summary
        existing_report.timeline_summary = timeline_summary
        existing_report.root_cause_analysis = root_cause_analysis
        existing_report.recommendations_summary = recommendations_summary
        existing_report.uncertainty_disclaimer = uncertainty_disclaimer
        existing_report.generated_at = now
        existing_report.updated_at = now
        report = existing_report
        is_created = False
    else:
        report = Report(
            incident_id=incident.id,
            title=target_title,
            status="DRAFT",
            executive_summary=executive_summary,
            timeline_summary=timeline_summary,
            root_cause_analysis=root_cause_analysis,
            recommendations_summary=recommendations_summary,
            uncertainty_disclaimer=uncertainty_disclaimer,
            generated_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(report)
        is_created = True

    # 8. Transaction commit with rollback safety
    try:
        await session.commit()
        await session.refresh(report)
    except Exception as exc:
        await session.rollback()
        logger.error(f"Failed to persist report for incident {incident_id}: {exc}")
        raise

    return report, is_created


async def get_incident_reports(
    session: AsyncSession,
    incident_id: uuid.UUID,
) -> List[Report]:
    """Retrieve all reports associated with an incident ordered chronologically descending."""
    stmt = (
        select(Report)
        .where(Report.incident_id == incident_id)
        .order_by(desc(Report.generated_at))
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_report_by_id(
    session: AsyncSession,
    incident_id: uuid.UUID,
    report_id: uuid.UUID,
) -> Optional[Report]:
    """Retrieve an individual report verifying it strictly belongs to the specified incident."""
    stmt = select(Report).where(
        Report.id == report_id,
        Report.incident_id == incident_id,
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()
