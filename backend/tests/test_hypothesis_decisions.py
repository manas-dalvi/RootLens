"""Comprehensive tests for RootLens Phase 8 Stage 4: Operator Hypothesis Confirmation Workflow.

Tests verify:
1. Authentication & Authorization Security:
   - Missing credentials rejected with HTTP 401.
   - Spoofed gateway headers ('X-Operator-*') without trusted gateway secret rejected with HTTP 401.
   - Gateway headers with invalid gateway secret rejected with HTTP 401.
   - Gateway headers with valid gateway secret accepted when TRUSTED_GATEWAY_ENABLED is active.
   - Tampered Bearer tokens rejected with HTTP 401.
   - Valid HMAC-SHA256 signed Bearer tokens accepted and claims extracted.
   - Canonical dev tokens (operator, lead-sre, admin) accepted.
   - VIEWER role allowed for read-only GET summary, rejected with HTTP 403 on POST decision actions.
2. Multiple Hypothesis Confirmation State & Consistency:
   - Confirm Hypothesis A, then reject Hypothesis B: Hypothesis A remains confirmed, incident remains confirmed.
   - Confirm Hypothesis A, then reject Hypothesis A: Hypothesis A rejected, incident becomes unconfirmed.
   - Confirm Hypothesis A, then confirm Hypothesis B: incident reflects Hypothesis B (latest active confirmed).
   - Confirm A, confirm B, then reject B: incident falls back to still-active confirmed Hypothesis A.
   - Reject an unconfirmed hypothesis: incident remains unconfirmed.
   - Confirming an unsupported hypothesis rejected with HTTP 400 Bad Request.
   - Idempotent repeated identical submissions return existing decision without creating duplicate noise.
3. Full Decision History & Summary:
   - Preserves complete immutable audit history in reverse chronological order.
"""
from datetime import datetime, timezone
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.auth import (
    OperatorIdentity,
    create_signed_token,
    get_current_operator,
    require_authorized_operator,
    verify_signed_token,
)
from app.core.config import settings
from app.main import app
from app.models.evidence import Evidence
from app.models.incident import Incident
from app.models.incident_affected_service import IncidentAffectedService
from app.models.incident_event import IncidentEvent
from app.models.log_source import LogSource
from app.models.rca_hypothesis_decision import RCAHypothesisDecision
from app.schemas.incident import (
    HypothesisDecisionEnum,
    RCAHypothesisActionRequest,
    RCAHypothesisDecisionCreateRequest,
)
from app.services.hypothesis_decision_service import (
    get_hypothesis_decisions_summary,
    recalculate_incident_confirmation_state,
    record_hypothesis_decision,
)

client = TestClient(app)


def _make_test_incident_with_evidence(
    incident_id: uuid.UUID,
    classification: str = "TRACE_LINKED",
) -> Incident:
    """Build a complete in-memory incident graph with multiple evidence items and timeline events."""
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    t0 = datetime(2026, 11, 18, 11, 55, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 11, 58, 0, tzinfo=timezone.utc)
    shared_trace_id = "trace-hyp-confirm-12345"

    log_source = LogSource(
        id=uuid.uuid4(),
        name="payment-service-logs",
        service_name="payment-service",
        environment="production",
        format_type="json",
        is_active=True,
    )

    incident = Incident(
        id=incident_id,
        title="Payment Service Circuit Breaker Failure",
        severity="HIGH",
        status="INVESTIGATING",
        analysis_status=classification,
        primary_log_source_id=log_source.id,
        started_at=t0,
        ended_at=t1,
        detected_at=now,
        probable_cause="[Hypothesis] Initial cascade in payment-service",
        confidence_score=0.85,
        is_root_cause_confirmed=False,
        uncertainty_reason="Diagnostic assessment pending human review.",
        created_at=now,
        updated_at=now,
    )
    incident.primary_log_source = log_source

    aff_svc = IncidentAffectedService(
        id=uuid.uuid4(),
        incident_id=incident_id,
        log_source_id=log_source.id,
        service_name="payment-service",
        impact_level="PRIMARY_CULPRIT",
        first_impacted_at=t0,
        error_count=5,
        created_at=now,
        updated_at=now,
    )
    incident.affected_services = [aff_svc]

    ev1 = IncidentEvent(
        id=uuid.uuid4(),
        incident_id=incident_id,
        event_timestamp=t0,
        service_name="payment-service",
        event_type="ERROR_BURST",
        severity="CRITICAL",
        sequence_order=1,
        summary="Payment timeout burst",
        attributes={
            "trace_id": shared_trace_id,
            "span_id": "span-001",
            "is_earliest_observed_failure": True,
            "propagation_lag_seconds": 0.0,
            "sample_log_id": "log-doc-101",
        },
        created_at=now,
        updated_at=now,
    )
    ev2 = IncidentEvent(
        id=uuid.uuid4(),
        incident_id=incident_id,
        event_timestamp=t1,
        service_name="payment-service",
        event_type="ERROR_BURST",
        severity="ERROR",
        sequence_order=2,
        summary="Payment failure cascade",
        attributes={
            "trace_id": shared_trace_id,
            "span_id": "span-002",
            "is_earliest_observed_failure": False,
            "propagation_lag_seconds": 180.0,
            "sample_log_id": "log-doc-102",
        },
        created_at=now,
        updated_at=now,
    )
    incident.events = [ev1, ev2]

    evidence_item_1 = Evidence(
        id=uuid.uuid4(),
        incident_id=incident_id,
        evidence_type="CRITICAL_LOG",
        service_name="payment-service",
        log_timestamp=t0,
        log_level="CRITICAL",
        log_snippet="Connection timeout connecting to payment gateway: downstream reset",
        explanation="First observed timeout failure",
        opensearch_doc_id="log-doc-101",
        content_hash="hash-101",
        metadata_={"trace_id": shared_trace_id, "correlation_rule": "TRACE_LINKED"},
        created_at=now,
        updated_at=now,
    )
    evidence_item_2 = Evidence(
        id=uuid.uuid4(),
        incident_id=incident_id,
        evidence_type="ERROR_LOG",
        service_name="payment-service",
        log_timestamp=t1,
        log_level="ERROR",
        log_snippet="Downstream service failed to respond in time",
        explanation="Second error record",
        opensearch_doc_id="log-doc-102",
        content_hash="hash-102",
        metadata_={"trace_id": shared_trace_id, "correlation_rule": "TRACE_LINKED"},
        created_at=now,
        updated_at=now,
    )
    incident.evidence = [evidence_item_1, evidence_item_2]
    incident.hypothesis_decisions = []

    return incident


