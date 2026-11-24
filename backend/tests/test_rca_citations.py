"""Focused tests for Phase 8 Stage 3: Fine-Grained RCA Citation Mapping.

Requirements:
- Valid claim-to-evidence mapping.
- Multiple citations supporting one claim.
- Invalid or fabricated evidence references rejected.
- Evidence belonging to another incident rejected.
- Service mismatch rejected.
- Unsupported claims handled cleanly (is_supported=False with reason).
- RCA serialization and persistence round-trip.
- Regression of existing recommendation citations.
- Invariants: is_root_cause_confirmed is strictly False; zero fabrication.
"""

from datetime import datetime, timezone
import uuid
import pytest

from app.services.evidence_analysis_service import (
    EvidenceReference,
    EvidenceSufficiencyAssessment,
    EvidenceSufficiencyClassification,
    evaluate_evidence_sufficiency,
)
from app.services.rca_synthesis_service import (
    RCACitationItem,
    RCAClaimMapping,
    RCACandidateFinding,
    RCASynthesisResult,
    synthesize_rca_findings,
    validate_and_resolve_rca_citations,
)
from app.schemas.incident import (
    RCACitationItemResponse,
    RCAClaimMappingResponse,
    RCACandidateFindingResponse,
    RCASynthesisResponse,
)


def _make_sample_evidence(
    log_id: str,
    service_name: str,
    timestamp: datetime,
    evidence_type: str = "CULPRIT_LOG",
    correlation_rule: str = "FIRST_FAILURE",
    trace_id: str = "trace-101",
    span_id: str = "span-202",
    content_hash: str = "abc123hash",
    log_snippet: str = "Connection refused to database pool",
    incident_id: str = "inc-999",
):
    return {
        "log_id": log_id,
        "opensearch_doc_id": log_id,
        "service_name": service_name,
        "timestamp": timestamp,
        "evidence_type": evidence_type,
        "correlation_rule": correlation_rule,
        "trace_id": trace_id,
        "span_id": span_id,
        "content_hash": content_hash,
        "log_snippet": log_snippet,
        "incident_id": incident_id,
    }


def test_valid_claim_to_evidence_mapping():
    """Verify that valid citations referencing authentic evidence are verified and resolved."""
    t0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    ev1 = _make_sample_evidence(
        log_id="log-auth-01",
        service_name="auth-service",
        timestamp=t0,
        log_snippet="FATAL: Authentication token signing key corrupted",
    )
    raw_evidence = [ev1]

    claim = RCAClaimMapping(
        claim_id="claim-1",
        claim_text="Authentication signing key was corrupted at onset.",
        claim_type="OBSERVED_FACT",
        is_supported=True,
        citations=[
            RCACitationItem(
                log_id="log-auth-01",
                service_name="auth-service",
                evidence_type="CULPRIT_LOG",
                support_explanation="Primary crash log showing key corruption.",
            )
        ],
    )

    resolved_claims = validate_and_resolve_rca_citations(
        claims=[claim],
        evidence_items=raw_evidence,
        expected_incident_id="inc-999",
    )

    assert len(resolved_claims) == 1
    res = resolved_claims[0]
    assert res.claim_id == "claim-1"
    assert res.is_supported is True
    assert res.unsupported_reason is None
    assert len(res.citations) == 1
    cit = res.citations[0]
    assert cit.log_id == "log-auth-01"
    assert cit.service_name == "auth-service"
    assert cit.timestamp == t0
    assert cit.log_snippet == "FATAL: Authentication token signing key corrupted"
    assert cit.trace_id == "trace-101"
    assert cit.content_hash == "abc123hash"
    assert "Primary crash log" in cit.support_explanation


def test_multiple_citations_supporting_single_claim():
    """Verify that multiple corroborating evidence records can be cited for one hypothesis claim."""
    t0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 10, 0, 5, tzinfo=timezone.utc)

    ev_upstream = _make_sample_evidence(
        log_id="log-up-01",
        service_name="auth-service",
        timestamp=t0,
        log_snippet="FATAL: Auth service crashed",
        trace_id="shared-trace-77",
    )
    ev_downstream = _make_sample_evidence(
        log_id="log-down-02",
        service_name="payment-service",
        timestamp=t1,
        log_snippet="ERROR: Token validation timeout connecting to auth-service",
        trace_id="shared-trace-77",
    )
    raw_evidence = [ev_upstream, ev_downstream]

    claim = RCAClaimMapping(
        claim_id="claim-hyp-cascade",
        claim_text="Auth service failure propagated along shared trace to payment service.",
        claim_type="INFERRED_HYPOTHESIS",
        is_supported=True,
        citations=[
            RCACitationItem(
                log_id="log-up-01",
                service_name="auth-service",
                evidence_type="CULPRIT_LOG",
                support_explanation="Initiating upstream failure.",
            ),
            RCACitationItem(
                log_id="log-down-02",
                service_name="payment-service",
                evidence_type="CORRELATION_SPIKE",
                support_explanation="Downstream failure on same trace.",
            ),
        ],
    )

    resolved_claims = validate_and_resolve_rca_citations(
        claims=[claim],
        evidence_items=raw_evidence,
    )

    assert len(resolved_claims) == 1
    res = resolved_claims[0]
    assert res.is_supported is True
    assert len(res.citations) == 2
    assert res.citations[0].log_id == "log-up-01"
    assert res.citations[0].service_name == "auth-service"
    assert res.citations[1].log_id == "log-down-02"
    assert res.citations[1].service_name == "payment-service"


