import json
from typing import Any, Dict, List
from app.parsers.base import BaseLogParser, ParsedRecord, ParsingResult


class JSONLogParser(BaseLogParser):
    """Safe parser for JSON Lines (NDJSON), JSON array of objects, and single JSON records."""

    def parse_lines(self, lines: List[str]) -> ParsingResult:
        records: List[ParsedRecord] = []
        if not lines:
            return ParsingResult(
                format_type="json",
                total_records=0,
                successful_records=0,
                failed_records=0,
                records=[],
            )

        content = "".join(lines).strip()
        if not content:
            return ParsingResult(
                format_type="json",
                total_records=0,
                successful_records=0,
                failed_records=0,
                records=[],
            )

        # Check for top-level JSON Array: [ { ... }, { ... } ]
        if content.startswith("[") and content.endswith("]"):
            return self._parse_json_array(content)

        # Otherwise parse line-delimited JSON (NDJSON) or single JSON object
        return self._parse_json_lines(lines)

    def _parse_json_array(self, content: str) -> ParsingResult:
        records: List[ParsedRecord] = []
        successful = 0
        failed = 0

        try:
            parsed_data = json.loads(content)
        except json.JSONDecodeError as exc:
            return ParsingResult(
                format_type="json",
                total_records=1,
                successful_records=0,
                failed_records=1,
                records=[
                    ParsedRecord(
                        line_number=1,
                        raw=content[:500],
                        extracted={},
                        is_valid=False,
                        error=f"Malformed JSON array: {exc.msg} at line {exc.lineno}",
                    )
                ],
                error_summary="Malformed JSON array document",
            )

        if not isinstance(parsed_data, list):
            return ParsingResult(
                format_type="json",
                total_records=1,
                successful_records=0,
                failed_records=1,
                records=[
                    ParsedRecord(
                        line_number=1,
                        raw=content[:500],
                        extracted={},
                        is_valid=False,
                        error="Unsupported JSON root type: expected list of objects",
                    )
                ],
                error_summary="Unsupported JSON root structure",
            )

        for idx, item in enumerate(parsed_data, start=1):
            if isinstance(item, dict):
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=json.dumps(item),
                        extracted=item,
                        is_valid=True,
                    )
                )
                successful += 1
            else:
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=str(item),
                        extracted={},
                        is_valid=False,
                        error=f"JSON array element must be an object, got {type(item).__name__}",
                    )
                )
                failed += 1

        error_summary = f"{failed} invalid items in JSON array" if failed > 0 else None
        return ParsingResult(
            format_type="json",
            total_records=len(records),
            successful_records=successful,
            failed_records=failed,
            records=records,
            error_summary=error_summary,
        )

    def _parse_json_lines(self, lines: List[str]) -> ParsingResult:
        records: List[ParsedRecord] = []
        successful = 0
        failed = 0

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                item = json.loads(stripped)
                if isinstance(item, dict):
                    records.append(
                        ParsedRecord(
                            line_number=idx,
                            raw=stripped,
                            extracted=item,
                            is_valid=True,
                        )
                    )
                    successful += 1
                else:
                    records.append(
                        ParsedRecord(
                            line_number=idx,
                            raw=stripped,
                            extracted={},
                            is_valid=False,
                            error=f"JSON record must be an object, got {type(item).__name__}",
                        )
                    )
                    failed += 1
            except json.JSONDecodeError as exc:
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=stripped,
                        extracted={},
                        is_valid=False,
                        error=f"Invalid JSON syntax: {exc.msg}",
                    )
                )
                failed += 1

        error_summary = f"{failed} malformed JSON lines encountered" if failed > 0 else None
        return ParsingResult(
            format_type="json",
            total_records=len(records),
            successful_records=successful,
            failed_records=failed,
            records=records,
            error_summary=error_summary,
        )