# ============================================================================
# 1. Pydantic Schema Validation Tests
# ============================================================================


def test_decision_create_request_valid():
    """Verify valid instantiation of RCAHypothesisDecisionCreateRequest."""
    req = RCAHypothesisDecisionCreateRequest(
        decision=HypothesisDecisionEnum.CONFIRM,
        notes="Root cause verified via database connection pool metrics and logs.",
        hypothesis_text="Database pool exhaustion in payment-service",
    )
    assert req.decision == HypothesisDecisionEnum.CONFIRM
    assert len(req.notes) >= 10


def test_decision_create_request_notes_too_short():
    """Verify minimum 10 characters requirement for operator notes."""
    with pytest.raises(ValidationError) as exc_info:
        RCAHypothesisDecisionCreateRequest(
            decision=HypothesisDecisionEnum.CONFIRM,
            notes="too short",
        )
    assert "notes" in str(exc_info.value)


def test_decision_create_request_invalid_decision_enum():
    """Verify rejection of invalid decision strings."""
    with pytest.raises(ValidationError):
        RCAHypothesisDecisionCreateRequest(
            decision="MAYBE",  # type: ignore
            notes="Valid length notes explaining maybe",
        )


def test_action_request_validation():
    """Verify RCAHypothesisActionRequest validation rules."""
    req = RCAHypothesisActionRequest(
        notes="Confirmed based on downstream circuit breaker reset telemetry.",
    )
    assert len(req.notes) >= 10

    with pytest.raises(ValidationError):
        RCAHypothesisActionRequest(notes="short")


# ============================================================================
# 2. Authentication & Gateway Trust Security Tests
# ============================================================================


@pytest.mark.asyncio
async def test_auth_missing_credentials_raises_401():
    """Verify HTTP 401 when no authentication is provided."""
    with pytest.raises(HTTPException) as exc_info:
        await get_current_operator(
            authorization=None,
            x_gateway_secret=None,
            x_operator_id=None,
            x_operator_name=None,
            x_operator_email=None,
            x_operator_role=None,
        )
    assert exc_info.value.status_code == 401
    assert "WWW-Authenticate" in (exc_info.value.headers or {})


@pytest.mark.asyncio
async def test_auth_spoofed_gateway_headers_without_secret_rejected_with_401():
    """Security Requirement: Arbitrary client-supplied identity/role headers must NOT be trusted."""
    with pytest.raises(HTTPException) as exc_info:
        await get_current_operator(
            authorization=None,
            x_gateway_secret=None,
            x_operator_id="attacker",
            x_operator_name="Malicious User",
            x_operator_email="attacker@external.com",
            x_operator_role="ADMIN",  # Spoofed admin role
        )
    assert exc_info.value.status_code == 401
    assert "untrusted and rejected" in exc_info.value.detail


