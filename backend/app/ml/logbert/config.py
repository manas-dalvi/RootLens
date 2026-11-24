"""
Configuration class for LogBERT model architecture and self-supervised objectives.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union


@dataclass
class LogBERTConfig:
    """
    Configuration parameters for LogBERT model and self-supervised objectives.
    """
    # Architecture hyperparameters
    vocab_size: int = 19  # Default matches HDFS benchmark vocabulary (14 templates + 5 special tokens)
    max_seq_len: int = 64
    hidden_size: int = 128
    num_hidden_layers: int = 4
    num_attention_heads: int = 4
    intermediate_size: int = 512
    hidden_dropout_prob: float = 0.1
    attention_probs_dropout_prob: float = 0.1
    hidden_act: str = "gelu"
    layer_norm_eps: float = 1e-12

    # Special token IDs (strictly matches Phase 1 vocabulary mapping)
    pad_token_id: int = 0
    unk_token_id: int = 1
    cls_token_id: int = 2
    dist_token_id: int = 2                # LogBERT [DIST] sequence distance token (alias of cls_token_id)
    sep_token_id: int = 3
    mask_token_id: int = 4

    # MLKP (Masked Log Key Prediction) objective hyperparameters
    mlkp_mask_prob: float = 0.15          # Standard BERT 15% masking rate
    mlkp_replace_prob: float = 0.80       # 80% replaced with [MASK]
    mlkp_random_prob: float = 0.10        # 10% replaced with random token
    mlkp_keep_prob: float = 0.10          # 10% kept unchanged

    # VHM (Volume of Hypersphere Minimization) objective hyperparameters
    vhm_alpha: float = 0.1                # Loss weighting hyperparameter: L_total = L_mlkp + alpha * L_vhm
    vhm_eps: float = 0.1                  # Center boundary epsilon to prevent collapse to origin

    def __post_init__(self) -> None:
        if self.hidden_size % self.num_attention_heads != 0:
            raise ValueError(
                f"hidden_size ({self.hidden_size}) must be divisible by "
                f"num_attention_heads ({self.num_attention_heads})"
            )
        if self.vocab_size <= self.mask_token_id:
            raise ValueError(
                f"vocab_size ({self.vocab_size}) must be greater than highest special token id ({self.mask_token_id})"
            )

    @property
    def attention_head_size(self) -> int:
        return self.hidden_size // self.num_attention_heads

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LogBERTConfig:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, file_path: Union[str, Path]) -> None:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, file_path: Union[str, Path]) -> LogBERTConfig:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
