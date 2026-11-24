from typing import Any, Optional, Tuple

_CANONICAL_LEVELS = {"TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"}

# String alias mappings to canonical RootLens log level
_SEVERITY_MAP = {
    # TRACE
    "trace": "TRACE",
    "verbose": "TRACE",
    "finest": "TRACE",
    "finer": "TRACE",
    "fine": "TRACE",
    "all": "TRACE",
    # DEBUG
    "debug": "DEBUG",
    "dbg": "DEBUG",
    "debu": "DEBUG",
    # INFO
    "info": "INFO",
    "information": "INFO",
    "informational": "INFO",
    "notice": "INFO",
    # WARN
    "warn": "WARN",
    "warning": "WARN",
    # ERROR
    "error": "ERROR",
    "err": "ERROR",
    "eror": "ERROR",
    # FATAL / CRITICAL
    "fatal": "FATAL",
    "crit": "FATAL",
    "critical": "FATAL",
    "emerg": "FATAL",
    "emergency": "FATAL",
    "alert": "FATAL",
    "panic": "FATAL",
    "severe": "FATAL",
}

# Syslog numeric severity codes (RFC 5424 / RFC 3164)
# 0: Emergency, 1: Alert, 2: Critical, 3: Error, 4: Warning, 5: Notice, 6: Informational, 7: Debug
_SYSLOG_NUMERIC_MAP = {
    0: "FATAL",
    1: "FATAL",
    2: "FATAL",
    3: "ERROR",
    4: "WARN",
    5: "INFO",
    6: "INFO",
    7: "DEBUG",
}


def normalize_severity(raw_val: Any) -> Tuple[Optional[str], Optional[str]]:
    """Normalize a raw severity/log level value into a canonical RootLens level.

    Canonical levels: TRACE, DEBUG, INFO, WARN, ERROR, FATAL.
    Does NOT invent values if missing.

    Returns:
        Tuple of (canonical_level, error_message):
        - (CANONICAL_LEVEL, None) if recognized
        - (None, "Missing log level/severity") if raw_val is None or empty
        - ("UNKNOWN", "Unrecognized log level: ...") if unmapped
    """
    if raw_val is None:
        return None, "Missing log level/severity"

    # Numeric handling (e.g. Syslog severity codes)
    if isinstance(raw_val, int):
        canonical = _SYSLOG_NUMERIC_MAP.get(raw_val)
        if canonical:
            return canonical, None
        return "UNKNOWN", f"Unrecognized numeric severity code: {raw_val}"

    # String handling
    val_str = str(raw_val).strip()
    if not val_str:
        return None, "Missing log level/severity"

    # Numeric string (e.g. "3" or "6")
    if val_str.isdigit():
        num = int(val_str)
        canonical = _SYSLOG_NUMERIC_MAP.get(num)
        if canonical:
            return canonical, None
        return "UNKNOWN", f"Unrecognized numeric severity code: {num}"

    # Strip surrounding brackets or colons (e.g. "[ERROR]" or "WARN:")
    cleaned = val_str.strip("[](): ").lower()

    canonical = _SEVERITY_MAP.get(cleaned)
    if canonical:
        return canonical, None

    # Check direct uppercase match if already canonical
    upper = cleaned.upper()
    if upper in _CANONICAL_LEVELS:
        return upper, None

    return "UNKNOWN", f"Unrecognized log level: '{val_str}'"
