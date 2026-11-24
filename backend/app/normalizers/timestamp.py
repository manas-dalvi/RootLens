import re
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

# Regex to detect BSD Syslog timestamp: e.g. "Oct 11 22:14:15" or "Oct  9 22:14:15"
_BSD_SYSLOG_PATTERN = re.compile(
    r"^(?P<month>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})$"
)

# Common explicit strptime formats to attempt
_EXPLICIT_FORMATS = [
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S.%f",
    "%d/%b/%Y:%H:%M:%S %z",  # Common Apache/Nginx format
]


def normalize_timestamp(raw_val: Any) -> Tuple[Optional[datetime], Optional[str]]:
    """Normalize raw timestamp into a timezone-aware UTC datetime.

    Supports:
    - ISO-8601 / RFC 3339 strings (with Z or timezone offset)
    - Naive ISO/space-separated strings (assumed UTC)
    - BSD Syslog format (e.g. "Oct 11 22:14:15") with current year inference
    - Numeric Unix timestamps (seconds, milliseconds, microseconds)
    - Safe error reporting on unparseable or missing timestamps

    Returns:
        Tuple of (utc_datetime, error_message):
        - (datetime_utc, None) on success
        - (None, "Missing timestamp field") if raw_val is None or empty
        - (None, "Unparseable timestamp: ...") if unparseable
    """
    if raw_val is None:
        return None, "Missing timestamp field"

    # 1. Already a datetime instance
    if isinstance(raw_val, datetime):
        if raw_val.tzinfo is None:
            return raw_val.replace(tzinfo=timezone.utc), None
        return raw_val.astimezone(timezone.utc), None

    # 2. Numeric epoch timestamp (int or float)
    if isinstance(raw_val, (int, float)):
        return _parse_epoch_number(float(raw_val))

    val_str = str(raw_val).strip()
    if not val_str:
        return None, "Missing timestamp field"

    # 3. Numeric string (epoch)
    try:
        numeric_val = float(val_str)
        # Check if reasonably an epoch timestamp (between 2000-01-01 and 2100-01-01)
        if 946684800 <= numeric_val <= 4102444800000:
            return _parse_epoch_number(numeric_val)
    except ValueError:
        pass

    # 4. Standard ISO-8601 parsing (handles 'Z' and offsets in Python 3.11+)
    # Clean bracketed timestamps like "[2026-10-09 14:32:01]"
    cleaned = val_str.strip("[]")

    # Replace comma with dot for fractional seconds (e.g. 2026-10-09 14:32:01,123)
    if "," in cleaned and ("-" in cleaned or "/" in cleaned):
        cleaned = cleaned.replace(",", ".")

    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc), None
        return dt.astimezone(timezone.utc), None
    except (ValueError, TypeError):
        pass

    # 5. Check BSD Syslog format (e.g. "Oct 11 22:14:15")
    bsd_match = _BSD_SYSLOG_PATTERN.match(cleaned)
    if bsd_match:
        current_year = datetime.now(timezone.utc).year
        candidate = f"{current_year} {cleaned}"
        try:
            # Reformat multiple spaces between month and day
            candidate_cleaned = " ".join(candidate.split())
            dt = datetime.strptime(candidate_cleaned, "%Y %b %d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc), None
        except ValueError:
            pass

    # 6. Fallback explicit strptime patterns
    for fmt in _EXPLICIT_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc), None
            return dt.astimezone(timezone.utc), None
        except (ValueError, TypeError):
            continue

    return None, f"Unparseable timestamp: '{val_str}'"


def _parse_epoch_number(ts: float) -> Tuple[Optional[datetime], Optional[str]]:
    """Convert numeric epoch into timezone-aware UTC datetime."""
    try:
        # Microseconds (> 1e14)
        if ts > 1e14:
            ts = ts / 1e6
        # Milliseconds (> 1e11)
        elif ts > 1e11:
            ts = ts / 1e3
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt, None
    except (ValueError, OverflowError, OSError) as exc:
        return None, f"Numeric epoch timestamp out of bounds ({ts}): {exc}"
