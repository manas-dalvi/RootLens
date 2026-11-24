"""RootLens Stage 5: Evidence-Grounded Root Cause Analysis (RCA) Integration Tests.

Validates:
1. Single-service failure analysis and evidence grounding.
2. Multi-service cascading failure analysis and downstream symptom distinction.
3. LogBERT-detected HDFS sequence anomaly integration into RCA findings.
4. Incident with insufficient evidence (strict abstention and inconclusive result).
5. Conflicting or ambiguous evidence detection and contradictory signal isolation.
6. Missing or fabricated log references rejection and citation validation.
7. Incorrect chronological ordering detection and timeline consistency bounds.
8. Operator verification safeguards (human-in-the-loop, permission checks, anti-auto-confirmation).
9. Evidence traceability and provenance hash verification.
10. Existing microservice regression (microservices continuing to bypass HDFS LogBERT).
"""

from datetime import datetime, timedelta, timezone
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services.evidence_analysis_service import (
    EvidenceReference,
    EvidenceSufficiencyAssessment,
    EvidenceSufficiencyClassification,
    evaluate_evidence_sufficiency,
)
from app.services.rca_synthesis_service import (
    RCACandidateFinding,
    RCACitationItem,
    RCAClaimMapping,
    RCASynthesisResult,
    synthesize_rca_findings,
    validate_and_resolve_rca_citations,
)
from app.services.logbert_service import is_hdfs_domain_compatible
from app.services.clustering_service import LogEventItem
from app.services.hypothesis_decision_service import (
    record_hypothesis_decision,
    get_hypothesis_decisions_summary,
)
from app.schemas.incident import (
    HypothesisDecisionEnum,
    RCAHypothesisDecisionCreateRequest,
)
from app.core.auth import OperatorIdentity


@pytest.fixture
def client():
    return TestClient(app)


