from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ParsedRecord:
    """Represents a single parsed log record with raw content and line references."""

    line_number: int
    raw: str
    extracted: Dict[str, Any] = field(default_factory=dict)
    is_valid: bool = True
    error: Optional[str] = None


@dataclass
class ParsingResult:
    """Summary and collection of parsed records for an ingestion file."""

    format_type: str
    total_records: int
    successful_records: int
    failed_records: int
    records: List[ParsedRecord] = field(default_factory=list)
    error_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "format_type": self.format_type,
            "total_records": self.total_records,
            "successful_records": self.successful_records,
            "failed_records": self.failed_records,
            "error_summary": self.error_summary,
            "records": [
                {
                    "line_number": r.line_number,
                    "raw": r.raw,
                    "extracted": r.extracted,
                    "is_valid": r.is_valid,
                    "error": r.error,
                }
                for r in self.records
            ],
        }


def read_file_lines_safe(file_path: Path) -> List[str]:
    """Read file content line-by-line supporting UTF-8, UTF-8-BOM, and Latin-1 fallbacks."""
    try:
        with open(file_path, "r", encoding="utf-8-sig", errors="strict") as f:
            return f.readlines()
    except (UnicodeDecodeError, LookupError):
        with open(file_path, "r", encoding="latin-1", errors="replace") as f:
            return f.readlines()


class BaseLogParser(ABC):
    """Abstract base class for format-specific log parsers."""

    @abstractmethod
    def parse_lines(self, lines: List[str]) -> ParsingResult:
        """Parse in-memory raw log lines into structured records."""
        pass

    def parse_file(self, file_path: Path) -> ParsingResult:
        """Read and parse a log file from local storage safely."""
        if not file_path.exists():
            return ParsingResult(
                format_type="unknown",
                total_records=0,
                successful_records=0,
                failed_records=0,
                records=[],
                error_summary=f"Log file does not exist on disk: {file_path.name}",
            )
        lines = read_file_lines_safe(file_path)
        return self.parse_lines(lines)
