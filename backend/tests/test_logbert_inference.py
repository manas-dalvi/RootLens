"""
Unit tests for LogBERT inference service, incident pipeline integration,
and API endpoints (Phase 4).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from starlette.testclient import TestClient
import torch

from app.core.config import settings
from app.main import app
from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.inference import LogBERTInferenceEngine, LogBERTInferenceResult
from app.services.clustering_service import LogCluster, LogEventItem
from app.services.correlation_service import correlate_clusters
from app.services.logbert_service import (
    detect_sequence_anomalies_as_clusters,
    extract_event_key,
    extract_session_id,
    get_logbert_engine,
    group_logs_into_sessions,
    reset_logbert_engine,
)

MODEL_PATH = Path("data/benchmarks/hdfs/model/logbert_best.pt")
VOCAB_PATH = Path("data/benchmarks/hdfs/processed/vocab.json")


@pytest.fixture(scope="module")
def inference_engine():
    """Module-scoped LogBERT inference engine instance."""
    assert MODEL_PATH.exists(), f"Model checkpoint not found at {MODEL_PATH}"
    assert VOCAB_PATH.exists(), f"Vocabulary not found at {VOCAB_PATH}"
    return LogBERTInferenceEngine(
        checkpoint_path=MODEL_PATH,
        vocab_path=VOCAB_PATH,
        device="cpu",
    )


class TestCheckpointLoading:
    """Verify safe checkpoint loading, configuration restoration, and parameter freezing."""

    def test_safe_checkpoint_loading(self, inference_engine):
        assert inference_engine is not None
        assert inference_engine.model is not None
        assert inference_engine.config.vocab_size == 19
        assert inference_engine.config.hidden_size == 128
        assert inference_engine.config.num_hidden_layers == 4
        assert inference_engine.config.num_attention_heads == 4
        assert inference_engine.threshold == pytest.approx(0.0388, abs=1e-4)

    def test_parameters_require_no_grad(self, inference_engine):
        for name, param in inference_engine.model.named_parameters():
            assert not param.requires_grad, f"Parameter '{name}' should have requires_grad=False in eval mode!"

    def test_center_restoration_and_norm(self, inference_engine):
        center = inference_engine.center
        assert center is not None
        assert center.shape == (128,)
        norm = torch.norm(center).item()
        assert norm == pytest.approx(11.2098, rel=1e-2)

    def test_missing_files_raise_filenotfound(self, tmp_path):
        bad_checkpoint = tmp_path / "nonexistent.pt"
        with pytest.raises(FileNotFoundError, match="LogBERT checkpoint file not found"):
            LogBERTInferenceEngine(
                checkpoint_path=bad_checkpoint,
                vocab_path=VOCAB_PATH,
                device="cpu",
            )


class TestInferenceEngine:
    """Verify inference scoring, template encoding, edge cases, and determinism."""

    def test_predict_single_known_sequence(self, inference_engine):
        result = inference_engine.predict_sequence(
            events=["E5", "E5"],
            session_id="session_known_001",
        )
        assert isinstance(result, LogBERTInferenceResult)
        assert result.session_id == "session_known_001"
        assert result.num_events == 2
        assert result.threshold == pytest.approx(0.0388, abs=1e-4)
        assert result.composite_score > 0.0
        assert isinstance(result.is_anomaly, bool)

    def test_unknown_event_templates_handled_safely(self, inference_engine):
        result = inference_engine.predict_sequence(
            events=["UNKNOWN_A", "E999", "RANDOM_EVENT"],
            session_id="session_unk_002",
        )
        assert result.session_id == "session_unk_002"
        assert result.num_events == 3
        # Unknown tokens should trigger mismatches
        assert result.mismatch_count >= 1
        assert result.is_anomaly is True

    def test_empty_sequence_handling(self, inference_engine):
        result = inference_engine.predict_sequence(
            events=[],
            session_id="session_empty_003",
        )
        assert result.session_id == "session_empty_003"
        assert result.num_events == 0
        assert result.composite_score == 0.0
        assert result.is_anomaly is False
        assert result.mismatch_count == 0

    def test_deterministic_cpu_evaluation(self, inference_engine):
        seq = ["E11", "E8"]
        res1 = inference_engine.predict_sequence(seq, session_id="det_01")
        res2 = inference_engine.predict_sequence(seq, session_id="det_02")
        assert res1.composite_score == pytest.approx(res2.composite_score, abs=1e-6)
        assert res1.vhm_distance == pytest.approx(res2.vhm_distance, abs=1e-6)
        assert res1.mismatch_count == res2.mismatch_count

    def test_threshold_application(self, inference_engine):
        seq = ["UNKNOWN_TOKEN"]
        # Normal inference with default threshold ~0.0388
        res_default = inference_engine.predict_sequence(seq)
        score = res_default.composite_score

        # Engine with an artificially high threshold
        engine_high = LogBERTInferenceEngine(
            checkpoint_path=MODEL_PATH,
            vocab_path=VOCAB_PATH,
            device="cpu",
            custom_threshold=score + 10.0,
        )
        res_high = engine_high.predict_sequence(seq)
        assert res_high.is_anomaly is False

        # Engine with an artificially low threshold
        engine_low = LogBERTInferenceEngine(
            checkpoint_path=MODEL_PATH,
            vocab_path=VOCAB_PATH,
            device="cpu",
            custom_threshold=score - 0.1,
        )
        res_low = engine_low.predict_sequence(seq)
        assert res_low.is_anomaly is True

    def test_batch_prediction(self, inference_engine):
        sequences = [
            ["E11"],
            ["UNKNOWN_X", "UNKNOWN_Y"],
            [],
        ]
        session_ids = ["s1", "s2", "s3"]
        batch_results = inference_engine.predict_batch(sequences, session_ids=session_ids)
        assert len(batch_results) == 3
        assert batch_results[0].session_id == "s1"
        assert batch_results[1].session_id == "s2"
        assert batch_results[2].session_id == "s3"
        assert batch_results[2].num_events == 0

    def test_model_info_safety(self, inference_engine):
        info = inference_engine.get_model_info()
        assert info["model_name"] == "LogBERT"
        assert info["version"] == "1.0.0"
        assert info["device"] == "cpu"
        assert info["total_parameters"] == 823187
        assert info["threshold"] == pytest.approx(0.0388, abs=1e-4)
        # Verify no file paths are exposed in model info
        for key in info:
            assert "path" not in key.lower()


class TestIncidentPipelineIntegration:
    """Verify integration of sequence anomalies into incident candidate evaluation."""

    def test_sequence_anomaly_clustering_from_info_logs(self, inference_engine):
        """Sequence anomaly should be detected even when all logs are INFO (no explicit ERROR)."""
        now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
        logs = [
            LogEventItem(
                log_id="log_info_01",
                timestamp=now,
                service_name="hdfs-datanode",
                log_level="INFO",
                message="Received block blk_-12345 src: /10.0.0.1:50010 dest: /10.0.0.2:50010 E999",
                attributes={"block_id": "blk_-12345", "template": "E999"},
                raw_log="2026-11-18 INFO Received block blk_-12345 E999",
            ),
            LogEventItem(
                log_id="log_info_02",
                timestamp=now + timedelta(seconds=1),
                service_name="hdfs-datanode",
                log_level="INFO",
                message="Unexpected packet transmission for block blk_-12345 E888",
                attributes={"block_id": "blk_-12345", "template": "E888"},
                raw_log="2026-11-18 INFO Unexpected packet transmission for block blk_-12345 E888",
            ),
        ]

        clusters = detect_sequence_anomalies_as_clusters(
            logs, window_minutes=15, engine=inference_engine
        )
        assert len(clusters) == 1
        cl = clusters[0]
        assert "cl-logbert-" in cl.cluster_id
        assert cl.service_name == "hdfs-datanode"
        assert cl.count == 2
        assert "LogBERT Sequence Anomaly" in cl.pattern
        assert cl.events[0].attributes["model"] == "LogBERT"
        assert cl.events[0].attributes["anomaly_score"] >= cl.events[0].attributes["threshold"]

    def test_correlate_sequence_anomaly_into_incident_candidate(self, inference_engine):
        """Anomalous sequence cluster should assemble cleanly into CorrelatedIncidentCandidate."""
        now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
        logs = [
            LogEventItem(
                log_id="log_anom_101",
                timestamp=now,
                service_name="order-service",
                log_level="INFO",
                message="Order processing transition initiated blk_99999 E999",
                trace_id="trace-abc-123",
                attributes={"block_id": "blk_99999", "template": "E999"},
                raw_log="raw log content 101",
            ),
            LogEventItem(
                log_id="log_anom_102",
                timestamp=now + timedelta(seconds=2),
                service_name="order-service",
                log_level="INFO",
                message="Abnormal state transition detected blk_99999 E888",
                trace_id="trace-abc-123",
                attributes={"block_id": "blk_99999", "template": "E888"},
                raw_log="raw log content 102",
            ),
        ]

        clusters = detect_sequence_anomalies_as_clusters(logs, window_minutes=15, engine=inference_engine)
        assert len(clusters) == 1

        candidates = correlate_clusters(clusters)
        assert len(candidates) == 1
        cand = candidates[0]

        # Invariant checks:
        assert cand.is_root_cause_confirmed is False
        assert "SEQUENCE_ANOMALY" in cand.correlation_rules_used

        # Timeline event check
        assert len(cand.timeline_events) == 2
        for tl_ev in cand.timeline_events:
            assert tl_ev.event_type == "SEQUENCE_ANOMALY"
            assert "LogBERT sequence anomaly" in tl_ev.summary

        # Evidence preservation check
        assert len(cand.evidence_items) >= 1
        ev_item = cand.evidence_items[0]
        assert ev_item.evidence_type == "SEQUENCE_ANOMALY"
        assert ev_item.correlation_rule == "SEQUENCE_ANOMALY"
        assert ev_item.log_id in ("log_anom_101", "log_anom_102")
        assert ev_item.trace_id == "trace-abc-123"

    def test_multi_service_sequence_anomaly_and_error_correlation(self, inference_engine):
        """LogBERT sequence anomaly in upstream service correlates with downstream ERROR cluster via trace_id."""
        now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
        trace_id = "trace-cross-service-777"

        # Upstream service: subtle INFO sequence anomaly
        upstream_logs = [
            LogEventItem(
                log_id="up_log_01",
                timestamp=now,
                service_name="gateway-service",
                log_level="INFO",
                message="Client request dispatched blk_777 E999",
                trace_id=trace_id,
                attributes={"block_id": "blk_777", "template": "E999"},
                raw_log="gateway raw log",
            ),
        ]
        upstream_clusters = detect_sequence_anomalies_as_clusters(
            upstream_logs, window_minutes=15, engine=inference_engine
        )

        # Downstream service: explicit ERROR cluster
        downstream_event = LogEventItem(
            log_id="down_log_01",
            timestamp=now + timedelta(seconds=3),
            service_name="payment-service",
            log_level="ERROR",
            message="Database connection pool exhausted during transaction",
            trace_id=trace_id,
            raw_log="payment raw log error",
        )
        downstream_cluster = LogCluster(
            cluster_id="cl-payment-db-timeout",
            service_name="payment-service",
            pattern="Database connection pool exhausted during transaction",
            pattern_hash="hash1234",
            severity="ERROR",
            window_start=now,
            window_end=now + timedelta(minutes=15),
            count=1,
            log_ids=["down_log_01"],
            sample_messages=["Database connection pool exhausted during transaction"],
            first_seen=downstream_event.timestamp,
            last_seen=downstream_event.timestamp,
            trace_ids=[trace_id],
            span_ids=[],
            events=[downstream_event],
        )

        combined_clusters = upstream_clusters + [downstream_cluster]
        candidates = correlate_clusters(combined_clusters)

        assert len(candidates) == 1
        cand = candidates[0]
        # Should link both services via TRACE_LINKED
        assert "TRACE_LINKED" in cand.correlation_rules_used
        assert len(cand.affected_services) == 2
        svc_names = {s.service_name for s in cand.affected_services}
        assert svc_names == {"gateway-service", "payment-service"}
        assert cand.is_root_cause_confirmed is False


class TestAPIEndpoints:
    """Verify REST API endpoints for LogBERT inference."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        self.client = TestClient(app)

    def test_api_model_info_endpoint(self):
        resp = self.client.get("/api/v1/logs/anomalies/model-info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_name"] == "LogBERT"
        assert data["hidden_size"] == 128
        assert data["num_hidden_layers"] == 4
        assert data["device"] == "cpu"
        assert data["status"] == "ready"
        assert data["threshold"] == pytest.approx(0.0004, abs=1e-4)

    def test_api_detect_sequence_anomaly(self):
        payload = {
            "session_id": "api_test_session_1",
            "events": ["E5", "E5"],
        }
        resp = self.client.post("/api/v1/logs/anomalies/detect", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "api_test_session_1"
        assert "is_anomaly" in data
        assert "composite_score" in data
        assert data["threshold"] == pytest.approx(0.0004, abs=1e-4)
        assert data["num_events"] == 2

    def test_api_detect_empty_sequence(self):
        payload = {
            "session_id": "api_test_empty",
            "events": [],
        }
        resp = self.client.post("/api/v1/logs/anomalies/detect", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_anomaly"] is False
        assert data["composite_score"] == 0.0
        assert data["num_events"] == 0

    def test_api_batch_detect_sequence_anomalies(self):
        payload = {
            "sequences": [
                {"session_id": "b1", "events": ["E11"]},
                {"session_id": "b2", "events": ["UNKNOWN_1", "UNKNOWN_2"]},
            ]
        }
        resp = self.client.post("/api/v1/logs/anomalies/batch-detect", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_evaluated"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["session_id"] == "b1"
        assert data["results"][1]["session_id"] == "b2"
