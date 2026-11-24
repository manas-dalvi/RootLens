from datetime import datetime, timezone
import pytest

from app.services.clustering_service import (
    LogCluster,
    LogEventItem,
    cluster_log_events,
    compute_pattern_hash,
    extract_message_pattern,
    floor_datetime_to_window,
    parse_opensearch_hit_to_event,
)


# ============================================================================
# 1. Message Pattern Masking & Normalization Tests
# ============================================================================


def test_extract_message_pattern_masks_dynamic_tokens():
    """Verify regex token masking on UUID, IP, Timestamp, Hex, Numbers, Strings."""
    raw_msg = (
        'Failed to connect to database at 192.168.1.50:5432 for user "admin" '
        "with session 12345678-1234-5678-1234-567812345678 at pointer 0x7fff5fbff840 "
        "after 45 retries at 2026-10-10T20:15:30Z"
    )
    pattern = extract_message_pattern(raw_msg)

    assert "<IP>" in pattern
    assert "<STR>" in pattern
    assert "<UUID>" in pattern
    assert "<HEX>" in pattern
    assert "<NUM>" in pattern
    assert "<TIMESTAMP>" in pattern
    assert "192.168.1.50" not in pattern
    assert "12345678-1234-5678-1234-567812345678" not in pattern
    assert "0x7fff5fbff840" not in pattern
    assert "admin" not in pattern


def test_extract_message_pattern_empty():
    """Verify empty or whitespace message yields <EMPTY_MESSAGE>."""
    assert extract_message_pattern("") == "<EMPTY_MESSAGE>"
    assert extract_message_pattern("   \n\t  ") == "<EMPTY_MESSAGE>"


def test_pattern_hash_deterministic_equality():
    """Verify two messages with different dynamic values yield identical patterns and hashes."""
    msg1 = 'User 1042 failed login from 10.0.0.1 with session 11111111-2222-3333-4444-555555555555'
    msg2 = 'User 9999 failed login from 10.0.0.99 with session aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

    pat1 = extract_message_pattern(msg1)
    pat2 = extract_message_pattern(msg2)
    assert pat1 == pat2
    assert compute_pattern_hash(pat1) == compute_pattern_hash(pat2)


def test_distinct_messages_yield_different_hashes():
    """Verify distinct error messages yield different patterns and hashes."""
    msg_db = "Connection timeout to PostgreSQL at 10.0.0.1"
    msg_mem = "Out of memory in worker thread 4"

    pat_db = extract_message_pattern(msg_db)
    pat_mem = extract_message_pattern(msg_mem)

    assert pat_db != pat_mem
    assert compute_pattern_hash(pat_db) != compute_pattern_hash(pat_mem)


# ============================================================================
# 2. Window Boundary Tests
# ============================================================================


def test_floor_datetime_to_window_boundaries():
    """Verify epoch-based window rounding to 15m intervals."""
    dt1 = datetime(2026, 10, 10, 14, 7, 23, tzinfo=timezone.utc)
    floored1 = floor_datetime_to_window(dt1, 15)
    assert floored1 == datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)

    dt2 = datetime(2026, 10, 10, 14, 29, 59, tzinfo=timezone.utc)
    floored2 = floor_datetime_to_window(dt2, 15)
    assert floored2 == datetime(2026, 10, 10, 14, 15, 0, tzinfo=timezone.utc)


# ============================================================================
# 3. Log Event Clustering Tests
# ============================================================================


