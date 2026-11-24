"""Tests for Phase 10: Root Cause Analysis (RCA) Validation Subsystem.

Validates:
1. Ground-truth benchmark integrity (12 required scenarios, non-invention, explicit logs).
2. End-to-end RCA evaluation harness execution.
3. Root cause identification (Top-1, Top-3, Culprit Service accuracy).
4. Evidence retrieval (Precision@K, Recall@K, MRR).
5. Grounding and citation validity (Citation validity rate, Unsupported claim rate).
6. Safety & uncertainty invariants:
   - Abstention rate on insufficient telemetry (must be 100%).
   - False confirmation rate (must be 0.0%).
   - False causal attribution rate (must be 0.0%).
7. Serialization of evaluation results and Markdown reporting.
"""

import json
import os
from pathlib import Path
import pytest

from app.evaluation.rca_evaluator import (
    AggregateEvaluationReport,
    MetricCount,
    RCAEvaluator,
    ScenarioEvaluationResult,
)


BENCHMARK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "benchmarks", "rca", "rca_ground_truth_benchmark.json"
)
RESULTS_JSON_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "benchmarks", "rca", "rca_evaluation_results.json"
)
REPORT_MD_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "docs", "rca_evaluation_report.md"
)


@pytest.fixture(scope="module")
def benchmark_data():
    """Load benchmark ground truth fixture."""
    resolved_path = os.path.abspath(BENCHMARK_PATH)
    assert os.path.exists(resolved_path), f"Benchmark file missing at {resolved_path}"
    with open(resolved_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


@pytest.fixture(scope="module")
def evaluator():
    """Shared RCAEvaluator instance."""
    return RCAEvaluator(cascade_window_seconds=120)


@pytest.fixture(scope="module")
def evaluation_report(evaluator):
    """Run full evaluation once for aggregate assertions."""
    resolved_path = os.path.abspath(BENCHMARK_PATH)
    return evaluator.run_full_evaluation(
        benchmark_file_path=resolved_path,
        output_json_path=os.path.abspath(RESULTS_JSON_PATH),
        output_md_path=os.path.abspath(REPORT_MD_PATH),
    )


# ==============================================================================
# 1. Ground Truth Benchmark Integrity Tests
# ==============================================================================

def test_benchmark_fixture_structure(benchmark_data):
    """Verify ground truth benchmark schema, metadata, and scenario completeness."""
    assert "metadata" in benchmark_data
    assert "scenarios" in benchmark_data

    metadata = benchmark_data["metadata"]
    assert metadata["dataset_type"] == "SYNTHETIC_EVALUATION_FIXTURE"
    assert metadata["version"] == "1.0.0"
    assert "disclaimer" in metadata

    scenarios = benchmark_data["scenarios"]
    assert len(scenarios) >= 12, f"Expected at least 12 scenarios, found {len(scenarios)}"

    expected_ids = {f"RCA-SCN-{i:02d}" for i in range(1, 13)}
    actual_ids = {s["scenario_id"] for s in scenarios}
    assert expected_ids.issubset(actual_ids), f"Missing scenario IDs: {expected_ids - actual_ids}"


def test_scenario_required_fields(benchmark_data):
    """Verify each scenario has all required independent ground-truth fields."""
    for s in benchmark_data["scenarios"]:
        scn_id = s["scenario_id"]
        assert "name" in s, f"{scn_id} missing 'name'"
        assert "description" in s, f"{scn_id} missing 'description'"
        assert "expected_classification" in s, f"{scn_id} missing 'expected_classification'"
        assert "input_logs" in s, f"{scn_id} missing 'input_logs'"
        assert isinstance(s["input_logs"], list), f"{scn_id} input_logs must be a list"
        assert len(s["input_logs"]) > 0, f"{scn_id} input_logs must not be empty"

        # Check log item structure
        for log in s["input_logs"]:
            assert "log_id" in log, f"{scn_id} log missing 'log_id'"
            assert "timestamp" in log, f"{scn_id} log missing 'timestamp'"
            assert "service_name" in log, f"{scn_id} log missing 'service_name'"
            assert "message" in log, f"{scn_id} log missing 'message'"


# ==============================================================================
# 2. Per-Scenario Evaluation Tests
# ==============================================================================

def test_scenario_01_sufficient_evidence_root_cause(evaluator, benchmark_data):
    """Scenario 1: Correct root cause identified with sufficient evidence (DB pool exhaustion)."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-01")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.top3_correct is True
    assert result.culprit_service_correct is True
    assert result.actual_culprit_service == "postgres-db"
    assert result.actual_classification == "TRACE_LINKED"
    # Prior routine info log reduces top-2 chronological precision to 0.5 while recall remains 1.0
    assert result.precision_at_k == 0.5
    assert result.recall_at_k == 1.0
    assert result.mrr == 0.5
    assert result.actual_is_root_cause_confirmed is False  # Safety invariant: unreviewed


def test_scenario_02_multiple_supporting_logs(evaluator, benchmark_data):
    """Scenario 2: Root cause supported by multiple corroborating logs (Auth JWKS key refresh failure)."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-02")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.culprit_service_correct is True
    assert result.actual_culprit_service == "auth-service"
    assert result.actual_classification == "TRACE_LINKED"
    assert len(result.retrieved_evidence_ids) >= 2
    assert result.recall_at_k == 1.0


