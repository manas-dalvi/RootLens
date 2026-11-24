import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from opensearchpy.exceptions import ConnectionError, NotFoundError, RequestError

from app.core.config import settings
from app.main import app
from app.schemas.log_search import LogDocumentResponse, LogSearchResponse
from app.services.search_service import (
    build_log_search_query,
    get_log_by_id,
    search_logs,
)

client = TestClient(app)


def make_mock_search_hit(doc_id="job1_1", index_name="rootlens-logs-2026.10"):
    """Generate a sample OpenSearch search hit dictionary."""
    return {
        "_id": doc_id,
        "_index": index_name,
        "_source": {
            "timestamp": "2026-10-10T12:00:00Z",
            "service_name": "auth-service",
            "environment": "production",
            "log_level": "INFO",
            "message": "User logged in successfully",
            "host": "auth-node-1",
            "trace_id": "trace-abc-123",
            "span_id": "span-def-456",
            "attributes": {
                "job_id": "00000000-0000-0000-0000-000000000001",
                "line_number": 1,
                "source_format": "json",
                "user_id": "usr_999",
                "api": "/v1/auth/login",
            },
            "raw_log": '{"message": "User logged in"}',
        },
    }


# ============================================================================
# 1. Query Construction Unit Tests
# ============================================================================


def test_build_log_search_query_no_filters():
    """Verify query DSL defaults to match_all with default sort and pagination."""
    query = build_log_search_query(page=1, page_size=50, sort_order="desc")
    assert query["query"] == {"match_all": {}}
    assert query["from"] == 0
    assert query["size"] == 50
    assert query["sort"] == [
        {"timestamp": {"order": "desc"}},
        {"_id": {"order": "desc"}},
    ]


def test_build_log_search_query_all_filters():
    """Verify query DSL incorporates all filters cleanly into bool query."""
    start_dt = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 10, 10, 23, 59, 59, tzinfo=timezone.utc)

    query = build_log_search_query(
        q="database connection failed",
        start_time=start_dt,
        end_time=end_dt,
        service="billing-api",
        severity="ERROR",
        source="csv",
        user_id="user_123",
        api="/checkout",
        errors_only=True,
        page=2,
        page_size=25,
        sort_order="asc",
    )

    assert "bool" in query["query"]
    bool_q = query["query"]["bool"]
    assert len(bool_q["must"]) == 1
    assert bool_q["must"][0]["multi_match"]["query"] == "database connection failed"

    filters = bool_q["filter"]
    # Check range filter
    range_filters = [f for f in filters if "range" in f]
    assert len(range_filters) == 1
    assert range_filters[0]["range"]["timestamp"]["gte"] == start_dt.isoformat()
    assert range_filters[0]["range"]["timestamp"]["lte"] == end_dt.isoformat()

    # Check service term filter
    service_filters = [f for f in filters if "term" in f and "service_name" in f["term"]]
    assert len(service_filters) == 1
    assert service_filters[0]["term"]["service_name"] == "billing-api"

    # Check level term filter
    level_filters = [f for f in filters if "term" in f and "log_level" in f["term"]]
    assert len(level_filters) == 1
    assert level_filters[0]["term"]["log_level"] == "ERROR"

    # Check errors_only terms filter
    errors_filters = [f for f in filters if "terms" in f and "log_level" in f["terms"]]
    assert len(errors_filters) == 1
    assert errors_filters[0]["terms"]["log_level"] == ["ERROR", "FATAL", "CRITICAL"]

    # Pagination
    assert query["from"] == 25
    assert query["size"] == 25
    assert query["sort"] == [
        {"timestamp": {"order": "asc"}},
        {"_id": {"order": "asc"}},
    ]


def test_build_log_search_query_level_alias():
    """Verify 'level' works as alias for 'severity' filter."""
    query = build_log_search_query(level="warn")
    bool_q = query["query"]["bool"]
    level_filters = [f for f in bool_q["filter"] if "term" in f and "log_level" in f["term"]]
    assert len(level_filters) == 1
    assert level_filters[0]["term"]["log_level"] == "WARN"


