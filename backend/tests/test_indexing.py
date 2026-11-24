import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.opensearch import resolve_log_index_name
from app.db.session import get_db
from app.main import app
from app.models.log_source import LogSource
from app.models.upload_job import UploadJob
from app.services.indexing_service import index_upload_job

client = TestClient(app)


# ============================================================================
# Helpers
# ============================================================================


def make_mock_session():
    """Create an AsyncMock session for database operations."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    return mock_session


def make_mock_opensearch(ping_alive: bool = True):
    """Create a mock OpenSearch client instance."""
    mock_es = MagicMock()
    mock_es.ping.return_value = ping_alive
    mock_es.indices.put_index_template.return_value = {"acknowledged": True}
    return mock_es


# ============================================================================
# 1. Index Name Resolution Tests
# ============================================================================


def test_resolve_log_index_name_with_timestamp():
    """Verify index name format matches '{prefix}-{YYYY.MM}'."""
    dt = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
    idx = resolve_log_index_name(dt)
    assert idx == "rootlens-logs-2026.10"

    # Custom prefix
    custom_idx = resolve_log_index_name(dt, index_prefix="custom-logs")
    assert custom_idx == "custom-logs-2026.10"


def test_resolve_log_index_name_without_timestamp():
    """Verify fallback to current UTC month when timestamp is None."""
    current_month = datetime.now(timezone.utc).strftime("%Y.%m")
    idx = resolve_log_index_name(None)
    assert idx == f"rootlens-logs-{current_month}"


# ============================================================================
# 2. Service-Level Indexing Pipeline Tests
# ============================================================================


@pytest.mark.asyncio
async def test_index_upload_job_success(tmp_path, monkeypatch):
    """Verify successful OpenSearch bulk indexing with full counter updates."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    # Create test raw file
    raw_file = tmp_path / f"{job_id}.json"
    raw_file.write_text(
        '{"timestamp": "2026-10-09T10:00:00Z", "level": "INFO", "message": "App started"}\n'
        '{"timestamp": "2026-10-09T10:01:00Z", "level": "WARN", "message": "High memory"}\n',
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="events.json",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
        total_lines=0,
        parsed_lines=0,
        error_lines=0,
    )
    source = LogSource(
        id=source_id,
        name="test-events",
        service_name="auth-service",
        environment="production",
        format_type="json",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    mock_es = make_mock_opensearch(ping_alive=True)

    with patch("app.services.indexing_service.bulk") as mock_bulk:
        mock_bulk.return_value = (2, [])  # 2 successes, 0 failures

        response = await index_upload_job(job_id=job_id, session=mock_session, client=mock_es)

        assert response.job_id == job_id
        assert response.status == "COMPLETED"
        assert response.target_index == "rootlens-logs-2026.10"
        assert response.total_records == 2
        assert response.indexed_records == 2
        assert response.failed_records == 0
        assert response.quarantined_records == 0

        # Verify UploadJob DB updates
        assert job.status == "COMPLETED"
        assert job.total_lines == 2
        assert job.parsed_lines == 2
        assert job.error_lines == 0
        assert job.error_summary is None

        # Verify bulk was called with actions
        assert mock_bulk.called
        actions = mock_bulk.call_args[1]["actions"]
        assert len(actions) == 2


@pytest.mark.asyncio
async def test_index_upload_job_deterministic_ids_and_actions(tmp_path, monkeypatch):
    """Verify document IDs are deterministic and actions match canonical mapping."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.csv"
    raw_file.write_text(
        "timestamp,level,message\n"
        "2026-10-09T10:00:00Z,INFO,Payment processed\n",
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="payments.csv",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="csv-source",
        service_name="payment-gateway",
        environment="production",
        format_type="csv",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    mock_es = make_mock_opensearch(ping_alive=True)

    with patch("app.services.indexing_service.bulk") as mock_bulk:
        mock_bulk.return_value = (1, [])

        await index_upload_job(job_id=job_id, session=mock_session, client=mock_es)

        actions = mock_bulk.call_args[1]["actions"]
        action = actions[0]

        # Verify deterministic ID pattern: '{job_id}_{line_number}'
        expected_id = f"{job_id}_2"
        assert action["_id"] == expected_id
        assert action["_op_type"] == "index"
        assert action["_index"] == "rootlens-logs-2026.10"

        # Verify canonical mapping structure in _source
        source_doc = action["_source"]
        assert source_doc["service_name"] == "payment-gateway"
        assert source_doc["environment"] == "production"
        assert source_doc["log_level"] == "INFO"
        assert source_doc["message"] == "Payment processed"
        assert source_doc["attributes"]["job_id"] == str(job_id)
        assert source_doc["attributes"]["line_number"] == 2
        assert source_doc["attributes"]["source_format"] == "csv"
        assert source_doc["raw_log"] == "2026-10-09T10:00:00Z,INFO,Payment processed"


@pytest.mark.asyncio
async def test_index_upload_job_empty_file(tmp_path, monkeypatch):
    """Verify empty file returns COMPLETED with zero documents indexed."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.json"
    raw_file.write_text("", encoding="utf-8")

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="empty.json",
        file_size_bytes=0,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="empty-source",
        service_name="test",
        environment="production",
        format_type="json",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    mock_es = make_mock_opensearch(ping_alive=True)

    with patch("app.services.indexing_service.bulk") as mock_bulk:
        response = await index_upload_job(job_id=job_id, session=mock_session, client=mock_es)

        assert response.status == "COMPLETED"
        assert response.total_records == 0
        assert response.indexed_records == 0
        assert job.status == "COMPLETED"
        assert not mock_bulk.called


@pytest.mark.asyncio
async def test_index_upload_job_partial_quarantine_and_success(tmp_path, monkeypatch):
    """Verify invalid records are quarantined while valid records are indexed."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    # 1 valid row, 1 malformed row with missing timestamp/message
    raw_file = tmp_path / f"{job_id}.csv"
    raw_file.write_text(
        "timestamp,level,message\n"
        "2026-10-09T10:00:00Z,INFO,Valid event\n"
        "only_one_column_row\n",
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="partial.csv",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="partial-source",
        service_name="test-svc",
        environment="production",
        format_type="csv",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    mock_es = make_mock_opensearch(ping_alive=True)

    with patch("app.services.indexing_service.bulk") as mock_bulk:
        mock_bulk.return_value = (1, [])  # Only 1 document sent to bulk, 0 bulk errors

        response = await index_upload_job(job_id=job_id, session=mock_session, client=mock_es)

        assert response.status == "PARTIALLY_FAILED"
        assert response.total_records == 2
        assert response.indexed_records == 1
        assert response.quarantined_records == 1
        assert response.failed_records == 0

        assert job.status == "PARTIALLY_FAILED"
        assert job.parsed_lines == 1
        assert job.error_lines == 1
        assert "quarantined" in job.error_summary


@pytest.mark.asyncio
async def test_index_upload_job_all_quarantined(tmp_path, monkeypatch):
    """Verify job sets FAILED when all records fail validation."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.syslog"
    raw_file.write_text(
        "non-syslog-bad-line-1\n"
        "non-syslog-bad-line-2\n",
        encoding="utf-8",
    )

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
        service_name="test-svc",
        environment="production",
        format_type="syslog",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    mock_es = make_mock_opensearch(ping_alive=True)

    with patch("app.services.indexing_service.bulk") as mock_bulk:
        response = await index_upload_job(job_id=job_id, session=mock_session, client=mock_es)

        assert response.status == "FAILED"
        assert response.total_records == 2
        assert response.indexed_records == 0
        assert response.quarantined_records == 2
        assert job.status == "FAILED"
        assert not mock_bulk.called


@pytest.mark.asyncio
async def test_index_upload_job_bulk_partial_failure(tmp_path, monkeypatch):
    """Verify partial failure returned from OpenSearch bulk is captured accurately."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.json"
    raw_file.write_text(
        '{"timestamp": "2026-10-09T10:00:00Z", "level": "INFO", "message": "Record 1"}\n'
        '{"timestamp": "2026-10-09T10:01:00Z", "level": "INFO", "message": "Record 2"}\n',
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="events.json",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="source",
        service_name="svc",
        environment="production",
        format_type="json",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    mock_es = make_mock_opensearch(ping_alive=True)

    with patch("app.services.indexing_service.bulk") as mock_bulk:
        # 1 success, 1 error returned by OpenSearch
        mock_bulk.return_value = (
            1,
            [{"index": {"_id": f"{job_id}_2", "error": {"type": "mapper_parsing_exception"}}}],
        )

        response = await index_upload_job(job_id=job_id, session=mock_session, client=mock_es)

        assert response.status == "PARTIALLY_FAILED"
        assert response.total_records == 2
        assert response.indexed_records == 1
        assert response.failed_records == 1
        assert response.quarantined_records == 0
        assert job.status == "PARTIALLY_FAILED"
        assert "rejected by OpenSearch" in job.error_summary


@pytest.mark.asyncio
async def test_index_upload_job_opensearch_unavailable(tmp_path, monkeypatch):
    """Verify HTTP 503 is raised and job marked FAILED when OpenSearch ping returns False."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.json"
    raw_file.write_text('{"timestamp": "2026-10-09T10:00:00Z", "message": "Hi"}\n', encoding="utf-8")

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="test.json",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="source",
        service_name="svc",
        environment="production",
        format_type="json",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    mock_es = make_mock_opensearch(ping_alive=False)

    with pytest.raises(Exception) as exc_info:
        await index_upload_job(job_id=job_id, session=mock_session, client=mock_es)

    assert "503" in str(exc_info.value) or "unavailable" in str(exc_info.value)
    assert job.status == "FAILED"
    assert "unavailable" in job.error_summary


@pytest.mark.asyncio
async def test_index_upload_job_missing_file_on_disk(tmp_path, monkeypatch):
    """Verify HTTP 404 is raised when raw file cannot be located on disk."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="missing.log",
        file_size_bytes=100,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="source",
        service_name="svc",
        environment="production",
        format_type="application_log",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    with pytest.raises(Exception) as exc_info:
        await index_upload_job(job_id=job_id, session=mock_session)

    assert "not found on disk" in str(exc_info.value)


# ============================================================================
# 3. API Endpoint Integration Tests
# ============================================================================


def test_api_index_endpoint_success(tmp_path, monkeypatch):
    """Verify POST /api/v1/logs/jobs/{job_id}/index returns 200 with indexing response."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    job_id = uuid.uuid4()
    source_id = uuid.uuid4()

    raw_file = tmp_path / f"{job_id}.json"
    raw_file.write_text(
        '{"timestamp": "2026-10-09T10:00:00Z", "level": "INFO", "message": "API Indexing"}\n',
        encoding="utf-8",
    )

    job = UploadJob(
        id=job_id,
        log_source_id=source_id,
        file_name="api_test.json",
        file_size_bytes=raw_file.stat().st_size,
        status="PENDING",
    )
    source = LogSource(
        id=source_id,
        name="api-source",
        service_name="core-service",
        environment="production",
        format_type="json",
    )

    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = (job, source)
    mock_session.execute.return_value = mock_result

    mock_es = make_mock_opensearch(ping_alive=True)

    app.dependency_overrides[get_db] = lambda: mock_session

    with patch("app.services.indexing_service.get_opensearch_client", return_value=mock_es):
        with patch("app.services.indexing_service.bulk", return_value=(1, [])):
            response = client.post(f"/api/v1/logs/jobs/{job_id}/index")

    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == str(job_id)
    assert data["status"] == "COMPLETED"
    assert data["indexed_records"] == 1
    assert data["failed_records"] == 0
    assert data["quarantined_records"] == 0


def test_api_index_endpoint_job_not_found():
    """Verify POST /api/v1/logs/jobs/{job_id}/index returns 404 for nonexistent job."""
    missing_id = uuid.uuid4()
    mock_session = make_mock_session()
    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session
    response = client.post(f"/api/v1/logs/jobs/{missing_id}/index")
    app.dependency_overrides.clear()

    assert response.status_code == 404
    assert f"Upload job '{missing_id}' not found." in response.json()["detail"]
