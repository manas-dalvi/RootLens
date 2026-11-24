"""
LogBERT CPU Training Performance Benchmark and Full-Scale Runtime Estimator.
Runs a deterministic 3-epoch benchmark on the 4,855-session HDFS full training sample.
Measures:
- Batches/sec, sequences/sec, training step latency
- Epoch wall-clock duration
- User & System CPU utilization and active PyTorch threads
- Peak process working set memory (RAM)
- Separate MLKP and VHM loss components
- Validation evaluation throughput
- Empirical projection of full 446,578-session dataset training duration
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import torch
from torch.utils.data import DataLoader

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.dataset import load_sessions_json
from app.ml.logbert.model import LogBERT
from app.ml.logbert.trainer import LogBERTDataset, LogBERTTrainer
from app.ml.logbert.vocab import EventVocab

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")


# Windows Memory Struct
class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_process_memory_mb() -> Tuple[float, float]:
    """Return (current_working_set_mb, peak_working_set_mb) on Windows."""
    try:
        pid = os.getpid()
        cmd = f"(Get-Process -Id {pid}).WorkingSet64 / 1MB; (Get-Process -Id {pid}).PeakWorkingSet64 / 1MB"
        out = subprocess.check_output(["powershell", "-NoProfile", "-Command", cmd]).decode().strip().splitlines()
        if len(out) >= 2:
            return float(out[0].strip()), float(out[1].strip())
        elif len(out) == 1:
            return float(out[0].strip()), float(out[0].strip())
    except Exception:
        pass
    return (0.0, 0.0)


def run_benchmark(
    data_dir: str = "data/benchmarks/hdfs_full/processed",
    output_dir: str = "data/benchmarks/hdfs_full/model_benchmark",
    batch_size: int = 64,
    benchmark_epochs: int = 3,
    lr: float = 1e-3,
    seed: int = 42,
) -> Dict[str, Any]:
    data_path = Path(data_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print("==================================================")
    print("ROOTLENS LOGBERT PHASE 6: TRAINING PERFORMANCE BENCHMARK")
    print("==================================================")

    # 1. Compatibility Audit
    vocab_path = data_path / "vocab.json"
    train_file = data_path / "train_sessions_sampled.json"
    val_file = data_path / "val_sessions_sampled.json"

    print("\n--- 1. Pre-Training Compatibility Audit ---")
    vocab = EventVocab.load(vocab_path)
    print(f"Loaded Vocabulary: {vocab.vocab_size} tokens from {vocab_path}")
    assert vocab.vocab_size == 22, f"Expected 22 tokens, got {vocab.vocab_size}"
    assert vocab.cls_id == 2 and vocab.pad_id == 0 and vocab.mask_id == 4

    train_sessions = load_sessions_json(train_file)
    val_sessions = load_sessions_json(val_file)

    # Invariant checks
    assert all(s.label == 0 for s in train_sessions), "DATA LEAKAGE: Anomaly present in training sample!"
    print(f"Training sample: {len(train_sessions):,} sessions (100% Normal, 0 Anomalies)")
    print(f"Validation sample: {len(val_sessions):,} sessions ({sum(1 for s in val_sessions if s.label == 0):,} Normal, {sum(1 for s in val_sessions if s.label == 1):,} Anomaly)")

    config = LogBERTConfig(
        vocab_size=vocab.vocab_size,
        max_seq_len=64,
        hidden_size=128,
        num_hidden_layers=4,
        num_attention_heads=4,
        intermediate_size=512,
        vhm_alpha=0.1,
    )

    torch.manual_seed(seed)
    model = LogBERT(config)
    param_counts = model.count_parameters()
    print(f"LogBERT instantiated: {param_counts['total_parameters']:,} parameters (~{param_counts['total_parameters']/1e6:.2f}M)")

    train_dataset = LogBERTDataset(train_sessions, vocab, max_seq_len=config.max_seq_len)
    val_dataset = LogBERTDataset(val_sessions, vocab, max_seq_len=config.max_seq_len)

    train_loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
    )

    trainer = LogBERTTrainer(
        model=model,
        config=config,
        lr=lr,
        device="cpu",
        seed=seed,
    )

    # Center Initialization
    print("Initializing hypersphere center c from normal training sequence representations...")
    t_center_0 = time.time()
    center = trainer.initialize_center_from_loader(train_loader, eps=config.vhm_eps)
    center_time = time.time() - t_center_0
    print(f"Center initialized in {center_time:.2f}s (norm={torch.norm(center).item():.4f}, mean={torch.mean(center).item():.4f})")

    # 2. Benchmark Epoch Execution
    print(f"\n--- 2. Executing {benchmark_epochs} Benchmark Epochs on CPU ---")
    print(f"Hardware: {os.cpu_count()} CPU cores, PyTorch threads: {torch.get_num_threads()}")
    print(f"Batches per epoch: {len(train_loader)} (batch_size={batch_size})")

    epoch_records: List[Dict[str, Any]] = []
    total_train_batches = 0
    total_train_time = 0.0

    for epoch in range(1, benchmark_epochs + 1):
        t_wall_0 = time.time()
        t_cpu_0 = os.times()
        mem_0, _ = get_process_memory_mb()

        # Train epoch with per-batch profiling
        trainer.model.train()
        epoch_total_loss = 0.0
        epoch_mlkp_loss = 0.0
        epoch_vhm_loss = 0.0
        batch_latencies_ms = []

        for batch in train_loader:
            t_b0 = time.perf_counter()
            input_ids = batch["input_ids"]
            attention_mask = batch["attention_mask"]

            masked_ids, labels = trainer.loss_fn.mlkp.mask_tokens(input_ids)
            output = trainer.model(masked_ids, attention_mask=attention_mask)
            losses = trainer.loss_fn(
                logits=output.logits,
                labels=labels,
                cls_representation=output.dist_representation,
            )
            total_loss = losses["total_loss"]

            trainer.optimizer.zero_grad()
            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(trainer.model.parameters(), max_norm=1.0)
            trainer.optimizer.step()

            t_b1 = time.perf_counter()
            b_dur_ms = (t_b1 - t_b0) * 1000.0
            batch_latencies_ms.append(b_dur_ms)

            epoch_total_loss += total_loss.item()
            epoch_mlkp_loss += losses["mlkp_loss"].item()
            epoch_vhm_loss += losses["vhm_loss"].item()

        t_wall_1 = time.time()
        t_cpu_1 = os.times()
        mem_1, peak_mem = get_process_memory_mb()

        num_b = len(train_loader)
        wall_sec = t_wall_1 - t_wall_0
        user_sec = t_cpu_1.user - t_cpu_0.user
        sys_sec = t_cpu_1.system - t_cpu_0.system
        total_cpu_sec = user_sec + sys_sec
        cpu_util_pct = (total_cpu_sec / wall_sec * 100.0) if wall_sec > 0 else 0.0

        batches_per_sec = num_b / wall_sec if wall_sec > 0 else 0.0
        seqs_per_sec = len(train_sessions) / wall_sec if wall_sec > 0 else 0.0
        mean_batch_ms = sum(batch_latencies_ms) / len(batch_latencies_ms) if batch_latencies_ms else 0.0

        total_train_batches += num_b
        total_train_time += wall_sec

        # Validation evaluation profiling
        t_val_0 = time.time()
        val_metrics = trainer.evaluate_validation(val_loader)
        val_sec = time.time() - t_val_0
        val_batches_per_sec = len(val_loader) / val_sec if val_sec > 0 else 0.0

        epoch_record = {
            "epoch": epoch,
            "train_wall_duration_seconds": round(wall_sec, 2),
            "batches_per_second": round(batches_per_sec, 2),
            "sequences_per_second": round(seqs_per_sec, 2),
            "mean_batch_latency_ms": round(mean_batch_ms, 2),
            "cpu_utilization_pct": round(cpu_util_pct, 1),
            "memory_working_set_mb": round(mem_1, 1),
            "peak_working_set_mb": round(peak_mem, 1),
            "losses": {
                "total_loss": round(epoch_total_loss / num_b, 5),
                "mlkp_loss": round(epoch_mlkp_loss / num_b, 5),
                "vhm_loss": round(epoch_vhm_loss / num_b, 5),
            },
            "validation": {
                "val_duration_seconds": round(val_sec, 2),
                "val_batches_per_second": round(val_batches_per_sec, 2),
                "val_loss": val_metrics["val_loss"],
                "val_threshold": val_metrics["threshold"],
                "val_f1": val_metrics["val_f1"],
                "val_precision": val_metrics["val_precision"],
                "val_recall": val_metrics["val_recall"],
            },
        }
        epoch_records.append(epoch_record)

        print(f"Epoch {epoch}/{benchmark_epochs}: Train {wall_sec:.2f}s ({batches_per_sec:.1f} b/s, {seqs_per_sec:.1f} seq/s) | "
              f"Loss={epoch_record['losses']['total_loss']:.4f} (MLKP={epoch_record['losses']['mlkp_loss']:.4f}, VHM={epoch_record['losses']['vhm_loss']:.4f}) | "
              f"Val Loss={val_metrics['val_loss']:.4f} (Val F1={val_metrics['val_f1']:.4f}) in {val_sec:.2f}s | "
              f"CPU={cpu_util_pct:.0f}%, RAM={mem_1:.1f}MB")

    # Save benchmark model checkpoint for verification
    benchmark_ckpt_path = out_path / "logbert_benchmark.pt"
    trainer.save_checkpoint(
        checkpoint_path=benchmark_ckpt_path,
        extra_metadata={
            "benchmark": True,
            "epochs": benchmark_epochs,
            "val_loss": epoch_records[-1]["validation"]["val_loss"],
        },
    )

    # 3. Full Dataset Projection Analysis
    avg_batches_per_sec = total_train_batches / total_train_time if total_train_time > 0 else 1.0
    avg_seqs_per_sec = (total_train_batches * batch_size) / total_train_time if total_train_time > 0 else 1.0

    full_train_sessions_count = 446578
    full_train_batches = int((full_train_sessions_count + batch_size - 1) / batch_size)
    full_epoch_sec = full_train_batches / avg_batches_per_sec
    full_epoch_min = full_epoch_sec / 60.0
    full_epoch_hours = full_epoch_sec / 3600.0

    full_val_sessions_count = 64241
    full_val_batches = int((full_val_sessions_count + batch_size - 1) / batch_size)
    val_eval_rate = epoch_records[-1]["validation"]["val_batches_per_second"]
    full_val_sec = full_val_batches / val_eval_rate if val_eval_rate > 0 else 0.0

    projections = {
        "full_dataset_normal_sessions": full_train_sessions_count,
        "batch_size": batch_size,
        "total_batches_per_epoch": full_train_batches,
        "measured_training_throughput_batches_per_sec": round(avg_batches_per_sec, 2),
        "measured_training_throughput_seqs_per_sec": round(avg_seqs_per_sec, 2),
        "estimated_train_time_per_epoch": {
            "seconds": round(full_epoch_sec, 1),
            "minutes": round(full_epoch_min, 1),
            "hours": round(full_epoch_hours, 2),
        },
        "estimated_val_eval_time_full_val_split": {
            "val_sessions_count": full_val_sessions_count,
            "seconds": round(full_val_sec, 1),
            "minutes": round(full_val_sec / 60.0, 1),
        },
        "projected_full_training_runs": {
            "1_epoch": f"{full_epoch_hours * 1:.2f} hours ({full_epoch_min * 1:.1f} minutes)",
            "3_epochs": f"{full_epoch_hours * 3:.2f} hours ({full_epoch_min * 3:.1f} minutes)",
            "5_epochs": f"{full_epoch_hours * 5:.2f} hours ({full_epoch_min * 5:.1f} minutes)",
            "10_epochs": f"{full_epoch_hours * 10:.2f} hours ({full_epoch_min * 10:.1f} minutes)",
        },
    }

    report = {
        "benchmark_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hardware": {
            "device": "cpu",
            "cpu_cores": os.cpu_count(),
            "pytorch_threads": torch.get_num_threads(),
        },
        "model_architecture": {
            "vocab_size": config.vocab_size,
            "hidden_size": config.hidden_size,
            "num_hidden_layers": config.num_hidden_layers,
            "num_attention_heads": config.num_attention_heads,
            "intermediate_size": config.intermediate_size,
            "max_seq_len": config.max_seq_len,
            "total_parameters": param_counts["total_parameters"],
        },
        "benchmark_configuration": {
            "sample_train_sessions": len(train_sessions),
            "sample_val_sessions": len(val_sessions),
            "batch_size": batch_size,
            "benchmark_epochs": benchmark_epochs,
            "learning_rate": lr,
            "seed": seed,
        },
        "epoch_records": epoch_records,
        "full_dataset_runtime_projections": projections,
        "checkpoint": {
            "path": str(benchmark_ckpt_path),
            "size_bytes": benchmark_ckpt_path.stat().st_size if benchmark_ckpt_path.exists() else 0,
        },
    }

    report_path = Path("data/benchmarks/hdfs_full/evaluation/training_performance_benchmark.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n==================================================")
    print("BENCHMARK & ESTIMATION RESULTS")
    print("==================================================")
    print(f"Throughput: {avg_batches_per_sec:.2f} batches/sec ({avg_seqs_per_sec:.2f} sequences/sec)")
    print(f"Memory: Working Set = {epoch_records[-1]['memory_working_set_mb']} MB (Peak = {epoch_records[-1]['peak_working_set_mb']} MB)")
    print(f"CPU Utilization: {epoch_records[-1]['cpu_utilization_pct']}% across {torch.get_num_threads()} threads")
    print(f"1 Full Epoch (446,578 sessions): {projections['estimated_train_time_per_epoch']['minutes']} minutes ({projections['estimated_train_time_per_epoch']['hours']} hours)")
    print(f"3 Full Epochs: {projections['projected_full_training_runs']['3_epochs']}")
    print(f"5 Full Epochs: {projections['projected_full_training_runs']['5_epochs']}")
    print(f"Saved benchmark report: {report_path}")

    return report


if __name__ == "__main__":
    run_benchmark()