# ==============================================================================
# Scenario 1: Single-Service Failure Analysis
# ==============================================================================
def test_1_single_service_failure():
    """Verify single-service intra-service failure cascade preserves evidence grounding."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "pay-log-01",
            "service_name": "payment-service",
            "timestamp": t0,
            "evidence_type": "CULPRIT_LOG",
            "correlation_rule": "FIRST_FAILURE",
            "trace_id": "trace-single-pay-1",
            "content_hash": "a1b2c3d4e5",
            "log_snippet": "Connection pool exhausted to payment gateway",
        },
        {
            "log_id": "pay-log-02",
            "service_name": "payment-service",
            "timestamp": t0 + timedelta(seconds=15),
            "evidence_type": "CORRELATION_SPIKE",
            "correlation_rule": "TRACE_LINKED",
            "trace_id": "trace-single-pay-1",
            "content_hash": "f6g7h8i9j0",
            "log_snippet": "Transaction rollback triggered after timeout",
        },
    ]
    timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "payment-service",
            "event_type": "CULPRIT_LOG",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "pay-log-01",
            "trace_id": "trace-single-pay-1",
        },
        {
            "sequence_order": 1,
            "timestamp": t0 + timedelta(seconds=15),
            "service_name": "payment-service",
            "event_type": "CORRELATION_SPIKE",
            "is_earliest_observed_failure": False,
            "propagation_lag_seconds": 15.0,
            "sample_log_id": "pay-log-02",
            "trace_id": "trace-single-pay-1",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    assert assessment.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert assessment.distinct_service_count == 1
    assert assessment.earliest_failure_service == "payment-service"
    assert assessment.is_root_cause_confirmed is False

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=timeline)
    assert rca.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert rca.is_root_cause_confirmed is False
    assert rca.potential_root_cause_service == "payment-service"
    assert "Intra-service failure cascade" in (rca.primary_hypothesis or "")
    assert rca.suspected_failure_mechanism is not None
    assert len(rca.findings) == 1
    assert rca.findings[0].is_root_cause_confirmed is False
    assert len(rca.findings[0].supporting_evidence_references) == 2


# ==============================================================================
# Scenario 2: Multi-Service Cascading Failure
# ==============================================================================
def test_2_multiservice_cascading_failure():
    """Verify multi-service cascade distinguishes initiating service from downstream symptoms."""
    t0 = datetime(2026, 11, 24, 14, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "db-log-01",
            "service_name": "postgres-db",
            "timestamp": t0,
            "evidence_type": "CULPRIT_LOG",
            "correlation_rule": "FIRST_FAILURE",
            "trace_id": "trace-multi-cascade-99",
            "content_hash": "db1111",
            "log_snippet": "FATAL: terminating connection due to administrator command",
        },
        {
            "log_id": "ord-log-02",
            "service_name": "order-service",
            "timestamp": t0 + timedelta(seconds=2),
            "evidence_type": "CORRELATION_SPIKE",
            "correlation_rule": "TRACE_LINKED",
            "trace_id": "trace-multi-cascade-99",
            "content_hash": "ord2222",
            "log_snippet": "Database connection refused on pool checkout",
        },
        {
            "log_id": "gw-log-03",
            "service_name": "api-gateway",
            "timestamp": t0 + timedelta(seconds=5),
            "evidence_type": "CORRELATION_SPIKE",
            "correlation_rule": "TRACE_LINKED",
            "trace_id": "trace-multi-cascade-99",
            "content_hash": "gw3333",
            "log_snippet": "HTTP 502 Bad Gateway from upstream order-service",
        },
    ]
    timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "postgres-db",
            "event_type": "CULPRIT_LOG",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "db-log-01",
            "trace_id": "trace-multi-cascade-99",
        },
        {
            "sequence_order": 1,
            "timestamp": t0 + timedelta(seconds=2),
            "service_name": "order-service",
            "event_type": "CORRELATION_SPIKE",
            "is_earliest_observed_failure": False,
            "propagation_lag_seconds": 2.0,
            "sample_log_id": "ord-log-02",
            "trace_id": "trace-multi-cascade-99",
        },
        {
            "sequence_order": 2,
            "timestamp": t0 + timedelta(seconds=5),
            "service_name": "api-gateway",
            "event_type": "CORRELATION_SPIKE",
            "is_earliest_observed_failure": False,
            "propagation_lag_seconds": 3.0,
            "sample_log_id": "gw-log-03",
            "trace_id": "trace-multi-cascade-99",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    assert assessment.classification == EvidenceSufficiencyClassification.TRACE_LINKED
    assert assessment.distinct_service_count == 3
    assert assessment.earliest_failure_service == "postgres-db"
    assert len(assessment.downstream_symptoms) == 2

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=timeline)
    assert rca.potential_root_cause_service == "postgres-db"
    assert rca.is_root_cause_confirmed is False
    assert "postgres-db" in (rca.primary_hypothesis or "")
    assert "api-gateway" in (rca.primary_hypothesis or "")
    assert "order-service" in (rca.primary_hypothesis or "")
    assert len(rca.downstream_symptoms) == 2
    # Verify citations exist and are all supported
    for f in rca.findings:
        assert f.is_root_cause_confirmed is False
        for c in f.claim_citations:
            if c.claim_type == "INFERRED_HYPOTHESIS":
                assert c.is_supported is True
                assert len(c.citations) > 0


# ==============================================================================
# Scenario 3: LogBERT-Detected HDFS Anomaly Integration
# ==============================================================================
def test_3_logbert_detected_hdfs_anomaly():
    """Verify LogBERT sequence anomaly clusters feed into structured RCA with block citations."""
    t0 = datetime(2008, 11, 10, 12, 29, 39, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "hdfs-e5-01",
            "service_name": "hdfs-datanode",
            "timestamp": t0,
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "trace_id": None,
            "content_hash": "hash_hdfs_e5",
            "log_snippet": "Receiving block blk_6492489344990292445 src: /10.251.75.79:33050 dest: /10.251.75.79:50010",
        },
        {
            "log_id": "hdfs-e22-02",
            "service_name": "hdfs-namenode",
            "timestamp": t0,
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "trace_id": None,
            "content_hash": "hash_hdfs_e22",
            "log_snippet": "BLOCK* NameSystem.allocateBlock: blk_6492489344990292445",
        },
        {
            "log_id": "hdfs-e21-03",
            "service_name": "hdfs",
            "timestamp": t0 + timedelta(hours=2),
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "trace_id": None,
            "content_hash": "hash_hdfs_e21",
            "log_snippet": "Deleting block blk_6492489344990292445 file /mnt/hadoop/dfs/data/current/blk_6492489344990292445",
        },
    ]
    timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "hdfs-datanode",
            "event_type": "SEQUENCE_ANOMALY",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "hdfs-e5-01",
            "cluster_id": "cl-logbert-blk_649248934499-200811101215",
        },
        {
            "sequence_order": 1,
            "timestamp": t0,
            "service_name": "hdfs-namenode",
            "event_type": "SEQUENCE_ANOMALY",
            "is_earliest_observed_failure": False,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "hdfs-e22-02",
            "cluster_id": "cl-logbert-blk_649248934499-200811101215",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    assert assessment.has_sequence_anomaly is True
    assert "blk_6492489344990292445" in assessment.sequence_anomaly_block_ids
    assert assessment.sequence_anomaly_count >= 2
    assert assessment.is_root_cause_confirmed is False

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=timeline)
    assert rca.is_root_cause_confirmed is False
    assert rca.potential_root_cause_service == "hdfs-datanode"
    assert "LogBERT" in (rca.suspected_failure_mechanism or "")
    assert "blk_6492489344990292445" in (rca.suspected_failure_mechanism or "")
    assert len(rca.alternative_explanations) > 0
    # Confirm sequence anomaly claim is present and supported
    seq_claim = next((c for c in rca.claim_citations if "sequence-anomaly" in c.claim_id), None)
    assert seq_claim is not None
    assert seq_claim.is_supported is True
    assert len(seq_claim.citations) > 0


# ==============================================================================
# Scenario 4: Incident with Insufficient Evidence
# ==============================================================================
def test_4_incident_with_insufficient_evidence():
    """Verify sparse isolated errors trigger explicit abstention without fabricated cause."""
    t0 = datetime(2026, 11, 24, 15, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "sparse-log-01",
            "service_name": "auth-service",
            "timestamp": t0,
            "evidence_type": "ERROR",
            "correlation_rule": "LOCAL_SERVICE",
            "trace_id": None,
            "content_hash": "sparse_hash",
            "log_snippet": "Uncaught token decode error",
        }
    ]
    timeline = []

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    assert assessment.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert assessment.is_root_cause_confirmed is False

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=timeline)
    assert rca.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert rca.primary_hypothesis is None  # Strictly None
    assert rca.potential_root_cause_service is None
    assert rca.is_root_cause_confirmed is False
    assert "Inconclusive" in (rca.suspected_failure_mechanism or "")
    assert len(rca.alternative_explanations) >= 2


# ==============================================================================
# Scenario 5: Conflicting or Ambiguous Evidence
# ==============================================================================
def test_5_conflicting_or_ambiguous_evidence():
    """Verify contradictory signals isolate conflicting logs and report diagnostic bounds."""
    t0 = datetime(2026, 11, 24, 16, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "app-early-01",
            "service_name": "cart-service",
            "timestamp": t0,
            "evidence_type": "CULPRIT_LOG",
            "correlation_rule": "FIRST_FAILURE",
            "trace_id": "trace-shared-alpha",
            "content_hash": "hash_cart",
            "log_snippet": "Cart redis timeout",
        },
        {
            "log_id": "unrelated-trace-02",
            "service_name": "cart-service",
            "timestamp": t0 + timedelta(seconds=1),
            "evidence_type": "CORRELATION_SPIKE",
            "correlation_rule": "TRACE_LINKED",
            "trace_id": "trace-different-omega",  # Disjoint conflicting trace
            "content_hash": "hash_unrelated",
            "log_snippet": "Unrelated payment callback timeout",
        },
        {
            "log_id": "prior-error-03",
            "service_name": "inventory-service",
            "timestamp": t0 - timedelta(minutes=5),  # Error occurring before designated earliest failure
            "evidence_type": "CORRELATION_SPIKE",
            "correlation_rule": "LOCAL_SERVICE",
            "trace_id": None,
            "log_level": "ERROR",
            "content_hash": "hash_prior",
            "log_snippet": "Deadlock detected in inventory allocations",
        },
    ]
    timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "cart-service",
            "event_type": "CULPRIT_LOG",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "app-early-01",
            "trace_id": "trace-shared-alpha",
        }
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=timeline)

    finding = rca.findings[0]
    assert len(finding.contradictory_signals) > 0
    assert len(finding.contradictory_evidence_references) > 0
    contradictory_ids = {r.log_id for r in finding.contradictory_evidence_references}
    assert "prior-error-03" in contradictory_ids
    assert finding.is_root_cause_confirmed is False


# ==============================================================================
# Scenario 6: Missing or Fabricated Log References Rejection
# ==============================================================================
def test_6_missing_or_fabricated_log_references():
    """Verify citation validator strips fabricated or mismatched citations and flags claims as unsupported."""
    real_evidence = [
        {
            "log_id": "real-doc-42",
            "service_name": "catalog-service",
            "timestamp": datetime(2026, 11, 24, 17, 0, 0, tzinfo=timezone.utc),
            "evidence_type": "CULPRIT_LOG",
            "content_hash": "real_hash_42",
            "log_snippet": "Elasticsearch cluster unreachable",
        }
    ]

    fabricated_claims = [
        RCAClaimMapping(
            claim_id="claim-fake-id",
            claim_text="Database crashed due to memory limit",
            claim_type="INFERRED_HYPOTHESIS",
            is_supported=True,
            citations=[
                RCACitationItem(
                    log_id="totally-fabricated-log-9999",
                    service_name="catalog-service",
                    evidence_type="CULPRIT_LOG",
                )
            ],
        ),
        RCAClaimMapping(
            claim_id="claim-wrong-service",
            claim_text="Catalog crashed due to payment error",
            claim_type="INFERRED_HYPOTHESIS",
            is_supported=True,
            citations=[
                RCACitationItem(
                    log_id="real-doc-42",
                    service_name="payment-service",  # Service mismatch! Real doc belongs to catalog-service
                    evidence_type="CULPRIT_LOG",
                )
            ],
        ),
    ]

    resolved = validate_and_resolve_rca_citations(claims=fabricated_claims, evidence_items=real_evidence)

    # Claim 1: Fabricated log ID
    assert resolved[0].is_supported is False
    assert len(resolved[0].citations) == 0
    assert "not found" in (resolved[0].unsupported_reason or "").lower()

    # Claim 2: Service mismatch
    assert resolved[1].is_supported is False
    assert len(resolved[1].citations) == 0
    assert "service mismatch" in (resolved[1].unsupported_reason or "").lower()


# ==============================================================================
# Scenario 7: Incorrect Chronological Ordering
# ==============================================================================
def test_7_incorrect_chronological_ordering():
    """Verify non-monotonic timestamp ordering causes timeline consistency failure and abstention."""
    t0 = datetime(2026, 11, 24, 18, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "ev-01",
            "service_name": "svc-a",
            "timestamp": t0,
            "evidence_type": "ERROR",
            "correlation_rule": "LOCAL_SERVICE",
        },
        {
            "log_id": "ev-02",
            "service_name": "svc-b",
            "timestamp": t0 - timedelta(seconds=30),  # Decreasing timestamp
            "evidence_type": "ERROR",
            "correlation_rule": "LOCAL_SERVICE",
        },
    ]
    corrupted_timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "svc-a",
            "event_type": "ERROR",
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "ev-01",
        },
        {
            "sequence_order": 1,
            "timestamp": t0 - timedelta(seconds=30),  # Inverted sequence!
            "service_name": "svc-b",
            "event_type": "ERROR",
            "propagation_lag_seconds": -30.0,  # Negative propagation lag
            "sample_log_id": "ev-02",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=corrupted_timeline)
    assert assessment.is_timeline_temporally_consistent is False
    assert assessment.classification == EvidenceSufficiencyClassification.INSUFFICIENT
    assert assessment.is_root_cause_confirmed is False


# ==============================================================================
# Scenario 8: Operator Verification Safeguards
# ==============================================================================
@pytest.mark.asyncio
async def test_8_operator_verification_safeguards():
    """Verify is_root_cause_confirmed defaults to False and requires authorized operator action."""
    # 1. Verification that assessment and RCA never auto-confirm
    assessment = evaluate_evidence_sufficiency([], [])
    assert assessment.is_root_cause_confirmed is False
    rca = synthesize_rca_findings(assessment, [], [])
    assert rca.is_root_cause_confirmed is False

    # 2. Rejection of unsupported hypothesis confirmation
    from unittest.mock import AsyncMock, MagicMock
    from tests.test_hypothesis_decisions import _make_test_incident_with_evidence
    import uuid
    from fastapi import HTTPException

    inc_id = uuid.uuid4()
    incident = _make_test_incident_with_evidence(inc_id)

    mock_operator = OperatorIdentity(
        operator_id="op-123",
        operator_name="lead_sre",
        operator_email="lead_sre@example.com",
        role="OPERATOR",
        is_authorized=True,
    )

    unsupported_req = RCAHypothesisDecisionCreateRequest(
        decision=HypothesisDecisionEnum.CONFIRM,
        notes="Attempting to confirm without supporting evidence citations",
        hypothesis_text="Specular theory without evidence",
    )

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = incident
    mock_session.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await record_hypothesis_decision(
            session=mock_session,
            incident_id=inc_id,
            hypothesis_id="finding-1-telemetry-gap",
            req=unsupported_req,
            operator=mock_operator,
        )
    assert exc_info.value.status_code == 400
    assert "cannot confirm unsupported hypothesis" in exc_info.value.detail.lower()


# ==============================================================================
# Scenario 9: Evidence Traceability and Provenance
# ==============================================================================
def test_9_evidence_traceability():
    """Verify all claim citations preserve exact source log_id, service_name, snippet, and content_hash."""
    t0 = datetime(2026, 11, 24, 19, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "traceable-doc-88",
            "service_name": "billing-service",
            "timestamp": t0,
            "evidence_type": "CULPRIT_LOG",
            "correlation_rule": "FIRST_FAILURE",
            "trace_id": "trace-prov-88",
            "content_hash": "sha256_e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "log_snippet": "Stripe webhook signature expired",
        },
        {
            "log_id": "traceable-doc-89",
            "service_name": "billing-service",
            "timestamp": t0 + timedelta(seconds=10),
            "evidence_type": "CORRELATION_SPIKE",
            "correlation_rule": "TRACE_LINKED",
            "trace_id": "trace-prov-88",
            "content_hash": "sha256_ca978112ca1bbdcafac231b39a23dc4da78608149616099287071f30f000",
            "log_snippet": "Invoice generation failed due to invalid signature",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=[])
    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=[])

    all_citations = [cit for claim in rca.claim_citations for cit in claim.citations]
    assert len(all_citations) > 0

    for cit in all_citations:
        assert cit.log_id in ("traceable-doc-88", "traceable-doc-89")
        assert cit.service_name == "billing-service"
        assert cit.log_snippet is not None
        assert cit.content_hash.startswith("sha256_")


# ==============================================================================
# Scenario 10: Existing Microservice Regression Bypasses HDFS LogBERT
# ==============================================================================
def test_10_microservice_regression_bypasses_logbert():
    """Verify ordinary microservice logs continue bypassing HDFS-specific LogBERT inference."""
    # 1. postgres-db log
    pg_log = LogEventItem(
        log_id="pg-01",
        timestamp=datetime.now(timezone.utc),
        service_name="postgres-db",
        log_level="ERROR",
        message="could not connect to server: Connection refused",
        attributes={"service": "postgres-db"},
    )
    is_compatible, reason, tokens = is_hdfs_domain_compatible(session_id="pg-session-1", session_events=[pg_log])
    assert is_compatible is False
    assert "non_hdfs_microservice" in reason.lower()

    # 2. payment-service log with deceptive E-number in error text
    pay_log = LogEventItem(
        log_id="pay-01",
        timestamp=datetime.now(timezone.utc),
        service_name="payment-service",
        log_level="ERROR",
        message="Payment transaction E11 failed with timeout",
        attributes={"service": "payment-service"},
    )
    is_compatible, reason, tokens = is_hdfs_domain_compatible(session_id="pay-session-1", session_events=[pay_log])
    assert is_compatible is False
    assert "non_hdfs_microservice" in reason.lower()

    # 3. api-gateway log
    gw_log = LogEventItem(
        log_id="gw-01",
        timestamp=datetime.now(timezone.utc),
        service_name="api-gateway",
        log_level="ERROR",
        message="504 Gateway Timeout while proxying request",
        attributes={"service": "api-gateway"},
    )
    is_compatible, reason, tokens = is_hdfs_domain_compatible(session_id="gw-session-1", session_events=[gw_log])
    assert is_compatible is False
    assert "non_hdfs_microservice" in reason.lower()


# ==============================================================================
# Scenario 11: LogBERT Anomaly Service Differs from Earliest Failure Service
# ==============================================================================
def test_11_logbert_anomaly_service_differs_from_earliest_failure():
    """Verify sequence failure mechanism is attributed to actual anomaly service, not earliest failure service."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "pg-early-01",
            "service_name": "postgres-db",
            "timestamp": t0,
            "evidence_type": "CULPRIT_LOG",
            "correlation_rule": "FIRST_FAILURE",
            "trace_id": None,
            "content_hash": "hash_pg_01",
            "log_snippet": "FATAL: connection limit exceeded",
        },
        {
            "log_id": "hdfs-seq-02",
            "service_name": "hdfs-datanode",
            "timestamp": t0 + timedelta(seconds=5),
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "trace_id": None,
            "content_hash": "hash_hdfs_seq",
            "log_snippet": "PacketResponder terminating for block blk_7777777777",
        },
    ]
    timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "postgres-db",
            "event_type": "CULPRIT_LOG",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "pg-early-01",
        },
        {
            "sequence_order": 1,
            "timestamp": t0 + timedelta(seconds=5),
            "service_name": "hdfs-datanode",
            "event_type": "SEQUENCE_ANOMALY",
            "is_earliest_observed_failure": False,
            "propagation_lag_seconds": 5.0,
            "sample_log_id": "hdfs-seq-02",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    assert assessment.earliest_failure_service == "postgres-db"
    assert assessment.sequence_anomaly_services == ["hdfs-datanode"]
    assert "blk_7777777777" in assessment.sequence_anomaly_block_ids

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=timeline)
    assert rca.potential_root_cause_service == "postgres-db"
    # The sequence anomaly mechanism MUST reference hdfs-datanode, NEVER postgres-db
    assert "in service 'hdfs-datanode'" in (rca.suspected_failure_mechanism or "")
    assert "in service 'postgres-db'" not in (rca.suspected_failure_mechanism or "")

    seq_claim = next((c for c in rca.claim_citations if c.claim_id == "finding-1-sequence-anomaly"), None)
    assert seq_claim is not None
    assert "in service 'hdfs-datanode'" in seq_claim.claim_text
    assert "in service 'postgres-db'" not in seq_claim.claim_text


