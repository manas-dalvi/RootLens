"""
Self-Supervised Objectives for LogBERT:
1. Masked Log Key Prediction (MLKP)
2. Volume of Hypersphere Minimization (VHM)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Union

import torch
import torch.nn as nn
import torch.nn.functional as F

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.model import LogBERT


class MLKPObjective:
    """
    Masked Log Key Prediction (MLKP) self-supervised objective.
    Randomly masks event tokens and computes cross-entropy prediction loss.
    """
    def __init__(self, config: LogBERTConfig):
        self.config = config

    def mask_tokens(
        self,
        input_ids: torch.Tensor,
        generator: Optional[torch.Generator] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Create masked input IDs and target labels following BERT/LogBERT 80/10/10 rule.

        Parameters:
            input_ids: [batch_size, seq_len] original token sequence.
            generator: Optional torch.Generator for deterministic reproducibility.

        Returns:
            masked_input_ids: [batch_size, seq_len] with masked tokens.
            labels: [batch_size, seq_len] with -100 for non-masked tokens, original token ID for masked.
        """
        masked_input_ids = input_ids.clone()
        labels = torch.full_like(input_ids, fill_value=-100)

        # Identify candidate positions for masking:
        # Exclude special tokens: [PAD], [CLS], [SEP]
        special_mask = (
            (input_ids == self.config.pad_token_id)
            | (input_ids == self.config.cls_token_id)
            | (input_ids == self.config.sep_token_id)
        )
        candidates = ~special_mask

        # Generate random probability matrix
        prob_matrix = torch.rand(input_ids.shape, generator=generator, device=input_ids.device)
        # Only mask candidate positions that exceed probability threshold
        mask_indices = candidates & (prob_matrix < self.config.mlkp_mask_prob)

        # For edge case where no candidate was masked, force at least one mask if candidate exists
        if not mask_indices.any() and candidates.any():
            candidate_indices = candidates.nonzero(as_tuple=False)
            if candidate_indices.size(0) > 0:
                random_idx = int(torch.randint(0, candidate_indices.size(0), (1,), generator=generator).item())
                chosen = candidate_indices[random_idx]
                mask_indices[chosen[0], chosen[1]] = True

        # Set target labels at masked positions
        labels[mask_indices] = input_ids[mask_indices]

        # 80% of masked positions -> replace with [MASK] token
        prob_replace = torch.rand(input_ids.shape, generator=generator, device=input_ids.device)
        indices_replaced = mask_indices & (prob_replace < self.config.mlkp_replace_prob)
        masked_input_ids[indices_replaced] = self.config.mask_token_id

        # 10% of masked positions -> replace with random valid event token (indices >= 5)
        # 0.8 <= prob_replace < 0.9
        indices_random = (
            mask_indices
            & (~indices_replaced)
            & (prob_replace < (self.config.mlkp_replace_prob + self.config.mlkp_random_prob))
        )
        if indices_random.any():
            num_events = self.config.vocab_size - 5
            if num_events > 0:
                random_tokens = torch.randint(
                    low=5,
                    high=self.config.vocab_size,
                    size=input_ids.shape,
                    generator=generator,
                    device=input_ids.device,
                )
            else:
                random_tokens = torch.full_like(input_ids, fill_value=self.config.unk_token_id)
            masked_input_ids[indices_random] = random_tokens[indices_random]

        # Remaining 10% of masked positions -> keep original token in masked_input_ids

        return masked_input_ids, labels

    def compute_loss(
        self,
        logits: torch.Tensor,
        labels: torch.Tensor,
    ) -> torch.Tensor:
        """
        Compute cross-entropy loss on masked token positions.

        Parameters:
            logits: [batch_size, seq_len, vocab_size]
            labels: [batch_size, seq_len] with -100 for ignored positions.
        """
        active_loss = labels.view(-1) != -100
        if not active_loss.any():
            return torch.tensor(0.0, device=logits.device, requires_grad=True)

        return F.cross_entropy(
            logits.view(-1, self.config.vocab_size),
            labels.view(-1),
            ignore_index=-100,
        )


