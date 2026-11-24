import re
from typing import Any, Dict, List, Optional
from app.parsers.base import BaseLogParser, ParsedRecord, ParsingResult

# Pattern 1: Fully bracketed log line
# e.g.: [2026-10-09 14:32:01.123] [INFO] [auth-service] User logged in
BRACKETED_PATTERN = re.compile(
    r"^\[(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)\]\s*"
    r"\[(?P<level>[A-Za-z]+)\]\s*"
    r"(?:\[(?P<logger>[^\]]+)\]\s*)?"
    r"(?P<message>.*)$"
)

# Pattern 2: Spring Boot / Java Logback style
# e.g.: 2026-10-09 14:32:01.123  INFO 12345 --- [main] c.e.DemoApp : Server started
SPRING_LOGBACK_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
    r"(?P<level>[A-Za-z]+)\s+"
    r"(?:(?P<pid>\d+)\s+---\s+)?\s*"
    r"(?:\[(?P<thread>[^\]]+)\]\s+)?"
    r"(?:(?P<logger>[^\s:]+)\s*:\s*)?"
    r"(?P<message>.*)$"
)

# Pattern 3: Standard ISO Timestamp + Level + [Service/Logger] + Message
# e.g.: 2026-10-09 14:32:01.123 INFO [payment-svc] - Card processed
# e.g.: 2026-10-09T14:32:01Z WARN auth-service: High token latency
STANDARD_APP_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
    r"(?:\[?(?P<level>INFO|WARN|WARNING|ERROR|FATAL|CRITICAL|DEBUG|TRACE)\]?)\s+"
    r"(?:(?:\[(?P<logger_bracket>[^\]]+)\]|(?P<logger_word>[a-zA-Z0-9_.-]+))\s*[-:]?\s*)?"
    r"(?P<message>.*)$",
    re.IGNORECASE,
)

# Pattern 4: Minimal Timestamp + Level + Message
# e.g.: 2026-10-09 14:32:01 ERROR Database connection failed
MINIMAL_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>INFO|WARN|WARNING|ERROR|FATAL|CRITICAL|DEBUG|TRACE)\s+"
    r"(?P<message>.*)$",
    re.IGNORECASE,
)


class ApplicationLogParser(BaseLogParser):
    """Safe parser for enterprise application logs (.log and .txt files)."""

    def parse_lines(self, lines: List[str]) -> ParsingResult:
        # Check if stream conforms to HDFS benchmark format
        from app.parsers.hdfs_parser import HDFSLogParser
        if HDFSLogParser.is_hdfs_format(lines):
            return HDFSLogParser().parse_lines(lines)

        records: List[ParsedRecord] = []
        successful = 0
        failed = 0

        for idx, line in enumerate(lines, start=1):
            raw_line = line.rstrip("\r\n")
            if not raw_line.strip():
                continue

            extracted = self._match_app_log(raw_line)
            if extracted is not None:
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=raw_line,
                        extracted=extracted,
                        is_valid=True,
                    )
                )
                successful += 1
            else:
                # Retain unmatched lines verbatim (such as stack traces or banner lines)
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=raw_line,
                        extracted={"message": raw_line},
                        is_valid=False,
                        error="Unmatched application log pattern",
                    )
                )
                failed += 1

        error_summary = f"{failed} unmatched application log lines encountered" if failed > 0 else None
        return ParsingResult(
            format_type="application_log",
            total_records=len(records),
            successful_records=successful,
            failed_records=failed,
            records=records,
            error_summary=error_summary,
        )

    def _match_app_log(self, text: str) -> Optional[Dict[str, Any]]:
        # 1. Try fully bracketed
        m = BRACKETED_PATTERN.match(text)
        if m:
            d = m.groupdict()
            return {
                "pattern": "bracketed",
                "timestamp": d["timestamp"],
                "log_level": d["level"].upper(),
                "logger": d.get("logger"),
                "message": d["message"].strip(),
            }

        # 2. Try Spring Boot / Logback
        m = SPRING_LOGBACK_PATTERN.match(text)
        if m:
            d = m.groupdict()
            return {
                "pattern": "logback",
                "timestamp": d["timestamp"],
                "log_level": d["level"].upper(),
                "pid": d.get("pid"),
                "thread": d.get("thread"),
                "logger": d.get("logger"),
                "message": d["message"].strip(),
            }

        # 3. Try standard ISO
        m = STANDARD_APP_PATTERN.match(text)
        if m:
            d = m.groupdict()
            logger_name = d.get("logger_bracket") or d.get("logger_word")
            return {
                "pattern": "standard",
                "timestamp": d["timestamp"],
                "log_level": d["level"].upper(),
                "logger": logger_name,
                "message": d["message"].strip(),
            }

        # 4. Try minimal
        m = MINIMAL_PATTERN.match(text)
        if m:
            d = m.groupdict()
            return {
                "pattern": "minimal",
                "timestamp": d["timestamp"],
                "log_level": d["level"].upper(),
                "message": d["message"].strip(),
            }

        return None
