"""
Audits the full HDFS benchmark dataset:
1. Raw HDFS.log line counts, block ID extraction, parser coverage.
2. Anomaly label counts and distribution.
3. Event_traces sequence properties, lengths, and label matches.
4. Cross-file integrity and unmatched block IDs.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

BLOCK_ID_REGEX = re.compile(r"(blk_[-0-9]+)")

# 29 canonical event templates from HDFS.log_templates.csv
# We build regex / keyword matchers to verify parser coverage on raw lines
TEMPLATE_PATTERNS = [
    ("E1", re.compile(r"Adding an already existing block")),
    ("E2", re.compile(r"Verification succeeded for")),
    ("E3", re.compile(r"Served block .* to")),
    ("E4", re.compile(r"Got exception while serving .* to")),
    ("E5", re.compile(r"Receiving block .* src: .* dest:")),
    ("E6", re.compile(r"Received block .* src: .* dest: .* of size")),
    ("E7", re.compile(r"writeBlock .* received exception")),
    ("E8", re.compile(r"PacketResponder .* for block .* Interrupted")),
    ("E9", re.compile(r"Received block .* of size .* from")),
    ("E10", re.compile(r"PacketResponder .* Exception")),
    ("E11", re.compile(r"PacketResponder .* for block .* terminating")),
    ("E12", re.compile(r"Exception writing block .* to mirror")),
    ("E13", re.compile(r"Receiving empty packet for block")),
    ("E14", re.compile(r"Exception in receiveBlock for block")),
    ("E15", re.compile(r"Changing block file offset of block")),
    ("E16", re.compile(r"Transmitted block .* to")),
    ("E17", re.compile(r"Failed to transfer .* to .* got")),
    ("E18", re.compile(r"Starting thread to transfer block .* to")),
    ("E19", re.compile(r"Reopen Block")),
    ("E20", re.compile(r"Unexpected error trying to delete block .* BlockInfo not found in volumeMap")),
    ("E21", re.compile(r"Deleting block .* file")),
    ("E22", re.compile(r"BLOCK\* NameSystem\.allocateBlock:")),
    ("E23", re.compile(r"BLOCK\* NameSystem\.delete: .* is added to invalidSet of")),
    ("E24", re.compile(r"BLOCK\* Removing block .* from neededReplications")),
    ("E25", re.compile(r"BLOCK\* ask .* to replicate .* to")),
    ("E26", re.compile(r"BLOCK\* NameSystem\.addStoredBlock: blockMap updated:")),
    ("E27", re.compile(r"BLOCK\* NameSystem\.addStoredBlock: Redundant addStoredBlock request received")),
    ("E28", re.compile(r"BLOCK\* NameSystem\.addStoredBlock: addStoredBlock request received .* But it does not belong to any file")),
    ("E29", re.compile(r"PendingReplicationMonitor timed out block")),
]


def match_template(line: str) -> str | None:
    """Match log line to one of the 29 canonical event templates."""
    for event_id, pattern in TEMPLATE_PATTERNS:
        if pattern.search(line):
            return event_id
    return None


def audit_labels(label_csv_path: Path) -> Tuple[Dict[str, int], Dict[str, Any]]:
    print(f"\n--- Auditing Ground Truth Labels: {label_csv_path} ---")
    labels: Dict[str, int] = {}
    label_counts = Counter()
    duplicate_blocks = 0
    malformed_rows = 0

    with open(label_csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            b_id = row.get("BlockId")
            lbl = row.get("Label")
            if not b_id or not lbl:
                malformed_rows += 1
                continue
            b_id = b_id.strip()
            lbl = lbl.strip().lower()
            if b_id in labels:
                duplicate_blocks += 1

            if lbl in ("normal", "success", "0"):
                labels[b_id] = 0
                label_counts["Normal"] += 1
            elif lbl in ("anomaly", "anomalous", "fail", "1"):
                labels[b_id] = 1
                label_counts["Anomaly"] += 1
            else:
                label_counts[f"Unknown_{lbl}"] += 1

    stats = {
        "total_labels": len(labels),
        "normal_count": label_counts["Normal"],
        "anomaly_count": label_counts["Anomaly"],
        "anomaly_percentage": round(label_counts["Anomaly"] / len(labels) * 100, 4) if labels else 0.0,
        "duplicate_blocks": duplicate_blocks,
        "malformed_rows": malformed_rows,
    }
    print(f"Total Unique Block Labels: {stats['total_labels']:,}")
    print(f"Normal: {stats['normal_count']:,} ({100 - stats['anomaly_percentage']:.2f}%)")
    print(f"Anomaly: {stats['anomaly_count']:,} ({stats['anomaly_percentage']:.2f}%)")
    print(f"Duplicate Block IDs in CSV: {duplicate_blocks}")
    print(f"Malformed Rows: {malformed_rows}")
    return labels, stats


def audit_event_traces(
    traces_csv_path: Path,
    labels: Dict[str, int]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    print(f"\n--- Auditing Event Traces: {traces_csv_path} ---")
    t0 = time.time()
    trace_blocks: Set[str] = set()
    label_mismatches = 0
    unlabeled_blocks = 0
    lengths: List[int] = []
    normal_lengths: List[int] = []
    anomaly_lengths: List[int] = []
    trace_event_counts = Counter()

    # Length bins
    bin_1 = 0
    bin_2_5 = 0
    bin_6_10 = 0
    bin_11_20 = 0
    bin_gt_20 = 0

    sessions_summary: List[Dict[str, Any]] = []

    with open(traces_csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            b_id = row.get("BlockId", "").strip()
            lbl_str = row.get("Label", "").strip().lower()
            features_str = row.get("Features", "").strip()

            if not b_id or not features_str:
                continue

            trace_blocks.add(b_id)
            # Parse events list: e.g. "[E5,E22,E5]" -> ["E5", "E22", "E5"]
            raw_features = features_str.strip("[]").replace("'", "").replace('"', "").split(",")
            events = [e.strip() for e in raw_features if e.strip()]
            seq_len = len(events)
            lengths.append(seq_len)

            # Map label
            gt_label = labels.get(b_id)
            if gt_label is None:
                unlabeled_blocks += 1
                curr_label = 1 if lbl_str == "fail" else 0
            else:
                curr_label = gt_label
                expected_lbl_str = "fail" if gt_label == 1 else "success"
                if lbl_str != expected_lbl_str:
                    label_mismatches += 1

            if curr_label == 0:
                normal_lengths.append(seq_len)
            else:
                anomaly_lengths.append(seq_len)

            for ev in events:
                trace_event_counts[ev] += 1

            if seq_len == 1:
                bin_1 += 1
            elif 2 <= seq_len <= 5:
                bin_2_5 += 1
            elif 6 <= seq_len <= 10:
                bin_6_10 += 1
            elif 11 <= seq_len <= 20:
                bin_11_20 += 1
            else:
                bin_gt_20 += 1

            if i < 10:
                sessions_summary.append({
                    "block_id": b_id,
                    "label": curr_label,
                    "length": seq_len,
                    "first_events": events[:5],
                })

    elapsed = time.time() - t0
    lengths.sort()
    n = len(lengths)

    stats = {
        "total_traces": n,
        "runtime_seconds": round(elapsed, 2),
        "unlabeled_blocks": unlabeled_blocks,
        "label_mismatches_with_anomaly_label_csv": label_mismatches,
        "unique_templates_in_traces": len(trace_event_counts),
        "template_counts": dict(trace_event_counts),
        "length_stats": {
            "min": lengths[0] if lengths else 0,
            "max": lengths[-1] if lengths else 0,
            "mean": round(sum(lengths) / n, 2) if n else 0.0,
            "median": lengths[n // 2] if n else 0,
            "p25": lengths[int(n * 0.25)] if n else 0,
            "p50": lengths[int(n * 0.50)] if n else 0,
            "p75": lengths[int(n * 0.75)] if n else 0,
            "p90": lengths[int(n * 0.90)] if n else 0,
            "p95": lengths[int(n * 0.95)] if n else 0,
            "p99": lengths[int(n * 0.99)] if n else 0,
        },
        "length_bins": {
            "length_1": bin_1,
            "length_1_pct": round(bin_1 / n * 100, 2),
            "length_2_to_5": bin_2_5,
            "length_2_to_5_pct": round(bin_2_5 / n * 100, 2),
            "length_6_to_10": bin_6_10,
            "length_6_to_10_pct": round(bin_6_10 / n * 100, 2),
            "length_11_to_20": bin_11_20,
            "length_11_to_20_pct": round(bin_11_20 / n * 100, 2),
            "length_gt_20": bin_gt_20,
            "length_gt_20_pct": round(bin_gt_20 / n * 100, 2),
        },
    }

    print(f"Processed {n:,} block sequences in {elapsed:.1f}s.")
    print(f"Length distribution: min={stats['length_stats']['min']}, max={stats['length_stats']['max']}, mean={stats['length_stats']['mean']}, median={stats['length_stats']['median']}, p95={stats['length_stats']['p95']}, p99={stats['length_stats']['p99']}")
    print(f"Length Bins:")
    print(f"  Length = 1:        {bin_1:,} ({stats['length_bins']['length_1_pct']}%)")
    print(f"  Length 2–5:        {bin_2_5:,} ({stats['length_bins']['length_2_to_5_pct']}%)")
    print(f"  Length 6–10:       {bin_6_10:,} ({stats['length_bins']['length_6_to_10_pct']}%)")
    print(f"  Length 11–20:      {bin_11_20:,} ({stats['length_bins']['length_11_to_20_pct']}%)")
    print(f"  Length > 20:       {bin_gt_20:,} ({stats['length_bins']['length_gt_20_pct']}%)")
    print(f"Label mismatches with anomaly_label.csv: {label_mismatches}")
    print(f"Unlabeled blocks: {unlabeled_blocks}")
    print(f"Unique event templates present: {len(trace_event_counts)}")

    return sessions_summary, stats


def audit_raw_log_sample(raw_log_path: Path, sample_lines: int = 100_000) -> Dict[str, Any]:
    print(f"\n--- Auditing Raw HDFS.log Sample ({sample_lines:,} lines) for Parser Coverage ---")
    t0 = time.time()
    total_lines = 0
    blocks_found = 0
    parsed_lines = 0
    unparsed_lines = 0
    unparsed_samples: List[str] = []
    template_match_counts = Counter()

    with open(raw_log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            total_lines += 1
            if BLOCK_ID_REGEX.search(line):
                blocks_found += 1

            matched_id = match_template(line)
            if matched_id:
                parsed_lines += 1
                template_match_counts[matched_id] += 1
            else:
                unparsed_lines += 1
                if len(unparsed_samples) < 5:
                    unparsed_samples.append(line.strip()[:120])

            if total_lines >= sample_lines:
                break

    elapsed = time.time() - t0
    coverage_pct = round(parsed_lines / total_lines * 100, 2) if total_lines else 0.0
    print(f"Sampled {total_lines:,} lines in {elapsed:.2f}s.")
    print(f"Lines containing block IDs: {blocks_found:,} ({blocks_found / total_lines * 100:.2f}%)")
    print(f"Parsed Lines: {parsed_lines:,} ({coverage_pct}%)")
    print(f"Unparsed Lines: {unparsed_lines:,} ({round(unparsed_lines / total_lines * 100, 2)}%)")
    if unparsed_samples:
        print("Sample unparsed lines:")
        for s in unparsed_samples:
            print(f"  {s}")

    return {
        "sample_lines": total_lines,
        "runtime_seconds": round(elapsed, 2),
        "lines_with_block_ids": blocks_found,
        "parsed_lines": parsed_lines,
        "unparsed_lines": unparsed_lines,
        "parser_coverage_pct": coverage_pct,
        "template_counts": dict(template_match_counts),
        "sample_unparsed": unparsed_samples,
    }


def count_raw_lines_and_blocks(raw_log_path: Path) -> Dict[str, Any]:
    print(f"\n--- Scanning Total Raw Lines and Unique Block IDs in {raw_log_path} ---")
    t0 = time.time()
    total_lines = 0
    block_set: Set[str] = set()

    with open(raw_log_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            total_lines += 1
            matches = BLOCK_ID_REGEX.findall(line)
            for m in matches:
                block_set.add(m)

    elapsed = time.time() - t0
    print(f"Scan complete in {elapsed:.1f}s.")
    print(f"Total Raw Lines: {total_lines:,}")
    print(f"Total Unique Block IDs: {len(block_set):,}")
    return {
        "total_raw_lines": total_lines,
        "unique_raw_block_ids": len(block_set),
        "scan_runtime_seconds": round(elapsed, 2),
    }


def main():
    raw_dir = Path("data/benchmarks/hdfs_full/raw")
    labels_file = raw_dir / "anomaly_label.csv"
    traces_file = raw_dir / "Event_traces.csv"
    raw_log_file = raw_dir / "HDFS.log"

    print("==================================================")
    print("ROOTLENS HDFS FULL BENCHMARK DATASET INTEGRITY AUDIT")
    print("==================================================")

    # 1. Labels
    labels, label_stats = audit_labels(labels_file)

    # 2. Event Traces
    samples, trace_stats = audit_event_traces(traces_file, labels)

    # 3. Parser coverage sample
    parser_sample_stats = audit_raw_log_sample(raw_log_file, sample_lines=200_000)

    # 4. Total lines scan
    raw_stats = count_raw_lines_and_blocks(raw_log_file)

    # Cross check blocks
    unmatched_labels = len(labels) - trace_stats["total_traces"]

    full_report = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "raw_log_audit": raw_stats,
        "ground_truth_label_audit": label_stats,
        "event_traces_audit": trace_stats,
        "parser_coverage_sample": parser_sample_stats,
        "cross_file_integrity": {
            "total_blocks_in_labels": len(labels),
            "total_blocks_in_traces": trace_stats["total_traces"],
            "total_blocks_in_raw": raw_stats["unique_raw_block_ids"],
            "unmatched_blocks": unmatched_labels,
            "labels_match_traces_100_percent": (len(labels) == trace_stats["total_traces"]),
        },
        "sample_sessions": samples,
    }

    report_path = raw_dir.parent / "full_dataset_integrity_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)
    print(f"\nSaved integrity report: {report_path}")


if __name__ == "__main__":
    main()
