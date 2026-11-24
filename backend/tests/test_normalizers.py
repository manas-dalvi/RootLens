import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.log_source import LogSource
from app.models.upload_job import UploadJob
from app.normalizers.aliases import resolve_aliases
from app.normalizers.base import (
    NormalizedLog,
    normalize_parsed_record,
    normalize_parsing_result,
)
from app.normalizers.severity import normalize_severity
from app.normalizers.timestamp import normalize_timestamp
from app.parsers.base import ParsedRecord, ParsingResult
from app.services.normalization_service import normalize_upload_job

client = TestClient(app)


# ============================================================================
# 1. Severity Normalization Tests
# ============================================================================


def test_severity_canonical_levels():
    """Verify standard canonical levels map directly without alteration."""
    for lvl in ["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"]:
        canonical, err = normalize_severity(lvl)
        assert canonical == lvl
        assert err is None


def test_severity_case_insensitivity_and_aliases():
    """Verify case-insensitivity and standard synonyms map to canonical levels."""
    assert normalize_severity("info")[0] == "INFO"
    assert normalize_severity("Information")[0] == "INFO"
    assert normalize_severity("notice")[0] == "INFO"
    assert normalize_severity("warning")[0] == "WARN"
    assert normalize_severity("WARN:")[0] == "WARN"
    assert normalize_severity("[ERROR]")[0] == "ERROR"
    assert normalize_severity("err")[0] == "ERROR"
    assert normalize_severity("critical")[0] == "FATAL"
    assert normalize_severity("emerg")[0] == "FATAL"
    assert normalize_severity("alert")[0] == "FATAL"
    assert normalize_severity("verbose")[0] == "TRACE"
    assert normalize_severity("debug")[0] == "DEBUG"


def test_severity_syslog_numeric_codes():
    """Verify numeric severity codes (0-7) map accurately to canonical levels."""
    # 0, 1, 2 -> FATAL
    assert normalize_severity(0)[0] == "FATAL"
    assert normalize_severity(1)[0] == "FATAL"
    assert normalize_severity(2)[0] == "FATAL"
    # 3 -> ERROR
    assert normalize_severity(3)[0] == "ERROR"
    # 4 -> WARN
    assert normalize_severity(4)[0] == "WARN"
    # 5, 6 -> INFO
    assert normalize_severity(5)[0] == "INFO"
    assert normalize_severity(6)[0] == "INFO"
    # 7 -> DEBUG
    assert normalize_severity(7)[0] == "DEBUG"
    # Numeric string e.g. "3"
    assert normalize_severity("3")[0] == "ERROR"


def test_severity_missing_and_unknown():
    """Verify missing severity does NOT invent values and unknown levels are flagged."""
    # Missing / None
    canonical, err = normalize_severity(None)
    assert canonical is None
    assert "Missing" in err

    canonical, err = normalize_severity("")
    assert canonical is None
    assert "Missing" in err

    # Unrecognized / Invalid
    canonical, err = normalize_severity("UNKNOWN_XYZ")
    assert canonical == "UNKNOWN"
    assert "Unrecognized log level" in err

    canonical, err = normalize_severity(99)
    assert canonical == "UNKNOWN"
    assert "Unrecognized numeric" in err


# ============================================================================
# 2. Timestamp Normalization Tests
# ============================================================================


def test_timestamp_iso8601_utc_and_offsets():
    """Verify ISO-8601 timestamps with UTC Z and timezone offsets convert to UTC."""
    # UTC with Z
    dt, err = normalize_timestamp("2026-10-09T22:14:15.003Z")
    assert err is None
    assert dt == datetime(2026, 10, 9, 22, 14, 15, 3000, tzinfo=timezone.utc)

    # Offset +05:30 -> UTC is 16:44:15
    dt, err = normalize_timestamp("2026-10-09T22:14:15+05:30")
    assert err is None
    assert dt == datetime(2026, 10, 9, 16, 44, 15, tzinfo=timezone.utc)

    # Offset -04:00 -> UTC is 2026-10-10 02:14:15
    dt, err = normalize_timestamp("2026-10-09T22:14:15-04:00")
    assert err is None
    assert dt == datetime(2026, 10, 10, 2, 14, 15, tzinfo=timezone.utc)


