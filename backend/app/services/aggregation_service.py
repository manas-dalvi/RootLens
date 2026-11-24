from datetime import datetime, timedelta, timezone
import hashlib
import logging
import math
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from opensearchpy import OpenSearch
from opensearchpy.exceptions import ConnectionError, ConnectionTimeout, NotFoundError, RequestError

from app.core.config import settings
from app.core.opensearch import get_opensearch_client
from app.schemas.log_aggregations import (
    EventEvidence,
    LogTimeSeriesResponse,
    SignificantEventResponse,
    SignificantEventsResponse,
    TimeBucketResponse,
)

logger = logging.getLogger("rootlens.aggregations")

# Map of supported intervals to their duration in seconds
SUPPORTED_INTERVALS: Dict[str, int] = {
    "1m": 60,
    "2m": 120,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}

MAX_ALLOWED_BUCKETS: int = 500


def validate_interval_and_range(
    interval: str,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
) -> int:
    """Validate interval name and enforce the 500-bucket safety limit.

    Returns the interval duration in seconds if valid.
    Raises HTTPException(400) if interval is unsupported, range is invalid,
    or projected buckets exceed MAX_ALLOWED_BUCKETS.
    """
    cleaned_interval = interval.strip().lower()
    if cleaned_interval not in SUPPORTED_INTERVALS:
        valid_options = ", ".join(f"'{k}'" for k in SUPPORTED_INTERVALS.keys())
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported interval '{interval}'. Supported intervals are: {valid_options}.",
        )

    interval_seconds = SUPPORTED_INTERVALS[cleaned_interval]

    if start_time and end_time:
        if start_time > end_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_time must be earlier than or equal to end_time.",
            )

        duration_seconds = (end_time - start_time).total_seconds()
        projected_buckets = math.ceil(duration_seconds / interval_seconds) + 1
        if projected_buckets > MAX_ALLOWED_BUCKETS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Requested interval '{interval}' produces {projected_buckets} buckets for the "
                    f"specified time range, exceeding the safety limit of {MAX_ALLOWED_BUCKETS} buckets. "
                    "Please choose a coarser interval or narrow the time range."
                ),
            )

    return interval_seconds


def build_time_series_aggregation_query(
    interval: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    service: Optional[str] = None,
    environment: Optional[str] = None,
) -> Dict[str, Any]:
    """Construct OpenSearch search query payload with date_histogram aggregation."""
    filter_clauses: List[Dict[str, Any]] = []

    # Timestamp range filter
    if start_time or end_time:
        range_dict: Dict[str, str] = {}
        if start_time:
            range_dict["gte"] = start_time.isoformat()
        if end_time:
            range_dict["lte"] = end_time.isoformat()
        filter_clauses.append({"range": {"timestamp": range_dict}})

    # Service name filter
    if service and service.strip():
        filter_clauses.append({"term": {"service_name": service.strip()}})

    # Environment filter
    if environment and environment.strip():
        filter_clauses.append({"term": {"environment": environment.strip()}})

    query_root: Dict[str, Any]
    if filter_clauses:
        query_root = {"bool": {"filter": filter_clauses}}
    else:
        query_root = {"match_all": {}}

    date_hist_body: Dict[str, Any] = {
        "field": "timestamp",
        "fixed_interval": interval.strip().lower(),
        "min_doc_count": 0 if (start_time and end_time) else 1,
    }

    if start_time and end_time:
        date_hist_body["extended_bounds"] = {
            "min": start_time.isoformat(),
            "max": end_time.isoformat(),
        }

    aggs_body = {
        "time_buckets": {
            "date_histogram": date_hist_body,
            "aggs": {
                "severities": {
                    "terms": {
                        "field": "log_level",
                        "size": 10,
                    }
                }
            },
        }
    }

    return {
        "size": 0,
        "query": query_root,
        "aggs": aggs_body,
    }


def parse_time_bucket(raw_bucket: Dict[str, Any]) -> TimeBucketResponse:
    """Parse raw OpenSearch date_histogram bucket into TimeBucketResponse."""
    key = raw_bucket.get("key")
    key_as_string = raw_bucket.get("key_as_string")
    bucket_ts: datetime

    if key_as_string:
        try:
            bucket_ts = datetime.fromisoformat(key_as_string.replace("Z", "+00:00"))
        except Exception:
            bucket_ts = datetime.fromtimestamp(key / 1000.0, tz=timezone.utc)
    elif isinstance(key, (int, float)):
        bucket_ts = datetime.fromtimestamp(key / 1000.0, tz=timezone.utc)
    else:
        bucket_ts = datetime.now(timezone.utc)

    severities: Dict[str, int] = {}
    sev_aggs = raw_bucket.get("severities", {}).get("buckets", [])
    for b in sev_aggs:
        sev_key = str(b.get("key", "")).upper()
        if sev_key:
            severities[sev_key] = int(b.get("doc_count", 0))

    return TimeBucketResponse(
        timestamp=bucket_ts,
        doc_count=int(raw_bucket.get("doc_count", 0)),
        severities=severities,
    )


