import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BENCHMARK_PATH = path.resolve(__dirname, "../data/benchmarks/rca/rca_ground_truth_benchmark.json");
const RESULTS_PATH = path.resolve(__dirname, "../data/benchmarks/rca/rca_evaluation_results.json");
const REPORT_MD_PATH = path.resolve(__dirname, "../docs/rca_evaluation_report.md");

test("Benchmark Ground Truth Fixture Contract", () => {
  assert.ok(fs.existsSync(BENCHMARK_PATH), "Benchmark JSON fixture file must exist");
  const raw = fs.readFileSync(BENCHMARK_PATH, "utf-8");
  const data = JSON.parse(raw);

  assert.ok(data.metadata, "Metadata object must be present");
  assert.equal(data.metadata.benchmark_name, "RootLens RCA Ground Truth Benchmark");
  assert.equal(data.metadata.version, "1.0.0");
  assert.equal(data.metadata.dataset_type, "SYNTHETIC_EVALUATION_FIXTURE");
  assert.ok(data.metadata.disclaimer, "Disclaimer/limitations disclosure must be present");
  assert.ok(Array.isArray(data.scenarios), "Scenarios must be an array");
  assert.ok(data.scenarios.length >= 12, "Must contain at least 12 scenarios");

  const expectedIds = Array.from({ length: 12 }, (_, i) => `RCA-SCN-${String(i + 1).padStart(2, "0")}`);
  const scenarioMap = new Map();

  for (const s of data.scenarios) {
    assert.ok(s.scenario_id, "Each scenario must have a scenario_id");
    assert.ok(s.name, `${s.scenario_id} must have a name`);
    assert.ok(s.description, `${s.scenario_id} must have a description`);
    assert.ok(s.expected_classification, `${s.scenario_id} must have expected_classification`);
    assert.ok(Array.isArray(s.input_logs), `${s.scenario_id} must have input_logs array`);
    assert.ok(s.input_logs.length > 0, `${s.scenario_id} input_logs must not be empty`);
    assert.equal(typeof s.expected_should_abstain, "boolean", `${s.scenario_id} expected_should_abstain must be boolean`);
    assert.equal(s.expected_is_root_cause_confirmed, false, `${s.scenario_id} expected_is_root_cause_confirmed must be false`);

    for (const log of s.input_logs) {
      assert.ok(log.log_id, "Log must have log_id");
      assert.ok(log.timestamp, "Log must have timestamp");
      assert.ok(log.message, "Log must have message");
      assert.ok(typeof log.service_name === "string", "Log service_name must be a string");
    }

    scenarioMap.set(s.scenario_id, s);
  }

  for (const expId of expectedIds) {
    assert.ok(scenarioMap.has(expId), `Scenario ${expId} must be present in benchmark`);
  }
});