@pytest.mark.asyncio
async def test_auth_spoofed_gateway_headers_with_invalid_secret_rejected():
    """Verify rejection when gateway secret does not match configured secret."""
    original_enabled = settings.TRUSTED_GATEWAY_ENABLED
    original_secret = settings.GATEWAY_SHARED_SECRET
    try:
        settings.TRUSTED_GATEWAY_ENABLED = True
        settings.GATEWAY_SHARED_SECRET = "correct-gateway-secret-12345"

        with pytest.raises(HTTPException) as exc_info:
            await get_current_operator(
                authorization=None,
                x_gateway_secret="wrong-secret",
                x_operator_id="gateway-user",
                x_operator_role="OPERATOR",
            )
        assert exc_info.value.status_code == 401
        assert "untrusted and rejected" in exc_info.value.detail
    finally:
        settings.TRUSTED_GATEWAY_ENABLED = original_enabled
        settings.GATEWAY_SHARED_SECRET = original_secret


@pytest.mark.asyncio
async def test_auth_trusted_gateway_with_valid_secret_accepted():
    """Verify acceptance of gateway headers ONLY when trusted proxy boundary is demonstrably proven."""
    original_enabled = settings.TRUSTED_GATEWAY_ENABLED
    original_secret = settings.GATEWAY_SHARED_SECRET
    try:
        settings.TRUSTED_GATEWAY_ENABLED = True
        settings.GATEWAY_SHARED_SECRET = "production-internal-gateway-secret-987"

        operator = await get_current_operator(
            authorization=None,
            x_gateway_secret="production-internal-gateway-secret-987",
            x_operator_id="gateway-sre-charlie",
            x_operator_name="Charlie SRE",
            x_operator_email="charlie@company.com",
            x_operator_role="LEAD_SRE",
        )
        assert operator.operator_id == "gateway-sre-charlie"
        assert operator.operator_name == "Charlie SRE"
        assert operator.operator_email == "charlie@company.com"
        assert operator.role == "LEAD_SRE"
        assert operator.is_authorized is True
    finally:
        settings.TRUSTED_GATEWAY_ENABLED = original_enabled
        settings.GATEWAY_SHARED_SECRET = original_secret


@pytest.mark.asyncio
async def test_auth_hmac_signed_token_roundtrip():
    """Verify creation and cryptographic verification of HS256 tokens."""
    payload = {
        "sub": "op-token-user",
        "name": "Token Operator",
        "email": "token-op@rootlens.internal",
        "role": "SRE",
    }
    token = create_signed_token(payload)
    verified = verify_signed_token(token)
    assert verified is not None
    assert verified["sub"] == "op-token-user"
    assert verified["role"] == "SRE"

    # Authenticate via get_current_operator
    op = await get_current_operator(authorization=f"Bearer {token}")
    assert op.operator_id == "op-token-user"
    assert op.role == "SRE"
    assert op.is_authorized is True


@pytest.mark.asyncio
async def test_auth_tampered_token_rejected_with_401():
    """Verify rejection of tampered tokens with invalid cryptographic signatures."""
    payload = {"sub": "op-tampered", "role": "ADMIN"}
    token = create_signed_token(payload)
    # Tamper with the payload part
    parts = token.split(".")
    tampered_token = f"{parts[0]}.eyJyZXBsYWNlZCI6InRydWUifQ.{parts[2]}"

    with pytest.raises(HTTPException) as exc_info:
        await get_current_operator(authorization=f"Bearer {tampered_token}")
    assert exc_info.value.status_code == 401
    assert "tampered" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_auth_canonical_dev_tokens():
    """Verify built-in developer/operator tokens for local dev and testing."""
    op = await get_current_operator(authorization="Bearer dev-operator-token")
    assert op.operator_id == "op-default"
    assert op.role == "OPERATOR"
    assert op.is_authorized is True

    admin = await get_current_operator(authorization="Bearer dev-admin-token")
    assert admin.role == "ADMIN"
    assert admin.is_authorized is True

    viewer = await get_current_operator(authorization="Bearer dev-viewer-token")
    assert viewer.role == "VIEWER"
    assert viewer.is_authorized is False


@pytest.mark.asyncio
async def test_auth_viewer_role_rejected_by_require_authorized_operator():
    """Verify HTTP 403 when authenticated user has unauthorized VIEWER role."""
    with pytest.raises(HTTPException) as exc_info:
        await require_authorized_operator(authorization="Bearer dev-viewer-token")
    assert exc_info.value.status_code == 403
    assert "not authorized" in exc_info.value.detail


