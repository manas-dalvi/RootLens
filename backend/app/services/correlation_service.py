from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.clustering_service import LogCluster, LogEventItem
from app.services.evidence_analysis_service import evaluate_evidence_sufficiency
from app.services.rca_synthesis_service import synthesize_rca_findings


# Default configuration
DEFAULT_CASCADE_WINDOW_SECONDS: int = 120
MAX_SPAN_CORRELATION_SECONDS: float = 10.0


@dataclass
class CorrelatedTimelineEvent:
    """Chronologically ordered milestone event in a candidate incident timeline."""

    sequence_order: int
    event_timestamp: datetime
    service_name: str
    event_type: str  # "FIRST_ERROR", "CASCADING_FAILURE", "ANOMALY_SPIKE"
    severity: str    # "CRITICAL", "HIGH", "MEDIUM"
    summary: str
    is_earliest_observed_failure: bool
    propagation_lag_seconds: float
    cluster_id: str
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    sample_log_id: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CorrelatedEvidenceItem:
    """Verifiable evidence item linking incident candidate to source log record."""

    evidence_type: str  # "CULPRIT_LOG", "CORRELATION_SPIKE"
    log_id: str
    timestamp: datetime
    service_name: str
    log_level: str
    snippet: str
    correlation_rule: str  # "TRACE_LINKED", "SPAN_LINKED", "REQUEST_LINKED", "CORRELATION_ID_LINKED", "TEMPORAL_CASCADE", "LOCAL_SERVICE"
    rationale: str
    content_hash: Optional[str] = None
    trace_id: Optional[str] = None
    relationship_type: Optional[str] = None
    correlation_confidence: Optional[float] = None


@dataclass
class CorrelatedAffectedService:
    """Blast radius and role of an individual service in a correlated incident."""

    service_name: str
    impact_level: str  # "PRIMARY_CULPRIT", "DOWNSTREAM_CASCADE", "CORRELATED_ANOMALY"
    first_impacted_at: datetime
    error_count: int


@dataclass
class CorrelatedIncidentCandidate:
    """A deterministic, evidence-backed candidate incident ready for triage or persistence."""

    candidate_id: str
    title: str
    severity: str
    started_at: datetime
    ended_at: datetime
    correlation_rules_used: List[str]
    is_root_cause_confirmed: bool  # Always False at this correlation stage
    uncertainty_reason: str
    affected_services: List[CorrelatedAffectedService]
    timeline_events: List[CorrelatedTimelineEvent]
    evidence_items: List[CorrelatedEvidenceItem]
    cluster_ids: List[str]
    assessment: Optional[Any] = None
    rca_synthesis: Optional[Any] = None


def _min_event_time_diff(c1: LogCluster, c2: LogCluster) -> float:
    """Compute the minimum time delta in seconds between any event in c1 and any event in c2.

    Uses two-pointer traversal on chronologically sorted events: O(|c1| + |c2|).
    """
    if not c1.events or not c2.events:
        return abs((c1.first_seen - c2.first_seen).total_seconds())

    i, j = 0, 0
    ev1, ev2 = c1.events, c2.events
    min_diff = float("inf")

    while i < len(ev1) and j < len(ev2):
        diff = (ev1[i].timestamp - ev2[j].timestamp).total_seconds()
        abs_diff = abs(diff)
        if abs_diff < min_diff:
            min_diff = abs_diff
        if min_diff == 0.0:
            break
        if diff < 0:
            i += 1
        else:
            j += 1

    return min_diff