# ============================================================================
# 2. Service-Level Search Tests
# ============================================================================


def test_search_logs_success():
    """Verify search_logs returns mapped LogSearchResponse with pagination."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    hit = make_mock_search_hit()
    mock_client.search.return_value = {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [hit],
        }
    }

    res = search_logs(q="User logged in", page=1, page_size=20, client=mock_client)
    assert res.total == 1
    assert res.page == 1
    assert res.page_size == 20
    assert res.total_pages == 1
    assert len(res.items) == 1

    doc = res.items[0]
    assert doc.id == "job1_1"
    assert doc.service_name == "auth-service"
    assert doc.log_level == "INFO"
    assert doc.message == "User logged in successfully"
    assert doc.attributes["user_id"] == "usr_999"


def test_search_logs_empty_hits():
    """Verify search_logs returns total 0 and empty items when no matches found."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.search.return_value = {
        "hits": {
            "total": {"value": 0, "relation": "eq"},
            "hits": [],
        }
    }

    res = search_logs(service="nonexistent", client=mock_client)
    assert res.total == 0
    assert res.total_pages == 0
    assert res.items == []


def test_search_logs_index_not_found_returns_empty():
    """Verify NotFoundError (no indices matching pattern) returns empty result cleanly."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.search.side_effect = NotFoundError(404, "index_not_found_exception")

    res = search_logs(client=mock_client)
    assert res.total == 0
    assert res.total_pages == 0
    assert res.items == []


def test_search_logs_opensearch_unavailable():
    """Verify HTTPException 503 is raised when OpenSearch ping returns False."""
    mock_client = MagicMock()
    mock_client.ping.return_value = False

    with pytest.raises(Exception) as exc_info:
        search_logs(client=mock_client)

    assert "503" in str(exc_info.value) or "unavailable" in str(exc_info.value)


def test_search_logs_validation_errors():
    """Verify invalid input raises HTTP 400."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True

    # Invalid page
    with pytest.raises(Exception) as exc:
        search_logs(page=0, client=mock_client)
    assert "400" in str(exc.value)

    # Invalid page size
    with pytest.raises(Exception) as exc:
        search_logs(page_size=101, client=mock_client)
    assert "400" in str(exc.value)

    # Invalid sort order
    with pytest.raises(Exception) as exc:
        search_logs(sort_order="random", client=mock_client)
    assert "400" in str(exc.value)

    # Invalid time range
    t1 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(Exception) as exc:
        search_logs(start_time=t1, end_time=t2, client=mock_client)
    assert "400" in str(exc.value)


# ============================================================================
# 3. Single Log Document Retrieval Tests
# ============================================================================


def test_get_log_by_id_success():
    """Verify get_log_by_id returns document matching ID."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    hit = make_mock_search_hit(doc_id="target_123")
    mock_client.search.return_value = {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [hit],
        }
    }

    doc = get_log_by_id(log_id="target_123", client=mock_client)
    assert doc.id == "target_123"
    assert doc.service_name == "auth-service"
    assert doc.message == "User logged in successfully"


def test_get_log_by_id_not_found():
    """Verify get_log_by_id raises 404 when document does not exist."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.search.return_value = {
        "hits": {
            "total": {"value": 0, "relation": "eq"},
            "hits": [],
        }
    }

    with pytest.raises(Exception) as exc_info:
        get_log_by_id(log_id="missing_doc", client=mock_client)

    assert "404" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_get_log_by_id_empty_id():
    """Verify get_log_by_id raises 400 on empty id string."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True

    with pytest.raises(Exception) as exc_info:
        get_log_by_id(log_id="  ", client=mock_client)

    assert "400" in str(exc_info.value)


# ============================================================================
# 4. API Endpoint Integration Tests
# ============================================================================


def test_api_get_logs_endpoint_success():
    """Verify GET /api/v1/logs returns 200 with search results."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    hit = make_mock_search_hit()
    mock_client.search.return_value = {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [hit],
        }
    }

    with patch("app.services.search_service.get_opensearch_client", return_value=mock_client):
        response = client.get("/api/v1/logs?q=login&service=auth-service&page=1&page_size=20")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert len(data["items"]) == 1
    assert data["items"][0]["service_name"] == "auth-service"


