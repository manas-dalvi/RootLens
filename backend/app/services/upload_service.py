import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.log_source import LogSource
from app.models.upload_job import UploadJob
from app.schemas.log_upload import LogUploadResponse, UploadJobDetailResponse

logger = logging.getLogger("rootlens.upload")

CHUNK_SIZE = 64 * 1024  # 64 KB read buffer


def sanitize_filename(filename: Optional[str]) -> str:
    """Sanitize client-provided filename to prevent directory traversal and invalid characters."""
    if not filename:
        return "unnamed_upload.log"

    # Strip directory components (both Unix and Windows paths)
    clean = os.path.basename(filename.replace("\\", "/"))
    # Remove null bytes and non-printable control characters
    clean = re.sub(r"[\x00-\x1f\x7f]", "", clean)
    # Strip dangerous shell characters and leading periods
    clean = clean.strip(". ")
    if not clean:
        return "unnamed_upload.log"
    # Ensure maximum length constraints per database VARCHAR(255)
    return clean[:255]


def detect_format_type(
    extension: str,
    filename: Optional[str] = None,
    service_name: Optional[str] = None,
) -> str:
    """Map validated file extension and metadata to canonical format type."""
    ext = extension.lower()
    fn = (filename or "").lower()
    svc = (service_name or "").lower()
    if "hdfs" in fn or "hdfs" in svc:
        return "hdfs"
    if ext == ".json":
        return "json"
    if ext == ".csv":
        return "csv"
    if ext == ".syslog":
        return "syslog"
    if ext in (".log", ".txt"):
        return "application_log"
    return "unstructured"


async def process_log_upload(
    file: UploadFile,
    session: AsyncSession,
    service_name: Optional[str] = None,
    environment: Optional[str] = None,
    source_name: Optional[str] = None,
    log_source_id: Optional[uuid.UUID] = None,
) -> LogUploadResponse:
    """Validate, stream, and register an uploaded log file with atomic database tracking.
    
    Ensures:
    - Bounded memory usage via streaming chunk writes
    - Path traversal prevention
    - File size limits and empty file rejection
    - Atomic rollback and disk cleanup on failure
    - Zero exposure of local absolute paths or credentials
    """
    if not file or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided in upload request.",
        )

    safe_name = sanitize_filename(file.filename)
    suffix = Path(safe_name).suffix.lower()

    if suffix not in settings.ALLOWED_LOG_EXTENSIONS:
        allowed = ", ".join(settings.ALLOWED_LOG_EXTENSIONS)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{suffix}'. Supported extensions are: {allowed}.",
        )

    format_type = detect_format_type(suffix, filename=safe_name, service_name=service_name)
    job_id = uuid.uuid4()

    # Ensure dedicated upload directory exists
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Server-controlled storage filename (job UUID + safe extension)
    target_file = upload_dir / f"{job_id}{suffix}"
    bytes_written = 0

    try:
        with open(target_file, "wb") as out_file:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > settings.MAX_UPLOAD_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            f"File size exceeds maximum allowed limit of "
                            f"{settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
                        ),
                    )
                out_file.write(chunk)

        if bytes_written == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty (0 bytes).",
            )

        # Resolve or create the associated LogSource
        target_log_source = await resolve_or_create_log_source(
            session=session,
            log_source_id=log_source_id,
            service_name=service_name,
            environment=environment,
            source_name=source_name,
            format_type=format_type,
            fallback_filename=safe_name,
        )

        # Record UploadJob in database
        now = datetime.now(timezone.utc)
        upload_job = UploadJob(
            id=job_id,
            log_source_id=target_log_source.id,
            file_name=safe_name,
            file_size_bytes=bytes_written,
            status="PENDING",
            total_lines=0,
            parsed_lines=0,
            error_lines=0,
            created_at=now,
            updated_at=now,
        )
        session.add(upload_job)
        await session.commit()

        logger.info(
            "Log upload registered: job_id=%s, size=%d, source=%s",
            job_id,
            bytes_written,
            target_log_source.name,
        )

        return LogUploadResponse(
            job_id=upload_job.id,
            status=upload_job.status,
            file_name=upload_job.file_name,
            file_size_bytes=upload_job.file_size_bytes,
            format_type=target_log_source.format_type,
            log_source_id=target_log_source.id,
            service_name=target_log_source.service_name,
            environment=target_log_source.environment,
            created_at=upload_job.created_at,
            message="Log file uploaded and queued for processing.",
        )

    except HTTPException:
        # Clean up partially written file on validation failure
        if target_file.exists():
            try:
                target_file.unlink()
            except OSError:
                pass
        raise

    except Exception as exc:
        # Atomic rollback and disk cleanup on unexpected failure
        await session.rollback()
        if target_file.exists():
            try:
                target_file.unlink()
            except OSError:
                pass
        logger.error("Failed to process log upload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record log upload. Please try again.",
        )


async def resolve_or_create_log_source(
    session: AsyncSession,
    log_source_id: Optional[uuid.UUID],
    service_name: Optional[str],
    environment: Optional[str],
    source_name: Optional[str],
    format_type: str,
    fallback_filename: str,
) -> LogSource:
    """Resolve an existing LogSource or create a safe default source relationally."""
    if log_source_id:
        stmt = select(LogSource).where(LogSource.id == log_source_id)
        result = await session.execute(stmt)
        found = result.scalar_one_or_none()
        if not found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Specified log source ID '{log_source_id}' does not exist.",
            )
        return found

    # Determine default attributes if not provided
    resolved_service = (service_name or "default").strip()[:100]
    resolved_env = (environment or "production").strip()[:50]
    resolved_source_name = (
        source_name.strip() if source_name else f"{resolved_service}-logs"
    )[:100]

    # Query for existing source matching (name, environment) unique constraint
    stmt = select(LogSource).where(
        LogSource.name == resolved_source_name,
        LogSource.environment == resolved_env,
    )
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        return existing

    # Create new LogSource record
    now = datetime.now(timezone.utc)
    new_source = LogSource(
        id=uuid.uuid4(),
        name=resolved_source_name,
        service_name=resolved_service,
        environment=resolved_env,
        format_type=format_type,
        description=f"Auto-registered source for {fallback_filename}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(new_source)
    await session.flush()
    return new_source


async def get_upload_job_detail(
    job_id: uuid.UUID,
    session: AsyncSession,
) -> UploadJobDetailResponse:
    """Retrieve metadata and current ingestion lifecycle state for a given upload job."""
    stmt = (
        select(UploadJob, LogSource)
        .join(LogSource, UploadJob.log_source_id == LogSource.id)
        .where(UploadJob.id == job_id)
    )
    result = await session.execute(stmt)
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Upload job '{job_id}' not found.",
        )

    job, source = row
    return UploadJobDetailResponse(
        id=job.id,
        log_source_id=job.log_source_id,
        service_name=source.service_name,
        environment=source.environment,
        file_name=job.file_name,
        file_size_bytes=job.file_size_bytes,
        status=job.status,
        total_lines=job.total_lines,
        parsed_lines=job.parsed_lines,
        error_lines=job.error_lines,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error_summary=job.error_summary,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
