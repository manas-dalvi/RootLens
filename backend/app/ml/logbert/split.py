"""
Deterministic dataset partitioning for LogBERT self-supervised training.
Guarantees:
1. Zero label leakage: Training split contains strictly normal sessions (label == 0).
2. Zero data leakage: Disjoint block IDs across train, val, and test partitions.
3. Deterministic reproducibility via explicit random seed.
"""

from __future__ import annotations

import random
from typing import Dict, List, Set, Tuple

from app.ml.logbert.dataset import SessionSequence


def split_sessions(
    sessions: List[SessionSequence],
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
    seed: int = 42,
) -> Tuple[List[SessionSequence], List[SessionSequence], List[SessionSequence]]:
    """
    Split session sequences into train, validation, and test sets.

    Parameters:
        sessions: Full list of SessionSequence objects.
        train_ratio: Fraction of normal sessions allocated to training (default: 0.8).
        val_ratio: Fraction of normal sessions allocated to validation (default: 0.1).
        test_ratio: Fraction of normal sessions allocated to test (default: 0.1).
        seed: Random seed for deterministic shuffling.

    Returns:
        Tuple of (train_sessions, val_sessions, test_sessions).
    """
    if not (0.999 <= (train_ratio + val_ratio + test_ratio) <= 1.001):
        raise ValueError(
            f"Split ratios must sum to 1.0, got {train_ratio + val_ratio + test_ratio}"
        )
    if train_ratio <= 0 or val_ratio < 0 or test_ratio < 0:
        raise ValueError("Split ratios must be non-negative, and train_ratio must be positive.")

    # 1. Separate normal and anomalous sessions
    normal_sessions = [s for s in sessions if s.label == 0]
    anomaly_sessions = [s for s in sessions if s.label == 1]

    rng = random.Random(seed)

    # 2. Deterministically shuffle both groups
    shuffled_normal = list(normal_sessions)
    shuffled_anomaly = list(anomaly_sessions)
    rng.shuffle(shuffled_normal)
    rng.shuffle(shuffled_anomaly)

    # 3. Partition normal sessions
    num_normal = len(shuffled_normal)
    n_train_norm = int(round(num_normal * train_ratio))
    n_val_norm = int(round(num_normal * val_ratio))
    # Ensure test gets the remainder to avoid rounding off-by-one errors
    train_norm = shuffled_normal[:n_train_norm]
    val_norm = shuffled_normal[n_train_norm : n_train_norm + n_val_norm]
    test_norm = shuffled_normal[n_train_norm + n_val_norm :]

    # 4. Partition anomalous sessions
    # STRICT RULE: Training split receives ZERO anomaly sessions!
    num_anomaly = len(shuffled_anomaly)
    if num_anomaly > 0:
        # Divide anomalies proportionally between validation and test
        eval_sum = val_ratio + test_ratio
        if eval_sum > 0:
            val_anomaly_ratio = val_ratio / eval_sum
        else:
            val_anomaly_ratio = 0.5
        n_val_anom = int(round(num_anomaly * val_anomaly_ratio))
        val_anom = shuffled_anomaly[:n_val_anom]
        test_anom = shuffled_anomaly[n_val_anom:]
    else:
        val_anom = []
        test_anom = []

    # 5. Assemble final partitions
    train_sessions = train_norm  # Exactly 100% normal
    val_sessions = val_norm + val_anom
    test_sessions = test_norm + test_anom

    # Re-shuffle validation and test so anomalies aren't clustered at the end
    rng.shuffle(val_sessions)
    rng.shuffle(test_sessions)

    # 6. Strict validation assertion checks
    _validate_split_integrity(
        all_sessions=sessions,
        train=train_sessions,
        val=val_sessions,
        test=test_sessions,
    )

    return train_sessions, val_sessions, test_sessions


def _validate_split_integrity(
    all_sessions: List[SessionSequence],
    train: List[SessionSequence],
    val: List[SessionSequence],
    test: List[SessionSequence],
) -> None:
    """Verify all isolation and leakage invariants."""
    # Invariant 1: No anomaly sessions in train
    train_anomalies = [s for s in train if s.label != 0]
    if train_anomalies:
        raise ValueError(
            f"DATA LEAKAGE: Training split contains {len(train_anomalies)} anomalous sessions!"
        )

    # Invariant 2: Block IDs are mutually disjoint
    train_blocks: Set[str] = {s.block_id for s in train}
    val_blocks: Set[str] = {s.block_id for s in val}
    test_blocks: Set[str] = {s.block_id for s in test}

    leak_train_val = train_blocks.intersection(val_blocks)
    if leak_train_val:
        raise ValueError(f"DATA LEAKAGE: {len(leak_train_val)} block IDs appear in both train and val!")

    leak_train_test = train_blocks.intersection(test_blocks)
    if leak_train_test:
        raise ValueError(f"DATA LEAKAGE: {len(leak_train_test)} block IDs appear in both train and test!")

    leak_val_test = val_blocks.intersection(test_blocks)
    if leak_val_test:
        raise ValueError(f"DATA LEAKAGE: {len(leak_val_test)} block IDs appear in both val and test!")

    # Invariant 3: Total count conservation
    total_split = len(train) + len(val) + len(test)
    if total_split != len(all_sessions):
        raise ValueError(
            f"COUNT MISMATCH: Original sessions ({len(all_sessions)}) != sum of splits ({total_split})"
        )


def compute_split_metrics(
    train: List[SessionSequence],
    val: List[SessionSequence],
    test: List[SessionSequence],
) -> Dict[str, Dict[str, int | float]]:
    """Compute summary metrics and class distributions for the splits."""
    def _metrics(subset: List[SessionSequence]) -> Dict[str, int | float]:
        total = len(subset)
        normal = sum(1 for s in subset if s.label == 0)
        anomaly = sum(1 for s in subset if s.label == 1)
        anomaly_rate = (anomaly / total * 100.0) if total > 0 else 0.0
        return {
            "total_sessions": total,
            "normal_sessions": normal,
            "anomaly_sessions": anomaly,
            "anomaly_rate_percent": round(anomaly_rate, 2),
        }

    return {
        "train": _metrics(train),
        "val": _metrics(val),
        "test": _metrics(test),
        "overall": _metrics(train + val + test),
    }
