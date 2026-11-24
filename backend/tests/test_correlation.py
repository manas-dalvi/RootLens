from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid
import pytest

from app.services.clustering_service import LogCluster, LogEventItem, cluster_log_events
from app.services.correlation_service import (
    CorrelatedEvidenceItem,
    CorrelatedIncidentCandidate,
    IncidentGroupingEvaluation,
    correlate_clusters,
    deduplicate_evidence_items,
    evaluate_incident_grouping,
)


def _make_dummy_cluster(
    cluster_id: str,
    service_name: str,
    start_ts: datetime,
    error_count: int = 1,
    severity: str = "ERROR",
    trace_id: str = None,
    span_id: str = None,
    pattern: str = "Error in service",
    attributes: Dict[str, Any] = None,
) -> LogCluster:
    """Helper to construct a typed LogCluster for correlation testing."""
    events = []
    log_ids = []
    attr_dict = dict(attributes or {})
    for i in range(error_count):
        lid = f"{cluster_id}_log_{i}"
        log_ids.append(lid)
        events.append(
            LogEventItem(
                log_id=lid,
                timestamp=start_ts,
                service_name=service_name,
                environment="production",
                log_level=severity,
                message=f"{pattern} instance {i}",
                host=f"{service_name}-host",
                trace_id=trace_id,
                span_id=span_id,
                raw_log=f'{{"level": "{severity}", "message": "{pattern}", "id": "{lid}"}}',
                attributes=attr_dict,
            )
        )

    return LogCluster(
        cluster_id=cluster_id,
        service_name=service_name,
        pattern=pattern,
        pattern_hash=f"hash_{cluster_id}",
        severity=severity,
        window_start=start_ts,
        window_end=start_ts,
        count=error_count,
        log_ids=log_ids,
        sample_messages=[f"{pattern} instance 0"],
        first_seen=start_ts,
        last_seen=start_ts,
        trace_ids=[trace_id] if trace_id else [],
        span_ids=[span_id] if span_id else [],
        events=events,
    )


# ============================================================================
# 1. Trace-Linked Correlation Tests
# ============================================================================


def test_correlate_clusters_trace_linked():
    """Verify clusters sharing a trace_id across distinct services are merged into one candidate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 5, tzinfo=timezone.utc)

    # payment-service failure with trace-123
    c1 = _make_dummy_cluster("c1", "payment-service", t0, trace_id="trace-123", pattern="Database deadlock")
    # api-gateway failure with trace-123
    c2 = _make_dummy_cluster("c2", "api-gateway", t1, trace_id="trace-123", pattern="HTTP 504 Gateway Timeout")

    candidates = correlate_clusters([c1, c2])

    assert len(candidates) == 1
    cand = candidates[0]
    assert "TRACE_LINKED" in cand.correlation_rules_used
    assert len(cand.affected_services) == 2

    # Earliest failure must be payment-service
    assert cand.timeline_events[0].service_name == "payment-service"
    assert cand.timeline_events[0].is_earliest_observed_failure is True
    assert cand.timeline_events[0].event_type == "FIRST_ERROR"
    assert cand.timeline_events[0].propagation_lag_seconds == 0.0

    # Downstream failure in api-gateway
    assert cand.timeline_events[1].service_name == "api-gateway"
    assert cand.timeline_events[1].is_earliest_observed_failure is False
    assert cand.timeline_events[1].event_type == "CASCADING_FAILURE"
    assert cand.timeline_events[1].propagation_lag_seconds == 5.0


# ============================================================================
# 2. Meaningful Span Relationship Tests
# ============================================================================


def test_correlate_clusters_span_linked_within_boundary():
    """Verify clusters sharing span_id within <= 10s communication window are correlated."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 2, tzinfo=timezone.utc)

    c1 = _make_dummy_cluster("c1", "order-service", t0, span_id="span-456")
    c2 = _make_dummy_cluster("c2", "inventory-service", t1, span_id="span-456")

    candidates = correlate_clusters([c1, c2])
    assert len(candidates) == 1
    assert "SPAN_LINKED" in candidates[0].correlation_rules_used


def test_correlate_clusters_span_not_linked_if_distant():
    """Verify coincidental span_id far apart (>10s) with no shared trace does not merge."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 5, 0, tzinfo=timezone.utc)  # 300s apart

    c1 = _make_dummy_cluster("c1", "order-service", t0, span_id="span-dup")
    c2 = _make_dummy_cluster("c2", "inventory-service", t1, span_id="span-dup")

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    # 300s > 120s cascade window, and 300s > 10s span window -> NOT merged
    assert len(candidates) == 2


# ============================================================================
# 3. Temporal Cascade Correlation Tests (No Traces Available)
# ============================================================================


def test_correlate_clusters_temporal_cascade_within_window():
    """Verify distinct service clusters without traces are correlated when within cascade window (<= 120s)."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 1, 15, tzinfo=timezone.utc)  # 75s lag <= 120s

    c1 = _make_dummy_cluster("c1", "auth-service", t0, trace_id=None, pattern="JWT signing key invalid")
    c2 = _make_dummy_cluster("c2", "web-portal", t1, trace_id=None, pattern="User session rejected")

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 1
    cand = candidates[0]
    assert "TEMPORAL_CASCADE" in cand.correlation_rules_used
    assert len(cand.affected_services) == 2

    # Check that relationship is explicitly documented as temporal
    assert "temporal" in cand.uncertainty_reason.lower() or "chronologically" in cand.uncertainty_reason.lower()
    assert cand.is_root_cause_confirmed is False