def deduplicate_evidence_items(items: List[Any]) -> List[Any]:
    """Deterministically deduplicate evidence items without losing legitimate provenance.

    Deduplication rules:
    - Stable identity: (log_id, evidence_type, correlation_rule).
    - True duplicates (compatible provenance) are merged into a single item,
      retaining non-empty content_hash, trace_id, and the richest rationale.
    - Distinct log IDs with identical message content remain separate.
    - Distinct evidence types (e.g. CULPRIT_LOG vs CORRELATION_SPIKE) remain separate.
    - Distinct correlation rules (e.g. FIRST_FAILURE vs TRACE_LINKED) remain separate.
    - Conflicting provenance (differing non-null content_hash, trace_id, or timestamp)
      is handled conservatively by preserving both records rather than discarding either.
    - Canonical sort ensures invariance to input ordering.
    """
    if not items:
        return []

    # 1. Canonical sort for input-order invariance
    def _sort_key(item: Any) -> Tuple[datetime, str, str, str, str, str, str, str]:
        ts = getattr(item, "timestamp", None)
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (
            ts or datetime.min.replace(tzinfo=timezone.utc),
            str(getattr(item, "service_name", "") or ""),
            str(getattr(item, "log_id", "") or ""),
            str(getattr(item, "evidence_type", "") or ""),
            str(getattr(item, "correlation_rule", "") or ""),
            str(getattr(item, "content_hash", "") or ""),
            str(getattr(item, "trace_id", "") or ""),
            str(getattr(item, "rationale", "") or ""),
        )

    canonical_items = sorted(items, key=_sort_key)

    # 2. Group items with compatibility and conflict resolution
    groups: List[Tuple[Dict[str, Any], type]] = []

    for item in canonical_items:
        lid = str(getattr(item, "log_id", "") or "")
        etype = str(getattr(item, "evidence_type", "") or "")
        crule = str(getattr(item, "correlation_rule", "") or "")
        chash = getattr(item, "content_hash", None)
        tid = getattr(item, "trace_id", None)
        ts = getattr(item, "timestamp", None)
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        svc = str(getattr(item, "service_name", "") or "")
        lvl = str(getattr(item, "log_level", "") or "")
        snp = str(getattr(item, "snippet", "") or "")
        rat = str(getattr(item, "rationale", "") or "")

        item_type = type(item)

        merged = False
        for g_data, _ in groups:
            # Check primary identity
            if g_data["log_id"] == lid and g_data["evidence_type"] == etype and g_data["correlation_rule"] == crule:
                # Provenance compatibility check
                hash_conflict = (
                    chash is not None
                    and g_data["content_hash"] is not None
                    and chash != g_data["content_hash"]
                )
                trace_conflict = (
                    tid is not None
                    and g_data["trace_id"] is not None
                    and tid != g_data["trace_id"]
                )
                time_conflict = (
                    ts is not None
                    and g_data["timestamp"] is not None
                    and ts != g_data["timestamp"]
                )

                if not (hash_conflict or trace_conflict or time_conflict):
                    # Compatible: merge into this group
                    if not g_data["content_hash"] and chash:
                        g_data["content_hash"] = chash
                    if not g_data["trace_id"] and tid:
                        g_data["trace_id"] = tid
                    if not g_data["timestamp"] and ts:
                        g_data["timestamp"] = ts
                    if len(snp) > len(g_data["snippet"]):
                        g_data["snippet"] = snp
                    if len(rat) > len(g_data["rationale"]):
                        g_data["rationale"] = rat
                    merged = True
                    break

        if not merged:
            groups.append((
                {
                    "log_id": lid,
                    "evidence_type": etype,
                    "correlation_rule": crule,
                    "timestamp": ts,
                    "service_name": svc,
                    "log_level": lvl,
                    "snippet": snp,
                    "rationale": rat,
                    "content_hash": chash,
                    "trace_id": tid,
                    "relationship_type": getattr(item, "relationship_type", None),
                    "correlation_confidence": getattr(item, "correlation_confidence", None),
                },
                item_type,
            ))

    # 3. Instantiate returned items using their original class
    result: List[Any] = []
    for g_data, cls in groups:
        init_kwargs = {
            "evidence_type": g_data["evidence_type"],
            "log_id": g_data["log_id"],
            "timestamp": g_data["timestamp"],
            "service_name": g_data["service_name"],
            "log_level": g_data["log_level"],
            "snippet": g_data["snippet"],
            "correlation_rule": g_data["correlation_rule"],
            "rationale": g_data["rationale"],
            "content_hash": g_data["content_hash"],
            "trace_id": g_data["trace_id"],
        }
        if hasattr(cls, "__dataclass_fields__") and "relationship_type" in cls.__dataclass_fields__:
            init_kwargs["relationship_type"] = g_data.get("relationship_type")
            init_kwargs["correlation_confidence"] = g_data.get("correlation_confidence")
        elif hasattr(cls, "model_fields") and "relationship_type" in cls.model_fields:
            init_kwargs["relationship_type"] = g_data.get("relationship_type")
            init_kwargs["correlation_confidence"] = g_data.get("correlation_confidence")

        result.append(cls(**init_kwargs))

    return result


# Keys inspected for request_id and correlation_id in normalized attributes
_REQUEST_ID_KEYS = (
    "request_id",
    "requestId",
    "request.id",
    "x-request-id",
    "x_request_id",
)
_CORRELATION_ID_KEYS = (
    "correlation_id",
    "correlationId",
    "correlation.id",
    "x-correlation-id",
    "x_correlation_id",
)


def extract_request_id(attributes: Dict[str, Any]) -> Optional[str]:
    """Safely extract request_id from normalized attributes.

    Returns non-empty, stripped string or None. Rejects empty, whitespace-only,
    and non-string values. Namespace is kept strictly distinct from correlation_id.
    """
    if not isinstance(attributes, dict):
        return None
    # 1. Exact match
    for k in _REQUEST_ID_KEYS:
        if k in attributes:
            val = attributes[k]
            if isinstance(val, str) and val.strip():
                return val.strip()
    # 2. Case-insensitive fallback
    for k, val in attributes.items():
        if isinstance(k, str) and k.lower() in ("request_id", "requestid", "request.id", "x-request-id", "x_request_id"):
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def extract_correlation_id(attributes: Dict[str, Any]) -> Optional[str]:
    """Safely extract correlation_id from normalized attributes.

    Returns non-empty, stripped string or None. Rejects empty, whitespace-only,
    and non-string values. Namespace is kept strictly distinct from request_id.
    """
    if not isinstance(attributes, dict):
        return None
    # 1. Exact match
    for k in _CORRELATION_ID_KEYS:
        if k in attributes:
            val = attributes[k]
            if isinstance(val, str) and val.strip():
                return val.strip()
    # 2. Case-insensitive fallback
    for k, val in attributes.items():
        if isinstance(k, str) and k.lower() in ("correlation_id", "correlationid", "correlation.id", "x-correlation-id", "x_correlation_id"):
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _get_cluster_request_ids(cl: LogCluster) -> Set[str]:
    """Collect valid request_id values from cluster events where trace_id is absent."""
    req_ids: Set[str] = set()
    for ev in cl.events:
        if not ev.trace_id:
            rid = extract_request_id(ev.attributes)
            if rid:
                req_ids.add(rid)
    return req_ids


def _get_cluster_correlation_ids(cl: LogCluster) -> Set[str]:
    """Collect valid correlation_id values from cluster events where trace_id is absent."""
    corr_ids: Set[str] = set()
    for ev in cl.events:
        if not ev.trace_id:
            cid = extract_correlation_id(ev.attributes)
            if cid:
                corr_ids.add(cid)
    return corr_ids


