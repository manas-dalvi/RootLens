"""
Exhaustive verification script for Phase 5B Final Quality Gate:
1. Exact count and percentage of sequences exceeding 62-event capacity (overall and per split).
2. Truncation impact analysis: position of anomaly tokens within long sequences.
3. Subsets nesting verification: verify sampled splits are genuinely strict subsets of full partitions.
4. Split disjointness and test-set isolation reconfirmation.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set, Tuple

PROCESSED_DIR = Path("data/benchmarks/hdfs_full/processed")


def verify_capacity_and_truncation():
    print("=== 1. SEQUENCE CAPACITY & TRUNCATION ANALYSIS (max_events = 62) ===")

    splits = ["train", "val", "test"]
    split_data = {}
    for sp in splits:
        with open(PROCESSED_DIR / f"{sp}_sessions.json", "r", encoding="utf-8") as f:
            split_data[sp] = json.load(f)

    all_sessions = []
    for sp in splits:
        all_sessions.extend(split_data[sp])

    total_sessions = len(all_sessions)
    exceeding_overall = [s for s in all_sessions if len(s["events"]) > 62]
    exceeding_count = len(exceeding_overall)
    exceeding_pct = exceeding_count / total_sessions * 100

    print(f"Total sessions: {total_sessions:,}")
    print(f"Sequences exceeding 62 events: {exceeding_count:,} ({exceeding_pct:.4f}%)")
    print(f"Sequences within 62 events:    {total_sessions - exceeding_count:,} ({100 - exceeding_pct:.4f}%)")

    # Breakdown by normal vs anomaly
    norm_exceeding = [s for s in exceeding_overall if s["label"] == 0]
    anom_exceeding = [s for s in exceeding_overall if s["label"] == 1]
    total_norm = sum(1 for s in all_sessions if s["label"] == 0)
    total_anom = sum(1 for s in all_sessions if s["label"] == 1)

    print(f"\nNormal sequences exceeding 62:    {len(norm_exceeding):,}/{total_norm:,} ({len(norm_exceeding)/total_norm*100:.4f}%)")
    print(f"Anomalous sequences exceeding 62: {len(anom_exceeding):,}/{total_anom:,} ({len(anom_exceeding)/total_anom*100:.4f}%)")

    # Per split
    for sp in splits:
        sp_sess = split_data[sp]
        sp_exc = sum(1 for s in sp_sess if len(s["events"]) > 62)
        print(f"  {sp:5s} split exceeding 62: {sp_exc:,}/{len(sp_sess):,} ({sp_exc/len(sp_sess)*100:.4f}%)")

    # Truncation Information Loss Analysis
    print("\n--- Truncation Information Loss on Anomalous Sequences ---")
    # For anomalous sequences exceeding 62 events, where do anomaly/rare/error templates occur?
    # Failure templates: E1, E7, E8, E10, E12, E13, E14, E15, E17, E19, E20, E24, E29
    ERROR_TEMPLATES = {"E1", "E7", "E8", "E10", "E12", "E13", "E14", "E15", "E17", "E19", "E20", "E24", "E29"}

    anom_with_error_tokens = 0
    anom_error_tokens_retained_in_head = 0
    anom_error_tokens_retained_in_tail = 0

    for s in anom_exceeding:
        events = s["events"]
        error_indices = [i for i, ev in enumerate(events) if ev in ERROR_TEMPLATES]
        if error_indices:
            anom_with_error_tokens += 1
            # Check head truncation (events[:62])
            head_retains = any(idx < 62 for idx in error_indices)
            if head_retains:
                anom_error_tokens_retained_in_head += 1
            # Check tail truncation (events[-62:])
            tail_retains = any(idx >= len(events) - 62 for idx in error_indices)
            if tail_retains:
                anom_error_tokens_retained_in_tail += 1

    print(f"Anomalous sequences > 62 with explicit error templates: {anom_with_error_tokens:,}/{len(anom_exceeding):,}")
    print(f"Head truncation (events[:62]) retains error token:      {anom_error_tokens_retained_in_head:,}/{anom_with_error_tokens:,} ({anom_error_tokens_retained_in_head/anom_with_error_tokens*100:.2f}%)")
    print(f"Tail truncation (events[-62:]) retains error token:     {anom_error_tokens_retained_in_tail:,}/{anom_with_error_tokens:,} ({anom_error_tokens_retained_in_tail/anom_with_error_tokens*100:.2f}%)")


def verify_sampled_subsets_nesting():
    print("\n=== 2. SUBSETS NESTING VERIFICATION ===")
    splits = ["train", "val", "test"]

    for sp in splits:
        with open(PROCESSED_DIR / f"{sp}_sessions.json", "r", encoding="utf-8") as f:
            full_list = json.load(f)
        with open(PROCESSED_DIR / f"{sp}_sessions_sampled.json", "r", encoding="utf-8") as f:
            sampled_list = json.load(f)

        full_blocks = set(s["block_id"] for s in full_list)
        sampled_blocks = set(s["block_id"] for s in sampled_list)

        is_strict_subset = sampled_blocks.issubset(full_blocks)
        overlap_count = len(sampled_blocks & full_blocks)
        assert is_strict_subset, f"CRITICAL: {sp}_sessions_sampled is NOT a subset of {sp}_sessions!"
        assert len(sampled_blocks) == len(sampled_list), f"Duplicate blocks in {sp}_sessions_sampled"

        print(f"{sp.capitalize()} Sampled: {len(sampled_list):,} sessions -> 100% strictly nested within {sp} ({overlap_count:,}/{len(sampled_list):,})")

    # Cross-split overlap on sampled
    with open(PROCESSED_DIR / "train_sessions_sampled.json", "r", encoding="utf-8") as f:
        tr_s = set(s["block_id"] for s in json.load(f))
    with open(PROCESSED_DIR / "val_sessions_sampled.json", "r", encoding="utf-8") as f:
        val_s = set(s["block_id"] for s in json.load(f))
    with open(PROCESSED_DIR / "test_sessions_sampled.json", "r", encoding="utf-8") as f:
        te_s = set(s["block_id"] for s in json.load(f))

    assert len(tr_s & val_s) == 0, "Train sampled and Val sampled overlap!"
    assert len(tr_s & te_s) == 0, "Train sampled and Test sampled overlap!"
    assert len(val_s & te_s) == 0, "Val sampled and Test sampled overlap!"
    print("Zero overlap between all sampled splits: VERIFIED.")


def verify_label_counts_and_disjointness():
    print("\n=== 3. FULL PARTITION DISJOINTNESS & ISOLATION RECONFIRMATION ===")
    splits = ["train", "val", "test"]
    blocks = {}
    labels = {}

    for sp in splits:
        with open(PROCESSED_DIR / f"{sp}_sessions.json", "r", encoding="utf-8") as f:
            sess = json.load(f)
        blocks[sp] = set(s["block_id"] for s in sess)
        labels[sp] = [s["label"] for s in sess]

    # Disjointness
    tr_val = blocks["train"] & blocks["val"]
    tr_te = blocks["train"] & blocks["test"]
    val_te = blocks["val"] & blocks["test"]

    print(f"Train / Val overlap:  {len(tr_val)}")
    print(f"Train / Test overlap: {len(tr_te)}")
    print(f"Val / Test overlap:   {len(val_te)}")
    assert len(tr_val) == 0 and len(tr_te) == 0 and len(val_te) == 0, "Data leakage detected across splits!"

    # Label counts
    print(f"Train sessions: {len(blocks['train']):,} (Normal: {labels['train'].count(0):,}, Anomaly: {labels['train'].count(1):,})")
    print(f"Val sessions:   {len(blocks['val']):,} (Normal: {labels['val'].count(0):,}, Anomaly: {labels['val'].count(1):,})")
    print(f"Test sessions:  {len(blocks['test']):,} (Normal: {labels['test'].count(0):,}, Anomaly: {labels['test'].count(1):,})")

    assert labels["train"].count(1) == 0, "CRITICAL: Training partition contains anomalous sessions!"
    assert labels["val"].count(1) == 8419, "Unexpected anomaly count in Val"
    assert labels["test"].count(1) == 8419, "Unexpected anomaly count in Test"
    print("Strict Zero-Leakage (Train Normal == 100%, Train Anomaly == 0): VERIFIED.")


if __name__ == "__main__":
    verify_capacity_and_truncation()
    verify_sampled_subsets_nesting()
    verify_label_counts_and_disjointness()
