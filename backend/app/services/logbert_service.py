"""
LogBERT Service: Incident Pipeline and Telemetry Integration.
Manages lazy singleton model loading, sequence extraction from normalized logs,
batch anomaly scoring, and construction of anomaly clusters for incident candidate correlation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.ml.logbert.inference import LogBERTInferenceEngine, LogBERTInferenceResult
from app.services.clustering_service import (
    LogCluster,
    LogEventItem,
    compute_pattern_hash,
    extract_message_pattern,
    floor_datetime_to_window,
)

logger = logging.getLogger("rootlens.ml.logbert")

# Singleton engine and concurrency lock
_engine_instance: Optional[LogBERTInferenceEngine] = None
_engine_lock = threading.Lock()

# Regex for detecting HDFS block IDs in log messages or attributes
_BLOCK_ID_REGEX = re.compile(r"\b(blk_-?\d+)\b")

# Regex for detecting explicit event template codes like E1..E99
_EVENT_CODE_REGEX = re.compile(r"\b(E\d+)\b")
_HDFS_EVENT_CODE_STRICT_REGEX = re.compile(r"^E\d+$")

# Canonical HDFS service identifiers
HDFS_CANONICAL_SERVICES = {
    "hdfs",
    "hdfs-datanode",
    "hdfs-namenode",
    "datanode",
    "namenode",
    "dfs",
    "hadoop",
    "hdfs_full",
}

# Known enterprise microservices that must never be evaluated against HDFS LogBERT
# unless an explicit HDFS benchmark domain indicator is present.
KNOWN_MICROSERVICES = {
    "postgres-db",
    "postgresql",
    "postgres",
    "db",
    "payment-service",
    "payments",
    "order-service",
    "orders",
    "api-gateway",
    "gateway",
    "ingress",
    "inventory-service",
    "inventory",
    "auth-service",
    "authentication",
    "notification-service",
    "notifications",
    "user-service",
    "users",
    "cart-service",
    "catalog-service",
    "frontend",
    "web",
    "client",
}

# Explicit HDFS dataset / domain indicator tags
HDFS_DOMAIN_TAGS = {"hdfs", "hdfs_benchmark", "hdfs_full"}


def is_hdfs_domain_compatible(
    session_id: str,
    session_events: List[LogEventItem],
    engine: Optional[LogBERTInferenceEngine] = None,
) -> Tuple[bool, str, List[str]]:
    """
    Evaluate whether a session of log events belongs to the supported HDFS event domain.

    Enforces strict domain validation:
    1. Explicit non-HDFS microservices (postgres-db, payment-service, api-gateway, etc.)
       are rejected unless tagged with an explicit HDFS benchmark dataset indicator.
    2. Presence of an E-number in a message or arbitrary template/event_id attribute
       does NOT grant HDFS compatibility without a reliable HDFS domain indicator.
    3. Requires a reliable domain indicator:
       - HDFS component service name (e.g. hdfs, hdfs-datanode, datanode, namenode), OR
       - Explicit domain/dataset/log_source attribute in ('hdfs', 'hdfs_benchmark', 'hdfs_full'), OR
       - Valid HDFS block identifier (blk_-?\\d+) on a non-blacklisted service.
    4. Validates sequence event tokens against the active HDFS model vocabulary.
       Sequences consisting entirely of unknown message patterns or non-HDFS tokens are bypassed.

    Returns:
        (is_compatible, reason_string, extracted_event_tokens)
    """
    if not session_events:
        return False, "EMPTY_SESSION", []

    # 1. Inspect Service Names Across Session
    services = {ev.service_name.lower().strip() for ev in session_events if ev.service_name}

    is_hdfs_service = any(
        svc in HDFS_CANONICAL_SERVICES or svc.startswith("hdfs-")
        for svc in services
    )
    is_block_session = bool(_BLOCK_ID_REGEX.search(session_id)) or any(
        bool(_BLOCK_ID_REGEX.search(str((ev.attributes or {}).get("block_id", ""))))
        for ev in session_events
    )

    # Check for explicit HDFS domain indicator in event attributes
    has_explicit_hdfs_tag = any(
        str((ev.attributes or {}).get("domain", "")).lower() in HDFS_DOMAIN_TAGS
        or str((ev.attributes or {}).get("dataset", "")).lower() in HDFS_DOMAIN_TAGS
        or str((ev.attributes or {}).get("log_source", "")).lower() in HDFS_DOMAIN_TAGS
        or str((ev.attributes or {}).get("benchmark", "")).lower() in HDFS_DOMAIN_TAGS
        for ev in session_events
    )

    # PostgreSQL / DB services are strictly relational and never HDFS
    is_db_service = any("postgres" in svc or svc == "db" for svc in services)
    if is_db_service and not has_explicit_hdfs_tag:
        primary_svc = next(iter(services), "postgres-db")
        return (
            False,
            f"BYPASSED_NON_HDFS_MICROSERVICE: Service '{primary_svc}' is a database service and not part of the HDFS benchmark domain.",
            [],
        )

    # Ordinary microservice without HDFS block ID or domain indicator
    has_microservice = any(
        svc in KNOWN_MICROSERVICES
        for svc in services
    )
    if has_microservice and not (has_explicit_hdfs_tag or is_block_session):
        primary_svc = next(iter(services), "microservice")
        return (
            False,
            f"BYPASSED_NON_HDFS_MICROSERVICE: Service '{primary_svc}' is an enterprise microservice without HDFS block context.",
            [],
        )

    if not (has_explicit_hdfs_tag or is_hdfs_service or is_block_session):
        primary_svc = next(iter(services), "unknown")
        return (
            False,
            f"BYPASSED_NO_HDFS_DOMAIN_INDICATOR: Session '{session_id}' in service '{primary_svc}' lacks a reliable HDFS domain or block indicator.",
            [],
        )

    # 3. Extract and Validate Event Tokens
    extracted_tokens: List[str] = [extract_event_key(ev) for ev in session_events]

    active_vocab = getattr(engine.vocab, "_token2id", None) if engine and hasattr(engine, "vocab") else None

    valid_hdfs_tokens = 0
    for tok in extracted_tokens:
        if _HDFS_EVENT_CODE_STRICT_REGEX.match(tok):
            valid_hdfs_tokens += 1
        elif active_vocab and tok in active_vocab and not tok.startswith("["):
            valid_hdfs_tokens += 1

    if valid_hdfs_tokens == 0:
        return (
            False,
            f"BYPASSED_INVALID_VOCABULARY: Session '{session_id}' contains 0 recognized HDFS event tokens (all tokens are arbitrary message patterns).",
            [],
        )

    return True, "COMPATIBLE_HDFS", extracted_tokens


def get_logbert_engine() -> Optional[LogBERTInferenceEngine]:
    """
    Retrieve the thread-safe LogBERT inference engine singleton.
    Loads model checkpoint and vocabulary on first call without retraining.
    Returns None if LogBERT is disabled or files are missing.
    """
    global _engine_instance
    if not settings.LOGBERT_ENABLED:
        logger.info("LogBERT anomaly detection is disabled in configuration.")
        return None

    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                model_path = Path(settings.LOGBERT_MODEL_PATH)
                vocab_path = Path(settings.LOGBERT_VOCAB_PATH)

                if not model_path.exists() or not vocab_path.exists():
                    logger.warning(
                        "LogBERT model artifacts not found at %s or %s. Inference unavailable.",
                        model_path,
                        vocab_path,
                    )
                    return None

                try:
                    logger.info("Loading LogBERT checkpoint from %s...", model_path)
                    _engine_instance = LogBERTInferenceEngine(
                        checkpoint_path=model_path,
                        vocab_path=vocab_path,
                        device=settings.LOGBERT_DEVICE,
                        custom_threshold=settings.LOGBERT_DEFAULT_THRESHOLD,
                    )
                    logger.info("LogBERT inference engine successfully loaded.")
                except Exception as exc:
                    logger.error("Failed to load LogBERT inference engine: %s", exc, exc_info=True)
                    return None

    return _engine_instance


def reset_logbert_engine() -> None:
    """Reset the singleton instance (useful for testing)."""
    global _engine_instance
    with _engine_lock:
        _engine_instance = None


def extract_event_key(log: LogEventItem) -> str:
    """
    Extract a normalized event template key for a log item.
    Checks structured attributes first, then regex event tokens (e.g. E1..E19),
    then falls back to normalized message pattern.
    """
    attrs = log.attributes or {}

    # 1. Check explicit event template attribute
    if "event_id" in attrs and attrs["event_id"]:
        return str(attrs["event_id"]).strip()
    if "template" in attrs and attrs["template"]:
        return str(attrs["template"]).strip()

    # 2. Check for HDFS benchmark event token pattern (E1, E2, etc.) in message
    match = _EVENT_CODE_REGEX.search(log.message)
    if match:
        return match.group(1)

    # 3. Fallback to normalized pattern
    return extract_message_pattern(log.message)


def extract_session_id(log: LogEventItem) -> Optional[str]:
    """
    Extract an identifier for session grouping.
    Checks block_id in attributes or message, then trace_id, then request_id.
    """
    attrs = log.attributes or {}
    if "block_id" in attrs and attrs["block_id"]:
        return str(attrs["block_id"]).strip()

    # Regex search in raw log or message
    match = _BLOCK_ID_REGEX.search(log.message)
    if match:
        return match.group(1)
    if log.raw_log:
        match_raw = _BLOCK_ID_REGEX.search(log.raw_log)
        if match_raw:
            return match_raw.group(1)

    # Distributed trace ID fallback
    if log.trace_id:
        return log.trace_id

    return None


def group_logs_into_sessions(
    logs: List[LogEventItem],
    window_minutes: int = 15,
) -> Dict[str, List[LogEventItem]]:
    """
    Group log items into logical session sequences for deep-learning evaluation.
    Prioritizes session identifiers (block_id, trace_id), falling back to
    (service_name, window_start) grouping.
    """
    sessions: Dict[str, List[LogEventItem]] = {}

    for log in logs:
        s_id = extract_session_id(log)
        if s_id:
            key = s_id
        else:
            w_start = floor_datetime_to_window(log.timestamp, window_minutes)
            key = f"{log.service_name}_{w_start.strftime('%Y%m%d%H%M')}"

        if key not in sessions:
            sessions[key] = []
        sessions[key].append(log)

    # Sort each session's events chronologically with stable log_id tie-breaking
    for k in sessions:
        sessions[k].sort(key=lambda ev: (ev.timestamp, ev.log_id))

    return sessions


def detect_sequence_anomalies_as_clusters(
    logs: List[LogEventItem],
    window_minutes: int = 15,
    engine: Optional[LogBERTInferenceEngine] = None,
) -> List[LogCluster]:
    """
    Evaluate log event sequences with LogBERT and convert detected anomalies
    into LogCluster instances compatible with the incident correlation pipeline.

    Guarantees:
    - Retains original log IDs, timestamps, snippets, trace IDs and provenance.
    - Operates on non-error logs (INFO, WARN) as well as ERROR logs.
    - Tags events with model metadata (model='LogBERT', anomaly_score, threshold).
    - Preserves existing incident correlation invariants (is_root_cause_confirmed remains False).
    """
    if not logs:
        return []

    active_engine = engine or get_logbert_engine()
    if active_engine is None:
        logger.debug("LogBERT engine is unavailable; skipping sequence anomaly clustering.")
        return []

    grouped = group_logs_into_sessions(logs, window_minutes=window_minutes)
    anomaly_clusters: List[LogCluster] = []

    for session_id, session_events in grouped.items():
        if not session_events:
            continue

        # Check domain and vocabulary compatibility before running inference
        is_compatible, bypass_reason, event_tokens = is_hdfs_domain_compatible(
            session_id=session_id,
            session_events=session_events,
            engine=active_engine,
        )

        if not is_compatible:
            logger.debug(
                "Session '%s' bypassed HDFS LogBERT inference: %s",
                session_id,
                bypass_reason,
            )
            for ev in session_events:
                ev.attributes["logbert_status"] = "BYPASSED"
                ev.attributes["logbert_bypass_reason"] = bypass_reason
            continue

        # Evaluate sequence with LogBERT
        res: LogBERTInferenceResult = active_engine.predict_sequence(
            events=event_tokens,
            session_id=session_id,
        )

        if res.is_anomaly:
            earliest_time = session_events[0].timestamp
            latest_time = session_events[-1].timestamp
            w_start = floor_datetime_to_window(earliest_time, window_minutes)
            w_end = floor_datetime_to_window(latest_time, window_minutes)

            # Determine dominant service for this session
            service_counts: Dict[str, int] = {}
            for ev in session_events:
                service_counts[ev.service_name] = service_counts.get(ev.service_name, 0) + 1
            primary_svc = max(service_counts.items(), key=lambda x: x[1])[0]

            # Collect distinct traces and spans
            trace_ids = sorted(list({ev.trace_id for ev in session_events if ev.trace_id}))
            span_ids = sorted(list({ev.span_id for ev in session_events if ev.span_id}))
            log_ids = [ev.log_id for ev in session_events]

            # Annotate events with LogBERT diagnostic and explainability metadata
            explanation = res.explanation or {}
            mismatch_positions = set(explanation.get("mismatch_positions", []))
            token_explanations = explanation.get("token_explanations", [])
            explanation_type = explanation.get(
                "explanation_type",
                "TOKEN_MISMATCH" if res.mismatch_count > 0 else "EMBEDDING_DISTANCE",
            )
            dominant_factor = explanation.get(
                "dominant_factor",
                "MLKP_MISMATCH" if res.mismatch_count > 0 else "VHM_DISTANCE",
            )

            for i, ev in enumerate(session_events):
                ev.attributes["logbert_status"] = "ANOMALOUS"
                ev.attributes["model"] = "LogBERT"
                ev.attributes["anomaly_score"] = res.composite_score
                ev.attributes["vhm_distance"] = res.vhm_distance
                ev.attributes["mismatch_ratio"] = res.mismatch_ratio
                ev.attributes["threshold"] = res.threshold
                ev.attributes["session_id"] = session_id
                ev.attributes["explanation_type"] = explanation_type
                ev.attributes["dominant_factor"] = dominant_factor
                is_mismatch = i in mismatch_positions
                ev.attributes["is_mlkp_mismatch"] = is_mismatch
                if i < len(token_explanations):
                    t_info = token_explanations[i]
                    ev.attributes["mlkp_observed_event"] = t_info.get("observed_event")
                    ev.attributes["mlkp_observed_prob"] = t_info.get("observed_probability")
                    ev.attributes["mlkp_observed_nll"] = t_info.get("observed_nll")
                    ev.attributes["mlkp_top_candidates"] = t_info.get("top_candidates")
                    ev.attributes["contributes_to_anomaly"] = t_info.get("contributes_to_anomaly", is_mismatch)
                else:
                    ev.attributes["contributes_to_anomaly"] = is_mismatch

            pattern = (
                f"LogBERT Sequence Anomaly in session '{session_id}' "
                f"[{explanation_type}] (score: {res.composite_score:.4f} >= threshold: {res.threshold:.4f})"
            )
            p_hash = compute_pattern_hash(pattern)
            time_slug = w_start.strftime("%Y%m%d%H%M")
            cluster_id = f"cl-logbert-{session_id[:16]}-{time_slug}"

            # Sample messages
            sample_msgs = [ev.message for ev in session_events[:3] if ev.message]

            cluster_severity = "CRITICAL" if res.composite_score >= 1.0 else ("HIGH" if res.composite_score >= 0.5 else "MEDIUM")

            cluster = LogCluster(
                cluster_id=cluster_id,
                service_name=primary_svc,
                pattern=pattern,
                pattern_hash=p_hash,
                severity=cluster_severity,
                window_start=w_start,
                window_end=w_end,
                count=len(session_events),
                log_ids=log_ids,
                sample_messages=sample_msgs,
                first_seen=earliest_time,
                last_seen=latest_time,
                trace_ids=trace_ids,
                span_ids=span_ids,
                events=session_events,
            )
            anomaly_clusters.append(cluster)
        else:
            for ev in session_events:
                ev.attributes["logbert_status"] = "NORMAL"
                ev.attributes["model"] = "LogBERT"
                ev.attributes["anomaly_score"] = res.composite_score
                ev.attributes["threshold"] = res.threshold
                ev.attributes["session_id"] = session_id

    return anomaly_clusters