def _build_cluster_edges(
    clusters: List[LogCluster],
    cascade_window_seconds: int = DEFAULT_CASCADE_WINDOW_SECONDS,
) -> Dict[int, Set[Tuple[int, str, str]]]:
    """Build an adjacency list connecting clusters based on deterministic correlation rules.

    Edges: (target_cluster_idx, rule_name, rationale)
    """
    n = len(clusters)
    adj: Dict[int, Set[Tuple[int, str, str]]] = {i: set() for i in range(n)}

    for i in range(n):
        c1 = clusters[i]
        c1_traces = set(c1.trace_ids)
        c1_spans = set(c1.span_ids)

        for j in range(i + 1, n):
            c2 = clusters[j]
            c2_traces = set(c2.trace_ids)
            c2_spans = set(c2.span_ids)

            # Rule 1: TRACE_LINKED (distributed tracing ground truth)
            shared_traces = c1_traces & c2_traces
            if shared_traces:
                trace_val = sorted(list(shared_traces))[0]
                rationale = (
                    f"Correlated via shared distributed trace_id '{trace_val}' "
                    f"between '{c1.service_name}' and '{c2.service_name}'"
                )
                adj[i].add((j, "TRACE_LINKED", rationale))
                adj[j].add((i, "TRACE_LINKED", rationale))
                continue

            # Rule 2: SPAN_LINKED (meaningful call linkage within tight time boundary)
            shared_spans = c1_spans & c2_spans
            if shared_spans:
                time_diff = abs((c1.first_seen - c2.first_seen).total_seconds())
                if time_diff <= MAX_SPAN_CORRELATION_SECONDS:
                    span_val = sorted(list(shared_spans))[0]
                    rationale = (
                        f"Correlated via shared span_id '{span_val}' within "
                        f"{time_diff:.1f}s communication window"
                    )
                    adj[i].add((j, "SPAN_LINKED", rationale))
                    adj[j].add((i, "SPAN_LINKED", rationale))
                    continue

            # Rule 3: REQUEST_LINKED (fallback request identifier when trace_id is absent)
            c1_req_ids = _get_cluster_request_ids(c1)
            c2_req_ids = _get_cluster_request_ids(c2)
            shared_requests = c1_req_ids & c2_req_ids
            if shared_requests:
                req_val = sorted(list(shared_requests))[0]
                rationale = (
                    f"Correlated via shared request_id '{req_val}' "
                    f"between '{c1.service_name}' and '{c2.service_name}'"
                )
                adj[i].add((j, "REQUEST_LINKED", rationale))
                adj[j].add((i, "REQUEST_LINKED", rationale))
                continue

            # Rule 4: CORRELATION_ID_LINKED (fallback correlation identifier when trace_id is absent)
            c1_corr_ids = _get_cluster_correlation_ids(c1)
            c2_corr_ids = _get_cluster_correlation_ids(c2)
            shared_correlations = c1_corr_ids & c2_corr_ids
            if shared_correlations:
                corr_val = sorted(list(shared_correlations))[0]
                rationale = (
                    f"Correlated via shared correlation_id '{corr_val}' "
                    f"between '{c1.service_name}' and '{c2.service_name}'"
                )
                adj[i].add((j, "CORRELATION_ID_LINKED", rationale))
                adj[j].add((i, "CORRELATION_ID_LINKED", rationale))
                continue

            # Strict Invariant: If both clusters carry explicit non-overlapping trace IDs,
            # they are proven to belong to disjoint distributed transactions.
            # Never heuristically merge disjoint traces via temporal proximity!
            has_disjoint_traces = bool(c1_traces and c2_traces and not shared_traces)
            if has_disjoint_traces:
                continue

            time_diff = _min_event_time_diff(c1, c2)

            # Rule 5A: LOCAL_SERVICE (temporal correlation within the same service)
            if c1.service_name == c2.service_name:
                if time_diff <= cascade_window_seconds:
                    rationale = (
                        f"Local service correlation: '{c1.service_name}' error clusters observed "
                        f"{time_diff:.1f}s apart within {cascade_window_seconds}s window"
                    )
                    adj[i].add((j, "LOCAL_SERVICE", rationale))
                    adj[j].add((i, "LOCAL_SERVICE", rationale))
                    continue

            # Rule 5B: TEMPORAL_CASCADE (heuristic cross-service correlation when traces absent)
            if c1.service_name != c2.service_name:
                if time_diff <= cascade_window_seconds:
                    if c1.first_seen < c2.first_seen:
                        earlier_svc, later_svc = c1.service_name, c2.service_name
                    elif c2.first_seen < c1.first_seen:
                        earlier_svc, later_svc = c2.service_name, c1.service_name
                    else:
                        earlier_svc = min(c1.service_name, c2.service_name)
                        later_svc = max(c1.service_name, c2.service_name)

                    rationale = (
                        f"Heuristic temporal correlation: '{later_svc}' error cluster observed "
                        f"{time_diff:.1f}s after '{earlier_svc}' (cascade window: {cascade_window_seconds}s)"
                    )
                    adj[i].add((j, "TEMPORAL_CASCADE", rationale))
                    adj[j].add((i, "TEMPORAL_CASCADE", rationale))

    return adj


