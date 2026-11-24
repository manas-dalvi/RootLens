import csv
import io
from typing import List, Optional
from app.parsers.base import BaseLogParser, ParsedRecord, ParsingResult


class CSVLogParser(BaseLogParser):
    """Safe CSV log parser handling headers, quoted fields, and malformed records."""

    def parse_lines(self, lines: List[str]) -> ParsingResult:
        records: List[ParsedRecord] = []
        if not lines:
            return ParsingResult(
                format_type="csv",
                total_records=0,
                successful_records=0,
                failed_records=0,
                records=[],
            )

        content = "".join(lines)
        reader = csv.reader(io.StringIO(content))

        headers: Optional[List[str]] = None
        line_num = 0
        successful = 0
        failed = 0

        for row in reader:
            line_num += 1
            raw_line = lines[line_num - 1] if line_num <= len(lines) else ",".join(row)

            # Skip empty lines
            if not row or (len(row) == 1 and not row[0].strip()):
                continue

            # First non-empty row: check if it acts as a header
            if headers is None:
                # Treat as header if row contains non-empty column identifiers
                headers = [col.strip() for col in row]
                # If header row, we don't treat it as a log event record
                continue

            # Check column count alignment
            if len(row) == len(headers):
                extracted = {headers[i]: row[i] for i in range(len(headers))}
                records.append(
                    ParsedRecord(
                        line_number=line_num,
                        raw=raw_line.rstrip("\r\n"),
                        extracted=extracted,
                        is_valid=True,
                    )
                )
                successful += 1
            else:
                # Malformed row with mismatched column count
                extracted = {f"col_{i}": val for i, val in enumerate(row)}
                err_msg = f"Column count mismatch: expected {len(headers)}, got {len(row)}"
                records.append(
                    ParsedRecord(
                        line_number=line_num,
                        raw=raw_line.rstrip("\r\n"),
                        extracted=extracted,
                        is_valid=False,
                        error=err_msg,
                    )
                )
                failed += 1

        error_summary = f"{failed} malformed CSV records encountered" if failed > 0 else None

        return ParsingResult(
            format_type="csv",
            total_records=len(records),
            successful_records=successful,
            failed_records=failed,
            records=records,
            error_summary=error_summary,
        )