def test_timestamp_naive_and_space_separated():
    """Verify naive dates and space-separated formats attach UTC timezone."""
    dt, err = normalize_timestamp("2026-10-09 14:32:01.123")
    assert err is None
    assert dt == datetime(2026, 10, 9, 14, 32, 1, 123000, tzinfo=timezone.utc)

    # Comma fractional seconds (Logback / Python logging)
    dt, err = normalize_timestamp("2026-10-09 14:32:01,500")
    assert err is None
    assert dt == datetime(2026, 10, 9, 14, 32, 1, 500000, tzinfo=timezone.utc)

    # Simple YYYY-MM-DD HH:MM:SS
    dt, err = normalize_timestamp("2026-10-09 14:32:01")
    assert err is None
    assert dt == datetime(2026, 10, 9, 14, 32, 1, tzinfo=timezone.utc)


def test_timestamp_bsd_syslog():
    """Verify BSD Syslog timestamp (RFC 3164) parses with current year in UTC."""
    current_year = datetime.now(timezone.utc).year
    dt, err = normalize_timestamp("Oct 11 22:14:15")
    assert err is None
    assert dt == datetime(current_year, 10, 11, 22, 14, 15, tzinfo=timezone.utc)

    # Single digit day with two spaces
    dt, err = normalize_timestamp("Oct  9 22:14:15")
    assert err is None
    assert dt == datetime(current_year, 10, 9, 22, 14, 15, tzinfo=timezone.utc)


def test_timestamp_epoch_numbers():
    """Verify Unix epoch timestamps in seconds, milliseconds, and microseconds."""
    # Epoch seconds: 1760048055 -> 2025-10-09 22:14:15 UTC
    expected = datetime(2025, 10, 9, 22, 14, 15, tzinfo=timezone.utc)
    ts_sec = 1760048055

    dt, err = normalize_timestamp(ts_sec)
    assert err is None
    assert dt == expected

    # Epoch milliseconds: 1760048055000
    dt_ms, err = normalize_timestamp(ts_sec * 1000)
    assert err is None
    assert dt_ms == expected


def test_timestamp_missing_and_unparseable():
    """Verify missing or invalid timestamps return None with descriptive errors."""
    dt, err = normalize_timestamp(None)
    assert dt is None
    assert "Missing" in err

    dt, err = normalize_timestamp("")
    assert dt is None
    assert "Missing" in err

    dt, err = normalize_timestamp("not-a-valid-timestamp")
    assert dt is None
    assert "Unparseable timestamp" in err


# ============================================================================
# 3. Field Aliases Resolution Tests
# ============================================================================


def test_resolve_aliases_comprehensive():
    """Verify aliases resolve for timestamp, level, service, host, message, trace_id, span_id."""
    data = {
        "@timestamp": "2026-10-09T12:00:00Z",
        "severity": "ERROR",
        "app_name": "payment-service",
        "hostname": "worker-node-42",
        "msg": "Transaction declined",
        "traceId": "trace-abc-123",
        "spanId": "span-456",
        "env": "staging",
        "custom_metric": 42.5,
        "user_id": "usr_99",
    }

    resolved = resolve_aliases(data)

    assert resolved.raw_timestamp == "2026-10-09T12:00:00Z"
    assert resolved.raw_level == "ERROR"
    assert resolved.raw_service == "payment-service"
    assert resolved.raw_host == "worker-node-42"
    assert resolved.raw_message == "Transaction declined"
    assert resolved.raw_trace_id == "trace-abc-123"
    assert resolved.raw_span_id == "span-456"
    assert resolved.raw_environment == "staging"

    # Unmapped fields isolated in attributes
    assert resolved.attributes == {"custom_metric": 42.5, "user_id": "usr_99"}


