"""
HDFS Dataset and Log Sequence representation for LogBERT.
Parses structured logs, groups events into block sessions, and matches ground truth labels.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# Regex to extract HDFS Block IDs, e.g., blk_-1608999687919862906 or blk_38865049064139660
BLOCK_ID_REGEX = re.compile(r"(blk_[-0-9]+)")


@dataclass
class SessionSequence:
    """
    Represents an ordered sequence of log events corresponding to a single HDFS block session.
    """
    block_id: str
    events: List[str]
    label: int  # 0: Normal, 1: Anomaly
    line_ids: List[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.events)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "events": self.events,
            "label": self.label,
            "line_ids": self.line_ids,
            "length": len(self.events),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SessionSequence:
        return cls(
            block_id=data["block_id"],
            events=list(data["events"]),
            label=int(data["label"]),
            line_ids=list(data.get("line_ids", [])),
        )


def extract_block_id(text: str) -> Optional[str]:
    """Extract HDFS block identifier (blk_<number>) from log message text."""
    match = BLOCK_ID_REGEX.search(text)
    return match.group(1) if match else None


def load_anomaly_labels(label_file_path: Union[str, Path]) -> Dict[str, int]:
    """
    Load HDFS ground truth labels from CSV file.
    Maps BlockId to 0 (Normal) or 1 (Anomaly).
    """
    path = Path(label_file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Anomaly label file not found: {path}")

    labels: Dict[str, int] = {}
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            block_id = row.get("BlockId") or row.get("block_id")
            label_str = row.get("Label") or row.get("label")
            if not block_id or not label_str:
                continue

            normalized_label = label_str.strip().lower()
            if normalized_label in ("normal", "0"):
                labels[block_id.strip()] = 0
            elif normalized_label in ("anomaly", "anomalous", "1"):
                labels[block_id.strip()] = 1
            else:
                raise ValueError(f"Unknown label '{label_str}' for block '{block_id}'")

    return labels


def build_session_sequences(
    structured_log_path: Union[str, Path],
    label_file_path: Union[str, Path],
    require_labels: bool = True,
) -> List[SessionSequence]:
    """
    Parse a structured HDFS log CSV, group events by block session,
    and associate each session with its ground truth label.
    """
    log_path = Path(structured_log_path)
    if not log_path.is_file():
        raise FileNotFoundError(f"Structured log file not found: {log_path}")

    labels = load_anomaly_labels(label_file_path)

    # Group by block_id maintaining chronological order
    session_events: Dict[str, List[str]] = {}
    session_line_ids: Dict[str, List[int]] = {}
    first_seen_order: List[str] = []

    with open(log_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Extract block ID either from Content or from explicit BlockId column
            content = row.get("Content", "")
            block_id = row.get("BlockId") or extract_block_id(content)
            if not block_id:
                continue

            block_id = block_id.strip()
            event_id = row.get("EventId", "").strip()
            line_id_str = row.get("LineId", "0").strip()
            line_id = int(line_id_str) if line_id_str.isdigit() else 0

            if block_id not in session_events:
                session_events[block_id] = []
                session_line_ids[block_id] = []
                first_seen_order.append(block_id)

            session_events[block_id].append(event_id)
            session_line_ids[block_id].append(line_id)

    # Build SessionSequence objects
    sequences: List[SessionSequence] = []
    unlabeled_count = 0

    for block_id in first_seen_order:
        events = session_events[block_id]
        if not events:
            continue

        if block_id in labels:
            label = labels[block_id]
        else:
            unlabeled_count += 1
            if require_labels:
                raise KeyError(f"Block '{block_id}' present in logs but missing from anomaly labels")
            label = 0  # Default to normal if not strictly required

        sequences.append(
            SessionSequence(
                block_id=block_id,
                events=events,
                label=label,
                line_ids=session_line_ids[block_id],
            )
        )

    return sequences


def save_sessions_json(sessions: List[SessionSequence], file_path: Union[str, Path]) -> None:
    """Save a list of session sequences to JSON."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [s.to_dict() for s in sessions]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_sessions_json(file_path: Union[str, Path]) -> List[SessionSequence]:
    """Load a list of session sequences from JSON."""
    path = Path(file_path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [SessionSequence.from_dict(item) for item in data]
