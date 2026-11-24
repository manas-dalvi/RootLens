from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from typing import List, Optional, Tuple
import pytest

from app.schemas.incident import (
    CorrelatedEvidenceItemResponse,
    CorrelatedTimelineEventResponse,
)
from app.services.evidence_analysis_service import (
    EvidenceReference,
    EvidenceSufficiencyAssessment,
    EvidenceSufficiencyClassification,
    evaluate_evidence_sufficiency,
)
from app.services.rca_synthesis_service import (
    RCACandidateFinding,
    RCASynthesisResult,
    synthesize_rca_findings,
)


# ============================================================================
# Helpers
# ============================================================================

def make_timestamps(base: Optional[datetime] = None) -> Tuple[datetime, datetime, datetime]:
    t0 = base or datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=20)
    t2 = t0 + timedelta(seconds=50)
    return t0, t1, t2


# ============================================================================
# 1. Trace-Linked Evidence Produces Evidence-Grounded Hypotheses
# ============================================================================

def test_trace_linked_evidence_produces_evidence_grounded_candidate_hypothesis():
    """Verify trace-linked telemetry produces an evidence-grounded hypothesis without confirming root cause."""
    t0, t1, t2 = make_timestamps()
    shared_trace = "trace-checkout-payment-500"

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="log-pay-01",
        timestamp=t0,
        service_name="payment-service",
        log_level="CRITICAL",
        snippet="Connection pool exhausted: DB timeout",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest failure",
        content_hash="hash_pay_01",
        trace_id=shared_trace,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-gw-01",
        timestamp=t1,
        service_name="api-gateway",
        log_level="ERROR",
        snippet="HTTP 504 Gateway Timeout forwarding /checkout",
        correlation_rule="TRACE_LINKED",
        rationale="Trace cascade",
        content_hash="hash_gw_01",
        trace_id=shared_trace,
    )

    tl0 = CorrelatedTimelineEventResponse(
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
        sample_log_id="log-pay-01",
    )
    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="api-gateway",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Gateway timeout",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=20.0,
        cluster_id="cl-gw",
        trace_id=shared_trace,
        sample_log_id="log-gw-01",
    )

    result = synthesize_rca_findings(
        evidence_items=[ev1, ev2],
        timeline_events=[tl0, tl1],
    )

    # Classification and safety invariants
    assert result.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert result.is_root_cause_confirmed is False
    assert result.primary_hypothesis is not None
    assert result.primary_hypothesis.startswith("[Hypothesis]")
    assert "payment-service" in result.primary_hypothesis
    assert "api-gateway" in result.primary_hypothesis
    assert shared_trace in result.primary_hypothesis

    # Findings structure
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.is_root_cause_confirmed is False
    assert finding.explanation == result.primary_hypothesis
    assert "payment-service" in finding.plausibility_rationale
    assert shared_trace in finding.plausibility_rationale

    # Evidence references
    assert len(finding.supporting_evidence_references) == 2
    assert finding.contradictory_evidence_references == []
    assert finding.contradictory_signals == []

    # Uncertainty disclosure
    assert "Low-to-Moderate" in finding.limitations_and_uncertainty
    assert "not confirmed root cause" in finding.limitations_and_uncertainty


# ============================================================================
# 2. Temporal-Only Evidence Does Not Claim Causation
# ============================================================================

def test_temporal_only_evidence_does_not_claim_causation():
    """Verify temporal cascade without trace linkage formulates a hypothesis but explicitly denies confirmed causality."""
    t0, t1, t2 = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="log-auth-01",
        timestamp=t0,
        service_name="auth-service",
        log_level="CRITICAL",
        snippet="Token signing key rotation failure",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest failure",
        content_hash="hash_auth_01",
        trace_id=None,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-user-01",
        timestamp=t1,
        service_name="user-service",
        log_level="ERROR",
        snippet="User profile fetch unauthorized",
        correlation_rule="TEMPORAL_CASCADE",
        rationale="Temporal cascade",
        content_hash="hash_user_01",
        trace_id=None,
    )

    tl0 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="auth-service",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="Key rotation failed",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-auth",
        sample_log_id="log-auth-01",
    )
    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="user-service",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Unauthorized spike",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=20.0,
        cluster_id="cl-user",
        sample_log_id="log-user-01",
    )

    result = synthesize_rca_findings(
        evidence_items=[ev1, ev2],
        timeline_events=[tl0, tl1],
    )

    assert result.classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY
    assert result.is_root_cause_confirmed is False
    assert result.primary_hypothesis is not None
    assert result.primary_hypothesis.startswith("[Hypothesis]")
    assert "causal propagation cannot be confirmed without distributed trace linkage" in result.primary_hypothesis

    finding = result.findings[0]
    assert finding.is_root_cause_confirmed is False
    assert "cannot be confirmed" in finding.plausibility_rationale
    assert "Moderate" in finding.limitations_and_uncertainty
    assert any("trace_id" in sig.lower() for sig in finding.missing_telemetry)