def test_resolve_aliases_case_insensitivity():
    """Verify aliases match case-insensitively."""
    data = {
        "TIMESTAMP": "2026-10-09T10:00:00Z",
        "LEVEL": "INFO",
        "SERVICE": "auth-gateway",
        "MESSAGE": "Token verified",
    }
    resolved = resolve_aliases(data)
    assert resolved.raw_timestamp == "2026-10-09T10:00:00Z"
    assert resolved.raw_level == "INFO"
    assert resolved.raw_service == "auth-gateway"
    assert resolved.raw_message == "Token verified"


# ============================================================================
# 4. Multi-Format Normalization Tests
# ============================================================================


def test_normalize_csv_record():
    """Verify CSV extracted record normalizes accurately into canonical schema."""
    parsed = ParsedRecord(
        line_number=2,
        raw="2026-10-09T10:00:00Z,INFO,billing,Invoice created",
        extracted={
            "timestamp": "2026-10-09T10:00:00Z",
            "level": "INFO",
            "service": "billing",
            "message": "Invoice created",
            "extra_code": "INV-001",
        },
        is_valid=True,
    )

    norm = normalize_parsed_record(parsed, source_format="csv", default_env="production")

    assert norm.is_valid is True
    assert norm.line_number == 2
    assert norm.service_name == "billing"
    assert norm.environment == "production"
    assert norm.log_level == "INFO"
    assert norm.message == "Invoice created"
    assert norm.timestamp == datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc)
    assert norm.attributes == {"extra_code": "INV-001"}
    assert norm.source_format == "csv"
    assert norm.raw_log == parsed.raw


def test_normalize_json_record_with_trace_context():
    """Verify JSON record with trace correlation and structured attributes."""
    parsed = ParsedRecord(
        line_number=1,
        raw='{"time": "2026-10-09T14:00:00Z", "lvl": "warn", "app": "order-api", "msg": "Retry", "trace_id": "tr-1", "span_id": "sp-2", "retry_count": 3}',
        extracted={
            "time": "2026-10-09T14:00:00Z",
            "lvl": "warn",
            "app": "order-api",
            "msg": "Retry",
            "trace_id": "tr-1",
            "span_id": "sp-2",
            "retry_count": 3,
        },
        is_valid=True,
    )

    norm = normalize_parsed_record(parsed, source_format="json")

    assert norm.is_valid is True
    assert norm.log_level == "WARN"
    assert norm.service_name == "order-api"
    assert norm.trace_id == "tr-1"
    assert norm.span_id == "sp-2"
    assert norm.attributes == {"retry_count": 3}


def test_normalize_syslog_rfc5424_record():
    """Verify RFC 5424 Syslog record maps facility/severity, host, and app_name."""
    parsed = ParsedRecord(
        line_number=5,
        raw='<34>1 2026-10-09T22:14:15.003Z host1 su 1234 ID47 - su root failed',
        extracted={
            "format": "rfc5424",
            "priority": 34,
            "facility": 4,
            "severity": 2,  # Syslog severity 2 -> FATAL
            "version": 1,
            "timestamp": "2026-10-09T22:14:15.003Z",
            "hostname": "host1",
            "app_name": "su",
            "procid": "1234",
            "msgid": "ID47",
            "message": "su root failed",
        },
        is_valid=True,
    )

    norm = normalize_parsed_record(parsed, source_format="syslog")

    assert norm.is_valid is True
    assert norm.service_name == "su"
    assert norm.host == "host1"
    assert norm.log_level == "FATAL"
    assert norm.message == "su root failed"
    assert norm.attributes["procid"] == "1234"
    assert norm.attributes["facility"] == 4