# ==============================================================================
# Scenario 12: Unrelated Block IDs in Incident Evidence Ignored
# ==============================================================================
def test_12_unrelated_block_ids_in_incident_evidence_ignored():
    """Verify block IDs are extracted ONLY from SEQUENCE_ANOMALY records, not unrelated general logs."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "gen-err-01",
            "service_name": "postgres-db",
            "timestamp": t0,
            "evidence_type": "CORRELATED_ERROR",
            "correlation_rule": "ERROR_SURGE",
            "trace_id": None,
            "content_hash": "hash_gen",
            "log_snippet": "Unrelated diagnostic message mentioning blk_9999999999999 in text",
        },
        {
            "log_id": "seq-anom-02",
            "service_name": "hdfs-datanode",
            "timestamp": t0 + timedelta(seconds=2),
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "trace_id": None,
            "content_hash": "hash_seq",
            "log_snippet": "Receiving block blk_1111111111111 src: /10.0.0.1:50010 dest: /10.0.0.2:50010",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=[])
    # Must contain ONLY the block ID from the sequence anomaly record
    assert "blk_1111111111111" in assessment.sequence_anomaly_block_ids
    assert "blk_9999999999999" not in assessment.sequence_anomaly_block_ids

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=[])
    assert "blk_1111111111111" in (rca.suspected_failure_mechanism or "")
    assert "blk_9999999999999" not in (rca.suspected_failure_mechanism or "")


# ==============================================================================
# Scenario 13: Duplicate Anomaly in Evidence and Timeline Deduplicated
# ==============================================================================
def test_13_duplicate_anomaly_in_evidence_and_timeline_deduplicated():
    """Verify the same sequence anomaly appearing in evidence and timeline is not double-counted."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "seq-doc-100",
            "service_name": "hdfs-datanode",
            "timestamp": t0,
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "trace_id": None,
            "content_hash": "hash_seq_100",
            "log_snippet": "Block blk_4444444444 out of sequence",
        },
    ]
    timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "hdfs-datanode",
            "event_type": "SEQUENCE_ANOMALY",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "seq-doc-100",  # Points to the exact same log record
            "cluster_id": "cl-logbert-blk_4444444444",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    assert assessment.has_sequence_anomaly is True
    # Count must be exactly 1, NOT 2
    assert assessment.sequence_anomaly_count == 1
    assert assessment.sequence_anomaly_block_ids == ["blk_4444444444"]
    assert assessment.sequence_anomaly_services == ["hdfs-datanode"]