# ============================================================================
# 3. Insufficient Evidence Produces No Fabricated Probable Cause
# ============================================================================

def test_insufficient_evidence_produces_no_fabricated_probable_cause():
    """Verify sparse or isolated telemetry produces primary_hypothesis=None and no fabricated cause."""
    t0, _, _ = make_timestamps()

    ev_sparse = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="log-isolated-01",
        timestamp=t0,
        service_name="notification-service",
        log_level="ERROR",
        snippet="SMTP connection timeout",
        correlation_rule="FIRST_FAILURE",
        rationale="Isolated failure",
        content_hash="hash_notif_01",
        trace_id=None,
    )

    result = synthesize_rca_findings(
        evidence_items=[ev_sparse],
        timeline_events=[],
    )

    assert result.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert result.is_root_cause_confirmed is False
    # Strictly None: no fabricated probable cause
    assert result.primary_hypothesis is None

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.is_root_cause_confirmed is False
    assert "[Insufficient Telemetry]" in finding.explanation
    assert "No plausible causal candidate can be established" in finding.plausibility_rationale
    assert "High" in finding.limitations_and_uncertainty
    assert "No root cause candidate synthesized" in result.summary


# ============================================================================
# 4. Supporting References Preserve Exact Source IDs and Provenance
# ============================================================================

def test_supporting_references_preserve_exact_source_ids_and_provenance():
    """Verify supporting evidence references retain exact log IDs, service names, and provenance hashes."""
    t0, t1, _ = make_timestamps()
    trace_id = "trace-billing-exact-44"

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="opensearch-doc-exact-9988",
        timestamp=t0,
        service_name="billing-service",
        log_level="CRITICAL",
        snippet="Invoice settlement deadlock",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest failure",
        content_hash="sha256_exact_hash_9988",
        trace_id=trace_id,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="opensearch-doc-exact-9989",
        timestamp=t1,
        service_name="ledger-service",
        log_level="ERROR",
        snippet="Ledger balance lock timeout",
        correlation_rule="TRACE_LINKED",
        rationale="Cascade failure",
        content_hash="sha256_exact_hash_9989",
        trace_id=trace_id,
    )

    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="billing-service",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="Deadlock",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-bill",
        trace_id=trace_id,
        sample_log_id="opensearch-doc-exact-9988",
    )
    tl2 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="ledger-service",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Lock timeout",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=20.0,
        cluster_id="cl-ledg",
        trace_id=trace_id,
        sample_log_id="opensearch-doc-exact-9989",
    )

    result = synthesize_rca_findings(
        evidence_items=[ev1, ev2],
        timeline_events=[tl1, tl2],
    )

    finding = result.findings[0]
    refs = finding.supporting_evidence_references
    assert len(refs) == 2

    assert refs[0].log_id == "opensearch-doc-exact-9988"
    assert refs[0].service_name == "billing-service"
    assert refs[0].content_hash == "sha256_exact_hash_9988"
    assert refs[0].trace_id == trace_id
    assert refs[0].timestamp == t0

    assert refs[1].log_id == "opensearch-doc-exact-9989"
    assert refs[1].service_name == "ledger-service"
    assert refs[1].content_hash == "sha256_exact_hash_9989"
    assert refs[1].trace_id == trace_id
    assert refs[1].timestamp == t1


# ============================================================================
# 5. Contradictory Signals are Represented Honestly
# ============================================================================

