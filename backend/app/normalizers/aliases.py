from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

TIMESTAMP_ALIASES = [
    "timestamp",
    "@timestamp",
    "time",
    "datetime",
    "date",
    "ts",
    "event_time",
    "logged_at",
    "log_time",
    "created_at",
    "record_time",
]

LEVEL_ALIASES = [
    "log_level",
    "level",
    "severity",
    "log_severity",
    "lvl",
    "log.level",
    "priority",
]

SERVICE_ALIASES = [
    "service_name",
    "service",
    "app",
    "app_name",
    "application",
    "logger",
    "logger_name",
    "source",
    "component",
    "module",
]

HOST_ALIASES = [
    "host",
    "hostname",
    "host_name",
    "server",
    "node",
    "machine",
    "ip",
    "client_ip",
]

MESSAGE_ALIASES = [
    "message",
    "msg",
    "log",
    "log_message",
    "event_message",
    "description",
    "text",
    "body",
    "payload",
]

TRACE_ID_ALIASES = [
    "trace_id",
    "traceId",
    "trace.id",
    "dd.trace_id",
    "x-request-id",
    "request_id",
    "correlation_id",
]

SPAN_ID_ALIASES = [
    "span_id",
    "spanId",
    "span.id",
    "dd.span_id",
]

ENVIRONMENT_ALIASES = [
    "environment",
    "env",
    "stage",
]


@dataclass
class ResolvedAliases:
    """Fields extracted via canonical aliases with unmapped keys isolated in attributes."""

    raw_timestamp: Optional[Any] = None
    raw_level: Optional[Any] = None
    raw_service: Optional[str] = None
    raw_host: Optional[str] = None
    raw_message: Optional[str] = None
    raw_trace_id: Optional[str] = None
    raw_span_id: Optional[str] = None
    raw_environment: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


def _find_field(data: Dict[str, Any], aliases: List[str]) -> Tuple[Optional[str], Any]:
    """Find the first matching key from aliases case-insensitively."""
    # 1. Exact match first
    for alias in aliases:
        if alias in data and data[alias] is not None:
            return alias, data[alias]

    # 2. Case-insensitive match
    lookup = {k.lower(): k for k in data.keys()}
    for alias in aliases:
        alias_lower = alias.lower()
        if alias_lower in lookup and data[lookup[alias_lower]] is not None:
            actual_key = lookup[alias_lower]
            return actual_key, data[actual_key]

    return None, None


def resolve_aliases(extracted: Dict[str, Any]) -> ResolvedAliases:
    """Inspect raw extracted dictionary and map recognized aliases to canonical properties.

    Any fields not consumed by canonical aliases are preserved in attributes.
    Internal parser metadata (e.g. pattern, procid, pid, thread) is cleanly retained.
    """
    consumed_keys: Set[str] = set()

    ts_key, raw_ts = _find_field(extracted, TIMESTAMP_ALIASES)
    if ts_key:
        consumed_keys.add(ts_key)

    lvl_key, raw_lvl = _find_field(extracted, LEVEL_ALIASES)
    if lvl_key:
        consumed_keys.add(lvl_key)

    svc_key, raw_svc = _find_field(extracted, SERVICE_ALIASES)
    if svc_key:
        consumed_keys.add(svc_key)

    host_key, raw_host = _find_field(extracted, HOST_ALIASES)
    if host_key:
        consumed_keys.add(host_key)

    msg_key, raw_msg = _find_field(extracted, MESSAGE_ALIASES)
    if msg_key:
        consumed_keys.add(msg_key)

    trace_key, raw_trace = _find_field(extracted, TRACE_ID_ALIASES)
    if trace_key:
        consumed_keys.add(trace_key)

    span_key, raw_span = _find_field(extracted, SPAN_ID_ALIASES)
    if span_key:
        consumed_keys.add(span_key)

    env_key, raw_env = _find_field(extracted, ENVIRONMENT_ALIASES)
    if env_key:
        consumed_keys.add(env_key)

    # Remaining fields become attributes
    remaining_attributes = {
        k: v for k, v in extracted.items() if k not in consumed_keys
    }

    return ResolvedAliases(
        raw_timestamp=raw_ts,
        raw_level=raw_lvl,
        raw_service=str(raw_svc).strip() if raw_svc is not None else None,
        raw_host=str(raw_host).strip() if raw_host is not None else None,
        raw_message=str(raw_msg) if raw_msg is not None else None,
        raw_trace_id=str(raw_trace).strip() if raw_trace is not None else None,
        raw_span_id=str(raw_span).strip() if raw_span is not None else None,
        raw_environment=str(raw_env).strip() if raw_env is not None else None,
        attributes=remaining_attributes,
    )
