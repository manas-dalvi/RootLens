"""
Comprehensive unit tests for LogBERT model architecture and self-supervised objectives:
- Configurable model dimensions and parameter counts
- Forward-pass tensor shapes and sequence representation
- Attention masks and padding isolation
- MLKP token masking and prediction loss
- VHM hypersphere center initialization, stability, and compactness loss
- Finite loss calculation and backward gradient propagation
- CPU-only execution verification
- State dict persistence and reload
- Seeded determinism
"""

import tempfile
from pathlib import Path

import pytest
import torch

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.model import LogBERT, LogBERTOutput
from app.ml.logbert.objectives import (
    LogBERTJointLoss,
    MLKPObjective,
    VHMObjective,
)


@pytest.fixture
def default_config() -> LogBERTConfig:
    return LogBERTConfig(
        vocab_size=19,
        max_seq_len=32,
        hidden_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        intermediate_size=128,
        hidden_dropout_prob=0.1,
        attention_probs_dropout_prob=0.1,
    )


class TestLogBERTConfig:
    """Verify configuration validation, constraints, and serialization."""

    def test_default_config_validity(self, default_config):
        assert default_config.vocab_size == 19
        assert default_config.hidden_size == 64
        assert default_config.num_attention_heads == 4
        assert default_config.attention_head_size == 16
        assert default_config.pad_token_id == 0
        assert default_config.cls_token_id == 2

    def test_invalid_hidden_size_raises(self):
        with pytest.raises(ValueError, match="divisible by num_attention_heads"):
            LogBERTConfig(hidden_size=65, num_attention_heads=4)

    def test_invalid_vocab_size_raises(self):
        with pytest.raises(ValueError, match="vocab_size"):
            LogBERTConfig(vocab_size=4, mask_token_id=4)

    def test_config_serialization(self, default_config, tmp_path: Path):
        save_path = tmp_path / "config.json"
        default_config.save(save_path)
        assert save_path.exists()

        loaded = LogBERTConfig.load(save_path)
        assert loaded.hidden_size == default_config.hidden_size
        assert loaded.vocab_size == default_config.vocab_size
        assert loaded.vhm_alpha == default_config.vhm_alpha


class TestLogBERTArchitecture:
    """Verify neural network instantiation, parameter counting, and CPU execution."""

    def test_model_construction_and_cpu_device(self, default_config):
        model = LogBERT(default_config)
        model.eval()

        # Confirm all parameters are on CPU
        for param in model.parameters():
            assert param.device.type == "cpu"

        param_counts = model.count_parameters()
        assert param_counts["total_parameters"] > 0
        assert param_counts["trainable_parameters"] == param_counts["total_parameters"]

    def test_forward_pass_tensor_shapes(self, default_config):
        model = LogBERT(default_config)
        model.eval()

        batch_size = 4
        seq_len = 16
        input_ids = torch.randint(low=0, high=default_config.vocab_size, size=(batch_size, seq_len))

        with torch.no_grad():
            output: LogBERTOutput = model(input_ids)

        assert output.logits.shape == (batch_size, seq_len, default_config.vocab_size)
        assert output.sequence_output.shape == (batch_size, seq_len, default_config.hidden_size)
        assert output.cls_representation.shape == (batch_size, default_config.hidden_size)

        # Confirm CLS representation strictly matches the first sequence position
        assert torch.equal(output.cls_representation, output.sequence_output[:, 0, :])
        # Confirm DIST representation is genuinely identical to CLS representation
        assert torch.equal(output.dist_representation, output.cls_representation)

    def test_dist_token_and_vocab_compatibility(self, default_config):
        from app.ml.logbert.vocab import EventVocab
        vocab = EventVocab()

        # Both [CLS] and [DIST] map to token ID 2
        assert vocab.cls_id == 2
        assert vocab.dist_id == 2
        assert vocab.token_to_id("[DIST]") == 2
        assert vocab.token_to_id("[CLS]") == 2

        # Vocab size preserved (alias does not inflate vocabulary)
        assert vocab.vocab_size == 5
        assert default_config.cls_token_id == 2
        assert default_config.dist_token_id == 2

    def test_attention_mask_padding_handling(self, default_config):
        model = LogBERT(default_config)
        model.eval()

        batch_size = 2
        seq_len = 8
        # Sequence 1: 5 real tokens, 3 pads
        # Sequence 2: 3 real tokens, 5 pads
        input_ids = torch.tensor([
            [2, 5, 6, 7, 8, 0, 0, 0],
            [2, 9, 10, 0, 0, 0, 0, 0],
        ])
        attention_mask = torch.tensor([
            [1, 1, 1, 1, 1, 0, 0, 0],
            [1, 1, 1, 0, 0, 0, 0, 0],
        ])

        with torch.no_grad():
            out_with_mask = model(input_ids, attention_mask=attention_mask)
            # Default behavior generates mask matching pad_token_id (0)
            out_auto_mask = model(input_ids)

        # Output with explicit mask vs auto-inferred mask from pad_token_id should match
        assert torch.allclose(out_with_mask.logits, out_auto_mask.logits, atol=1e-5)


