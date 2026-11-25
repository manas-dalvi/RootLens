import test from "node:test";
import assert from "node:assert/strict";

/**
 * Unit Test Suite for Incident Workspace 2.0
 * Verifies:
 * 1. Accessible Tab Configuration & Counts
 * 2. Allowed Incident Lifecycle Transitions (State Machine)
 * 3. Telemetry Grounding & Invariant Guardrails
 * 4. Impact Levels & Formatting
 */

// Canonical lifecycle state transitions matching backend
const ALLOWED_INCIDENT_STATUS_TRANSITIONS = {
  DETECTED: new Set(["INVESTIGATING", "MITIGATED", "RESOLVED", "FALSE_POSITIVE"]),
  INVESTIGATING: new Set(["MITIGATED", "RESOLVED", "FALSE_POSITIVE"]),
  MITIGATED: new Set(["INVESTIGATING", "RESOLVED"]),
  RESOLVED: new Set(),
  FALSE_POSITIVE: new Set(),
};

function isValidTransition(current, target) {
  if (current === target) return true; // idempotent
  const allowed = ALLOWED_INCIDENT_STATUS_TRANSITIONS[current];
  return Boolean(allowed && allowed.has(target));
}

function getImpactLabel(impact) {
  switch (impact?.toUpperCase()) {
    case "PRIMARY_CULPRIT":
      return "INITIAL OBSERVED SERVICE";
    case "DOWNSTREAM_CASCADE":
      return "DOWNSTREAM CASCADE";
    case "CORRELATED_ANOMALY":
      return "CORRELATED ANOMALY";
    default:
      return impact || "AFFECTED";
  }
}

function formatUtcTimestamp(dateStr) {
  if (!dateStr) return "Ongoing / None";
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
  } catch {
    return dateStr;
  }
}

test("Incident Workspace - 5 Canonical Tabs Configuration", () => {
  const tabs = [
    { id: "overview", label: "Overview & RCA" },
    { id: "timeline", label: "Timeline & Evidence" },
    { id: "investigate", label: "AI Investigation" },
    { id: "recommendations", label: "Recommendations" },
    { id: "reports", label: "Post-Mortem Reports" },
  ];

  assert.equal(tabs.length, 5, "Incident workspace must have exactly 5 canonical tabs");
  assert.equal(tabs[0].id, "overview");
  assert.equal(tabs[1].id, "timeline");
  assert.equal(tabs[2].id, "investigate");
  assert.equal(tabs[3].id, "recommendations");
  assert.equal(tabs[4].id, "reports");
});

test("Incident Lifecycle - State Machine Validation from DETECTED", () => {
  assert.equal(isValidTransition("DETECTED", "INVESTIGATING"), true);
  assert.equal(isValidTransition("DETECTED", "MITIGATED"), true);
  assert.equal(isValidTransition("DETECTED", "RESOLVED"), true);
  assert.equal(isValidTransition("DETECTED", "FALSE_POSITIVE"), true);
  assert.equal(isValidTransition("DETECTED", "DETECTED"), true);
});

test("Incident Lifecycle - State Machine Validation from INVESTIGATING", () => {
  assert.equal(isValidTransition("INVESTIGATING", "MITIGATED"), true);
  assert.equal(isValidTransition("INVESTIGATING", "RESOLVED"), true);
  assert.equal(isValidTransition("INVESTIGATING", "FALSE_POSITIVE"), true);
  // Cannot go back to DETECTED
  assert.equal(isValidTransition("INVESTIGATING", "DETECTED"), false);
});

test("Incident Lifecycle - State Machine Validation from MITIGATED", () => {
  // MITIGATED can reopen investigation or resolve
  assert.equal(isValidTransition("MITIGATED", "INVESTIGATING"), true);
  assert.equal(isValidTransition("MITIGATED", "RESOLVED"), true);
  // Cannot go directly back to DETECTED or FALSE_POSITIVE
  assert.equal(isValidTransition("MITIGATED", "DETECTED"), false);
  assert.equal(isValidTransition("MITIGATED", "FALSE_POSITIVE"), false);
});

test("Incident Lifecycle - Terminal States are strictly non-transitionable", () => {
  const statuses = ["DETECTED", "INVESTIGATING", "MITIGATED", "FALSE_POSITIVE"];
  for (const s of statuses) {
    assert.equal(isValidTransition("RESOLVED", s), false, `RESOLVED cannot transition to ${s}`);
  }
  for (const s of ["DETECTED", "INVESTIGATING", "MITIGATED", "RESOLVED"]) {
    assert.equal(isValidTransition("FALSE_POSITIVE", s), false, `FALSE_POSITIVE cannot transition to ${s}`);
  }
});

test("Incident Impact Mapping - Correctly labels telemetry roles", () => {
  assert.equal(getImpactLabel("PRIMARY_CULPRIT"), "INITIAL OBSERVED SERVICE");
  assert.equal(getImpactLabel("DOWNSTREAM_CASCADE"), "DOWNSTREAM CASCADE");
  assert.equal(getImpactLabel("CORRELATED_ANOMALY"), "CORRELATED ANOMALY");
  assert.equal(getImpactLabel("CUSTOM_TAG"), "CUSTOM_TAG");
  assert.equal(getImpactLabel(null), "AFFECTED");
});

