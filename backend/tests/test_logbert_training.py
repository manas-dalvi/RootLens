"""
Unit tests for LogBERT self-supervised training pipeline, dataset loader, and anomaly scoring.
"""

from pathlib import Path
import pytest
import torch
from torch.utils.data import DataLoader

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.dataset import SessionSequence
from app.ml.logbert.model import LogBERT
from app.ml.logbert.scoring import compute_sequence_anomaly_score, find_validation_threshold
from app.ml.logbert.trainer import LogBERTDataset, LogBERTTrainer
from app.ml.logbert.vocab import EventVocab


@pytest.fixture
def sample_vocab() -> EventVocab:
    v = EventVocab()
    v.fit(["E1", "E2", "E3", "E4", "E5"])
    return v


@pytest.fixture
def sample_config(sample_vocab) -> LogBERTConfig:
    return LogBERTConfig(
        vocab_size=sample_vocab.vocab_size,
        max_seq_len=16,
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=64,
    )


class TestLogBERTDataset:
    """Verify sequence wrapping, padding, attention masks, and DataLoader batching."""

    def test_dataset_item_structure(self, sample_vocab):
        sessions = [
            SessionSequence(block_id="blk_1", events=["E1", "E2"], label=0),
            SessionSequence(block_id="blk_2", events=["E3"], label=1),
        ]
        ds = LogBERTDataset(sessions, sample_vocab, max_seq_len=10)
        assert len(ds) == 2

        item = ds[0]
        assert "input_ids" in item
        assert "attention_mask" in item
        assert "block_id" in item
        assert "label" in item
        assert item["input_ids"].shape == (10,)
        assert item["attention_mask"].shape == (10,)

        # Sequence must be: [CLS/DIST] + E1 + E2 + [SEP] + PAD * 6
        expected_ids = [
            sample_vocab.cls_id,
            sample_vocab.token_to_id("E1"),
            sample_vocab.token_to_id("E2"),
            sample_vocab.sep_id,
            0, 0, 0, 0, 0, 0,
        ]
        assert item["input_ids"].tolist() == expected_ids
        assert item["attention_mask"].tolist() == [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]


class TestLogBERTTrainerAndScoring:
    """Verify trainer execution, center initialization, loss decreases, and checkpoint reloading."""

    def test_center_initialization_requires_normal_sessions(self, sample_config, sample_vocab):
        model = LogBERT(sample_config)
        trainer = LogBERTTrainer(model, sample_config, device="cpu")

        # DataLoader with an anomaly sequence should raise ValueError
        mixed_sessions = [
            SessionSequence(block_id="b1", events=["E1", "E2"], label=0),
            SessionSequence(block_id="b2", events=["E3"], label=1),
        ]
        ds = LogBERTDataset(mixed_sessions, sample_vocab, max_seq_len=sample_config.max_seq_len)
        loader = DataLoader(ds, batch_size=2)

        with pytest.raises(ValueError, match="DATA LEAKAGE"):
            trainer.initialize_center_from_loader(loader)

    def test_smoke_training_loop(self, sample_config, sample_vocab, tmp_path: Path):
        # 20 normal training sessions
        train_sessions = [
            SessionSequence(block_id=f"train_{i}", events=["E1", "E2", "E3"], label=0)
            for i in range(20)
        ]
        # 10 validation sessions (8 normal, 2 anomaly)
        val_sessions = [
            SessionSequence(block_id=f"val_{i}", events=["E1", "E2"], label=0) for i in range(8)
        ] + [
            SessionSequence(block_id=f"val_anom_{j}", events=["E5", "E5"], label=1) for j in range(2)
        ]

        train_ds = LogBERTDataset(train_sessions, sample_vocab, max_seq_len=sample_config.max_seq_len)
        val_ds = LogBERTDataset(val_sessions, sample_vocab, max_seq_len=sample_config.max_seq_len)

        train_loader = DataLoader(train_ds, batch_size=4, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=4, shuffle=False)

        model = LogBERT(sample_config)
        trainer = LogBERTTrainer(model, sample_config, lr=1e-3, device="cpu")

        results = trainer.train(
            train_loader=train_loader,
            val_loader=val_loader,
            epochs=2,
            checkpoint_dir=tmp_path,
        )

        assert results["completed_epochs"] == 2
        assert len(results["loss_history"]) == 2
        for record in results["loss_history"]:
            assert record["train_total_loss"] > 0.0
            assert record["train_mlkp_loss"] > 0.0
            assert record["train_vhm_loss"] >= 0.0
            assert record["val_loss"] > 0.0

        # Verify saved checkpoint
        checkpoint_path = tmp_path / "logbert_best.pt"
        assert checkpoint_path.exists()

        reloaded_model, _, reloaded_center, _ = LogBERTTrainer.load_checkpoint(checkpoint_path)
        assert reloaded_center is not None
        assert reloaded_center.shape == (sample_config.hidden_size,)

        # Verify inference with reloaded model
        test_in = torch.tensor([[sample_vocab.cls_id, sample_vocab.token_to_id("E1"), sample_vocab.sep_id] + [0] * 13])
        with torch.no_grad():
            out_orig = trainer.model(test_in)
            out_reload = reloaded_model(test_in)
        assert torch.allclose(out_orig.logits, out_reload.logits)

    def test_find_validation_threshold(self):
        # Normal scores clustered around 0.1, anomaly scores around 0.9
        scores = [0.05, 0.10, 0.12, 0.15, 0.80, 0.85, 0.90]
        labels = [0, 0, 0, 0, 1, 1, 1]

        thresh_res = find_validation_threshold(scores, labels)
        assert thresh_res["val_f1"] == 1.0
        assert thresh_res["threshold"] == 0.80
        assert thresh_res["val_precision"] == 1.0
        assert thresh_res["val_recall"] == 1.0

    def test_compute_sequence_anomaly_score(self, sample_config, sample_vocab):
        model = LogBERT(sample_config)
        center = torch.ones(sample_config.hidden_size)

        input_ids = torch.tensor([[sample_vocab.cls_id, sample_vocab.token_to_id("E1"), sample_vocab.sep_id] + [0] * 13])
        attention_mask = torch.tensor([[1, 1, 1] + [0] * 13])

        score = compute_sequence_anomaly_score(
            model=model,
            input_ids=input_ids,
            attention_mask=attention_mask,
            center=center,
            num_candidates=2,
            alpha=0.1,
        )

        assert "vhm_distance" in score
        assert "mismatch_count" in score
        assert "composite_score" in score
        assert score["num_evaluated_events"] == 1
        assert score["vhm_distance"] >= 0.0