test("Machine-Readable Evaluation Results Contract", () => {
  assert.ok(fs.existsSync(RESULTS_PATH), "Results JSON file must exist");
  const raw = fs.readFileSync(RESULTS_PATH, "utf-8");
  const results = JSON.parse(raw);

  assert.equal(results.benchmark_name, "RootLens RCA Ground Truth Benchmark");
  assert.equal(results.dataset_type, "SYNTHETIC_EVALUATION_FIXTURE");
  assert.equal(results.total_scenarios, 12);
  assert.equal(results.scenarios_evaluated, 12);
  assert.ok(results.metrics, "Metrics object must be present");

  const requiredMetrics = [
    "top1_accuracy",
    "top3_accuracy",
    "culprit_service_accuracy",
    "precision_at_k",
    "recall_at_k",
    "mean_reciprocal_rank",
    "citation_validity_rate",
    "unsupported_claim_rate",
    "insufficient_evidence_abstention_rate",
    "incorrect_abstention_rate",
    "false_root_cause_attribution_rate",
    "false_confirmation_rate",
    "false_causal_attribution_rate",
  ];

  for (const mName of requiredMetrics) {
    const metric = results.metrics[mName];
    assert.ok(metric, `Metric '${mName}' must exist`);
    assert.ok(typeof metric.numerator === "number", `'${mName}' numerator must be number`);
    assert.ok(typeof metric.denominator === "number", `'${mName}' denominator must be number`);
    assert.ok(typeof metric.sample_count === "number", `'${mName}' sample_count must be number`);
    assert.ok(metric.sample_count > 0, `'${mName}' sample_count must be > 0`);
    assert.ok(metric.denominator > 0, `'${mName}' denominator must be > 0`);

    if (metric.rate !== null) {
      const expectedRate = Math.round((metric.numerator / metric.denominator) * 10000) / 10000;
      assert.ok(
        Math.abs(metric.rate - expectedRate) < 0.001,
        `'${mName}' rate (${metric.rate}) must match numerator/denominator calculation (${expectedRate})`
      );
    }
  }

  // Safety & Uncertainty Invariants
  assert.equal(results.metrics.false_confirmation_rate.rate, 0.0, "False confirmation rate must strictly be 0.0");
  assert.equal(results.metrics.false_causal_attribution_rate.rate, 0.0, "False causal attribution rate must strictly be 0.0");
  assert.equal(results.metrics.insufficient_evidence_abstention_rate.rate, 1.0, "Abstention rate must strictly be 1.0 (100%)");
  assert.equal(results.metrics.incorrect_abstention_rate.rate, 0.0, "Incorrect abstention rate must strictly be 0.0");
  assert.equal(results.metrics.false_root_cause_attribution_rate.rate, 0.0, "False root cause attribution rate must strictly be 0.0");
  assert.equal(results.metrics.citation_validity_rate.rate, 1.0, "Citation validity rate must strictly be 1.0 (100% genuine evidence)");

  // Identification Accuracy Targets (over 11 classifiable scenarios)
  assert.equal(results.metrics.top1_accuracy.sample_count, 11, "Top-1 accuracy sample count must be 11");
  assert.ok(results.metrics.top1_accuracy.rate >= 0.80, "Top-1 accuracy must be >= 80%");
  assert.ok(results.metrics.top3_accuracy.rate >= 0.90, "Top-3 accuracy must be >= 90%");
  assert.ok(results.metrics.culprit_service_accuracy.rate >= 0.80, "Culprit service accuracy must be >= 80%");

  // Retrieval Quality Targets
  assert.ok(results.metrics.precision_at_k.rate >= 0.70, "Precision@K must be >= 70%");
  assert.ok(results.metrics.recall_at_k.rate >= 0.80, "Recall@K must be >= 80%");
  assert.ok(results.metrics.mean_reciprocal_rank.rate >= 0.70, "MRR must be >= 70%");

  // Scenarios Array validation
  assert.ok(Array.isArray(results.scenarios), "Scenarios list must be an array");
  assert.equal(results.scenarios.length, 12, "Must contain all 12 scenario evaluation results");

  for (const scn of results.scenarios) {
    assert.ok(scn.scenario_id, "Scenario must have scenario_id");
    assert.ok(Array.isArray(scn.pipeline_stages_exercised), "Scenario must track exercised pipeline stages");
    assert.ok(scn.pipeline_stages_exercised.length > 0, "Exercised pipeline stages must not be empty");
    assert.equal(scn.actual_is_root_cause_confirmed, false, "Hypothesis confirmation must strictly remain false");
    assert.ok(typeof scn.top1_correct === "boolean" || scn.top1_correct === null);
    assert.ok(typeof scn.culprit_service_correct === "boolean" || scn.culprit_service_correct === null);
  }
});

test("Evaluation Markdown Report Structure Contract", () => {
  assert.ok(fs.existsSync(REPORT_MD_PATH), "Report Markdown file must exist");
  const report = fs.readFileSync(REPORT_MD_PATH, "utf-8");

  assert.ok(report.includes("# RootLens RCA Ground Truth Benchmark — Evaluation Report"), "Report title must match");
  assert.ok(report.includes("## 1. Measured Metric Summary"), "Must have section 1 Metric Summary");
  assert.ok(report.includes("## 2. Per-Scenario Evaluation Breakdown"), "Must have section 2 Per-Scenario Breakdown");
  assert.ok(report.includes("## 3. Dataset Integrity & Limitations Disclosure"), "Must have section 3 Limitations Disclosure");

  // Verify all 12 scenario IDs appear in the breakdown table
  for (let i = 1; i <= 12; i++) {
    const scnId = `RCA-SCN-${String(i).padStart(2, "0")}`;
    assert.ok(report.includes(scnId), `Report must include scenario ${scnId} in breakdown table`);
  }

  // Verify limitations disclosure warnings
  assert.ok(report.includes("Synthetic Fixture"), "Report must disclose synthetic fixture nature");
  assert.ok(report.includes("LogBERT F1: 0.6802"), "Report must cite LogBERT HDFS distinction");
  assert.ok(report.includes("Non-Invention Guarantee"), "Report must mention non-invention guarantee");
});