# ==============================================================================
# Scenario 14: Service in Incident Without Downstream Failure Excluded
# ==============================================================================
def test_14_service_in_incident_without_downstream_failure_excluded():
    """Verify services with no subsequent failure evidence are not labeled as downstream symptoms."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "ord-01",
            "service_name": "order-service",
            "timestamp": t0,
            "evidence_type": "CULPRIT_LOG",
            "log_level": "ERROR",
            "log_snippet": "Connection pool exhausted",
        },
        {
            "log_id": "pay-02",
            "service_name": "payment-service",
            "timestamp": t0 + timedelta(seconds=2),
            "evidence_type": "CORRELATED_ERROR",
            "log_level": "ERROR",
            "log_snippet": "Payment processing failed due to upstream order-service",
        },
        {
            "log_id": "inv-03",
            "service_name": "inventory-service",
            "timestamp": t0 + timedelta(seconds=3),
            "evidence_type": "INFO",
            "log_level": "INFO",  # Merely informational log, NOT a failure
            "log_snippet": "Inventory check completed successfully",
        },
    ]
    timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "order-service",
            "event_type": "CULPRIT_LOG",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "ord-01",
        },
        {
            "sequence_order": 1,
            "timestamp": t0 + timedelta(seconds=2),
            "service_name": "payment-service",
            "event_type": "CORRELATED_ERROR",
            "is_earliest_observed_failure": False,
            "propagation_lag_seconds": 2.0,
            "sample_log_id": "pay-02",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    assert assessment.earliest_failure_service == "order-service"
    # payment-service has subsequent failure evidence -> INCLUDED
    assert any("payment-service" in s for s in assessment.downstream_symptoms)
    # inventory-service has only INFO -> EXCLUDED
    assert not any("inventory-service" in s for s in assessment.downstream_symptoms)
    # initiating service order-service -> EXCLUDED
    assert not any("order-service" in s for s in assessment.downstream_symptoms)

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=timeline)
    assert any("payment-service" in s for s in rca.downstream_symptoms)
    assert not any("inventory-service" in s for s in rca.downstream_symptoms)


# ==============================================================================
# Scenario 15: Out-of-Order Downstream Timestamps Excluded
# ==============================================================================
def test_15_out_of_order_downstream_timestamps_excluded():
    """Verify records with timestamps preceding earliest failure cannot be counted as downstream symptoms."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "main-01",
            "service_name": "checkout-service",
            "timestamp": t0,
            "evidence_type": "CULPRIT_LOG",
            "log_level": "ERROR",
            "log_snippet": "Checkout failed with timeout",
        },
        {
            "log_id": "old-02",
            "service_name": "auth-service",
            "timestamp": t0 - timedelta(minutes=5),  # Preceding t0 -> out of order for downstream symptom
            "evidence_type": "CORRELATED_ERROR",
            "log_level": "ERROR",
            "log_snippet": "Auth service token expired",
        },
        {
            "log_id": "valid-03",
            "service_name": "notification-service",
            "timestamp": t0 + timedelta(seconds=10),
            "evidence_type": "CORRELATED_ERROR",
            "log_level": "ERROR",
            "log_snippet": "Notification delivery failed",
        },
    ]
    timeline = [
        {
            "sequence_order": 0,
            "timestamp": t0,
            "service_name": "checkout-service",
            "event_type": "CULPRIT_LOG",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "main-01",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=timeline)
    assert assessment.earliest_failure_service == "checkout-service"
    # notification-service occurred after t0 -> INCLUDED
    assert any("notification-service" in s for s in assessment.downstream_symptoms)
    # auth-service occurred before t0 -> EXCLUDED as downstream symptom
    assert not any("auth-service" in s for s in assessment.downstream_symptoms)


# ==============================================================================
# Scenario 16: Missing Sequence Anomaly Service Attribution
# ==============================================================================
def test_16_missing_sequence_anomaly_service_attribution():
    """Verify sequence anomaly with unknown service explicitly reports inconclusive attribution without fabricating."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    evidence = [
        {
            "log_id": "seq-anon-01",
            "service_name": None,  # Missing service attribution
            "timestamp": t0,
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "trace_id": None,
            "content_hash": "hash_anon",
            "log_snippet": "Receiving block blk_5555555555 unexpected transition",
        },
        {
            "log_id": "seq-anon-02",
            "service_name": "unknown",  # Unknown service attribution
            "timestamp": t0 + timedelta(seconds=1),
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "trace_id": None,
            "content_hash": "hash_anon2",
            "log_snippet": "Block blk_5555555555 out-of-order execution",
        },
    ]

    assessment = evaluate_evidence_sufficiency(evidence_items=evidence, timeline_events=[])
    assert assessment.has_sequence_anomaly is True
    assert assessment.sequence_anomaly_services == []

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=[])
    # Must explicitly state unknown service attribution and NOT invent a service name
    assert "with unknown service attribution" in (rca.suspected_failure_mechanism or "")
    assert "in service '" not in (rca.suspected_failure_mechanism or "")

    seq_claim = next((c for c in rca.claim_citations if c.claim_id == "finding-1-sequence-anomaly"), None)
    assert seq_claim is not None
    assert "unknown service" in seq_claim.claim_text


# ==============================================================================
# Scenario 17: Alternative Hypotheses Remain Explicitly Unverified
# ==============================================================================
def test_17_alternative_hypotheses_remain_explicitly_unverified():
    """Verify alternative explanations are explicitly labeled as unverified hypotheses and have no citations."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)

    # 1. INSUFFICIENT telemetry case
    rca_insuff = synthesize_rca_findings(evidence_items=[], timeline_events=[])
    assert len(rca_insuff.alternative_explanations) >= 2
    for alt in rca_insuff.alternative_explanations:
        assert alt.startswith("[Unverified Alternative]")

    # 2. TEMPORAL_ONLY case with sequence anomaly
    evidence = [
        {
            "log_id": "ev-01",
            "service_name": "hdfs-datanode",
            "timestamp": t0,
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "content_hash": "hash_01",
            "log_snippet": "blk_3333333333 sequence anomaly",
        },
        {
            "log_id": "ev-02",
            "service_name": "hdfs-datanode",
            "timestamp": t0 + timedelta(seconds=1),
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "content_hash": "hash_02",
            "log_snippet": "blk_3333333333 out of order",
        },
    ]
    rca_seq = synthesize_rca_findings(evidence_items=evidence, timeline_events=[])
    assert len(rca_seq.alternative_explanations) >= 2
    for alt in rca_seq.alternative_explanations:
        assert alt.startswith("[Unverified Alternative]")

    # Confirm NO claim citations are attached to alternative explanations
    claim_texts = {c.claim_text for c in rca_seq.claim_citations}
    for alt in rca_seq.alternative_explanations:
        # None of the alternative hypotheses should be presented as verified claims with citations
        matching_claim = next((c for c in rca_seq.claim_citations if c.claim_text == alt), None)
        assert matching_claim is None

    # Strict invariant: root cause confirmation is False
    assert rca_seq.is_root_cause_confirmed is False
    assert rca_insuff.is_root_cause_confirmed is False


# ==============================================================================
# Scenario 18: Sequence Anomaly Citation Service Filter and Boolean Precedence
# ==============================================================================
def test_18_sequence_anomaly_citation_service_filter():
    """Verify sequence anomaly citations strictly filter on target service for both evidence_type and correlation_rule paths."""
    t0 = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    evidence = [
        # Anomaly from correct service via evidence_type
        {
            "log_id": "seq-correct-type",
            "service_name": "hdfs-datanode",
            "timestamp": t0,
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "OTHER_RULE",
            "content_hash": "hash_correct_1",
            "log_snippet": "Receiving block blk_1111111111 from datanode",
        },
        # Anomaly from correct service via correlation_rule
        {
            "log_id": "seq-correct-rule",
            "service_name": "hdfs-datanode",
            "timestamp": t0 + timedelta(seconds=1),
            "evidence_type": "CORRELATED_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "content_hash": "hash_correct_2",
            "log_snippet": "PacketResponder terminating for block blk_1111111111",
        },
        # Anomaly from UNRELATED service via evidence_type (must be excluded despite evidence_type == SEQUENCE_ANOMALY)
        {
            "log_id": "seq-unrelated-type",
            "service_name": "unrelated-service",
            "timestamp": t0 + timedelta(seconds=2),
            "evidence_type": "SEQUENCE_ANOMALY",
            "correlation_rule": "OTHER_RULE",
            "content_hash": "hash_unrelated_1",
            "log_snippet": "Unrelated sequence anomaly in another service",
        },
        # Anomaly from UNRELATED service via correlation_rule (must be excluded)
        {
            "log_id": "seq-unrelated-rule",
            "service_name": "unrelated-service",
            "timestamp": t0 + timedelta(seconds=3),
            "evidence_type": "CORRELATED_ANOMALY",
            "correlation_rule": "SEQUENCE_ANOMALY",
            "content_hash": "hash_unrelated_2",
            "log_snippet": "Unrelated correlation anomaly in another service",
        },
    ]

    assessment = EvidenceSufficiencyAssessment(
        classification=EvidenceSufficiencyClassification.TEMPORAL_ONLY,
        is_root_cause_confirmed=False,
        distinct_evidence_count=4,
        distinct_service_count=2,
        distinct_services=["hdfs-datanode", "unrelated-service"],
        earliest_failure_service="hdfs-datanode",
        earliest_failure_timestamp=t0,
        earliest_failure_log_id="seq-correct-type",
        has_sequence_anomaly=True,
        sequence_anomaly_count=2,
        sequence_anomaly_block_ids=["blk_1111111111"],
        sequence_anomaly_services=["hdfs-datanode"],  # Strictly restricted to hdfs-datanode
        evidence_references=[
            EvidenceReference(
                log_id=e["log_id"],
                service_name=e["service_name"],
                timestamp=e["timestamp"],
                evidence_type=e["evidence_type"],
                correlation_rule=e["correlation_rule"],
                content_hash=e["content_hash"],
                log_snippet=e["log_snippet"],
            )
            for e in evidence
        ],
    )

    rca = synthesize_rca_findings(assessment=assessment, evidence_items=evidence, timeline_events=[])
    seq_claim = next((c for c in rca.claim_citations if c.claim_id == "finding-1-sequence-anomaly"), None)
    assert seq_claim is not None
    assert seq_claim.is_supported is True

    citation_log_ids = {cit.log_id for cit in seq_claim.citations}
    # 1. Sequence anomaly from correct service via evidence_type must be ACCEPTED
    assert "seq-correct-type" in citation_log_ids
    # 2. Sequence anomaly from correct service via correlation_rule must be ACCEPTED
    assert "seq-correct-rule" in citation_log_ids
    # 3. Sequence anomaly from unrelated service via evidence_type must be EXCLUDED
    assert "seq-unrelated-type" not in citation_log_ids
    # 4. Sequence anomaly from unrelated service via correlation_rule must be EXCLUDED
    assert "seq-unrelated-rule" not in citation_log_ids

    # Citation validation check: every cited record must match source evidence
    for cit in seq_claim.citations:
        matching_source = next((e for e in evidence if e["log_id"] == cit.log_id), None)
        assert matching_source is not None
        assert cit.service_name == "hdfs-datanode"
        assert cit.content_hash == matching_source["content_hash"]


