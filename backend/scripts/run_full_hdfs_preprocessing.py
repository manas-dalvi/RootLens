"""
Script to execute the full HDFS benchmark preprocessing pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ml.logbert.hdfs_full_pipeline import run_full_preprocessing


def main():
    metadata = run_full_preprocessing(
        raw_data_dir="data/benchmarks/hdfs_full/raw",
        output_dir="data/benchmarks/hdfs_full/processed",
        train_ratio=0.8,
        val_ratio=0.1,
        test_ratio=0.1,
        seed=42,
        sample_train_size=4855,
        sample_eval_size=2000,
    )
    print("\nSummary:")
    print("Total Sessions:", metadata["sequence_statistics"]["total_sessions"])
    print("Mean Length:", metadata["sequence_statistics"]["mean"])
    print("Median Length:", metadata["sequence_statistics"]["median"])
    print("Train Normal:", metadata["metrics"]["train"]["normal_sessions"], "Train Anomaly:", metadata["metrics"]["train"]["anomaly_sessions"])
    print("Val Normal:", metadata["metrics"]["val"]["normal_sessions"], "Val Anomaly:", metadata["metrics"]["val"]["anomaly_sessions"])
    print("Test Normal:", metadata["metrics"]["test"]["normal_sessions"], "Test Anomaly:", metadata["metrics"]["test"]["anomaly_sessions"])
    print("Validation OOV rate:", metadata["vocabulary"]["oov_analysis"]["validation_oov_rate_pct"], "%")
    print("Test OOV rate:", metadata["vocabulary"]["oov_analysis"]["test_oov_rate_pct"], "%")
    print("\nSUCCESS: Full HDFS preprocessing completed successfully.")


if __name__ == "__main__":
    main()
