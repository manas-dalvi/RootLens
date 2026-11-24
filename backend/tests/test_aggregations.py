from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from opensearchpy.exceptions import ConnectionError, NotFoundError, RequestError

from app.main import app
from app.services.aggregation_service import (
    MAX_ALLOWED_BUCKETS,
    SUPPORTED_INTERVALS,
    build_time_series_aggregation_query,
    detect_significant_events,
    floor_datetime_to_window,
    get_log_time_series,
    parse_time_bucket,
    validate_interval_and_range,
)

client = TestClient(app)


# ============================================================================
# 1. Validation & Interval Tests
# ============================================================================


def test_validate_interval_success():
    """Verify all supported intervals map to correct durations."""
    assert validate_interval_and_range("1m", None, None) == 60
    assert validate_interval_and_range("2m", None, None) == 120
    assert validate_interval_and_range("1h", None, None) == 3600
    assert validate_interval_and_range("1d", None, None) == 86400


def test_validate_interval_unsupported():
    """Verify unsupported intervals raise HTTP 400."""
    with pytest.raises(Exception) as exc_info:
        validate_interval_and_range("3m", None, None)
    assert exc_info.value.status_code == 400
    assert "Unsupported interval" in exc_info.value.detail


def test_validate_interval_range_inverted():
    """Verify start_time > end_time raises HTTP 400."""
    start = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)
    with pytest.raises(Exception) as exc_info:
        validate_interval_and_range("1h", start, end)
    assert exc_info.value.status_code == 400
    assert "start_time must be earlier than or equal to end_time" in exc_info.value.detail


def test_validate_interval_safety_limit_exceeded():
    """Verify requesting interval that yields > 500 buckets raises HTTP 400."""
    start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc)
    # 9 days = 12960 minutes = 6480 2m buckets > 500 limit
    with pytest.raises(Exception) as exc_info:
        validate_interval_and_range("2m", start, end)
    assert exc_info.value.status_code == 400
    assert f"exceeding the safety limit of {MAX_ALLOWED_BUCKETS} buckets" in exc_info.value.detail


def test_validate_interval_day_thresholds():
    """Verify 500-bucket boundary checks across 15d, 90d, 365d, 500d, 501d, and 2 years."""
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)

    # 1. 15 days with 12h: ceil(15 * 86400 / 43200) + 1 = 31 buckets <= 500 (VALID)
    end_15d = start + timedelta(days=15)
    assert validate_interval_and_range("12h", start, end_15d) == 43200

    # 2. 90 days with 1d: ceil(90) + 1 = 91 buckets <= 500 (VALID)
    end_90d = start + timedelta(days=90)
    assert validate_interval_and_range("1d", start, end_90d) == 86400

    # 3. 365 days with 1d: ceil(365) + 1 = 366 buckets <= 500 (VALID)
    end_365d = start + timedelta(days=365)
    assert validate_interval_and_range("1d", start, end_365d) == 86400

    # 4. 500 days with 1d: ceil(500) + 1 = 501 buckets > 500 (EXCEEDS LIMIT)
    end_500d = start + timedelta(days=500)
    with pytest.raises(Exception) as exc_info:
        validate_interval_and_range("1d", start, end_500d)
    assert exc_info.value.status_code == 400
    assert "exceeding the safety limit of 500 buckets" in exc_info.value.detail

    # 5. 501 days with 1d: ceil(501) + 1 = 502 buckets > 500 (EXCEEDS LIMIT)
    end_501d = start + timedelta(days=501)
    with pytest.raises(Exception) as exc_info:
        validate_interval_and_range("1d", start, end_501d)
    assert exc_info.value.status_code == 400
    assert "exceeding the safety limit of 500 buckets" in exc_info.value.detail

    # 6. 2 years (730 days) with 1d: ceil(730) + 1 = 731 buckets > 500 (EXCEEDS LIMIT)
    end_2y = start + timedelta(days=730)
    with pytest.raises(Exception) as exc_info:
        validate_interval_and_range("1d", start, end_2y)
    assert exc_info.value.status_code == 400
    assert "exceeding the safety limit of 500 buckets" in exc_info.value.detail


# ============================================================================
# 2. Query Construction Tests
# ============================================================================