class VHMObjective:
    """
    Volume of Hypersphere Minimization (VHM) self-supervised objective.
    Encourages latent normal log sequence representations to concentrate
    within a compact hypersphere centered at point c.
    """
    def __init__(self, config: LogBERTConfig):
        self.config = config

    @staticmethod
    def apply_epsilon_guard(center: torch.Tensor, eps: float = 0.1) -> torch.Tensor:
        """
        Deep SVDD / LogBERT numerical stability guard against hypersphere center collapse.
        For any dimension j where |c_j| < eps:
            c_j = +eps if c_j >= 0 else -eps

        Guarantees that coordinates exactly equal to 0.0 or within (-eps, eps)
        are bounded away from zero (fixing the sign(0) == 0 collapse bug).
        """
        center = center.clone()
        near_zero = torch.abs(center) < eps
        if near_zero.any():
            # Robust sign logic: values >= 0 (including 0.0) map to +1.0, values < 0 map to -1.0
            signs = torch.where(
                center >= 0.0,
                torch.tensor(1.0, dtype=center.dtype, device=center.device),
                torch.tensor(-1.0, dtype=center.dtype, device=center.device),
            )
            center[near_zero] = signs[near_zero] * eps
        return center

    @staticmethod
    def initialize_center(
        model: LogBERT,
        normal_sequences: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
        eps: float = 0.1,
    ) -> torch.Tensor:
        """
        Initialize the hypersphere center c as the mean representation of normal sequences.
        Includes numerical stability bounding to prevent representation collapse to origin.

        METHODOLOGY & TRAINING STAGE:
        1. Training Stage: Center c is computed once from normal training representations
           prior to joint optimization (or after initial MLKP warm-up). It remains a FIXED
           reference point throughout training (not updated by gradient descent).
        2. Normal Training Data Invariant: normal_sequences MUST contain strictly normal
           training sessions (label == 0). It must NEVER include anomalous or validation/test sessions.

        Parameters:
            model: LogBERT model.
            normal_sequences: [N, seq_len] normal training sequence token IDs.
            attention_mask: Optional [N, seq_len] mask.
            eps: Minimum dimension offset to avoid collapse near origin.

        Returns:
            center: [hidden_size] fixed hypersphere center tensor.
        """
        model.eval()
        with torch.no_grad():
            output = model(normal_sequences, attention_mask=attention_mask)
            # output.cls_representation / dist_representation: [N, hidden_size]
            center = torch.mean(output.cls_representation, dim=0)

        # Apply numerical stability guard
        center = VHMObjective.apply_epsilon_guard(center, eps=eps)
        return center.detach()

    def compute_loss(
        self,
        cls_representation: torch.Tensor,
        center: torch.Tensor,
    ) -> torch.Tensor:
        """
        Compute VHM loss: mean squared Euclidean distance from representations to center.

        Formula:
            L_VHM = (1 / B) * sum_{i=1}^B || h_i - c ||_2^2

        Parameters:
            cls_representation: [batch_size, hidden_size]
            center: [hidden_size]
        """
        diff = cls_representation - center.unsqueeze(0)
        dist_sq = torch.sum(diff ** 2, dim=-1)
        return torch.mean(dist_sq)


class LogBERTJointLoss(nn.Module):
    """
    Combined multi-task loss for LogBERT:
        L_total = L_MLKP + alpha * L_VHM
    """
    def __init__(self, config: LogBERTConfig, center: Optional[torch.Tensor] = None):
        super().__init__()
        self.config = config
        self.mlkp = MLKPObjective(config)
        self.vhm = VHMObjective(config)

        if center is not None:
            self.register_buffer("center", center.clone().detach())
        else:
            self.register_buffer("center", None)

    def set_center(self, center: torch.Tensor) -> None:
        """Set or update the hypersphere center tensor."""
        self.register_buffer("center", center.clone().detach())

    def forward(
        self,
        logits: torch.Tensor,
        labels: torch.Tensor,
        cls_representation: torch.Tensor,
    ) -> Dict[str, torch.Tensor]:
        """
        Compute joint loss components.

        Returns:
            Dictionary containing 'total_loss', 'mlkp_loss', and 'vhm_loss'.
        """
        mlkp_loss = self.mlkp.compute_loss(logits, labels)

        if self.center is not None:
            vhm_loss = self.vhm.compute_loss(cls_representation, self.center)
            total_loss = mlkp_loss + self.config.vhm_alpha * vhm_loss
        else:
            vhm_loss = torch.tensor(0.0, device=logits.device)
            total_loss = mlkp_loss

        return {
            "total_loss": total_loss,
            "mlkp_loss": mlkp_loss,
            "vhm_loss": vhm_loss,
        }