def test_correlate_clusters_negative_isolation_outside_window():
    """Verify distinct service errors outside the cascade window (>120s) remain separate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 10, 0, tzinfo=timezone.utc)  # 600s lag > 120s

    c1 = _make_dummy_cluster("c1", "auth-service", t0, trace_id=None)
    c2 = _make_dummy_cluster("c2", "billing-service", t1, trace_id=None)

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 2


# ============================================================================
# 4. Out-of-Order Timestamps & Earliest Failure Identification
# ============================================================================


def test_correlate_clusters_handles_out_of_order_timestamps():
    """Verify clusters passed out of order are sorted chronologically to identify first failure."""
    t_earlier = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t_later = datetime(2026, 10, 10, 14, 0, 30, tzinfo=timezone.utc)

    # Pass later cluster first in the list
    c_later = _make_dummy_cluster("c_later", "frontend-app", t_later, trace_id="tr-xyz")
    c_earlier = _make_dummy_cluster("c_earlier", "backend-api", t_earlier, trace_id="tr-xyz")

    candidates = correlate_clusters([c_later, c_earlier])
    assert len(candidates) == 1
    cand = candidates[0]

    # First timeline event must be backend-api (earlier timestamp)
    assert cand.timeline_events[0].service_name == "backend-api"
    assert cand.timeline_events[0].is_earliest_observed_failure is True
    assert cand.timeline_events[0].event_timestamp == t_earlier
    assert cand.timeline_events[0].propagation_lag_seconds == 0.0

    # Second timeline event is frontend-app
    assert cand.timeline_events[1].service_name == "frontend-app"
    assert cand.timeline_events[1].is_earliest_observed_failure is False
    assert cand.timeline_events[1].propagation_lag_seconds == 30.0

    # Proven root cause is NEVER confirmed automatically
    assert cand.is_root_cause_confirmed is False
    assert cand.uncertainty_reason is not None


# ============================================================================
# 5. Evidence Provenance Retention Tests
# ============================================================================


def test_correlate_clusters_evidence_provenance():
    """Verify evidence items retain source log IDs, timestamps, snippets, content hashes, and rationales."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    c1 = _make_dummy_cluster("c1", "db-service", t0, error_count=2, pattern="Deadlock detected")

    candidates = correlate_clusters([c1])
    assert len(candidates) == 1
    cand = candidates[0]

    assert len(cand.evidence_items) >= 1
    first_ev = cand.evidence_items[0]
    assert first_ev.log_id == "c1_log_0"
    assert first_ev.timestamp == t0
    assert first_ev.service_name == "db-service"
    assert first_ev.snippet != ""
    assert first_ev.content_hash is not None
    assert "db-service" in first_ev.rationale


# ============================================================================
# 6. Empty Input Tests
# ============================================================================


def test_correlate_clusters_empty_input():
    """Verify empty cluster list returns empty candidates list."""
    assert correlate_clusters([]) == []


# ============================================================================
# 7. Deterministic Tie-Breaking & Cascade Proximity Tests (Phase 15 Step 1)
# ============================================================================


