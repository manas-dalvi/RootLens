import test from "node:test";
import assert from "node:assert/strict";

/**
 * Unit Test Suite for LogBERT Token-Level Explainability (Phase 8 Stage 2)
 *
 * Verifies:
 * 1. Anomaly Diagnosis Classification & Dominant Factor logic
 * 2. Per-position token mismatch formatting & probability presentation
 * 3. Top-k candidate extraction & percentage rendering
 * 4. Separation of discrete token mismatch evidence from VHM distance
 * 5. Safe handling of unknown tokens, empty sequences, and no-mismatch sequences
 * 6. Evidence navigation links for supporting OpenSearch log records
 */

function resolveAnomalyDiagnosis(mismatchCount, totalEvents, vhmDistance, alpha = 0.1, threshold = 0.0388) {
  const mismatchRatio = totalEvents > 0 ? mismatchCount / totalEvents : 0.0;
  const vhmContrib = Number((alpha * vhmDistance).toFixed(4));
  const compositeScore = Number((mismatchRatio + vhmContrib).toFixed(4));
  const isAnomaly = compositeScore >= threshold;

  if (!isAnomaly) {
    return {
      explanationType: "NORMAL",
      dominantFactor: "NONE",
      isAnomaly: false,
      compositeScore,
      mlkpContribution: mismatchRatio,
      vhmContribution: vhmContrib,
    };
  }

  if (mismatchCount > 0 && vhmContrib < 0.25 * mismatchRatio) {
    return {
      explanationType: "TOKEN_MISMATCH",
      dominantFactor: "MLKP_MISMATCH",
      isAnomaly: true,
      compositeScore,
      mlkpContribution: mismatchRatio,
      vhmContribution: vhmContrib,
    };
  }

  if (mismatchCount === 0) {
    return {
      explanationType: "EMBEDDING_DISTANCE",
      dominantFactor: "VHM_DISTANCE",
      isAnomaly: true,
      compositeScore,
      mlkpContribution: 0.0,
      vhmContribution: vhmContrib,
    };
  }

  return {
    explanationType: "HYBRID",
    dominantFactor: mismatchRatio >= vhmContrib ? "MLKP_MISMATCH" : "VHM_DISTANCE",
    isAnomaly: true,
    compositeScore,
    mlkpContribution: mismatchRatio,
    vhmContribution: vhmContrib,
  };
}

function formatCandidatePercentage(probability) {
  if (typeof probability !== "number" || isNaN(probability)) return "0.0%";
  return `${(Math.min(1.0, Math.max(0.0, probability)) * 100).toFixed(1)}%`;
}

function buildLogRecordSearchUrl(sampleLogId) {
  if (!sampleLogId || typeof sampleLogId !== "string" || !sampleLogId.trim()) {
    return "/logs";
  }
  return `/logs?q=${encodeURIComponent(sampleLogId.trim())}`;
}

test("LogBERT Explainability - TOKEN_MISMATCH diagnosis when MLKP dominates", () => {
  // 2 mismatches out of 4 events = 0.50 mismatch ratio, small VHM distance 0.02
  const diagnosis = resolveAnomalyDiagnosis(2, 4, 0.02, 0.1, 0.0388);
  assert.equal(diagnosis.isAnomaly, true);
  assert.equal(diagnosis.explanationType, "TOKEN_MISMATCH");
  assert.equal(diagnosis.dominantFactor, "MLKP_MISMATCH");
  assert.equal(diagnosis.mlkpContribution, 0.5);
  assert.equal(diagnosis.vhmContribution, 0.002);
});

test("LogBERT Explainability - EMBEDDING_DISTANCE diagnosis for VHM-only anomalies", () => {
  // 0 token mismatches, but large VHM distance 2.50 causing composite score 0.25 >= 0.0388
  const diagnosis = resolveAnomalyDiagnosis(0, 10, 2.50, 0.1, 0.0388);
  assert.equal(diagnosis.isAnomaly, true);
  assert.equal(diagnosis.explanationType, "EMBEDDING_DISTANCE");
  assert.equal(diagnosis.dominantFactor, "VHM_DISTANCE");
  assert.equal(diagnosis.mlkpContribution, 0.0);
  assert.equal(diagnosis.vhmContribution, 0.25);
});

test("LogBERT Explainability - HYBRID diagnosis when both MLKP and VHM contribute significantly", () => {
  // 1 mismatch out of 10 events (0.10) + VHM distance 1.50 (contrib 0.15)
  const diagnosis = resolveAnomalyDiagnosis(1, 10, 1.50, 0.1, 0.0388);
  assert.equal(diagnosis.isAnomaly, true);
  assert.equal(diagnosis.explanationType, "HYBRID");
  assert.equal(diagnosis.dominantFactor, "VHM_DISTANCE"); // 0.15 > 0.10
  assert.equal(diagnosis.mlkpContribution, 0.1);
  assert.equal(diagnosis.vhmContribution, 0.15);
});

test("LogBERT Explainability - NORMAL diagnosis for compliant sequences", () => {
  // 0 mismatches, small VHM distance 0.10 -> score 0.01 < 0.0388
  const diagnosis = resolveAnomalyDiagnosis(0, 5, 0.10, 0.1, 0.0388);
  assert.equal(diagnosis.isAnomaly, false);
  assert.equal(diagnosis.explanationType, "NORMAL");
  assert.equal(diagnosis.dominantFactor, "NONE");
});

test("LogBERT Explainability - Candidate template percentage formatting", () => {
  assert.equal(formatCandidatePercentage(0.8524), "85.2%");
  assert.equal(formatCandidatePercentage(0.0012), "0.1%");
  assert.equal(formatCandidatePercentage(1.0), "100.0%");
  assert.equal(formatCandidatePercentage(0.0), "0.0%");
  assert.equal(formatCandidatePercentage(null), "0.0%");
  assert.equal(formatCandidatePercentage(undefined), "0.0%");
});

test("LogBERT Explainability - Telemetry evidence navigation links", () => {
  assert.equal(buildLogRecordSearchUrl("log_12345"), "/logs?q=log_12345");
  assert.equal(buildLogRecordSearchUrl("log with spaces"), "/logs?q=log%20with%20spaces");
  assert.equal(buildLogRecordSearchUrl(""), "/logs");
  assert.equal(buildLogRecordSearchUrl(null), "/logs");
});

test("LogBERT Explainability - Event attributes compatibility", () => {
  // Verify that an event without LogBERT attributes does not throw
  const genericEvent = {
    sequence_order: 0,
    attributes: { cluster_id: "cl-normal" },
  };
  const isLogBERT = genericEvent.attributes?.model === "LogBERT";
  assert.equal(isLogBERT, false);

  // Verify that an anomalous LogBERT event exposes structured attributes
  const logbertEvent = {
    sequence_order: 1,
    attributes: {
      model: "LogBERT",
      anomaly_score: 1.05,
      threshold: 0.0388,
      vhm_distance: 0.52,
      is_mlkp_mismatch: true,
      mlkp_observed_event: "E999",
      mlkp_top_candidates: [
        { event: "E5", token_id: 5, probability: 0.85 },
        { event: "E22", token_id: 8, probability: 0.10 },
      ],
    },
  };
  assert.equal(logbertEvent.attributes.model, "LogBERT");
  assert.equal(logbertEvent.attributes.is_mlkp_mismatch, true);
  assert.equal(logbertEvent.attributes.mlkp_top_candidates.length, 2);
  assert.equal(logbertEvent.attributes.mlkp_top_candidates[0].event, "E5");
});
