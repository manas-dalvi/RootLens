"""
LogBERT Self-Supervised Trainer.
Handles dataset batching, hypersphere center initialization, joint MLKP + VHM training,
validation tracking, and checkpoint persistence on CPU.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.dataset import SessionSequence
from app.ml.logbert.model import LogBERT
from app.ml.logbert.objectives import LogBERTJointLoss, VHMObjective
from app.ml.logbert.scoring import compute_sequence_anomaly_score, find_validation_threshold
from app.ml.logbert.vocab import EventVocab

logger = logging.getLogger(__name__)


class LogBERTDataset(Dataset):
    """
    PyTorch Dataset for LogBERT log sequences.
    Transforms SessionSequence objects into token ID tensors with [CLS]/[DIST] and [SEP] wrappers.
    """
    def __init__(
        self,
        sessions: List[SessionSequence],
        vocab: EventVocab,
        max_seq_len: int = 64,
    ):
        self.sessions = sessions
        self.vocab = vocab
        self.max_seq_len = max_seq_len

    def __len__(self) -> int:
        return len(self.sessions)

    def __getitem__(self, idx: int) -> Dict[str, Any]:
        session = self.sessions[idx]
        event_ids = [self.vocab.token_to_id(e) for e in session.events]

        # Truncate if sequence exceeds max_seq_len - 2 (reserving space for [CLS] and [SEP])
        max_events = self.max_seq_len - 2
        if len(event_ids) > max_events:
            event_ids = event_ids[:max_events]

        # Assemble: [CLS/DIST] + events + [SEP]
        token_sequence = [self.vocab.cls_id] + event_ids + [self.vocab.sep_id]
        seq_len = len(token_sequence)

        # Pad up to max_seq_len
        pad_len = self.max_seq_len - seq_len
        padded_tokens = token_sequence + [self.vocab.pad_id] * pad_len
        attention_mask = [1] * seq_len + [0] * pad_len

        return {
            "input_ids": torch.tensor(padded_tokens, dtype=torch.long),
            "attention_mask": torch.tensor(attention_mask, dtype=torch.long),
            "block_id": session.block_id,
            "label": session.label,
            "seq_len": seq_len,
        }


class LogBERTTrainer:
    """
    Coordinates self-supervised training of LogBERT via joint MLKP + VHM optimization.
    """
    def __init__(
        self,
        model: LogBERT,
        config: LogBERTConfig,
        lr: float = 1e-3,
        weight_decay: float = 0.01,
        device: str = "cpu",
        seed: int = 42,
    ):
        self.model = model.to(device)
        self.config = config
        self.device = device
        self.seed = seed

        torch.manual_seed(seed)
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=lr,
            weight_decay=weight_decay,
        )
        self.loss_fn = LogBERTJointLoss(config)
        self.center: Optional[torch.Tensor] = None

    def initialize_center_from_loader(
        self,
        train_loader: DataLoader,
        eps: float = 0.1,
    ) -> torch.Tensor:
        """
        Compute the initial hypersphere center c as the empirical centroid
        of normal training sequence representations.
        """
        self.model.eval()
        representations: List[torch.Tensor] = []

        with torch.no_grad():
            for batch in train_loader:
                # Invariant check: training batch must contain ONLY normal sequences
                labels = batch["label"]
                if (labels != 0).any():
                    raise ValueError("DATA LEAKAGE: Anomaly session found in training loader during center initialization!")

                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)
                output = self.model(input_ids, attention_mask=attention_mask)
                representations.append(output.dist_representation.cpu())

        all_reps = torch.cat(representations, dim=0)
        center = torch.mean(all_reps, dim=0)
        center = VHMObjective.apply_epsilon_guard(center, eps=eps)

        self.center = center.to(self.device)
        self.loss_fn.set_center(self.center)
        return self.center

    def train_epoch(self, train_loader: DataLoader) -> Dict[str, float]:
        """Execute one training epoch over normal sessions."""
        self.model.train()
        total_loss_accum = 0.0
        mlkp_loss_accum = 0.0
        vhm_loss_accum = 0.0
        num_batches = 0

        for batch in train_loader:
            # Verify training labels are strictly normal
            if (batch["label"] != 0).any():
                raise ValueError("DATA LEAKAGE: Anomalous session present in training step!")

            input_ids = batch["input_ids"].to(self.device)
            attention_mask = batch["attention_mask"].to(self.device)

            # 1. Mask tokens according to MLKP objective
            masked_ids, labels = self.loss_fn.mlkp.mask_tokens(input_ids)

            # 2. Forward pass
            output = self.model(masked_ids, attention_mask=attention_mask)

            # 3. Compute joint MLKP + VHM loss
            losses = self.loss_fn(
                logits=output.logits,
                labels=labels,
                cls_representation=output.dist_representation,
            )

            total_loss = losses["total_loss"]

            # 4. Backward & Optimize
            self.optimizer.zero_grad()
            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
            self.optimizer.step()

            total_loss_accum += total_loss.item()
            mlkp_loss_accum += losses["mlkp_loss"].item()
            vhm_loss_accum += losses["vhm_loss"].item()
            num_batches += 1

        denom = max(num_batches, 1)
        return {
            "total_loss": round(total_loss_accum / denom, 5),
            "mlkp_loss": round(mlkp_loss_accum / denom, 5),
            "vhm_loss": round(vhm_loss_accum / denom, 5),
        }

    def evaluate_validation(self, val_loader: DataLoader) -> Dict[str, Any]:
        """
        Evaluate validation split to calculate validation loss and derive
        anomaly threshold from validation scores.
        """
        self.model.eval()
        val_losses: List[float] = []
        val_scores: List[float] = []
        val_labels: List[int] = []

        with torch.no_grad():
            for batch in val_loader:
                input_ids = batch["input_ids"].to(self.device)
                attention_mask = batch["attention_mask"].to(self.device)
                labels = batch["label"].tolist()

                # Evaluate loss on normal validation sequences
                normal_idx = [i for i, y in enumerate(labels) if y == 0]
                if normal_idx:
                    norm_ids = input_ids[normal_idx]
                    norm_mask = attention_mask[normal_idx]
                    masked_ids, mlkp_labels = self.loss_fn.mlkp.mask_tokens(norm_ids)
                    output = self.model(masked_ids, attention_mask=norm_mask)
                    losses = self.loss_fn(
                        logits=output.logits,
                        labels=mlkp_labels,
                        cls_representation=output.dist_representation,
                    )
                    val_losses.append(losses["total_loss"].item())

                # Batched forward pass for sequence anomaly scoring
                batch_output = self.model(input_ids, attention_mask=attention_mask)
                center_ref = self.center if self.center is not None else torch.zeros(self.config.hidden_size, device=self.device)
                vhm_dists = torch.sum((batch_output.dist_representation - center_ref) ** 2, dim=-1)

                probs = F.softmax(batch_output.logits, dim=-1)
                top_k = min(6, self.config.vocab_size)
                _, top_candidates = torch.topk(probs, k=top_k, dim=-1)

                for i in range(input_ids.size(0)):
                    seq_tokens = input_ids[i]
                    eval_mask = (
                        (seq_tokens != self.config.pad_token_id)
                        & (seq_tokens != self.config.cls_token_id)
                        & (seq_tokens != self.config.sep_token_id)
                    )
                    eval_indices = eval_mask.nonzero(as_tuple=False).view(-1)
                    num_events = len(eval_indices)
                    mismatches = 0
                    if num_events > 0:
                        for idx in eval_indices:
                            true_tok = seq_tokens[idx].item()
                            if true_tok not in top_candidates[i, idx].tolist():
                                mismatches += 1
                        mismatch_ratio = mismatches / num_events
                    else:
                        mismatch_ratio = 0.0

                    vhm_dist = float(vhm_dists[i].item())
                    composite = mismatch_ratio + self.config.vhm_alpha * vhm_dist
                    val_scores.append(round(composite, 4))
                    val_labels.append(labels[i])

        mean_val_loss = round(sum(val_losses) / len(val_losses), 5) if val_losses else 0.0

        # Select anomaly threshold using validation scores only
        thresh_info = find_validation_threshold(val_scores, val_labels)

        # Normal vs anomaly mean score breakdown
        norm_scores = [s for s, y in zip(val_scores, val_labels) if y == 0]
        anom_scores = [s for s, y in zip(val_scores, val_labels) if y == 1]

        return {
            "val_loss": mean_val_loss,
            "threshold": thresh_info["threshold"],
            "val_f1": thresh_info["val_f1"],
            "val_precision": thresh_info["val_precision"],
            "val_recall": thresh_info["val_recall"],
            "mean_normal_score": round(sum(norm_scores) / len(norm_scores), 4) if norm_scores else 0.0,
            "mean_anomaly_score": round(sum(anom_scores) / len(anom_scores), 4) if anom_scores else 0.0,
            "total_val_evaluated": len(val_scores),
        }

    def train(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        epochs: int = 10,
        patience: int = 3,
        checkpoint_dir: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """
        Run the complete self-supervised training loop.
        """
        start_time = time.time()

        # Step 1: Initialize center from normal training sequences
        if self.center is None:
            self.initialize_center_from_loader(train_loader, eps=self.config.vhm_eps)

        best_val_loss = float("inf")
        best_epoch = 0
        best_val_metrics: Dict[str, Any] = {}
        best_state_dict: Optional[Dict[str, Any]] = None
        epochs_without_improvement = 0
        loss_history: List[Dict[str, Any]] = []

        out_path = Path(checkpoint_dir) if checkpoint_dir else None
        if out_path:
            out_path.mkdir(parents=True, exist_ok=True)

        for epoch in range(1, epochs + 1):
            epoch_start = time.time()
            train_metrics = self.train_epoch(train_loader)
            val_metrics = self.evaluate_validation(val_loader)
            epoch_duration = round(time.time() - epoch_start, 2)

            record = {
                "epoch": epoch,
                "duration_seconds": epoch_duration,
                "train_total_loss": train_metrics["total_loss"],
                "train_mlkp_loss": train_metrics["mlkp_loss"],
                "train_vhm_loss": train_metrics["vhm_loss"],
                "val_loss": val_metrics["val_loss"],
                "val_threshold": val_metrics["threshold"],
                "val_f1": val_metrics["val_f1"],
                "mean_normal_score": val_metrics["mean_normal_score"],
                "mean_anomaly_score": val_metrics["mean_anomaly_score"],
            }
            loss_history.append(record)

            # Checkpoint on validation improvement
            val_loss = val_metrics["val_loss"]
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_epoch = epoch
                best_val_metrics = val_metrics
                best_state_dict = {k: v.cpu().clone() for k, v in self.model.state_dict().items()}
                epochs_without_improvement = 0

                if out_path:
                    self.save_checkpoint(out_path / "logbert_best.pt", val_metrics)
            else:
                epochs_without_improvement += 1
                if epochs_without_improvement >= patience:
                    break

        # Restore model to best checkpoint weights
        if best_state_dict is not None:
            self.model.load_state_dict(best_state_dict)
            self.model.eval()

        total_runtime = round(time.time() - start_time, 2)

        return {
            "total_runtime_seconds": total_runtime,
            "completed_epochs": len(loss_history),
            "best_epoch": best_epoch,
            "best_val_loss": best_val_loss,
            "best_val_metrics": best_val_metrics,
            "loss_history": loss_history,
        }

    def save_checkpoint(
        self,
        checkpoint_path: Union[str, Path],
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save model weights, configuration, hypersphere center, and optimizer state."""
        path = Path(checkpoint_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "model_state_dict": self.model.state_dict(),
            "config": self.config.to_dict(),
            "center": self.center.cpu() if self.center is not None else None,
            "optimizer_state_dict": self.optimizer.state_dict(),
            "metadata": extra_metadata or {},
        }
        torch.save(payload, path)

    @classmethod
    def load_checkpoint(
        cls,
        checkpoint_path: Union[str, Path],
        device: str = "cpu",
    ) -> Tuple[LogBERT, LogBERTConfig, torch.Tensor, Dict[str, Any]]:
        """Load saved checkpoint into a fresh LogBERT model instance."""
        payload = torch.load(checkpoint_path, map_location=device, weights_only=False)
        config = LogBERTConfig.from_dict(payload["config"])
        model = LogBERT(config).to(device)
        model.load_state_dict(payload["model_state_dict"])
        model.eval()

        center = payload.get("center")
        if center is not None:
            center = center.to(device)
        metadata = payload.get("metadata", {})

        return model, config, center, metadata