def test_normalize_application_log_record():
    """Verify Application log bracketed format normalizes accurately."""
    parsed = ParsedRecord(
        line_number=10,
        raw="[2026-10-09 14:30:00] [INFO] [auth-service] User logged in",
        extracted={
            "pattern": "bracketed",
            "timestamp": "2026-10-09 14:30:00",
            "log_level": "INFO",
            "logger": "auth-service",
            "message": "User logged in",
        },
        is_valid=True,
    )

    norm = normalize_parsed_record(parsed, source_format="application_log")

    assert norm.is_valid is True
    assert norm.service_name == "auth-service"
    assert norm.log_level == "INFO"
    assert norm.message == "User logged in"
    assert norm.timestamp == datetime(2026, 10, 9, 14, 30, 0, tzinfo=timezone.utc)


# ============================================================================
# 5. Validation & Edge Cases Tests
# ============================================================================


def test_normalize_missing_timestamp_flags_invalid():
    """Verify missing timestamp sets is_valid=False and records validation error."""
    parsed = ParsedRecord(
        line_number=1,
        raw="Just a message without a timestamp",
        extracted={"message": "Just a message without a timestamp", "level": "INFO"},
        is_valid=True,
    )

    norm = normalize_parsed_record(parsed, source_format="application_log")

    assert norm.is_valid is False
    assert norm.timestamp is None
    assert any("Missing timestamp" in e for e in norm.validation_errors)
    assert norm.raw_log == "Just a message without a timestamp"


def test_normalize_unparseable_timestamp_preserves_raw():
    """Verify unparseable timestamp sets is_valid=False and preserves raw timestamp."""
    parsed = ParsedRecord(
        line_number=1,
        raw="2026-99-99 ERROR Failed",
        extracted={"timestamp": "2026-99-99-invalid", "level": "ERROR", "message": "Failed"},
        is_valid=True,
    )

    norm = normalize_parsed_record(parsed, source_format="application_log")

    assert norm.is_valid is False
    assert norm.timestamp is None
    assert norm.raw_timestamp == "2026-99-99-invalid"
    assert any("Unparseable timestamp" in e for e in norm.validation_errors)


def test_normalize_missing_severity_does_not_invent_level():
    """Verify missing severity leaves log_level as None without fabricating a fake level."""
    parsed = ParsedRecord(
        line_number=1,
        raw="2026-10-09T10:00:00Z Database started",
        extracted={"timestamp": "2026-10-09T10:00:00Z", "message": "Database started"},
        is_valid=True,
    )

    norm = normalize_parsed_record(parsed, source_format="application_log")

    assert norm.log_level is None
    assert any("Missing log level/severity" in e for e in norm.validation_errors)
    # Valid timestamp and message means the event can still be indexed even without severity
    assert norm.is_valid is True


def test_normalize_unrecognized_severity_flags_invalid():
    """Verify unrecognized severity string sets level to UNKNOWN and marks invalid."""
    parsed = ParsedRecord(
        line_number=1,
        raw="2026-10-09T10:00:00Z BANANA Database started",
        extracted={
            "timestamp": "2026-10-09T10:00:00Z",
            "level": "BANANA",
            "message": "Database started",
        },
        is_valid=True,
    )

    norm = normalize_parsed_record(parsed, source_format="application_log")

    assert norm.log_level == "UNKNOWN"
    assert norm.is_valid is False
    assert any("Unrecognized log level" in e for e in norm.validation_errors)


def test_normalize_parser_error_preserves_raw_and_flags_invalid():
    """Verify a record that failed in parsing preserves original line and error."""
    parsed = ParsedRecord(
        line_number=4,
        raw="mismatched,csv,row",
        extracted={"col_0": "mismatched", "col_1": "csv", "col_2": "row"},
        is_valid=False,
        error="Column count mismatch: expected 4, got 3",
    )

    norm = normalize_parsed_record(parsed, source_format="csv")

    assert norm.is_valid is False
    assert norm.line_number == 4
    assert norm.raw_log == "mismatched,csv,row"
    assert any("Parser error" in e for e in norm.validation_errors)


