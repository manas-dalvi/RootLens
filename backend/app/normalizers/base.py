from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.normalizers.aliases import resolve_aliases
from app.normalizers.severity import normalize_severity
from app.normalizers.timestamp import normalize_timestamp
from app.parsers.base import ParsedRecord, ParsingResult


@dataclass
class NormalizedLog:
    """Canonical representation of a normalized and validated RootLens log document.

    Adheres to docs/architecture.md and core OpenSearch mappings:
    - Core search fields: timestamp (UTC), service_name, environment, log_level, message
    - Correlation fields: host, trace_id, span_id, attributes
    - Diagnostic metadata: raw_log, line_number, source_format, is_valid, validation_errors
    """

    timestamp: Optional[datetime]
    service_name: str
    environment: str
    log_level: Optional[str]
    message: str
    host: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    raw_log: str = ""

    line_number: int = 1
    source_format: str = "unknown"
    is_valid: bool = True
    validation_errors: List[str] = field(default_factory=list)
    raw_timestamp: Optional[str] = None
    raw_level: Optional[str] = None
    original_fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert into JSON-serializable dictionary."""
        return {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "service_name": self.service_name,
            "environment": self.environment,
            "log_level": self.log_level,
            "message": self.message,
            "host": self.host,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "attributes": self.attributes,
            "raw_log": self.raw_log,
            "line_number": self.line_number,
            "source_format": self.source_format,
            "is_valid": self.is_valid,
            "validation_errors": self.validation_errors,
            "raw_timestamp": self.raw_timestamp,
            "raw_level": self.raw_level,
            "original_fields": self.original_fields,
        }


@dataclass
class NormalizationResult:
    """Collection and statistics of normalized logs for an ingestion run."""

    total_records: int
    valid_records: int
    invalid_records: int
    records: List[NormalizedLog] = field(default_factory=list)
    summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert into JSON-serializable dictionary."""
        return {
            "total_records": self.total_records,
            "valid_records": self.valid_records,
            "invalid_records": self.invalid_records,
            "summary": self.summary,
            "records": [r.to_dict() for r in self.records],
        }


def normalize_parsed_record(
    record: ParsedRecord,
    source_format: str,
    default_service: str = "default",
    default_env: str = "production",
) -> NormalizedLog:
    """Normalize a single ParsedRecord into the canonical RootLens log representation.

    Rules:
    - Resolves field aliases for timestamp, level, service, host, message, trace_id, span_id, environment.
    - Preserves unmapped fields in attributes.
    - Normalizes recognized severity values without inventing missing values.
    - Normalizes timestamps to UTC while retaining raw values.
    - Preserves raw log line, source format, line number, and original extracted fields.
    - Distinguishes missing, invalid, and successfully normalized fields.
    """
    validation_errors: List[str] = []

    # If the record failed parser-level extraction (e.g. malformed CSV row, broken JSON)
    if not record.is_valid and record.error:
        validation_errors.append(f"Parser error: {record.error}")

    resolved = resolve_aliases(record.extracted)

    # 1. Message resolution
    raw_msg = resolved.raw_message
    if raw_msg is not None and str(raw_msg).strip():
        message = str(raw_msg).strip()
    elif record.raw and record.raw.strip():
        # Fallback to verbatim raw line if no structured message key exists
        message = record.raw.strip()
    else:
        message = ""
        validation_errors.append("Missing message content")

    # 2. Timestamp normalization
    raw_ts_str = str(resolved.raw_timestamp) if resolved.raw_timestamp is not None else None
    normalized_ts, ts_err = normalize_timestamp(resolved.raw_timestamp)
    if ts_err:
        validation_errors.append(ts_err)

    # 3. Severity / log level normalization
    raw_lvl_str = str(resolved.raw_level) if resolved.raw_level is not None else None
    normalized_lvl, lvl_err = normalize_severity(resolved.raw_level)
    if lvl_err:
        # Note: missing severity is recorded in validation_errors without fabricating a fake level
        validation_errors.append(lvl_err)

    # 4. Service name resolution
    service_name = (
        resolved.raw_service.strip()
        if resolved.raw_service and resolved.raw_service.strip()
        else default_service
    )

    # 5. Environment resolution
    environment = (
        resolved.raw_environment.strip()
        if resolved.raw_environment and resolved.raw_environment.strip()
        else default_env
    )

    # 6. Overall record validity determination
    # A record is valid if:
    # - Parser succeeded (record.is_valid is True)
    # - Timestamp was parsed successfully
    # - Message is present and non-empty
    # Note: Missing severity is noted in validation_errors but does not inherently drop the record.
    # Unrecognized severity ("UNKNOWN") is flagged.
    is_valid = (
        record.is_valid
        and normalized_ts is not None
        and bool(message)
        and normalized_lvl != "UNKNOWN"
    )

    return NormalizedLog(
        timestamp=normalized_ts,
        service_name=service_name,
        environment=environment,
        log_level=normalized_lvl,
        message=message,
        host=resolved.raw_host,
        trace_id=resolved.raw_trace_id,
        span_id=resolved.raw_span_id,
        attributes=resolved.attributes,
        raw_log=record.raw,
        line_number=record.line_number,
        source_format=source_format,
        is_valid=is_valid,
        validation_errors=validation_errors,
        raw_timestamp=raw_ts_str,
        raw_level=raw_lvl_str,
        original_fields=record.extracted,
    )


def normalize_parsing_result(
    result: ParsingResult,
    default_service: str = "default",
    default_env: str = "production",
) -> NormalizationResult:
    """Normalize all records in a ParsingResult into a structured NormalizationResult."""
    normalized_records: List[NormalizedLog] = []
    valid_count = 0
    invalid_count = 0

    for r in result.records:
        norm = normalize_parsed_record(
            record=r,
            source_format=result.format_type,
            default_service=default_service,
            default_env=default_env,
        )
        normalized_records.append(norm)
        if norm.is_valid:
            valid_count += 1
        else:
            invalid_count += 1

    total = len(normalized_records)
    if invalid_count > 0:
        summary = (
            f"Normalized {total} records: {valid_count} valid, {invalid_count} invalid "
            f"or malformed."
        )
    else:
        summary = f"Successfully normalized all {total} records."

    return NormalizationResult(
        total_records=total,
        valid_records=valid_count,
        invalid_records=invalid_count,
        records=normalized_records,
        summary=summary,
    )