class TestMLKPObjective:
    """Verify Masked Log Key Prediction masking logic and cross-entropy loss."""

    def test_mask_tokens_candidate_rules(self, default_config):
        mlkp = MLKPObjective(default_config)
        # Sequence with [CLS], event tokens, and [PAD]
        input_ids = torch.tensor([
            [2, 5, 6, 7, 8, 9, 10, 3, 0, 0],
        ])

        gen = torch.Generator().manual_seed(42)
        masked_ids, labels = mlkp.mask_tokens(input_ids, generator=gen)

        assert masked_ids.shape == input_ids.shape
        assert labels.shape == input_ids.shape

        # Special tokens must NEVER be masked (labels must be -100)
        assert labels[0, 0].item() == -100  # [CLS]
        assert labels[0, 7].item() == -100  # [SEP]
        assert labels[0, 8].item() == -100  # [PAD]
        assert labels[0, 9].item() == -100  # [PAD]

        # Positions with targets != -100 must match original input_ids
        masked_positions = (labels != -100).nonzero(as_tuple=False)
        for pos in masked_positions:
            b, s = pos[0].item(), pos[1].item()
            assert labels[b, s].item() == input_ids[b, s].item()

    def test_mlkp_loss_and_gradients(self, default_config):
        model = LogBERT(default_config)
        model.train()
        mlkp = MLKPObjective(default_config)

        input_ids = torch.tensor([[2, 5, 6, 7, 8, 3]])
        masked_ids, labels = mlkp.mask_tokens(input_ids)

        output = model(masked_ids)
        loss = mlkp.compute_loss(output.logits, labels)

        assert loss.item() >= 0.0
        assert not torch.isnan(loss)
        assert not torch.isinf(loss)

        # Verify gradient backprop
        loss.backward()
        for name, param in model.named_parameters():
            if param.requires_grad:
                assert param.grad is not None, f"Gradient missing for {name}"


class TestVHMObjective:
    """Verify Volume of Hypersphere Minimization center initialization and loss."""

    def test_center_initialization_and_numerical_stability(self, default_config):
        model = LogBERT(default_config)
        model.eval()

        normal_seqs = torch.tensor([
            [2, 5, 6, 7, 3],
            [2, 6, 8, 9, 3],
            [2, 5, 5, 10, 3],
        ])

        center = VHMObjective.initialize_center(model, normal_seqs, eps=0.1)

        assert center.shape == (default_config.hidden_size,)
        assert not torch.isnan(center).any()
        assert not torch.isinf(center).any()
        # Verify no coordinate has absolute value less than eps (stability guard)
        assert (torch.abs(center) >= 0.1 - 1e-6).all()

    def test_epsilon_guard_zero_and_near_zero_coordinates(self):
        # Test synthetic center tensor with exact zeros and small values
        raw_center = torch.tensor([0.0, -0.0, 0.05, -0.05, 0.5, -0.5, 0.1, -0.1])
        guarded = VHMObjective.apply_epsilon_guard(raw_center, eps=0.1)

        # 0.0 -> +0.1 (not 0.0)
        assert guarded[0].item() == pytest.approx(0.1)
        # -0.0 -> +0.1
        assert guarded[1].item() == pytest.approx(0.1)
        # 0.05 -> +0.1
        assert guarded[2].item() == pytest.approx(0.1)
        # -0.05 -> -0.1
        assert guarded[3].item() == pytest.approx(-0.1)
        # 0.5 -> 0.5 (unchanged)
        assert guarded[4].item() == pytest.approx(0.5)
        # -0.5 -> -0.5 (unchanged)
        assert guarded[5].item() == pytest.approx(-0.5)
        # 0.1 -> 0.1 (unchanged boundary)
        assert guarded[6].item() == pytest.approx(0.1)
        # -0.1 -> -0.1 (unchanged boundary)
        assert guarded[7].item() == pytest.approx(-0.1)

        # All coordinates must be strictly >= eps in magnitude
        assert (torch.abs(guarded) >= 0.1 - 1e-7).all()

        # All-zero tensor must not collapse
        all_zeros = torch.zeros(64)
        guarded_zeros = VHMObjective.apply_epsilon_guard(all_zeros, eps=0.1)
        assert torch.allclose(guarded_zeros, torch.full((64,), 0.1))

    def test_vhm_loss_calculation(self, default_config):
        vhm = VHMObjective(default_config)

        # Synthetic center and representations
        center = torch.ones(default_config.hidden_size)
        # Identical representation -> zero loss
        exact_rep = torch.ones(2, default_config.hidden_size)
        zero_loss = vhm.compute_loss(exact_rep, center)
        assert torch.isclose(zero_loss, torch.tensor(0.0), atol=1e-6)

        # Distant representation -> positive loss
        distant_rep = torch.ones(2, default_config.hidden_size) + 2.0
        # Euclidean distance squared per element = 2^2 * hidden_size
        expected = 4.0 * default_config.hidden_size
        pos_loss = vhm.compute_loss(distant_rep, center)
        assert torch.isclose(pos_loss, torch.tensor(expected), atol=1e-4)

    def test_vhm_loss_gradient_flow(self, default_config):
        model = LogBERT(default_config)
        model.train()
        vhm = VHMObjective(default_config)

        center = torch.randn(default_config.hidden_size)
        input_ids = torch.tensor([[2, 5, 6, 7, 3]])

        output = model(input_ids)
        loss = vhm.compute_loss(output.cls_representation, center)

        assert loss.item() > 0.0
        loss.backward()

        # Check gradients exist on encoder parameters
        assert model.encoder.layers[0].attention.query.weight.grad is not None


