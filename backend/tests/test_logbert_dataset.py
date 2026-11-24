"""
Unit tests for LogBERT HDFS dataset preparation, sequence generation, vocabulary, and split integrity.
Ensures zero data leakage and strict preservation of ground truth labels.
"""

import json
import pytest
from pathlib import Path

from app.ml.logbert.vocab import EventVocab
from app.ml.logbert.dataset import (
    SessionSequence,
    extract_block_id,
    load_anomaly_labels,
    build_session_sequences,
    save_sessions_json,
    load_sessions_json,
)
from app.ml.logbert.split import split_sessions, compute_split_metrics
from app.ml.logbert.preprocess import run_preprocessing, calculate_sequence_stats


class TestEventVocab:
    """Test EventVocab tokenization, special tokens, and serialization."""

    def test_special_tokens_initialization(self):
        vocab = EventVocab()
        assert vocab.pad_id == 0
        assert vocab.unk_id == 1
        assert vocab.cls_id == 2
        assert vocab.sep_id == 3
        assert vocab.mask_id == 4
        assert vocab.vocab_size == 5

    def test_fit_and_encode(self):
        vocab = EventVocab()
        vocab.fit(["E1", "E2", "E3", "E1", "E2"])
        # 5 special tokens + 3 unique events = 8
        assert vocab.vocab_size == 8
        assert vocab.token_to_id("E1") >= 5
        assert vocab.token_to_id("E2") >= 5
        assert vocab.token_to_id("E3") >= 5

        # Test unseen token maps to UNK
        assert vocab.token_to_id("E999_UNKNOWN") == vocab.unk_id

    def test_encode_and_decode(self):
        vocab = EventVocab()
        vocab.fit(["E10", "E20"])

        # Without special tokens
        encoded = vocab.encode(["E10", "E20", "E_UNKNOWN"])
        assert encoded == [vocab.token_to_id("E10"), vocab.token_to_id("E20"), vocab.unk_id]

        decoded = vocab.decode(encoded, skip_special_tokens=False)
        assert decoded == ["E10", "E20", "[UNK]"]

        # With special tokens
        encoded_special = vocab.encode(["E10"], add_special_tokens=True)
        assert encoded_special == [vocab.cls_id, vocab.token_to_id("E10"), vocab.sep_id]

        decoded_skip = vocab.decode(encoded_special, skip_special_tokens=True)
        assert decoded_skip == ["E10"]

    def test_vocab_serialization(self, tmp_path: Path):
        vocab = EventVocab()
        vocab.fit(["E1", "E5", "E10"])
        save_path = tmp_path / "vocab.json"
        vocab.save(save_path)

        assert save_path.exists()
        loaded = EventVocab.load(save_path)
        assert loaded.vocab_size == vocab.vocab_size
        assert loaded.encode(["E1", "E5"]) == vocab.encode(["E1", "E5"])
        assert loaded.pad_id == vocab.pad_id


class TestDatasetAndParsing:
    """Test block extraction, session grouping, and label assignment."""

    def test_extract_block_id(self):
        assert extract_block_id("PacketResponder 1 for block blk_38865049064139660 terminating") == "blk_38865049064139660"
        assert extract_block_id("Received block blk_-1608999687919862906 of size 91178") == "blk_-1608999687919862906"
        assert extract_block_id("No block id here in this string") is None

    def test_load_anomaly_labels(self, tmp_path: Path):
        label_file = tmp_path / "labels.csv"
        label_file.write_text(
            "BlockId,Label\n"
            "blk_1,Normal\n"
            "blk_2,Anomaly\n"
            "blk_3,normal\n"
            "blk_4,anomalous\n"
        )
        labels = load_anomaly_labels(label_file)
        assert labels["blk_1"] == 0
        assert labels["blk_2"] == 1
        assert labels["blk_3"] == 0
        assert labels["blk_4"] == 1

    def test_session_serialization(self, tmp_path: Path):
        sessions = [
            SessionSequence(block_id="blk_101", events=["E1", "E2"], label=0, line_ids=[1, 2]),
            SessionSequence(block_id="blk_102", events=["E3"], label=1, line_ids=[3]),
        ]
        save_file = tmp_path / "sessions.json"
        save_sessions_json(sessions, save_file)

        loaded = load_sessions_json(save_file)
        assert len(loaded) == 2
        assert loaded[0].block_id == "blk_101"
        assert loaded[0].events == ["E1", "E2"]
        assert loaded[0].label == 0
        assert loaded[1].label == 1


