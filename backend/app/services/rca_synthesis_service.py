"""Evidence-Backed RCA Synthesis Service for RootLens.

Pure in-memory synthesis layer converting deterministic evidence sufficiency
assessments and telemetry into explainable, strictly evidence-grounded
root cause analysis (RCA) candidate findings.

Safety Invariants:
- Never sets is_root_cause_confirmed = True.
- Never invents confidence scores, percentages, or probabilities.
- Never invents services, trace IDs, log IDs, or causal links.
- Purely deterministic and free of external API / LLM calls.
- Never mutates inputs.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.evidence_analysis_service import (
    EvidenceReference,
    EvidenceSufficiencyAssessment,
    EvidenceSufficiencyClassification,
    evaluate_evidence_sufficiency,
    _ensure_utc,
    _extract_evidence_item_fields,
    _extract_timeline_event_fields,
    _get_val,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RCACitationItem:
    """Fine-grained citation pointing to verified supporting evidence for an RCA claim."""

    log_id: str
    service_name: str
    evidence_type: str
    timestamp: Optional[datetime] = None
    evidence_id: Optional[str] = None
    log_snippet: Optional[str] = None
    support_explanation: str = ""
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    content_hash: Optional[str] = None


@dataclass(frozen=True)
class RCAClaimMapping:
    """Structured claim with fine-grained evidence citation mapping."""

    claim_id: str
    claim_text: str
    claim_type: str  # "OBSERVED_FACT" | "INFERRED_HYPOTHESIS" | "UNSUPPORTED_SPECULATION"
    is_supported: bool
    citations: List[RCACitationItem] = field(default_factory=list)
    unsupported_reason: Optional[str] = None


@dataclass(frozen=True)
class RCACandidateFinding:
    """A deterministic, evidence-grounded root cause explanation candidate."""

    explanation: str
    plausibility_rationale: str
    supporting_evidence_references: List[EvidenceReference] = field(default_factory=list)
    contradictory_evidence_references: List[EvidenceReference] = field(default_factory=list)
    contradictory_signals: List[str] = field(default_factory=list)
    missing_telemetry: List[str] = field(default_factory=list)
    limitations_and_uncertainty: str = ""
    is_root_cause_confirmed: bool = False  # Strictly False: correlation is not confirmed causation
    claim_citations: List[RCAClaimMapping] = field(default_factory=list)
    potential_root_cause_service: Optional[str] = None
    suspected_failure_mechanism: Optional[str] = None
    initial_observed_failure: Optional[Dict[str, Any]] = None
    downstream_symptoms: List[str] = field(default_factory=list)
    alternative_explanations: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RCASynthesisResult:
    """Overall structured RCA synthesis report containing evaluated candidate findings."""

    classification: EvidenceSufficiencyClassification
    is_root_cause_confirmed: bool = False  # Strictly False
    primary_hypothesis: Optional[str] = None  # None for INSUFFICIENT; unconfirmed hypothesis string otherwise
    findings: List[RCACandidateFinding] = field(default_factory=list)
    observed_facts: List[str] = field(default_factory=list)
    uncertainty_statement: str = ""
    summary: str = ""
    claim_citations: List[RCAClaimMapping] = field(default_factory=list)
    potential_root_cause_service: Optional[str] = None
    suspected_failure_mechanism: Optional[str] = None
    initial_observed_failure: Optional[Dict[str, Any]] = None
    downstream_symptoms: List[str] = field(default_factory=list)
    alternative_explanations: List[str] = field(default_factory=list)


def _ref_sort_key(ref: EvidenceReference) -> Tuple[datetime, str, str, str, str]:
    """Deterministic sort key for EvidenceReference objects."""
    ts = ref.timestamp or datetime.min.replace(tzinfo=timezone.utc)
    return (
        ts,
        ref.service_name or "",
        ref.log_id or "",
        ref.evidence_type or "",
        ref.correlation_rule or "",
    )


def validate_and_resolve_rca_citations(
    claims: List[RCAClaimMapping],
    evidence_items: Optional[List[Any]] = None,
    timeline_events: Optional[List[Any]] = None,
    expected_incident_id: Optional[str] = None,
) -> List[RCAClaimMapping]:
    """Validate and resolve fine-grained RCA claim citations against verified incident evidence.

    Strict Grounding Rules:
    - Never accept fabricated, missing, or unknown log record identifiers.
    - Validate that cited service_name exactly matches the service associated with that evidence record.
    - If expected_incident_id is provided, reject evidence items associated with another incident.
    - If a citation is invalid, fabricated, or carries a mismatched service:
        - The citation is rejected.
        - If any citation in the claim fails validation or if no valid citations remain,
          is_supported is set to False, citations are cleared, and unsupported_reason is populated explaining why.
    - If a claim has valid, grounded citations:
        - Verified citations are populated with canonical evidence_id, timestamps,
          exact log_snippet, service, and provenance hash.
        - is_supported is True.
    - For unsupported claims or speculation without citations:
        - is_supported is False.
        - unsupported_reason is preserved or set to an honest explanation.
    - Pure function: does not mutate inputs.
    """
    raw_evidence = evidence_items or []
    extracted_evidence = [_extract_evidence_item_fields(item) for item in raw_evidence]

    # Build primary evidence lookup index by all valid identifiers
    evidence_by_id: Dict[str, Tuple[Dict[str, Any], Any]] = {}
    for idx, item in enumerate(raw_evidence):
        fields = extracted_evidence[idx]
        lid = fields.get("log_id")
        if lid:
            evidence_by_id[lid] = (fields, item)
        # Also map opensearch_doc_id, id, etc.
        alt_ids = [
            _get_val(item, "opensearch_doc_id"),
            str(_get_val(item, "id", default="") or "") if _get_val(item, "id") is not None else None,
        ]
        for aid in alt_ids:
            if aid and str(aid).strip() and str(aid).strip() not in evidence_by_id:
                evidence_by_id[str(aid).strip()] = (fields, item)

    resolved_claims: List[RCAClaimMapping] = []

    for claim in claims:
        raw_citations = claim.citations or []
        validated_citations: List[RCACitationItem] = []
        rejection_reasons: List[str] = []

        for cit in raw_citations:
            cit_log_id = str(getattr(cit, "log_id", "") or getattr(cit, "evidence_id", "") or "").strip()
            cit_service = str(getattr(cit, "service_name", "") or "").strip()

            if not cit_log_id:
                rejection_reasons.append("Citation rejected: empty log_id or evidence_id.")
                continue

            if cit_log_id not in evidence_by_id:
                rejection_reasons.append(
                    f"Citation rejected: evidence ID '{cit_log_id}' not found in incident evidence."
                )
                continue

            ev_fields, raw_item = evidence_by_id[cit_log_id]

            # Cross-incident check
            ev_inc_id = ev_fields.get("incident_id") or str(_get_val(raw_item, "incident_id", default="") or "")
            if expected_incident_id and ev_inc_id:
                if str(ev_inc_id).strip() != str(expected_incident_id).strip():
                    rejection_reasons.append(
                        f"Citation rejected: evidence ID '{cit_log_id}' belongs to another incident ({ev_inc_id})."
                    )
                    continue

            # Service match check
            actual_service = ev_fields.get("service_name") or "unknown"
            if cit_service and actual_service != "unknown":
                if cit_service.lower() != actual_service.lower():
                    rejection_reasons.append(
                        f"Citation rejected: service mismatch for '{cit_log_id}'. Cited as '{cit_service}', but evidence record belongs to '{actual_service}'."
                    )
                    continue

            # Canonical resolution from verified evidence record
            resolved_cit = RCACitationItem(
                log_id=ev_fields.get("log_id") or cit_log_id,
                service_name=actual_service if actual_service != "unknown" else cit_service,
                evidence_type=ev_fields.get("evidence_type") or getattr(cit, "evidence_type", "EVIDENCE"),
                timestamp=ev_fields.get("timestamp") or getattr(cit, "timestamp", None),
                evidence_id=getattr(cit, "evidence_id", None) or ev_fields.get("log_id"),
                log_snippet=ev_fields.get("log_snippet") or getattr(cit, "log_snippet", None),
                support_explanation=getattr(cit, "support_explanation", "")
                or f"Corroborating telemetry evidence in service '{actual_service}'.",
                trace_id=ev_fields.get("trace_id") or getattr(cit, "trace_id", None),
                span_id=ev_fields.get("span_id") or getattr(cit, "span_id", None),
                content_hash=ev_fields.get("content_hash") or getattr(cit, "content_hash", None),
            )
            validated_citations.append(resolved_cit)

        # Grounding decision
        if raw_citations:
            if rejection_reasons:
                is_supported = len(validated_citations) > 0 and len(rejection_reasons) == 0
                unsupported_reason = "; ".join(rejection_reasons) if not is_supported else None
                final_citations = validated_citations if is_supported else []
            else:
                is_supported = True
                unsupported_reason = None
                final_citations = validated_citations
        else:
            if claim.claim_type == "OBSERVED_FACT":
                is_supported = False
                unsupported_reason = claim.unsupported_reason or "Empirical fact lacks direct supporting telemetry citation."
            elif claim.claim_type == "UNSUPPORTED_SPECULATION":
                is_supported = False
                unsupported_reason = claim.unsupported_reason or "Hypothesis lacks corroborating telemetry in incident evidence."
            else:
                is_supported = False
                unsupported_reason = claim.unsupported_reason or "Claim has insufficient supporting telemetry."
            final_citations = []

        resolved_claims.append(
            RCAClaimMapping(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                claim_type=claim.claim_type,
                is_supported=is_supported,
                citations=final_citations,
                unsupported_reason=unsupported_reason,
            )
        )

    return resolved_claims


def synthesize_rca_findings(
    assessment: Optional[EvidenceSufficiencyAssessment] = None,
    evidence_items: Optional[List[Any]] = None,
    timeline_events: Optional[List[Any]] = None,
) -> RCASynthesisResult:
    """Deterministically synthesize explainable RCA candidate findings from evidence telemetry.

    Pure in-memory function:
    - Grounded strictly in available telemetry and evidence sufficiency assessment.
    - Preserves exact source log IDs, service names, and provenance hashes.
    - Differentiates candidate hypotheses, supporting signals, contradictory telemetry, and gaps.
    - Strictly preserves is_root_cause_confirmed = False.
    - Never generates arbitrary confidence scores or percentages.
    """
    raw_evidence = evidence_items or []
    raw_timeline = timeline_events or []

    # 1. Obtain or compute the deterministic evidence sufficiency assessment
    if assessment is None:
        assessment = evaluate_evidence_sufficiency(
            evidence_items=raw_evidence,
            timeline_events=raw_timeline,
        )

    # 2. Extract telemetry without mutating inputs
    extracted_evidence = [_extract_evidence_item_fields(item) for item in raw_evidence]
    extracted_timeline = [_extract_timeline_event_fields(ev) for ev in raw_timeline]

    # Map all available references by log_id for exact provenance lookup
    ref_by_log_id: Dict[str, EvidenceReference] = {
        ref.log_id: ref for ref in assessment.evidence_references if ref.log_id
    }

    # If any evidence item was not in assessment.evidence_references, construct deterministic reference
    for item in extracted_evidence:
        lid = item["log_id"]
        if lid and lid not in ref_by_log_id:
            ref_by_log_id[lid] = EvidenceReference(
                log_id=lid,
                service_name=item["service_name"],
                timestamp=item["timestamp"],
                evidence_type=item["evidence_type"],
                correlation_rule=item["correlation_rule"],
                trace_id=item["trace_id"],
                span_id=item["span_id"],
                content_hash=item["content_hash"],
                log_snippet=item.get("log_snippet"),
            )

    # 3. Detect Contradictory Telemetry and Signals
    contradictory_signals: List[str] = []
    contradictory_refs_set: Set[str] = set()

    # (a) Timeline consistency contradictions
    if extracted_timeline:
        sorted_tl = sorted(extracted_timeline, key=lambda e: e["sequence_order"])
        for i, ev in enumerate(sorted_tl):
            # Non-finite or negative propagation lag
            if not math.isfinite(ev["propagation_lag_seconds"]) or ev["propagation_lag_seconds"] < 0:
                contradictory_signals.append(
                    f"Timeline event at sequence {ev['sequence_order']} ({ev['service_name']}) has non-finite or negative propagation lag ({ev['propagation_lag_seconds']}s)."
                )
                if ev["sample_log_id"] and ev["sample_log_id"] in ref_by_log_id:
                    contradictory_refs_set.add(ev["sample_log_id"])

            # Non-monotonic timestamp ordering relative to sequence
            if i > 0:
                prev_ev = sorted_tl[i - 1]
                if prev_ev["timestamp"] and ev["timestamp"] and ev["timestamp"] < prev_ev["timestamp"]:
                    contradictory_signals.append(
                        f"Timeline event at sequence {ev['sequence_order']} ({ev['timestamp'].isoformat()}) occurred before preceding sequence {prev_ev['sequence_order']} ({prev_ev['timestamp'].isoformat()})."
                    )
                    if ev["sample_log_id"] and ev["sample_log_id"] in ref_by_log_id:
                        contradictory_refs_set.add(ev["sample_log_id"])

    # (b) Contradiction with designated earliest failure
    if assessment.earliest_failure_timestamp and assessment.earliest_failure_service:
        earliest_ts = assessment.earliest_failure_timestamp
        earliest_svc = assessment.earliest_failure_service
        for idx, item in enumerate(extracted_evidence):
            item_ts = item["timestamp"]
            item_svc = item["service_name"]
            item_lid = item["log_id"]
            raw_item = raw_evidence[idx]
            raw_level = str(
                getattr(raw_item, "log_level", None)
                or (raw_item.get("log_level") if isinstance(raw_item, dict) else None)
                or "ERROR"
            ).upper()
            # Qualifying error record occurring strictly before the earliest failure in a different service
            if item_ts and item_ts < earliest_ts and item_svc != earliest_svc and raw_level in ("CRITICAL", "ERROR", "FATAL"):
                contradictory_signals.append(
                    f"Evidence record '{item_lid}' in service '{item_svc}' has error timestamp ({item_ts.isoformat()}) preceding designated earliest failure ({earliest_ts.isoformat()}) in '{earliest_svc}'."
                )
                if item_lid:
                    contradictory_refs_set.add(item_lid)

    # (c) Conflicting trace context in a trace-linked candidate
    if assessment.classification == EvidenceSufficiencyClassification.TRACE_LINKED and assessment.shared_trace_ids:
        shared_tids = set(assessment.shared_trace_ids)
        for item in extracted_evidence:
            item_tid = item["trace_id"]
            item_lid = item["log_id"]
            if item_tid and item_tid not in shared_tids and item_lid:
                # Evidence item carries an unrelated / mismatched trace ID
                contradictory_signals.append(
                    f"Evidence record '{item_lid}' in service '{item['service_name']}' carries trace_id '{item_tid}', which does not match shared trace context ({', '.join(sorted(shared_tids))})."
                )
                contradictory_refs_set.add(item_lid)

    # Deterministically sort contradictory signals
    contradictory_signals = sorted(list(set(contradictory_signals)))

    # Collect contradictory evidence references
    contradictory_evidence_references = sorted(
        [ref_by_log_id[lid] for lid in contradictory_refs_set if lid in ref_by_log_id],
        key=_ref_sort_key,
    )

    # 4. Partition Supporting Evidence References
    # Supporting evidence references are those in assessment, excluding contradictory ones
    all_refs = sorted(list(ref_by_log_id.values()), key=_ref_sort_key)
    supporting_evidence_references = [
        ref for ref in all_refs if ref.log_id not in contradictory_refs_set
    ]

    # 5. Formulate Hypotheses, Plausibility, and Findings
    classification = assessment.classification
    earliest_svc = assessment.earliest_failure_service or "unknown"
    other_services = [s for s in assessment.distinct_services if s != earliest_svc]
    missing_telemetry = list(assessment.missing_signals or [])
    observed_facts = list(assessment.observed_facts or [])

    has_seq_anomaly = bool(getattr(assessment, "has_sequence_anomaly", False))
    seq_block_ids = list(getattr(assessment, "sequence_anomaly_block_ids", []) or [])
    seq_services = list(getattr(assessment, "sequence_anomaly_services", []) or [])
    block_txt = f" on block(s) '{', '.join(seq_block_ids)}'" if seq_block_ids else ""
    potential_root_cause_service: Optional[str] = (
        earliest_svc if classification != EvidenceSufficiencyClassification.INSUFFICIENT else None
    )
    initial_observed_failure = getattr(assessment, "initial_observed_failure_dict", None)
    downstream_symptoms = list(getattr(assessment, "downstream_symptoms", []) or [])

    findings: List[RCACandidateFinding] = []
    all_synthesis_claims: List[RCAClaimMapping] = []

    # Map empirical observed facts into structured claim objects
    for idx, fact in enumerate(observed_facts):
        fact_cits: List[RCACitationItem] = []
        lower_fact = fact.lower()
        if "earliest observed failure" in lower_fact and assessment.earliest_failure_log_id:
            earliest_ref = ref_by_log_id.get(assessment.earliest_failure_log_id)
            if earliest_ref:
                fact_cits.append(
                    RCACitationItem(
                        log_id=earliest_ref.log_id,
                        service_name=earliest_ref.service_name,
                        evidence_type=earliest_ref.evidence_type,
                        timestamp=earliest_ref.timestamp,
                        evidence_id=earliest_ref.log_id,
                        log_snippet=getattr(earliest_ref, "log_snippet", None),
                        support_explanation=f"Chronologically earliest failure recorded in initiating service '{earliest_svc}'.",
                        trace_id=earliest_ref.trace_id,
                        span_id=earliest_ref.span_id,
                        content_hash=earliest_ref.content_hash,
                    )
                )
        elif "shared trace" in lower_fact and assessment.shared_trace_ids:
            for ref in supporting_evidence_references:
                if ref.trace_id in assessment.shared_trace_ids:
                    fact_cits.append(
                        RCACitationItem(
                            log_id=ref.log_id,
                            service_name=ref.service_name,
                            evidence_type=ref.evidence_type,
                            timestamp=ref.timestamp,
                            evidence_id=ref.log_id,
                            log_snippet=getattr(ref, "log_snippet", None),
                            support_explanation=f"Corroborating telemetry record linked via shared trace context '{ref.trace_id}'.",
                            trace_id=ref.trace_id,
                            span_id=ref.span_id,
                            content_hash=ref.content_hash,
                        )
                    )
        elif "observed" in lower_fact and "distinct evidence" in lower_fact and supporting_evidence_references:
            for ref in supporting_evidence_references[:3]:
                fact_cits.append(
                    RCACitationItem(
                        log_id=ref.log_id,
                        service_name=ref.service_name,
                        evidence_type=ref.evidence_type,
                        timestamp=ref.timestamp,
                        evidence_id=ref.log_id,
                        log_snippet=getattr(ref, "log_snippet", None),
                        support_explanation=f"Corroborating telemetry evidence in service '{ref.service_name}'.",
                        trace_id=ref.trace_id,
                        span_id=ref.span_id,
                        content_hash=ref.content_hash,
                    )
                )

        all_synthesis_claims.append(
            RCAClaimMapping(
                claim_id=f"fact-{idx + 1}",
                claim_text=fact,
                claim_type="OBSERVED_FACT",
                is_supported=len(fact_cits) > 0,
                citations=fact_cits,
                unsupported_reason=(
                    None
                    if len(fact_cits) > 0
                    else "Empirical observation derived from cluster telemetry bounds without direct single-log attribution."
                ),
            )
        )

    if classification == EvidenceSufficiencyClassification.INSUFFICIENT:
        # Strictly no fabricated probable cause
        primary_hypothesis = None
        explanation = (
            "[Insufficient Telemetry] Available telemetry is too sparse or contradictory to formulate a credible root cause hypothesis."
        )
        plausibility_rationale = (
            "No plausible causal candidate can be established because telemetry lacks sufficient corroboration or contains contradictory temporal progression."
        )
        limitations_and_uncertainty = (
            assessment.uncertainty_statement
            or "Diagnostic uncertainty: High. Telemetry is insufficient to support an incident correlation hypothesis."
        )
        summary = "No root cause candidate synthesized due to insufficient or contradictory telemetry."
        suspected_failure_mechanism = "Inconclusive failure mechanism: available telemetry is too sparse or contradictory to establish causality."
        alternative_explanations = [
            "[Unverified Alternative] Telemetry loss or logging interruption prevented recording initiating failure event.",
            "[Unverified Alternative] Unmonitored external infrastructure failure outside monitored service boundaries.",
            "[Unverified Alternative] Transient network degradation between microservices without error logs.",
        ]

        finding_claims = [
            RCAClaimMapping(
                claim_id="finding-1-insufficient-hypothesis",
                claim_text=explanation,
                claim_type="UNSUPPORTED_SPECULATION",
                is_supported=False,
                citations=[],
                unsupported_reason="Available telemetry is too sparse or contradictory to formulate a credible root cause hypothesis.",
            )
        ]
        validated_finding_claims = validate_and_resolve_rca_citations(finding_claims, raw_evidence)

        finding = RCACandidateFinding(
            explanation=explanation,
            plausibility_rationale=plausibility_rationale,
            supporting_evidence_references=supporting_evidence_references,
            contradictory_evidence_references=contradictory_evidence_references,
            contradictory_signals=contradictory_signals,
            missing_telemetry=missing_telemetry,
            limitations_and_uncertainty=limitations_and_uncertainty,
            is_root_cause_confirmed=False,
            claim_citations=validated_finding_claims,
            potential_root_cause_service=potential_root_cause_service,
            suspected_failure_mechanism=suspected_failure_mechanism,
            initial_observed_failure=initial_observed_failure,
            downstream_symptoms=downstream_symptoms,
            alternative_explanations=alternative_explanations,
        )
        findings.append(finding)
        all_synthesis_claims.extend(validated_finding_claims)

    elif classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY:
        # Formulate non-causal temporal hypothesis
        if assessment.inferred_hypotheses:
            hypothesis_str = assessment.inferred_hypotheses[0]
        elif other_services:
            hypothesis_str = (
                f"[Hypothesis] Failure in service '{earliest_svc}' is temporally associated with subsequent error(s) in "
                f"({', '.join(other_services)}), but causal propagation cannot be confirmed without distributed trace linkage."
            )
        else:
            hypothesis_str = (
                f"[Hypothesis] Error surge in service '{earliest_svc}' exhibits temporal clustering, but causality between events is unconfirmed."
            )

        primary_hypothesis = hypothesis_str
        ts_str = (
            assessment.earliest_failure_timestamp.isoformat()
            if assessment.earliest_failure_timestamp
            else "unknown timestamp"
        )

        if other_services:
            plausibility_rationale = (
                f"Earliest failure was observed chronologically in service '{earliest_svc}' at {ts_str}, "
                f"followed temporally within the observation window by anomalies in {len(other_services)} service(s) ({', '.join(other_services)}). "
                f"Temporal proximity suggests potential association, but without distributed trace identifiers, shared causal propagation cannot be confirmed."
            )
        else:
            plausibility_rationale = (
                f"Error surge in service '{earliest_svc}' occurred within a concentrated temporal window starting at {ts_str}. "
                f"Absence of distributed trace context prevents establishing causal relationships between events."
            )

        limitations_and_uncertainty = (
            assessment.uncertainty_statement
            or "Diagnostic uncertainty: Moderate. Telemetry establishes temporal association within the observation window, "
            "but lacks shared distributed trace context (trace_id). Co-occurring failures cannot be conclusively proven "
            "as causal cascades versus independent simultaneous failures."
        )
        summary = (
            f"Hypothesized temporal association originating in '{earliest_svc}'. "
            f"Causal propagation unconfirmed due to absence of distributed trace identifiers."
        )

        hyp_cits = []
        for ref in supporting_evidence_references:
            expl = (
                f"Chronologically earliest failure in cluster initiating service '{ref.service_name}'."
                if ref.service_name == earliest_svc
                else f"Temporally correlated error in service '{ref.service_name}' within observation window."
            )
            hyp_cits.append(
                RCACitationItem(
                    log_id=ref.log_id,
                    service_name=ref.service_name,
                    evidence_type=ref.evidence_type,
                    timestamp=ref.timestamp,
                    evidence_id=ref.log_id,
                    log_snippet=getattr(ref, "log_snippet", None),
                    support_explanation=expl,
                    trace_id=ref.trace_id,
                    span_id=ref.span_id,
                    content_hash=ref.content_hash,
                )
            )

        if has_seq_anomaly:
            if seq_services:
                seq_svc_str = ", ".join(seq_services)
                suspected_failure_mechanism = (
                    f"Execution sequence deviation detected by LogBERT deep sequence model in service '{seq_svc_str}'{block_txt}, "
                    f"violating expected state transition grammar."
                )
                target_service_label = f"'{seq_svc_str}'"
            else:
                suspected_failure_mechanism = (
                    f"Execution sequence deviation detected by LogBERT deep sequence model{block_txt} "
                    f"with unknown service attribution, violating expected state transition grammar."
                )
                target_service_label = "the affected component"

            alternative_explanations = [
                f"[Unverified Alternative] Legitimate unmodeled concurrency or unusual client access pattern triggering rare normal transitions in {target_service_label}.",
                f"[Unverified Alternative] Transient disk I/O timeout or network packet drop during block transfer causing premature termination or out-of-order execution in {target_service_label}.",
            ]
        else:
            downstream_str = f" ({', '.join(other_services)})" if other_services else ""
            suspected_failure_mechanism = (
                f"Temporal error clustering in service '{earliest_svc}' co-occurring with errors in downstream services"
                f"{downstream_str} without distributed trace linkage."
            )
            alternative_explanations = [
                f"[Unverified Alternative] Independent coincident failure occurring simultaneously in '{earliest_svc}' and downstream services within the observation window.",
                f"[Unverified Alternative] Unmonitored shared upstream dependency (e.g., database, host VM, or network switch) causing simultaneous downstream faults.",
                f"[Unverified Alternative] Downstream service failure initiating retry storm that manifested first in '{earliest_svc}'.",
            ]

        finding_claims = [
            RCAClaimMapping(
                claim_id="finding-1-temporal-hypothesis",
                claim_text=hypothesis_str,
                claim_type="INFERRED_HYPOTHESIS",
                is_supported=len(hyp_cits) > 0,
                citations=hyp_cits,
                unsupported_reason=None if hyp_cits else "Telemetry lacks corroborating timestamps.",
            ),
            RCAClaimMapping(
                claim_id="finding-1-trace-gap",
                claim_text="Absence of distributed trace context prevents proving causal propagation.",
                claim_type="UNSUPPORTED_SPECULATION",
                is_supported=False,
                citations=[],
                unsupported_reason="Shared trace_id is absent from telemetry across involved services.",
            ),
        ]
        if has_seq_anomaly:
            seq_cits = [
                RCACitationItem(
                    log_id=ref.log_id,
                    service_name=ref.service_name,
                    evidence_type=ref.evidence_type,
                    timestamp=ref.timestamp,
                    evidence_id=ref.log_id,
                    log_snippet=getattr(ref, "log_snippet", None),
                    support_explanation=f"LogBERT sequence anomaly transition record in '{ref.service_name}'.",
                    trace_id=ref.trace_id,
                    span_id=ref.span_id,
                    content_hash=ref.content_hash,
                )
                for ref in supporting_evidence_references
                if (
                    ref.evidence_type == "SEQUENCE_ANOMALY"
                    or ref.correlation_rule == "SEQUENCE_ANOMALY"
                ) and (
                    not seq_services or ref.service_name in seq_services
                )
            ][:5]
            seq_target = ", ".join(seq_services) if seq_services else "unknown service"
            if seq_cits:
                finding_claims.append(
                    RCAClaimMapping(
                        claim_id="finding-1-sequence-anomaly",
                        claim_text=f"LogBERT detected anomalous event sequence transition in service '{seq_target}'{block_txt}.",
                        claim_type="INFERRED_HYPOTHESIS",
                        is_supported=True,
                        citations=seq_cits,
                        unsupported_reason=None,
                    )
                )
            else:
                finding_claims.append(
                    RCAClaimMapping(
                        claim_id="finding-1-sequence-anomaly",
                        claim_text=f"LogBERT detected anomalous event sequence transition in service '{seq_target}'{block_txt}.",
                        claim_type="UNSUPPORTED_SPECULATION",
                        is_supported=False,
                        citations=[],
                        unsupported_reason="No supporting sequence anomaly telemetry citations were found.",
                    )
                )

        validated_finding_claims = validate_and_resolve_rca_citations(finding_claims, raw_evidence)

        finding = RCACandidateFinding(
            explanation=hypothesis_str,
            plausibility_rationale=plausibility_rationale,
            supporting_evidence_references=supporting_evidence_references,
            contradictory_evidence_references=contradictory_evidence_references,
            contradictory_signals=contradictory_signals,
            missing_telemetry=missing_telemetry,
            limitations_and_uncertainty=limitations_and_uncertainty,
            is_root_cause_confirmed=False,
            claim_citations=validated_finding_claims,
            potential_root_cause_service=potential_root_cause_service,
            suspected_failure_mechanism=suspected_failure_mechanism,
            initial_observed_failure=initial_observed_failure,
            downstream_symptoms=downstream_symptoms,
            alternative_explanations=alternative_explanations,
        )
        findings.append(finding)
        all_synthesis_claims.extend(validated_finding_claims)

    else:  # TRACE_LINKED
        shared_tid = assessment.shared_trace_ids[0] if assessment.shared_trace_ids else "unknown"
        if assessment.inferred_hypotheses:
            hypothesis_str = assessment.inferred_hypotheses[0]
        elif other_services:
            hypothesis_str = (
                f"[Hypothesis] Failure initiated in service '{earliest_svc}' propagated to downstream service(s) "
                f"({', '.join(other_services)}) along shared trace context '{shared_tid}'."
            )
        else:
            hypothesis_str = (
                f"[Hypothesis] Intra-service failure cascade in '{earliest_svc}' propagated along shared trace context '{shared_tid}'."
            )

        primary_hypothesis = hypothesis_str
        if has_seq_anomaly:
            if seq_services:
                seq_svc_str = ", ".join(seq_services)
                suspected_failure_mechanism = (
                    f"Execution sequence deviation detected by LogBERT deep sequence model in service '{seq_svc_str}'{block_txt}, "
                    f"propagating along shared trace context '{shared_tid}'."
                )
                target_service_label = f"'{seq_svc_str}'"
            else:
                suspected_failure_mechanism = (
                    f"Execution sequence deviation detected by LogBERT deep sequence model{block_txt} "
                    f"with unknown service attribution, propagating along shared trace context '{shared_tid}'."
                )
                target_service_label = "the affected component"

            alternative_explanations = [
                f"[Unverified Alternative] Legitimate unmodeled concurrency or unusual client access pattern triggering rare normal transitions in {target_service_label}.",
                f"[Unverified Alternative] Transient disk I/O timeout or network packet drop during block transfer causing premature termination or out-of-order execution in {target_service_label}.",
            ]
        else:
            suspected_failure_mechanism = (
                f"Distributed failure cascade initiated in service '{earliest_svc}' propagating along shared trace context "
                f"'{shared_tid}' to downstream dependencies ({', '.join(other_services)})."
            )
            alternative_explanations = [
                f"[Unverified Alternative] Independent unmonitored external dependency failure (e.g., third-party API outage) along the shared execution path.",
                f"[Unverified Alternative] Database deadlock or resource exhaustion in shared backend masquerading as '{earliest_svc}' initiation.",
            ]

        if other_services:
            plausibility_rationale = (
                f"Earliest observed failure was recorded chronologically in service '{earliest_svc}' and shares distributed trace context "
                f"'{shared_tid}' with {len(other_services)} downstream service(s) ({', '.join(other_services)}). "
                f"This trace linkage substantiates a common transactional execution path."
            )
        else:
            plausibility_rationale = (
                f"Earliest observed failure was recorded chronologically in service '{earliest_svc}' and shares distributed trace context "
                f"'{shared_tid}' across multiple events within the service."
            )

        limitations_and_uncertainty = (
            assessment.uncertainty_statement
            or "Diagnostic uncertainty: Low-to-Moderate. Shared trace identifiers connect the involved services/events, "
            "substantiating a common execution path. However, chronological earliest failure is not confirmed root cause; "
            "the failure may be a symptom of an unmonitored external dependency or database deadlock."
        )
        summary = (
            f"Evidence-grounded candidate explanation: failure initiated in '{earliest_svc}' propagated along shared trace '{shared_tid}'. "
            f"Note: chronological precedence does not prove root cause."
        )

        hyp_cits = []
        for ref in supporting_evidence_references:
            expl = (
                f"Chronologically earliest failure initiating trace cascade in '{ref.service_name}'."
                if ref.service_name == earliest_svc
                else f"Downstream failure in service '{ref.service_name}' propagating along shared trace '{shared_tid}'."
            )
            hyp_cits.append(
                RCACitationItem(
                    log_id=ref.log_id,
                    service_name=ref.service_name,
                    evidence_type=ref.evidence_type,
                    timestamp=ref.timestamp,
                    evidence_id=ref.log_id,
                    log_snippet=getattr(ref, "log_snippet", None),
                    support_explanation=expl,
                    trace_id=ref.trace_id,
                    span_id=ref.span_id,
                    content_hash=ref.content_hash,
                )
            )

        finding_claims = [
            RCAClaimMapping(
                claim_id="finding-1-trace-hypothesis",
                claim_text=hypothesis_str,
                claim_type="INFERRED_HYPOTHESIS",
                is_supported=len(hyp_cits) > 0,
                citations=hyp_cits,
                unsupported_reason=None if hyp_cits else "Telemetry lacks shared trace context.",
            ),
        ]
        if missing_telemetry:
            finding_claims.append(
                RCAClaimMapping(
                    claim_id="finding-1-telemetry-gap",
                    claim_text=f"Telemetry gap: {missing_telemetry[0]}",
                    claim_type="UNSUPPORTED_SPECULATION",
                    is_supported=False,
                    citations=[],
                    unsupported_reason="Independent confirmation telemetry is missing from the indexed incident dataset.",
                )
            )
        if has_seq_anomaly:
            seq_cits = [
                RCACitationItem(
                    log_id=ref.log_id,
                    service_name=ref.service_name,
                    evidence_type=ref.evidence_type,
                    timestamp=ref.timestamp,
                    evidence_id=ref.log_id,
                    log_snippet=getattr(ref, "log_snippet", None),
                    support_explanation=f"LogBERT sequence anomaly transition record in '{ref.service_name}'.",
                    trace_id=ref.trace_id,
                    span_id=ref.span_id,
                    content_hash=ref.content_hash,
                )
                for ref in supporting_evidence_references
                if (
                    ref.evidence_type == "SEQUENCE_ANOMALY"
                    or ref.correlation_rule == "SEQUENCE_ANOMALY"
                ) and (
                    not seq_services or ref.service_name in seq_services
                )
            ][:5]
            seq_target = ", ".join(seq_services) if seq_services else "unknown service"
            if seq_cits:
                finding_claims.append(
                    RCAClaimMapping(
                        claim_id="finding-1-sequence-anomaly",
                        claim_text=f"LogBERT detected anomalous event sequence transition in service '{seq_target}'{block_txt}.",
                        claim_type="INFERRED_HYPOTHESIS",
                        is_supported=True,
                        citations=seq_cits,
                        unsupported_reason=None,
                    )
                )
            else:
                finding_claims.append(
                    RCAClaimMapping(
                        claim_id="finding-1-sequence-anomaly",
                        claim_text=f"LogBERT detected anomalous event sequence transition in service '{seq_target}'{block_txt}.",
                        claim_type="UNSUPPORTED_SPECULATION",
                        is_supported=False,
                        citations=[],
                        unsupported_reason="No supporting sequence anomaly telemetry citations were found.",
                    )
                )
        validated_finding_claims = validate_and_resolve_rca_citations(finding_claims, raw_evidence)

        finding = RCACandidateFinding(
            explanation=hypothesis_str,
            plausibility_rationale=plausibility_rationale,
            supporting_evidence_references=supporting_evidence_references,
            contradictory_evidence_references=contradictory_evidence_references,
            contradictory_signals=contradictory_signals,
            missing_telemetry=missing_telemetry,
            limitations_and_uncertainty=limitations_and_uncertainty,
            is_root_cause_confirmed=False,
            claim_citations=validated_finding_claims,
            potential_root_cause_service=potential_root_cause_service,
            suspected_failure_mechanism=suspected_failure_mechanism,
            initial_observed_failure=initial_observed_failure,
            downstream_symptoms=downstream_symptoms,
            alternative_explanations=alternative_explanations,
        )
        findings.append(finding)
        all_synthesis_claims.extend(validated_finding_claims)

    validated_synthesis_claims = validate_and_resolve_rca_citations(all_synthesis_claims, raw_evidence)

    primary_finding = findings[0] if findings else None
    return RCASynthesisResult(
        classification=classification,
        is_root_cause_confirmed=False,
        primary_hypothesis=primary_hypothesis,
        findings=findings,
        observed_facts=observed_facts,
        uncertainty_statement=assessment.uncertainty_statement,
        summary=summary,
        claim_citations=validated_synthesis_claims,
        potential_root_cause_service=potential_root_cause_service,
        suspected_failure_mechanism=primary_finding.suspected_failure_mechanism if primary_finding else None,
        initial_observed_failure=initial_observed_failure,
        downstream_symptoms=downstream_symptoms,
        alternative_explanations=primary_finding.alternative_explanations if primary_finding else [],
    )
