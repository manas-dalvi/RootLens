"""
RootLens — Full-Scale LogBERT Training Pipeline (Phase 6, Option A)
Trains LogBERT on the complete 446,578-session normal HDFS training partition.
Includes atomic checkpointing, mid-epoch resumption, and hypersphere center caching.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Sampler

from app.ml.logbert.config import LogBERTConfig
from app.ml.logbert.dataset import SessionSequence, load_sessions_json
from app.ml.logbert.inference import LogBERTInferenceEngine
from app.ml.logbert.model import LogBERT
from app.ml.logbert.scoring import find_validation_threshold
from app.ml.logbert.trainer import LogBERTDataset, LogBERTTrainer
from app.ml.logbert.vocab import EventVocab

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("train_full_logbert")


def get_memory_mb() -> float:
    """Return current process working set memory in MB."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {os.getpid()}).WorkingSet64 / 1MB"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return round(float(out.decode().strip()), 1)
    except Exception:
        return 0.0


def save_atomic_checkpoint(payload: Dict[str, Any], filepath: Path) -> None:
    """Save PyTorch checkpoint dictionary atomically using a temporary file, fsync, and replace."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = filepath.with_name(f"{filepath.name}.tmp_{os.getpid()}")
    try:
        with open(tmp_path, "wb") as f:
            torch.save(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise


def get_rng_states() -> Dict[str, Any]:
    """Capture current PyTorch, Python, and NumPy RNG states."""
    states: Dict[str, Any] = {
        "torch": torch.get_rng_state(),
        "python": random.getstate(),
    }
    try:
        import numpy as np  # type: ignore
        states["numpy"] = np.random.get_state()
    except Exception:
        states["numpy"] = None
    return states


def set_rng_states(rng: Dict[str, Any]) -> None:
    """Restore PyTorch, Python, and NumPy RNG states."""
    if "torch" in rng and rng["torch"] is not None:
        torch.set_rng_state(rng["torch"])
    if "python" in rng and rng["python"] is not None:
        random.setstate(rng["python"])
    if "numpy" in rng and rng["numpy"] is not None:
        try:
            import numpy as np  # type: ignore
            np.random.set_state(rng["numpy"])
        except Exception:
            pass


class ResumableBatchSampler(Sampler[List[int]]):
    """
    Deterministic BatchSampler supporting resumption from an exact batch offset.
    Yields batches of indices starting at start_step without skipping or repeating.
    """

    def __init__(self, indices: List[int], batch_size: int, start_step: int = 0):
        self.indices = indices
        self.batch_size = batch_size
        self.start_step = start_step

    def __iter__(self):
        total_batches = len(self)
        for k in range(self.start_step, total_batches):
            start_idx = k * self.batch_size
            end_idx = min(start_idx + self.batch_size, len(self.indices))
            yield self.indices[start_idx:end_idx]

    def __len__(self) -> int:
        return (len(self.indices) + self.batch_size - 1) // self.batch_size


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Full-Scale LogBERT Training with Interruption-Safe Resumption")
    parser.add_argument("--data-dir", type=str, default="data/benchmarks/hdfs_full/processed", help="Directory with processed HDFS dataset")
    parser.add_argument("--output-dir", type=str, default="data/benchmarks/hdfs_full/model", help="Directory to save model checkpoints")
    parser.add_argument("--eval-dir", type=str, default="data/benchmarks/hdfs_full/evaluation", help="Directory to save evaluation reports")
    parser.add_argument("--batch-size", type=int, default=64, help="Batch size for training (default: 64)")
    parser.add_argument("--max-epochs", type=int, default=3, help="Maximum training epochs (default: 3)")
    parser.add_argument("--patience", type=int, default=1, help="Early stopping patience (default: 1)")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate (default: 1e-3)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--save-interval", type=int, default=1000, help="Batches between resumable checkpoints (default: 1000)")
    parser.add_argument("--resume", action="store_true", default=True, help="Automatically resume from checkpoint if available")
    parser.add_argument("--no-resume", action="store_false", dest="resume", help="Do not resume; start clean training")
    return parser.parse_args()


def run_full_training(
    data_dir: Path | str = "data/benchmarks/hdfs_full/processed",
    output_dir: Path | str = "data/benchmarks/hdfs_full/model",
    eval_dir: Path | str = "data/benchmarks/hdfs_full/evaluation",
    batch_size: int = 64,
    max_epochs: int = 3,
    patience: int = 1,
    lr: float = 1e-3,
    seed: int = 42,
    hidden_size: int = 128,
    num_layers: int = 4,
    num_heads: int = 4,
    vhm_alpha: float = 0.1,
    max_seq_len: int = 64,
    save_interval: int = 1000,
    resume: bool = True,
    interrupt_after_step: Optional[int] = None,
) -> Dict[str, Any]:
    data_path = Path(data_dir)
    out_path = Path(output_dir)
    ev_path = Path(eval_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    ev_path.mkdir(parents=True, exist_ok=True)

    log_file = out_path / "training.log"
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(file_handler)

    logger.info("======================================================================")
    logger.info("ROOTLENS: FULL-SCALE LOGBERT TRAINING PIPELINE (RESUMABLE OPTION A)")
    logger.info("======================================================================")
    logger.info("Process ID: %d | Log file: %s", os.getpid(), log_file)
    logger.info("Configuration:")
    logger.info("  Batch size: %d | Max epochs: %d | Patience: %d | LR: %g | Seed: %d", batch_size, max_epochs, patience, lr, seed)
    logger.info("  Save interval: every %d batches | Auto-resume: %s", save_interval, resume)
    logger.info("  Architecture: hidden=%d, layers=%d, heads=%d, max_seq_len=%d, alpha=%g", hidden_size, num_layers, num_heads, max_seq_len, vhm_alpha)
    logger.info("  Hardware: %d CPU cores, %d PyTorch threads", os.cpu_count() or 1, torch.get_num_threads())

    # 1. Load Vocabulary (Verified 22 tokens)
    vocab_path = data_path / "vocab.json"
    if not vocab_path.exists():
        logger.error("Vocabulary file not found: %s", vocab_path)
        sys.exit(1)
    vocab = EventVocab.load(vocab_path)
    logger.info("Loaded vocabulary: %d tokens from %s", vocab.vocab_size, vocab_path)
    if vocab.vocab_size != 22:
        logger.error("FATAL: Vocab size is %d, expected exactly 22 tokens!", vocab.vocab_size)
        sys.exit(1)

    # 2. Load Datasets & Verify Invariants
    logger.info("Loading full training partition...")
    t_load_0 = time.time()
    train_sessions = load_sessions_json(data_path / "train_sessions.json")
    t_train_load = time.time() - t_load_0
    total_train = len(train_sessions)
    anom_train = sum(1 for s in train_sessions if s.label != 0)
    logger.info("Loaded %s training sessions in %.2fs (Memory: %.1f MB)", f"{total_train:,}", t_train_load, get_memory_mb())

    if anom_train > 0:
        logger.error("FATAL DATA LEAKAGE: %d anomalous sessions in training partition!", anom_train)
        sys.exit(1)
    logger.info("Invariant Verified: 100%% of training sessions are normal (0 anomalies).")

    logger.info("Loading full validation partition...")
    t_val_load_0 = time.time()
    val_sessions = load_sessions_json(data_path / "val_sessions.json")
    t_val_load = time.time() - t_val_load_0
    total_val = len(val_sessions)
    val_norm = sum(1 for s in val_sessions if s.label == 0)
    val_anom = sum(1 for s in val_sessions if s.label == 1)
    logger.info("Loaded %s validation sessions (%s normal, %s anomaly) in %.2fs",
                f"{total_val:,}", f"{val_norm:,}", f"{val_anom:,}", t_val_load)

    # 3. Model Configuration & Datasets
    config = LogBERTConfig(
        vocab_size=vocab.vocab_size,
        max_seq_len=max_seq_len,
        hidden_size=hidden_size,
        num_hidden_layers=num_layers,
        num_attention_heads=num_heads,
        intermediate_size=hidden_size * 4,
        vhm_alpha=vhm_alpha,
    )

    train_dataset = LogBERTDataset(train_sessions, vocab, max_seq_len=config.max_seq_len)
    val_dataset = LogBERTDataset(val_sessions, vocab, max_seq_len=config.max_seq_len)

    val_loader = DataLoader(
        val_dataset,
        batch_size=128,  # Evaluation with larger batch size on CPU for speed
        shuffle=False,
        generator=torch.Generator().manual_seed(seed),
    )

    total_batches = (total_train + batch_size - 1) // batch_size
    logger.info("Train batches per epoch: %s (batch size %d)", f"{total_batches:,}", batch_size)
    logger.info("Val batches per epoch:   %s (batch size 128)", f"{len(val_loader):,}")

    # 4. Instantiate Model & Trainer
    torch.manual_seed(seed)
    random.seed(seed)
    model = LogBERT(config)
    param_counts = model.count_parameters()
    logger.info("LogBERT instantiated: %s parameters (~%.2fM)", f"{param_counts['total_parameters']:,}", param_counts['total_parameters'] / 1e6)

    trainer = LogBERTTrainer(
        model=model,
        config=config,
        lr=lr,
        device="cpu",
        seed=seed,
    )

    # 5. Hypersphere Center Caching & Initialization
    center_file = out_path / "hypersphere_center.pt"
    if center_file.exists():
        logger.info("Found cached hypersphere center at: %s", center_file)
        cached_center_payload = torch.load(center_file, map_location="cpu", weights_only=False)
        center = cached_center_payload["center"]
        trainer.center = center
        trainer.loss_fn.set_center(center)
        logger.info("Restored hypersphere center from disk: norm = %.4f, mean = %.4f (Saved 7+ minutes calculation)",
                    float(torch.norm(center).item()), float(torch.mean(center).item()))
    else:
        logger.info("Initializing hypersphere center c from normal training representations...")
        t_c0 = time.time()
        # Compute from fresh baseline loader
        base_loader = DataLoader(
            train_dataset,
            batch_size=batch_size,
            shuffle=False,
            generator=torch.Generator().manual_seed(seed),
        )
        center = trainer.initialize_center_from_loader(base_loader, eps=config.vhm_eps)
        c_time = time.time() - t_c0
        save_atomic_checkpoint({"center": center, "norm": float(torch.norm(center).item())}, center_file)
        logger.info("Center initialized in %.2fs and cached to %s: norm = %.4f, mean = %.4f, eps bounded = True",
                    c_time, center_file, float(torch.norm(center).item()), float(torch.mean(center).item()))

    # 6. Checkpoint Selection Criterion Documentation
    logger.info("----------------------------------------------------------------------")
    logger.info("CHECKPOINT SELECTION CRITERION:")
    logger.info("  Primary Criterion: Minimum Validation Loss (val_loss) on full validation set.")
    logger.info("  Secondary Monitoring: Sequence-level Validation F1-score and False Positive Rate (FPR).")
    logger.info("  Early Stopping: Patience = %d epoch. Training stops if val_loss fails to improve.", patience)
    logger.info("  Best checkpoint will be saved to: %s", out_path / "logbert_best.pt")
    logger.info("  Resumable checkpoint will be updated every %d batches to: %s", save_interval, out_path / "checkpoint_resumable.pt")
    logger.info("----------------------------------------------------------------------")

    # 7. Check for Resumable Checkpoint
    resumable_ckpt_file = out_path / "checkpoint_resumable.pt"
    start_epoch = 1
    start_step = 0
    best_val_loss = float("inf")
    best_epoch = 0
    best_val_metrics: Dict[str, Any] = {}
    epochs_without_improvement = 0
    loss_history: List[Dict[str, Any]] = []
    resumed_epoch_indices: Optional[List[int]] = None
    accumulated_losses: Optional[Dict[str, float]] = None

    if resume and resumable_ckpt_file.exists():
        logger.info("Resumable checkpoint detected at: %s", resumable_ckpt_file)
        try:
            ckpt = torch.load(resumable_ckpt_file, map_location="cpu", weights_only=False)
            trainer.model.load_state_dict(ckpt["model_state_dict"])
            trainer.optimizer.load_state_dict(ckpt["optimizer_state_dict"])
            if ckpt.get("center") is not None and isinstance(ckpt["center"], torch.Tensor):
                restored_center: torch.Tensor = ckpt["center"]
                trainer.center = restored_center
                center = restored_center
                trainer.loss_fn.set_center(restored_center)

            # Restore RNG states
            if "rng_states" in ckpt:
                set_rng_states(ckpt["rng_states"])

            best_val_loss = ckpt.get("best_val_loss", float("inf"))
            best_epoch = ckpt.get("best_epoch", 0)
            best_val_metrics = ckpt.get("best_val_metrics", {})
            epochs_without_improvement = ckpt.get("epochs_without_improvement", 0)
            loss_history = ckpt.get("loss_history", [])

            ckpt_epoch = ckpt.get("epoch", 1)
            ckpt_step = ckpt.get("step", 0)

            if ckpt_step < total_batches:
                # Interrupted mid-epoch
                start_epoch = ckpt_epoch
                start_step = ckpt_step
                resumed_epoch_indices = ckpt.get("epoch_indices")
                accumulated_losses = {
                    "total_loss": ckpt.get("epoch_total_loss", 0.0),
                    "mlkp_loss": ckpt.get("epoch_mlkp_loss", 0.0),
                    "vhm_loss": ckpt.get("epoch_vhm_loss", 0.0),
                }
                logger.info("RESUMING MID-EPOCH: Epoch %d, Step %d/%d (%.1f%% complete). Accumulated loss restored.",
                            start_epoch, start_step, total_batches, (start_step / total_batches) * 100.0)
            else:
                # Interrupted between epochs
                start_epoch = ckpt_epoch + 1
                start_step = 0
                logger.info("RESUMING AT EPOCH BOUNDARY: Next Epoch %d/%d.", start_epoch, max_epochs)
        except Exception as exc:
            logger.warning("Failed to restore resumable checkpoint (%s). Starting clean training run.", exc)
            start_epoch = 1
            start_step = 0

    if start_epoch > max_epochs:
        logger.info("Training already completed %d epochs. Exiting.", max_epochs)
        return {"completed": True, "epochs": max_epochs}

    # 8. Training Loop
    training_start_time = time.time()

    for epoch in range(start_epoch, max_epochs + 1):
        epoch_start_time = time.time()
        logger.info("\n>>> STARTING EPOCH %d/%d <<<", epoch, max_epochs)

        trainer.model.train()

        # Resolve deterministic epoch indices
        if epoch == start_epoch and resumed_epoch_indices is not None and accumulated_losses is not None:
            epoch_indices = resumed_epoch_indices
            epoch_total_loss = accumulated_losses["total_loss"]
            epoch_mlkp_loss = accumulated_losses["mlkp_loss"]
            epoch_vhm_loss = accumulated_losses["vhm_loss"]
            cur_start_step = start_step
            logger.info("Using restored shuffle sequence from checkpoint. Resuming from batch %d...", cur_start_step)
        else:
            g = torch.Generator().manual_seed(seed + epoch * 1000)
            epoch_indices = torch.randperm(total_train, generator=g).tolist()
            epoch_total_loss = 0.0
            epoch_mlkp_loss = 0.0
            epoch_vhm_loss = 0.0
            cur_start_step = 0

        # Construct batch sampler starting from cur_start_step
        batch_sampler = ResumableBatchSampler(epoch_indices, batch_size=batch_size, start_step=cur_start_step)
        loader_gen = torch.Generator().manual_seed(seed + epoch * 1000)
        train_loader = DataLoader(train_dataset, batch_sampler=batch_sampler, generator=loader_gen)

        t_batch_start = time.time()

        for step_idx, batch in enumerate(train_loader, cur_start_step + 1):
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

            epoch_total_loss += total_loss.item()
            epoch_mlkp_loss += losses["mlkp_loss"].item()
            epoch_vhm_loss += losses["vhm_loss"].item()

            # Periodic Resumable Checkpoint (every save_interval steps)
            if step_idx % save_interval == 0:
                resumable_payload = {
                    "epoch": epoch,
                    "step": step_idx,
                    "epoch_indices": epoch_indices,
                    "model_state_dict": trainer.model.state_dict(),
                    "optimizer_state_dict": trainer.optimizer.state_dict(),
                    "center": trainer.center.cpu() if trainer.center is not None else None,
                    "epoch_total_loss": epoch_total_loss,
                    "epoch_mlkp_loss": epoch_mlkp_loss,
                    "epoch_vhm_loss": epoch_vhm_loss,
                    "best_val_loss": best_val_loss,
                    "best_epoch": best_epoch,
                    "best_val_metrics": best_val_metrics,
                    "epochs_without_improvement": epochs_without_improvement,
                    "loss_history": loss_history,
                    "rng_states": get_rng_states(),
                }
                save_atomic_checkpoint(resumable_payload, resumable_ckpt_file)
                logger.info("Saved atomic resumable checkpoint at step %d/%d (%.1f%%) to %s",
                            step_idx, total_batches, (step_idx / total_batches) * 100.0, resumable_ckpt_file)

                if interrupt_after_step is not None and step_idx == interrupt_after_step:
                    logger.info("Simulating interruption after step %d as requested.", step_idx)
                    raise KeyboardInterrupt(f"Simulated interruption after step {step_idx}")

            # Progress log (every 500 batches or at end)
            if step_idx % 500 == 0 or step_idx == total_batches:
                elapsed_b = time.time() - t_batch_start
                batches_processed_in_run = step_idx - cur_start_step
                b_per_s = batches_processed_in_run / elapsed_b if elapsed_b > 0 else 1.0
                remaining_batches = total_batches - step_idx
                eta_s = remaining_batches / b_per_s
                eta_min = eta_s / 60.0
                pct = (step_idx / total_batches) * 100.0
                cur_avg_loss = epoch_total_loss / step_idx
                logger.info(
                    "Epoch %d [%5d/%5d (%.1f%%)] | Loss: %.4f (MLKP: %.4f, VHM: %.4f) | Rate: %.2f b/s | Elapsed: %.1fm | ETA: %.1fm | RAM: %.1fMB",
                    epoch, step_idx, total_batches, pct, cur_avg_loss,
                    epoch_mlkp_loss / step_idx, epoch_vhm_loss / step_idx,
                    b_per_s, elapsed_b / 60.0, eta_min, get_memory_mb()
                )

        train_wall_duration = round(time.time() - epoch_start_time, 2)
        mean_train_total = round(epoch_total_loss / total_batches, 5)
        mean_train_mlkp = round(epoch_mlkp_loss / total_batches, 5)
        mean_train_vhm = round(epoch_vhm_loss / total_batches, 5)
        throughput_b_per_s = round(total_batches / train_wall_duration, 2) if train_wall_duration > 0 else 0.0
        throughput_seq_per_s = round(total_train / train_wall_duration, 2) if train_wall_duration > 0 else 0.0

        logger.info("Epoch %d Training Complete in %.2fs (%.2f min) | Throughput: %.2f b/s (%.2f seq/s)",
                    epoch, train_wall_duration, train_wall_duration / 60.0, throughput_b_per_s, throughput_seq_per_s)
        logger.info("Training Losses: Total=%.5f, MLKP=%.5f, VHM=%.5f",
                    mean_train_total, mean_train_mlkp, mean_train_vhm)

        # Full Validation Evaluation
        logger.info("Evaluating on FULL Validation Set (%s sessions)...", f"{total_val:,}")
        t_val_start = time.time()
        trainer.model.eval()

        val_losses: List[float] = []
        val_scores: List[float] = []
        val_labels: List[int] = []

        with torch.no_grad():
            for v_batch in val_loader:
                v_ids = v_batch["input_ids"]
                v_mask = v_batch["attention_mask"]
                v_lbls = v_batch["label"].tolist()

                # Normal sequence loss
                norm_idx = [i for i, y in enumerate(v_lbls) if y == 0]
                if norm_idx:
                    norm_ids = v_ids[norm_idx]
                    norm_mask = v_mask[norm_idx]
                    v_masked_ids, v_mlkp_labels = trainer.loss_fn.mlkp.mask_tokens(norm_ids)
                    v_output = trainer.model(v_masked_ids, attention_mask=norm_mask)
                    v_loss_dict = trainer.loss_fn(
                        logits=v_output.logits,
                        labels=v_mlkp_labels,
                        cls_representation=v_output.dist_representation,
                    )
                    val_losses.append(v_loss_dict["total_loss"].item())

                # Batched forward pass for sequence scoring
                b_output = trainer.model(v_ids, attention_mask=v_mask)
                vhm_dists = torch.sum((b_output.dist_representation - center) ** 2, dim=-1)
                probs = F.softmax(b_output.logits, dim=-1)
                top_k = min(6, config.vocab_size)
                _, top_cands = torch.topk(probs, k=top_k, dim=-1)

                for i in range(v_ids.size(0)):
                    seq_toks = v_ids[i]
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
                        mismatch_ratio = mismatches / num_events
                    else:
                        mismatch_ratio = 0.0

                    vhm_dist = float(vhm_dists[i].item())
                    composite = mismatch_ratio + config.vhm_alpha * vhm_dist
                    val_scores.append(round(composite, 4))
                    val_labels.append(v_lbls[i])

        val_duration = round(time.time() - t_val_start, 2)
        mean_val_loss = round(sum(val_losses) / len(val_losses), 5) if val_losses else 0.0

        # Calibrate optimal threshold on validation scores
        thresh_info = find_validation_threshold(val_scores, val_labels)
        opt_threshold = thresh_info["threshold"]

        # Confusion Matrix at optimal threshold
        preds = [1 if s >= opt_threshold else 0 for s in val_scores]
        tp = sum(1 for p, y in zip(preds, val_labels) if p == 1 and y == 1)
        tn = sum(1 for p, y in zip(preds, val_labels) if p == 0 and y == 0)
        fp = sum(1 for p, y in zip(preds, val_labels) if p == 1 and y == 0)
        fn = sum(1 for p, y in zip(preds, val_labels) if p == 0 and y == 1)

        precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
        recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
        f1 = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) > 0 else 0.0
        fpr = round(fp / (fp + tn), 4) if (fp + tn) > 0 else 0.0
        accuracy = round((tp + tn) / len(preds), 4)

        norm_scores = [s for s, y in zip(val_scores, val_labels) if y == 0]
        anom_scores = [s for s, y in zip(val_scores, val_labels) if y == 1]
        mean_norm_score = round(sum(norm_scores) / len(norm_scores), 4) if norm_scores else 0.0
        mean_anom_score = round(sum(anom_scores) / len(anom_scores), 4) if anom_scores else 0.0

        val_seq_per_s = round(total_val / val_duration, 1) if val_duration > 0 else 0.0
        logger.info("Validation Complete in %.2fs (%.1f seq/s)", val_duration, val_seq_per_s)
        logger.info("Validation Results: Val Loss=%.5f | Threshold=%.4f", mean_val_loss, opt_threshold)
        logger.info("Metrics: F1=%.4f | Precision=%.4f | Recall=%.4f | FPR=%.4f | Accuracy=%.4f",
                    f1, precision, recall, fpr, accuracy)
        logger.info("Confusion Matrix: TP=%d, FN=%d, TN=%d, FP=%d", tp, fn, tn, fp)
        logger.info("Mean Scores: Normal=%.4f, Anomaly=%.4f", mean_norm_score, mean_anom_score)

        epoch_record = {
            "epoch": epoch,
            "train_wall_duration_seconds": train_wall_duration,
            "train_batches_per_second": throughput_b_per_s,
            "train_sequences_per_second": throughput_seq_per_s,
            "train_total_loss": mean_train_total,
            "train_mlkp_loss": mean_train_mlkp,
            "train_vhm_loss": mean_train_vhm,
            "val_duration_seconds": val_duration,
            "val_loss": mean_val_loss,
            "val_threshold": opt_threshold,
            "val_f1": f1,
            "val_precision": precision,
            "val_recall": recall,
            "val_fpr": fpr,
            "val_accuracy": accuracy,
            "tp": tp,
            "fn": fn,
            "tn": tn,
            "fp": fp,
            "mean_normal_score": mean_norm_score,
            "mean_anomaly_score": mean_anom_score,
            "memory_working_set_mb": get_memory_mb(),
        }
        loss_history.append(epoch_record)

        # Checkpoint Decision based on Minimum Validation Loss
        best_ckpt_file = out_path / "logbert_best.pt"
        if mean_val_loss < best_val_loss or not best_ckpt_file.exists():
            improvement = best_val_loss - mean_val_loss
            logger.info("VAL LOSS IMPROVED from %.5f to %.5f (delta: -%.5f). Updating best checkpoint!",
                        best_val_loss, mean_val_loss, improvement)
            best_val_loss = min(best_val_loss, mean_val_loss)
            best_epoch = epoch
            best_val_metrics = epoch_record
            epochs_without_improvement = 0

            # Save best checkpoint atomically
            best_ckpt_file = out_path / "logbert_best.pt"
            best_payload = {
                "model_state_dict": trainer.model.state_dict(),
                "config": trainer.config.to_dict(),
                "center": trainer.center.cpu() if trainer.center is not None else None,
                "optimizer_state_dict": trainer.optimizer.state_dict(),
                "metadata": {
                    "epoch": epoch,
                    "val_loss": mean_val_loss,
                    "threshold": opt_threshold,
                    "val_f1": f1,
                    "val_precision": precision,
                    "val_recall": recall,
                    "val_fpr": fpr,
                    "tp": tp,
                    "fn": fn,
                    "tn": tn,
                    "fp": fp,
                    "dataset": "HDFS_Full_v1",
                    "training_sessions": total_train,
                    "validation_sessions": total_val,
                },
            }
            save_atomic_checkpoint(best_payload, best_ckpt_file)
            logger.info("Saved atomic best checkpoint: %s (%.2f MB)",
                        best_ckpt_file, best_ckpt_file.stat().st_size / 1e6)
        else:
            epochs_without_improvement += 1
            logger.info("Val loss did not improve (Current: %.5f, Best: %.5f). Epochs without improvement: %d/%d",
                        mean_val_loss, best_val_loss, epochs_without_improvement, patience)
            if epochs_without_improvement >= patience:
                logger.info("Early stopping triggered at epoch %d after %d epoch(s) without improvement.",
                            epoch, patience)
                # Save end-of-epoch checkpoint before exiting
                end_epoch_payload = {
                    "epoch": epoch,
                    "step": total_batches,
                    "epoch_indices": epoch_indices,
                    "model_state_dict": trainer.model.state_dict(),
                    "optimizer_state_dict": trainer.optimizer.state_dict(),
                    "center": trainer.center.cpu() if trainer.center is not None else None,
                    "epoch_total_loss": epoch_total_loss,
                    "epoch_mlkp_loss": epoch_mlkp_loss,
                    "epoch_vhm_loss": epoch_vhm_loss,
                    "best_val_loss": best_val_loss,
                    "best_epoch": best_epoch,
                    "best_val_metrics": best_val_metrics,
                    "epochs_without_improvement": epochs_without_improvement,
                    "loss_history": loss_history,
                    "rng_states": get_rng_states(),
                }
                save_atomic_checkpoint(end_epoch_payload, resumable_ckpt_file)
                break

        # Save end-of-epoch resumable checkpoint
        end_epoch_payload = {
            "epoch": epoch,
            "step": total_batches,
            "epoch_indices": epoch_indices,
            "model_state_dict": trainer.model.state_dict(),
            "optimizer_state_dict": trainer.optimizer.state_dict(),
            "center": trainer.center.cpu() if trainer.center is not None else None,
            "epoch_total_loss": epoch_total_loss,
            "epoch_mlkp_loss": epoch_mlkp_loss,
            "epoch_vhm_loss": epoch_vhm_loss,
            "best_val_loss": best_val_loss,
            "best_epoch": best_epoch,
            "best_val_metrics": best_val_metrics,
            "epochs_without_improvement": epochs_without_improvement,
            "loss_history": loss_history,
            "rng_states": get_rng_states(),
        }
        save_atomic_checkpoint(end_epoch_payload, resumable_ckpt_file)

        # Save live history after each epoch
        history_path = out_path / "training_history.json"
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump({"loss_history": loss_history}, f, indent=2)

    total_training_duration = round(time.time() - training_start_time, 2)
    logger.info("\n======================================================================")
    logger.info("TRAINING COMPLETED in %.2fs (%.2f hours)", total_training_duration, total_training_duration / 3600.0)
    logger.info("Best Epoch: %d with Val Loss: %.5f | Val F1: %.4f | Val Threshold: %.4f",
                best_epoch, best_val_loss, best_val_metrics.get("val_f1", 0.0), best_val_metrics.get("val_threshold", 0.0))
    logger.info("======================================================================")

    # 9. Save Configuration & Comprehensive Metadata
    config.save(out_path / "config.json")
    best_ckpt_file = out_path / "logbert_best.pt"

    metadata = {
        "dataset": "HDFS_Full_v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "random_seed": seed,
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
        "training_configuration": {
            "batch_size": batch_size,
            "max_epochs": max_epochs,
            "patience": patience,
            "learning_rate": lr,
            "vhm_alpha": vhm_alpha,
            "save_interval_steps": save_interval,
            "checkpoint_selection_criterion": "Minimum Validation Loss on Normal Validation Sequences (with Sequence F1 monitoring)",
        },
        "partition_sizes": {
            "train_sessions": total_train,
            "train_anomalies": anom_train,
            "val_sessions": total_val,
            "val_normal": val_norm,
            "val_anomalies": val_anom,
        },
        "center_vector": {
            "norm": round(float(torch.norm(center).item()), 4) if isinstance(center, torch.Tensor) else 0.0,
            "mean": round(float(torch.mean(center).item()), 4) if isinstance(center, torch.Tensor) else 0.0,
            "cached_file": str(center_file),
        },
        "training_summary": {
            "total_runtime_seconds": total_training_duration,
            "total_runtime_hours": round(total_training_duration / 3600.0, 3),
            "completed_epochs": len(loss_history),
            "best_epoch": best_epoch,
            "best_val_loss": best_val_loss,
            "best_val_metrics": best_val_metrics,
            "epoch_history": loss_history,
        },
        "checkpoint": {
            "file": str(best_ckpt_file.name),
            "path": str(best_ckpt_file),
            "size_bytes": best_ckpt_file.stat().st_size if best_ckpt_file.exists() else 0,
        },
    }

    metadata_path = out_path / "training_metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Saved training metadata: %s", metadata_path)

    # 10. Verify Production Inference Pipeline Reload
    if best_ckpt_file.exists():
        logger.info("Verifying checkpoint reload through production LogBERTInferenceEngine...")
        engine = LogBERTInferenceEngine(
            checkpoint_path=best_ckpt_file,
            vocab_path=vocab_path,
            device="cpu",
        )
        logger.info("Inference engine loaded successfully with threshold: %.4f", engine.threshold)

        sample_norm = next(s for s in val_sessions if s.label == 0)
        sample_anom = next(s for s in val_sessions if s.label == 1)

        pred_norm = engine.predict_sequence(sample_norm.events, session_id=sample_norm.block_id)
        pred_anom = engine.predict_sequence(sample_anom.events, session_id=sample_anom.block_id)

        logger.info("Sanity Eval Normal [%s]: is_anomaly=%s, score=%.4f (mismatches=%d/%d)",
                    sample_norm.block_id, pred_norm.is_anomaly, pred_norm.composite_score,
                    pred_norm.mismatch_count, pred_norm.num_events)
        logger.info("Sanity Eval Anomaly [%s]: is_anomaly=%s, score=%.4f (mismatches=%d/%d)",
                    sample_anom.block_id, pred_anom.is_anomaly, pred_anom.composite_score,
                    pred_anom.mismatch_count, pred_anom.num_events)

    # Save evaluation report
    report_path = ev_path / "full_training_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Saved full training report to: %s", report_path)

    logger.info("======================================================================")
    logger.info("FULL-SCALE LOGBERT TRAINING PIPELINE COMPLETE")
    logger.info("======================================================================")
    return metadata


if __name__ == "__main__":
    args = parse_args()
    run_full_training(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        eval_dir=args.eval_dir,
        batch_size=args.batch_size,
        max_epochs=args.max_epochs,
        patience=args.patience,
        lr=args.lr,
        seed=args.seed,
        save_interval=args.save_interval,
        resume=args.resume,
    )
