import re
from typing import Any, Dict, List, Optional
from app.parsers.base import BaseLogParser, ParsedRecord, ParsingResult

# RFC 5424 (IETF Syslog) regex
# Example: <165>1 2026-10-09T22:14:15.003Z myhost evtsys 1234 ID47 - An application event
RFC_5424_PATTERN = re.compile(
    r"^<(?P<pri>\d{1,3})>(?P<version>\d+)\s+"
    r"(?P<timestamp>[^\s]+)\s+"
    r"(?P<hostname>[^\s]+)\s+"
    r"(?P<app_name>[^\s]+)\s+"
    r"(?P<procid>[^\s]+)\s+"
    r"(?P<msgid>[^\s]+)\s+"
    r"(?P<structured_data>\[.*?\]|-)\s*"
    r"(?P<message>.*)$"
)

# RFC 3164 (BSD Syslog with PRI header) regex
# Example: <34>Oct 09 22:14:15 myhost su[1234]: 'su root' failed
RFC_3164_PATTERN = re.compile(
    r"^<(?P<pri>\d{1,3})>"
    r"(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<hostname>[^\s]+)\s+"
    r"(?P<app_name>[^:\[\s]+)(?:\[(?P<pid>\d+)\])?:\s*"
    r"(?P<message>.*)$"
)

# BSD Syslog without PRI header
# Example: Oct  9 22:14:15 myhost sshd[5678]: Accepted publickey
BSD_STANDARD_PATTERN = re.compile(
    r"^(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<hostname>[^\s]+)\s+"
    r"(?P<app_name>[^:\[\s]+)(?:\[(?P<pid>\d+)\])?:\s*"
    r"(?P<message>.*)$"
)


class SyslogParser(BaseLogParser):
    """Syslog parser supporting RFC 5424, RFC 3164, and standard BSD syslog formats."""

    def parse_lines(self, lines: List[str]) -> ParsingResult:
        records: List[ParsedRecord] = []
        successful = 0
        failed = 0

        for idx, line in enumerate(lines, start=1):
            raw_line = line.rstrip("\r\n")
            if not raw_line.strip():
                continue

            extracted = self._match_syslog(raw_line)
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
                # Retain line verbatim without inventing missing fields
                records.append(
                    ParsedRecord(
                        line_number=idx,
                        raw=raw_line,
                        extracted={"raw": raw_line},
                        is_valid=False,
                        error="Unmatched syslog format (does not match RFC 5424 or RFC 3164)",
                    )
                )
                failed += 1

        error_summary = f"{failed} unmatched syslog lines encountered" if failed > 0 else None
        return ParsingResult(
            format_type="syslog",
            total_records=len(records),
            successful_records=successful,
            failed_records=failed,
            records=records,
            error_summary=error_summary,
        )

    def _match_syslog(self, text: str) -> Optional[Dict[str, Any]]:
        # 1. Try RFC 5424
        m = RFC_5424_PATTERN.match(text)
        if m:
            d = m.groupdict()
            pri = int(d["pri"])
            return {
                "format": "rfc5424",
                "priority": pri,
                "facility": pri >> 3,
                "severity": pri & 7,
                "version": int(d["version"]),
                "timestamp": d["timestamp"],
                "hostname": d["hostname"] if d["hostname"] != "-" else None,
                "app_name": d["app_name"] if d["app_name"] != "-" else None,
                "procid": d["procid"] if d["procid"] != "-" else None,
                "msgid": d["msgid"] if d["msgid"] != "-" else None,
                "structured_data": d["structured_data"] if d["structured_data"] != "-" else None,
                "message": d["message"].strip(),
            }

        # 2. Try RFC 3164 (with PRI)
        m = RFC_3164_PATTERN.match(text)
        if m:
            d = m.groupdict()
            pri = int(d["pri"])
            return {
                "format": "rfc3164",
                "priority": pri,
                "facility": pri >> 3,
                "severity": pri & 7,
                "timestamp": d["timestamp"],
                "hostname": d["hostname"],
                "app_name": d["app_name"],
                "pid": int(d["pid"]) if d.get("pid") else None,
                "message": d["message"].strip(),
            }

        # 3. Try BSD standard (without PRI)
        m = BSD_STANDARD_PATTERN.match(text)
        if m:
            d = m.groupdict()
            return {
                "format": "bsd",
                "timestamp": d["timestamp"],
                "hostname": d["hostname"],
                "app_name": d["app_name"],
                "pid": int(d["pid"]) if d.get("pid") else None,
                "message": d["message"].strip(),
            }

        return None
