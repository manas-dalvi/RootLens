import io
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
from app.services.upload_service import detect_format_type, sanitize_filename

client = TestClient(app)


def test_sanitize_filename_prevents_traversal():
    """Verify directory traversal patterns are safely stripped."""
    assert sanitize_filename("../../etc/passwd.log") == "passwd.log"
    assert sanitize_filename("..\\..\\windows\\system32\\calc.log") == "calc.log"
    assert sanitize_filename("/var/log/syslog") == "syslog"
    assert sanitize_filename("..") == "unnamed_upload.log"
    assert sanitize_filename("") == "unnamed_upload.log"
    assert sanitize_filename(None) == "unnamed_upload.log"


def test_sanitize_filename_strips_control_characters():
    """Verify null bytes and control chars are removed."""
    dirty = "app\x00\x1f_test.log"
    clean = sanitize_filename(dirty)
    assert "\x00" not in clean
    assert "\x1f" not in clean
    assert clean == "app_test.log"


def test_detect_format_type_mapping():
    """Verify extensions map to expected canonical formats."""
    assert detect_format_type(".json") == "json"
    assert detect_format_type(".csv") == "csv"
    assert detect_format_type(".syslog") == "syslog"
    assert detect_format_type(".log") == "application_log"
    assert detect_format_type(".txt") == "application_log"
    assert detect_format_type(".unknown") == "unstructured"


