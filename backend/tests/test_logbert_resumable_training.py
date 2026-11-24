"""
Unit tests for LogBERT resumable training pipeline, atomic checkpointing, and center caching.
"""

import json
from pathlib import Path
import pytest
import torch

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.dataset import SessionSequence
from app.ml.logbert.inference import LogBERTInferenceEngine
from app.ml.logbert.model import LogBERT
from app.ml.logbert.trainer import LogBERTTrainer, LogBERTDataset
from app.ml.logbert.vocab import EventVocab
from backend.scripts.train_full_logbert import (
    ResumableBatchSampler,
    save_atomic_checkpoint,
    run_full_training,
)


class TestResumableBatchSampler:
    """Verify deterministic batch iteration and exact resumption without skipped/repeated items."""

    def test_sampler_full_iteration(self):
        indices = list(range(25))
        sampler = ResumableBatchSampler(indices, batch_size=10, start_step=0)
        assert len(sampler) == 3

        batches = list(sampler)
        assert batches == [
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
            [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
            [20, 21, 22, 23, 24],
        ]

    def test_sampler_resumption_exactness(self):
        indices = list(range(100))
        batch_size = 16
        full_sampler = ResumableBatchSampler(indices, batch_size=batch_size, start_step=0)
        full_batches = list(full_sampler)

        # Resume from step 2 (should skip batches 0 and 1, yield batch 2 onwards)
        resumed_sampler = ResumableBatchSampler(indices, batch_size=batch_size, start_step=2)
        resumed_batches = list(resumed_sampler)

        assert len(resumed_batches) == len(full_batches) - 2
        assert resumed_batches == full_batches[2:]

        # Verify no items are repeated or missing across (0..1) + (2..end)
        reconstructed = full_batches[:2] + resumed_batches
        assert reconstructed == full_batches


class TestAtomicCheckpoint:
    """Verify atomic checkpoint writing and safety against corruptions."""

    def test_atomic_write_creates_valid_file(self, tmp_path: Path):
        target = tmp_path / "model" / "test_ckpt.pt"
        payload = {"epoch": 1, "test_tensor": torch.tensor([1.0, 2.0, 3.0])}

        save_atomic_checkpoint(payload, target)
        assert target.exists()

        loaded = torch.load(target, map_location="cpu", weights_only=False)
        assert loaded["epoch"] == 1
        assert torch.equal(loaded["test_tensor"], payload["test_tensor"])

        # Overwrite atomically with updated payload
        updated_payload = {"epoch": 2, "test_tensor": torch.tensor([4.0, 5.0])}
        save_atomic_checkpoint(updated_payload, target)
        reloaded = torch.load(target, map_location="cpu", weights_only=False)
        assert reloaded["epoch"] == 2
        assert torch.equal(reloaded["test_tensor"], updated_payload["test_tensor"])


class TestResumableTrainingIntegration:
    """End-to-end controlled test of checkpoint saving, interruption simulation, and exact resumption."""

    @pytest.fixture
    def test_env(self, tmp_path: Path):
        data_dir = tmp_path / "data"
        output_dir = tmp_path / "model"
        eval_dir = tmp_path / "eval"
        data_dir.mkdir(parents=True)

        vocab = EventVocab()
        # Ensure 22 tokens (5 special + 17 event templates)
        templates = ["E11", "E16", "E18", "E2", "E20", "E21", "E22", "E23", "E25", "E26", "E27", "E28", "E3", "E4", "E5", "E6", "E9"]
        for t in templates:
            vocab.add_token(t)
        assert vocab.vocab_size == 22
        vocab.save(data_dir / "vocab.json")

        # 40 normal training sessions
        train_sessions = [
            {
                "block_id": f"train_{i}",
                "events": ["E22", "E5", "E5", "E26", "E11", "E9", "E2", "E23", "E21"],
                "label": 0,
                "length": 9,
                "line_ids": [],
            }
            for i in range(40)
        ]
        with open(data_dir / "train_sessions.json", "w") as f:
            json.dump(train_sessions, f)

        # 10 validation sessions (8 normal, 2 anomaly with E7)
        val_sessions = [
            {
                "block_id": f"val_norm_{i}",
                "events": ["E22", "E5", "E5", "E26", "E11", "E9", "E2", "E23", "E21"],
                "label": 0,
                "length": 9,
                "line_ids": [],
            }
            for i in range(8)
        ] + [
            {
                "block_id": f"val_anom_{j}",
                "events": ["E22", "E5", "E7", "E26", "E11", "E9", "E2", "E23", "E21"],
                "label": 1,
                "length": 9,
                "line_ids": [],
            }
            for j in range(2)
        ]
        with open(data_dir / "val_sessions.json", "w") as f:
            json.dump(val_sessions, f)

        return {
            "data_dir": data_dir,
            "output_dir": output_dir,
            "eval_dir": eval_dir,
            "vocab_path": data_dir / "vocab.json",
        }

    def test_center_caching_and_reload(self, test_env):
        # Run 1 epoch: should create and cache hypersphere_center.pt
        result1 = run_full_training(
            data_dir=test_env["data_dir"],
            output_dir=test_env["output_dir"],
            eval_dir=test_env["eval_dir"],
            batch_size=8,
            max_epochs=1,
            hidden_size=32,
            num_layers=2,
            num_heads=2,
            save_interval=2,
            resume=False,
        )

        center_file = test_env["output_dir"] / "hypersphere_center.pt"
        assert center_file.exists(), "hypersphere_center.pt must be cached"

        cached = torch.load(center_file, map_location="cpu", weights_only=False)
        assert "center" in cached
        assert cached["center"].shape == (32,)

        # Run 2nd time: should restore cached center without error
        result2 = run_full_training(
            data_dir=test_env["data_dir"],
            output_dir=test_env["output_dir"],
            eval_dir=test_env["eval_dir"],
            batch_size=8,
            max_epochs=1,
            hidden_size=32,
            num_layers=2,
            num_heads=2,
            save_interval=2,
            resume=False,
        )
        assert (test_env["output_dir"] / "logbert_best.pt").exists()

    def test_checkpoint_save_and_mid_epoch_resume(self, test_env):
        # Total sessions = 40, batch_size = 8 -> 5 batches per epoch.
        # Run with save_interval = 2.
        # Checkpoints should be saved at batch 2 and batch 4!
        run_full_training(
            data_dir=test_env["data_dir"],
            output_dir=test_env["output_dir"],
            eval_dir=test_env["eval_dir"],
            batch_size=8,
            max_epochs=1,
            hidden_size=32,
            num_layers=2,
            num_heads=2,
            save_interval=2,
            resume=False,
        )

        resumable_ckpt_path = test_env["output_dir"] / "checkpoint_resumable.pt"
        assert resumable_ckpt_path.exists(), "checkpoint_resumable.pt must be saved"

        ckpt = torch.load(resumable_ckpt_path, map_location="cpu", weights_only=False)
        assert "epoch" in ckpt
        assert "step" in ckpt
        assert "model_state_dict" in ckpt
        assert "optimizer_state_dict" in ckpt
        assert "epoch_indices" in ckpt
        assert "rng_states" in ckpt

        # Now simulate resuming to run epoch 2
        resumed_result = run_full_training(
            data_dir=test_env["data_dir"],
            output_dir=test_env["output_dir"],
            eval_dir=test_env["eval_dir"],
            batch_size=8,
            max_epochs=2,
            hidden_size=32,
            num_layers=2,
            num_heads=2,
            save_interval=2,
            resume=True,
        )

        best_ckpt = test_env["output_dir"] / "logbert_best.pt"
        assert best_ckpt.exists()

        # Verify best checkpoint reloads in LogBERTInferenceEngine
        engine = LogBERTInferenceEngine(
            checkpoint_path=best_ckpt,
            vocab_path=test_env["vocab_path"],
            device="cpu",
        )
        assert engine.config.hidden_size == 32
        assert engine.center.shape == (32,)

        pred = engine.predict_sequence(["E22", "E5", "E5", "E26", "E11", "E9", "E2", "E23", "E21"])
        assert isinstance(pred.is_anomaly, bool)
        assert pred.composite_score >= 0.0

    def test_resumed_matches_uninterrupted_execution(self, tmp_path: Path):
        """
        Verify that an execution interrupted mid-epoch and resumed produces identical
        numerical model weights, optimizer states, loss accumulators, and epoch/batch positions
        compared to uninterrupted execution.
        """
        env_uninterrupted = tmp_path / "uninterrupted"
        env_resumed = tmp_path / "resumed"
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)

        vocab = EventVocab()
        templates = ["E11", "E16", "E18", "E2", "E20", "E21", "E22", "E23", "E25", "E26", "E27", "E28", "E3", "E4", "E5", "E6", "E9"]
        for t in templates:
            vocab.add_token(t)
        vocab.save(data_dir / "vocab.json")

        train_sessions = [
            {
                "block_id": f"train_{i}",
                "events": ["E22", "E5", "E5", "E26", "E11", "E9", "E2", "E23", "E21"],
                "label": 0,
                "length": 9,
                "line_ids": [],
            }
            for i in range(32)
        ]
        with open(data_dir / "train_sessions.json", "w") as f:
            json.dump(train_sessions, f)

        val_sessions = [
            {
                "block_id": f"val_{i}",
                "events": ["E22", "E5", "E5", "E26", "E11", "E9", "E2", "E23", "E21"],
                "label": 0,
                "length": 9,
                "line_ids": [],
            }
            for i in range(8)
        ] + [
            {
                "block_id": f"val_anom_{j}",
                "events": ["E22", "E5", "E7", "E26", "E11", "E9", "E2", "E23", "E21"],
                "label": 1,
                "length": 9,
                "line_ids": [],
            }
            for j in range(2)
        ]
        with open(data_dir / "val_sessions.json", "w") as f:
            json.dump(val_sessions, f)

        # 1. Run uninterrupted execution: 1 epoch of 4 batches (32 train sessions, batch_size=8)
        uninterrupted_result = run_full_training(
            data_dir=data_dir,
            output_dir=env_uninterrupted / "model",
            eval_dir=env_uninterrupted / "eval",
            batch_size=8,
            max_epochs=1,
            hidden_size=32,
            num_layers=2,
            num_heads=2,
            seed=42,
            save_interval=2,
            resume=False,
        )

        # 2. Run interrupted execution with realistic mid-epoch interruption:
        # Save checkpoint at batch 2, and interrupt after step 2.
        with pytest.raises(KeyboardInterrupt):
            run_full_training(
                data_dir=data_dir,
                output_dir=env_resumed / "model",
                eval_dir=env_resumed / "eval",
                batch_size=8,
                max_epochs=1,
                hidden_size=32,
                num_layers=2,
                num_heads=2,
                seed=42,
                save_interval=2,
                interrupt_after_step=2,
                resume=False,
            )

        # Verify step-2 checkpoint was saved before the interruption
        resumable_ckpt_step2 = env_resumed / "model" / "checkpoint_resumable.pt"
        assert resumable_ckpt_step2.exists()
        ckpt_payload_step2 = torch.load(resumable_ckpt_step2, map_location="cpu", weights_only=False)
        assert ckpt_payload_step2["epoch"] == 1
        assert ckpt_payload_step2["step"] == 2
        assert "rng_states" in ckpt_payload_step2

        # 3. Resume from step-2 checkpoint to complete the epoch
        resumed_result = run_full_training(
            data_dir=data_dir,
            output_dir=env_resumed / "model",
            eval_dir=env_resumed / "eval",
            batch_size=8,
            max_epochs=1,
            hidden_size=32,
            num_layers=2,
            num_heads=2,
            seed=42,
            save_interval=2,
            resume=True,
        )

        # Load final checkpoints from both runs
        ckpt_uninterrupted = torch.load(
            env_uninterrupted / "model" / "checkpoint_resumable.pt",
            map_location="cpu",
            weights_only=False,
        )
        ckpt_resumed = torch.load(
            env_resumed / "model" / "checkpoint_resumable.pt",
            map_location="cpu",
            weights_only=False,
        )

        best_uninterrupted = torch.load(
            env_uninterrupted / "model" / "logbert_best.pt",
            map_location="cpu",
            weights_only=False,
        )
        best_resumed = torch.load(
            env_resumed / "model" / "logbert_best.pt",
            map_location="cpu",
            weights_only=False,
        )

        # 1. Compare EVERY model parameter between uninterrupted and resumed execution using torch.testing.assert_close
        weights_uninterrupted = ckpt_uninterrupted["model_state_dict"]
        weights_resumed = ckpt_resumed["model_state_dict"]
        assert set(weights_uninterrupted.keys()) == set(weights_resumed.keys()), "Parameter keys must match exactly"
        for param_name, param_uninterrupted in weights_uninterrupted.items():
            param_resumed = weights_resumed[param_name]
            torch.testing.assert_close(
                param_resumed,
                param_uninterrupted,
                rtol=1e-5,
                atol=1e-5,
                msg=lambda m: f"Model parameter '{param_name}' does not match between uninterrupted and resumed run: {m}",
            )

        # Also check best checkpoint parameters
        for param_name, param_uninterrupted in best_uninterrupted["model_state_dict"].items():
            param_resumed = best_resumed["model_state_dict"][param_name]
            torch.testing.assert_close(
                param_resumed,
                param_uninterrupted,
                rtol=1e-5,
                atol=1e-5,
                msg=lambda m: f"Best model parameter '{param_name}' does not match: {m}",
            )

        # 2. Compare relevant AdamW optimizer state tensors and step counters
        opt_uninterrupted = ckpt_uninterrupted["optimizer_state_dict"]
        opt_resumed = ckpt_resumed["optimizer_state_dict"]
        assert set(opt_uninterrupted["state"].keys()) == set(opt_resumed["state"].keys())

        for pid, state_uninterrupted in opt_uninterrupted["state"].items():
            state_resumed = opt_resumed["state"][pid]
            # Step counter comparison
            step_uninterrupted = state_uninterrupted["step"]
            step_resumed = state_resumed["step"]
            if isinstance(step_uninterrupted, torch.Tensor):
                torch.testing.assert_close(step_resumed, step_uninterrupted)
            else:
                assert step_resumed == step_uninterrupted

            # Optimizer 1st moment vector (exp_avg)
            torch.testing.assert_close(
                state_resumed["exp_avg"],
                state_uninterrupted["exp_avg"],
                rtol=1e-5,
                atol=1e-5,
                msg=lambda m: f"Optimizer exp_avg mismatch for parameter {pid}: {m}",
            )
            # Optimizer 2nd moment vector (exp_avg_sq)
            torch.testing.assert_close(
                state_resumed["exp_avg_sq"],
                state_uninterrupted["exp_avg_sq"],
                rtol=1e-5,
                atol=1e-5,
                msg=lambda m: f"Optimizer exp_avg_sq mismatch for parameter {pid}: {m}",
            )

        # 3. Compare final accumulated training losses
        assert ckpt_uninterrupted["epoch_total_loss"] == pytest.approx(ckpt_resumed["epoch_total_loss"], rel=1e-5, abs=1e-5)
        assert ckpt_uninterrupted["epoch_mlkp_loss"] == pytest.approx(ckpt_resumed["epoch_mlkp_loss"], rel=1e-5, abs=1e-5)
        assert ckpt_uninterrupted["epoch_vhm_loss"] == pytest.approx(ckpt_resumed["epoch_vhm_loss"], rel=1e-5, abs=1e-5)

        # Compare validation metrics
        summary_unint = uninterrupted_result["training_summary"]
        summary_res = resumed_result["training_summary"]
        assert summary_unint["best_val_metrics"]["train_total_loss"] == pytest.approx(
            summary_res["best_val_metrics"]["train_total_loss"], rel=1e-5, abs=1e-5
        )
        assert summary_unint["best_val_metrics"]["val_loss"] == pytest.approx(
            summary_res["best_val_metrics"]["val_loss"], rel=1e-5, abs=1e-5
        )

        # 4. Compare final epoch and batch position
        assert ckpt_uninterrupted["epoch"] == 1
        assert ckpt_resumed["epoch"] == 1
        assert ckpt_uninterrupted["step"] == 4  # Total batches = 32 / 8 = 4
        assert ckpt_resumed["step"] == 4
        assert summary_unint["best_epoch"] == summary_res["best_epoch"]