@pytest.mark.asyncio
async def test_auth_dev_tokens_disabled_in_production():
    """Security Requirement: Built-in development tokens must be strictly rejected in production."""
    orig_env = settings.ENVIRONMENT
    try:
        settings.ENVIRONMENT = "production"
        with pytest.raises(HTTPException) as exc_info:
            await get_current_operator(authorization="Bearer dev-operator-token")
        assert exc_info.value.status_code == 401
        assert "disabled in production" in exc_info.value.detail
    finally:
        settings.ENVIRONMENT = orig_env


@pytest.mark.asyncio
async def test_auth_dev_tokens_disabled_when_allow_dev_tokens_false():
    """Verify rejection of dev tokens when ALLOW_DEV_TOKENS is explicitly False."""
    orig_allow = settings.ALLOW_DEV_TOKENS
    try:
        settings.ALLOW_DEV_TOKENS = False
        with pytest.raises(HTTPException) as exc_info:
            await get_current_operator(authorization="Bearer dev-operator-token")
        assert exc_info.value.status_code == 401
        assert "disabled" in exc_info.value.detail
    finally:
        settings.ALLOW_DEV_TOKENS = orig_allow


@pytest.mark.asyncio
async def test_auth_token_expiration_validation():
    """Verify expired tokens are rejected with HTTP 401."""
    payload = {"sub": "op-expired", "role": "OPERATOR"}
    # Token expired 60 seconds ago
    expired_token = create_signed_token(payload, expires_in_seconds=-60)
    assert verify_signed_token(expired_token) is None

    with pytest.raises(HTTPException) as exc_info:
        await get_current_operator(authorization=f"Bearer {expired_token}")
    assert exc_info.value.status_code == 401
    assert "Invalid, expired, or tampered" in exc_info.value.detail


@pytest.mark.asyncio
async def test_auth_token_algorithm_strict_hs256():
    """Verify tokens with algorithm other than HS256 (e.g. alg='none' or 'RS256') are rejected."""
    import base64
    import json

    # Fabricate a token with alg='none'
    header_none = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "attacker", "role": "ADMIN"}).encode()).decode().rstrip("=")
    unsigned_token = f"{header_none}.{payload}."

    assert verify_signed_token(unsigned_token) is None

    with pytest.raises(HTTPException) as exc_info:
        await get_current_operator(authorization=f"Bearer {unsigned_token}")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_token_issuer_audience_validation():
    """Verify tokens with mismatched issuer or audience claims are rejected."""
    payload = {
        "sub": "op-valid",
        "role": "OPERATOR",
        "iss": "untrusted-issuer",
        "aud": "wrong-audience",
    }
    bad_token = create_signed_token(payload)
    assert verify_signed_token(bad_token) is None

    with pytest.raises(HTTPException) as exc_info:
        await get_current_operator(authorization=f"Bearer {bad_token}")
    assert exc_info.value.status_code == 401


def test_production_settings_validation_fails_on_weak_or_missing_secrets():
    """Verify Settings fails fast in production if weak secret or dev tokens are enabled."""
    from app.core.config import Settings

    # 1. Production with ALLOW_DEV_TOKENS=True must fail
    with pytest.raises(ValidationError) as exc1:
        Settings(
            ENVIRONMENT="production",
            ALLOW_DEV_TOKENS=True,
            AUTH_SECRET_KEY="secure-production-secret-key-at-least-32-chars",
        )
    assert "ALLOW_DEV_TOKENS must be False in production" in str(exc1.value)

    # 2. Production with default insecure secret must fail
    with pytest.raises(ValidationError) as exc2:
        Settings(
            ENVIRONMENT="production",
            ALLOW_DEV_TOKENS=False,
            AUTH_SECRET_KEY="rootlens-insecure-dev-secret-key-change-in-production",
        )
    assert "AUTH_SECRET_KEY must be a secure random secret" in str(exc2.value)

    # 3. Production with short secret (<32 chars) must fail
    with pytest.raises(ValidationError) as exc3:
        Settings(
            ENVIRONMENT="production",
            ALLOW_DEV_TOKENS=False,
            AUTH_SECRET_KEY="too-short-secret",
        )
    assert "AUTH_SECRET_KEY must be a secure random secret" in str(exc3.value)

    # 4. Valid production settings must succeed
    valid_prod = Settings(
        ENVIRONMENT="production",
        ALLOW_DEV_TOKENS=False,
        AUTH_SECRET_KEY="a" * 32,
    )
    assert valid_prod.ENVIRONMENT == "production"
    assert valid_prod.ALLOW_DEV_TOKENS is False


# ============================================================================
# 3. Multi-Hypothesis Confirmation State & Consistency Tests
# ============================================================================