def get_log_time_series(
    interval: str = "1h",
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    service: Optional[str] = None,
    environment: Optional[str] = None,
    client: Optional[OpenSearch] = None,
) -> LogTimeSeriesResponse:
    """Execute time-bucketed date_histogram aggregation against OpenSearch cluster."""
    # 1. Validate interval and range
    validate_interval_and_range(interval, start_time, end_time)

    # 2. Check OpenSearch connectivity
    target_client = client or get_opensearch_client()
    try:
        is_alive = target_client.ping()
    except Exception as exc:
        logger.error("OpenSearch ping failed during aggregation: %s", exc)
        is_alive = False

    if not is_alive:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )

    # 3. Build query and execute aggregation
    body = build_time_series_aggregation_query(
        interval=interval,
        start_time=start_time,
        end_time=end_time,
        service=service,
        environment=environment,
    )

    target_index = f"{settings.OPENSEARCH_INDEX_PREFIX}-*"

    try:
        search_res = target_client.search(index=target_index, body=body)
    except NotFoundError:
        # Graceful empty response if no matching indices exist yet
        return LogTimeSeriesResponse(
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            total_logs=0,
            buckets=[],
        )
    except (ConnectionError, ConnectionTimeout):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )
    except RequestError as err:
        logger.warning("Invalid aggregation query parameters: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid aggregation query parameters.",
        )
    except Exception as exc:
        err_str = str(exc).lower()
        if "too_many_buckets_exception" in err_str:
            logger.warning("Aggregation query produced too many buckets: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aggregation produced too many buckets. Please specify a narrower time range or a coarser interval.",
            )
        logger.error("Error executing log time-series aggregation: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute log time-series aggregation.",
        )

    # 4. Extract total logs and parse buckets
    hits_obj = search_res.get("hits", {})
    total_obj = hits_obj.get("total", {})
    total_logs = total_obj.get("value", 0) if isinstance(total_obj, dict) else int(total_obj)

    raw_buckets = search_res.get("aggregations", {}).get("time_buckets", {}).get("buckets", [])
    buckets = [parse_time_bucket(b) for b in raw_buckets]

    return LogTimeSeriesResponse(
        interval=interval,
        start_time=start_time,
        end_time=end_time,
        total_logs=total_logs,
        buckets=buckets,
    )


def floor_datetime_to_window(dt: datetime, window_minutes: int) -> datetime:
    """Round a datetime down to the nearest bounded window start boundary."""
    dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    total_seconds = dt_utc.minute * 60 + dt_utc.second + dt_utc.microsecond / 1e6
    window_seconds = window_minutes * 60
    floored_seconds = math.floor(total_seconds / window_seconds) * window_seconds
    return dt_utc.replace(minute=0, second=0, microsecond=0) + timedelta(seconds=floored_seconds)