def test_contradictory_signals_represented_honestly():
    """Verify contradictory telemetry (non-monotonic timestamps and invalid lag) is honestly captured."""
    t0, t1, _ = make_timestamps()
    # Inverted timestamp: sequence 1 occurred before sequence 0
    t_inverted = t0 - timedelta(seconds=10)

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="log-norm-01",
        timestamp=t0,
        service_name="service-a",
        log_level="CRITICAL",
        snippet="Normal error",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest failure",
        content_hash="hash_a",
        trace_id="tr-1",
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="log-contra-02",
        timestamp=t_inverted,
        service_name="service-b",
        log_level="ERROR",
        snippet="Inverted timestamp error",
        correlation_rule="TRACE_LINKED",
        rationale="Contradiction",
        content_hash="hash_b",
        trace_id="tr-1",
    )

    tl0 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="service-a",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="Event 0",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-a",
        sample_log_id="log-norm-01",
    )
    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t_inverted,  # Inverted!
        service_name="service-b",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Event 1 inverted",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=-10.0,  # Negative lag!
        cluster_id="cl-b",
        sample_log_id="log-contra-02",
    )

    result = synthesize_rca_findings(
        evidence_items=[ev1, ev2],
        timeline_events=[tl0, tl1],
    )

    # Inconsistent timeline forces INSUFFICIENT classification
    assert result.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert result.primary_hypothesis is None

    finding = result.findings[0]
    assert len(finding.contradictory_signals) >= 1
    # Check that contradictory signals explain the timeline issue
    assert any(
        "negative propagation lag" in sig or "occurred before preceding" in sig
        for sig in finding.contradictory_signals
    )
    assert any(
        ref.log_id == "log-contra-02"
        for ref in finding.contradictory_evidence_references
    )


# ============================================================================
# 6. Missing or Nullable Fields Handled Safely
# ============================================================================

def test_missing_or_nullable_fields_handled_safely():
    """Verify that null timestamps, missing trace/span IDs, and empty inputs execute safely without crashes."""
    # 1. Nullable fields using dictionary / arbitrary objects with None values
    raw_null_item = {
        "log_id": "doc-nulls",
        "timestamp": None,
        "service_name": None,
        "log_level": None,
        "snippet": None,
        "correlation_rule": None,
        "rationale": None,
        "content_hash": None,
        "trace_id": None,
        "span_id": None,
    }
    result = synthesize_rca_findings(evidence_items=[raw_null_item], timeline_events=[])
    assert result.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert result.primary_hypothesis is None
    assert result.is_root_cause_confirmed is False

    # 2. None passed for all arguments
    empty_result = synthesize_rca_findings(assessment=None, evidence_items=None, timeline_events=None)
    assert empty_result.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert empty_result.primary_hypothesis is None
    assert empty_result.is_root_cause_confirmed is False
    assert len(empty_result.findings) == 1


# ============================================================================
# 7. Output Ordering and Repeated Evaluations are Deterministic
# ============================================================================

def test_output_ordering_and_repeated_evaluations_deterministic():
    """Verify repeated evaluations produce strictly identical, deterministically ordered results."""
    t0, t1, t2 = make_timestamps()
    trace_id = "trace-deter-123"

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-a",
        timestamp=t0,
        service_name="service-alpha",
        log_level="CRITICAL",
        snippet="Alpha error",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest",
        content_hash="hash_a",
        trace_id=trace_id,
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-b",
        timestamp=t1,
        service_name="service-beta",
        log_level="ERROR",
        snippet="Beta error",
        correlation_rule="TRACE_LINKED",
        rationale="Cascade",
        content_hash="hash_b",
        trace_id=trace_id,
    )

    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="service-alpha",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="Alpha event",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-a",
        trace_id=trace_id,
        sample_log_id="doc-a",
    )
    tl2 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="service-beta",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Beta event",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=20.0,
        cluster_id="cl-b",
        trace_id=trace_id,
        sample_log_id="doc-b",
    )

    # Run 5 times in natural order
    res1 = synthesize_rca_findings(evidence_items=[ev1, ev2], timeline_events=[tl1, tl2])
    res2 = synthesize_rca_findings(evidence_items=[ev1, ev2], timeline_events=[tl1, tl2])

    assert res1.classification == res2.classification
    assert res1.primary_hypothesis == res2.primary_hypothesis
    assert res1.findings[0].supporting_evidence_references == res2.findings[0].supporting_evidence_references

    # Run with reversed evidence input order: deterministic tie-breakers must yield identical order
    res_reversed = synthesize_rca_findings(evidence_items=[ev2, ev1], timeline_events=[tl2, tl1])
    assert res_reversed.primary_hypothesis == res1.primary_hypothesis
    assert (
        res_reversed.findings[0].supporting_evidence_references
        == res1.findings[0].supporting_evidence_references
    )


# ============================================================================
# 8. Inputs are Not Mutated
# ============================================================================