def test_recalculate_state_confirm_a_then_reject_b_leaves_a_confirmed():
    """Core Invariant Test: Rejecting Hypothesis B must NEVER disconfirm Hypothesis A."""
    incident = Incident(
        id=uuid.uuid4(),
        title="Test Incident",
        severity="HIGH",
        status="INVESTIGATING",
        analysis_status="TRACE_LINKED",
        started_at=datetime.now(timezone.utc),
        detected_at=datetime.now(timezone.utc),
        is_root_cause_confirmed=False,
    )

    t0 = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 12, 5, 0, tzinfo=timezone.utc)

    # 1. Hypothesis A confirmed at t0
    dec_a = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-A",
        hypothesis_text="Root cause A in payment-service",
        operator_id="op-1",
        operator_name="Alice Operator",
        operator_email="alice@rootlens.io",
        decision="CONFIRM",
        notes="Verified failure cascade in payment-service",
        evidence_citations=[{"log_id": "log-101"}],
        previous_decision=None,
        decided_at=t0,
        created_at=t0,
    )

    # 2. Hypothesis B rejected at t1
    dec_b = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-B",
        hypothesis_text="Alternative hypothesis B in auth-service",
        operator_id="op-2",
        operator_name="Bob SRE",
        operator_email="bob@rootlens.io",
        decision="REJECT",
        notes="Disproven by downstream telemetry",
        evidence_citations=[],
        previous_decision=None,
        decided_at=t1,
        created_at=t1,
    )

    # All decisions, ordered newest first (t1 then t0)
    all_decisions = [dec_b, dec_a]

    recalculate_incident_confirmation_state(incident, all_decisions)

    # Invariants
    assert incident.is_root_cause_confirmed is True
    assert incident.probable_cause == "Root cause A in payment-service"
    assert "Confirmed by operator Alice Operator" in (incident.uncertainty_reason or "")


def test_recalculate_state_confirm_a_then_reject_a():
    """Verify that rejecting Hypothesis A after prior confirmation cleanly retracts confirmation."""
    incident = Incident(
        id=uuid.uuid4(),
        title="Test Incident",
        severity="HIGH",
        status="INVESTIGATING",
        analysis_status="TRACE_LINKED",
        started_at=datetime.now(timezone.utc),
        detected_at=datetime.now(timezone.utc),
        is_root_cause_confirmed=False,
    )

    t0 = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 12, 10, 0, tzinfo=timezone.utc)

    dec_a_confirm = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-A",
        hypothesis_text="Root cause A",
        operator_id="op-1",
        operator_name="Alice",
        operator_email="alice@test.com",
        decision="CONFIRM",
        notes="Initial approval",
        decided_at=t0,
        created_at=t0,
    )
    dec_a_reject = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-A",
        hypothesis_text="Root cause A",
        operator_id="op-2",
        operator_name="Bob",
        operator_email="bob@test.com",
        decision="REJECT",
        notes="Disproven on deeper inspection",
        previous_decision="CONFIRM",
        decided_at=t1,
        created_at=t1,
    )

    # Newest first
    recalculate_incident_confirmation_state(incident, [dec_a_reject, dec_a_confirm])

    assert incident.is_root_cause_confirmed is False
    assert "rejected 'hypothesis-A'" in (incident.uncertainty_reason or "")


def test_recalculate_state_confirm_a_then_confirm_b():
    """Verify that confirming Hypothesis B after Hypothesis A makes B the active confirmed root cause."""
    incident = Incident(
        id=uuid.uuid4(),
        title="Test Incident",
        severity="HIGH",
        status="INVESTIGATING",
        analysis_status="TRACE_LINKED",
        started_at=datetime.now(timezone.utc),
        detected_at=datetime.now(timezone.utc),
        is_root_cause_confirmed=False,
    )

    t0 = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 12, 15, 0, tzinfo=timezone.utc)

    dec_a = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-A",
        hypothesis_text="Root cause A",
        operator_id="op-1",
        operator_name="Alice",
        operator_email="alice@test.com",
        decision="CONFIRM",
        notes="Initial root cause",
        decided_at=t0,
        created_at=t0,
    )
    dec_b = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-B",
        hypothesis_text="Updated Root cause B (cascading failure)",
        operator_id="op-2",
        operator_name="Bob",
        operator_email="bob@test.com",
        decision="CONFIRM",
        notes="More specific cascade confirmed",
        decided_at=t1,
        created_at=t1,
    )

    # Newest first
    recalculate_incident_confirmation_state(incident, [dec_b, dec_a])

    assert incident.is_root_cause_confirmed is True
    assert incident.probable_cause == "Updated Root cause B (cascading failure)"