def _find_connected_components(
    adj: Dict[int, Set[Tuple[int, str, str]]]
) -> List[Tuple[List[int], List[Tuple[str, str]]]]:
    """Partition cluster indices into connected component groups with their link rationales."""
    visited = set()
    components = []

    for node in sorted(adj.keys()):
        if node not in visited:
            comp_nodes = []
            comp_edges = []
            queue = [node]
            visited.add(node)

            while queue:
                curr = queue.pop(0)
                comp_nodes.append(curr)
                for neighbor, rule, rationale in sorted(adj[curr], key=lambda x: (x[0], x[1])):
                    comp_edges.append((rule, rationale))
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            components.append((comp_nodes, comp_edges))

    return components


def reconstruct_incident_candidate(
    component_clusters: List[LogCluster],
    edge_rules: List[Tuple[str, str]],
) -> CorrelatedIncidentCandidate:
    """Assemble a single CorrelatedIncidentCandidate from correlated clusters."""
    # 1. Flatten all events from member clusters
    all_events: List[Tuple[LogEventItem, str]] = []
    for cl in component_clusters:
        for ev in cl.events:
            all_events.append((ev, cl.cluster_id))

    # 2. Sort all events chronologically with deterministic tie-breakers (timestamp ASC, service ASC, log_id ASC)
    all_events.sort(key=lambda pair: (pair[0].timestamp, pair[0].service_name, pair[0].log_id))

    if not all_events:
        raise ValueError("Cannot reconstruct candidate from empty cluster events.")

    earliest_event, _ = all_events[0]
    started_at = earliest_event.timestamp
    ended_at = all_events[-1][0].timestamp

    # 3. Identify rules used
    rules_used = sorted(list({rule for rule, _ in edge_rules}))
    if any("cl-logbert-" in cl.cluster_id for cl in component_clusters):
        if "SEQUENCE_ANOMALY" not in rules_used:
            rules_used.append("SEQUENCE_ANOMALY")
            rules_used.sort()
    if not rules_used:
        rules_used = ["LOCAL_SERVICE"]

    # 4. Identify earliest observed error/fatal failure
    earliest_failure_idx = None
    for idx, (ev, _) in enumerate(all_events):
        if ev.log_level in ("FATAL", "ERROR", "CRITICAL"):
            earliest_failure_idx = idx
            break

    # If no error/fatal (rare fallback), default to first event
    if earliest_failure_idx is None:
        earliest_failure_idx = 0

    first_failure_event = all_events[earliest_failure_idx][0]
    primary_culprit_service = (first_failure_event.service_name or "").strip() or "unassigned"

    # 5. Reconstruct Chronological Timeline Events
    timeline_events: List[CorrelatedTimelineEvent] = []
    for seq_order, (ev, cl_id) in enumerate(all_events):
        is_first_failure = (seq_order == earliest_failure_idx)
        ev_svc = (ev.service_name or "").strip() or "unassigned"

        # Calculate propagation lag relative to the first failure event
        lag_seconds = 0.0
        if ev.timestamp >= first_failure_event.timestamp:
            lag_seconds = round((ev.timestamp - first_failure_event.timestamp).total_seconds(), 3)

        # Default event classification and summary
        event_type: str = "ANOMALY_SPIKE"
        summary: str = f"Subsequent error in '{ev_svc}': {ev.message[:120]}"

        if ev.attributes.get("model") == "LogBERT":
            event_type = "SEQUENCE_ANOMALY"
            score = float(ev.attributes.get("anomaly_score") or 0.0)
            is_mismatch = bool(ev.attributes.get("is_mlkp_mismatch", False))
            exp_type = str(ev.attributes.get("explanation_type") or ("TOKEN_MISMATCH" if is_mismatch else "EMBEDDING_DISTANCE"))
            mismatch_tag = " [MLKP mismatch]" if is_mismatch else (" [VHM embedding]" if exp_type == "EMBEDDING_DISTANCE" else "")
            summary = (
                f"LogBERT sequence anomaly in '{ev_svc}' "
                f"(score: {score:.4f}{mismatch_tag}): {ev.message[:120]}"
            )
        elif is_first_failure:
            event_type = "FIRST_ERROR"
            summary = f"Earliest observed failure recorded in service '{ev_svc}': {ev.message[:120]}"
        elif ev_svc != primary_culprit_service:
            event_type = "CASCADING_FAILURE"
            summary = (
                f"Downstream error in '{ev_svc}' ({lag_seconds:.1f}s after initial failure): "
                f"{ev.message[:120]}"
            )

        # Determine relationship type and correlation confidence
        rel_type = "TEMPORAL_ASSOCIATION"
        confidence_val = 0.35
        if ev.trace_id and any("TRACE_LINKED" in r for r in rules_used):
            rel_type = "TRACE_BASED"
            confidence_val = 1.0
        elif ev.span_id and any("SPAN_LINKED" in r for r in rules_used):
            rel_type = "SPAN_BASED"
            confidence_val = 0.8
        elif any(r in ("REQUEST_LINKED", "CORRELATION_ID_LINKED") for r in rules_used):
            rel_type = "REQUEST_BASED"
            confidence_val = 0.7
        elif any("TEMPORAL_CASCADE" in r for r in rules_used):
            rel_type = "SUSPECTED_DEPENDENCY" if not is_first_failure else "TEMPORAL_ASSOCIATION"
            confidence_val = 0.35
        elif "LOCAL_SERVICE" in rules_used or ev_svc == primary_culprit_service:
            rel_type = "SERVICE_LOCAL"
            confidence_val = 0.6

        missing_telemetry: List[str] = []
        if not ev.trace_id:
            missing_telemetry.append("missing_trace_id")
        if not ev.span_id:
            missing_telemetry.append("missing_span_id")
        if ev_svc in ("unassigned", "unknown", ""):
            missing_telemetry.append("unassigned_service")

        timeline_events.append(
            CorrelatedTimelineEvent(
                sequence_order=seq_order,
                event_timestamp=ev.timestamp,
                service_name=ev_svc,
                event_type=event_type,
                severity=ev.log_level,
                summary=summary,
                is_earliest_observed_failure=is_first_failure,
                propagation_lag_seconds=lag_seconds,
                cluster_id=cl_id,
                trace_id=ev.trace_id,
                span_id=ev.span_id,
                sample_log_id=ev.log_id,
                attributes={
                    "is_earliest_observed_failure": is_first_failure,
                    "propagation_lag_seconds": lag_seconds,
                    "environment": ev.environment,
                    "host": ev.host,
                    "relationship_type": rel_type,
                    "correlation_confidence": confidence_val,
                    "missing_telemetry": missing_telemetry,
                },
            )
        )

    # 6. Reconstruct Affected Services (Blast Radius)
    services_map: Dict[str, Dict[str, Any]] = {}
    for ev, _ in all_events:
        svc = (ev.service_name or "").strip() or "unassigned"
        if svc not in services_map:
            services_map[svc] = {
                "first_impacted_at": ev.timestamp,
                "error_count": 0,
            }
        services_map[svc]["error_count"] += 1
        if ev.timestamp < services_map[svc]["first_impacted_at"]:
            services_map[svc]["first_impacted_at"] = ev.timestamp

    affected_services: List[CorrelatedAffectedService] = []
    for svc, data in sorted(services_map.items()):
        if svc == primary_culprit_service:
            impact_level = "PRIMARY_CULPRIT"
        elif any(r in rules_used for r in ("TRACE_LINKED", "SPAN_LINKED", "REQUEST_LINKED", "CORRELATION_ID_LINKED")):
            impact_level = "DOWNSTREAM_CASCADE"
        else:
            impact_level = "CORRELATED_ANOMALY"

        affected_services.append(
            CorrelatedAffectedService(
                service_name=svc,
                impact_level=impact_level,
                first_impacted_at=data["first_impacted_at"],
                error_count=data["error_count"],
            )
        )

    # 7. Collect Evidence Items with provenance and rationale
    raw_evidence_items: List[CorrelatedEvidenceItem] = []

    # Create an evidence item for the first failure
    content_hash = (
        hashlib.sha256(first_failure_event.raw_log.encode("utf-8")).hexdigest()
        if first_failure_event.raw_log
        else None
    )
    is_logbert_first = first_failure_event.attributes.get("model") == "LogBERT"
    first_score = float(first_failure_event.attributes.get("anomaly_score") or 0.0)
    first_is_mismatch = bool(first_failure_event.attributes.get("is_mlkp_mismatch", False))
    first_top_cands = first_failure_event.attributes.get("mlkp_top_candidates", [])
    first_cand_str = ""
    if first_is_mismatch and first_top_cands:
        first_cand_str = f" [MLKP mismatch, expected: {', '.join(c['event'] for c in first_top_cands[:3])}]"
    elif not first_is_mismatch and is_logbert_first:
        first_cand_str = " [VHM embedding distance]"

    first_rel_type = "SERVICE_LOCAL"
    first_conf = 0.6
    if any("TRACE_LINKED" in r for r in rules_used) and first_failure_event.trace_id:
        first_rel_type = "TRACE_BASED"
        first_conf = 1.0
    elif any(r in ("REQUEST_LINKED", "CORRELATION_ID_LINKED") for r in rules_used):
        first_rel_type = "REQUEST_BASED"
        first_conf = 0.7
    elif any("TEMPORAL_CASCADE" in r for r in rules_used):
        first_rel_type = "TEMPORAL_ASSOCIATION"
        first_conf = 0.35

    raw_evidence_items.append(
        CorrelatedEvidenceItem(
            evidence_type="SEQUENCE_ANOMALY" if is_logbert_first else "CULPRIT_LOG",
            log_id=first_failure_event.log_id,
            timestamp=first_failure_event.timestamp,
            service_name=primary_culprit_service,
            log_level=first_failure_event.log_level,
            snippet=first_failure_event.message[:500],
            correlation_rule="SEQUENCE_ANOMALY" if is_logbert_first else "FIRST_FAILURE",
            rationale=(
                f"LogBERT sequence anomaly in service '{primary_culprit_service}' "
                f"(score: {first_score:.4f}){first_cand_str}"
                if is_logbert_first
                else f"Earliest observed failure event in service '{primary_culprit_service}' at {first_failure_event.timestamp.isoformat()}"
            ),
            content_hash=content_hash,
            trace_id=first_failure_event.trace_id,
            relationship_type=first_rel_type,
            correlation_confidence=first_conf,
        )
    )

    # Add evidence for sample logs across clusters (sorted deterministically)
    sorted_clusters = sorted(
        component_clusters,
        key=lambda c: (c.first_seen, c.service_name, c.cluster_id),
    )
    for cl in sorted_clusters:
        for ev in cl.events[:3]:  # sample representative logs per cluster
            # Avoid duplicate generic sample for the first failure log
            if ev.log_id == first_failure_event.log_id:
                continue
            ev_svc = (ev.service_name or "").strip() or "unassigned"
            e_hash = (
                hashlib.sha256(ev.raw_log.encode("utf-8")).hexdigest()
                if ev.raw_log
                else None
            )
            # Determine rationale for this evidence item
            req_id = extract_request_id(ev.attributes) if not ev.trace_id else None
            corr_id = extract_correlation_id(ev.attributes) if not ev.trace_id else None

            # Safe initial defaults for evidence attributes
            ev_type: str = "CORRELATION_SPIKE"
            ev_rule: str = "LOCAL_SERVICE"
            ev_rationale: str = f"Error event in service '{ev_svc}'"
            ev_rel_type: str = "SERVICE_LOCAL"
            ev_confidence: float = 0.6

            if ev.attributes.get("model") == "LogBERT" or "cl-logbert-" in cl.cluster_id:
                ev_type = "SEQUENCE_ANOMALY"
                ev_rule = "SEQUENCE_ANOMALY"
                score = float(ev.attributes.get("anomaly_score") or 0.0)
                thresh = float(ev.attributes.get("threshold") or 0.0388)
                is_mismatch = bool(ev.attributes.get("is_mlkp_mismatch", False))
                top_cands = ev.attributes.get("mlkp_top_candidates", [])
                cand_str = ""
                if is_mismatch and top_cands:
                    cand_str = f" [MLKP mismatch, expected: {', '.join(c['event'] for c in top_cands[:3])}]"
                elif not is_mismatch and ev.attributes.get("explanation_type") == "EMBEDDING_DISTANCE":
                    cand_str = " [VHM embedding distance]"
                ev_rationale = (
                    f"LogBERT sequence anomaly in '{ev_svc}' "
                    f"(score: {score:.4f}, threshold: {thresh:.4f}){cand_str}"
                )
                ev_rel_type = "TRACE_BASED" if ev.trace_id else "SERVICE_LOCAL"
                ev_confidence = min(1.0, round(score * 10, 2)) if score > 0 else 0.5
            elif ev.trace_id and any("TRACE_LINKED" in r for r in rules_used):
                ev_rule = "TRACE_LINKED"
                ev_rationale = f"Correlated event sharing trace_id '{ev.trace_id}'"
                ev_rel_type = "TRACE_BASED"
                ev_confidence = 1.0
            elif ev.span_id and any("SPAN_LINKED" in r for r in rules_used):
                ev_rule = "SPAN_LINKED"
                ev_rationale = f"Correlated event sharing span_id '{ev.span_id}'"
                ev_rel_type = "SPAN_BASED"
                ev_confidence = 0.8
            elif req_id and any("REQUEST_LINKED" in r for r in rules_used):
                ev_rule = "REQUEST_LINKED"
                ev_rationale = f"Correlated event sharing request_id '{req_id}'"
                ev_rel_type = "REQUEST_BASED"
                ev_confidence = 0.7
            elif corr_id and any("CORRELATION_ID_LINKED" in r for r in rules_used):
                ev_rule = "CORRELATION_ID_LINKED"
                ev_rationale = f"Correlated event sharing correlation_id '{corr_id}'"
                ev_rel_type = "REQUEST_BASED"
                ev_confidence = 0.7
            elif any("TEMPORAL_CASCADE" in r for r in rules_used):
                ev_rule = "TEMPORAL_CASCADE"
                ev_rationale = (
                    f"Correlated cascade event in '{ev_svc}' within temporal window"
                )
                ev_rel_type = "SUSPECTED_DEPENDENCY" if ev_svc != primary_culprit_service else "TEMPORAL_ASSOCIATION"
                ev_confidence = 0.35
            else:
                ev_rule = "LOCAL_SERVICE"
                ev_rationale = f"Error event in service '{ev_svc}'"
                ev_rel_type = "SERVICE_LOCAL"
                ev_confidence = 0.6

            raw_evidence_items.append(
                CorrelatedEvidenceItem(
                    evidence_type=ev_type,
                    log_id=ev.log_id,
                    timestamp=ev.timestamp,
                    service_name=ev_svc,
                    log_level=ev.log_level,
                    snippet=ev.message[:500],
                    correlation_rule=ev_rule,
                    rationale=ev_rationale,
                    content_hash=e_hash,
                    trace_id=ev.trace_id,
                    relationship_type=ev_rel_type,
                    correlation_confidence=ev_confidence,
                )
            )

    # Deterministically deduplicate evidence items while preserving distinct provenance
    evidence_items: List[CorrelatedEvidenceItem] = deduplicate_evidence_items(raw_evidence_items)

    # 8. Title, Severity, Candidate ID
    has_fatal = any(cl.severity == "FATAL" for cl in component_clusters)
    is_multi_service = len(services_map) > 1

    if has_fatal:
        overall_severity = "CRITICAL"
    elif is_multi_service:
        overall_severity = "HIGH"
    else:
        overall_severity = "MEDIUM"

    distinct_services = sorted(list(services_map.keys()))
    has_error_logs = any(ev.log_level in ("FATAL", "ERROR", "CRITICAL") for ev, _ in all_events)
    if is_multi_service:
        title = (
            f"Cross-service failure cascade initiated by '{primary_culprit_service}' "
            f"impacting {', '.join(s for s in distinct_services if s != primary_culprit_service)}"
        )
    elif not has_error_logs and any(ev.attributes.get("model") == "LogBERT" for ev, _ in all_events):
        title = f"LogBERT detected anomalous sequence in '{primary_culprit_service}'"
    else:
        title = f"Correlated error surge in '{primary_culprit_service}'"

    # Deterministic candidate identifier
    time_str = started_at.strftime("%Y%m%d%H%M%S")
    hash_seed = f"{primary_culprit_service}-{len(distinct_services)}-{started_at.isoformat()}"
    cid_hash = hashlib.sha256(hash_seed.encode("utf-8")).hexdigest()[:8]
    candidate_id = f"cand-{primary_culprit_service}-{time_str}-{cid_hash}"

    # 8. Deterministic Evidence Sufficiency Assessment & RCA Synthesis
    assessment = evaluate_evidence_sufficiency(
        evidence_items=evidence_items,
        timeline_events=timeline_events,
    )
    rca_synthesis = synthesize_rca_findings(
        assessment=assessment,
        evidence_items=evidence_items,
        timeline_events=timeline_events,
    )

    # Uncertainty Reason: explicit, truthful disclaimer grounded in evidence assessment
    uncertainty_reason = (
        f"Earliest observed failure identified chronologically in service '{primary_culprit_service}'. "
        f"{assessment.uncertainty_statement}"
    )

    return CorrelatedIncidentCandidate(
        candidate_id=candidate_id,
        title=title,
        severity=overall_severity,
        started_at=started_at,
        ended_at=ended_at,
        correlation_rules_used=rules_used,
        is_root_cause_confirmed=False,
        uncertainty_reason=uncertainty_reason,
        affected_services=affected_services,
        timeline_events=timeline_events,
        evidence_items=evidence_items,
        cluster_ids=[cl.cluster_id for cl in component_clusters],
        assessment=assessment,
        rca_synthesis=rca_synthesis,
    )