def test_correlate_clusters_deterministic_tie_breaker_reversed_input_order():
    """Verify identical timestamps resolve to the same earliest failure regardless of input order."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)

    # c_auth has service "auth-service" and log_id "c_auth_log_0"
    c_auth = _make_dummy_cluster("c_auth", "auth-service", t0, trace_id="shared-tr-1")
    # c_billing has service "billing-service" and log_id "c_billing_log_0"
    c_billing = _make_dummy_cluster("c_billing", "billing-service", t0, trace_id="shared-tr-1")

    # Run correlation with [c_auth, c_billing]
    candidates_forward = correlate_clusters([c_auth, c_billing])
    # Run correlation with reversed input [c_billing, c_auth]
    candidates_reversed = correlate_clusters([c_billing, c_auth])

    assert len(candidates_forward) == 1
    assert len(candidates_reversed) == 1

    cand_f = candidates_forward[0]
    cand_r = candidates_reversed[0]

    # Candidate IDs must match identically
    assert cand_f.candidate_id == cand_r.candidate_id

    # The earliest observed failure must be deterministically chosen ("auth-service" < "billing-service")
    assert cand_f.timeline_events[0].service_name == "auth-service"
    assert cand_r.timeline_events[0].service_name == "auth-service"
    assert cand_f.timeline_events[0].is_earliest_observed_failure is True
    assert cand_r.timeline_events[0].is_earliest_observed_failure is True

    # Entire event sequence must be identical
    assert len(cand_f.timeline_events) == len(cand_r.timeline_events)
    for ev_f, ev_r in zip(cand_f.timeline_events, cand_r.timeline_events):
        assert ev_f.service_name == ev_r.service_name
        assert ev_f.sample_log_id == ev_r.sample_log_id
        assert ev_f.is_earliest_observed_failure == ev_r.is_earliest_observed_failure


def test_correlate_clusters_temporal_cascade_cross_window_proximity():
    """Verify cluster 2 correlates with cluster 1 if its start is near cluster 1's end, even if start times are far apart."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t_end_c1 = datetime(2026, 10, 10, 14, 14, 0, tzinfo=timezone.utc)  # 840s after t0
    t_start_c2 = datetime(2026, 10, 10, 14, 14, 30, tzinfo=timezone.utc)  # 30s after t_end_c1, but 870s after t0

    # Cluster 1: checkout-service active from 14:00 to 14:14
    ev1_0 = LogEventItem(
        log_id="c1_ev0",
        timestamp=t0,
        service_name="checkout-service",
        log_level="ERROR",
        message="Slow connection pool",
    )
    ev1_1 = LogEventItem(
        log_id="c1_ev1",
        timestamp=t_end_c1,
        service_name="checkout-service",
        log_level="ERROR",
        message="Connection pool exhausted",
    )
    c1 = LogCluster(
        cluster_id="c1",
        service_name="checkout-service",
        pattern="Connection issue",
        pattern_hash="hash_c1",
        severity="ERROR",
        window_start=t0,
        window_end=t_end_c1,
        count=2,
        log_ids=["c1_ev0", "c1_ev1"],
        sample_messages=["Slow connection pool"],
        first_seen=t0,
        last_seen=t_end_c1,
        trace_ids=[],
        span_ids=[],
        events=[ev1_0, ev1_1],
    )

    # Cluster 2: notification-service starts at 14:14:30 (30s after c1_ev1)
    ev2_0 = LogEventItem(
        log_id="c2_ev0",
        timestamp=t_start_c2,
        service_name="notification-service",
        log_level="ERROR",
        message="Failed to enqueue order notification",
    )
    c2 = LogCluster(
        cluster_id="c2",
        service_name="notification-service",
        pattern="Failed notification",
        pattern_hash="hash_c2",
        severity="ERROR",
        window_start=t_start_c2,
        window_end=t_start_c2,
        count=1,
        log_ids=["c2_ev0"],
        sample_messages=["Failed notification"],
        first_seen=t_start_c2,
        last_seen=t_start_c2,
        trace_ids=[],
        span_ids=[],
        events=[ev2_0],
    )

    # With cascade_window_seconds = 120s:
    # Cluster start times are 870s apart (> 120s), but event proximity between c1 end and c2 start is 30s (<= 120s)
    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)

    assert len(candidates) == 1
    cand = candidates[0]
    assert "TEMPORAL_CASCADE" in cand.correlation_rules_used
    assert len(cand.affected_services) == 2

    # Earliest failure is checkout-service at t0
    assert cand.timeline_events[0].service_name == "checkout-service"
    assert cand.timeline_events[0].is_earliest_observed_failure is True
    assert cand.timeline_events[0].event_timestamp == t0

    # Notification service error appears in timeline with correct lag
    notif_event = [e for e in cand.timeline_events if e.service_name == "notification-service"][0]
    assert notif_event.is_earliest_observed_failure is False
    assert notif_event.propagation_lag_seconds == 870.0


def test_correlate_clusters_temporal_cascade_negative_isolated_events():
    """Verify clusters with events exceeding the cascade window do NOT correlate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 5, 0, tzinfo=timezone.utc)  # 300s > 120s

    c1 = _make_dummy_cluster("c1", "payment-service", t0, trace_id=None)
    c2 = _make_dummy_cluster("c2", "shipping-service", t1, trace_id=None)

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)

    # 300s gap exceeds 120s cascade window -> must NOT merge
    assert len(candidates) == 2
    for cand in candidates:
        assert "TEMPORAL_CASCADE" not in cand.correlation_rules_used


# ============================================================================
# 8. Request-ID / Correlation-ID Fallback Tests (Phase 15 Step 2)
# ============================================================================


def test_correlate_clusters_request_id_fallback_when_trace_absent():
    """Verify clusters sharing request_id in attributes correlate when trace_id is absent."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 8, 0, tzinfo=timezone.utc)  # 480s > 120s cascade window

    c1 = _make_dummy_cluster(
        "c1",
        "auth-service",
        t0,
        trace_id=None,
        attributes={"request_id": "req-xyz-100"},
        pattern="Authentication failed",
    )
    c2 = _make_dummy_cluster(
        "c2",
        "order-service",
        t1,
        trace_id=None,
        attributes={"request_id": "req-xyz-100"},
        pattern="Order submission blocked",
    )

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)

    assert len(candidates) == 1
    cand = candidates[0]
    assert "REQUEST_LINKED" in cand.correlation_rules_used
    assert len(cand.affected_services) == 2

    # Earliest failure must be auth-service
    assert cand.timeline_events[0].service_name == "auth-service"
    assert cand.timeline_events[0].is_earliest_observed_failure is True

    # Order service is marked downstream cascade
    order_svc = [s for s in cand.affected_services if s.service_name == "order-service"][0]
    assert order_svc.impact_level == "DOWNSTREAM_CASCADE"

    # Evidence items retain genuine trace_id as None (not overwritten)
    for ev in cand.evidence_items:
        assert ev.trace_id is None
    # Non-first-failure evidence rule is REQUEST_LINKED
    req_ev = [ev for ev in cand.evidence_items if ev.service_name == "order-service"][0]
    assert req_ev.correlation_rule == "REQUEST_LINKED"
    assert "req-xyz-100" in req_ev.rationale


