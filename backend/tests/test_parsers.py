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
from app.parsers.app_log_parser import ApplicationLogParser
from app.parsers.base import read_file_lines_safe
from app.parsers.csv_parser import CSVLogParser
from app.parsers.factory import get_parser_for_format
from app.parsers.json_parser import JSONLogParser
from app.parsers.syslog_parser import SyslogParser
from app.services.parsing_service import parse_upload_job

client = TestClient(app)


# ============================================================================
# 1. CSV Parser Tests
# ============================================================================


def test_csv_parser_valid_file(tmp_path):
    """Verify parsing of valid CSV log with headers and rows."""
    file_path = tmp_path / "valid.csv"
    file_path.write_text(
        "timestamp,level,service,message\n"
        "2026-10-09T10:00:00Z,INFO,auth,User logged in\n"
        "2026-10-09T10:01:00Z,ERROR,billing,Payment timed out\n",
        encoding="utf-8",
    )

    parser = CSVLogParser()
    result = parser.parse_file(file_path)

    assert result.format_type == "csv"
    assert result.total_records == 2
    assert result.successful_records == 2
    assert result.failed_records == 0
    assert result.records[0].extracted["level"] == "INFO"
    assert result.records[0].extracted["service"] == "auth"
    assert result.records[0].line_number == 2
    assert result.records[1].extracted["level"] == "ERROR"
    assert result.records[1].is_valid is True


def test_csv_parser_quoted_fields(tmp_path):
    """Verify quoted fields containing commas are preserved."""
    file_path = tmp_path / "quoted.csv"
    file_path.write_text(
        'timestamp,level,message\n'
        '2026-10-09T10:00:00Z,WARN,"Warning: High memory, node 3"\n',
        encoding="utf-8",
    )

    parser = CSVLogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 1
    assert result.successful_records == 1
    assert result.records[0].extracted["message"] == "Warning: High memory, node 3"


def test_csv_parser_malformed_row(tmp_path):
    """Verify row with column count mismatch is marked invalid but preserved."""
    file_path = tmp_path / "malformed.csv"
    file_path.write_text(
        "col1,col2,col3\n"
        "val1,val2,val3\n"
        "val1,val2\n"  # Only 2 columns instead of 3
        "valA,valB,valC\n",
        encoding="utf-8",
    )

    parser = CSVLogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 3
    assert result.successful_records == 2
    assert result.failed_records == 1
    bad_record = result.records[1]
    assert bad_record.is_valid is False
    assert "expected 3" in bad_record.error.lower()
    assert bad_record.line_number == 3
    assert "val1,val2" in bad_record.raw


def test_csv_parser_empty_and_header_only(tmp_path):
    """Verify empty CSV and header-only CSV return 0 records cleanly."""
    empty_path = tmp_path / "empty.csv"
    empty_path.write_text("", encoding="utf-8")

    parser = CSVLogParser()
    res1 = parser.parse_file(empty_path)
    assert res1.total_records == 0
    assert res1.successful_records == 0

    header_only = tmp_path / "header_only.csv"
    header_only.write_text("timestamp,level,message\n", encoding="utf-8")
    res2 = parser.parse_file(header_only)
    assert res2.total_records == 0
    assert res2.successful_records == 0


# ============================================================================
# 2. JSON Parser Tests
# ============================================================================


def test_json_parser_ndjson(tmp_path):
    """Verify NDJSON format parsing line by line."""
    file_path = tmp_path / "events.json"
    file_path.write_text(
        '{"timestamp": "2026-10-09T12:00:00Z", "level": "INFO", "msg": "Booted"}\n'
        '{"timestamp": "2026-10-09T12:01:00Z", "level": "ERROR", "msg": "Crash"}\n',
        encoding="utf-8",
    )

    parser = JSONLogParser()
    result = parser.parse_file(file_path)

    assert result.format_type == "json"
    assert result.total_records == 2
    assert result.successful_records == 2
    assert result.failed_records == 0
    assert result.records[0].extracted["level"] == "INFO"
    assert result.records[1].extracted["level"] == "ERROR"


