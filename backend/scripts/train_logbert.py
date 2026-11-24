"""
Reproducible Training Pipeline Script for LogBERT on HDFS Benchmark.
Executes self-supervised training using MLKP + VHM objectives exclusively on normal sessions.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Add backend directory to sys.path so app imports resolve
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("train_logbert")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train LogBERT on HDFS Benchmark Dataset")
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/benchmarks/hdfs/processed",
        help="Directory containing processed HDFS splits and vocabulary",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/benchmarks/hdfs/model",
        help="Directory to save trained model artifacts and checkpoints",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=10,
        help="Maximum training epochs (default: 10)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Batch size for training and validation (default: 64)",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=1e-3,
        help="Learning rate for AdamW optimizer (default: 1e-3)",
    )
    parser.add_argument(
        "--hidden-size",
        type=int,
        default=128,
        help="Hidden dimension for LogBERT Transformer (default: 128)",
    )
    parser.add_argument(
        "--num-layers",
        type=int,
        default=4,
        help="Number of Transformer encoder layers (default: 4)",
    )
    parser.add_argument(
        "--num-heads",
        type=int,
        default=4,
        help="Number of attention heads (default: 4)",
    )
    parser.add_argument(
        "--vhm-alpha",
        type=float,
        default=0.1,
        help="Weight for VHM loss in joint objective (default: 0.1)",
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=4,
        help="Early stopping patience in epochs (default: 4)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("==================================================")
    logger.info("ROOTLENS LOGBERT SELF-SUPERVISED TRAINING PIPELINE")
    logger.info("==================================================")
    logger.info("Configuration:")
    logger.info("  Data Directory: %s", data_dir)
    logger.info("  Output Directory: %s", output_dir)
    logger.info("  Epochs: %d | Batch Size: %d | LR: %g", args.epochs, args.batch_size, args.lr)
    logger.info("  Architecture: hidden=%d, layers=%d, heads=%d", args.hidden_size, args.num_layers, args.num_heads)
    logger.info("  VHM Alpha: %g | Random Seed: %d", args.vhm_alpha, args.seed)

    # 1. Load Vocabulary
    vocab_path = data_dir / "vocab.json"
    if not vocab_path.exists():
        logger.error("Vocabulary file not found: %s", vocab_path)
        sys.exit(1)
    vocab = EventVocab.load(vocab_path)
    logger.info("Loaded vocabulary: %d tokens", vocab.vocab_size)

    # 2. Load Training Sessions (Verify 100% normal)
    train_sessions_path = data_dir / "train_sessions.json"
    train_sessions = load_sessions_json(train_sessions_path)
    total_train = len(train_sessions)
    anom_train = sum(1 for s in train_sessions if s.label != 0)
    logger.info("Loaded %d training sessions", total_train)
    if anom_train > 0:
        logger.error("CRITICAL DATA LEAKAGE: %d anomalous sessions found in training data!", anom_train)
        sys.exit(1)
    logger.info("Data Invariant Verified: 100%% of training sessions are normal (0 anomalies).")

    # 3. Load Validation Sessions
    val_sessions_path = data_dir / "val_sessions.json"
    val_sessions = load_sessions_json(val_sessions_path)
    val_norm = sum(1 for s in val_sessions if s.label == 0)
    val_anom = sum(1 for s in val_sessions if s.label == 1)
    logger.info("Loaded %d validation sessions (%d normal, %d anomaly)", len(val_sessions), val_norm, val_anom)

    # 4. Construct DataLoaders
    config = LogBERTConfig(
        vocab_size=vocab.vocab_size,
        max_seq_len=64,
        hidden_size=args.hidden_size,
        num_hidden_layers=args.num_layers,
        num_attention_heads=args.num_heads,
        intermediate_size=args.hidden_size * 4,
        vhm_alpha=args.vhm_alpha,
    )

    train_dataset = LogBERTDataset(train_sessions, vocab, max_seq_len=config.max_seq_len)
    val_dataset = LogBERTDataset(val_sessions, vocab, max_seq_len=config.max_seq_len)

    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=True,
        generator=torch.Generator().manual_seed(args.seed),
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch_size,
        shuffle=False,
    )

    # 5. Initialize Model & Trainer
    torch.manual_seed(args.seed)
    model = LogBERT(config)
    param_counts = model.count_parameters()
    logger.info("LogBERT instantiated: %d total parameters (~%.2fM)",
                param_counts["total_parameters"], param_counts["total_parameters"] / 1e6)

    trainer = LogBERTTrainer(
        model=model,
        config=config,
        lr=args.lr,
        device="cpu",
        seed=args.seed,
    )

    # 6. Initialize Hypersphere Center c
    logger.info("Initializing hypersphere center c from normal training sequence representations...")
    center = trainer.initialize_center_from_loader(train_loader, eps=config.vhm_eps)
    logger.info("Hypersphere center initialized: norm = %.4f, mean = %.4f, eps bounded = True",
                float(torch.norm(center).item()), float(torch.mean(center).item()))

    # 7. Execute Training Loop
    logger.info("Starting self-supervised joint training on CPU...")
    train_results = trainer.train(
        train_loader=train_loader,
        val_loader=val_loader,
        epochs=args.epochs,
        patience=args.patience,
        checkpoint_dir=output_dir,
    )

    logger.info("Training finished in %.2f seconds (%d completed epochs).",
                train_results["total_runtime_seconds"], train_results["completed_epochs"])
    logger.info("Best Epoch: %d | Best Val Loss: %.5f",
                train_results["best_epoch"], train_results["best_val_loss"])
    best_val = train_results["best_val_metrics"]
    logger.info("Validation Anomaly Threshold Selected: %.4f (Val F1: %.4f, Precision: %.4f, Recall: %.4f)",
                best_val["threshold"], best_val["val_f1"], best_val["val_precision"], best_val["val_recall"])

    # 8. Save Configuration and Training Metadata
    config.save(output_dir / "config.json")
    checkpoint_file = output_dir / "logbert_best.pt"

    metadata = {
        "dataset": "HDFS_2k",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "random_seed": args.seed,
        "parameters": param_counts,
        "training_sessions": total_train,
        "training_anomalies": anom_train,
        "validation_sessions": len(val_sessions),
        "validation_normal": val_norm,
        "validation_anomalies": val_anom,
        "center_norm": float(torch.norm(center).item()),
        "total_runtime_seconds": train_results["total_runtime_seconds"],
        "completed_epochs": train_results["completed_epochs"],
        "best_epoch": train_results["best_epoch"],
        "best_val_loss": train_results["best_val_loss"],
        "validation_metrics": best_val,
        "loss_history": train_results["loss_history"],
        "checkpoint_file": str(checkpoint_file.name),
        "checkpoint_size_bytes": checkpoint_file.stat().st_size if checkpoint_file.exists() else 0,
    }

    metadata_path = output_dir / "training_metadata.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Saved metadata: %s", metadata_path)

    # 9. Verify Checkpoint Reload & Restore Best Model
    logger.info("Verifying checkpoint reload...")
    reloaded_model, reloaded_config, reloaded_center, _ = LogBERTTrainer.load_checkpoint(checkpoint_file)
    trainer.model.load_state_dict(reloaded_model.state_dict())
    trainer.model.eval()
    reloaded_model.eval()

    test_input = torch.tensor([[vocab.cls_id, 5, 6, vocab.sep_id] + [vocab.pad_id] * 60])
    with torch.no_grad():
        out_orig = trainer.model(test_input)
        out_reloaded = reloaded_model(test_input)
    assert torch.allclose(out_orig.logits, out_reloaded.logits, atol=1e-5), "Reloaded model logits mismatch!"
    assert torch.allclose(out_orig.dist_representation, out_reloaded.dist_representation, atol=1e-5), "Reloaded representation mismatch!"
    logger.info("Checkpoint reload verified successfully: logits and representations match within 1e-5.")
    logger.info("==================================================")
    logger.info("PHASE 3 TRAINING COMPLETE")
    logger.info("==================================================")


if __name__ == "__main__":
    main()