def test_correlate_clusters_correlation_id_fallback_when_trace_absent():
    """Verify clusters sharing correlation_id in attributes correlate when trace_id is absent."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 7, 0, tzinfo=timezone.utc)  # 420s > 120s cascade window

    c1 = _make_dummy_cluster(
        "c1",
        "payment-service",
        t0,
        trace_id=None,
        attributes={"correlation_id": "corr-token-200"},
        pattern="Payment timeout",
    )
    c2 = _make_dummy_cluster(
        "c2",
        "ledger-service",
        t1,
        trace_id=None,
        attributes={"correlation_id": "corr-token-200"},
        pattern="Ledger sync deferred",
    )

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)

    assert len(candidates) == 1
    cand = candidates[0]
    assert "CORRELATION_ID_LINKED" in cand.correlation_rules_used
    assert len(cand.affected_services) == 2

    ledger_svc = [s for s in cand.affected_services if s.service_name == "ledger-service"][0]
    assert ledger_svc.impact_level == "DOWNSTREAM_CASCADE"

    corr_ev = [ev for ev in cand.evidence_items if ev.service_name == "ledger-service"][0]
    assert corr_ev.correlation_rule == "CORRELATION_ID_LINKED"
    assert "corr-token-200" in corr_ev.rationale


def test_correlate_clusters_missing_or_blank_identifiers_do_not_link():
    """Verify empty or whitespace-only identifiers do not create links."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 10, 0, tzinfo=timezone.utc)  # 600s > 120s

    c1 = _make_dummy_cluster("c1", "svc-a", t0, trace_id=None, attributes={"request_id": "   "})
    c2 = _make_dummy_cluster("c2", "svc-b", t1, trace_id=None, attributes={"request_id": ""})

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 2
    for cand in candidates:
        assert "REQUEST_LINKED" not in cand.correlation_rules_used


def test_correlate_clusters_non_string_identifiers_do_not_link():
    """Verify non-string identifier values (int, list, dict) are safely ignored."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 10, 0, tzinfo=timezone.utc)  # 600s > 120s

    c1 = _make_dummy_cluster("c1", "svc-a", t0, trace_id=None, attributes={"request_id": 12345})
    c2 = _make_dummy_cluster("c2", "svc-b", t1, trace_id=None, attributes={"request_id": 12345})

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 2


def test_correlate_clusters_different_identifiers_do_not_link():
    """Verify clusters with distinct request_id values do not correlate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 10, 0, tzinfo=timezone.utc)  # 600s > 120s

    c1 = _make_dummy_cluster("c1", "svc-a", t0, trace_id=None, attributes={"request_id": "req-AAA"})
    c2 = _make_dummy_cluster("c2", "svc-b", t1, trace_id=None, attributes={"request_id": "req-BBB"})

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 2


def test_correlate_clusters_request_and_correlation_id_namespaces_do_not_cross_link():
    """Verify request_id and correlation_id namespaces are strictly disjoint and cannot link."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 10, 0, tzinfo=timezone.utc)  # 600s > 120s

    # Same token string ("shared-token-999") but in different attribute namespaces
    c1 = _make_dummy_cluster("c1", "svc-a", t0, trace_id=None, attributes={"request_id": "shared-token-999"})
    c2 = _make_dummy_cluster("c2", "svc-b", t1, trace_id=None, attributes={"correlation_id": "shared-token-999"})

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 2


def test_correlate_clusters_genuine_trace_id_takes_precedence_and_is_not_replaced():
    """Verify genuine trace_id takes precedence as TRACE_LINKED and is never replaced by request_id."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 2, 0, tzinfo=timezone.utc)

    c1 = _make_dummy_cluster(
        "c1",
        "api-svc",
        t0,
        trace_id="genuine-trace-1",
        attributes={"request_id": "req-fallback-1"},
    )
    c2 = _make_dummy_cluster(
        "c2",
        "backend-svc",
        t1,
        trace_id="genuine-trace-1",
        attributes={"request_id": "req-fallback-1"},
    )

    candidates = correlate_clusters([c1, c2])
    assert len(candidates) == 1
    cand = candidates[0]
    assert "TRACE_LINKED" in cand.correlation_rules_used
    assert "REQUEST_LINKED" not in cand.correlation_rules_used
    assert cand.timeline_events[0].trace_id == "genuine-trace-1"