def test_json_parser_array_of_objects(tmp_path):
    """Verify JSON array of objects format parsing."""
    file_path = tmp_path / "array.json"
    data = [
        {"timestamp": "2026-10-09T10:00:00Z", "level": "INFO", "action": "login"},
        {"timestamp": "2026-10-09T10:05:00Z", "level": "WARN", "action": "retry"},
    ]
    file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    parser = JSONLogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 2
    assert result.successful_records == 2
    assert result.records[0].extracted["action"] == "login"
    assert result.records[1].extracted["action"] == "retry"


def test_json_parser_malformed_line_in_ndjson(tmp_path):
    """Verify invalid JSON syntax in NDJSON is captured as failed record."""
    file_path = tmp_path / "broken_ndjson.json"
    file_path.write_text(
        '{"timestamp": "2026-10-09T10:00:00Z", "level": "INFO"}\n'
        '{malformed json line}\n'
        '{"timestamp": "2026-10-09T10:02:00Z", "level": "DEBUG"}\n',
        encoding="utf-8",
    )

    parser = JSONLogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 3
    assert result.successful_records == 2
    assert result.failed_records == 1
    bad_rec = result.records[1]
    assert bad_rec.is_valid is False
    assert "Invalid JSON" in bad_rec.error
    assert bad_rec.line_number == 2


def test_json_parser_non_object_records(tmp_path):
    """Verify non-dictionary items in JSON array or lines are captured."""
    file_path = tmp_path / "non_dict.json"
    file_path.write_text(
        '{"level": "INFO"}\n'
        '"just a plain string"\n'
        '12345\n',
        encoding="utf-8",
    )

    parser = JSONLogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 3
    assert result.successful_records == 1
    assert result.failed_records == 2


def test_json_parser_empty_and_unsupported_root(tmp_path):
    """Verify empty file and primitive top-level JSON are handled gracefully."""
    empty_file = tmp_path / "empty.json"
    empty_file.write_text("", encoding="utf-8")
    parser = JSONLogParser()
    assert parser.parse_file(empty_file).total_records == 0

    bad_root = tmp_path / "number_root.json"
    bad_root.write_text("42", encoding="utf-8")
    res = parser.parse_file(bad_root)
    assert res.total_records == 1
    assert res.failed_records == 1


# ============================================================================
# 3. Syslog Parser Tests
# ============================================================================


def test_syslog_parser_rfc5424(tmp_path):
    """Verify RFC 5424 syslog parsing with PRI calculation."""
    file_path = tmp_path / "rfc5424.syslog"
    # PRI 34: Facility = 34 // 8 = 4 (auth), Severity = 34 % 8 = 2 (crit)
    file_path.write_text(
        '<34>1 2026-10-09T22:14:15.003Z mymachine.example.com su 1234 ID47 '
        '[exampleSDID@32473 iut="3"] \'su root\' failed for user\n',
        encoding="utf-8",
    )

    parser = SyslogParser()
    result = parser.parse_file(file_path)

    assert result.format_type == "syslog"
    assert result.total_records == 1
    assert result.successful_records == 1
    rec = result.records[0]
    assert rec.is_valid is True
    assert rec.extracted["format"] == "rfc5424"
    assert rec.extracted["facility"] == 4
    assert rec.extracted["severity"] == 2
    assert rec.extracted["hostname"] == "mymachine.example.com"
    assert rec.extracted["app_name"] == "su"
    assert rec.extracted["procid"] == "1234"


