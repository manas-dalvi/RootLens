from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from typing import Optional, Tuple
import uuid
import pytest

from app.models.evidence import Evidence
from app.models.incident_event import IncidentEvent
from app.schemas.incident import (
    CorrelatedEvidenceItemResponse,
    CorrelatedTimelineEventResponse,
)
from app.services.correlation_service import (
    CorrelatedEvidenceItem,
    CorrelatedTimelineEvent,
)
from app.services.evidence_analysis_service import (
    EvidenceReference,
    EvidenceSufficiencyAssessment,
    EvidenceSufficiencyClassification,
    evaluate_evidence_sufficiency,
)


# ============================================================================
# Test Fixtures & Helpers
# ============================================================================

def make_timestamps(base: Optional[datetime] = None) -> Tuple[datetime, datetime, datetime]:
    t0 = base or datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=15)
    t2 = t0 + timedelta(seconds=45)
    return t0, t1, t2


# ============================================================================
# 1. Relevant Shared Trace Identifiers Produce TRACE_LINKED
# ============================================================================

def test_shared_trace_across_services_produces_trace_linked():
    """Verify that relevant shared trace ID bridging across distinct services yields TRACE_LINKED."""
    t0, t1, t2 = make_timestamps()
    shared_trace = "trace-cross-payment-gateway-100"

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-pay-01",
        timestamp=t0,
        service_name="payment-service",
        log_level="CRITICAL",
        snippet="Database connection pool timeout",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest failure",
        content_hash="hash_pay_01",
        trace_id=shared_trace,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-gw-01",
        timestamp=t1,
        service_name="api-gateway",
        log_level="ERROR",
        snippet="504 Gateway Timeout forwarding /checkout",
        correlation_rule="TRACE_LINKED",
        rationale="Shared trace",
        content_hash="hash_gw_01",
        trace_id=shared_trace,
    )

    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="payment-service",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="Pool exhausted",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-pay",
        trace_id=shared_trace,
        sample_log_id="doc-pay-01",
    )
    tl2 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="api-gateway",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Gateway timeout",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=15.0,
        cluster_id="cl-gw",
        trace_id=shared_trace,
        sample_log_id="doc-gw-01",
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=[ev1, ev2],
        timeline_events=[tl1, tl2],
    )

    assert assessment.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert assessment.has_shared_trace is True
    assert assessment.shared_trace_ids == [shared_trace]
    assert assessment.distinct_service_count == 2
    assert assessment.distinct_services == ["api-gateway", "payment-service"]
    assert assessment.distinct_evidence_count == 2
    assert assessment.earliest_failure_service == "payment-service"
    assert assessment.earliest_failure_log_id == "doc-pay-01"
    assert assessment.is_root_cause_confirmed is False
    assert any("[Hypothesis]" in h for h in assessment.inferred_hypotheses)
    assert "payment-service" in assessment.inferred_hypotheses[0]
    assert "api-gateway" in assessment.inferred_hypotheses[0]
    assert shared_trace in assessment.inferred_hypotheses[0]


# ============================================================================
# 2. Temporal Correlation Without Shared Trace Linkage Produces TEMPORAL_ONLY
# ============================================================================

def test_temporal_cascade_without_trace_produces_temporal_only():
    """Verify that multi-service failure cascade without shared trace IDs yields TEMPORAL_ONLY."""
    t0, t1, _ = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-auth-01",
        timestamp=t0,
        service_name="auth-service",
        log_level="CRITICAL",
        snippet="Redis cluster connection refused",
        correlation_rule="FIRST_FAILURE",
        rationale="Initial failure",
        content_hash="hash_auth",
        trace_id=None,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-user-01",
        timestamp=t1,
        service_name="user-service",
        log_level="ERROR",
        snippet="Token validation timed out",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="Cascade in temporal window",
        content_hash="hash_user",
        trace_id=None,
    )

    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="auth-service",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="Redis down",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-auth",
        sample_log_id="doc-auth-01",
    )
    tl2 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="user-service",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Token validation failure",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=15.0,
        cluster_id="cl-user",
        sample_log_id="doc-user-01",
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=[ev1, ev2],
        timeline_events=[tl1, tl2],
    )

    assert assessment.classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY
    assert assessment.has_shared_trace is False
    assert assessment.shared_trace_ids == []
    assert assessment.distinct_service_count == 2
    assert assessment.distinct_services == ["auth-service", "user-service"]
    assert assessment.is_root_cause_confirmed is False
    assert "Diagnostic uncertainty: Moderate" in assessment.uncertainty_statement
    assert any("temporal proximity" in sig.lower() for sig in assessment.supporting_signals)
    assert any("trace_id" in sig.lower() for sig in assessment.missing_signals)