def test_build_time_series_query_structure():
    """Verify query payload contains date_histogram and sub-aggregation by log_level."""
    start = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 10, 10, 11, 0, tzinfo=timezone.utc)
    query = build_time_series_aggregation_query(
        interval="2m",
        start_time=start,
        end_time=end,
        service="payment-service",
        environment="production",
    )

    assert query["size"] == 0
    filter_clauses = query["query"]["bool"]["filter"]
    assert len(filter_clauses) == 3

    # Check date_histogram body
    aggs = query["aggs"]["time_buckets"]
    date_hist = aggs["date_histogram"]
    assert date_hist["field"] == "timestamp"
    assert date_hist["fixed_interval"] == "2m"
    assert date_hist["min_doc_count"] == 0
    assert date_hist["extended_bounds"]["min"] == start.isoformat()
    assert date_hist["extended_bounds"]["max"] == end.isoformat()

    # Check nested terms aggregation
    sev_agg = aggs["aggs"]["severities"]
    assert sev_agg["terms"]["field"] == "log_level"


def test_build_time_series_query_unbounded_min_doc_count():
    """Verify unbounded query sets min_doc_count=1 to prevent bucket explosion across years."""
    query = build_time_series_aggregation_query(interval="1h")
    date_hist = query["aggs"]["time_buckets"]["date_histogram"]
    assert date_hist["min_doc_count"] == 1
    assert "extended_bounds" not in date_hist



# ============================================================================
# 3. Aggregation Execution & Parsing Tests
# ============================================================================


def test_parse_time_bucket():
    """Verify raw OpenSearch date_histogram bucket converts to TimeBucketResponse."""
    raw = {
        "key_as_string": "2026-10-10T19:00:00.000Z",
        "key": 1791658800000,
        "doc_count": 5,
        "severities": {
            "buckets": [
                {"key": "INFO", "doc_count": 3},
                {"key": "ERROR", "doc_count": 2},
            ]
        },
    }
    parsed = parse_time_bucket(raw)
    assert parsed.doc_count == 5
    assert parsed.severities == {"INFO": 3, "ERROR": 2}
    assert parsed.timestamp.year == 2026


def test_get_log_time_series_mocked_success():
    """Verify get_log_time_series parses cluster search response cleanly."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.search.return_value = {
        "hits": {"total": {"value": 8}},
        "aggregations": {
            "time_buckets": {
                "buckets": [
                    {
                        "key_as_string": "2026-10-10T19:00:00Z",
                        "key": 1791658800000,
                        "doc_count": 4,
                        "severities": {
                            "buckets": [
                                {"key": "INFO", "doc_count": 3},
                                {"key": "WARN", "doc_count": 1},
                            ]
                        },
                    },
                    {
                        "key_as_string": "2026-10-10T19:30:00Z",
                        "key": 1791660600000,
                        "doc_count": 4,
                        "severities": {
                            "buckets": [
                                {"key": "ERROR", "doc_count": 4},
                            ]
                        },
                    },
                ]
            }
        },
    }

    res = get_log_time_series(interval="30m", client=mock_client)
    assert res.total_logs == 8
    assert len(res.buckets) == 2
    assert res.buckets[0].doc_count == 4
    assert res.buckets[0].severities == {"INFO": 3, "WARN": 1}
    assert res.buckets[1].severities == {"ERROR": 4}


def test_get_log_time_series_not_found_index():
    """Verify empty index (NotFoundError) returns empty buckets rather than 500 error."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.search.side_effect = NotFoundError(404, "index_not_found_exception")

    res = get_log_time_series(interval="1h", client=mock_client)
    assert res.total_logs == 0
    assert res.buckets == []


def test_get_log_time_series_cluster_unavailable():
    """Verify cluster down raises HTTP 503."""
    mock_client = MagicMock()
    mock_client.ping.return_value = False

    with pytest.raises(Exception) as exc_info:
        get_log_time_series(interval="1h", client=mock_client)
    assert exc_info.value.status_code == 503


# ============================================================================
# 4. Significant Event Detection Tests
# ============================================================================


def test_floor_datetime_to_window():
    """Verify datetime is correctly rounded down to the window boundary."""
    dt = datetime(2026, 10, 10, 19, 27, 45, tzinfo=timezone.utc)
    floored_15 = floor_datetime_to_window(dt, 15)
    assert floored_15.minute == 15
    assert floored_15.second == 0

    floored_30 = floor_datetime_to_window(dt, 30)
    assert floored_30.minute == 0


