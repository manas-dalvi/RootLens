"""
End-to-end preprocessing pipeline for LogBERT benchmark datasets.
Transforms structured logs and labels into serialized train/val/test splits,
vocabulary mappings, and execution metadata.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.ml.logbert.dataset import (
    SessionSequence,
    build_session_sequences,
    save_sessions_json,
)
from app.ml.logbert.split import compute_split_metrics, split_sessions
from app.ml.logbert.vocab import EventVocab


def calculate_sequence_stats(sessions: List[SessionSequence]) -> Dict[str, Any]:
    """Compute distribution statistics for session sequence lengths."""
    lengths = sorted([len(s) for s in sessions])
    if not lengths:
        return {"min": 0, "max": 0, "mean": 0.0, "median": 0, "p95": 0, "p99": 0}

    total = sum(lengths)
    n = len(lengths)
    mean_val = round(total / n, 2)
    median_val = lengths[n // 2]
    p95_idx = min(int(n * 0.95), n - 1)
    p99_idx = min(int(n * 0.99), n - 1)

    return {
        "min": lengths[0],
        "max": lengths[-1],
        "mean": mean_val,
        "median": median_val,
        "p95": lengths[p95_idx],
        "p99": lengths[p99_idx],
        "total_events": total,
    }


def run_preprocessing(
    raw_data_dir: Union[str, Path] = "data/benchmarks/hdfs/raw",
    output_dir: Union[str, Path] = "data/benchmarks/hdfs/processed",
    log_filename: str = "HDFS_2k.log_structured.csv",
    label_filename: str = "anomaly_label.csv",
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Execute the complete preprocessing pipeline for HDFS:
    1. Parse structured logs and extract block sessions.
    2. Join with ground truth anomaly labels.
    3. Partition deterministically into train, validation, and test splits.
    4. Construct and save vocabulary.
    5. Save processed splits and metadata JSON.
    """
    raw_path = Path(raw_data_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    log_file = raw_path / log_filename
    label_file = raw_path / label_filename

    # 1. Build session sequences
    sessions = build_session_sequences(
        structured_log_path=log_file,
        label_file_path=label_file,
        require_labels=True,
    )

    # 2. Partition sessions (Strict Zero-Leakage Policy)
    train_sessions, val_sessions, test_sessions = split_sessions(
        sessions=sessions,
        train_ratio=train_ratio,
        val_ratio=val_ratio,
        test_ratio=test_ratio,
        seed=seed,
    )

    # 3. Build Vocabulary
    # LogBERT fits vocabulary on all valid event templates present in the benchmark
    all_events = [e for s in sessions for e in s.events]
    vocab = EventVocab()
    vocab.fit(all_events)

    # 4. Save processed split files
    save_sessions_json(train_sessions, out_path / "train_sessions.json")
    save_sessions_json(val_sessions, out_path / "val_sessions.json")
    save_sessions_json(test_sessions, out_path / "test_sessions.json")

    # 5. Save vocabulary
    vocab.save(out_path / "vocab.json")

    # 6. Compute statistics and metrics
    split_metrics = compute_split_metrics(train_sessions, val_sessions, test_sessions)
    seq_stats = calculate_sequence_stats(sessions)

    # Count template frequencies
    template_counts: Dict[str, int] = {}
    for event in all_events:
        template_counts[event] = template_counts.get(event, 0) + 1

    metadata: Dict[str, Any] = {
        "dataset_name": "HDFS",
        "benchmark_variant": log_filename,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "random_seed": seed,
        "split_ratios": {
            "train": train_ratio,
            "val": val_ratio,
            "test": test_ratio,
        },
        "metrics": split_metrics,
        "sequence_statistics": seq_stats,
        "vocabulary": {
            "vocab_size": vocab.vocab_size,
            "special_tokens": {
                "pad": vocab.pad_id,
                "unk": vocab.unk_id,
                "cls": vocab.cls_id,
                "sep": vocab.sep_id,
                "mask": vocab.mask_id,
            },
            "template_counts": template_counts,
        },
        "provenance": {
            "source": "LogPAI loghub / loglizer (SOSP 2009 HDFS Benchmark)",
            "license": "MIT License",
            "citation": "Wei Xu et al. SOSP 2009; Jieming Zhu et al. ICSE 2019",
        },
    }

    # Save metadata JSON
    metadata_file = out_path / "preprocessing_metadata.json"
    with open(metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    return metadata
