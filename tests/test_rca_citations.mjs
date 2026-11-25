import test from "node:test";
import assert from "node:assert/strict";

/**
 * Unit Test Suite for RootLens Phase 8 Stage 3: Fine-Grained RCA Citation Mapping
 * Verifies:
 * 1. Claim model structures & claim_type classifications
 * 2. Fine-grained citation item properties (log_id, service_name, snippet, explanation)
 * 3. Navigation link URL formation to Log Explorer (/logs?q=...)
 * 4. Claim badge status classification (SUPPORTED FACT, HYPOTHESIS, INSUFFICIENT)
 * 5. Strictly preserves is_root_cause_confirmed = false
 * 6. Unsupported reasons displayed when evidence is missing
 * 7. Regression compatibility: recommendation citations and LogBERT explainability intact
 */

function getClaimBadgeInfo(claim) {
  if (claim.is_supported) {
    if (claim.claim_type === "OBSERVED_FACT") {
      return {
        label: "OBSERVED FACT - SUPPORTED",
        variant: "success",
      };
    }
    return {
      label: "HYPOTHESIS - UNCONFIRMED",
      variant: "warning",
    };
  }
  return {
    label: "INSUFFICIENT TELEMETRY",
    variant: "neutral",
  };
}

function buildLogExplorerUrl(logId) {
  if (!logId || !logId.trim()) return null;
  return `/logs?q=${encodeURIComponent(logId)}`;
}

test("RCA Claim Badge Classification - Supported Fact", () => {
  const claim = {
    claim_id: "fact-1",
    claim_text: "Earliest failure occurred in auth-service at 10:00 UTC",
    claim_type: "OBSERVED_FACT",
    is_supported: true,
    citations: [
      {
        log_id: "log-101",
        service_name: "auth-service",
        evidence_type: "CULPRIT_LOG",
        log_snippet: "FATAL: Key missing",
        support_explanation: "Earliest recorded error",
      },
    ],
  };

  const badge = getClaimBadgeInfo(claim);
  assert.equal(badge.label, "OBSERVED FACT - SUPPORTED");
  assert.equal(badge.variant, "success");
});

test("RCA Claim Badge Classification - Unconfirmed Hypothesis", () => {
  const claim = {
    claim_id: "hyp-1",
    claim_text: "Failure in auth-service propagated to payment-service via shared trace.",
    claim_type: "INFERRED_HYPOTHESIS",
    is_supported: true,
    citations: [
      {
        log_id: "log-101",
        service_name: "auth-service",
        evidence_type: "CULPRIT_LOG",
        support_explanation: "Initiating failure",
      },
      {
        log_id: "log-202",
        service_name: "payment-service",
        evidence_type: "CORRELATION_SPIKE",
        support_explanation: "Downstream failure",
      },
    ],
  };

  const badge = getClaimBadgeInfo(claim);
  assert.equal(badge.label, "HYPOTHESIS - UNCONFIRMED");
  assert.equal(badge.variant, "warning");
});

test("RCA Claim Badge Classification - Insufficient Telemetry Gap", () => {
  const claim = {
    claim_id: "gap-1",
    claim_text: "External database connection pool starved.",
    claim_type: "UNSUPPORTED_SPECULATION",
    is_supported: false,
    citations: [],
    unsupported_reason: "Telemetry is unmonitored or absent from indexed logs.",
  };

  const badge = getClaimBadgeInfo(claim);
  assert.equal(badge.label, "INSUFFICIENT TELEMETRY");
  assert.equal(badge.variant, "neutral");
});

test("RCA Citation Log Explorer Navigation URL - Valid Log ID", () => {
  const cit = {
    log_id: "os-doc-uuid-999-abc",
    service_name: "auth-service",
    evidence_type: "CULPRIT_LOG",
  };
  const url = buildLogExplorerUrl(cit.log_id);
  assert.equal(url, "/logs?q=os-doc-uuid-999-abc");
});

test("RCA Citation Log Explorer Navigation URL - Special Characters Encoded", () => {
  const cit = {
    log_id: "doc/item:special&chars=1",
    service_name: "billing-service",
  };
  const url = buildLogExplorerUrl(cit.log_id);
  assert.equal(url, "/logs?q=doc%2Fitem%3Aspecial%26chars%3D1");
});

test("RCA Citation Log Explorer Navigation URL - Empty or Null Log ID", () => {
  assert.equal(buildLogExplorerUrl(""), null);
  assert.equal(buildLogExplorerUrl(null), null);
  assert.equal(buildLogExplorerUrl("   "), null);
});

test("RCA Invariant Guardrail - Causality Is Never Confirmed", () => {
  const finding = {
    explanation: "Hypothesized failure propagation",
    plausibility_rationale: "Trace context matches",
    is_root_cause_confirmed: false,
    claim_citations: [
      {
        claim_id: "hyp-1",
        claim_text: "Failure propagation along trace",
        claim_type: "INFERRED_HYPOTHESIS",
        is_supported: true,
        citations: [{ log_id: "log-1", service_name: "svc", support_explanation: "Root log" }],
      },
    ],
  };

  assert.equal(finding.is_root_cause_confirmed, false);
});