def test_detect_significant_events_window_grouping():
    """Verify repeated errors are grouped by service within explicit bounded windows."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True

    # 4 errors for payment-service in window A (19:00 - 19:15)
    # 2 errors for payment-service in window B (19:30 - 19:45)
    # 1 FATAL for auth-service in window B
    mock_client.search.return_value = {
        "hits": {
            "hits": [
                {
                    "_id": "log-p1",
                    "_source": {
                        "timestamp": "2026-10-10T19:05:00Z",
                        "service_name": "payment-service",
                        "log_level": "ERROR",
                        "message": "Payment gateway timeout 1",
                    },
                },
                {
                    "_id": "log-p2",
                    "_source": {
                        "timestamp": "2026-10-10T19:07:00Z",
                        "service_name": "payment-service",
                        "log_level": "ERROR",
                        "message": "Payment gateway timeout 2",
                    },
                },
                {
                    "_id": "log-p3",
                    "_source": {
                        "timestamp": "2026-10-10T19:10:00Z",
                        "service_name": "payment-service",
                        "log_level": "ERROR",
                        "message": "Payment gateway timeout 3",
                    },
                },
                {
                    "_id": "log-p4",
                    "_source": {
                        "timestamp": "2026-10-10T19:12:00Z",
                        "service_name": "payment-service",
                        "log_level": "ERROR",
                        "message": "Payment gateway timeout 4",
                    },
                },
                # Window B: Only 2 errors for payment-service (below threshold 3)
                {
                    "_id": "log-p5",
                    "_source": {
                        "timestamp": "2026-10-10T19:35:00Z",
                        "service_name": "payment-service",
                        "log_level": "ERROR",
                        "message": "Payment timeout 5",
                    },
                },
                {
                    "_id": "log-p6",
                    "_source": {
                        "timestamp": "2026-10-10T19:36:00Z",
                        "service_name": "payment-service",
                        "log_level": "ERROR",
                        "message": "Payment timeout 6",
                    },
                },
                # Window B: 1 FATAL for auth-service (triggers fatal rule)
                {
                    "_id": "log-a1",
                    "_source": {
                        "timestamp": "2026-10-10T19:38:00Z",
                        "service_name": "auth-service",
                        "log_level": "FATAL",
                        "message": "Fatal cryptographic key failure",
                    },
                },
            ]
        }
    }

    res = detect_significant_events(
        error_threshold=3,
        window_minutes=15,
        client=mock_client,
    )

    # Should detect 2 events:
    # 1. Error Surge for payment-service in window 19:00 (4 errors >= 3)
    # 2. Fatal Burst for auth-service in window 19:30 (1 fatal)
    # Window B payment-service (2 errors) should NOT trigger an event because it is below threshold 3!
    assert res.total_events == 2

    event_rules = {e.rule_name for e in res.events}
    assert "Critical/Fatal Log Burst" in event_rules
    assert "Elevated Service Error Surge" in event_rules

    surge_event = next(e for e in res.events if e.rule_name == "Elevated Service Error Surge")
    assert surge_event.service_name == "payment-service"
    assert surge_event.count == 4
    assert surge_event.evidence.log_ids == ["log-p1", "log-p2", "log-p3", "log-p4"]
    assert "Payment gateway timeout 1" in surge_event.evidence.sample_messages

    fatal_event = next(e for e in res.events if e.rule_name == "Critical/Fatal Log Burst")
    assert fatal_event.service_name == "auth-service"
    assert fatal_event.count == 1
    assert fatal_event.evidence.log_ids == ["log-a1"]


def test_detect_significant_events_empty_results():
    """Verify empty search results return 0 events gracefully."""
    mock_client = MagicMock()
    mock_client.ping.return_value = True
    mock_client.search.return_value = {"hits": {"hits": []}}

    res = detect_significant_events(client=mock_client)
    assert res.total_events == 0
    assert res.events == []


# ============================================================================
# 5. FastAPI Endpoint & Route Ordering Tests
# ============================================================================


def test_api_route_ordering_not_shadowed():
    """Verify /logs/aggregations and /logs/events are NOT shadowed by /logs/{id}."""
    with patch("app.services.aggregation_service.get_opensearch_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.search.return_value = {
            "hits": {"total": {"value": 0}},
            "aggregations": {"time_buckets": {"buckets": []}},
        }
        mock_get_client.return_value = mock_client

        # Test aggregations route
        resp = client.get("/api/v1/logs/aggregations?interval=1h")
        assert resp.status_code == 200
        data = resp.json()
        assert "buckets" in data
        assert data["interval"] == "1h"

        # Test events route
        mock_client.search.return_value = {"hits": {"hits": []}}
        resp_events = client.get("/api/v1/logs/events")
        assert resp_events.status_code == 200
        data_events = resp_events.json()
        assert "events" in data_events
        assert data_events["total_events"] == 0