def test_correlate_clusters_request_id_deterministic_reversed_input_order():
    """Verify request_id correlation produces identical candidate_id and order regardless of input order."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)

    c_auth = _make_dummy_cluster(
        "c_auth",
        "auth-service",
        t0,
        trace_id=None,
        attributes={"request_id": "shared-req-42"},
    )
    c_billing = _make_dummy_cluster(
        "c_billing",
        "billing-service",
        t0,
        trace_id=None,
        attributes={"request_id": "shared-req-42"},
    )

    cand_forward = correlate_clusters([c_auth, c_billing])[0]
    cand_reversed = correlate_clusters([c_billing, c_auth])[0]

    assert cand_forward.candidate_id == cand_reversed.candidate_id
    assert cand_forward.timeline_events[0].service_name == "auth-service"
    assert cand_reversed.timeline_events[0].service_name == "auth-service"
    assert cand_forward.timeline_events[0].is_earliest_observed_failure is True
    assert cand_reversed.timeline_events[0].is_earliest_observed_failure is True


# ============================================================================
# 9. Phase 15 Step 3: Evidence Deduplication Regression Tests
# ============================================================================


def test_deduplicate_evidence_exact_duplicate_is_deduplicated():
    """Verify true duplicate evidence items with identical identity and provenance are collapsed into one."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    ev1 = CorrelatedEvidenceItem(
        evidence_type="CULPRIT_LOG",
        log_id="log-exact-1",
        timestamp=t0,
        service_name="payment-svc",
        log_level="ERROR",
        snippet="Timeout connecting to gateway",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest failure",
        content_hash="hash-123",
        trace_id="tr-abc",
    )
    ev2 = CorrelatedEvidenceItem(
        evidence_type="CULPRIT_LOG",
        log_id="log-exact-1",
        timestamp=t0,
        service_name="payment-svc",
        log_level="ERROR",
        snippet="Timeout connecting to gateway",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest failure",
        content_hash="hash-123",
        trace_id="tr-abc",
    )

    deduped = deduplicate_evidence_items([ev1, ev2])
    assert len(deduped) == 1
    assert deduped[0].log_id == "log-exact-1"
    assert deduped[0].content_hash == "hash-123"
    assert deduped[0].trace_id == "tr-abc"


def test_deduplicate_evidence_distinct_source_log_ids_with_identical_messages_remain_separate():
    """Verify distinct log IDs with identical message, service, and timestamp remain separate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    ev1 = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-distinct-1",
        timestamp=t0,
        service_name="auth-svc",
        log_level="ERROR",
        snippet="JWT validation failed",
        correlation_rule="LOCAL_SERVICE",
        rationale="Auth error",
    )
    ev2 = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-distinct-2",
        timestamp=t0,
        service_name="auth-svc",
        log_level="ERROR",
        snippet="JWT validation failed",
        correlation_rule="LOCAL_SERVICE",
        rationale="Auth error",
    )

    deduped = deduplicate_evidence_items([ev1, ev2])
    assert len(deduped) == 2
    log_ids = [e.log_id for e in deduped]
    assert "log-distinct-1" in log_ids
    assert "log-distinct-2" in log_ids


def test_deduplicate_evidence_different_evidence_types_remain_separate():
    """Verify the same log record serving different evidence roles (e.g. CULPRIT vs SPIKE) remains separate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    ev_culprit = CorrelatedEvidenceItem(
        evidence_type="CULPRIT_LOG",
        log_id="log-shared-1",
        timestamp=t0,
        service_name="api-gateway",
        log_level="CRITICAL",
        snippet="Service unavailable 503",
        correlation_rule="FIRST_FAILURE",
        rationale="Root failure point",
    )
    ev_spike = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-shared-1",
        timestamp=t0,
        service_name="api-gateway",
        log_level="CRITICAL",
        snippet="Service unavailable 503",
        correlation_rule="FIRST_FAILURE",
        rationale="Part of spike cluster",
    )

    deduped = deduplicate_evidence_items([ev_culprit, ev_spike])
    assert len(deduped) == 2
    types = {e.evidence_type for e in deduped}
    assert types == {"CULPRIT_LOG", "CORRELATION_SPIKE"}


def test_deduplicate_evidence_different_correlation_rules_remain_separate():
    """Verify the same log record with different correlation rules remains separate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    ev_trace = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-rules-1",
        timestamp=t0,
        service_name="order-svc",
        log_level="ERROR",
        snippet="Database error",
        correlation_rule="TRACE_LINKED",
        rationale="Linked via trace",
    )
    ev_cascade = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-rules-1",
        timestamp=t0,
        service_name="order-svc",
        log_level="ERROR",
        snippet="Database error",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="Linked via temporal cascade",
    )

    deduped = deduplicate_evidence_items([ev_trace, ev_cascade])
    assert len(deduped) == 2
    rules = {e.correlation_rule for e in deduped}
    assert rules == {"TRACE_LINKED", "TEMPORAL_CASCADE"}


def test_deduplicate_evidence_invariant_to_input_ordering():
    """Verify evidence deduplication output is identical regardless of input order."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 2, 0, tzinfo=timezone.utc)

    ev_a = CorrelatedEvidenceItem(
        evidence_type="CULPRIT_LOG",
        log_id="log-a",
        timestamp=t0,
        service_name="svc-a",
        log_level="ERROR",
        snippet="Error A",
        correlation_rule="FIRST_FAILURE",
        rationale="First error in A",
    )
    ev_a_dup = CorrelatedEvidenceItem(
        evidence_type="CULPRIT_LOG",
        log_id="log-a",
        timestamp=t0,
        service_name="svc-a",
        log_level="ERROR",
        snippet="Error A",
        correlation_rule="FIRST_FAILURE",
        rationale="First error in A (duplicate)",
    )
    ev_b = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-b",
        timestamp=t1,
        service_name="svc-b",
        log_level="ERROR",
        snippet="Error B",
        correlation_rule="REQUEST_LINKED",
        rationale="Linked to A via request_id",
    )

    order_1 = deduplicate_evidence_items([ev_a, ev_a_dup, ev_b])
    order_2 = deduplicate_evidence_items([ev_b, ev_a_dup, ev_a])
    order_3 = deduplicate_evidence_items([ev_a_dup, ev_b, ev_a])

    assert len(order_1) == 2
    assert [e.log_id for e in order_1] == [e.log_id for e in order_2] == [e.log_id for e in order_3]
    assert [e.rationale for e in order_1] == [e.rationale for e in order_2] == [e.rationale for e in order_3]


