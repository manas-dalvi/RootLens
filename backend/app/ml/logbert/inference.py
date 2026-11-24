"""
Production LogBERT Inference Engine.
Loads trained checkpoints safely, applies evaluation mode on CPU,
encodes log sequences, and generates deterministic anomaly scores and predictions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import torch
import torch.nn.functional as F

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.model import LogBERT
from app.ml.logbert.scoring import compute_sequence_anomaly_score
from app.ml.logbert.trainer import LogBERTTrainer
from app.ml.logbert.vocab import EventVocab

logger = logging.getLogger(__name__)


@dataclass
class LogBERTInferenceResult:
    """Structured inference result for an evaluated log event sequence."""

    session_id: Optional[str]
    is_anomaly: bool
    composite_score: float
    threshold: float
    vhm_distance: float
    mismatch_count: int
    mismatch_ratio: float
    mean_nll: float
    num_events: int
    events: List[str]
    explanation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert dataclass to dictionary."""
        return asdict(self)


class LogBERTInferenceEngine:
    """
    Thread-safe, CPU-based LogBERT inference engine.
    Loads trained checkpoint weights, restores VHM hypersphere center and
    validation anomaly threshold, and evaluates log sequences in evaluation mode.
    """

    def __init__(
        self,
        checkpoint_path: Union[str, Path],
        vocab_path: Union[str, Path],
        device: str = "cpu",
        custom_threshold: Optional[float] = None,
    ) -> None:
        self.device = device
        self.checkpoint_path = Path(checkpoint_path)
        self.vocab_path = Path(vocab_path)

        if not self.checkpoint_path.exists():
            raise FileNotFoundError(f"LogBERT checkpoint file not found: {self.checkpoint_path}")
        if not self.vocab_path.exists():
            raise FileNotFoundError(f"LogBERT vocabulary file not found: {self.vocab_path}")

        # 1. Load Vocabulary
        self.vocab = EventVocab.load(self.vocab_path)

        # 2. Load Checkpoint and restore components
        self.model, self.config, self.center, self.metadata = LogBERTTrainer.load_checkpoint(
            self.checkpoint_path, device=self.device
        )
        self.model.eval()

        # 3. Ensure no gradients are tracked during inference
        for param in self.model.parameters():
            param.requires_grad = False

        if self.center is None:
            raise ValueError("LogBERT checkpoint does not contain a saved hypersphere center tensor.")

        # 4. Resolve Anomaly Decision Threshold
        if custom_threshold is not None:
            self.threshold = float(custom_threshold)
            logger.info("Using custom anomaly threshold: %.4f", self.threshold)
        elif "threshold" in self.metadata:
            self.threshold = float(self.metadata["threshold"])
            logger.info("Restored validation anomaly threshold from checkpoint: %.4f", self.threshold)
        else:
            self.threshold = 0.0388
            logger.warning("Checkpoint metadata lacks threshold; falling back to default %.4f", self.threshold)

        logger.info(
            "LogBERTInferenceEngine initialized successfully (hidden=%d, layers=%d, heads=%d, vocab=%d, device=%s)",
            self.config.hidden_size,
            self.config.num_hidden_layers,
            self.config.num_attention_heads,
            self.vocab.vocab_size,
            self.device,
        )

    def _encode_sequence(self, events: List[str]) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Encode a list of event token strings into padded input IDs and attention mask tensors.

        Handles:
        - Special tokens: prepends [DIST]/[CLS], appends [SEP].
        - Unknown tokens: mapped to [UNK] (ID 1).
        - Truncation: truncates to max_seq_len - 2 if input exceeds maximum capacity.
        - Padding: right-padded with [PAD] (ID 0) up to max_seq_len.
        """
        max_events = self.config.max_seq_len - 2
        truncated_events = events[:max_events] if len(events) > max_events else events

        token_ids = self.vocab.encode(truncated_events, add_special_tokens=True)

        seq_len = len(token_ids)
        pad_len = self.config.max_seq_len - seq_len

        input_ids = token_ids + [self.config.pad_token_id] * pad_len
        attention_mask = [1] * seq_len + [0] * pad_len

        return (
            torch.tensor([input_ids], dtype=torch.long, device=self.device),
            torch.tensor([attention_mask], dtype=torch.long, device=self.device),
        )

    def predict_sequence(
        self,
        events: List[str],
        session_id: Optional[str] = None,
        num_candidates: int = 6,
    ) -> LogBERTInferenceResult:
        """
        Evaluate a single event sequence for anomalies.

        Handles:
        - Empty event input: returns non-anomalous result safely.
        - Unknown event tokens: handled via [UNK] mapping.
        - Short sequences: evaluated safely using available event positions.
        """
        if not events:
            return LogBERTInferenceResult(
                session_id=session_id,
                is_anomaly=False,
                composite_score=0.0,
                threshold=self.threshold,
                vhm_distance=0.0,
                mismatch_count=0,
                mismatch_ratio=0.0,
                mean_nll=0.0,
                num_events=0,
                events=[],
                explanation={
                    "explanation_type": "NORMAL",
                    "dominant_factor": "NONE",
                    "is_anomaly": False,
                    "composite_score": 0.0,
                    "threshold": round(self.threshold, 4),
                    "mlkp_contribution": 0.0,
                    "vhm_contribution": 0.0,
                    "mismatch_count": 0,
                    "total_evaluated_events": 0,
                    "mismatch_positions": [],
                    "token_explanations": [],
                    "vhm_evidence": {
                        "vhm_distance": 0.0,
                        "vhm_alpha": round(self.config.vhm_alpha, 4),
                        "vhm_contribution": 0.0,
                        "center_norm": round(float(torch.norm(self.center).item()), 4),
                        "explanation": "Empty sequence; no log events evaluated.",
                    },
                    "summary": "Empty sequence; no log events evaluated.",
                },
            )

        max_events = self.config.max_seq_len - 2
        truncated_events = events[:max_events] if len(events) > max_events else events
        input_ids, attention_mask = self._encode_sequence(events)

        with torch.no_grad():
            scores = compute_sequence_anomaly_score(
                model=self.model,
                input_ids=input_ids,
                attention_mask=attention_mask,
                center=self.center,
                num_candidates=num_candidates,
                alpha=self.config.vhm_alpha,
                vocab=self.vocab,
                original_events=truncated_events,
                threshold=self.threshold,
            )

        composite_score = scores["composite_score"]
        is_anomaly = bool(composite_score >= self.threshold)

        return LogBERTInferenceResult(
            session_id=session_id,
            is_anomaly=is_anomaly,
            composite_score=composite_score,
            threshold=self.threshold,
            vhm_distance=scores["vhm_distance"],
            mismatch_count=scores["mismatch_count"],
            mismatch_ratio=scores["mismatch_ratio"],
            mean_nll=scores["mean_nll"],
            num_events=scores["num_evaluated_events"],
            events=events,
            explanation=scores.get("explanation"),
        )

    def predict_batch(
        self,
        sequences: List[List[str]],
        session_ids: Optional[List[str]] = None,
        num_candidates: int = 6,
    ) -> List[LogBERTInferenceResult]:
        """Evaluate a batch of log sequences efficiently."""
        if not sequences:
            return []

        results: List[LogBERTInferenceResult] = []
        for i, seq in enumerate(sequences):
            s_id = session_ids[i] if session_ids and i < len(session_ids) else None
            res = self.predict_sequence(
                events=seq,
                session_id=s_id,
                num_candidates=num_candidates,
            )
            results.append(res)
        return results

    def get_model_info(self) -> Dict[str, Any]:
        """Return non-sensitive model metadata and runtime settings."""
        param_count = sum(p.numel() for p in self.model.parameters())
        return {
            "model_name": "LogBERT",
            "version": "1.0.0",
            "hidden_size": self.config.hidden_size,
            "num_hidden_layers": self.config.num_hidden_layers,
            "num_attention_heads": self.config.num_attention_heads,
            "vocab_size": self.vocab.vocab_size,
            "max_seq_len": self.config.max_seq_len,
            "total_parameters": param_count,
            "threshold": self.threshold,
            "vhm_alpha": self.config.vhm_alpha,
            "device": self.device,
            "center_norm": round(float(torch.norm(self.center).item()), 4),
            "status": "ready",
        }