def make_mock_session():
    """Create an AsyncMock session with synchronous add/flush helpers."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_result.first.return_value = None
    mock_session.execute.return_value = mock_result
    return mock_session


def test_upload_successful_json(tmp_path, monkeypatch):
    """Verify successful upload of a JSON log file with bounded streaming storage."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    mock_session = make_mock_session()
    app.dependency_overrides[get_db] = lambda: mock_session

    content = b'{"timestamp": "2026-10-09T20:00:00Z", "level": "ERROR", "message": "Failed"}\n'
    file_payload = ("test_events.json", io.BytesIO(content), "application/json")

    response = client.post(
        "/api/v1/logs/upload",
        files={"file": file_payload},
        data={"service_name": "payment-service", "environment": "staging"},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "PENDING"
    assert data["file_name"] == "test_events.json"
    assert data["file_size_bytes"] == len(content)
    assert data["format_type"] == "json"
    assert data["service_name"] == "payment-service"
    assert data["environment"] == "staging"

    # Verify file saved on disk under server-controlled filename
    saved_file = tmp_path / f"{data['job_id']}.json"
    assert saved_file.exists()
    assert saved_file.read_bytes() == content


@pytest.mark.parametrize("ext,format_expected", [
    (".csv", "csv"),
    (".log", "application_log"),
    (".txt", "application_log"),
    (".syslog", "syslog"),
])
def test_upload_all_supported_extensions(tmp_path, monkeypatch, ext, format_expected):
    """Verify all approved file extensions are accepted and properly categorized."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    mock_session = make_mock_session()
    app.dependency_overrides[get_db] = lambda: mock_session

    content = b"2026-10-09 INFO Test log entry"
    filename = f"sample_log{ext}"
    response = client.post(
        "/api/v1/logs/upload",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        data={"service_name": "worker"},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 201
    data = response.json()
    assert data["format_type"] == format_expected
    assert data["file_size_bytes"] == len(content)


def test_upload_missing_file():
    """Verify request without file returns 422 Unprocessable Entity."""
    response = client.post("/api/v1/logs/upload", data={"service_name": "api"})
    assert response.status_code == 422


def test_upload_empty_file(tmp_path, monkeypatch):
    """Verify empty 0-byte file upload is rejected with 400 Bad Request."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    mock_session = make_mock_session()
    app.dependency_overrides[get_db] = lambda: mock_session

    response = client.post(
        "/api/v1/logs/upload",
        files={"file": ("empty.log", io.BytesIO(b""), "text/plain")},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()
    # Confirm no residual files on disk
    assert len(list(tmp_path.iterdir())) == 0


def test_upload_unsupported_extension(tmp_path, monkeypatch):
    """Verify unsupported file extensions are rejected with 400 Bad Request."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    mock_session = make_mock_session()
    app.dependency_overrides[get_db] = lambda: mock_session

    response = client.post(
        "/api/v1/logs/upload",
        files={"file": ("document.pdf", io.BytesIO(b"%PDF-1.4..."), "application/pdf")},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"].lower()
    assert len(list(tmp_path.iterdir())) == 0


def test_upload_oversized_file(tmp_path, monkeypatch):
    """Verify files exceeding MAX_UPLOAD_SIZE_BYTES are rejected with 413 and cleaned up."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 50)  # 50 bytes threshold

    mock_session = make_mock_session()
    app.dependency_overrides[get_db] = lambda: mock_session

    big_content = b"X" * 100
    response = client.post(
        "/api/v1/logs/upload",
        files={"file": ("big.log", io.BytesIO(big_content), "text/plain")},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 413
    assert "exceeds" in response.json()["detail"].lower()
    # Verify file was cleaned up from disk
    assert len(list(tmp_path.iterdir())) == 0


def test_upload_database_failure_cleans_up_disk(tmp_path, monkeypatch):
    """Verify disk file is deleted if database commit fails."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    mock_session = make_mock_session()
    # Simulate DB error during commit
    mock_session.commit.side_effect = RuntimeError("Database deadlock")

    app.dependency_overrides[get_db] = lambda: mock_session

    response = client.post(
        "/api/v1/logs/upload",
        files={"file": ("error_test.log", io.BytesIO(b"data"), "text/plain")},
    )
    app.dependency_overrides.clear()

    assert response.status_code == 500
    mock_session.rollback.assert_called_once()
    # File must be cleaned up from disk
    assert len(list(tmp_path.iterdir())) == 0


def test_get_upload_job_status_success():
    """Verify retrieving existing upload job metadata returns expected schema."""
    job_id = uuid.uuid4()
    source_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_job = MagicMock(spec=UploadJob)
    mock_job.id = job_id
    mock_job.log_source_id = source_id
    mock_job.file_name = "test.log"
    mock_job.file_size_bytes = 1024
    mock_job.status = "PENDING"
    mock_job.total_lines = 0
    mock_job.parsed_lines = 0
    mock_job.error_lines = 0
    mock_job.started_at = None
    mock_job.completed_at = None
    mock_job.error_summary = None
    mock_job.created_at = now
    mock_job.updated_at = now

    mock_source = MagicMock(spec=LogSource)
    mock_source.id = source_id
    mock_source.service_name = "auth-service"
    mock_source.environment = "production"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.first.return_value = (mock_job, mock_source)
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session

    response = client.get(f"/api/v1/logs/jobs/{job_id}")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(job_id)
    assert data["service_name"] == "auth-service"
    assert data["environment"] == "production"
    assert data["status"] == "PENDING"
    assert data["file_size_bytes"] == 1024


def test_get_upload_job_status_not_found():
    """Verify 404 is returned when job does not exist."""
    job_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.first.return_value = None
    mock_session.execute.return_value = mock_result

    app.dependency_overrides[get_db] = lambda: mock_session

    response = client.get(f"/api/v1/logs/jobs/{job_id}")
    app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_upload_never_exposes_absolute_disk_path(tmp_path, monkeypatch):
    """Verify response payload does not expose internal host filesystem paths."""
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)

    mock_session = make_mock_session()
    app.dependency_overrides[get_db] = lambda: mock_session

    response = client.post(
        "/api/v1/logs/upload",
        files={"file": ("safe.log", io.BytesIO(b"log data"), "text/plain")},
    )
    app.dependency_overrides.clear()

    text_resp = response.text
    # Host drive letter or absolute path shouldn't appear in client JSON
    assert str(tmp_path) not in text_resp
    assert "d:\\" not in text_resp.lower()
    assert "/data/uploads" not in text_resp