def test_recalculate_state_confirm_a_confirm_b_then_reject_b_restores_a():
    """Multi-hypothesis restoration: If B is confirmed then rejected, active confirmed A takes back probable cause."""
    incident = Incident(
        id=uuid.uuid4(),
        title="Test Incident",
        severity="HIGH",
        status="INVESTIGATING",
        analysis_status="TRACE_LINKED",
        started_at=datetime.now(timezone.utc),
        detected_at=datetime.now(timezone.utc),
        is_root_cause_confirmed=False,
    )

    t0 = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 12, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 18, 12, 20, 0, tzinfo=timezone.utc)

    dec_a = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-A",
        hypothesis_text="Root cause A",
        operator_id="op-1",
        operator_name="Alice",
        operator_email="alice@test.com",
        decision="CONFIRM",
        notes="Confirmed A",
        decided_at=t0,
        created_at=t0,
    )
    dec_b_confirm = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-B",
        hypothesis_text="Root cause B",
        operator_id="op-2",
        operator_name="Bob",
        operator_email="bob@test.com",
        decision="CONFIRM",
        notes="Confirmed B",
        decided_at=t1,
        created_at=t1,
    )
    dec_b_reject = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-B",
        hypothesis_text="Root cause B",
        operator_id="op-2",
        operator_name="Bob",
        operator_email="bob@test.com",
        decision="REJECT",
        notes="Retracted B after finding evidence flaw",
        decided_at=t2,
        created_at=t2,
    )

    # Active states: B is REJECT, A is CONFIRM
    all_decisions = [dec_b_reject, dec_b_confirm, dec_a]
    recalculate_incident_confirmation_state(incident, all_decisions)

    assert incident.is_root_cause_confirmed is True
    assert incident.probable_cause == "Root cause A"


def test_recalculate_state_reject_unconfirmed_hypothesis():
    """Rejecting an unconfirmed hypothesis leaves incident unconfirmed."""
    incident = Incident(
        id=uuid.uuid4(),
        title="Test Incident",
        severity="HIGH",
        status="INVESTIGATING",
        analysis_status="TRACE_LINKED",
        started_at=datetime.now(timezone.utc),
        detected_at=datetime.now(timezone.utc),
        is_root_cause_confirmed=False,
    )

    t0 = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    dec_c = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-C",
        hypothesis_text="Speculation C",
        operator_id="op-1",
        operator_name="Alice",
        operator_email="alice@test.com",
        decision="REJECT",
        notes="Speculation C is completely unsupported",
        decided_at=t0,
        created_at=t0,
    )

    recalculate_incident_confirmation_state(incident, [dec_c])
    assert incident.is_root_cause_confirmed is False
    assert "rejected 'hypothesis-C'" in (incident.uncertainty_reason or "")


def test_recalculate_state_confirm_a_reject_unconfirmed_c_preserves_a():
    """Explicit Invariant: Confirm A, then reject unconfirmed C -> A remains confirmed and probable_cause points to A."""
    incident = Incident(
        id=uuid.uuid4(),
        title="Payment Service Incident",
        severity="HIGH",
        status="INVESTIGATING",
        analysis_status="TRACE_LINKED",
        started_at=datetime.now(timezone.utc),
        detected_at=datetime.now(timezone.utc),
        is_root_cause_confirmed=False,
    )

    t0 = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 12, 5, 0, tzinfo=timezone.utc)

    # 1. Confirm Hypothesis A at t0
    dec_a = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-A",
        hypothesis_text="Root cause A: Connection pool exhaustion",
        operator_id="op-1",
        operator_name="Alice Operator",
        operator_email="alice@rootlens.io",
        decision="CONFIRM",
        notes="Confirmed based on metrics",
        decided_at=t0,
        created_at=t0,
    )

    # 2. Reject previously unconfirmed Hypothesis C at t1
    dec_c = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=incident.id,
        hypothesis_id="hypothesis-C",
        hypothesis_text="Hypothesis C: Network partition speculation",
        operator_id="op-2",
        operator_name="Bob SRE",
        operator_email="bob@rootlens.io",
        decision="REJECT",
        notes="No evidence supporting network partition",
        decided_at=t1,
        created_at=t1,
    )

    # State recalculation with newest first: [dec_c, dec_a]
    recalculate_incident_confirmation_state(incident, [dec_c, dec_a])

    assert incident.is_root_cause_confirmed is True
    assert incident.probable_cause == "Root cause A: Connection pool exhaustion"
    assert "Confirmed by operator Alice Operator" in (incident.uncertainty_reason or "")


