"""
Unit tests for RootLens Phase 5B: Full HDFS Benchmark Upgrade.
Validates:
1. Block ID grouping and sequence integrity.
2. Chronological event ordering.
3. Ground-truth label mapping and consistency.
4. Label integrity and absence of malformed records.
5. Strict zero split overlap (disjoint Block IDs across train, val, test).
6. Normal-only training partition (strictly 0 anomalies in train).
7. Deterministic preprocessing via random seed.
8. Vocabulary consistency, special token mappings, and OOV isolation.
9. Large-file chunked streaming processing.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Set

import pytest

from app.ml.logbert.dataset import SessionSequence, load_anomaly_labels, load_sessions_json
from app.ml.logbert.hdfs_full_pipeline import (
    compute_comprehensive_sequence_stats,
    load_all_full_sessions,
    stream_event_traces,
)
from app.ml.logbert.split import split_sessions
from app.ml.logbert.vocab import EventVocab

FULL_RAW_DIR = Path("data/benchmarks/hdfs_full/raw")
FULL_PROCESSED_DIR = Path("data/benchmarks/hdfs_full/processed")


class TestHDFSFullIntegrity:
    """Tests for raw and preprocessed full HDFS benchmark files."""

    @pytest.fixture(scope="class")
    def metadata(self) -> Dict:
        meta_path = FULL_PROCESSED_DIR / "preprocessing_metadata.json"
        assert meta_path.exists(), f"Metadata not found at {meta_path}"
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture(scope="class")
    def labels_dict(self) -> Dict[str, int]:
        label_file = FULL_RAW_DIR / "anomaly_label.csv"
        assert label_file.exists(), f"Label file not found at {label_file}"
        return load_anomaly_labels(label_file)

    def test_raw_files_exist(self):
        """Verify all required full raw files exist and are non-empty."""
        for fname in ["HDFS.log", "anomaly_label.csv", "Event_traces.csv", "HDFS.log_templates.csv"]:
            p = FULL_RAW_DIR / fname
            assert p.is_file(), f"Expected raw file missing: {p}"
            assert p.stat().st_size > 0, f"Raw file is empty: {p}"

    def test_processed_files_exist(self):
        """Verify all full processed partition files exist."""
        expected_files = [
            "train_sessions.json",
            "val_sessions.json",
            "test_sessions.json",
            "train_sessions_sampled.json",
            "val_sessions_sampled.json",
            "test_sessions_sampled.json",
            "vocab.json",
            "vocab_canonical.json",
            "preprocessing_metadata.json",
        ]
        for fname in expected_files:
            p = FULL_PROCESSED_DIR / fname
            assert p.is_file(), f"Expected processed file missing: {p}"
            assert p.stat().st_size > 0, f"Processed file is empty: {p}"

    def test_ground_truth_label_counts(self, labels_dict: Dict[str, int]):
        """Verify 575,061 unique labels: 558,223 normal and 16,838 anomaly."""
        assert len(labels_dict) == 575061
        normal_count = sum(1 for v in labels_dict.values() if v == 0)
        anomaly_count = sum(1 for v in labels_dict.values() if v == 1)
        assert normal_count == 558223
        assert anomaly_count == 16838
        assert normal_count + anomaly_count == 575061

    def test_chunked_streaming_pipeline(self):
        """Verify chunked streaming reads arbitrary chunk sizes without error."""
        traces_file = FULL_RAW_DIR / "Event_traces.csv"
        label_file = FULL_RAW_DIR / "anomaly_label.csv"
        chunks = list(stream_event_traces(traces_file, label_file, chunk_size=100))
        # Test first chunk properties
        assert len(chunks) > 0
        first_chunk = chunks[0]
        assert len(first_chunk) == 100
        for s in first_chunk:
            assert isinstance(s, SessionSequence)
            assert s.block_id.startswith("blk_")
            assert len(s.events) >= 2
            assert s.label in (0, 1)

    def test_chronological_ordering_in_traces(self):
        """Verify first 5 event tokens of standard HDFS blocks match chronological lifecycle."""
        traces_file = FULL_RAW_DIR / "Event_traces.csv"
        label_file = FULL_RAW_DIR / "anomaly_label.csv"
        first_chunk = next(stream_event_traces(traces_file, label_file, chunk_size=5))
        s0 = first_chunk[0]
        # In HDFS, block lifecycle begins with receiving/allocation (E5/E22)
        assert s0.events[0] in ("E5", "E22")
        # And finishes with delete/block file deletion (E23/E21) or terminal events
        assert s0.events[-1] in ("E21", "E23", "E2", "E3", "E11", "E20")


class TestPartitionAndLeakageIntegrity:
    """Verify strict zero-leakage, split disjointness, and normal-only training."""

    @pytest.fixture(scope="class")
    def sampled_splits(self) -> Dict[str, List[SessionSequence]]:
        return {
            "train": load_sessions_json(FULL_PROCESSED_DIR / "train_sessions_sampled.json"),
            "val": load_sessions_json(FULL_PROCESSED_DIR / "val_sessions_sampled.json"),
            "test": load_sessions_json(FULL_PROCESSED_DIR / "test_sessions_sampled.json"),
        }

    def test_sampled_splits_counts(self, sampled_splits: Dict[str, List[SessionSequence]]):
        """Verify sampled splits have expected sizes for rapid CPU development."""
        assert len(sampled_splits["train"]) == 4855
        assert len(sampled_splits["val"]) == 2000
        assert len(sampled_splits["test"]) == 2000

    def test_normal_only_training(self, sampled_splits: Dict[str, List[SessionSequence]]):
        """Verify training partition contains STRICTLY 0 anomalous sessions."""
        train_labels = [s.label for s in sampled_splits["train"]]
        assert all(lbl == 0 for lbl in train_labels)
        assert sum(train_labels) == 0

    def test_zero_split_overlap(self, sampled_splits: Dict[str, List[SessionSequence]]):
        """Verify zero Block ID overlap across train, val, and test splits."""
        train_blocks = set(s.block_id for s in sampled_splits["train"])
        val_blocks = set(s.block_id for s in sampled_splits["val"])
        test_blocks = set(s.block_id for s in sampled_splits["test"])

        assert len(train_blocks & val_blocks) == 0, "DATA LEAKAGE: Train and Val overlap!"
        assert len(train_blocks & test_blocks) == 0, "DATA LEAKAGE: Train and Test overlap!"
        assert len(val_blocks & test_blocks) == 0, "DATA LEAKAGE: Val and Test overlap!"

    def test_anomalies_present_in_evaluation(self, sampled_splits: Dict[str, List[SessionSequence]]):
        """Verify both validation and test sets contain anomalous sessions for calibration and evaluation."""
        val_anom = sum(1 for s in sampled_splits["val"] if s.label == 1)
        test_anom = sum(1 for s in sampled_splits["test"] if s.label == 1)
        assert val_anom == 200
        assert test_anom == 200

    def test_sequence_lengths_are_multievent(self, sampled_splits: Dict[str, List[SessionSequence]]):
        """Verify full HDFS sessions have genuine multi-event sequences (no single-event sessions)."""
        for split_name, sessions in sampled_splits.items():
            lengths = [len(s.events) for s in sessions]
            assert min(lengths) >= 2, f"Found single-event session in {split_name}!"
            assert sum(lengths) / len(lengths) > 15.0, f"Mean length too low in {split_name}"


class TestVocabularyConsistency:
    """Tests for vocabulary generation, special token IDs, and OOV isolation."""

    def test_vocab_special_tokens(self):
        vocab_path = FULL_PROCESSED_DIR / "vocab.json"
        vocab = EventVocab.load(vocab_path)
        assert vocab.pad_id == 0
        assert vocab.unk_id == 1
        assert vocab.cls_id == 2
        assert vocab.sep_id == 3
        assert vocab.mask_id == 4

    def test_training_vocab_size_and_templates(self):
        """Verify training vocab contains 22 tokens (5 special + 17 normal event templates)."""
        vocab = EventVocab.load(FULL_PROCESSED_DIR / "vocab.json")
        assert vocab.vocab_size == 22
        # Normal templates must be in vocab
        for norm_t in ["E26", "E5", "E11", "E9", "E21", "E23", "E22", "E3", "E4", "E2"]:
            assert vocab.token_to_id(norm_t) >= 5

    def test_error_templates_map_to_unk(self):
        """Verify pure error templates map to [UNK] (ID 1) under training-fitted vocab."""
        vocab = EventVocab.load(FULL_PROCESSED_DIR / "vocab.json")
        for err_t in ["E1", "E7", "E8", "E10", "E12", "E13", "E14", "E15", "E17", "E19", "E29"]:
            assert vocab.token_to_id(err_t) == vocab.unk_id

    def test_canonical_vocab_size(self):
        """Verify canonical vocabulary contains 34 tokens (5 special + 29 canonical templates)."""
        vocab = EventVocab.load(FULL_PROCESSED_DIR / "vocab_canonical.json")
        assert vocab.vocab_size == 34
        for i in range(1, 30):
            assert vocab.token_to_id(f"E{i}") >= 5


class TestDeterministicPreprocessing:
    """Verify deterministic splitting and reproducibility."""

    def test_deterministic_split_reproducibility(self):
        """Verify split_sessions produces identical partitions given the same seed."""
        dummy_sessions = [
            SessionSequence(block_id=f"blk_{i}", events=["E5", "E22"], label=(1 if i % 10 == 0 else 0))
            for i in range(100)
        ]
        tr1, v1, te1 = split_sessions(dummy_sessions, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1, seed=42)
        tr2, v2, te2 = split_sessions(dummy_sessions, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1, seed=42)

        assert [s.block_id for s in tr1] == [s.block_id for s in tr2]
        assert [s.block_id for s in v1] == [s.block_id for s in v2]
        assert [s.block_id for s in te1] == [s.block_id for s in te2]
