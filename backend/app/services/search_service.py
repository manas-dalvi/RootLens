import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
from opensearchpy import OpenSearch
from opensearchpy.exceptions import ConnectionError, ConnectionTimeout, NotFoundError, RequestError

from app.core.config import settings
from app.core.opensearch import get_opensearch_client
from app.schemas.log_search import LogDocumentResponse, LogSearchResponse

logger = logging.getLogger("rootlens.search")


def _hit_to_log_document(hit: Dict[str, Any]) -> LogDocumentResponse:
    """Transform an OpenSearch search hit dictionary into LogDocumentResponse."""
    source = hit.get("_source", {})
    raw_ts = source.get("timestamp")
    parsed_ts: Optional[datetime] = None
    if raw_ts:
        try:
            if isinstance(raw_ts, str):
                parsed_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            elif isinstance(raw_ts, (int, float)):
                parsed_ts = datetime.fromtimestamp(raw_ts / 1000.0, tz=timezone.utc)
        except Exception:
            parsed_ts = None

    return LogDocumentResponse(
        id=str(hit.get("_id", "")),
        index=hit.get("_index"),
        timestamp=parsed_ts,
        service_name=source.get("service_name"),
        environment=source.get("environment"),
        log_level=source.get("log_level"),
        message=source.get("message"),
        host=source.get("host"),
        trace_id=source.get("trace_id"),
        span_id=source.get("span_id"),
        attributes=source.get("attributes") or {},
        raw_log=source.get("raw_log"),
    )


def build_log_search_query(
    q: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    service: Optional[str] = None,
    severity: Optional[str] = None,
    level: Optional[str] = None,
    source: Optional[str] = None,
    user_id: Optional[str] = None,
    api: Optional[str] = None,
    errors_only: bool = False,
    page: int = 1,
    page_size: int = 50,
    sort_order: str = "desc",
) -> Dict[str, Any]:
    """Construct a parameterized OpenSearch search query DSL payload."""
    must_clauses: List[Dict[str, Any]] = []
    filter_clauses: List[Dict[str, Any]] = []

    # Free text search across analyzed and descriptive fields (including document _id)
    if q and q.strip():
        must_clauses.append({
            "multi_match": {
                "query": q.strip(),
                "fields": ["message^2", "service_name", "host", "trace_id", "span_id", "_id"],
            }
        })

    # Timestamp range filter
    if start_time or end_time:
        range_dict: Dict[str, str] = {}
        if start_time:
            range_dict["gte"] = start_time.isoformat()
        if end_time:
            range_dict["lte"] = end_time.isoformat()
        filter_clauses.append({"range": {"timestamp": range_dict}})

    # Exact service name filter
    if service and service.strip():
        filter_clauses.append({"term": {"service_name": service.strip()}})

    # Severity / log level filter (accepts either 'severity' or 'level')
    target_level = severity or level
    if target_level and target_level.strip():
        filter_clauses.append({"term": {"log_level": target_level.strip().upper()}})

    # Errors-only filter
    if errors_only:
        filter_clauses.append({"terms": {"log_level": ["ERROR", "FATAL", "CRITICAL"]}})

    # Source metadata filter (matches source_format or job_id in attributes)
    if source and source.strip():
        src = source.strip()
        filter_clauses.append({
            "bool": {
                "should": [
                    {"term": {"attributes.source_format.keyword": src.lower()}},
                    {"term": {"attributes.source_format": src.lower()}},
                    {"term": {"attributes.job_id.keyword": src}},
                    {"term": {"attributes.job_id": src}},
                ],
                "minimum_should_match": 1,
            }
        })

    # User ID filter in structured attributes
    if user_id and user_id.strip():
        uid = user_id.strip()
        filter_clauses.append({
            "bool": {
                "should": [
                    {"term": {"attributes.user_id.keyword": uid}},
                    {"term": {"attributes.user_id": uid}},
                ],
                "minimum_should_match": 1,
            }
        })

    # API / Endpoint filter in structured attributes (exact phrase and keyword matching)
    if api and api.strip():
        api_str = api.strip()
        filter_clauses.append({
            "bool": {
                "should": [
                    {"term": {"attributes.api.keyword": api_str}},
                    {"term": {"attributes.endpoint.keyword": api_str}},
                    {"term": {"attributes.path.keyword": api_str}},
                    {"term": {"attributes.uri.keyword": api_str}},
                    {"match_phrase": {"attributes.api": api_str}},
                    {"match_phrase": {"attributes.endpoint": api_str}},
                    {"match_phrase": {"attributes.path": api_str}},
                    {"match_phrase": {"attributes.uri": api_str}},
                ],
                "minimum_should_match": 1,
            }
        })

    # Assemble query root
    if must_clauses or filter_clauses:
        bool_dict: Dict[str, Any] = {}
        if must_clauses:
            bool_dict["must"] = must_clauses
        if filter_clauses:
            bool_dict["filter"] = filter_clauses
        query_root = {"bool": bool_dict}
    else:
        query_root = {"match_all": {}}

    from_index = (page - 1) * page_size
    return {
        "query": query_root,
        "from": from_index,
        "size": page_size,
        "sort": [
            {"timestamp": {"order": sort_order.lower()}},
            {"_id": {"order": sort_order.lower()}},
        ],
    }