# ============================================================================
# 3. Sparse Evidence Produces INSUFFICIENT
# ============================================================================

def test_single_isolated_log_produces_insufficient():
    """Verify that a single isolated log record without corroborating evidence yields INSUFFICIENT."""
    t0, _, _ = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-single-01",
        timestamp=t0,
        service_name="inventory-service",
        log_level="ERROR",
        snippet="Null pointer in StockChecker",
        correlation_rule="FIRST_FAILURE",
        rationale="Single log",
        content_hash="hash_inv",
        trace_id="trace-single",
    )

    assessment = evaluate_evidence_sufficiency(evidence_items=[ev1], timeline_events=[])

    assert assessment.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert assessment.distinct_evidence_count == 1
    assert assessment.is_root_cause_confirmed is False
    assert "Diagnostic uncertainty: High" in assessment.uncertainty_statement
    assert any("fewer than 2" in sig for sig in assessment.missing_signals)


def test_empty_inputs_produce_insufficient():
    """Verify that empty inputs gracefully return INSUFFICIENT without crashing."""
    assessment = evaluate_evidence_sufficiency(evidence_items=[], timeline_events=[])

    assert assessment.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert assessment.distinct_evidence_count == 0
    assert assessment.distinct_service_count == 0
    assert assessment.is_root_cause_confirmed is False
    assert assessment.evidence_references == []
    assert "Diagnostic uncertainty: High" in assessment.uncertainty_statement


def test_inconsistent_timeline_produces_insufficient():
    """Verify that timeline with backward timestamp decreases yields INSUFFICIENT."""
    t0, t1, _ = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-01",
        timestamp=t0,
        service_name="svc-a",
        log_level="ERROR",
        snippet="Err 1",
        correlation_rule="FIRST_FAILURE",
        rationale="Err 1",
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-02",
        timestamp=t1,
        service_name="svc-b",
        log_level="ERROR",
        snippet="Err 2",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="Err 2",
    )

    # Inconsistent: seq 1 has earlier timestamp than seq 0!
    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t1,  # Later
        service_name="svc-a",
        event_type="FIRST_ERROR",
        severity="ERROR",
        summary="Event 1",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-1",
    )
    tl2 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t0,  # Earlier (contradicts monotonic sequence!)
        service_name="svc-b",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Event 2",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=10.0,
        cluster_id="cl-2",
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=[ev1, ev2],
        timeline_events=[tl1, tl2],
    )

    assert assessment.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert assessment.is_timeline_temporally_consistent is False
    assert "timeline consistency validation failed" in assessment.uncertainty_statement


# ============================================================================
# 4. Missing Trace IDs and Nullable Fields Handled Safely
# ============================================================================

def test_missing_and_nullable_fields_handled_safely():
    """Verify that null/missing trace_id, span_id, content_hash, and naive datetimes are safely handled."""
    naive_t0 = datetime(2026, 10, 10, 14, 0, 0)  # naive datetime
    naive_t1 = datetime(2026, 10, 10, 14, 0, 20)

    # Use dataclasses or raw dictionaries with missing optional fields
    ev1 = CorrelatedEvidenceItem(
        evidence_type="CULPRIT_LOG",
        log_id="doc-none-1",
        timestamp=naive_t0,
        service_name="svc-x",
        log_level="ERROR",
        snippet="Some error",
        correlation_rule="FIRST_FAILURE",
        rationale="Initial error",
        content_hash=None,
        trace_id=None,
    )
    ev2 = CorrelatedEvidenceItem(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-none-2",
        timestamp=naive_t1,
        service_name="svc-y",
        log_level="ERROR",
        snippet="Another error",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="Follow-up",
        content_hash=None,
        trace_id="",  # Empty string should be treated as None
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=[ev1, ev2],
        timeline_events=[],
    )

    assert assessment.classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY
    assert assessment.trace_ids_found == []
    assert assessment.has_shared_trace is False
    assert len(assessment.evidence_references) == 2
    assert assessment.evidence_references[0].trace_id is None
    assert assessment.evidence_references[1].trace_id is None
    # Timestamps normalized to UTC
    assert assessment.evidence_references[0].timestamp.tzinfo == timezone.utc