def test_syslog_parser_rfc3164(tmp_path):
    """Verify RFC 3164 BSD syslog parsing."""
    file_path = tmp_path / "rfc3164.syslog"
    # PRI 13: Facility = 13 // 8 = 1 (user), Severity = 13 % 8 = 5 (notice)
    file_path.write_text(
        "<13>Oct 11 22:14:15 mymachine su[123]: 'su root' failed for lonvick\n",
        encoding="utf-8",
    )

    parser = SyslogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 1
    assert result.successful_records == 1
    rec = result.records[0]
    assert rec.extracted["format"] == "rfc3164"
    assert rec.extracted["facility"] == 1
    assert rec.extracted["severity"] == 5
    assert rec.extracted["hostname"] == "mymachine"
    assert rec.extracted["app_name"] == "su"


def test_syslog_parser_bsd_without_pri(tmp_path):
    """Verify legacy BSD syslog without priority header."""
    file_path = tmp_path / "legacy.syslog"
    file_path.write_text(
        "Oct 11 22:14:15 webserver01 nginx: Worker process 412 started\n",
        encoding="utf-8",
    )

    parser = SyslogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 1
    assert result.successful_records == 1
    rec = result.records[0]
    assert rec.extracted["hostname"] == "webserver01"
    assert rec.extracted["app_name"] == "nginx"


def test_syslog_parser_unmatched_lines(tmp_path):
    """Verify unmatched syslog line is retained with is_valid=False."""
    file_path = tmp_path / "unmatched.syslog"
    file_path.write_text(
        "Invalid non-syslog line content here without standard format\n",
        encoding="utf-8",
    )

    parser = SyslogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 1
    assert result.successful_records == 0
    assert result.failed_records == 1
    rec = result.records[0]
    assert rec.is_valid is False
    assert "Unmatched syslog format" in rec.error
    assert "Invalid non-syslog line" in rec.raw


# ============================================================================
# 4. Application Log Parser Tests
# ============================================================================


def test_app_log_parser_bracketed_pattern(tmp_path):
    """Verify bracketed application log pattern parsing."""
    file_path = tmp_path / "app.log"
    file_path.write_text(
        "[2026-10-09 14:30:00] [INFO] [auth-service] User session initialized\n"
        "[2026-10-09 14:31:00] [ERROR] [payment-gateway] Gateway connection refused\n",
        encoding="utf-8",
    )

    parser = ApplicationLogParser()
    result = parser.parse_file(file_path)

    assert result.format_type == "application_log"
    assert result.total_records == 2
    assert result.successful_records == 2
    assert result.records[0].extracted["log_level"] == "INFO"
    assert result.records[0].extracted["logger"] == "auth-service"
    assert result.records[1].extracted["log_level"] == "ERROR"


def test_app_log_parser_logback_pattern(tmp_path):
    """Verify Spring Boot / Logback style pattern parsing."""
    file_path = tmp_path / "spring.log"
    file_path.write_text(
        "2026-10-09 12:00:00.123  INFO 12345 --- [main] c.e.demo.Application : Started demo application\n",
        encoding="utf-8",
    )

    parser = ApplicationLogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 1
    assert result.successful_records == 1
    rec = result.records[0]
    assert rec.extracted["log_level"] == "INFO"
    assert rec.extracted["pid"] == "12345"
    assert rec.extracted["thread"] == "main"
    assert rec.extracted["logger"] == "c.e.demo.Application"


def test_app_log_parser_iso_and_minimal_patterns(tmp_path):
    """Verify ISO format and minimal level/message patterns."""
    file_path = tmp_path / "iso_minimal.log"
    file_path.write_text(
        "2026-10-09T15:00:00.000Z ERROR Database timeout\n"
        "2026-10-09 15:01:00 WARN High CPU usage\n",
        encoding="utf-8",
    )

    parser = ApplicationLogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 2
    assert result.successful_records == 2
    assert result.records[0].extracted["log_level"] == "ERROR"
    assert result.records[1].extracted["log_level"] == "WARN"