def test_api_get_logs_endpoint_invalid_pagination():
    """Verify GET /api/v1/logs returns 400 for bad page or page_size."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True

    with patch("app.services.search_service.get_opensearch_client", return_value=mock_client):
        res1 = client.get("/api/v1/logs?page=0")
        assert res1.status_code == 400

        res2 = client.get("/api/v1/logs?page_size=200")
        assert res2.status_code == 400


def test_api_get_single_log_endpoint_success():
    """Verify GET /api/v1/logs/{id} returns 200 with log document."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    hit = make_mock_search_hit(doc_id="doc_xyz")
    mock_client.search.return_value = {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [hit],
        }
    }

    with patch("app.services.search_service.get_opensearch_client", return_value=mock_client):
        response = client.get("/api/v1/logs/doc_xyz")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "doc_xyz"
    assert data["service_name"] == "auth-service"


def test_api_get_single_log_endpoint_not_found():
    """Verify GET /api/v1/logs/{id} returns 404 for missing document."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.search.return_value = {
        "hits": {
            "total": {"value": 0, "relation": "eq"},
            "hits": [],
        }
    }

    with patch("app.services.search_service.get_opensearch_client", return_value=mock_client):
        response = client.get("/api/v1/logs/nonexistent_doc_id")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_api_route_ordering_preserves_existing_jobs():
    """Verify GET /api/v1/logs/jobs/{job_id} does not collide with /{id} route."""
    job_uuid = uuid.uuid4()
    # If route ordering is correct, requesting /jobs/{job_id} targets get_upload_job_status
    # and raises 404 from upload_service ("Upload job '{job_id}' not found.") rather than
    # "Log document with ID 'jobs' not found" or /{id} handler.
    response = client.get(f"/api/v1/logs/jobs/{job_uuid}")
    assert response.status_code == 404
    assert f"Upload job '{job_uuid}' not found." in response.json()["detail"]


# ============================================================================
# 5. Audit Regression Tests (Fix 1 & Fix 2)
# ============================================================================


def test_build_log_search_query_source_format_keyword():
    """Verify source filtering targets attributes.source_format.keyword."""
    query = build_log_search_query(source="JSON")
    bool_q = query["query"]["bool"]
    source_filters = [f for f in bool_q["filter"] if "bool" in f and "should" in f["bool"]]
    assert len(source_filters) == 1
    should_clauses = source_filters[0]["bool"]["should"]
    keyword_terms = [s["term"] for s in should_clauses if "term" in s and "attributes.source_format.keyword" in s["term"]]
    assert len(keyword_terms) == 1
    assert keyword_terms[0]["attributes.source_format.keyword"] == "json"


def test_build_log_search_query_api_uses_match_phrase():
    """Verify API path filter uses match_phrase and exact keywords, avoiding loose match with OR."""
    query = build_log_search_query(api="/v1/checkout")
    bool_q = query["query"]["bool"]
    api_filters = [f for f in bool_q["filter"] if "bool" in f and "should" in f["bool"]]
    assert len(api_filters) == 1
    should_clauses = api_filters[0]["bool"]["should"]

    # Must contain match_phrase for path fields
    match_phrases = [s["match_phrase"] for s in should_clauses if "match_phrase" in s]
    assert len(match_phrases) >= 3
    assert any("attributes.api" in mp and mp["attributes.api"] == "/v1/checkout" for mp in match_phrases)

    # Must NOT contain loose match
    loose_matches = [s for s in should_clauses if "match" in s]
    assert len(loose_matches) == 0


def test_api_path_filtering_regression_checkout_vs_metrics():
    """Regression test: verify querying /v1/checkout matches exact record and not /v1/metrics."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True

    hit_checkout = make_mock_search_hit(doc_id="doc_checkout")
    hit_checkout["_source"]["attributes"]["api"] = "/v1/checkout"

    # Simulate OpenSearch executing match_phrase / keyword queries correctly returning only checkout
    mock_client.search.return_value = {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [hit_checkout],
        }
    }

    with patch("app.services.search_service.get_opensearch_client", return_value=mock_client):
        response = client.get("/api/v1/logs?api=/v1/checkout")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["attributes"]["api"] == "/v1/checkout"

    # Verify OpenSearch search query inspected: no loose "match" operators
    called_body = mock_client.search.call_args[1]["body"]
    filter_clauses = called_body["query"]["bool"]["filter"]
    api_clause = filter_clauses[0]["bool"]["should"]
    assert not any("match" in c for c in api_clause)
    assert any("match_phrase" in c for c in api_clause)