# ============================================================================
# 5. Unrelated Shared Trace IDs Do Not Produce False TRACE_LINKED
# ============================================================================

def test_unrelated_shared_trace_ids_do_not_produce_false_trace_linked():
    """Verify that shared trace IDs on unrelated auxiliary services do not falsely upgrade a cascade."""
    t0, t1, t2 = make_timestamps()

    # The actual failure cascade is between billing-service and notification-service (neither has trace-unrelated)
    tl_bill = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="billing-service",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="Billing DB timeout",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-bill",
        trace_id=None,
        sample_log_id="doc-bill",
    )
    tl_notif = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="notification-service",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Failed to send receipt",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=15.0,
        cluster_id="cl-notif",
        trace_id=None,
        sample_log_id="doc-notif",
    )

    ev_bill = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-bill",
        timestamp=t0,
        service_name="billing-service",
        log_level="CRITICAL",
        snippet="Billing DB lock",
        correlation_rule="FIRST_FAILURE",
        rationale="Billing failed",
        trace_id=None,
    )
    ev_notif = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-notif",
        timestamp=t1,
        service_name="notification-service",
        log_level="ERROR",
        snippet="Notification failed",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="Cascade failed",
        trace_id=None,
    )

    # An auxiliary unrelated service record with a trace ID (e.g. background metrics collector)
    # Even if two background collector logs share 'trace-unrelated', it does NOT connect billing and notification!
    ev_aux1 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-aux-1",
        timestamp=t2,
        service_name="metrics-collector",
        log_level="WARN",
        snippet="Collector buffer warning",
        correlation_rule="LOCAL_SERVICE",
        rationale="Auxiliary",
        trace_id="trace-unrelated-metrics",
    )
    ev_aux2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-aux-2",
        timestamp=t2,
        service_name="metrics-collector",
        log_level="WARN",
        snippet="Collector retry",
        correlation_rule="LOCAL_SERVICE",
        rationale="Auxiliary 2",
        trace_id="trace-unrelated-metrics",
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=[ev_bill, ev_notif, ev_aux1, ev_aux2],
        timeline_events=[tl_bill, tl_notif],
    )

    # Must NOT be TRACE_LINKED because 'trace-unrelated-metrics' is only in metrics-collector and does not bridge billing/notification!
    assert assessment.classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY
    assert assessment.has_shared_trace is False
    assert assessment.shared_trace_ids == []
    assert "metrics-collector" in assessment.distinct_services
    assert "billing-service" in assessment.distinct_services


# ============================================================================
# 6. Evidence References Have Deterministic Ordering Regardless of Input Ordering
# ============================================================================

def test_evidence_references_deterministic_ordering_invariant():
    """Verify that evidence references sort deterministically regardless of how items are ordered in the input."""
    t0, t1, t2 = make_timestamps()

    ev_a = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-a",
        timestamp=t0,
        service_name="payment-service",
        log_level="ERROR",
        snippet="Err A",
        correlation_rule="FIRST_FAILURE",
        rationale="A",
    )
    ev_b = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-b",
        timestamp=t1,
        service_name="auth-service",
        log_level="ERROR",
        snippet="Err B",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="B",
    )
    ev_c = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-c",
        timestamp=t2,
        service_name="frontend",
        log_level="ERROR",
        snippet="Err C",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="C",
    )

    # Evaluate with permutation 1: [A, B, C]
    res1 = evaluate_evidence_sufficiency([ev_a, ev_b, ev_c])
    # Permutation 2: [C, A, B]
    res2 = evaluate_evidence_sufficiency([ev_c, ev_a, ev_b])
    # Permutation 3: [B, C, A]
    res3 = evaluate_evidence_sufficiency([ev_b, ev_c, ev_a])

    assert res1.evidence_references == res2.evidence_references
    assert res2.evidence_references == res3.evidence_references
    # Expected chronological order: doc-a (t0), doc-b (t1), doc-c (t2)
    assert [r.log_id for r in res1.evidence_references] == ["doc-a", "doc-b", "doc-c"]


# ============================================================================
# 7. Repeated Evaluation of Identical Inputs Returns Identical Assessments
# ============================================================================