def test_app_log_parser_unmatched_stack_trace_line(tmp_path):
    """Verify unmatched stack trace line is preserved without inventing fields."""
    file_path = tmp_path / "trace.log"
    file_path.write_text(
        "[2026-10-09 10:00:00] [ERROR] [core] NullPointer occurred\n"
        "    at com.example.service.execute(Service.java:42)\n",
        encoding="utf-8",
    )

    parser = ApplicationLogParser()
    result = parser.parse_file(file_path)

    assert result.total_records == 2
    assert result.successful_records == 1
    assert result.failed_records == 1
    rec = result.records[1]
    assert rec.is_valid is False
    assert "Unmatched application log pattern" in rec.error
    assert "at com.example.service" in rec.raw


# ============================================================================
# 5. Encoding Edge Cases & Parser Factory Tests
# ============================================================================


def test_read_file_lines_safe_utf8_bom(tmp_path):
    """Verify UTF-8-BOM encoded file is read safely without BOM character in line 1."""
    bom_file = tmp_path / "bom.csv"
    content = "col1,col2\nval1,val2\n"
    bom_file.write_bytes(content.encode("utf-8-sig"))

    lines = read_file_lines_safe(bom_file)
    assert len(lines) == 2
    assert lines[0] == "col1,col2\n"
    assert not lines[0].startswith("\ufeff")


def test_read_file_lines_safe_latin1_fallback(tmp_path):
    """Verify Latin-1 encoded file with non-UTF-8 bytes falls back safely."""
    latin1_file = tmp_path / "latin1.log"
    # 0xe9 is 'é' in latin-1, which is invalid raw UTF-8
    raw_bytes = "[2026-10-09 12:00:00] [INFO] Café order received\n".encode("latin-1")
    latin1_file.write_bytes(raw_bytes)

    lines = read_file_lines_safe(latin1_file)
    assert len(lines) == 1
    assert "Café order" in lines[0]


def test_get_parser_for_format_routing():
    """Verify factory routes format strings to correct parser classes."""
    assert isinstance(get_parser_for_format("csv"), CSVLogParser)
    assert isinstance(get_parser_for_format("json"), JSONLogParser)
    assert isinstance(get_parser_for_format("syslog"), SyslogParser)
    assert isinstance(get_parser_for_format("application_log"), ApplicationLogParser)

    with pytest.raises(ValueError, match="Unsupported format type"):
        get_parser_for_format("unknown_format")


# ============================================================================
# 6. Service & API Endpoint Integration Tests
# ============================================================================


