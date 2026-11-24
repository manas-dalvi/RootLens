"""
Event vocabulary representation for LogBERT.
Maps structured log event keys/templates to dense numerical tokens.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union


class EventVocab:
    """
    Vocabulary mapping for LogBERT event IDs.
    Reserves indices 0..4 for standard BERT special tokens:
        0: [PAD]
        1: [UNK]
        2: [CLS]
        3: [SEP]
        4: [MASK]
    """

    PAD_TOKEN = "[PAD]"
    UNK_TOKEN = "[UNK]"
    CLS_TOKEN = "[CLS]"
    DIST_TOKEN = "[DIST]"  # LogBERT distance token (semantically equivalent alias of CLS_TOKEN)
    SEP_TOKEN = "[SEP]"
    MASK_TOKEN = "[MASK]"

    SPECIAL_TOKENS = [PAD_TOKEN, UNK_TOKEN, CLS_TOKEN, SEP_TOKEN, MASK_TOKEN]

    def __init__(self, token2id: Optional[Dict[str, int]] = None):
        if token2id is not None:
            self._token2id = dict(token2id)
            self._id2token = {idx: token for token, idx in self._token2id.items()}
        else:
            self._token2id = {token: idx for idx, token in enumerate(self.SPECIAL_TOKENS)}
            self._id2token = {idx: token for token, idx in self._token2id.items()}

    @property
    def pad_id(self) -> int:
        return self._token2id[self.PAD_TOKEN]

    @property
    def unk_id(self) -> int:
        return self._token2id[self.UNK_TOKEN]

    @property
    def cls_id(self) -> int:
        return self._token2id[self.CLS_TOKEN]

    @property
    def dist_id(self) -> int:
        """Alias of cls_id corresponding to LogBERT [DIST] sequence token."""
        return self._token2id.get(self.DIST_TOKEN, self.cls_id)

    @property
    def sep_id(self) -> int:
        return self._token2id[self.SEP_TOKEN]

    @property
    def mask_id(self) -> int:
        return self._token2id[self.MASK_TOKEN]

    def __len__(self) -> int:
        return len(self._token2id)

    @property
    def vocab_size(self) -> int:
        return len(self._token2id)

    def add_token(self, token: str) -> int:
        """Add a token to the vocabulary if not already present."""
        if token not in self._token2id:
            idx = len(self._token2id)
            self._token2id[token] = idx
            self._id2token[idx] = token
            return idx
        return self._token2id[token]

    def fit(self, events: Iterable[str]) -> EventVocab:
        """Fit vocabulary on an iterable of event identifiers."""
        # Sort for determinism
        unique_events = sorted(set(events))
        for event in unique_events:
            if event not in self._token2id:
                self.add_token(event)
        return self

    def token_to_id(self, token: str) -> int:
        """Get ID for token, returning UNK ID if token is unseen. Supports [DIST] as alias for [CLS]."""
        if token == self.DIST_TOKEN:
            return self.cls_id
        return self._token2id.get(token, self.unk_id)

    def id_to_token(self, idx: int) -> str:
        """Get token string for token ID, returning [UNK] if out of bounds."""
        return self._id2token.get(idx, self.UNK_TOKEN)

    def encode(self, tokens: List[str], add_special_tokens: bool = False) -> List[int]:
        """Convert a sequence of event tokens to token IDs."""
        ids = [self.token_to_id(t) for t in tokens]
        if add_special_tokens:
            return [self.cls_id] + ids + [self.sep_id]
        return ids

    def decode(self, ids: List[int], skip_special_tokens: bool = False) -> List[str]:
        """Convert token IDs back to token strings."""
        tokens = [self.id_to_token(i) for i in ids]
        if skip_special_tokens:
            return [t for t in tokens if t not in self.SPECIAL_TOKENS]
        return tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "token2id": self._token2id,
            "special_tokens": {
                "pad": self.pad_id,
                "unk": self.unk_id,
                "cls": self.cls_id,
                "sep": self.sep_id,
                "mask": self.mask_id,
            },
            "vocab_size": len(self),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EventVocab:
        token2id = data["token2id"]
        # Ensure integer keys
        token2id = {k: int(v) for k, v in token2id.items()}
        return cls(token2id=token2id)

    def save(self, file_path: Union[str, Path]) -> None:
        """Save vocabulary to JSON file."""
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)

    @classmethod
    def load(cls, file_path: Union[str, Path]) -> EventVocab:
        """Load vocabulary from JSON file."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)
