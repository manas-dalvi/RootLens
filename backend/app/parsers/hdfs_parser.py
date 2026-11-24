"""
Native parser for raw HDFS benchmark log streams and files.
Parses the standard LogPAI LogHub HDFS benchmark log format:
Format:
  YYMMDD HHMMSS PID LEVEL component: message
Example:
  081109 203518 143 INFO dfs.DataNode$DataXceiver: Receiving block blk_-1608999687919862906 src: /10.250.19.102:54106 dest: /10.250.19.102:50010

Guarantees:
- Maps raw log messages to the exact 29 canonical event templates (E1..E29) from the benchmark.
- Extracts block_id (blk_[-0-9]+) as the primary session key for LogBERT.
- Normalizes timestamps to UTC datetime representations.
- Safely quarantines malformed lines with detailed diagnostic error messages.
- Strictly differentiates HDFS logs from ordinary enterprise microservice logs.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.parsers.base import BaseLogParser, ParsedRecord, ParsingResult

# Raw HDFS Benchmark Line Pattern
# e.g.: 081109 203518 143 INFO dfs.DataNode$DataXceiver: Receiving block blk_-1608999687919862906 src: ...
HDFS_RAW_PATTERN = re.compile(
    r"^(?P<date>\d{6})\s+(?P<time>\d{6})\s+(?P<pid>\d+)\s+(?P<level>[A-Z]+)\s+(?P<component>[^:]+):\s*(?P<message>.*)$"
)

# Block ID Pattern
BLOCK_ID_REGEX = re.compile(r"(blk_[-0-9]+)")

# 29 canonical event templates from HDFS.log_templates.csv
TEMPLATE_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
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


def match_hdfs_template(message: str) -> Optional[str]:
    """Match log message against the 29 canonical benchmark event templates."""
    for event_id, pattern in TEMPLATE_PATTERNS:
        if pattern.search(message):
            return event_id
    return None


def map_hdfs_component_to_service(component: str) -> str:
    """Map HDFS Java component name to standard service identifier."""
    comp = component.strip().lower()
    if "datanode" in comp:
        return "hdfs-datanode"
    if "fsnamesystem" in comp or "namenode" in comp:
        return "hdfs-namenode"
    return "hdfs"


class HDFSLogParser(BaseLogParser):
    """
    Parser for raw LogPAI LogHub HDFS benchmark log files.
    """

    @classmethod
    def is_hdfs_format(cls, lines: List[str], sample_size: int = 20) -> bool:
        """
        Heuristic to detect whether an arbitrary log stream conforms to HDFS benchmark format.
        Requires sample lines to match HDFS_RAW_PATTERN, have a dfs. component, and contain block IDs.
        """
        non_empty = [ln.strip() for ln in lines if ln.strip()][:sample_size]
        if not non_empty:
            return False

        hdfs_matches = 0
        for line in non_empty:
            m = HDFS_RAW_PATTERN.match(line)
            if m:
                component = m.group("component").lower()
                message = m.group("message")
                if component.startswith("dfs.") and BLOCK_ID_REGEX.search(message):
                    hdfs_matches += 1

        # Must have at least 80% matching sample lines
        return (hdfs_matches / len(non_empty)) >= 0.8

    def parse_lines(self, lines: List[str]) -> ParsingResult:
        """
        Parse raw HDFS log lines into structured ParsedRecord items.
        Safely quarantines malformed or unmapped records.
        """
        records: List[ParsedRecord] = []
        successful = 0
        failed = 0

        for idx, line in enumerate(lines, start=1):
            raw_line = line.rstrip("\r\n")
            if not raw_line.strip():
                continue

            m = HDFS_RAW_PATTERN.match(raw_line)
            if not m:
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=raw_line,
                        extracted={"message": raw_line},
                        is_valid=False,
                        error="Malformed HDFS record: does not match benchmark timestamp/component header structure",
                    )
                )
                failed += 1
                continue

            date_str = m.group("date")
            time_str = m.group("time")
            pid_str = m.group("pid")
            level_str = m.group("level").upper()
            component_str = m.group("component").strip()
            message_str = m.group("message").strip()

            # Parse datetime
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%y%m%d %H%M%S").replace(
                    tzinfo=timezone.utc
                )
                iso_timestamp = dt.isoformat()
            except ValueError:
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=raw_line,
                        extracted={"message": raw_line},
                        is_valid=False,
                        error=f"Malformed HDFS record: invalid date/time digits '{date_str} {time_str}'",
                    )
                )
                failed += 1
                continue

            # Extract block ID
            block_match = BLOCK_ID_REGEX.search(message_str)
            if not block_match:
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=raw_line,
                        extracted={
                            "timestamp": iso_timestamp,
                            "log_level": level_str,
                            "component": component_str,
                            "message": message_str,
                        },
                        is_valid=False,
                        error="Quarantined HDFS record: missing required block ID (blk_-...)",
                    )
                )
                failed += 1
                continue

            block_id = block_match.group(1)

            # Match event template
            event_id = match_hdfs_template(message_str)
            if not event_id:
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=raw_line,
                        extracted={
                            "timestamp": iso_timestamp,
                            "log_level": level_str,
                            "component": component_str,
                            "message": message_str,
                            "block_id": block_id,
                        },
                        is_valid=False,
                        error="Quarantined HDFS record: message did not match any canonical HDFS event template (E1..E29)",
                    )
                )
                failed += 1
                continue

            service_name = map_hdfs_component_to_service(component_str)

            extracted: Dict[str, Any] = {
                "timestamp": iso_timestamp,
                "log_level": level_str,
                "service_name": service_name,
                "component": component_str,
                "message": message_str,
                "block_id": block_id,
                "event_id": event_id,
                "template": event_id,
                "pid": pid_str,
                "domain": "hdfs",
                "dataset": "hdfs_benchmark",
                "raw_log": raw_line,
            }

            records.append(
                ParsedRecord(
                    line_number=idx,
                    raw=raw_line,
                    extracted=extracted,
                    is_valid=True,
                )
            )
            successful += 1

        error_summary = None
        if failed > 0:
            error_summary = f"{failed} of {successful + failed} records quarantined due to formatting/template mismatch."

        return ParsingResult(
            format_type="hdfs",
            total_records=successful + failed,
            successful_records=successful,
            failed_records=failed,
            records=records,
            error_summary=error_summary,
        )