def correlate_clusters(
    clusters: List[LogCluster],
    cascade_window_seconds: int = DEFAULT_CASCADE_WINDOW_SECONDS,
) -> List[CorrelatedIncidentCandidate]:
    """Correlate log clusters across services into distinct incident candidates.

    Deterministic rules:
    - TRACE_LINKED: Merges clusters sharing a distributed trace_id.
    - SPAN_LINKED: Merges clusters sharing a span_id within a tight 10s communication window.
    - TEMPORAL_CASCADE: When traces are absent, merges clusters across distinct services within
      cascade_window_seconds (default: 120s).
    - Unrelated clusters outside cascade_window_seconds remain independent candidates.
    - Preserves earliest-observed-failure flag, propagation lag, and verifiable evidence provenance.
    """
    if not clusters:
        return []

    # 1. Build adjacency graph between clusters
    adj = _build_cluster_edges(clusters, cascade_window_seconds=cascade_window_seconds)

    # 2. Extract connected components
    components = _find_connected_components(adj)

    # 3. Assemble incident candidates from components
    candidates: List[CorrelatedIncidentCandidate] = []
    for comp_indices, comp_edges in components:
        comp_clusters = [clusters[idx] for idx in comp_indices]
        candidate = reconstruct_incident_candidate(comp_clusters, comp_edges)
        candidates.append(candidate)

    # Sort candidates chronologically descending by start time with deterministic tie-breaker
    candidates.sort(key=lambda c: (c.started_at, c.candidate_id), reverse=True)
    return candidates