class TestSplitIntegrityAndLeakagePrevention:
    """Verify zero label leakage and block disjointness."""

    @pytest.fixture
    def mock_sessions(self) -> list[SessionSequence]:
        # 100 normal sessions, 10 anomalous sessions
        sessions = []
        for i in range(100):
            sessions.append(SessionSequence(block_id=f"blk_norm_{i}", events=["E1", "E2"], label=0))
        for j in range(10):
            sessions.append(SessionSequence(block_id=f"blk_anom_{j}", events=["E1", "E9"], label=1))
        return sessions

    def test_zero_label_leakage_in_training(self, mock_sessions):
        train, val, test = split_sessions(mock_sessions, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1, seed=42)

        # STRICT RULE: Training set must contain ONLY normal sessions
        assert len(train) == 80
        assert all(s.label == 0 for s in train), "Found anomalous session in training set!"
        assert sum(s.label for s in train) == 0

    def test_disjoint_partitions(self, mock_sessions):
        train, val, test = split_sessions(mock_sessions, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1, seed=42)

        train_ids = {s.block_id for s in train}
        val_ids = {s.block_id for s in val}
        test_ids = {s.block_id for s in test}

        assert train_ids.isdisjoint(val_ids), "Data leakage between train and val!"
        assert train_ids.isdisjoint(test_ids), "Data leakage between train and test!"
        assert val_ids.isdisjoint(test_ids), "Data leakage between val and test!"

        # Conservation of sessions
        assert len(train) + len(val) + len(test) == len(mock_sessions)

    def test_anomalies_distributed_to_val_and_test(self, mock_sessions):
        train, val, test = split_sessions(mock_sessions, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1, seed=42)

        val_anomalies = [s for s in val if s.label == 1]
        test_anomalies = [s for s in test if s.label == 1]

        # 10 anomalies divided evenly (5 and 5)
        assert len(val_anomalies) == 5
        assert len(test_anomalies) == 5

    def test_deterministic_reproducibility(self, mock_sessions):
        train1, val1, test1 = split_sessions(mock_sessions, seed=123)
        train2, val2, test2 = split_sessions(mock_sessions, seed=123)

        assert [s.block_id for s in train1] == [s.block_id for s in train2]
        assert [s.block_id for s in val1] == [s.block_id for s in val2]
        assert [s.block_id for s in test1] == [s.block_id for s in test2]


class TestHDFSBenchmarkPipeline:
    """End-to-end integration tests using the staged HDFS benchmark files."""

    def test_hdfs_staged_benchmark_preprocessing(self, tmp_path: Path):
        raw_dir = Path("data/benchmarks/hdfs/raw")
        if not (raw_dir / "HDFS_2k.log_structured.csv").exists():
            pytest.skip("HDFS raw benchmark files not present in data/benchmarks/hdfs/raw")

        out_dir = tmp_path / "processed"
        meta = run_preprocessing(
            raw_data_dir=raw_dir,
            output_dir=out_dir,
            train_ratio=0.8,
            val_ratio=0.1,
            test_ratio=0.1,
            seed=42,
        )

        # 1. Verify files exist
        assert (out_dir / "train_sessions.json").exists()
        assert (out_dir / "val_sessions.json").exists()
        assert (out_dir / "test_sessions.json").exists()
        assert (out_dir / "vocab.json").exists()
        assert (out_dir / "preprocessing_metadata.json").exists()

        # 2. Verify metadata structure
        assert meta["dataset_name"] == "HDFS"
        assert meta["metrics"]["train"]["anomaly_sessions"] == 0
        assert meta["metrics"]["train"]["normal_sessions"] == 1541
        assert meta["metrics"]["val"]["anomaly_sessions"] == 34
        assert meta["metrics"]["test"]["anomaly_sessions"] == 34
        assert meta["metrics"]["overall"]["total_sessions"] == 1994

        # 3. Verify loaded files match metadata
        train = load_sessions_json(out_dir / "train_sessions.json")
        val = load_sessions_json(out_dir / "val_sessions.json")
        test = load_sessions_json(out_dir / "test_sessions.json")
        vocab = EventVocab.load(out_dir / "vocab.json")

        assert len(train) == 1541
        assert len(val) == 227
        assert len(test) == 226
        assert all(s.label == 0 for s in train)
        assert vocab.vocab_size == 19
