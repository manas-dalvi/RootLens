"""
Automated tests for LogBERT test split evaluation and benchmark comparison (Phase 5).
Verifies evaluation data integrity, split disjointness, artifact consistency,
and deterministic CPU evaluation.
"""

import json
from pathlib import Path
import pytest

from app.ml.logbert.inference import LogBERTInferenceEngine

PROCESSED_DIR = Path("data/benchmarks/hdfs/processed")
MODEL_PATH = Path("data/benchmarks/hdfs/model/logbert_best.pt")
VOCAB_PATH = Path("data/benchmarks/hdfs/processed/vocab.json")
EVAL_REPORT_PATH = Path("data/benchmarks/hdfs/evaluation/evaluation_report.json")


class TestEvaluationDataIntegrity:
    """Verify evaluation dataset integrity, strict split partitioning, and class distribution."""

    def test_test_split_exists_and_counts(self):
        assert (PROCESSED_DIR / "test_sessions.json").exists()
        with open(PROCESSED_DIR / "test_sessions.json") as f:
            test_sessions = json.load(f)
        assert len(test_sessions) == 226
        normal_count = sum(1 for s in test_sessions if s["label"] == 0)
        anomaly_count = sum(1 for s in test_sessions if s["label"] == 1)
        assert normal_count == 192
        assert anomaly_count == 34

    def test_strict_split_disjointness(self):
        with open(PROCESSED_DIR / "train_sessions.json") as f:
            train = json.load(f)
        with open(PROCESSED_DIR / "val_sessions.json") as f:
            val = json.load(f)
        with open(PROCESSED_DIR / "test_sessions.json") as f:
            test = json.load(f)

        train_blocks = set(s["block_id"] for s in train)
        val_blocks = set(s["block_id"] for s in val)
        test_blocks = set(s["block_id"] for s in test)

        # Zero block overlap
        assert len(train_blocks & test_blocks) == 0, "DATA LEAKAGE: Train and Test overlap!"
        assert len(val_blocks & test_blocks) == 0, "DATA LEAKAGE: Val and Test overlap!"
        assert len(train_blocks & val_blocks) == 0, "DATA LEAKAGE: Train and Val overlap!"


class TestEvaluationReportArtifact:
    """Verify saved evaluation report artifact contents and numerical integrity."""

    def test_evaluation_report_exists_and_valid(self):
        assert EVAL_REPORT_PATH.exists()
        with open(EVAL_REPORT_PATH) as f:
            data = json.load(f)

        assert data["test_session_count"] == 226
        assert data["test_normal_count"] == 192
        assert data["test_anomaly_count"] == 34

        metrics = data["comparative_metrics"]
        assert "method_a_rule_based_error" in metrics
        assert "method_b_statistical_frequency" in metrics
        assert "method_c_logbert" in metrics

        # Method C (LogBERT) checks
        logbert_metrics = metrics["method_c_logbert"]
        assert logbert_metrics["threshold"] == pytest.approx(0.0388, abs=1e-4)
        assert logbert_metrics["tp"] == 17
        assert logbert_metrics["fp"] == 101
        assert logbert_metrics["tn"] == 91
        assert logbert_metrics["fn"] == 17
        assert logbert_metrics["recall"] == pytest.approx(0.5000, abs=1e-4)
        assert logbert_metrics["precision"] == pytest.approx(0.1441, abs=1e-4)
        assert logbert_metrics["f1"] == pytest.approx(0.2237, abs=1e-4)

        # Latency statistics check
        latency = data["latency_stats_cpu_ms"]
        assert latency["mean_ms"] > 0
        assert latency["median_ms"] > 0
        assert latency["p95_ms"] >= latency["median_ms"]

        # OOV analysis check
        oov = data["vocabulary_oov_analysis"]
        assert oov["vocab_size"] == 19
        assert oov["total_test_tokens"] == 226
        assert oov["oov_tokens"] == 0
        assert oov["oov_rate"] == 0.0


class TestDeterministicEvaluation:
    """Verify reproducible, deterministic inference across multiple passes."""

    def test_deterministic_scoring_on_test_subset(self):
        engine = LogBERTInferenceEngine(
            checkpoint_path=MODEL_PATH,
            vocab_path=VOCAB_PATH,
            device="cpu",
        )

        with open(PROCESSED_DIR / "test_sessions.json") as f:
            test_sessions = json.load(f)

        sample = test_sessions[:10]

        scores_pass_1 = [engine.predict_sequence(s["events"], session_id=s["block_id"]).composite_score for s in sample]
        scores_pass_2 = [engine.predict_sequence(s["events"], session_id=s["block_id"]).composite_score for s in sample]

        for s1, s2 in zip(scores_pass_1, scores_pass_2):
            assert s1 == pytest.approx(s2, abs=1e-6)