def make_mock_session():
    """Create an AsyncMock session for parsing service tests."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    return mock_session


@pytest.mark.asyncio
async def test_parse_upload_job_service_success(tmp_path, monkeypatch):
    """Verify parse_upload_job service updates job lifecycle to COMPLETED."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    # Create dummy raw CSV file
    raw_file = tmp_path / f"{job_id}.csv"
    raw_file.write_text(
        "timestamp,level,message\n"
        "2026-10-09T10:00:00Z,INFO,Service ready\n",
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="test.csv",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
        total_lines=0,
        parsed_lines=0,
        error_lines=0,
    )
    source = LogSource(
        id=source_id,
        name="test-csv-source",
        service_name="test",
        environment="production",
        format_type="csv",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    response = await parse_upload_job(job_id=job_id, session=mock_session)

    assert response.job_id == job_id
    assert response.status == "COMPLETED"
    assert response.format_type == "csv"
    assert response.total_records == 1
    assert response.successful_records == 1
    assert response.failed_records == 0
    assert job.status == "COMPLETED"
    assert job.parsed_lines == 1


@pytest.mark.asyncio
async def test_parse_upload_job_service_partially_failed(tmp_path, monkeypatch):
    """Verify parse_upload_job service sets PARTIALLY_FAILED when some rows fail."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.csv"
    raw_file.write_text(
        "col1,col2\n"
        "val1,val2\n"
        "mismatched_row_only_one_col\n",
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="mixed.csv",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
        total_lines=0,
        parsed_lines=0,
        error_lines=0,
    )
    source = LogSource(
        id=source_id,
        name="mixed-source",
        service_name="test",
        environment="production",
        format_type="csv",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    response = await parse_upload_job(job_id=job_id, session=mock_session)

    assert response.status == "PARTIALLY_FAILED"
    assert response.total_records == 2
    assert response.successful_records == 1
    assert response.failed_records == 1
    assert job.status == "PARTIALLY_FAILED"


def test_api_parse_endpoint_success(tmp_path, monkeypatch):
    """Verify POST /api/v1/logs/jobs/{job_id}/parse returns HTTP 200 with parsed results."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.json"
    raw_file.write_text(
        '{"timestamp": "2026-10-09T10:00:00Z", "level": "INFO", "msg": "API test"}\n',
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="api_test.json",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
        total_lines=0,
        parsed_lines=0,
        error_lines=0,
    )
    source = LogSource(
        id=source_id,
        name="json-source",
        service_name="api",
        environment="staging",
        format_type="json",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session

    response = client.post(f"/api/v1/logs/jobs/{job_id}/parse")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == str(job_id)
    assert data["status"] == "COMPLETED"
    assert data["format_type"] == "json"
    assert data["total_records"] == 1
    assert data["successful_records"] == 1
    assert len(data["records"]) == 1
    assert data["records"][0]["extracted"]["msg"] == "API test"


def test_api_parse_endpoint_job_not_found():
    """Verify POST /api/v1/logs/jobs/{job_id}/parse returns 404 for nonexistent job."""
    missing_id = uuid.uuid4()
    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session
    response = client.post(f"/api/v1/logs/jobs/{missing_id}/parse")
    app.dependency_overrides.clear()

    assert response.status_code == 404
    assert f"Upload job '{missing_id}' not found." in response.json()["detail"]


def test_base_parser_parse_file_not_found(tmp_path):
    """Verify BaseLogParser.parse_file returns safe error summary when file does not exist."""
    missing_file = tmp_path / "does_not_exist.log"
    parser = CSVLogParser()
    result = parser.parse_file(missing_file)
    assert result.total_records == 0
    assert result.successful_records == 0
    assert "does not exist on disk" in result.error_summary


@pytest.mark.asyncio
async def test_parse_upload_job_file_missing_on_disk(tmp_path, monkeypatch):
    """Verify parse_upload_job raises 404 when file is not in upload dir."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="missing.csv",
        file_size_bytes=100,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="test-source",
        service_name="test",
        environment="production",
        format_type="csv",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    with pytest.raises(Exception) as exc_info:
        await parse_upload_job(job_id=job_id, session=mock_session)

    assert "not found on disk" in str(exc_info.value)


@pytest.mark.asyncio
async def test_parse_upload_job_unsupported_format_type(tmp_path, monkeypatch):
    """Verify parse_upload_job handles unsupported format type by setting status to FAILED."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.bin"
    raw_file.write_bytes(b"binary data")

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="data.bin",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="bin-source",
        service_name="test",
        environment="production",
        format_type="unsupported_format_xyz",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    with pytest.raises(Exception) as exc_info:
        await parse_upload_job(job_id=job_id, session=mock_session)

    assert job.status == "FAILED"
    assert "Unsupported format type" in job.error_summary


@pytest.mark.asyncio
async def test_parse_upload_job_service_all_failed(tmp_path, monkeypatch):
    """Verify parse_upload_job sets status FAILED when all records fail parsing."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.syslog"
    raw_file.write_text("random bad line 1\nrandom bad line 2\n", encoding="utf-8")

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="bad.syslog",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="bad-source",
        service_name="test",
        environment="production",
        format_type="syslog",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    response = await parse_upload_job(job_id=job_id, session=mock_session)

    assert response.status == "FAILED"
    assert response.total_records == 2
    assert response.successful_records == 0
    assert response.failed_records == 2
    assert job.status == "FAILED"