@dataclass
class IncidentGroupingEvaluation:
    """Evaluation result for determining whether an event belongs to an existing incident."""

    matches: bool
    incident_id: str
    match_rule: str  # "TRACE_MATCH", "SAME_SERVICE_ACTIVE_WINDOW", "TEMPORAL_CASCADE_ACTIVE_WINDOW", "OUTSIDE_WINDOW", "DISJOINT_TRACE_CONFLICT", "UNRELATED_SERVICE", "DUPLICATE_LOG_RECORD"
    confidence: float
    rationale: str
    is_duplicate_log: bool = False
    relationship_type: Optional[str] = None


def evaluate_incident_grouping(
    incident: Any,
    event: LogEventItem,
    cascade_window_seconds: int = DEFAULT_CASCADE_WINDOW_SECONDS,
) -> IncidentGroupingEvaluation:
    """Determine whether an incoming log event or anomaly belongs to an existing incident.

    Guarantees:
    - Avoids duplicate event registration (returns is_duplicate_log=True if log_id already present).
    - Preserves trace boundaries: disjoint non-overlapping traces strictly reject grouping.
    - Preserves temporal boundaries: events outside the active cascade window are rejected.
    - Preserves incident invariants: correlation does not alter confirmation status.
    """
    from datetime import timedelta
    inc_id_str = str(getattr(incident, "id", "") or "unknown")

    # 1. Check for duplicate log record in existing evidence or events
    existing_evidence = getattr(incident, "evidence", []) or []
    for ev in existing_evidence:
        doc_id = getattr(ev, "opensearch_doc_id", None) or getattr(ev, "log_id", None)
        if doc_id and str(doc_id) == str(event.log_id):
            return IncidentGroupingEvaluation(
                matches=True,
                incident_id=inc_id_str,
                match_rule="DUPLICATE_LOG_RECORD",
                confidence=1.0,
                rationale=f"Log record '{event.log_id}' is already present in incident {inc_id_str} evidence.",
                is_duplicate_log=True,
                relationship_type=str((getattr(ev, "metadata_", {}) or {}).get("relationship_type") or "EXACT_DUPLICATE"),
            )

    # 2. Distributed Trace Context Evaluation
    incident_traces: Set[str] = set()
    for ev in getattr(incident, "events", []) or []:
        tid = getattr(ev, "trace_id", None) or (getattr(ev, "attributes", {}) or {}).get("trace_id")
        if tid:
            incident_traces.add(str(tid))
    for ev in existing_evidence:
        tid = getattr(ev, "trace_id", None) or (getattr(ev, "metadata_", {}) or {}).get("trace_id")
        if tid:
            incident_traces.add(str(tid))

    if event.trace_id:
        if event.trace_id in incident_traces:
            return IncidentGroupingEvaluation(
                matches=True,
                incident_id=inc_id_str,
                match_rule="TRACE_MATCH",
                confidence=1.0,
                rationale=f"Event shares distributed trace_id '{event.trace_id}' with incident {inc_id_str}.",
                is_duplicate_log=False,
                relationship_type="TRACE_BASED",
            )
        elif incident_traces:
            # Conflicting non-empty trace: strictly cannot belong to this incident
            return IncidentGroupingEvaluation(
                matches=False,
                incident_id=inc_id_str,
                match_rule="DISJOINT_TRACE_CONFLICT",
                confidence=0.0,
                rationale=(
                    f"Event trace_id '{event.trace_id}' does not match any of the incident's "
                    f"known distributed traces {sorted(list(incident_traces))}."
                ),
                is_duplicate_log=False,
                relationship_type=None,
            )

    # 3. Temporal Boundary Evaluation
    started_at = getattr(incident, "started_at", None)
    if started_at is not None and started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    ended_at = getattr(incident, "ended_at", None) or started_at
    if ended_at is not None and ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=timezone.utc)

    ev_ts = event.timestamp
    if ev_ts.tzinfo is None:
        ev_ts = ev_ts.replace(tzinfo=timezone.utc)

    if started_at is not None:
        effective_end = ended_at if ended_at is not None else started_at
        # Bounded incident window: 30s before start to cascade_window_seconds after ended_at
        min_allowed = started_at - timedelta(seconds=30)
        max_allowed = effective_end + timedelta(seconds=cascade_window_seconds)

        if ev_ts < min_allowed or ev_ts > max_allowed:
            return IncidentGroupingEvaluation(
                matches=False,
                incident_id=inc_id_str,
                match_rule="OUTSIDE_WINDOW",
                confidence=0.0,
                rationale=(
                    f"Event timestamp ({ev_ts.isoformat()}) falls outside incident {inc_id_str} "
                    f"temporal window ({min_allowed.isoformat()} to {max_allowed.isoformat()})."
                ),
                is_duplicate_log=False,
                relationship_type=None,
            )

    # 4. Service-Level Correlation Evaluation
    affected_services: Set[str] = {
        str(getattr(s, "service_name", ""))
        for s in (getattr(incident, "affected_services", []) or [])
    }
    primary_svc = (
        getattr(incident, "primary_service", None)
        or (getattr(getattr(incident, "primary_log_source", None), "service_name", None))
    )
    if primary_svc:
        affected_services.add(str(primary_svc))

    event_svc = (event.service_name or "unassigned").strip()

    if event_svc and event_svc in affected_services:
        return IncidentGroupingEvaluation(
            matches=True,
            incident_id=inc_id_str,
            match_rule="SAME_SERVICE_ACTIVE_WINDOW",
            confidence=0.6,
            rationale=f"Event belongs to existing affected service '{event_svc}' within incident temporal boundary.",
            is_duplicate_log=False,
            relationship_type="SERVICE_LOCAL",
        )

    # 5. Cross-Service Temporal Cascade within Active Status
    inc_status = str(getattr(incident, "status", "DETECTED") or "DETECTED").upper()
    if inc_status in ("DETECTED", "INVESTIGATING"):
        # Check proximity to latest event
        events_list = getattr(incident, "events", []) or []
        latest_event_ts: Optional[datetime] = ended_at or started_at
        if events_list:
            ev_timestamps: List[datetime] = []
            for e in events_list:
                ets = getattr(e, "event_timestamp", None)
                if isinstance(ets, datetime):
                    ev_timestamps.append(ets)
            if ev_timestamps:
                latest_event_ts = max(ev_timestamps)
                if latest_event_ts.tzinfo is None:
                    latest_event_ts = latest_event_ts.replace(tzinfo=timezone.utc)

        if latest_event_ts is not None:
            time_delta = abs((ev_ts - latest_event_ts).total_seconds())
            if time_delta <= cascade_window_seconds:
                return IncidentGroupingEvaluation(
                    matches=True,
                    incident_id=inc_id_str,
                    match_rule="TEMPORAL_CASCADE_ACTIVE_WINDOW",
                    confidence=0.35,
                    rationale=(
                        f"Cross-service event in '{event_svc}' occurred within {time_delta:.1f}s of incident "
                        f"activity (cascade limit: {cascade_window_seconds}s). Non-causal temporal association."
                    ),
                    is_duplicate_log=False,
                    relationship_type="TEMPORAL_ASSOCIATION",
                )
            else:
                return IncidentGroupingEvaluation(
                    matches=False,
                    incident_id=inc_id_str,
                    match_rule="OUTSIDE_CASCADE_WINDOW",
                    confidence=0.0,
                    rationale=(
                        f"Cross-service event in '{event_svc}' occurred {time_delta:.1f}s from latest activity, "
                        f"exceeding cascade window ({cascade_window_seconds}s)."
                    ),
                    is_duplicate_log=False,
                    relationship_type=None,
                )

    return IncidentGroupingEvaluation(
        matches=False,
        incident_id=inc_id_str,
        match_rule="UNRELATED_SERVICE",
        confidence=0.0,
        rationale=f"Event in service '{event_svc}' is not associated with inactive/resolved incident {inc_id_str}.",
        is_duplicate_log=False,
        relationship_type=None,
    )