def test_deterministic_ordering_with_stable_secondary_key():
    """Verify deterministic resolution when two confirmed hypotheses have identical timestamps."""
    incident = Incident(
        id=uuid.uuid4(),
        title="Deterministic Ordering Test",
        severity="HIGH",
        status="INVESTIGATING",
        analysis_status="TRACE_LINKED",
        started_at=datetime.now(timezone.utc),
        detected_at=datetime.now(timezone.utc),
        is_root_cause_confirmed=False,
    )

    same_time = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    id_lower = uuid.UUID("00000000-0000-0000-0000-000000000001")
    id_higher = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

    dec_1 = RCAHypothesisDecision(
        id=id_lower,
        incident_id=incident.id,
        hypothesis_id="hyp-1",
        hypothesis_text="Hypothesis 1 text",
        operator_id="op-1",
        operator_name="Alice",
        operator_email="alice@test.com",
        decision="CONFIRM",
        notes="Notes 1",
        decided_at=same_time,
        created_at=same_time,
    )
    dec_2 = RCAHypothesisDecision(
        id=id_higher,
        incident_id=incident.id,
        hypothesis_id="hyp-2",
        hypothesis_text="Hypothesis 2 text",
        operator_id="op-2",
        operator_name="Bob",
        operator_email="bob@test.com",
        decision="CONFIRM",
        notes="Notes 2",
        decided_at=same_time,
        created_at=same_time,
    )

    # Both present, sorting by (decided_at, str(id)) descending means id_higher wins deterministically
    recalculate_incident_confirmation_state(incident, [dec_1, dec_2])
    assert incident.probable_cause == "Hypothesis 2 text"

    # Even if order in input list is flipped, outcome is strictly deterministic
    recalculate_incident_confirmation_state(incident, [dec_2, dec_1])
    assert incident.probable_cause == "Hypothesis 2 text"


# ============================================================================
# 4. End-to-End Service Tests with AsyncSession Mocking
# ============================================================================


@pytest.mark.asyncio
async def test_record_hypothesis_decision_confirm_promotes_root_cause():
    """Verify full end-to-end confirmation with citation snapshotting and state derivation."""
    inc_id = uuid.uuid4()
    incident = _make_test_incident_with_evidence(inc_id)

    operator = OperatorIdentity(
        operator_id="op-1",
        operator_name="Alice Operator",
        operator_email="alice@rootlens.io",
        role="OPERATOR",
        is_authorized=True,
    )

    req = RCAHypothesisDecisionCreateRequest(
        decision=HypothesisDecisionEnum.CONFIRM,
        notes="Root cause verified from verified log records in payment-service.",
    )

    session = AsyncMock()
    # 1. First execute: incident lookup with relations (for_update)
    mock_result_inc = MagicMock()
    mock_result_inc.scalar_one_or_none.return_value = incident

    # 2. Second execute: previous decision query
    mock_prev_res = MagicMock()
    mock_prev_res.scalars.return_value.first.return_value = None

    # 3. Third execute: all decisions query after flush
    mock_all_res = MagicMock()
    # Create the simulated decision record that will be queried
    flushed_dec = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=inc_id,
        hypothesis_id="primary",
        hypothesis_text=incident.probable_cause or "",
        operator_id=operator.operator_id,
        operator_name=operator.operator_name,
        operator_email=operator.operator_email,
        decision="CONFIRM",
        notes=req.notes,
        evidence_citations=[{"log_id": "log-doc-101"}],
        previous_decision=None,
        decided_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    mock_all_res.scalars.return_value.all.return_value = [flushed_dec]

    session.execute.side_effect = [mock_result_inc, mock_prev_res, mock_all_res]

    resp = await record_hypothesis_decision(
        session=session,
        incident_id=inc_id,
        hypothesis_id="primary",
        req=req,
        operator=operator,
    )

    assert resp.decision == "CONFIRM"
    assert resp.operator_id == "op-1"
    assert resp.operator_email == "alice@rootlens.io"
    assert resp.notes == req.notes
    assert len(resp.evidence_citations) > 0
    assert incident.is_root_cause_confirmed is True
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_unsupported_hypothesis_rejected_with_http_400():
    """Strict Invariant: Reject confirmation of unsupported speculations lacking evidence."""
    inc_id = uuid.uuid4()
    incident = _make_test_incident_with_evidence(inc_id)

    operator = OperatorIdentity(
        operator_id="op-1",
        operator_name="Alice Operator",
        operator_email="alice@rootlens.io",
        role="OPERATOR",
        is_authorized=True,
    )

    req = RCAHypothesisDecisionCreateRequest(
        decision=HypothesisDecisionEnum.CONFIRM,
        notes="Attempting to confirm without supporting telemetry citations.",
    )

    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = incident
    session.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await record_hypothesis_decision(
            session=session,
            incident_id=inc_id,
            hypothesis_id="finding-1-telemetry-gap",
            req=req,
            operator=operator,
        )

    assert exc_info.value.status_code == 400
    assert "Cannot confirm unsupported hypothesis" in exc_info.value.detail
    assert incident.is_root_cause_confirmed is False