def search_logs(
    q: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    service: Optional[str] = None,
    severity: Optional[str] = None,
    level: Optional[str] = None,
    source: Optional[str] = None,
    user_id: Optional[str] = None,
    api: Optional[str] = None,
    errors_only: bool = False,
    page: int = 1,
    page_size: int = 50,
    sort_order: str = "desc",
    client: Optional[OpenSearch] = None,
) -> LogSearchResponse:
    """Execute paginated log search against time-partitioned OpenSearch indices."""
    # 1. Input Validation
    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Page must be greater than or equal to 1.",
        )
    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Page size must be between 1 and 100.",
        )
    if sort_order.lower() not in ("asc", "desc"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sort order must be 'asc' or 'desc'.",
        )
    if start_time and end_time and start_time > end_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_time must be earlier than or equal to end_time.",
        )

    # 2. Check OpenSearch connectivity
    target_client = client or get_opensearch_client()
    try:
        is_alive = target_client.ping()
    except Exception as exc:
        logger.error("OpenSearch ping failed during search: %s", exc)
        is_alive = False

    if not is_alive:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )

    # 3. Build query and execute search across wildcard pattern
    body = build_log_search_query(
        q=q,
        start_time=start_time,
        end_time=end_time,
        service=service,
        severity=severity,
        level=level,
        source=source,
        user_id=user_id,
        api=api,
        errors_only=errors_only,
        page=page,
        page_size=page_size,
        sort_order=sort_order,
    )

    target_index = f"{settings.OPENSEARCH_INDEX_PREFIX}-*"

    try:
        search_res = target_client.search(index=target_index, body=body)
    except NotFoundError:
        # Graceful empty response if no matching indices have been created yet
        return LogSearchResponse(
            items=[],
            page=page,
            page_size=page_size,
            total=0,
            total_pages=0,
        )
    except (ConnectionError, ConnectionTimeout):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )
    except RequestError as err:
        logger.warning("Invalid search query parameters: %s", err)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid search query parameters.",
        )
    except Exception as exc:
        logger.error("Error executing log search: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute log search.",
        )

    # 4. Process hits and calculate pagination metadata
    hits_obj = search_res.get("hits", {})
    total_obj = hits_obj.get("total", {})
    total = total_obj.get("value", 0) if isinstance(total_obj, dict) else int(total_obj)
    hits = hits_obj.get("hits", [])
    items = [_hit_to_log_document(hit) for hit in hits]
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return LogSearchResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


def get_log_by_id(
    log_id: str,
    client: Optional[OpenSearch] = None,
) -> LogDocumentResponse:
    """Retrieve an individual indexed log document by its unique document ID."""
    if not log_id or not log_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Log ID must be provided.",
        )

    target_client = client or get_opensearch_client()
    try:
        is_alive = target_client.ping()
    except Exception as exc:
        logger.error("OpenSearch ping failed during get_log_by_id %s: %s", log_id, exc)
        is_alive = False

    if not is_alive:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )

    target_index = f"{settings.OPENSEARCH_INDEX_PREFIX}-*"
    body = {"query": {"ids": {"values": [log_id.strip()]}}, "size": 1}

    try:
        search_res = target_client.search(index=target_index, body=body)
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log document with ID '{log_id}' not found.",
        )
    except (ConnectionError, ConnectionTimeout):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )
    except Exception as exc:
        logger.error("Error retrieving log document %s: %s", log_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve log document.",
        )

    hits = search_res.get("hits", {}).get("hits", [])
    if not hits:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log document with ID '{log_id}' not found.",
        )

    return _hit_to_log_document(hits[0])