def test_idempotent_evaluation_purity():
    """Verify that evaluating identical inputs repeatedly produces strictly identical results (pure function)."""
    t0, t1, _ = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-1",
        timestamp=t0,
        service_name="service-1",
        log_level="ERROR",
        snippet="Error msg",
        correlation_rule="FIRST_FAILURE",
        rationale="Rationale 1",
        trace_id="tr-1",
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-2",
        timestamp=t1,
        service_name="service-2",
        log_level="ERROR",
        snippet="Error msg 2",
        correlation_rule="TRACE_LINKED",
        rationale="Rationale 2",
        trace_id="tr-1",
    )

    run1 = evaluate_evidence_sufficiency([ev1, ev2])
    run2 = evaluate_evidence_sufficiency([ev1, ev2])

    assert run1 == run2
    assert run1.classification == run2.classification
    assert run1.observed_facts == run2.observed_facts
    assert run1.inferred_hypotheses == run2.inferred_hypotheses
    assert run1.uncertainty_statement == run2.uncertainty_statement


# ============================================================================
# 8. Evidence Source IDs and Provenance Fields Are Preserved
# ============================================================================

def test_source_provenance_fields_preserved():
    """Verify that exact source document ID, content_hash, and trace_id are retained in EvidenceReference."""
    t0, t1, _ = make_timestamps()
    test_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="os-exact-doc-12345",
        timestamp=t0,
        service_name="order-service",
        log_level="CRITICAL",
        snippet="Out of memory in worker 3",
        correlation_rule="FIRST_FAILURE",
        rationale="Root trigger",
        content_hash=test_hash,
        trace_id="trace-exact-9999",
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="os-exact-doc-67890",
        timestamp=t1,
        service_name="payment-service",
        log_level="ERROR",
        snippet="Payment rejected",
        correlation_rule="TRACE_LINKED",
        rationale="Cascade trigger",
        content_hash="second_hash",
        trace_id="trace-exact-9999",
    )

    assessment = evaluate_evidence_sufficiency([ev1, ev2])

    ref_map = {r.log_id: r for r in assessment.evidence_references}
    assert "os-exact-doc-12345" in ref_map
    ref1 = ref_map["os-exact-doc-12345"]
    assert ref1.content_hash == test_hash
    assert ref1.trace_id == "trace-exact-9999"
    assert ref1.service_name == "order-service"
    assert ref1.evidence_type == "CULPRIT_LOG"
    assert ref1.correlation_rule == "FIRST_FAILURE"


# ============================================================================
# 9. No Assessment Claims a Confirmed Root Cause
# ============================================================================

def test_invariant_is_root_cause_confirmed_always_false():
    """Verify strict invariant: is_root_cause_confirmed is False across all classifications."""
    t0, t1, _ = make_timestamps()

    # 1. TRACE_LINKED input
    ev_trace1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="d1",
        timestamp=t0,
        service_name="svc1",
        log_level="ERROR",
        snippet="s1",
        correlation_rule="FIRST_FAILURE",
        rationale="r1",
        trace_id="t1",
    )
    ev_trace2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="d2",
        timestamp=t1,
        service_name="svc2",
        log_level="ERROR",
        snippet="s2",
        correlation_rule="TRACE_LINKED",
        rationale="r2",
        trace_id="t1",
    )
    res_trace = evaluate_evidence_sufficiency([ev_trace1, ev_trace2])
    assert res_trace.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert res_trace.is_root_cause_confirmed is False

    # 2. TEMPORAL_ONLY input
    ev_temp1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="d1",
        timestamp=t0,
        service_name="svc1",
        log_level="ERROR",
        snippet="s1",
        correlation_rule="FIRST_FAILURE",
        rationale="r1",
    )
    ev_temp2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="d2",
        timestamp=t1,
        service_name="svc2",
        log_level="ERROR",
        snippet="s2",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="r2",
    )
    res_temp = evaluate_evidence_sufficiency([ev_temp1, ev_temp2])
    assert res_temp.classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY
    assert res_temp.is_root_cause_confirmed is False

    # 3. INSUFFICIENT input
    res_insuf = evaluate_evidence_sufficiency([ev_temp1])
    assert res_insuf.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert res_insuf.is_root_cause_confirmed is False


# ============================================================================
# 10. Existing Evidence and Timeline Inputs Are Not Mutated
# ============================================================================