def detect_significant_events(
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    service: Optional[str] = None,
    error_threshold: Optional[int] = None,
    window_minutes: Optional[int] = None,
    client: Optional[OpenSearch] = None,
) -> SignificantEventsResponse:
    """Detect significant log events grouped by service within explicit bounded time windows.

    Deterministic rules:
    1. Fatal Event: Any FATAL log record within a window.
    2. Repeated Error Surge: >= error_threshold ERROR/CRITICAL logs for a service in a window.

    Never creates duplicate event entities for the same service and time window.
    """
    threshold = error_threshold if error_threshold is not None else settings.EVENT_ERROR_BURST_THRESHOLD
    win_mins = window_minutes if window_minutes is not None else settings.EVENT_WINDOW_MINUTES

    if threshold < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="error_threshold must be greater than or equal to 1.",
        )
    if win_mins < 1 or win_mins > 1440:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="window_minutes must be between 1 and 1440 (24 hours).",
        )
    if start_time and end_time and start_time > end_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_time must be earlier than or equal to end_time.",
        )

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

    # Query matching error/fatal records within time range (up to 1000 items)
    filter_clauses: List[Dict[str, Any]] = [
        {"terms": {"log_level": ["ERROR", "FATAL", "CRITICAL"]}}
    ]

    if start_time or end_time:
        range_dict: Dict[str, str] = {}
        if start_time:
            range_dict["gte"] = start_time.isoformat()
        if end_time:
            range_dict["lte"] = end_time.isoformat()
        filter_clauses.append({"range": {"timestamp": range_dict}})

    if service and service.strip():
        filter_clauses.append({"term": {"service_name": service.strip()}})

    search_query = {
        "size": 1000,
        "query": {"bool": {"filter": filter_clauses}},
        "sort": [{"timestamp": {"order": "desc"}}],
    }

    target_index = f"{settings.OPENSEARCH_INDEX_PREFIX}-*"

    try:
        search_res = target_client.search(index=target_index, body=search_query)
    except NotFoundError:
        return SignificantEventsResponse(total_events=0, events=[])
    except (ConnectionError, ConnectionTimeout):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )
    except Exception as exc:
        logger.error("Error querying logs for significant event detection: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to detect significant log events.",
        )

    hits = search_res.get("hits", {}).get("hits", [])
    if not hits:
        return SignificantEventsResponse(total_events=0, events=[])

    # Group hits by (window_start, service_name)
    # Structure: windows[(w_start, svc)] = {"fatal": [...hits], "error": [...hits]}
    windows: Dict[tuple[datetime, str], Dict[str, List[Dict[str, Any]]]] = {}

    for hit in hits:
        source = hit.get("_source", {})
        raw_ts = source.get("timestamp")
        hit_ts: Optional[datetime] = None

        if raw_ts:
            try:
                if isinstance(raw_ts, str):
                    hit_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                elif isinstance(raw_ts, (int, float)):
                    hit_ts = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc)
            except Exception:
                hit_ts = None

        if not hit_ts:
            continue

        w_start = floor_datetime_to_window(hit_ts, win_mins)
        svc_name = source.get("service_name") or "unassigned"
        key = (w_start, svc_name)

        if key not in windows:
            windows[key] = {"fatal": [], "error": []}

        lvl = str(source.get("log_level") or "").upper()
        if lvl == "FATAL":
            windows[key]["fatal"].append(hit)
        else:
            windows[key]["error"].append(hit)

    detected_events: List[SignificantEventResponse] = []

    # Sort window keys chronologically descending
    sorted_keys = sorted(windows.keys(), key=lambda k: k[0], reverse=True)

    for w_start, svc_name in sorted_keys:
        group = windows[(w_start, svc_name)]
        w_end = w_start + timedelta(minutes=win_mins)
        time_slug = w_start.strftime("%Y%m%d%H%M")

        # 1. Rule: Fatal log burst
        if group["fatal"]:
            f_hits = group["fatal"]
            log_ids = [str(h.get("_id")) for h in f_hits[:10]]
            messages = [str(h.get("_source", {}).get("message") or "") for h in f_hits[:3]]
            event_id = f"sig-fatal-{svc_name}-{time_slug}"

            detected_events.append(
                SignificantEventResponse(
                    event_id=event_id,
                    rule_name="Critical/Fatal Log Burst",
                    severity="CRITICAL",
                    service_name=svc_name,
                    count=len(f_hits),
                    window_start=w_start,
                    window_end=w_end,
                    description=(
                        f"{len(f_hits)} FATAL level event(s) recorded for service '{svc_name}' "
                        f"within bounded {win_mins}m window."
                    ),
                    evidence=EventEvidence(
                        log_ids=log_ids,
                        sample_messages=messages,
                    ),
                )
            )

        # 2. Rule: Repeated error surge
        if len(group["error"]) >= threshold:
            e_hits = group["error"]
            log_ids = [str(h.get("_id")) for h in e_hits[:10]]
            messages = [str(h.get("_source", {}).get("message") or "") for h in e_hits[:3]]
            event_id = f"sig-surge-{svc_name}-{time_slug}"

            detected_events.append(
                SignificantEventResponse(
                    event_id=event_id,
                    rule_name="Elevated Service Error Surge",
                    severity="HIGH",
                    service_name=svc_name,
                    count=len(e_hits),
                    window_start=w_start,
                    window_end=w_end,
                    description=(
                        f"{len(e_hits)} ERROR level event(s) recorded for service '{svc_name}' "
                        f"within bounded {win_mins}m window (threshold: {threshold})."
                    ),
                    evidence=EventEvidence(
                        log_ids=log_ids,
                        sample_messages=messages,
                    ),
                )
            )

    return SignificantEventsResponse(
        total_events=len(detected_events),
        events=detected_events,
    )