def test_invalid_or_fabricated_evidence_reference_rejected():
    """Verify that fabricated log IDs not present in incident evidence are strictly rejected."""
    t0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    ev1 = _make_sample_evidence(log_id="log-real-01", service_name="auth-service", timestamp=t0)
    raw_evidence = [ev1]

    claim = RCAClaimMapping(
        claim_id="claim-fake",
        claim_text="Database connection pool was exhausted.",
        claim_type="OBSERVED_FACT",
        is_supported=True,
        citations=[
            RCACitationItem(
                log_id="log-FABRICATED-9999",
                service_name="auth-service",
                evidence_type="CULPRIT_LOG",
                support_explanation="Fabricated reference.",
            )
        ],
    )

    resolved = validate_and_resolve_rca_citations(claims=[claim], evidence_items=raw_evidence)
    assert len(resolved) == 1
    res = resolved[0]
    assert res.is_supported is False
    assert len(res.citations) == 0
    assert "log-FABRICATED-9999" in (res.unsupported_reason or "")
    assert "not found" in (res.unsupported_reason or "").lower()


def test_evidence_belonging_to_another_incident_rejected():
    """Verify that evidence from a different incident is rejected when expected_incident_id is verified."""
    t0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    ev_other = _make_sample_evidence(
        log_id="log-other-01",
        service_name="auth-service",
        timestamp=t0,
        incident_id="incident-ALPHA",
    )
    raw_evidence = [ev_other]

    claim = RCAClaimMapping(
        claim_id="claim-cross",
        claim_text="Cross incident crash cited.",
        claim_type="OBSERVED_FACT",
        is_supported=True,
        citations=[
            RCACitationItem(
                log_id="log-other-01",
                service_name="auth-service",
                evidence_type="CULPRIT_LOG",
            )
        ],
    )

    # Validate expecting incident-BETA
    resolved = validate_and_resolve_rca_citations(
        claims=[claim],
        evidence_items=raw_evidence,
        expected_incident_id="incident-BETA",
    )

    assert len(resolved) == 1
    res = resolved[0]
    assert res.is_supported is False
    assert len(res.citations) == 0
    assert "another incident" in (res.unsupported_reason or "").lower()


def test_service_mismatch_rejected():
    """Verify that citations citing the wrong service for a log are rejected."""
    t0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    ev = _make_sample_evidence(
        log_id="log-auth-42",
        service_name="auth-service",
        timestamp=t0,
    )
    raw_evidence = [ev]

    claim = RCAClaimMapping(
        claim_id="claim-mismatch",
        claim_text="Billing service failed.",
        claim_type="OBSERVED_FACT",
        is_supported=True,
        citations=[
            RCACitationItem(
                log_id="log-auth-42",
                service_name="billing-service",  # Wrong service
                evidence_type="CULPRIT_LOG",
            )
        ],
    )

    resolved = validate_and_resolve_rca_citations(claims=[claim], evidence_items=raw_evidence)
    assert len(resolved) == 1
    res = resolved[0]
    assert res.is_supported is False
    assert len(res.citations) == 0
    assert "service mismatch" in (res.unsupported_reason or "").lower()


def test_unsupported_claims_handled_cleanly():
    """Verify that claims without telemetry or speculative hypotheses are flagged as unsupported."""
    claim_spec = RCAClaimMapping(
        claim_id="claim-spec-1",
        claim_text="Unmonitored third-party payment gateway experienced packet loss.",
        claim_type="UNSUPPORTED_SPECULATION",
        is_supported=False,
        citations=[],
        unsupported_reason="No gateway telemetry collected.",
    )

    resolved = validate_and_resolve_rca_citations(claims=[claim_spec], evidence_items=[])
    assert len(resolved) == 1
    res = resolved[0]
    assert res.is_supported is False
    assert len(res.citations) == 0
    assert res.unsupported_reason == "No gateway telemetry collected."


