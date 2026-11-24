from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Set, Tuple


class EvidenceSufficiencyClassification(str, Enum):
    """Deterministic classification of incident evidence sufficiency."""

    TRACE_LINKED = "TRACE_LINKED"
    TEMPORAL_ONLY = "TEMPORAL_ONLY"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class EvidenceReference:
    """Immutable, deterministic reference to source evidence preserving exact provenance."""

    log_id: str
    service_name: str
    timestamp: Optional[datetime]
    evidence_type: str
    correlation_rule: str
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    content_hash: Optional[str] = None
    log_snippet: Optional[str] = None


@dataclass(frozen=True)
class EvidenceSufficiencyAssessment:
    """Structured in-memory assessment of incident evidence sufficiency and bounds."""

    classification: EvidenceSufficiencyClassification
    is_root_cause_confirmed: bool = False  # Strictly False: correlation is not confirmed causation
    distinct_evidence_count: int = 0
    distinct_service_count: int = 0
    distinct_services: List[str] = field(default_factory=list)
    trace_ids_found: List[str] = field(default_factory=list)
    span_ids_found: List[str] = field(default_factory=list)
    has_shared_trace: bool = False
    shared_trace_ids: List[str] = field(default_factory=list)
    correlation_rules_detected: List[str] = field(default_factory=list)
    evidence_types_detected: List[str] = field(default_factory=list)
    is_timeline_temporally_consistent: bool = True
    earliest_failure_service: Optional[str] = None
    earliest_failure_timestamp: Optional[datetime] = None
    earliest_failure_log_id: Optional[str] = None
    evidence_references: List[EvidenceReference] = field(default_factory=list)
    observed_facts: List[str] = field(default_factory=list)
    inferred_hypotheses: List[str] = field(default_factory=list)
    uncertainty_statement: str = ""
    supporting_signals: List[str] = field(default_factory=list)
    missing_signals: List[str] = field(default_factory=list)
    has_sequence_anomaly: bool = False
    sequence_anomaly_count: int = 0
    sequence_anomaly_block_ids: List[str] = field(default_factory=list)
    sequence_anomaly_services: List[str] = field(default_factory=list)
    sequence_anomaly_log_ids: List[str] = field(default_factory=list)
    initial_observed_failure_dict: Optional[Dict[str, Any]] = None
    downstream_symptoms: List[str] = field(default_factory=list)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _get_val(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safely extract a value from a dict, dataclass, Pydantic model, or ORM object."""
    for k in keys:
        if isinstance(obj, dict):
            if k in obj and obj[k] is not None:
                return obj[k]
        else:
            val = getattr(obj, k, None)
            if val is not None:
                return val
    return default


def _extract_evidence_item_fields(item: Any) -> Dict[str, Any]:
    """Extract canonical fields from an evidence item of any supported type."""
    # Handle SQLAlchemy metadata_ or Pydantic attributes
    meta = _get_val(item, "metadata_", "metadata", "attributes", default={})
    if not isinstance(meta, dict):
        meta = {}

    log_id = str(_get_val(item, "log_id", "opensearch_doc_id", "id", default="") or "")
    service_name = str(_get_val(item, "service_name", default="unknown") or "unknown")
    raw_ts = _get_val(item, "timestamp", "log_timestamp", "event_timestamp", default=None)
    ts = _ensure_utc(raw_ts) if isinstance(raw_ts, datetime) else None

    evidence_type = str(_get_val(item, "evidence_type", default="EVIDENCE") or "EVIDENCE")
    correlation_rule = str(
        _get_val(item, "correlation_rule", default=None)
        or meta.get("correlation_rule")
        or "UNKNOWN"
    )

    trace_id_val = _get_val(item, "trace_id", default=None) or meta.get("trace_id")
    trace_id = str(trace_id_val).strip() if trace_id_val and str(trace_id_val).strip() else None

    span_id_val = _get_val(item, "span_id", default=None) or meta.get("span_id")
    span_id = str(span_id_val).strip() if span_id_val and str(span_id_val).strip() else None

    content_hash_val = _get_val(item, "content_hash", default=None)
    content_hash = (
        str(content_hash_val).strip()
        if content_hash_val and str(content_hash_val).strip()
        else None
    )

    snippet_val = _get_val(item, "log_snippet", "snippet", "message", default=None)
    log_snippet = (
        str(snippet_val).strip()
        if snippet_val and str(snippet_val).strip()
        else None
    )

    incident_id_val = _get_val(item, "incident_id", default=None)
    incident_id = (
        str(incident_id_val).strip()
        if incident_id_val and str(incident_id_val).strip()
        else None
    )

    raw_level = _get_val(item, "log_level", "severity", default=None) or meta.get("log_level") or meta.get("severity")
    log_level = str(raw_level).upper() if raw_level else None

    return {
        "log_id": log_id,
        "service_name": service_name,
        "timestamp": ts,
        "log_level": log_level,
        "evidence_type": evidence_type,
        "correlation_rule": correlation_rule,
        "trace_id": trace_id,
        "span_id": span_id,
        "content_hash": content_hash,
        "log_snippet": log_snippet,
        "incident_id": incident_id,
    }


def _extract_timeline_event_fields(event: Any) -> Dict[str, Any]:
    """Extract canonical fields from a timeline event of any supported type."""
    attrs = _get_val(event, "attributes", default={})
    if not isinstance(attrs, dict):
        attrs = {}

    raw_ts = _get_val(event, "event_timestamp", "timestamp", default=None)
    ts = _ensure_utc(raw_ts) if isinstance(raw_ts, datetime) else None

    seq_order = int(_get_val(event, "sequence_order", default=0) or 0)
    service_name = str(_get_val(event, "service_name", default="unknown") or "unknown")
    severity = str(_get_val(event, "severity", "log_level", default="INFO") or "INFO").upper()

    is_earliest = bool(
        _get_val(event, "is_earliest_observed_failure", default=False)
        or attrs.get("is_earliest_observed_failure", False)
    )

    raw_lag = _get_val(event, "propagation_lag_seconds", default=None)
    if raw_lag is None:
        raw_lag = attrs.get("propagation_lag_seconds", 0.0)

    try:
        lag_seconds = float(raw_lag)
    except (TypeError, ValueError):
        lag_seconds = float("nan")

    trace_id_val = _get_val(event, "trace_id", default=None) or attrs.get("trace_id")
    trace_id = str(trace_id_val).strip() if trace_id_val and str(trace_id_val).strip() else None

    span_id_val = _get_val(event, "span_id", default=None) or attrs.get("span_id")
    span_id = str(span_id_val).strip() if span_id_val and str(span_id_val).strip() else None

    sample_log_id = str(
        _get_val(event, "sample_log_id", "log_id", default="")
        or attrs.get("sample_log_id", "")
        or ""
    )

    return {
        "sequence_order": seq_order,
        "timestamp": ts,
        "service_name": service_name,
        "severity": severity,
        "is_earliest_observed_failure": is_earliest,
        "propagation_lag_seconds": lag_seconds,
        "trace_id": trace_id,
        "span_id": span_id,
        "sample_log_id": sample_log_id,
    }


def evaluate_evidence_sufficiency(
    evidence_items: Optional[List[Any]] = None,
    timeline_events: Optional[List[Any]] = None,
) -> EvidenceSufficiencyAssessment:
    """Deterministically assess the sufficiency, provenance, and bounds of incident evidence.

    Pure in-memory analysis function:
    - Never mutates input evidence or timeline items.
    - Preserves exact source log IDs and provenance hashes.
    - Deterministically orders evidence references.
    - Differentiates observed facts from inferred hypotheses.
    - Strictly preserves is_root_cause_confirmed = False.
    """
    raw_evidence = evidence_items or []
    raw_timeline = timeline_events or []

    # 1. Safely extract normalized data without mutating inputs
    extracted_evidence = [_extract_evidence_item_fields(item) for item in raw_evidence]
    extracted_timeline = [_extract_timeline_event_fields(ev) for ev in raw_timeline]

    # 2. Evaluate Timeline Consistency using existing data
    is_timeline_consistent = True
    timeline_issues: List[str] = []

    if extracted_timeline:
        # Sort local copy by sequence_order to verify temporal progression
        sorted_tl = sorted(extracted_timeline, key=lambda e: e["sequence_order"])

        for i in range(len(sorted_tl)):
            curr = sorted_tl[i]
            # Verify lag is finite
            if not math.isfinite(curr["propagation_lag_seconds"]) or curr["propagation_lag_seconds"] < 0:
                is_timeline_consistent = False
                timeline_issues.append(f"Non-finite or negative propagation lag at sequence {curr['sequence_order']}.")

            # Verify timestamp ordering
            if i > 0:
                prev = sorted_tl[i - 1]
                if prev["timestamp"] and curr["timestamp"] and curr["timestamp"] < prev["timestamp"]:
                    is_timeline_consistent = False
                    timeline_issues.append(
                        f"Non-monotonic timestamp decrease from sequence {prev['sequence_order']} to {curr['sequence_order']}."
                    )

    # 3. Identify Distinct Services & Telemetry Identifiers
    evidence_services = {e["service_name"] for e in extracted_evidence if e["service_name"] and e["service_name"] != "unknown"}
    timeline_services = {e["service_name"] for e in extracted_timeline if e["service_name"] and e["service_name"] != "unknown"}
    all_services = sorted(list(evidence_services | timeline_services))
    distinct_service_count = len(all_services)

    # Distinct evidence records by log_id
    distinct_log_ids = {e["log_id"] for e in extracted_evidence if e["log_id"]}
    distinct_evidence_count = len(distinct_log_ids)

    # Telemetry IDs found
    all_trace_ids = sorted(list({
        e["trace_id"]
        for e in (extracted_evidence + extracted_timeline)
        if e["trace_id"]
    }))
    all_span_ids = sorted(list({
        e["span_id"]
        for e in (extracted_evidence + extracted_timeline)
        if e["span_id"]
    }))

    correlation_rules = sorted(list({e["correlation_rule"] for e in extracted_evidence if e["correlation_rule"] and e["correlation_rule"] != "UNKNOWN"}))
    evidence_types = sorted(list({e["evidence_type"] for e in extracted_evidence if e["evidence_type"]}))

    # 4. Identify Earliest Observed Failure
    earliest_failure_svc: Optional[str] = None
    earliest_failure_ts: Optional[datetime] = None
    earliest_failure_log_id: Optional[str] = None

    # First check timeline events explicitly marked
    for ev in extracted_timeline:
        if ev["is_earliest_observed_failure"]:
            earliest_failure_svc = ev["service_name"]
            earliest_failure_ts = ev["timestamp"]
            earliest_failure_log_id = ev["sample_log_id"] or None
            break

    # Fallback to evidence item with CULPRIT_LOG or FIRST_FAILURE
    if not earliest_failure_svc:
        for ev in extracted_evidence:
            if ev["evidence_type"] == "CULPRIT_LOG" or ev["correlation_rule"] == "FIRST_FAILURE":
                earliest_failure_svc = ev["service_name"]
                earliest_failure_ts = ev["timestamp"]
                earliest_failure_log_id = ev["log_id"] or None
                break

    # Fallback to chronologically earliest qualifying item
    if not earliest_failure_svc and extracted_evidence:
        valid_evs = [e for e in extracted_evidence if e["timestamp"] is not None]
        if valid_evs:
            sorted_by_ts = sorted(valid_evs, key=lambda e: (e["timestamp"], e["log_id"]))
            earliest_failure_svc = sorted_by_ts[0]["service_name"]
            earliest_failure_ts = sorted_by_ts[0]["timestamp"]
            earliest_failure_log_id = sorted_by_ts[0]["log_id"] or None

    # 4b. Sequence Anomaly Analysis & Initial Failure Details
    import re
    _blk_re = re.compile(r"\b(blk_-?\d+)\b")

    # Deduplicate sequence anomalies to prevent double counting across evidence and timeline
    seq_records_by_key: Dict[str, Dict[str, Any]] = {}

    for e in extracted_evidence:
        is_seq = (
            e.get("evidence_type") == "SEQUENCE_ANOMALY"
            or e.get("correlation_rule") == "SEQUENCE_ANOMALY"
            or "cl-logbert-" in str(e.get("cluster_id") or "")
        )
        if is_seq:
            key = e.get("log_id") or f"{e.get('service_name')}:{e.get('timestamp')}:{e.get('log_snippet')}"
            seq_records_by_key[key] = e

    for ev in extracted_timeline:
        is_seq = (
            ev.get("event_type") == "SEQUENCE_ANOMALY"
            or "cl-logbert-" in str(ev.get("cluster_id") or "")
        )
        if is_seq:
            sample_id = ev.get("sample_log_id") or ev.get("log_id")
            if sample_id and sample_id in seq_records_by_key:
                continue
            key = sample_id or f"{ev.get('service_name')}:{ev.get('timestamp')}:{ev.get('summary')}"
            if key not in seq_records_by_key:
                seq_records_by_key[key] = ev

    seq_items = list(seq_records_by_key.values())
    has_sequence_anomaly = len(seq_items) > 0
    sequence_anomaly_count = len(seq_items)

    # Extract block IDs ONLY from records associated with SEQUENCE_ANOMALY
    seq_block_ids: List[str] = sorted(list({
        m.group(1)
        for item in seq_items
        for text in [item.get("log_snippet") or "", item.get("message") or "", str(item.get("attributes") or ""), item.get("summary") or ""]
        for m in _blk_re.finditer(text)
    }))

    # Associate each sequence anomaly with its actual source service
    seq_services: List[str] = sorted(list({
        item.get("service_name")
        for item in seq_items
        if item.get("service_name") and item.get("service_name") != "unknown"
    }))

    seq_log_ids: List[str] = sorted(list({
        (item.get("log_id") or item.get("sample_log_id"))
        for item in seq_items
        if (item.get("log_id") or item.get("sample_log_id"))
    }))

    initial_observed_failure_dict: Optional[Dict[str, Any]] = None
    if earliest_failure_svc and earliest_failure_ts:
        matching_item = None
        for item in extracted_evidence:
            if item.get("log_id") == earliest_failure_log_id:
                matching_item = item
                break
        if not matching_item and extracted_timeline:
            for item in extracted_timeline:
                if item.get("sample_log_id") == earliest_failure_log_id:
                    matching_item = item
                    break
        initial_observed_failure_dict = {
            "service_name": earliest_failure_svc,
            "timestamp": earliest_failure_ts.isoformat(),
            "log_id": earliest_failure_log_id,
            "event_type": (matching_item.get("evidence_type") or matching_item.get("event_type") or "FAILURE") if matching_item else "FAILURE",
            "snippet": (matching_item.get("log_snippet") or matching_item.get("message")) if matching_item else None,
        }

    # 5. Determine Relevant Shared Trace Identifiers
    # A trace ID is considered "relevant and shared" only if it connects the assessed events or services:
    # - In multi-service scenarios: connects at least two distinct services within the incident's assessed services.
    # - In single-service scenarios: connects the earliest observed failure event/log to at least one other distinct timeline event or evidence record.
    traces_by_service: Dict[str, Set[str]] = {}
    for item in extracted_evidence + extracted_timeline:
        svc = item["service_name"]
        tid = item["trace_id"]
        if svc in all_services and tid:
            if svc not in traces_by_service:
                traces_by_service[svc] = set()
            traces_by_service[svc].add(tid)

    shared_trace_ids: List[str] = []
    if distinct_service_count >= 2:
        # Must bridge across at least two distinct assessed services
        for tid in all_trace_ids:
            services_sharing = [s for s in all_services if tid in traces_by_service.get(s, set())]
            if len(services_sharing) >= 2:
                shared_trace_ids.append(tid)
    elif distinct_service_count == 1:
        # Must connect the earliest failure log/event with at least one other distinct record in the service
        single_svc = all_services[0]
        # Find trace on earliest failure
        earliest_traces = set()
        for item in extracted_evidence + extracted_timeline:
            is_earliest = (
                item.get("is_earliest_observed_failure")
                or item.get("evidence_type") == "CULPRIT_LOG"
                or (earliest_failure_log_id and item.get("log_id") == earliest_failure_log_id)
            )
            if is_earliest and item.get("trace_id"):
                earliest_traces.add(item["trace_id"])

        for tid in all_trace_ids:
            # Check if this trace is on the earliest failure AND at least one other non-earliest record
            if tid in earliest_traces:
                other_matching = [
                    item for item in extracted_evidence + extracted_timeline
                    if item.get("trace_id") == tid
                    and not (
                        item.get("is_earliest_observed_failure")
                        or item.get("evidence_type") == "CULPRIT_LOG"
                        or (earliest_failure_log_id and item.get("log_id") == earliest_failure_log_id)
                    )
                ]
                if other_matching:
                    shared_trace_ids.append(tid)

    shared_trace_ids = sorted(shared_trace_ids)
    has_shared_trace = len(shared_trace_ids) > 0

    # 6. Classification Decision Logic
    # Sufficiency criteria:
    # INSUFFICIENT when:
    # - 0 evidence items
    # - Only 1 evidence record AND <= 1 timeline event (sparse isolated failure)
    # - Timeline has corrupted temporal ordering (inconsistent)
    # - No valid timestamps on any items
    has_valid_timestamps = any(e["timestamp"] is not None for e in extracted_evidence + extracted_timeline)

    is_sparse = (distinct_evidence_count < 2 and len(extracted_timeline) < 2)

    if not extracted_evidence or is_sparse or not has_valid_timestamps or not is_timeline_consistent:
        classification = EvidenceSufficiencyClassification.INSUFFICIENT
    elif has_shared_trace:
        classification = EvidenceSufficiencyClassification.TRACE_LINKED
    else:
        classification = EvidenceSufficiencyClassification.TEMPORAL_ONLY

    # 6b. Downstream Symptom Identification with Strict Evidence and Timestamp Ordering
    downstream_symptoms: List[str] = []
    if earliest_failure_svc and earliest_failure_ts and classification != EvidenceSufficiencyClassification.INSUFFICIENT:
        downstream_by_service: Dict[str, List[Dict[str, Any]]] = {}

        for item in extracted_evidence:
            svc = item.get("service_name")
            if not svc or svc == "unknown" or svc == earliest_failure_svc:
                continue
            ts = item.get("timestamp")
            # Verify timestamp ordering: must be subsequent or co-occurring (ts >= earliest_failure_ts)
            if ts and ts >= earliest_failure_ts:
                lvl = str(item.get("log_level") or "").upper()
                etype = str(item.get("evidence_type") or "").upper()
                is_error_level = lvl in ("ERROR", "CRITICAL", "FATAL", "WARN", "WARNING")
                is_error_type = etype in (
                    "ERROR", "EXCEPTION", "CULPRIT_LOG", "SEQUENCE_ANOMALY", "CORRELATED_ERROR", "CORRELATION_SPIKE"
                )
                if (is_error_level or is_error_type) and lvl not in ("INFO", "DEBUG") and etype not in ("INFO", "DEBUG"):
                    downstream_by_service.setdefault(svc, []).append(item)

        for ev in extracted_timeline:
            svc = ev.get("service_name")
            if not svc or svc == "unknown" or svc == earliest_failure_svc:
                continue
            ts = ev.get("timestamp")
            if ts and ts >= earliest_failure_ts:
                etype = str(ev.get("event_type") or "").upper()
                if etype in ("FAILURE", "ERROR", "ANOMALY", "SEQUENCE_ANOMALY", "CORRELATION_SPIKE"):
                    sample_id = ev.get("sample_log_id")
                    existing_lids = {x.get("log_id") for x in downstream_by_service.get(svc, [])}
                    if not sample_id or sample_id not in existing_lids:
                        downstream_by_service.setdefault(svc, []).append(ev)

        for svc in sorted(downstream_by_service.keys()):
            records = sorted(
                downstream_by_service[svc],
                key=lambda r: r.get("timestamp") or datetime.max.replace(tzinfo=timezone.utc),
            )
            first_rec = records[0]
            first_ts = first_rec.get("timestamp")
            first_lid = first_rec.get("log_id") or first_rec.get("sample_log_id") or "unspecified"
            ts_str = first_ts.isoformat() if first_ts else "unspecified timestamp"
            downstream_symptoms.append(
                f"Subsequent failure in service '{svc}' at {ts_str} (log_id: '{first_lid}', {len(records)} symptom record(s) observed)"
            )

    # 7. Formulate Deterministic Evidence References
    # Deterministic sort order: (timestamp, service_name, log_id, evidence_type, correlation_rule)
    def _ref_sort_key(e: Dict[str, Any]) -> Tuple[datetime, str, str, str, str]:
        ts = e["timestamp"] or datetime.min.replace(tzinfo=timezone.utc)
        return (
            ts,
            e["service_name"] or "",
            e["log_id"] or "",
            e["evidence_type"] or "",
            e["correlation_rule"] or "",
        )

    sorted_evidence_items = sorted(extracted_evidence, key=_ref_sort_key)
    evidence_references: List[EvidenceReference] = [
        EvidenceReference(
            log_id=e["log_id"],
            service_name=e["service_name"],
            timestamp=e["timestamp"],
            evidence_type=e["evidence_type"],
            correlation_rule=e["correlation_rule"],
            trace_id=e["trace_id"],
            span_id=e["span_id"],
            content_hash=e["content_hash"],
            log_snippet=e.get("log_snippet"),
        )
        for e in sorted_evidence_items
    ]

    # 8. Formulate Observed Facts, Inferred Hypotheses, and Uncertainty
    observed_facts: List[str] = []
    inferred_hypotheses: List[str] = []
    supporting_signals: List[str] = []
    missing_signals: List[str] = []

    # Facts
    observed_facts.append(
        f"Observed {distinct_evidence_count} distinct evidence record(s) across {distinct_service_count} service(s): {', '.join(all_services) if all_services else 'none'}."
    )

    if extracted_timeline:
        observed_facts.append(
            f"Timeline contains {len(extracted_timeline)} milestone event(s) (temporal consistency: {'VERIFIED' if is_timeline_consistent else 'FAILED'})."
        )

    if earliest_failure_svc and earliest_failure_ts:
        observed_facts.append(
            f"Earliest observed failure recorded in service '{earliest_failure_svc}' at {earliest_failure_ts.isoformat()}"
            + (f" (log_id: '{earliest_failure_log_id}')" if earliest_failure_log_id else "")
            + "."
        )

    if has_sequence_anomaly:
        svc_txt = f" in service(s) '{', '.join(seq_services)}'" if seq_services else " with unknown service attribution"
        blk_txt = f" on block(s) {', '.join(seq_block_ids)}" if seq_block_ids else ""
        observed_facts.append(
            f"LogBERT deep neural sequence anomaly detected: {sequence_anomaly_count} sequence deviation record(s){svc_txt}{blk_txt}."
        )
        supporting_signals.append(f"LogBERT neural transition anomaly in {', '.join(seq_services) if seq_services else 'telemetry'} substantiating execution sequence deviation")

    if shared_trace_ids:
        observed_facts.append(
            f"Relevant shared trace identifier(s) detected: {', '.join(shared_trace_ids)} connecting involved services/events."
        )
    elif all_trace_ids:
        observed_facts.append(
            f"Trace identifiers detected in telemetry ({', '.join(all_trace_ids)}), but none establish a relevant cross-event or cross-service connection."
        )
    else:
        observed_facts.append("No distributed trace identifiers (trace_id) were present in the assessed telemetry.")

    hashes_present = len([e for e in extracted_evidence if e["content_hash"]])
    if hashes_present > 0:
        observed_facts.append(
            f"Provenance verification hashes (SHA-256) present on {hashes_present} of {len(extracted_evidence)} evidence record(s)."
        )

    # Hypotheses & Uncertainty by classification
    other_services = [s for s in all_services if s != earliest_failure_svc]

    if classification == EvidenceSufficiencyClassification.TRACE_LINKED:
        if other_services:
            inferred_hypotheses.append(
                f"[Hypothesis] Failure initiated in service '{earliest_failure_svc}' propagated to downstream service(s) "
                f"({', '.join(other_services)}) along shared trace context '{shared_trace_ids[0]}'."
            )
        else:
            inferred_hypotheses.append(
                f"[Hypothesis] Intra-service failure cascade in '{earliest_failure_svc}' propagated along shared trace context '{shared_trace_ids[0]}'."
            )

        uncertainty_statement = (
            "Diagnostic uncertainty: Low-to-Moderate. Shared trace identifiers connect the involved services/events, "
            "substantiating a common execution path. However, chronological earliest failure is not confirmed root cause; "
            "the failure may be a symptom of an unmonitored external dependency or database deadlock."
        )
        supporting_signals.append(f"Shared trace identifier(s) '{', '.join(shared_trace_ids)}' link assessed entities")
        supporting_signals.append("Temporally consistent milestone event ordering verified")
        supporting_signals.append(f"{distinct_evidence_count} distinct corroborating evidence records available")
        missing_signals.append("Independent upstream dependency telemetry unverified")
        missing_signals.append("Direct parent-child span invocation hierarchy unverified")

    elif classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY:
        if other_services:
            inferred_hypotheses.append(
                f"[Hypothesis] Failure in service '{earliest_failure_svc}' is temporally correlated with subsequent error(s) in "
                f"({', '.join(other_services)}), but causal propagation cannot be confirmed without distributed trace linkage."
            )
        else:
            inferred_hypotheses.append(
                f"[Hypothesis] Error surge in service '{earliest_failure_svc}' exhibits temporal clustering, but causality between events is unconfirmed."
            )

        uncertainty_statement = (
            "Diagnostic uncertainty: Moderate. Telemetry establishes temporal association within the observation window, "
            "but lacks shared distributed trace context (trace_id). Co-occurring failures cannot be conclusively proven "
            "as causal cascades versus independent simultaneous failures."
        )
        supporting_signals.append(f"Temporal proximity and correlation observed across {distinct_service_count} service(s)")
        supporting_signals.append("Temporally non-decreasing milestone event ordering verified")
        supporting_signals.append(f"{distinct_evidence_count} distinct corroborating evidence records available")
        missing_signals.append("Shared distributed trace context (trace_id) missing between involved services")
        missing_signals.append("Direct RPC/HTTP call-chain linkage unverified")

    else:  # INSUFFICIENT
        inferred_hypotheses.append(
            "[Hypothesis] Available telemetry is too sparse or contradictory to formulate a reliable correlation or root-cause hypothesis."
        )

        reasons = []
        if distinct_evidence_count < 2 and len(extracted_timeline) < 2:
            reasons.append("fewer than 2 corroborating evidence records or timeline milestones available")
        if not is_timeline_consistent:
            reasons.append(f"timeline consistency validation failed ({'; '.join(timeline_issues)})")
        if not has_valid_timestamps:
            reasons.append("valid timestamps absent from telemetry")
        if not extracted_evidence:
            reasons.append("no evidence records provided")

        reason_str = "; ".join(reasons) if reasons else "telemetry lacks sufficient corroboration"
        uncertainty_statement = (
            f"Diagnostic uncertainty: High. Telemetry is insufficient to support an incident correlation hypothesis ({reason_str})."
        )
        missing_signals.append("Insufficient corroborating evidence records (fewer than 2 distinct records)")
        missing_signals.append("Distributed trace context absent")
        missing_signals.append("Downstream cascade telemetry absent")
        if not is_timeline_consistent:
            missing_signals.append("Temporally consistent timeline progression absent")

    return EvidenceSufficiencyAssessment(
        classification=classification,
        is_root_cause_confirmed=False,
        distinct_evidence_count=distinct_evidence_count,
        distinct_service_count=distinct_service_count,
        distinct_services=all_services,
        trace_ids_found=all_trace_ids,
        span_ids_found=all_span_ids,
        has_shared_trace=has_shared_trace,
        shared_trace_ids=shared_trace_ids,
        correlation_rules_detected=correlation_rules,
        evidence_types_detected=evidence_types,
        is_timeline_temporally_consistent=is_timeline_consistent,
        earliest_failure_service=earliest_failure_svc,
        earliest_failure_timestamp=earliest_failure_ts,
        earliest_failure_log_id=earliest_failure_log_id,
        evidence_references=evidence_references,
        observed_facts=observed_facts,
        inferred_hypotheses=inferred_hypotheses,
        uncertainty_statement=uncertainty_statement,
        supporting_signals=supporting_signals,
        missing_signals=missing_signals,
        has_sequence_anomaly=has_sequence_anomaly,
        sequence_anomaly_count=sequence_anomaly_count,
        sequence_anomaly_block_ids=seq_block_ids,
        sequence_anomaly_services=seq_services,
        sequence_anomaly_log_ids=seq_log_ids,
        initial_observed_failure_dict=initial_observed_failure_dict,
        downstream_symptoms=downstream_symptoms,
    )
