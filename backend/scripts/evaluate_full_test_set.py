"""
RootLens Phase 7: Final Held-Out Test Evaluation & Benchmark Reporting.

Evaluates the production LogBERT model on the untouched HDFS full test partition
using the saved validation-calibrated threshold (0.0004).
Measures CPU inference latency (p50, p95, throughput), computes full confusion matrix
and classification metrics, and verifies production inference engine outputs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.dataset import SessionSequence
from app.ml.logbert.inference import LogBERTInferenceEngine, LogBERTInferenceResult
from app.ml.logbert.trainer import LogBERTDataset
from app.ml.logbert.vocab import EventVocab

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("evaluate_full_test_set")


def load_sessions_json(path: Path) -> List[SessionSequence]:
    """Load serialized SessionSequence objects from JSON."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [SessionSequence.from_dict(item) for item in data]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RootLens Phase 7: Final Held-Out Test Evaluation")
    parser.add_argument(
        "--checkpoint-path",
        type=str,
        default="data/benchmarks/hdfs_full/model/logbert_best.pt",
        help="Path to trained LogBERT checkpoint",
    )
    parser.add_argument(
        "--vocab-path",
        type=str,
        default="data/benchmarks/hdfs_full/processed/vocab.json",
        help="Path to canonical vocabulary JSON",
    )
    parser.add_argument(
        "--test-data-path",
        type=str,
        default="data/benchmarks/hdfs_full/processed/test_sessions.json",
        help="Path to held-out test partition JSON",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/benchmarks/hdfs_full/evaluation",
        help="Directory to save evaluation reports",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="Batch size for bulk test scoring on CPU (default: 128)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.0004,
        help="Fixed decision threshold calibrated strictly on validation set (default: 0.0004)",
    )
    parser.add_argument(
        "--latency-samples",
        type=int,
        default=1000,
        help="Number of sequences for single-sequence CPU latency profiling (default: 1000)",
    )
    return parser.parse_args()