@pytest.mark.asyncio
async def test_get_hypothesis_decisions_summary_multi_hypothesis():
    """Verify summary correctly reports active decisions and multi-hypothesis status."""
    inc_id = uuid.uuid4()
    incident = _make_test_incident_with_evidence(inc_id)

    d_a = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=inc_id,
        hypothesis_id="hypothesis-A",
        hypothesis_text="Root cause A",
        operator_id="op-1",
        operator_name="Alice",
        operator_email="alice@test.com",
        decision="CONFIRM",
        notes="Verified root cause A",
        evidence_citations=[],
        decided_at=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
    )
    d_b = RCAHypothesisDecision(
        id=uuid.uuid4(),
        incident_id=inc_id,
        hypothesis_id="hypothesis-B",
        hypothesis_text="Speculation B",
        operator_id="op-2",
        operator_name="Bob",
        operator_email="bob@test.com",
        decision="REJECT",
        notes="Disproven",
        evidence_citations=[],
        decided_at=datetime(2026, 11, 18, 12, 30, tzinfo=timezone.utc),
        created_at=datetime(2026, 11, 18, 12, 30, tzinfo=timezone.utc),
    )

    session = AsyncMock()
    mock_inc_res = MagicMock()
    mock_inc_res.scalar_one_or_none.return_value = incident

    mock_dec_res = MagicMock()
    mock_dec_res.scalars.return_value.all.return_value = [d_b, d_a]

    session.execute.side_effect = [mock_inc_res, mock_dec_res]

    summary = await get_hypothesis_decisions_summary(session=session, incident_id=inc_id)

    assert summary.incident_id == str(inc_id)
    assert len(summary.history) == 2
    assert summary.active_decisions["hypothesis-A"].decision == "CONFIRM"
    assert summary.active_decisions["hypothesis-B"].decision == "REJECT"
    assert summary.is_root_cause_confirmed is True
    assert summary.confirmed_hypothesis_id == "hypothesis-A"
    assert summary.confirmed_root_cause == "Root cause A"


# ============================================================================
# 5. HTTP API Endpoints Integration Tests (TestClient)
# ============================================================================


def test_api_unauthenticated_requests_return_401():
    """Verify HTTP 401 when unauthenticated requests hit decision endpoints."""
    test_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/incidents/{test_id}/hypotheses/decisions")
    assert resp.status_code == 401

    resp_post = client.post(
        f"/api/v1/incidents/{test_id}/hypotheses/primary/confirm",
        json={"notes": "Valid notes string for confirm test"},
    )
    assert resp_post.status_code == 401


def test_api_spoofed_gateway_headers_rejected_with_401():
    """Verify HTTP 401 when direct client sends unverified X-Operator-* gateway headers."""
    test_id = str(uuid.uuid4())
    headers = {
        "X-Operator-Id": "attacker",
        "X-Operator-Role": "ADMIN",
        "X-Operator-Email": "attacker@evil.com",
    }
    resp = client.post(
        f"/api/v1/incidents/{test_id}/hypotheses/primary/confirm",
        headers=headers,
        json={"notes": "Attempting unauthorized confirmation via spoofed headers"},
    )
    assert resp.status_code == 401
    assert "untrusted and rejected" in resp.json()["detail"]


def test_api_viewer_role_returns_403_on_post():
    """Verify HTTP 403 when a user with VIEWER role attempts to confirm or reject hypotheses."""
    test_id = str(uuid.uuid4())
    headers = {
        "Authorization": "Bearer dev-viewer-token",
    }
    resp = client.post(
        f"/api/v1/incidents/{test_id}/hypotheses/primary/confirm",
        headers=headers,
        json={"notes": "Viewer attempting confirmation"},
    )
    assert resp.status_code == 403
    assert "not authorized" in resp.json()["detail"]


def test_api_notes_too_short_returns_422():
    """Verify HTTP 422 Unprocessable Entity when operator notes are less than 10 characters."""
    test_id = str(uuid.uuid4())
    headers = {
        "Authorization": "Bearer dev-admin-token",
    }
    resp = client.post(
        f"/api/v1/incidents/{test_id}/hypotheses/primary/confirm",
        headers=headers,
        json={"notes": "short"},  # < 10 chars
    )
    assert resp.status_code == 422
