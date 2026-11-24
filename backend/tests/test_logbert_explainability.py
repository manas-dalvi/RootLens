"""
Unit and integration tests for LogBERT Token-Level Explainability (Phase 8 Stage 2).

Verifies:
1. Per-position MLKP mismatch identification and contributing positions.
2. Top-k predicted candidate template extraction with valid softmax probabilities.
3. Separation of discrete token mismatch evidence from continuous VHM embedding distance.
4. Pure VHM-only anomaly detection (0 token mismatches, but anomalous embedding distance).
5. Safe handling of unknown tokens ([UNK]), special tokens ([CLS], [SEP], [PAD]), and empty sequences.
6. Non-anomalous (normal) sequences and sequences with 0 mismatches.
7. API response serialization with structured AnomalyExplanation models.
8. Telemetry and timeline event attribute propagation with evidence navigation links.
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
from app.ml.logbert.model import LogBERT
from app.ml.logbert.scoring import compute_sequence_anomaly_score
from app.ml.logbert.vocab import EventVocab
from app.schemas.log_anomaly import AnomalyExplanation, LogBERTInferenceResponse
from app.services.clustering_service import LogEventItem
from app.services.correlation_service import correlate_clusters
from app.services.logbert_service import detect_sequence_anomalies_as_clusters

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


class TestTokenLevelExplainability:
    """Verifies detailed per-position MLKP prediction and mismatch extraction."""

    def test_per_position_mismatch_identification(self, inference_engine):
        """Known normal sequence followed by an invalid token transition flags exactly the unexpected position."""
        # E5, E5, E5 is typical normal HDFS block lifecycle; UNKNOWN_FAIL is unexpected
        events = ["E5", "E5", "UNKNOWN_FAIL", "E5"]
        result = inference_engine.predict_sequence(events=events, session_id="test_exp_001")

        assert result.explanation is not None
        exp = result.explanation
        assert "mismatch_positions" in exp
        assert "token_explanations" in exp
        assert exp["total_evaluated_events"] == 4
        assert len(exp["token_explanations"]) == 4

        # Position 2 corresponds to UNKNOWN_FAIL
        pos2 = exp["token_explanations"][2]
        assert pos2["position"] == 2
        assert pos2["observed_event"] == "UNKNOWN_FAIL"
        assert pos2["observed_token_id"] == inference_engine.vocab.unk_id
        assert pos2["is_mismatch"] is True
        assert pos2["contributes_to_anomaly"] is True
        assert 2 in exp["mismatch_positions"]

    def test_top_k_candidate_extraction_properties(self, inference_engine):
        """Top-k candidates should contain valid decoded templates, sorted probabilities, and non-empty candidates."""
        events = ["E5", "E22"]
        result = inference_engine.predict_sequence(events=events, session_id="test_exp_candidates", num_candidates=5)
        assert result.explanation is not None
        tokens = result.explanation["token_explanations"]
        assert len(tokens) == 2

        for t in tokens:
            cands = t["top_candidates"]
            assert len(cands) <= 5
            assert len(cands) > 0

            prev_prob = 1.01
            prob_sum = 0.0
            for c in cands:
                assert "event" in c
                assert "token_id" in c
                assert "probability" in c
                # Valid template name (not empty or None)
                assert isinstance(c["event"], str) and len(c["event"]) > 0
                assert 0.0 <= c["probability"] <= 1.0
                # Top-k should be sorted in descending probability order
                assert c["probability"] <= prev_prob + 1e-4
                prev_prob = c["probability"]
                prob_sum += c["probability"]

            # Probability sum of top-k cannot exceed 1.0
            assert prob_sum <= 1.0001

    def test_distinguish_token_mismatch_vs_vhm_evidence(self, inference_engine):
        """Explanation explicitly breaks down MLKP contribution and VHM distance contribution."""
        events = ["UNKNOWN_EVENT_A", "UNKNOWN_EVENT_B"]
        result = inference_engine.predict_sequence(events=events, session_id="test_exp_distinguish")
        exp = result.explanation
        assert exp is not None

        assert "mlkp_contribution" in exp
        assert "vhm_contribution" in exp
        assert "vhm_evidence" in exp
        assert "dominant_factor" in exp

        vhm_ev = exp["vhm_evidence"]
        assert vhm_ev["vhm_distance"] == result.vhm_distance
        assert vhm_ev["vhm_alpha"] == pytest.approx(inference_engine.config.vhm_alpha, abs=1e-4)
        assert vhm_ev["vhm_contribution"] == pytest.approx(
            round(inference_engine.config.vhm_alpha * result.vhm_distance, 4), abs=1e-3
        )
        assert isinstance(vhm_ev["explanation"], str)
        assert len(vhm_ev["explanation"]) > 0

    def test_vhm_only_anomaly_case(self, sample_config=None):
        """A sequence with 0 token mismatches whose VHM distance exceeds threshold produces EMBEDDING_DISTANCE explanation."""
        # Create a synthetic model and center where VHM distance is large but token predictions match
        config = LogBERTConfig(vocab_size=10, hidden_size=32, num_hidden_layers=2, num_attention_heads=2, max_seq_len=16)
        vocab = EventVocab().fit(["E1", "E2"])
        model = LogBERT(config)
        model.eval()

        # Place center very far away so VHM distance is large (> 50.0)
        far_center = torch.ones(config.hidden_size) * 10.0

        # Normal sequence with valid tokens
        input_ids = torch.tensor([[vocab.cls_id, vocab.token_to_id("E1"), vocab.sep_id] + [0] * 13])
        attention_mask = torch.tensor([[1, 1, 1] + [0] * 13])

        # num_candidates large enough to ensure true token is in top candidates
        score_res = compute_sequence_anomaly_score(
            model=model,
            input_ids=input_ids,
            attention_mask=attention_mask,
            center=far_center,
            num_candidates=10,
            alpha=0.1,
            vocab=vocab,
            original_events=["E1"],
            threshold=0.05,
        )

        assert score_res["mismatch_count"] == 0
        assert score_res["mismatch_ratio"] == 0.0
        exp = score_res["explanation"]
        assert exp["is_anomaly"] is True
        assert exp["mismatch_count"] == 0
        assert exp["explanation_type"] == "EMBEDDING_DISTANCE"
        assert exp["dominant_factor"] == "VHM_DISTANCE"
        assert "all observed event transitions appeared locally valid" in exp["summary"]

    def test_unknown_and_special_tokens_handling(self, inference_engine):
        """Unknown tokens map to [UNK] and are evaluated as mismatches; special tokens are never evaluated."""
        events = ["CUSTOM_NON_EXISTENT_TOKEN_123"]
        result = inference_engine.predict_sequence(events=events, session_id="test_unk_single")
        exp = result.explanation
        assert exp is not None
        assert exp["total_evaluated_events"] == 1
        assert len(exp["token_explanations"]) == 1

        t0 = exp["token_explanations"][0]
        # Preserves input event string
        assert t0["observed_event"] == "CUSTOM_NON_EXISTENT_TOKEN_123"
        # Token ID in vocab is UNK (1)
        assert t0["observed_token_id"] == inference_engine.vocab.unk_id
        assert t0["is_mismatch"] is True
        assert t0["contributes_to_anomaly"] is True

    def test_empty_sequence_handling(self, inference_engine):
        """Empty sequence returns safe non-anomalous explanation without dividing by zero."""
        result = inference_engine.predict_sequence(events=[], session_id="test_empty")
        assert result.is_anomaly is False
        assert result.composite_score == 0.0
        assert result.num_events == 0
        exp = result.explanation
        assert exp is not None
        assert exp["explanation_type"] == "NORMAL"
        assert exp["dominant_factor"] == "NONE"
        assert exp["mismatch_count"] == 0
        assert exp["token_explanations"] == []
        assert exp["mismatch_positions"] == []


class TestExplainabilityAPIAndPipeline:
    """Verifies API serialization and incident correlation integration."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        self.client = TestClient(app)

    def test_api_detect_sequence_anomaly_returns_explanation(self):
        """POST /api/v1/logs/anomalies/detect includes structured explanation matching schema."""
        payload = {
            "session_id": "api_explain_001",
            "events": ["E5", "UNKNOWN_EVENT_Z", "E5"],
        }
        resp = self.client.post("/api/v1/logs/anomalies/detect", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        # Validate with Pydantic response schema
        validated_resp = LogBERTInferenceResponse(**data)
        assert validated_resp.explanation is not None
        exp: AnomalyExplanation = validated_resp.explanation

        assert exp.explanation_type in ("TOKEN_MISMATCH", "EMBEDDING_DISTANCE", "HYBRID", "NORMAL")
        assert exp.dominant_factor in ("MLKP_MISMATCH", "VHM_DISTANCE", "NONE")
        assert len(exp.token_explanations) == 3
        assert exp.token_explanations[1].observed_event == "UNKNOWN_EVENT_Z"
        assert exp.token_explanations[1].is_mismatch is True

    def test_incident_pipeline_populates_explainability_attributes(self, inference_engine):
        """Anomalous logs clustered via detect_sequence_anomalies_as_clusters receive MLKP and VHM attributes."""
        now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
        logs = [
            LogEventItem(
                log_id="log_exp_1",
                timestamp=now,
                service_name="hdfs-datanode",
                log_level="INFO",
                message="Starting block processing blk_1111 E5",
                trace_id="trace-exp-1",
                attributes={"block_id": "blk_1111", "template": "E5"},
                raw_log="raw log 1",
            ),
            LogEventItem(
                log_id="log_exp_2",
                timestamp=now + timedelta(seconds=1),
                service_name="hdfs-datanode",
                log_level="INFO",
                message="Unexpected transition blk_1111 UNKNOWN_BAD_EVENT",
                trace_id="trace-exp-1",
                attributes={"block_id": "blk_1111", "template": "UNKNOWN_BAD_EVENT"},
                raw_log="raw log 2",
            ),
        ]

        clusters = detect_sequence_anomalies_as_clusters(logs, window_minutes=15, engine=inference_engine)
        assert len(clusters) == 1
        cl = clusters[0]
        assert len(cl.events) == 2

        # Verify individual event attribute annotations
        ev0 = cl.events[0]
        ev1 = cl.events[1]
        assert ev0.attributes["model"] == "LogBERT"
        assert "explanation_type" in ev0.attributes
        assert "dominant_factor" in ev0.attributes

        # ev1 should be marked as mismatch
        assert ev1.attributes["is_mlkp_mismatch"] is True
        assert ev1.attributes["contributes_to_anomaly"] is True
        assert ev1.attributes["mlkp_observed_event"] == "UNKNOWN_BAD_EVENT"
        assert isinstance(ev1.attributes["mlkp_top_candidates"], list)

        # Correlate into incident candidates
        candidates = correlate_clusters(clusters)
        assert len(candidates) == 1
        cand = candidates[0]
        assert "SEQUENCE_ANOMALY" in cand.correlation_rules_used

        # Check evidence item rationale contains explainability tags
        ev_item = cand.evidence_items[0]
        assert "LogBERT sequence anomaly" in ev_item.rationale
        assert "MLKP mismatch" in ev_item.rationale or "VHM" in ev_item.rationale
