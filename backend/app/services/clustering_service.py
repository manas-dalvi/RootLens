from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import math
import re
from typing import Any, Dict, List, Optional, Tuple


# Regex patterns for deterministic message normalization / template masking
_UUID_REGEX = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_TIMESTAMP_REGEX = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_IPV4_REGEX = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b")
_HEX_REGEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_QUOTED_STR_REGEX = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_NUMBER_REGEX = re.compile(r"\b\d+\b")
_WHITESPACE_REGEX = re.compile(r"\s+")


def extract_message_pattern(message: str) -> str:
    """Extract a deterministic normalized pattern template from a log message.

    Dynamic parameters such as UUIDs, timestamps, IP addresses, memory addresses,
    numbers, and quoted string literals are masked with standardized placeholders.
    """
    if not message or not message.strip():
        return "<EMPTY_MESSAGE>"

    text = message.strip()
    text = _UUID_REGEX.sub("<UUID>", text)
    text = _TIMESTAMP_REGEX.sub("<TIMESTAMP>", text)
    text = _IPV4_REGEX.sub("<IP>", text)
    text = _HEX_REGEX.sub("<HEX>", text)
    text = _QUOTED_STR_REGEX.sub("<STR>", text)
    text = _NUMBER_REGEX.sub("<NUM>", text)
    text = _WHITESPACE_REGEX.sub(" ", text).strip()
    return text


def compute_pattern_hash(pattern: str) -> str:
    """Calculate a deterministic 16-character SHA-256 fingerprint for a pattern."""
    return hashlib.sha256(pattern.encode("utf-8")).hexdigest()[:16]


def floor_datetime_to_window(dt: datetime, window_minutes: int) -> datetime:
    """Round a datetime down to the nearest bounded window start boundary from UTC epoch."""
    dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    total_seconds = (dt_utc - epoch).total_seconds()
    window_seconds = max(1, window_minutes) * 60
    floored_seconds = math.floor(total_seconds / window_seconds) * window_seconds
    return epoch + timedelta(seconds=floored_seconds)


@dataclass
class LogEventItem:
    """Internal canonical representation of an individual log event for clustering."""

    log_id: str
    timestamp: datetime
    service_name: str
    environment: str = "production"
    log_level: str = "ERROR"
    message: str = ""
    host: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    raw_log: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LogCluster:
    """A deterministic cluster of related log events sharing service, message pattern, and time window."""

    cluster_id: str
    service_name: str
    pattern: str
    pattern_hash: str
    severity: str
    window_start: datetime
    window_end: datetime
    count: int
    log_ids: List[str]
    sample_messages: List[str]
    first_seen: datetime
    last_seen: datetime
    trace_ids: List[str]
    span_ids: List[str]
    events: List[LogEventItem]


def parse_opensearch_hit_to_event(hit: Dict[str, Any]) -> Optional[LogEventItem]:
    """Parse an OpenSearch hit dictionary into a typed LogEventItem."""
    source = hit.get("_source", {})
    doc_id = str(hit.get("_id") or "")
    if not doc_id:
        return None

    raw_ts = source.get("timestamp")
    event_ts: Optional[datetime] = None
    if raw_ts:
        try:
            if isinstance(raw_ts, str):
                event_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            elif isinstance(raw_ts, (int, float)):
                event_ts = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc)
        except Exception:
            event_ts = None

    if not event_ts:
        return None

    if not event_ts.tzinfo:
        event_ts = event_ts.replace(tzinfo=timezone.utc)

    return LogEventItem(
        log_id=doc_id,
        timestamp=event_ts,
        service_name=source.get("service_name") or "unassigned",
        environment=source.get("environment") or "production",
        log_level=str(source.get("log_level") or "ERROR").upper(),
        message=str(source.get("message") or ""),
        host=source.get("host"),
        trace_id=source.get("trace_id"),
        span_id=source.get("span_id"),
        raw_log=source.get("raw_log"),
        attributes=source.get("attributes") or {},
    )