class TestJointLossAndEndToEnd:
    """Verify joint multi-task loss, backward pass, serialization, and determinism."""

    def test_joint_loss_computation_and_backward(self, default_config):
        model = LogBERT(default_config)
        model.train()

        center = torch.randn(default_config.hidden_size)
        joint_loss_fn = LogBERTJointLoss(default_config, center=center)

        input_ids = torch.tensor([
            [2, 5, 6, 7, 8, 3],
            [2, 10, 11, 12, 13, 3],
        ])

        masked_ids, labels = joint_loss_fn.mlkp.mask_tokens(input_ids)
        output = model(masked_ids)

        losses = joint_loss_fn(
            logits=output.logits,
            labels=labels,
            cls_representation=output.cls_representation,
        )

        assert "total_loss" in losses
        assert "mlkp_loss" in losses
        assert "vhm_loss" in losses

        total_loss = losses["total_loss"]
        assert total_loss.item() > 0.0
        assert not torch.isnan(total_loss)
        assert not torch.isinf(total_loss)

        # Expected combination: total = mlkp + alpha * vhm
        expected = losses["mlkp_loss"] + default_config.vhm_alpha * losses["vhm_loss"]
        assert torch.isclose(total_loss, expected, atol=1e-5)

        # Full backward pass
        total_loss.backward()
        for p in model.parameters():
            if p.requires_grad:
                assert p.grad is not None

    def test_model_state_dict_save_and_load(self, default_config, tmp_path: Path):
        model = LogBERT(default_config)
        model.eval()

        input_ids = torch.tensor([[2, 5, 6, 7, 3]])
        with torch.no_grad():
            orig_output = model(input_ids)

        save_file = tmp_path / "logbert_checkpoint.pt"
        torch.save(model.state_dict(), save_file)
        assert save_file.exists()

        # Load into fresh model instance
        new_model = LogBERT(default_config)
        new_model.load_state_dict(torch.load(save_file, weights_only=True))
        new_model.eval()

        with torch.no_grad():
            new_output = new_model(input_ids)

        assert torch.allclose(orig_output.logits, new_output.logits)
        assert torch.allclose(orig_output.cls_representation, new_output.cls_representation)

    def test_seeded_reproducibility(self, default_config):
        torch.manual_seed(12345)
        model1 = LogBERT(default_config)
        model1.eval()

        torch.manual_seed(12345)
        model2 = LogBERT(default_config)
        model2.eval()

        input_ids = torch.tensor([[2, 5, 6, 7, 3]])
        with torch.no_grad():
            out1 = model1(input_ids)
            out2 = model2(input_ids)

        assert torch.equal(out1.logits, out2.logits)
        assert torch.equal(out1.cls_representation, out2.cls_representation)