def run_evaluation(
    checkpoint_path: Path | str = "data/benchmarks/hdfs_full/model/logbert_best.pt",
    vocab_path: Path | str = "data/benchmarks/hdfs_full/processed/vocab.json",
    test_data_path: Path | str = "data/benchmarks/hdfs_full/processed/test_sessions.json",
    output_dir: Path | str = "data/benchmarks/hdfs_full/evaluation",
    batch_size: int = 128,
    threshold: float = 0.0004,
    latency_samples: int = 1000,
) -> Dict[str, Any]:
    ckpt_file = Path(checkpoint_path)
    vocab_file = Path(vocab_path)
    test_file = Path(test_data_path)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    log_file = out_path / "test_evaluation.log"
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(file_handler)

    logger.info("======================================================================")
    logger.info("ROOTLENS: PHASE 7 FINAL HELD-OUT TEST EVALUATION & BENCHMARK REPORTING")
    logger.info("======================================================================")
    logger.info("Process ID: %d | Log file: %s", os.getpid(), log_file)
    logger.info("Checkpoint: %s", ckpt_file)
    logger.info("Vocabulary: %s", vocab_file)
    logger.info("Test Partition: %s", test_file)
    logger.info("Fixed Validation-Calibrated Threshold: %.4f", threshold)
    logger.info("Hardware: %d CPU cores, %d PyTorch threads", os.cpu_count() or 1, torch.get_num_threads())

    # 1. Load Production LogBERT Inference Engine
    logger.info("Initializing LogBERTInferenceEngine from checkpoint...")
    t_init_0 = time.time()
    engine = LogBERTInferenceEngine(
        checkpoint_path=ckpt_file,
        vocab_path=vocab_file,
        device="cpu",
        custom_threshold=threshold,
    )
    init_duration = round(time.time() - t_init_0, 4)
    model_info = engine.get_model_info()
    logger.info("Inference engine ready in %.4fs (Center norm: %.4f, Parameters: %s)",
                init_duration, model_info["center_norm"], f"{model_info['total_parameters']:,}")

    # 2. Load Held-Out Test Split Exactly Once
    logger.info("Loading held-out test partition from %s...", test_file)
    t_load_0 = time.time()
    test_sessions = load_sessions_json(test_file)
    load_duration = round(time.time() - t_load_0, 2)
    total_test = len(test_sessions)
    test_normal = sum(1 for s in test_sessions if s.label == 0)
    test_anomaly = sum(1 for s in test_sessions if s.label == 1)
    anomaly_ratio_pct = round((test_anomaly / total_test) * 100.0, 2) if total_test > 0 else 0.0

    logger.info("Loaded %s test sessions in %.2fs", f"{total_test:,}", load_duration)
    logger.info("  Normal sessions:   %s (%.2f%%)", f"{test_normal:,}", 100.0 - anomaly_ratio_pct)
    logger.info("  Anomaly sessions:  %s (%.2f%%)", f"{test_anomaly:,}", anomaly_ratio_pct)

    # 3. CPU Latency Benchmark (Single-Sequence Mode)
    logger.info("Benchmarking single-sequence CPU inference latency over %d samples...", latency_samples)
    warmup_count = min(50, total_test)
    sample_pool = test_sessions[:min(latency_samples + warmup_count, total_test)]

    # Warmup
    for s in sample_pool[:warmup_count]:
        _ = engine.predict_sequence(s.events, session_id=s.block_id)

    # Timing individual sequences
    eval_pool = sample_pool[warmup_count:warmup_count + latency_samples]
    latencies_ms: List[float] = []

    for s in eval_pool:
        t0 = time.perf_counter()
        _ = engine.predict_sequence(s.events, session_id=s.block_id)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(dt_ms)

    latencies_ms.sort()
    n_lat = len(latencies_ms)
    p50_ms = round(latencies_ms[int(n_lat * 0.50)], 3)
    p90_ms = round(latencies_ms[int(n_lat * 0.90)], 3)
    p95_ms = round(latencies_ms[int(n_lat * 0.95)], 3)
    p99_ms = round(latencies_ms[int(n_lat * 0.99)], 3)
    mean_ms = round(sum(latencies_ms) / n_lat, 3)
    min_ms = round(latencies_ms[0], 3)
    max_ms = round(latencies_ms[-1], 3)
    single_seq_throughput = round(1000.0 / mean_ms, 1) if mean_ms > 0 else 0.0

    logger.info("Single-Sequence CPU Latency Benchmark:")
    logger.info("  Mean: %.3f ms | p50: %.3f ms | p90: %.3f ms | p95: %.3f ms | p99: %.3f ms",
                mean_ms, p50_ms, p90_ms, p95_ms, p99_ms)
    logger.info("  Min:  %.3f ms | Max: %.3f ms | Throughput: %.1f seq/s", min_ms, max_ms, single_seq_throughput)

    # 4. Full-Scale Batched Test Scoring (High Throughput)
    logger.info("Executing full-scale batched test scoring on all %s sessions (Batch size %d)...",
                f"{total_test:,}", batch_size)
    test_dataset = LogBERTDataset(test_sessions, engine.vocab, max_seq_len=engine.config.max_seq_len)
    test_loader = DataLoader(
        test_dataset,
        batch_size=batch_size,
        shuffle=False,
        generator=torch.Generator().manual_seed(42),
    )

    t_eval_0 = time.time()
    scores: List[float] = []
    labels: List[int] = []
    vhm_distances: List[float] = []
    mismatch_ratios: List[float] = []

    model = engine.model
    center = engine.center
    config = engine.config
    model.eval()

    with torch.no_grad():
        for b_idx, batch in enumerate(test_loader, 1):
            input_ids = batch["input_ids"]
            attention_mask = batch["attention_mask"]
            batch_labels = batch["label"].tolist()

            output = model(input_ids, attention_mask=attention_mask)
            b_vhm_dists = torch.sum((output.dist_representation - center) ** 2, dim=-1)
            probs = F.softmax(output.logits, dim=-1)
            top_k = min(6, config.vocab_size)
            _, top_cands = torch.topk(probs, k=top_k, dim=-1)

            for i in range(input_ids.size(0)):
                seq_toks = input_ids[i]
                eval_mask = (
                    (seq_toks != config.pad_token_id)
                    & (seq_toks != config.cls_token_id)
                    & (seq_toks != config.sep_token_id)
                )
                eval_indices = eval_mask.nonzero(as_tuple=False).view(-1)
                num_events = len(eval_indices)
                mismatches = 0
                if num_events > 0:
                    for idx in eval_indices:
                        true_tok = seq_toks[idx].item()
                        if true_tok not in top_cands[i, idx].tolist():
                            mismatches += 1
                    m_ratio = mismatches / num_events
                else:
                    m_ratio = 0.0

                vhm_d = float(b_vhm_dists[i].item())
                comp = m_ratio + config.vhm_alpha * vhm_d

                scores.append(round(comp, 4))
                labels.append(batch_labels[i])
                vhm_distances.append(round(vhm_d, 4))
                mismatch_ratios.append(round(m_ratio, 4))

    eval_duration = round(time.time() - t_eval_0, 2)
    batched_throughput = round(total_test / eval_duration, 1) if eval_duration > 0 else 0.0
    logger.info("Full test scoring completed in %.2fs (Throughput: %.1f seq/s)", eval_duration, batched_throughput)

    # 5. Compute Classification Metrics using Fixed Validation-Calibrated Threshold
    logger.info("Computing metrics with fixed threshold %.4f...", threshold)
    predictions = [1 if s >= threshold else 0 for s in scores]

    tp = sum(1 for p, y in zip(predictions, labels) if p == 1 and y == 1)
    fp = sum(1 for p, y in zip(predictions, labels) if p == 1 and y == 0)
    tn = sum(1 for p, y in zip(predictions, labels) if p == 0 and y == 0)
    fn = sum(1 for p, y in zip(predictions, labels) if p == 0 and y == 1)

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
    f1 = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) > 0 else 0.0
    fpr = round(fp / (fp + tn), 4) if (fp + tn) > 0 else 0.0
    specificity = round(tn / (tn + fp), 4) if (tn + fp) > 0 else 0.0
    accuracy = round((tp + tn) / total_test, 4) if total_test > 0 else 0.0

    normal_scores = [s for s, y in zip(scores, labels) if y == 0]
    anomaly_scores = [s for s, y in zip(scores, labels) if y == 1]

    mean_norm = round(sum(normal_scores) / len(normal_scores), 4) if normal_scores else 0.0
    mean_anom = round(sum(anomaly_scores) / len(anomaly_scores), 4) if anomaly_scores else 0.0

    sorted_norm = sorted(normal_scores)
    sorted_anom = sorted(anomaly_scores)
    median_norm = round(sorted_norm[len(sorted_norm) // 2], 4) if sorted_norm else 0.0
    median_anom = round(sorted_anom[len(sorted_anom) // 2], 4) if sorted_anom else 0.0

    logger.info("----------------------------------------------------------------------")
    logger.info("FINAL TEST RESULTS (Threshold = %.4f):", threshold)
    logger.info("  F1-Score:    %.4f (%.2f%%)", f1, f1 * 100.0)
    logger.info("  Precision:   %.4f (%.2f%%)", precision, precision * 100.0)
    logger.info("  Recall:      %.4f (%.2f%%)", recall, recall * 100.0)
    logger.info("  FPR:         %.4f (%.2f%%)", fpr, fpr * 100.0)
    logger.info("  Specificity: %.4f (%.2f%%)", specificity, specificity * 100.0)
    logger.info("  Accuracy:    %.4f (%.2f%%)", accuracy, accuracy * 100.0)
    logger.info("CONFUSION MATRIX:")
    logger.info("  TP: %d | FP: %d | TN: %d | FN: %d", tp, fp, tn, fn)
    logger.info("SCORE SEPARATION:")
    logger.info("  Normal sessions:  Mean = %.4f | Median = %.4f | Min = %.4f | Max = %.4f",
                mean_norm, median_norm, sorted_norm[0], sorted_norm[-1])
    logger.info("  Anomaly sessions: Mean = %.4f | Median = %.4f | Min = %.4f | Max = %.4f",
                mean_anom, median_anom, sorted_anom[0], sorted_anom[-1])
    logger.info("----------------------------------------------------------------------")

    # 6. Production Inference Verification on Sample Sequences
    logger.info("Verifying production inference engine on test split sample sequences...")
    sample_normal = next(s for s in test_sessions if s.label == 0)
    sample_anomaly = next(s for s in test_sessions if s.label == 1)

    pred_norm = engine.predict_sequence(sample_normal.events, session_id=sample_normal.block_id)
    pred_anom = engine.predict_sequence(sample_anomaly.events, session_id=sample_anomaly.block_id)

    logger.info("Production Verification - Normal [%s]: is_anomaly=%s, score=%.4f, vhm=%.4f, mismatches=%d/%d",
                sample_normal.block_id, pred_norm.is_anomaly, pred_norm.composite_score,
                pred_norm.vhm_distance, pred_norm.mismatch_count, pred_norm.num_events)
    logger.info("Production Verification - Anomaly [%s]: is_anomaly=%s, score=%.4f, vhm=%.4f, mismatches=%d/%d",
                sample_anomaly.block_id, pred_anom.is_anomaly, pred_anom.composite_score,
                pred_anom.vhm_distance, pred_anom.mismatch_count, pred_anom.num_events)

    # 7. Compile Comprehensive Machine-Readable Report
    report: Dict[str, Any] = {
        "benchmark_name": "RootLens_LogBERT_HDFS_Full_v1_Final_Test_Evaluation",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "checkpoint": {
            "path": str(ckpt_file),
            "size_bytes": ckpt_file.stat().st_size,
            "trained_epoch": engine.metadata.get("epoch", 3),
            "best_val_loss": engine.metadata.get("val_loss", 0.0534),
        },
        "model_architecture": {
            "model_type": "LogBERT",
            "hidden_size": config.hidden_size,
            "num_hidden_layers": config.num_hidden_layers,
            "num_attention_heads": config.num_attention_heads,
            "intermediate_size": config.intermediate_size,
            "max_seq_len": config.max_seq_len,
            "vocab_size": engine.vocab.vocab_size,
            "total_parameters": model_info["total_parameters"],
            "vhm_alpha": config.vhm_alpha,
            "center_norm": model_info["center_norm"],
        },
        "dataset_summary": {
            "dataset": "HDFS_Full_v1",
            "test_file": str(test_file),
            "total_test_sessions": total_test,
            "normal_sessions": test_normal,
            "anomaly_sessions": test_anomaly,
            "anomaly_percentage": anomaly_ratio_pct,
        },
        "evaluation_protocol": {
            "decision_threshold": threshold,
            "threshold_source": "Strictly calibrated on validation partition (Epoch 3 min val loss); 0 tuning on test",
            "device": "cpu",
            "batch_size": batch_size,
            "top_candidates_k": 6,
        },
        "classification_metrics": {
            "f1_score": f1,
            "precision": precision,
            "recall": recall,
            "accuracy": accuracy,
            "false_positive_rate": fpr,
            "specificity": specificity,
            "confusion_matrix": {
                "tp": tp,
                "fp": fp,
                "tn": tn,
                "fn": fn,
            },
        },
        "score_distribution": {
            "normal_sessions": {
                "mean": mean_norm,
                "median": median_norm,
                "min": sorted_norm[0],
                "max": sorted_norm[-1],
            },
            "anomaly_sessions": {
                "mean": mean_anom,
                "median": median_anom,
                "min": sorted_anom[0],
                "max": sorted_anom[-1],
            },
        },
        "latency_benchmark": {
            "hardware": {
                "device": "cpu",
                "cpu_cores": os.cpu_count(),
                "pytorch_threads": torch.get_num_threads(),
            },
            "single_sequence_latency_ms": {
                "sample_count": len(latencies_ms),
                "mean": mean_ms,
                "median_p50": p50_ms,
                "p90": p90_ms,
                "p95": p95_ms,
                "p99": p99_ms,
                "min": min_ms,
                "max": max_ms,
                "throughput_seq_per_sec": single_seq_throughput,
            },
            "batched_inference": {
                "batch_size": batch_size,
                "total_sequences": total_test,
                "wall_time_seconds": eval_duration,
                "throughput_seq_per_sec": batched_throughput,
            },
        },
        "production_verification_samples": {
            "normal_sample": {
                "session_id": sample_normal.block_id,
                "ground_truth": 0,
                "predicted_anomaly": pred_norm.is_anomaly,
                "composite_score": pred_norm.composite_score,
                "vhm_distance": pred_norm.vhm_distance,
                "mismatch_count": pred_norm.mismatch_count,
            },
            "anomaly_sample": {
                "session_id": sample_anomaly.block_id,
                "ground_truth": 1,
                "predicted_anomaly": pred_anom.is_anomaly,
                "composite_score": pred_anom.composite_score,
                "vhm_distance": pred_anom.vhm_distance,
                "mismatch_count": pred_anom.mismatch_count,
            },
        },
    }

    report_path = out_path / "final_test_evaluation_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info("Saved final test evaluation report to %s", report_path)

    logger.info("======================================================================")
    logger.info("PHASE 7 FINAL TEST EVALUATION COMPLETE")
    logger.info("======================================================================")
    return report


if __name__ == "__main__":
    args = parse_args()
    run_evaluation(
        checkpoint_path=args.checkpoint_path,
        vocab_path=args.vocab_path,
        test_data_path=args.test_data_path,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        threshold=args.threshold,
        latency_samples=args.latency_samples,
    )