def cluster_log_events(
    events: List[LogEventItem],
    window_minutes: int = 15,
) -> List[LogCluster]:
    """Cluster log events by service, normalized message pattern, and bounded time window.

    Deterministic rules:
    - Events with differing normalized message patterns are never merged into the same cluster.
    - Events outside the bounded time window are placed in separate window clusters.
    - Preserves all source log IDs, timestamps, traces, and unmanipulated sample messages.
    """
    if not events:
        return []

    # Map key: (service_name, pattern_hash, window_start) -> list of events
    clusters_map: Dict[Tuple[str, str, datetime], List[Tuple[LogEventItem, str]]] = {}
    pattern_by_hash: Dict[str, str] = {}

    for event in events:
        pattern = extract_message_pattern(event.message)
        p_hash = compute_pattern_hash(pattern)
        pattern_by_hash[p_hash] = pattern

        w_start = floor_datetime_to_window(event.timestamp, window_minutes)
        svc_name = (event.service_name or "").strip() or "unassigned"
        key = (svc_name, p_hash, w_start)

        if key not in clusters_map:
            clusters_map[key] = []
        clusters_map[key].append((event, pattern))

    result_clusters: List[LogCluster] = []

    for (svc_name, p_hash, w_start), item_pairs in clusters_map.items():
        w_end = w_start + timedelta(minutes=window_minutes)
        pattern = pattern_by_hash[p_hash]

        # Deduplicate events by log_id within the cluster to prevent duplicate records from skewing metrics
        unique_events_by_id: Dict[str, LogEventItem] = {}
        for ev, _ in item_pairs:
            ev.service_name = svc_name
            lid = ev.log_id
            if lid not in unique_events_by_id:
                unique_events_by_id[lid] = ev
            else:
                existing_ev = unique_events_by_id[lid]
                if not existing_ev.trace_id and ev.trace_id:
                    existing_ev.trace_id = ev.trace_id
                if not existing_ev.span_id and ev.span_id:
                    existing_ev.span_id = ev.span_id

        # Sort events chronologically within the cluster with stable log_id tie-breaking
        sorted_events = sorted(unique_events_by_id.values(), key=lambda e: (e.timestamp, e.log_id))
        first_seen = sorted_events[0].timestamp
        last_seen = sorted_events[-1].timestamp

        # Extract distinct trace and span IDs
        trace_ids = sorted(list({e.trace_id for e in sorted_events if e.trace_id}))
        span_ids = sorted(list({e.span_id for e in sorted_events if e.span_id}))

        # Collect source log IDs
        log_ids = [e.log_id for e in sorted_events]

        # Representative unique sample messages (up to 3)
        sample_messages: List[str] = []
        seen_msgs = set()
        for e in sorted_events:
            if e.message and e.message not in seen_msgs:
                seen_msgs.add(e.message)
                sample_messages.append(e.message)
                if len(sample_messages) >= 3:
                    break

        # If any event in the cluster is FATAL/CRITICAL, elevate severity
        has_fatal = any(e.log_level in ("FATAL", "CRITICAL") for e in sorted_events)
        cluster_severity = "FATAL" if has_fatal else "ERROR"

        time_slug = w_start.strftime("%Y%m%d%H%M")
        cluster_id = f"cl-{svc_name}-{p_hash}-{time_slug}"

        result_clusters.append(
            LogCluster(
                cluster_id=cluster_id,
                service_name=svc_name,
                pattern=pattern,
                pattern_hash=p_hash,
                severity=cluster_severity,
                window_start=w_start,
                window_end=w_end,
                count=len(sorted_events),
                log_ids=log_ids,
                sample_messages=sample_messages,
                first_seen=first_seen,
                last_seen=last_seen,
                trace_ids=trace_ids,
                span_ids=span_ids,
                events=sorted_events,
            )
        )

    # Sort clusters chronologically descending by window_start, then by count descending, with cluster_id tie-breaker
    result_clusters.sort(key=lambda c: (c.window_start, c.count, c.cluster_id), reverse=True)
    return result_clusters