def test_synthesize_rca_findings_generates_claim_citations():
    """Verify that synthesize_rca_findings creates grounded claim citations across findings and observed facts."""
    t0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 10, 0, 2, tzinfo=timezone.utc)

    ev1 = _make_sample_evidence(
        log_id="log-init-01",
        service_name="order-service",
        timestamp=t0,
        trace_id="trace-xyz-9",
        log_snippet="PANIC: deadlock in order commit",
    )
    ev2 = _make_sample_evidence(
        log_id="log-down-02",
        service_name="inventory-service",
        timestamp=t1,
        trace_id="trace-xyz-9",
        log_snippet="ERROR: order commit failed timeout",
    )
    raw_evidence = [ev1, ev2]

    tl_event = {
        "event_timestamp": t0,
        "service_name": "order-service",
        "event_type": "FIRST_FAILURE",
        "sequence_order": 1,
        "sample_log_id": "log-init-01",
        "propagation_lag_seconds": 0.0,
        "attributes": {"trace_id": "trace-xyz-9"},
    }

    result = synthesize_rca_findings(
        evidence_items=raw_evidence,
        timeline_events=[tl_event],
    )

    # Invariants
    assert result.is_root_cause_confirmed is False
    assert result.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert result.primary_hypothesis is not None
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.is_root_cause_confirmed is False

    # Check finding claim citations
    assert len(finding.claim_citations) >= 1
    hyp_claim = next((c for c in finding.claim_citations if c.claim_type == "INFERRED_HYPOTHESIS"), None)
    assert hyp_claim is not None
    assert hyp_claim.is_supported is True
    assert len(hyp_claim.citations) >= 1
    # Check that citation includes snippet and explanation
    assert any("order-service" in c.service_name for c in hyp_claim.citations)
    assert any("deadlock" in (c.log_snippet or "") for c in hyp_claim.citations)

    # Check top-level synthesis claim citations
    assert len(result.claim_citations) >= 1
    fact_claims = [c for c in result.claim_citations if c.claim_type == "OBSERVED_FACT"]
    assert len(fact_claims) > 0


def test_rca_schema_serialization_roundtrip():
    """Verify that RCA responses serialize and deserialize claim citations with complete fidelity."""
    t0 = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
    cit = RCACitationItem(
        log_id="log-100",
        service_name="payment-service",
        evidence_type="CULPRIT_LOG",
        timestamp=t0,
        evidence_id="log-100",
        log_snippet="Connection timed out",
        support_explanation="Primary crash point",
        trace_id="trace-123",
        span_id="span-456",
        content_hash="hash789",
    )
    claim = RCAClaimMapping(
        claim_id="claim-test-1",
        claim_text="Payment service crashed due to connection timeout.",
        claim_type="INFERRED_HYPOTHESIS",
        is_supported=True,
        citations=[cit],
    )
    finding = RCACandidateFinding(
        explanation="Payment service failure",
        plausibility_rationale="Earliest failure log",
        limitations_and_uncertainty="Unverified database telemetry",
        is_root_cause_confirmed=False,
        claim_citations=[claim],
    )
    synth = RCASynthesisResult(
        classification=EvidenceSufficiencyClassification.TRACE_LINKED,
        is_root_cause_confirmed=False,
        primary_hypothesis="Payment failure",
        findings=[finding],
        observed_facts=["Observed 1 evidence record"],
        uncertainty_statement="Moderate",
        summary="Test summary",
        claim_citations=[claim],
    )

    # Pydantic conversion
    synth_resp = RCASynthesisResponse.from_result(synth)
    assert synth_resp.is_root_cause_confirmed is False
    assert len(synth_resp.claim_citations) == 1
    serialized = synth_resp.model_dump()

    assert serialized["is_root_cause_confirmed"] is False
    assert serialized["claim_citations"][0]["claim_id"] == "claim-test-1"
    assert serialized["claim_citations"][0]["citations"][0]["log_id"] == "log-100"
    assert serialized["claim_citations"][0]["citations"][0]["log_snippet"] == "Connection timed out"
    assert serialized["findings"][0]["claim_citations"][0]["claim_id"] == "claim-test-1"

    # Deserialization
    reconstructed = RCASynthesisResponse.model_validate(serialized)
    assert reconstructed.claim_citations[0].citations[0].log_id == "log-100"
    assert reconstructed.claim_citations[0].citations[0].timestamp == t0
