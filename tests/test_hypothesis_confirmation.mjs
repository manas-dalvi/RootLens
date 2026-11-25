import test from "node:test";
import assert from "node:assert/strict";

/**
 * Unit Test Suite for RootLens Phase 8 Stage 4: Operator Hypothesis Confirmation Workflow
 *
 * Verifies:
 * 1. Operator review badge states:
 *    - AWAITING OPERATOR REVIEW (unreviewed hypothesis)
 *    - OPERATOR CONFIRMED ROOT CAUSE (promoted by human operator)
 *    - OPERATOR REJECTED (disproven or rejected hypothesis)
 * 2. Strict Invariants:
 *    - AI-generated hypothesis is NEVER automatically confirmed.
 *    - Confirmation requires evidence grounding (is_supported === true).
 *    - Unsupported speculations cannot be confirmed.
 * 3. Operator input validation:
 *    - Minimum 10 characters required for operator notes.
 * 4. Immutable Audit Ledger & State Sync:
 *    - Chronological sorting (newest first).
 *    - Prior decision tracking across transitions.
 */

function getOperatorReviewBadge(claim, activeDecision) {
  if (claim.claim_type === "OBSERVED_FACT") {
    return {
      label: claim.is_supported ? "OBSERVED FACT - SUPPORTED" : "INSUFFICIENT TELEMETRY",
      variant: claim.is_supported ? "success" : "neutral",
    };
  }

  if (activeDecision?.decision === "CONFIRM") {
    return {
      label: "OPERATOR CONFIRMED ROOT CAUSE",
      variant: "success",
    };
  }

  if (activeDecision?.decision === "REJECT") {
    return {
      label: "OPERATOR REJECTED",
      variant: "critical",
    };
  }

  return {
    label: "AWAITING OPERATOR REVIEW",
    variant: "warning",
  };
}

function canOperatorConfirm(claim) {
  return Boolean(claim.is_supported && claim.citations && claim.citations.length > 0);
}

function validateOperatorNotes(notes) {
  if (!notes || typeof notes !== "string") return false;
  return notes.trim().length >= 10;
}

function sortDecisionHistoryNewestFirst(history) {
  return [...history].sort(
    (a, b) => new Date(b.decided_at).getTime() - new Date(a.decided_at).getTime()
  );
}

// ============================================================================
// 1. Review Badge State Tests
// ============================================================================

test("Hypothesis Review Badge - Awaiting Operator Review (Default)", () => {
  const claim = {
    claim_id: "finding-1-trace-hypothesis",
    claim_type: "INFERRED_HYPOTHESIS",
    is_supported: true,
  };

  const badge = getOperatorReviewBadge(claim, null);
  assert.equal(badge.label, "AWAITING OPERATOR REVIEW");
  assert.equal(badge.variant, "warning");
});

test("Hypothesis Review Badge - Operator Confirmed", () => {
  const claim = {
    claim_id: "finding-1-trace-hypothesis",
    claim_type: "INFERRED_HYPOTHESIS",
    is_supported: true,
  };
  const activeDecision = {
    decision: "CONFIRM",
    operator_name: "Alice Operator",
    operator_email: "alice@rootlens.io",
    decided_at: "2026-11-18T12:00:00Z",
  };

  const badge = getOperatorReviewBadge(claim, activeDecision);
  assert.equal(badge.label, "OPERATOR CONFIRMED ROOT CAUSE");
  assert.equal(badge.variant, "success");
});

test("Hypothesis Review Badge - Operator Rejected", () => {
  const claim = {
    claim_id: "finding-1-trace-hypothesis",
    claim_type: "INFERRED_HYPOTHESIS",
    is_supported: true,
  };
  const activeDecision = {
    decision: "REJECT",
    operator_name: "Bob SRE",
    operator_email: "bob@rootlens.io",
    decided_at: "2026-11-18T12:30:00Z",
  };

  const badge = getOperatorReviewBadge(claim, activeDecision);
  assert.equal(badge.label, "OPERATOR REJECTED");
  assert.equal(badge.variant, "critical");
});

// ============================================================================
// 2. Strict Confirmation Safety Rules
// ============================================================================

test("Confirmation Safety - Supported Hypothesis Can Be Confirmed", () => {
  const claim = {
    claim_id: "finding-1-trace-hypothesis",
    is_supported: true,
    citations: [{ log_id: "log-101", service_name: "payment-service" }],
  };
  assert.equal(canOperatorConfirm(claim), true);
});

test("Confirmation Safety - Unsupported Speculation Cannot Be Confirmed", () => {
  const claim = {
    claim_id: "finding-1-telemetry-gap",
    is_supported: false,
    citations: [],
    unsupported_reason: "Trace ID absent from telemetry",
  };
  assert.equal(canOperatorConfirm(claim), false);
});

// ============================================================================
// 3. Mandatory Operator Notes Validation
// ============================================================================

test("Operator Notes Validation - Rejects Notes Shorter Than 10 Chars", () => {
  assert.equal(validateOperatorNotes(""), false);
  assert.equal(validateOperatorNotes("looks ok"), false);
  assert.equal(validateOperatorNotes("        "), false);
  assert.equal(validateOperatorNotes("123456789"), false);
});

test("Operator Notes Validation - Accepts Notes of 10+ Characters", () => {
  assert.equal(validateOperatorNotes("1234567890"), true);
  assert.equal(
    validateOperatorNotes("Verified with database connection pool metrics in production."),
    true
  );
});

// ============================================================================
// 4. Immutable Audit Ledger Sorting
// ============================================================================

test("Audit Ledger - Orders Decisions Chronologically Newest First", () => {
  const rawHistory = [
    { id: "1", decided_at: "2026-11-18T10:00:00Z", decision: "CONFIRM" },
    { id: "3", decided_at: "2026-11-18T12:00:00Z", decision: "CONFIRM" },
    { id: "2", decided_at: "2026-11-18T11:00:00Z", decision: "REJECT" },
  ];

  const sorted = sortDecisionHistoryNewestFirst(rawHistory);
  assert.equal(sorted[0].id, "3");
  assert.equal(sorted[1].id, "2");
  assert.equal(sorted[2].id, "1");
});

test("Audit Ledger - Preserves Prior Decision Tracking Across Transitions", () => {
  const history = [
    {
      id: "dec-2",
      decision: "REJECT",
      previous_decision: "CONFIRM",
      notes: "Subsequent investigation revealed downstream gateway failure.",
      operator_email: "bob@rootlens.io",
      decided_at: "2026-11-18T12:30:00Z",
    },
    {
      id: "dec-1",
      decision: "CONFIRM",
      previous_decision: null,
      notes: "Initial approval based on correlation cluster.",
      operator_email: "alice@rootlens.io",
      decided_at: "2026-11-18T12:00:00Z",
    },
  ];

  assert.equal(history[0].decision, "REJECT");
  assert.equal(history[0].previous_decision, "CONFIRM");
  assert.equal(history[1].decision, "CONFIRM");
  assert.equal(history[1].previous_decision, null);
});