def test_scenario_03_competing_hypotheses(evaluator, benchmark_data):
    """Scenario 3: Competing hypotheses ranked correctly (Redis distributed lock deadlock)."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-03")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.top3_correct is True
    assert result.culprit_service_correct is True
    assert result.actual_culprit_service == "inventory-service"
    assert result.actual_classification == "TRACE_LINKED"


def test_scenario_04_unrelated_logs_isolation(evaluator, benchmark_data):
    """Scenario 4: Unrelated background telemetry does not pollute root cause attribution."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-04")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.culprit_service_correct is True
    assert result.actual_culprit_service == "billing-service"
    assert result.actual_culprit_service != "cron-worker"
    assert result.actual_culprit_service != "metrics-collector"
    assert result.recall_at_k == 1.0


def test_scenario_05_missing_trace_context(evaluator, benchmark_data):
    """Scenario 5: Correlation succeeds gracefully despite missing distributed trace context."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-05")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.culprit_service_correct is True
    assert result.actual_culprit_service == "checkout-service"
    assert result.actual_classification == "TEMPORAL_ONLY"


def test_scenario_06_sparse_telemetry_uncertainty(evaluator, benchmark_data):
    """Scenario 6: Incomplete telemetry flagged with appropriate uncertainty."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-06")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.culprit_service_correct is True
    assert result.actual_culprit_service == "unassigned"
    assert result.actual_classification == "TEMPORAL_ONLY"
    assert result.actual_is_root_cause_confirmed is False


def test_scenario_07_insufficient_evidence_abstention(evaluator, benchmark_data):
    """Scenario 7: Pipeline strictly abstains when telemetry is insufficient (< 2 evidence items)."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-07")
    result = evaluator.evaluate_scenario(scenario)

    assert result.actual_abstention is True
    assert result.actual_classification == "INSUFFICIENT"
    # Root cause identification is not applicable for abstention scenarios (no root cause claimed)
    assert result.top1_correct is None
    assert result.top3_correct is None
    assert result.culprit_service_correct is None
    assert result.correct_abstention is True
    assert result.actual_culprit_service is None
    assert result.actual_is_root_cause_confirmed is False


def test_scenario_08_fabricated_evidence_rejection(evaluator, benchmark_data):
    """Scenario 8: Fabricated or incorrect evidence references rejected in citation validation."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-08")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.culprit_service_correct is True
    # The fabricated citation log-fake-9999 must NOT be counted as a valid citation
    assert result.citation_validity_rate == 1.0  # Only genuine evidence citations resolve
    # The claim with fabricated citation must be flagged unsupported
    assert result.unsupported_factual_claims > 0


def test_scenario_09_cross_incident_evidence_rejection(evaluator, benchmark_data):
    """Scenario 9: Evidence from another incident rejected by provenance check."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-09")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.culprit_service_correct is True
    assert result.unsupported_factual_claims > 0


def test_scenario_10_wrong_service_evidence_rejection(evaluator, benchmark_data):
    """Scenario 10: Evidence attributed to mismatched service rejected by citation validation."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-10")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.culprit_service_correct is True
    assert result.actual_culprit_service == "redis-cache"
    assert result.unsupported_factual_claims > 0