def test_inputs_are_not_mutated():
    """Verify that synthesizing RCA findings does not mutate passed evidence or timeline objects."""
    t0, t1, _ = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-m1",
        timestamp=t0,
        service_name="svc-m1",
        log_level="CRITICAL",
        snippet="Error 1",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest",
        content_hash="h1",
        trace_id="tr-m",
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-m2",
        timestamp=t1,
        service_name="svc-m2",
        log_level="ERROR",
        snippet="Error 2",
        correlation_rule="TRACE_LINKED",
        rationale="Cascade",
        content_hash="h2",
        trace_id="tr-m",
    )

    ev_list = [ev1, ev2]
    ev_copy = deepcopy(ev_list)

    tl1 = CorrelatedTimelineEventResponse(
        sequence_order=0,
        event_timestamp=t0,
        service_name="svc-m1",
        event_type="FIRST_ERROR",
        severity="CRITICAL",
        summary="Event 1",
        is_earliest_observed_failure=True,
        propagation_lag_seconds=0.0,
        cluster_id="cl-1",
        trace_id="tr-m",
        sample_log_id="doc-m1",
    )
    tl2 = CorrelatedTimelineEventResponse(
        sequence_order=1,
        event_timestamp=t1,
        service_name="svc-m2",
        event_type="CASCADING_FAILURE",
        severity="ERROR",
        summary="Event 2",
        is_earliest_observed_failure=False,
        propagation_lag_seconds=20.0,
        cluster_id="cl-2",
        trace_id="tr-m",
        sample_log_id="doc-m2",
    )

    tl_list = [tl1, tl2]
    tl_copy = deepcopy(tl_list)

    _ = synthesize_rca_findings(evidence_items=ev_list, timeline_events=tl_list)

    assert ev_list == ev_copy
    assert tl_list == tl_copy


# ============================================================================
# 9. No Result Confirms a Root Cause or Invents a Confidence Percentage
# ============================================================================

def test_no_result_confirms_root_cause_or_invents_confidence_percentage():
    """Verify that is_root_cause_confirmed is universally False and no arbitrary confidence scores exist."""
    t0, t1, _ = make_timestamps()

    ev1 = CorrelatedEvidenceItemResponse(
        evidence_type="CULPRIT_LOG",
        log_id="doc-c1",
        timestamp=t0,
        service_name="auth-svc",
        log_level="CRITICAL",
        snippet="Crash",
        correlation_rule="FIRST_FAILURE",
        rationale="Earliest",
        content_hash="hc1",
        trace_id="tr-c",
    )
    ev2 = CorrelatedEvidenceItemResponse(
        evidence_type="CORRELATION_SPIKE",
        log_id="doc-c2",
        timestamp=t1,
        service_name="web-svc",
        log_level="ERROR",
        snippet="Crash 2",
        correlation_rule="TRACE_LINKED",
        rationale="Cascade",
        content_hash="hc2",
        trace_id="tr-c",
    )

    result = synthesize_rca_findings(evidence_items=[ev1, ev2])

    # 1. is_root_cause_confirmed is strictly False
    assert result.is_root_cause_confirmed is False
    for f in result.findings:
        assert f.is_root_cause_confirmed is False

    # 2. No arbitrary confidence score or percentage attributes
    assert not hasattr(result, "confidence_score")
    assert not hasattr(result, "confidence_percentage")
    assert not hasattr(result, "probability")

    for f in result.findings:
        assert not hasattr(f, "confidence_score")
        assert not hasattr(f, "confidence_percentage")
        assert not hasattr(f, "probability")


# ============================================================================
# 10. Existing Evidence Assessment Classifications are Respected
# ============================================================================

def test_existing_evidence_assessment_classifications_respected():
    """Verify that passing an existing EvidenceSufficiencyAssessment directly is respected."""
    # Create artificial assessment directly
    ass = EvidenceSufficiencyAssessment(
        classification=EvidenceSufficiencyClassification.TEMPORAL_ONLY,
        is_root_cause_confirmed=False,
        distinct_evidence_count=2,
        distinct_service_count=2,
        distinct_services=["service-x", "service-y"],
        is_timeline_temporally_consistent=True,
        earliest_failure_service="service-x",
        earliest_failure_timestamp=datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc),
        inferred_hypotheses=[
            "[Hypothesis] Failure in service 'service-x' is temporally associated with 'service-y'."
        ],
        uncertainty_statement="Diagnostic uncertainty: Moderate.",
        supporting_signals=["Temporal proximity observed"],
        missing_signals=["Distributed trace context absent"],
    )

    result = synthesize_rca_findings(assessment=ass)

    assert result.classification == EvidenceSufficiencyClassification.TEMPORAL_ONLY
    assert result.is_root_cause_confirmed is False
    assert result.primary_hypothesis == ass.inferred_hypotheses[0]
    assert len(result.findings) == 1
    assert result.findings[0].explanation == ass.inferred_hypotheses[0]
    assert "service-x" in result.findings[0].plausibility_rationale
    assert result.uncertainty_statement == "Diagnostic uncertainty: Moderate."