def test_normalize_parsing_result_aggregation():
    """Verify normalize_parsing_result tallies valid and invalid records correctly."""
    r1 = ParsedRecord(
        line_number=1,
        raw='{"ts": "2026-10-09T10:00:00Z", "level": "INFO", "msg": "Ready"}',
        extracted={"ts": "2026-10-09T10:00:00Z", "level": "INFO", "msg": "Ready"},
        is_valid=True,
    )
    r2 = ParsedRecord(
        line_number=2,
        raw='bad json row without timestamp',
        extracted={"message": "bad"},
        is_valid=False,
        error="Invalid JSON syntax",
    )

    parsing_result = ParsingResult(
        format_type="json",
        total_records=2,
        successful_records=1,
        failed_records=1,
        records=[r1, r2],
    )

    norm_res = normalize_parsing_result(parsing_result)

    assert norm_res.total_records == 2
    assert norm_res.valid_records == 1
    assert norm_res.invalid_records == 1
    assert "1 invalid" in norm_res.summary


# ============================================================================
# 6. Service & Endpoint Integration Tests
# ============================================================================


def make_mock_session():
    """Create an AsyncMock session for service tests."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    return mock_session


@pytest.mark.asyncio
async def test_normalize_upload_job_service_success(tmp_path, monkeypatch):
    """Verify normalize_upload_job service updates job lifecycle to COMPLETED."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.json"
    raw_file.write_text(
        '{"timestamp": "2026-10-09T10:00:00Z", "level": "INFO", "message": "All good"}\n',
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="test.json",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
        total_lines=0,
        parsed_lines=0,
        error_lines=0,
    )
    source = LogSource(
        id=source_id,
        name="test-source",
        service_name="payment-svc",
        environment="production",
        format_type="json",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    response = await normalize_upload_job(job_id=job_id, session=mock_session)

    assert response.job_id == job_id
    assert response.status == "COMPLETED"
    assert response.total_records == 1
    assert response.valid_records == 1
    assert response.invalid_records == 0
    assert len(response.records) == 1
    assert response.records[0].log_level == "INFO"
    assert response.records[0].service_name == "payment-svc"
    assert response.records[0].is_valid is True
    assert job.status == "COMPLETED"


def test_api_normalize_endpoint_success(tmp_path, monkeypatch):
    """Verify POST /api/v1/logs/jobs/{job_id}/normalize returns 200 with normalized logs."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.csv"
    raw_file.write_text(
        "timestamp,level,message\n"
        "2026-10-09T12:00:00Z,WARN,High latency detected\n",
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="latency.csv",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
        total_lines=0,
        parsed_lines=0,
        error_lines=0,
    )
    source = LogSource(
        id=source_id,
        name="csv-source",
        service_name="api-gateway",
        environment="staging",
        format_type="csv",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session

    response = client.post(f"/api/v1/logs/jobs/{job_id}/normalize")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == str(job_id)
    assert data["status"] == "COMPLETED"
    assert data["format_type"] == "csv"
    assert data["total_records"] == 1
    assert data["valid_records"] == 1
    assert len(data["records"]) == 1
    rec = data["records"][0]
    assert rec["log_level"] == "WARN"
    assert rec["service_name"] == "api-gateway"
    assert rec["environment"] == "staging"
    assert rec["message"] == "High latency detected"
    assert rec["is_valid"] is True


def test_api_normalize_endpoint_job_not_found():
    """Verify POST /api/v1/logs/jobs/{job_id}/normalize returns 404 for missing job."""
    missing_id = uuid.uuid4()
    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session
    response = client.post(f"/api/v1/logs/jobs/{missing_id}/normalize")
    app.dependency_overrides.clear()

    assert response.status_code == 404
    assert f"Upload job '{missing_id}' not found." in response.json()["detail"]