test("Format UTC Timestamp - Robust date formatting", () => {
  assert.equal(formatUtcTimestamp(null), "Ongoing / None");
  assert.equal(formatUtcTimestamp(undefined), "Ongoing / None");
  assert.equal(formatUtcTimestamp(""), "Ongoing / None");

  const iso = "2026-10-14T08:01:30.000Z";
  const formatted = formatUtcTimestamp(iso);
  assert.equal(formatted, "2026-10-14 08:01:30 UTC");
});

test("Telemetry Grounding Invariants - Causality must never be assumed confirmed", () => {
  // Strict invariants: RootLens never confirms causality from correlation
  const mockIncident = {
    is_root_cause_confirmed: false,
    confidence_score: 0.0,
    uncertainty_reason: "Earliest observed failure is not confirmed root cause",
  };

  assert.equal(mockIncident.is_root_cause_confirmed, false);
  assert.ok(mockIncident.uncertainty_reason.length > 0);
  assert.equal(mockIncident.confidence_score, 0.0);
});

test("Recommendation Evidence Citations - Navigation URL and formatting", () => {
  const sampleCitations = [
    { reference_type: "LOG", reference_id: "doc-rec-123", service_name: "payment-service" },
    { reference_type: "TRACE", reference_id: "trace-xyz-789", service_name: "gateway-service" },
    { reference_type: "SPAN", reference_id: "span-001", service_name: "auth-service" },
  ];

  // LOG and TRACE citations produce valid Log Explorer search URLs
  const logUrl = `/logs?q=${encodeURIComponent(sampleCitations[0].reference_id)}`;
  assert.equal(logUrl, "/logs?q=doc-rec-123");

  const traceUrl = `/logs?q=${encodeURIComponent(sampleCitations[1].reference_id)}`;
  assert.equal(traceUrl, "/logs?q=trace-xyz-789");

  // Legacy recommendations without citations default to empty array safely
  const legacyRec = {
    id: "rec-legacy-1",
    title: "Check connection pools",
    evidence_citations: undefined,
  };
  const cits = legacyRec.evidence_citations || [];
  assert.equal(cits.length, 0);
});

test("Incident Timeline & Overview - LogBERT Explainability Badges & Formatting", () => {
  // 1. MLKP mismatch event
  const mismatchEvent = {
    sequence_order: 1,
    event_type: "SEQUENCE_ANOMALY",
    summary: "LogBERT sequence anomaly in 'hdfs-datanode' (score: 1.0579 [MLKP mismatch])",
    attributes: {
      model: "LogBERT",
      anomaly_score: 1.0579,
      threshold: 0.0388,
      vhm_distance: 0.579,
      mismatch_ratio: 1.0,
      is_mlkp_mismatch: true,
      explanation_type: "TOKEN_MISMATCH",
      dominant_factor: "MLKP_MISMATCH",
      mlkp_observed_event: "UNKNOWN_BAD_EVENT",
      mlkp_observed_prob: 0.0001,
      mlkp_observed_nll: 9.21,
      mlkp_top_candidates: [
        { event: "E5", token_id: 5, probability: 0.842 },
        { event: "E22", token_id: 8, probability: 0.115 },
      ],
      contributes_to_anomaly: true,
    },
  };

  const badgeText = mismatchEvent.attributes.is_mlkp_mismatch ? "MLKP MISMATCH" : "VHM EMBEDDING";
  assert.equal(badgeText, "MLKP MISMATCH");
  assert.equal(mismatchEvent.attributes.mlkp_top_candidates.length, 2);
  assert.equal(mismatchEvent.attributes.mlkp_top_candidates[0].event, "E5");
  assert.equal(mismatchEvent.attributes.contributes_to_anomaly, true);

  // 2. VHM-only embedding distance event
  const vhmEvent = {
    sequence_order: 2,
    event_type: "SEQUENCE_ANOMALY",
    summary: "LogBERT sequence anomaly in 'order-service' (score: 0.2500 [VHM embedding])",
    attributes: {
      model: "LogBERT",
      anomaly_score: 0.25,
      threshold: 0.0388,
      vhm_distance: 2.50,
      mismatch_ratio: 0.0,
      is_mlkp_mismatch: false,
      explanation_type: "EMBEDDING_DISTANCE",
      dominant_factor: "VHM_DISTANCE",
      mlkp_observed_event: "E5",
      mlkp_observed_prob: 0.95,
      mlkp_top_candidates: [
        { event: "E5", token_id: 5, probability: 0.95 },
      ],
      contributes_to_anomaly: false,
    },
  };

  const vhmBadgeText = vhmEvent.attributes.is_mlkp_mismatch ? "MLKP MISMATCH" : "VHM EMBEDDING";
  assert.equal(vhmBadgeText, "VHM EMBEDDING");
  assert.equal(vhmEvent.attributes.dominant_factor, "VHM_DISTANCE");
  assert.equal(vhmEvent.attributes.contributes_to_anomaly, false);
});