def test_deduplicate_evidence_conflicting_provenance_handled_conservatively():
    """Verify that records with identical identity but conflicting content hashes are preserved conservatively."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    ev1 = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-conflict-1",
        timestamp=t0,
        service_name="cache-svc",
        log_level="ERROR",
        snippet="Connection reset",
        correlation_rule="LOCAL_SERVICE",
        rationale="Evidence hash variant 1",
        content_hash="sha256-hash-alpha",
    )
    ev2 = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-conflict-1",
        timestamp=t0,
        service_name="cache-svc",
        log_level="ERROR",
        snippet="Connection reset",
        correlation_rule="LOCAL_SERVICE",
        rationale="Evidence hash variant 2",
        content_hash="sha256-hash-beta",
    )

    deduped = deduplicate_evidence_items([ev1, ev2])
    # Both records preserved to prevent silent loss of verifiable hash provenance
    assert len(deduped) == 2
    hashes = {e.content_hash for e in deduped}
    assert hashes == {"sha256-hash-alpha", "sha256-hash-beta"}


def test_deduplicate_evidence_compatible_provenance_merged():
    """Verify that partial compatible provenance (missing hash on one, missing trace on other) is merged."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    ev1 = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-compat-1",
        timestamp=t0,
        service_name="search-svc",
        log_level="ERROR",
        snippet="Timeout querying index",
        correlation_rule="SPAN_LINKED",
        rationale="Span linked event",
        content_hash="sha256-compat-hash",
        trace_id=None,
    )
    ev2 = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-compat-1",
        timestamp=t0,
        service_name="search-svc",
        log_level="ERROR",
        snippet="Timeout querying index",
        correlation_rule="SPAN_LINKED",
        rationale="Span linked event with trace",
        content_hash=None,
        trace_id="trace-compat-999",
    )

    deduped = deduplicate_evidence_items([ev1, ev2])
    assert len(deduped) == 1
    item = deduped[0]
    assert item.content_hash == "sha256-compat-hash"
    assert item.trace_id == "trace-compat-999"


def test_correlate_clusters_candidate_evidence_deduplicated_and_first_failure_preserved():
    """Verify end-to-end candidate correlation generates clean deduplicated evidence items."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 1, 0, tzinfo=timezone.utc)

    c1 = _make_dummy_cluster("c1", "db-service", t0, error_count=5, trace_id="trace-e2e")
    c2 = _make_dummy_cluster("c2", "api-service", t1, error_count=5, trace_id="trace-e2e")

    candidates = correlate_clusters([c1, c2])
    assert len(candidates) == 1
    cand = candidates[0]

    # Verify first failure item exists as CULPRIT_LOG / FIRST_FAILURE
    first_ev = [e for e in cand.evidence_items if e.evidence_type == "CULPRIT_LOG"][0]
    assert first_ev.correlation_rule == "FIRST_FAILURE"
    assert first_ev.log_id == "c1_log_0"

    # Verify no exact duplicate evidence entries exist
    seen_identities = set()
    for e in cand.evidence_items:
        ident = (e.log_id, e.evidence_type, e.correlation_rule)
        assert ident not in seen_identities
        seen_identities.add(ident)


# ============================================================================
# 8. RootLens Phase 9: Deterministic Event Correlation Verification Scenarios
# ============================================================================


def test_scenario_1_related_events_from_same_service():
    """Scenario 1: Related error events from the same service within temporal window correlate into one incident."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 8, tzinfo=timezone.utc)

    # payment-service error cluster 1: Connection pool timeout
    c1 = _make_dummy_cluster("c1", "payment-service", t0, pattern="Connection pool timeout")
    # payment-service error cluster 2: Failed to process transaction (8s later)
    c2 = _make_dummy_cluster("c2", "payment-service", t1, pattern="Failed to process transaction")

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)

    # Must be unified into a single candidate under LOCAL_SERVICE
    assert len(candidates) == 1
    cand = candidates[0]
    assert "LOCAL_SERVICE" in cand.correlation_rules_used
    assert len(cand.timeline_events) == 2
    assert cand.timeline_events[0].service_name == "payment-service"
    assert cand.timeline_events[0].is_earliest_observed_failure is True
    assert cand.timeline_events[1].service_name == "payment-service"
    assert cand.timeline_events[1].propagation_lag_seconds == 8.0
    assert cand.is_root_cause_confirmed is False


def test_scenario_2_related_events_across_multiple_services():
    """Scenario 2: Related events across multiple services sharing trace context merge deterministically."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 4, tzinfo=timezone.utc)

    c_auth = _make_dummy_cluster("c_auth", "auth-service", t0, trace_id="shared-trace-999", pattern="Auth token expired")
    c_gateway = _make_dummy_cluster("c_gateway", "api-gateway", t1, trace_id="shared-trace-999", pattern="502 Bad Gateway")

    candidates = correlate_clusters([c_auth, c_gateway])

    assert len(candidates) == 1
    cand = candidates[0]
    assert "TRACE_LINKED" in cand.correlation_rules_used
    assert len(cand.affected_services) == 2
    primary = [s for s in cand.affected_services if s.impact_level == "PRIMARY_CULPRIT"][0]
    downstream = [s for s in cand.affected_services if s.impact_level == "DOWNSTREAM_CASCADE"][0]
    assert primary.service_name == "auth-service"
    assert downstream.service_name == "api-gateway"


def test_scenario_3_events_outside_configured_time_window():
    """Scenario 3: Events occurring outside the configured time window are NOT arbitrarily merged."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 15, 0, tzinfo=timezone.utc)  # 900s > 120s window

    c1 = _make_dummy_cluster("c1", "cart-service", t0, trace_id=None)
    c2 = _make_dummy_cluster("c2", "shipping-service", t1, trace_id=None)

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 2