def test_cluster_log_events_groups_matching_patterns_in_window():
    """Verify events with identical patterns and service within same window cluster together."""
    events = [
        LogEventItem(
            log_id="log-1",
            timestamp=datetime(2026, 10, 10, 14, 2, 0, tzinfo=timezone.utc),
            service_name="payment-service",
            log_level="ERROR",
            message="Gateway timeout talking to 10.0.0.5 after 30s",
            trace_id="tr-1",
        ),
        LogEventItem(
            log_id="log-2",
            timestamp=datetime(2026, 10, 10, 14, 5, 0, tzinfo=timezone.utc),
            service_name="payment-service",
            log_level="ERROR",
            message="Gateway timeout talking to 10.0.0.8 after 30s",
            trace_id="tr-2",
        ),
        LogEventItem(
            log_id="log-3",
            timestamp=datetime(2026, 10, 10, 14, 8, 0, tzinfo=timezone.utc),
            service_name="payment-service",
            log_level="FATAL",
            message="Gateway timeout talking to 10.0.0.9 after 30s",
            trace_id="tr-3",
        ),
    ]

    clusters = cluster_log_events(events, window_minutes=15)
    assert len(clusters) == 1

    cl = clusters[0]
    assert cl.service_name == "payment-service"
    assert cl.count == 3
    assert cl.log_ids == ["log-1", "log-2", "log-3"]
    assert cl.severity == "FATAL"  # Elevated because log-3 is FATAL
    assert cl.trace_ids == ["tr-1", "tr-2", "tr-3"]
    assert len(cl.sample_messages) <= 3


def test_cluster_log_events_does_not_group_unrelated_patterns():
    """Verify semantically distinct errors in same service and time window remain separate clusters."""
    events = [
        LogEventItem(
            log_id="log-db",
            timestamp=datetime(2026, 10, 10, 14, 2, 0, tzinfo=timezone.utc),
            service_name="payment-service",
            log_level="ERROR",
            message="Connection pool exhausted to database",
        ),
        LogEventItem(
            log_id="log-auth",
            timestamp=datetime(2026, 10, 10, 14, 2, 10, tzinfo=timezone.utc),
            service_name="payment-service",
            log_level="ERROR",
            message="User authentication failed: invalid token",
        ),
    ]

    clusters = cluster_log_events(events, window_minutes=15)
    assert len(clusters) == 2
    patterns = {c.pattern for c in clusters}
    assert any("Connection pool" in p for p in patterns)
    assert any("User authentication" in p for p in patterns)


def test_cluster_log_events_separates_distinct_windows():
    """Verify events occurring in different time windows are placed in separate clusters."""
    events = [
        LogEventItem(
            log_id="log-w1",
            timestamp=datetime(2026, 10, 10, 14, 5, 0, tzinfo=timezone.utc),
            service_name="order-service",
            log_level="ERROR",
            message="Database connection reset",
        ),
        LogEventItem(
            log_id="log-w2",
            timestamp=datetime(2026, 10, 10, 14, 25, 0, tzinfo=timezone.utc),
            service_name="order-service",
            log_level="ERROR",
            message="Database connection reset",
        ),
    ]

    clusters = cluster_log_events(events, window_minutes=15)
    assert len(clusters) == 2
    window_starts = {c.window_start for c in clusters}
    assert datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc) in window_starts
    assert datetime(2026, 10, 10, 14, 15, 0, tzinfo=timezone.utc) in window_starts


def test_cluster_log_events_empty():
    """Verify empty event input returns empty cluster list."""
    assert cluster_log_events([]) == []


# ============================================================================
# 4. OpenSearch Hit Parsing Tests
# ============================================================================


def test_parse_opensearch_hit_to_event():
    """Verify OpenSearch hit parsing correctly extracts LogEventItem."""
    hit = {
        "_id": "job-123_45",
        "_source": {
            "timestamp": "2026-10-10T15:30:00Z",
            "service_name": "checkout-service",
            "environment": "production",
            "log_level": "ERROR",
            "message": "Payment processor error",
            "host": "checkout-worker-1",
            "trace_id": "trace-abc",
            "span_id": "span-def",
            "raw_log": '{"error": "Payment processor error"}',
        },
    }

    event = parse_opensearch_hit_to_event(hit)
    assert event is not None
    assert event.log_id == "job-123_45"
    assert event.service_name == "checkout-service"
    assert event.timestamp == datetime(2026, 10, 10, 15, 30, 0, tzinfo=timezone.utc)
    assert event.trace_id == "trace-abc"
    assert event.span_id == "span-def"
    assert event.host == "checkout-worker-1"


def test_parse_opensearch_hit_invalid():
    """Verify missing doc id or invalid timestamp returns None gracefully."""
    assert parse_opensearch_hit_to_event({}) is None
    assert parse_opensearch_hit_to_event({"_id": "doc-1", "_source": {"timestamp": "invalid"}}) is None