def test_input_immutability():
    """Verify that calling evaluate_evidence_sufficiency does not modify input lists or objects."""
    t0, t1, _ = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-mut-1",
        timestamp=t0,
        service_name="svc-a",
        log_level="ERROR",
        snippet="Error A",
        correlation_rule="FIRST_FAILURE",
        rationale="Rat A",
        trace_id="tr-mut",
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-mut-2",
        timestamp=t1,
        service_name="svc-b",
        log_level="ERROR",
        snippet="Error B",
        correlation_rule="TRACE_LINKED",
        rationale="Rat B",
        trace_id="tr-mut",
    )

    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="svc-a",
        event_type="FIRST_ERROR",
        severity="ERROR",
        summary="Summary A",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-a",
        trace_id="tr-mut",
    )

    ev_list = [ev2, ev1]  # Out of chronological order
    tl_list = [tl1]

    # Deep copies before execution
    ev_list_copy = deepcopy(ev_list)
    tl_list_copy = deepcopy(tl_list)

    assessment = evaluate_evidence_sufficiency(
        evidence_items=ev_list,
        timeline_events=tl_list,
    )

    # Input lists must maintain identical order and contents
    assert ev_list == ev_list_copy
    assert tl_list == tl_list_copy
    assert ev_list[0].log_id == "doc-mut-2"
    assert ev_list[1].log_id == "doc-mut-1"


# ============================================================================
# 11. SQLAlchemy ORM Model Support
# ============================================================================

def test_sqlalchemy_orm_evidence_and_event_support():
    """Verify that evaluate_evidence_sufficiency handles SQLAlchemy Evidence & IncidentEvent instances."""
    t0, t1, _ = make_timestamps()
    inc_id = uuid.uuid4()
    trace_id = "trace-orm-cross-service"

    orm_ev1 = Evidence(
        id=uuid.uuid4(),
        incident_id=inc_id,
        evidence_type="CULPRIT_LOG",
        opensearch_doc_id="os-doc-orm-1",
        log_timestamp=t0,
        service_name="database-service",
        log_level="CRITICAL",
        log_snippet="Deadlock detected",
        explanation="Initial DB deadlock",
        metadata_={"correlation_rule": "FIRST_FAILURE", "trace_id": trace_id},
    )
    orm_ev2 = Evidence(
        id=uuid.uuid4(),
        incident_id=inc_id,
        evidence_type="CORRELATION_SPIKE",
        opensearch_doc_id="os-doc-orm-2",
        log_timestamp=t1,
        service_name="app-service",
        log_level="ERROR",
        log_snippet="SQL transaction failed",
        explanation="App cascade",
        metadata_={"correlation_rule": "TRACE_LINKED", "trace_id": trace_id},
    )

    orm_event1 = IncidentEvent(
        id=uuid.uuid4(),
        incident_id=inc_id,
        event_timestamp=t0,
        service_name="database-service",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        sequence_order=0,
        summary="Deadlock in PostgreSQL",
        attributes={
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "trace_id": trace_id,
            "sample_log_id": "os-doc-orm-1",
        },
    )
    orm_event2 = IncidentEvent(
        id=uuid.uuid4(),
        incident_id=inc_id,
        event_timestamp=t1,
        service_name="app-service",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        sequence_order=1,
        summary="App cascade failure",
        attributes={
            "is_earliest_observed_failure": False,
            "propagation_lag_seconds": 15.0,
            "trace_id": trace_id,
            "sample_log_id": "os-doc-orm-2",
        },
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=[orm_ev1, orm_ev2],
        timeline_events=[orm_event1, orm_event2],
    )

    assert assessment.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert assessment.has_shared_trace is True
    assert assessment.shared_trace_ids == [trace_id]
    assert assessment.distinct_services == ["app-service", "database-service"]
    assert assessment.earliest_failure_service == "database-service"
    assert assessment.earliest_failure_log_id == "os-doc-orm-1"


# ============================================================================
# 12. Single-Service Incidents (Intra-Service Trace Linked vs Temporal)
# ============================================================================