# ============================================================================
# 5. Phase 15 Step 3: Historical Search Deterministic Ordering & Pagination
# ============================================================================


def test_search_query_deterministic_secondary_sort_when_timestamps_equal():
    """Verify search query includes secondary _id sort matching primary direction for stable tie-breaking."""
    q_desc = build_log_search_query(sort_order="desc")
    assert q_desc["sort"] == [
        {"timestamp": {"order": "desc"}},
        {"_id": {"order": "desc"}},
    ]

    q_asc = build_log_search_query(sort_order="asc")
    assert q_asc["sort"] == [
        {"timestamp": {"order": "asc"}},
        {"_id": {"order": "asc"}},
    ]


def test_search_logs_pagination_stability_with_equal_timestamps():
    """Verify that multiple records with identical timestamps paginate stably without records jumping."""
    t_shared = "2026-10-10T12:00:00Z"
    all_hits = [
        make_mock_search_hit(doc_id=f"doc_{i}") for i in range(1, 5)
    ]
    for h in all_hits:
        h["_source"]["timestamp"] = t_shared

    mock_client = MagicMock()
    mock_client.ping.return_value = True

    # Simulate OpenSearch executing query with deterministic (timestamp desc, _id desc) sort
    # Reverse so doc_4, doc_3 come on page 1 and doc_2, doc_1 come on page 2
    sorted_hits = sorted(all_hits, key=lambda h: h["_id"], reverse=True)

    def mock_search_fn(index, body):
        from_idx = body.get("from", 0)
        size = body.get("size", 2)
        page_slice = sorted_hits[from_idx : from_idx + size]
        return {
            "hits": {
                "total": {"value": len(sorted_hits), "relation": "eq"},
                "hits": page_slice,
            }
        }

    mock_client.search.side_effect = mock_search_fn

    # Fetch Page 1 (size=2)
    res_page1 = search_logs(page=1, page_size=2, sort_order="desc", client=mock_client)
    assert len(res_page1.items) == 2
    p1_ids = [doc.id for doc in res_page1.items]

    # Fetch Page 2 (size=2)
    res_page2 = search_logs(page=2, page_size=2, sort_order="desc", client=mock_client)
    assert len(res_page2.items) == 2
    p2_ids = [doc.id for doc in res_page2.items]

    # Deterministic non-overlapping partition
    assert set(p1_ids).isdisjoint(set(p2_ids))
    assert p1_ids == ["doc_4", "doc_3"]
    assert p2_ids == ["doc_2", "doc_1"]


def test_search_logs_order_reversal_is_exact_inversion():
    """Verify ascending vs descending queries generate symmetric inverted sort specifications."""
    q_asc = build_log_search_query(sort_order="asc", page=1, page_size=10)
    q_desc = build_log_search_query(sort_order="desc", page=1, page_size=10)

    assert q_asc["sort"][0]["timestamp"]["order"] == "asc"
    assert q_asc["sort"][1]["_id"]["order"] == "asc"
    assert q_desc["sort"][0]["timestamp"]["order"] == "desc"
    assert q_desc["sort"][1]["_id"]["order"] == "desc"