def test_scenario_11_disjoint_traces_isolation(evaluator, benchmark_data):
    """Scenario 11: Disjoint traces split into separate incident candidates cleanly."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-11")
    result = evaluator.evaluate_scenario(scenario)

    assert result.top1_correct is True
    assert result.culprit_service_correct is True
    assert result.actual_culprit_service == "search-indexer"
    assert result.actual_classification == "TRACE_LINKED"


def test_scenario_12_temporal_only_correlation_safety(evaluator, benchmark_data):
    """Scenario 12: Pure temporal correlation does NOT establish confirmed causality."""
    scenario = next(s for s in benchmark_data["scenarios"] if s["scenario_id"] == "RCA-SCN-12")
    result = evaluator.evaluate_scenario(scenario)

    assert result.actual_classification == "TEMPORAL_ONLY"
    assert result.actual_culprit_service == "recommender-engine"
    assert result.false_causal_attribution is False
    assert result.actual_is_root_cause_confirmed is False


# ==============================================================================
# 3. Aggregate Metrics & Safety Invariants
# ==============================================================================

def test_aggregate_accuracy_targets(evaluation_report: AggregateEvaluationReport):
    """Verify aggregate accuracy over classifiable scenarios meets or exceeds validation targets."""
    assert evaluation_report.scenarios_evaluated == 12

    # Identification accuracy targets over classifiable incidents (11 classifiable scenarios)
    assert evaluation_report.top1_accuracy.sample_count == 11
    assert evaluation_report.top1_accuracy.rate is not None
    assert evaluation_report.top1_accuracy.rate == 1.0, f"Top-1 accuracy {evaluation_report.top1_accuracy.rate} != 1.0"

    assert evaluation_report.top3_accuracy.sample_count == 11
    assert evaluation_report.top3_accuracy.rate is not None
    assert evaluation_report.top3_accuracy.rate == 1.0, f"Top-3 accuracy {evaluation_report.top3_accuracy.rate} != 1.0"

    assert evaluation_report.culprit_service_accuracy.sample_count == 11
    assert evaluation_report.culprit_service_accuracy.rate is not None
    assert evaluation_report.culprit_service_accuracy.rate == 1.0, f"Culprit accuracy {evaluation_report.culprit_service_accuracy.rate} != 1.0"


def test_aggregate_retrieval_targets(evaluation_report: AggregateEvaluationReport):
    """Verify evidence retrieval precision, recall, and MRR meet quality thresholds."""
    assert evaluation_report.precision_at_k.rate is not None
    assert evaluation_report.precision_at_k.rate >= 0.70, f"Precision@K {evaluation_report.precision_at_k.rate} < 0.70"

    assert evaluation_report.recall_at_k.rate is not None
    assert evaluation_report.recall_at_k.rate >= 0.80, f"Recall@K {evaluation_report.recall_at_k.rate} < 0.80"

    assert evaluation_report.mean_reciprocal_rank.rate is not None
    assert evaluation_report.mean_reciprocal_rank.rate >= 0.70, f"MRR {evaluation_report.mean_reciprocal_rank.rate} < 0.70"


def test_aggregate_grounding_targets(evaluation_report: AggregateEvaluationReport):
    """Verify citation validity and factual claim grounding."""
    # 100% of validated citations must map to genuine retrieved incident evidence
    assert evaluation_report.citation_validity_rate.rate == 1.0

    # Unsupported claim rate should be transparently computed and finite
    assert evaluation_report.unsupported_claim_rate.rate is not None
    assert 0.0 <= evaluation_report.unsupported_claim_rate.rate <= 1.0


def test_aggregate_safety_and_uncertainty_invariants(evaluation_report: AggregateEvaluationReport):
    """Verify non-negotiable safety and uncertainty invariants."""
    # 1. 100% abstention on insufficient telemetry
    assert evaluation_report.insufficient_evidence_abstention_rate.rate == 1.0, (
        f"Abstention rate must be 1.0, got {evaluation_report.insufficient_evidence_abstention_rate.rate}"
    )

    # 2. 0% incorrect abstention on classifiable incidents (false negative abstention)
    assert evaluation_report.incorrect_abstention_rate.rate == 0.0, (
        f"Incorrect abstention rate must be 0.0, got {evaluation_report.incorrect_abstention_rate.rate}"
    )

    # 3. 0% false root cause attribution on insufficient telemetry (false positive attribution)
    assert evaluation_report.false_root_cause_attribution_rate.rate == 0.0, (
        f"False attribution rate must be 0.0, got {evaluation_report.false_root_cause_attribution_rate.rate}"
    )

    # 4. 0% false root cause confirmation (unreviewed hypotheses NEVER confirmed)
    assert evaluation_report.false_confirmation_rate.rate == 0.0, (
        f"False confirmation rate must be 0.0, got {evaluation_report.false_confirmation_rate.rate}"
    )

    # 5. 0% false causal attribution on temporal-only correlation
    assert evaluation_report.false_causal_attribution_rate.rate == 0.0, (
        f"False causal attribution rate must be 0.0, got {evaluation_report.false_causal_attribution_rate.rate}"
    )


def test_metric_counts_have_explicit_denominators(evaluation_report: AggregateEvaluationReport):
    """Verify that every metric includes transparent numerator, denominator, and sample count."""
    metrics_dict = evaluation_report.to_dict()["metrics"]
    for name, metric in metrics_dict.items():
        assert "numerator" in metric, f"Metric '{name}' missing numerator"
        assert "denominator" in metric, f"Metric '{name}' missing denominator"
        assert "sample_count" in metric, f"Metric '{name}' missing sample_count"
        assert "rate" in metric, f"Metric '{name}' missing rate"
        assert metric["sample_count"] > 0, f"Metric '{name}' sample_count must be > 0"
        assert metric["denominator"] > 0, f"Metric '{name}' denominator must be > 0"


def test_evaluation_output_artifacts_exist():
    """Verify that evaluation generates machine-readable JSON and Markdown report."""
    assert os.path.exists(RESULTS_JSON_PATH), f"Missing {RESULTS_JSON_PATH}"
    assert os.path.exists(REPORT_MD_PATH), f"Missing {REPORT_MD_PATH}"

    # Verify JSON content is valid
    with open(RESULTS_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "metrics" in data
    assert "scenarios" in data
    assert len(data["scenarios"]) == 12

    # Verify Markdown report content
    with open(REPORT_MD_PATH, "r", encoding="utf-8") as f:
        md_text = f.read()
    assert "# RootLens RCA Ground Truth Benchmark — Evaluation Report" in md_text
    assert "## 1. Measured Metric Summary" in md_text
    assert "## 2. Per-Scenario Evaluation Breakdown" in md_text
    assert "## 3. Dataset Integrity & Limitations Disclosure" in md_text
    assert "LogBERT F1: 0.6802" in md_text