def test_scenario_4_unrelated_events_close_together_blocked_by_disjoint_traces():
    """Scenario 4: Unrelated events close in time with disjoint traces are strictly kept separate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 2, tzinfo=timezone.utc)  # Only 2s apart!

    # Cluster 1 belongs to user checkout trace
    c1 = _make_dummy_cluster("c1", "checkout-service", t0, trace_id="trace-checkout-user-1")
    # Cluster 2 belongs to background analytics trace
    c2 = _make_dummy_cluster("c2", "analytics-service", t1, trace_id="trace-analytics-sync-2")

    # Even though time diff is 2s (well within 120s cascade window), disjoint traces MUST block merging
    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 2


def test_scenario_5_out_of_order_timestamps():
    """Scenario 5: Out-of-order events are deterministically sorted chronologically."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 5, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 10, 14, 0, 10, tzinfo=timezone.utc)

    c0 = _make_dummy_cluster("c0", "db", t0, trace_id="tr-1")
    c1 = _make_dummy_cluster("c1", "api", t1, trace_id="tr-1")
    c2 = _make_dummy_cluster("c2", "web", t2, trace_id="tr-1")

    # Pass in completely reversed order
    candidates = correlate_clusters([c2, c1, c0])
    assert len(candidates) == 1
    cand = candidates[0]

    timeline = cand.timeline_events
    assert [e.service_name for e in timeline] == ["db", "api", "web"]
    assert timeline[0].is_earliest_observed_failure is True
    assert timeline[0].propagation_lag_seconds == 0.0
    assert timeline[1].propagation_lag_seconds == 5.0
    assert timeline[2].propagation_lag_seconds == 10.0


def test_scenario_6_duplicate_log_records():
    """Scenario 6: Ingestion duplicate log records do not artificially inflate event counts."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    event1 = LogEventItem(
        log_id="log-dup-100",
        timestamp=t0,
        service_name="payment",
        log_level="ERROR",
        message="Payment timeout error",
    )
    # Exact duplicate record
    event1_dup = LogEventItem(
        log_id="log-dup-100",
        timestamp=t0,
        service_name="payment",
        log_level="ERROR",
        message="Payment timeout error",
    )

    clusters = cluster_log_events([event1, event1_dup])
    assert len(clusters) == 1
    assert clusters[0].count == 1  # Deduplicated from 2 to 1!

    cand = correlate_clusters(clusters)[0]
    assert len(cand.timeline_events) == 1


def test_scenario_7_missing_service_identifiers():
    """Scenario 7: Missing or blank service identifiers are safely represented without failure."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    ev_no_svc = LogEventItem(
        log_id="log-no-svc",
        timestamp=t0,
        service_name="",  # Empty service identifier
        log_level="ERROR",
        message="Unclassified system failure",
    )

    clusters = cluster_log_events([ev_no_svc])
    assert clusters[0].service_name == "unassigned"

    cand = correlate_clusters(clusters)[0]
    assert cand.affected_services[0].service_name == "unassigned"
    assert "unassigned_service" in cand.timeline_events[0].attributes["missing_telemetry"]


def test_scenario_8_missing_trace_identifiers():
    """Scenario 8: Events without trace context correlate via fallback rules and explicitly document gaps."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 15, tzinfo=timezone.utc)

    c1 = _make_dummy_cluster("c1", "upstream", t0, trace_id=None)
    c2 = _make_dummy_cluster("c2", "downstream", t1, trace_id=None)

    candidates = correlate_clusters([c1, c2], cascade_window_seconds=120)
    assert len(candidates) == 1
    cand = candidates[0]
    assert "TEMPORAL_CASCADE" in cand.correlation_rules_used

    # Check that missing trace context is explicitly flagged
    for ev in cand.timeline_events:
        assert "missing_trace_id" in ev.attributes["missing_telemetry"]
        assert ev.attributes["relationship_type"] in ("TEMPORAL_ASSOCIATION", "SUSPECTED_DEPENDENCY")

    # Assessment must disclaim causality
    assert cand.is_root_cause_confirmed is False


def test_scenario_9_multiple_anomalies_associated_with_one_incident():
    """Scenario 9: Multiple LogBERT sequence anomalies in a failure cascade unite into one incident candidate."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 20, tzinfo=timezone.utc)

    # Anomaly 1 in payment-service
    c_anom1 = _make_dummy_cluster(
        "cl-logbert-sess1-202610101400",
        "payment-service",
        t0,
        pattern="Anomalous sequence 1",
        attributes={
            "model": "LogBERT",
            "anomaly_score": 0.085,
            "threshold": 0.0388,
            "is_mlkp_mismatch": True,
            "mlkp_top_candidates": [{"event": "Expected Template A"}],
        },
    )
    # Anomaly 2 in order-service (cascading 20s later)
    c_anom2 = _make_dummy_cluster(
        "cl-logbert-sess2-202610101400",
        "order-service",
        t1,
        pattern="Anomalous sequence 2",
        attributes={
            "model": "LogBERT",
            "anomaly_score": 0.062,
            "threshold": 0.0388,
            "explanation_type": "EMBEDDING_DISTANCE",
        },
    )

    candidates = correlate_clusters([c_anom1, c_anom2], cascade_window_seconds=120)
    assert len(candidates) == 1
    cand = candidates[0]

    assert "SEQUENCE_ANOMALY" in cand.correlation_rules_used
    assert len(cand.timeline_events) == 2
    assert all(e.event_type == "SEQUENCE_ANOMALY" for e in cand.timeline_events)
    assert cand.is_root_cause_confirmed is False