def test_single_service_shared_trace_produces_trace_linked():
    """Verify that a single-service incident where earliest failure connects to another log via trace_id is TRACE_LINKED."""
    t0, t1, _ = make_timestamps()
    trace_intra = "trace-single-svc-transaction-1"

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-auth-1",
        timestamp=t0,
        service_name="auth-service",
        log_level="CRITICAL",
        snippet="JWT validation panic",
        correlation_rule="FIRST_FAILURE",
        rationale="Initial failure",
        trace_id=trace_intra,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-auth-2",
        timestamp=t1,
        service_name="auth-service",
        log_level="ERROR",
        snippet="Session store unreachable",
        correlation_rule="TRACE_LINKED",
        rationale="Same transaction",
        trace_id=trace_intra,
    )

    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="auth-service",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="JWT panic",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-auth-1",
        trace_id=trace_intra,
        sample_log_id="doc-auth-1",
    )
    tl2 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="auth-service",
        event_type="ANOMALY_SPIKE",
        severity="ERROR",
        summary="Session store error",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=15.0,
        cluster_id="cl-auth-2",
        trace_id=trace_intra,
        sample_log_id="doc-auth-2",
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=[ev1, ev2],
        timeline_events=[tl1, tl2],
    )

    assert assessment.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert assessment.has_shared_trace is True
    assert assessment.shared_trace_ids == [trace_intra]
    assert assessment.distinct_service_count == 1
    assert assessment.distinct_services == ["auth-service"]
    assert assessment.is_root_cause_confirmed is False
    assert "Intra-service failure cascade" in assessment.inferred_hypotheses[0]


def test_single_service_without_shared_trace_produces_temporal_only():
    """Verify that a single-service incident with multiple events but no shared trace is TEMPORAL_ONLY."""
    t0, t1, _ = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-auth-1",
        timestamp=t0,
        service_name="auth-service",
        log_level="CRITICAL",
        snippet="JWT validation panic",
        correlation_rule="FIRST_FAILURE",
        rationale="Initial failure",
        trace_id=None,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-auth-2",
        timestamp=t1,
        service_name="auth-service",
        log_level="ERROR",
        snippet="Session store unreachable",
        correlation_rule="LOCAL_SERVICE",
        rationale="Second error in service",
        trace_id=None,
    )

    assessment = evaluate_evidence_sufficiency(evidence_items=[ev1, ev2])

    assert assessment.classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY
    assert assessment.has_shared_trace is False
    assert assessment.distinct_service_count == 1
    assert assessment.distinct_services == ["auth-service"]
    assert assessment.is_root_cause_confirmed is False


# ============================================================================
# 13. Phase 17 Step 4 Boundary & Regression Audits
# ============================================================================