def test_scenario_10_one_anomaly_not_merged_into_unrelated_incident():
    """Scenario 10: Anomaly distant in time from an incident remains separate."""
    t_incident = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t_distant_anomaly = datetime(2026, 10, 10, 16, 0, 0, tzinfo=timezone.utc)  # 2 hours later!

    c_inc = _make_dummy_cluster("c_inc", "payment-service", t_incident, pattern="Deadlock")
    c_anom = _make_dummy_cluster(
        "cl-logbert-distant",
        "data-service",
        t_distant_anomaly,
        pattern="Anomalous batch",
        attributes={"model": "LogBERT", "anomaly_score": 0.09},
    )

    candidates = correlate_clusters([c_inc, c_anom], cascade_window_seconds=120)
    assert len(candidates) == 2


def test_scenario_11_insufficient_telemetry():
    """Scenario 11: Single isolated log event evaluates to SPARSE_EVIDENCE with unconfirmed root cause."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    c_sparse = _make_dummy_cluster("c_sparse", "worker-service", t0, error_count=1, pattern="Isolated worker error")

    candidates = correlate_clusters([c_sparse])
    assert len(candidates) == 1
    cand = candidates[0]

    assert cand.assessment.classification.value in ("INSUFFICIENT", "SPARSE_EVIDENCE")
    assert cand.is_root_cause_confirmed is False
    assert "insufficient" in cand.uncertainty_reason.lower() or "sparse" in cand.uncertainty_reason.lower()


def test_scenario_12_existing_incident_timeline_integrity():
    """Scenario 12: evaluate_incident_grouping deterministically respects existing incident boundaries."""
    from unittest.mock import MagicMock

    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 5, 0, tzinfo=timezone.utc)

    # Mock an existing active incident
    mock_incident = MagicMock()
    mock_incident.id = uuid.uuid4()
    mock_incident.status = "INVESTIGATING"
    mock_incident.started_at = t0
    mock_incident.ended_at = t1

    # Incident has events with trace-101 and service payment-service
    ev1 = MagicMock()
    ev1.event_timestamp = t0
    ev1.trace_id = "trace-101"
    ev1.attributes = {"trace_id": "trace-101"}
    mock_incident.events = [ev1]

    evid1 = MagicMock()
    evid1.opensearch_doc_id = "doc-already-present"
    evid1.trace_id = "trace-101"
    evid1.metadata_ = {"trace_id": "trace-101"}
    mock_incident.evidence = [evid1]

    aff1 = MagicMock()
    aff1.service_name = "payment-service"
    mock_incident.affected_services = [aff1]

    # Test 12.1: Duplicate log record
    dup_event = LogEventItem(
        log_id="doc-already-present",
        timestamp=t0,
        service_name="payment-service",
        message="duplicate log",
    )
    res_dup = evaluate_incident_grouping(mock_incident, dup_event)
    assert res_dup.matches is True
    assert res_dup.is_duplicate_log is True
    assert res_dup.match_rule == "DUPLICATE_LOG_RECORD"

    # Test 12.2: Matching distributed trace
    trace_event = LogEventItem(
        log_id="doc-new-trace",
        timestamp=t0,
        service_name="auth-service",
        trace_id="trace-101",
        message="trace matched log",
    )
    res_trace = evaluate_incident_grouping(mock_incident, trace_event)
    assert res_trace.matches is True
    assert res_trace.match_rule == "TRACE_MATCH"
    assert res_trace.confidence == 1.0

    # Test 12.3: Disjoint trace conflict
    disjoint_event = LogEventItem(
        log_id="doc-wrong-trace",
        timestamp=t0,
        service_name="payment-service",
        trace_id="trace-completely-different",
        message="conflicting trace log",
    )
    res_disjoint = evaluate_incident_grouping(mock_incident, disjoint_event)
    assert res_disjoint.matches is False
    assert res_disjoint.match_rule == "DISJOINT_TRACE_CONFLICT"

    # Test 12.4: Outside temporal window
    outside_event = LogEventItem(
        log_id="doc-outside",
        timestamp=datetime(2026, 10, 10, 16, 0, 0, tzinfo=timezone.utc),  # 2 hours later
        service_name="payment-service",
        message="late log",
    )
    res_outside = evaluate_incident_grouping(mock_incident, outside_event)
    assert res_outside.matches is False
    assert res_outside.match_rule == "OUTSIDE_WINDOW"