def test_duplicate_evidence_items_collapse_and_yield_insufficient():
    """Verify that multiple duplicate evidence items with identical log_id collapse to count 1 and yield INSUFFICIENT."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)

    # 3 copies of the exact same log_id
    ev_dup = [
        CorrelatedEvidenceItemResponse(
            evidence_type="CULPRIT_LOG",
            log_id="doc-dup-01",
            timestamp=t0,
            service_name="auth-service",
            log_level="ERROR",
            snippet="Identical error snippet",
            correlation_rule="FIRST_FAILURE",
            rationale="Initial failure",
            content_hash="hash-dup",
            trace_id="tr-dup",
        )
        for _ in range(3)
    ]

    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="auth-service",
        event_type="FIRST_ERROR",
        severity="ERROR",
        summary="Initial failure",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-auth",
        sample_log_id="doc-dup-01",
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=ev_dup,
        timeline_events=[tl1],
    )

    # Must collapse distinct_evidence_count to 1 and recognize sparsity
    assert assessment.distinct_evidence_count == 1
    assert assessment.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert assessment.is_root_cause_confirmed is False
    assert "Diagnostic uncertainty: High" in assessment.uncertainty_statement
    assert "fewer than 2 corroborating evidence records or timeline milestones available" in assessment.uncertainty_statement


def test_contradictory_telemetry_with_shared_trace_overrides_to_insufficient():
    """Verify that contradictory telemetry (non-finite propagation lag or backward timestamps) overrides shared trace to INSUFFICIENT."""
    t0, t1, _ = make_timestamps()
    shared_trace = "trace-contradictory-100"

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-c1",
        timestamp=t0,
        service_name="svc-a",
        log_level="ERROR",
        snippet="Error A",
        correlation_rule="FIRST_FAILURE",
        rationale="Rat A",
        trace_id=shared_trace,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-c2",
        timestamp=t1,
        service_name="svc-b",
        log_level="ERROR",
        snippet="Error B",
        correlation_rule="TRACE_LINKED",
        rationale="Rat B",
        trace_id=shared_trace,
    )

    # Corrupted / contradictory timeline: non-finite NaN propagation lag on sequence 1
    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="svc-a",
        event_type="FIRST_ERROR",
        severity="ERROR",
        summary="Event A",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-a",
        trace_id=shared_trace,
        sample_log_id="doc-c1",
    )
    tl2 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="svc-b",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Event B",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=float("nan"),  # Non-finite contradictory lag!
        cluster_id="cl-b",
        trace_id=shared_trace,
        sample_log_id="doc-c2",
    )

    assessment = evaluate_evidence_sufficiency(
        evidence_items=[ev1, ev2],
        timeline_events=[tl1, tl2],
    )

    assert assessment.is_timeline_temporally_consistent is False
    assert assessment.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert assessment.is_root_cause_confirmed is False
    assert "Diagnostic uncertainty: High" in assessment.uncertainty_statement
    assert "timeline consistency validation failed" in assessment.uncertainty_statement


def test_single_service_trace_absent_from_earliest_failure_yields_temporal_only():
    """Verify that in single-service incidents, trace ID connecting non-earliest logs without earliest failure yields TEMPORAL_ONLY."""
    t0, t1, t2 = make_timestamps()

    # Earliest failure has NO trace ID
    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-s-1",
        timestamp=t0,
        service_name="payment-service",
        log_level="CRITICAL",
        snippet="Connection reset by peer",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest failure",
        trace_id=None,
    )
    # Subsequent error logs share a trace, but NOT with the earliest failure
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-s-2",
        timestamp=t1,
        service_name="payment-service",
        log_level="ERROR",
        snippet="Subsequent retry failed",
        correlation_rule="LOCAL_SERVICE",
        rationale="Retry error",
        trace_id="tr-late-retry",
    )
    ev3 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-s-3",
        timestamp=t2,
        service_name="payment-service",
        log_level="ERROR",
        snippet="Circuit breaker opened",
        correlation_rule="LOCAL_SERVICE",
        rationale="Breaker open",
        trace_id="tr-late-retry",
    )

    assessment = evaluate_evidence_sufficiency(evidence_items=[ev1, ev2, ev3])

    # In single-service incidents, shared trace must connect to earliest failure
    assert assessment.has_shared_trace is False
    assert assessment.classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY
    assert assessment.is_root_cause_confirmed is False
    assert "Diagnostic uncertainty: Moderate" in assessment.uncertainty_statement


def test_nullable_timestamps_preserve_deterministic_reference_ordering():
    """Verify that evidence references sort deterministically even with mixed None and timezone-aware timestamps."""
    t0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 10, 14, 0, 30, tzinfo=timezone.utc)
    inc_id = uuid.uuid4()

    ev_with_ts1 = Evidence(
        id=uuid.uuid4(),
        incident_id=inc_id,
        evidence_type="CULPRIT_LOG",
        opensearch_doc_id="doc-a",
        log_timestamp=t0,
        service_name="service-a",
        log_level="ERROR",
        log_snippet="Msg A",
        explanation="Rat A",
        metadata_={"correlation_rule": "FIRST_FAILURE"},
    )
    ev_none_ts = Evidence(
        id=uuid.uuid4(),
        incident_id=inc_id,
        evidence_type="CORRELATION_SPIKE",
        opensearch_doc_id="doc-b",
        log_timestamp=None,  # Nullable timestamp on ORM model
        service_name="service-b",
        log_level="ERROR",
        log_snippet="Msg B",
        explanation="Rat B",
        metadata_={"correlation_rule": "TEMPORAL_CASCADE"},
    )
    ev_with_ts2 = Evidence(
        id=uuid.uuid4(),
        incident_id=inc_id,
        evidence_type="CORRELATION_SPIKE",
        opensearch_doc_id="doc-c",
        log_timestamp=t1,
        service_name="service-c",
        log_level="ERROR",
        log_snippet="Msg C",
        explanation="Rat C",
        metadata_={"correlation_rule": "TEMPORAL_CASCADE"},
    )

    assessment1 = evaluate_evidence_sufficiency([ev_none_ts, ev_with_ts2, ev_with_ts1])
    assessment2 = evaluate_evidence_sufficiency([ev_with_ts1, ev_none_ts, ev_with_ts2])

    refs1 = [r.log_id for r in assessment1.evidence_references]
    refs2 = [r.log_id for r in assessment2.evidence_references]

    # Order must be strictly identical regardless of input ordering
    assert refs1 == refs2
    # None timestamp sorts first (mapped to datetime.min in sort key)
    assert refs1 == ["doc-b", "doc-a", "doc-c"]

