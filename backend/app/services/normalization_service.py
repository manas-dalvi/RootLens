import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.log_source import LogSource
from app.models.upload_job import UploadJob
from app.normalizers import normalize_parsing_result
from app.parsers import get_parser_for_format
from app.schemas.log_normalization import NormalizationResponse, NormalizedLogItem

logger = logging.getLogger("rootlens.normalization")


async def normalize_upload_job(
    job_id: uuid.UUID,
    session: AsyncSession,
) -> NormalizationResponse:
    """Execute log normalization and validation on an upload job.

    Workflow:
    1. Query UploadJob and associated LogSource.
    2. Locate the stored raw file.
    3. Transition status to PROCESSING.
    4. Run format-specific parser for the validated format_type.
    5. Execute normalization and validation across all extracted records.
    6. Update UploadJob lifecycle status, line metrics, error summary, and timestamps.
    7. Commit transaction and return NormalizationResponse.
    """
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

    # Locate raw file
    upload_dir = Path(settings.UPLOAD_DIR)
    suffix = Path(job.file_name).suffix.lower()
    target_file = upload_dir / f"{job.id}{suffix}"

    if not target_file.exists():
        matches = list(upload_dir.glob(f"{job.id}*"))
        if matches:
            target_file = matches[0]
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Stored raw file for upload job '{job_id}' was not found on disk.",
            )

    now = datetime.now(timezone.utc)
    job.status = "PROCESSING"
    if not job.started_at:
        job.started_at = now
    job.updated_at = now
    await session.commit()

    # Resolve parser
    try:
        parser = get_parser_for_format(source.format_type)
    except ValueError as err:
        job.status = "FAILED"
        job.completed_at = datetime.now(timezone.utc)
        job.error_summary = str(err)
        job.updated_at = datetime.now(timezone.utc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )

    # Parse raw log records
    try:
        parse_result = parser.parse_file(target_file)
    except Exception as exc:
        logger.error("Parsing failed during normalization for job %s: %s", job.id, exc, exc_info=True)
        job.status = "FAILED"
        job.completed_at = datetime.now(timezone.utc)
        job.error_summary = f"Parsing failed: {str(exc)}"
        job.updated_at = datetime.now(timezone.utc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to parse log file.",
        )

    # Execute normalization & validation
    norm_result = normalize_parsing_result(
        result=parse_result,
        default_service=source.service_name,
        default_env=source.environment,
    )

    # Determine updated lifecycle status
    if norm_result.total_records == 0:
        new_status = "COMPLETED"
    elif norm_result.invalid_records == 0:
        new_status = "COMPLETED"
    elif norm_result.valid_records > 0 and norm_result.invalid_records > 0:
        new_status = "PARTIALLY_FAILED"
    else:
        new_status = "FAILED"

    completion_time = datetime.now(timezone.utc)
    job.status = new_status
    job.total_lines = norm_result.total_records
    job.parsed_lines = norm_result.valid_records
    job.error_lines = norm_result.invalid_records
    job.error_summary = norm_result.summary if norm_result.invalid_records > 0 else None
    job.completed_at = completion_time
    job.updated_at = completion_time
    await session.commit()

    logger.info(
        "Job %s normalized: status=%s, total=%d, valid=%d, invalid=%d",
        job.id,
        new_status,
        norm_result.total_records,
        norm_result.valid_records,
        norm_result.invalid_records,
    )

    items = [
        NormalizedLogItem(
            line_number=r.line_number,
            timestamp=r.timestamp,
            service_name=r.service_name,
            environment=r.environment,
            log_level=r.log_level,
            message=r.message,
            host=r.host,
            trace_id=r.trace_id,
            span_id=r.span_id,
            attributes=r.attributes,
            raw_log=r.raw_log,
            source_format=r.source_format,
            is_valid=r.is_valid,
            validation_errors=r.validation_errors,
            raw_timestamp=r.raw_timestamp,
            raw_level=r.raw_level,
            original_fields=r.original_fields,
        )
        for r in norm_result.records
    ]

    return NormalizationResponse(
        job_id=job.id,
        status=job.status,
        format_type=source.format_type,
        total_records=norm_result.total_records,
        valid_records=norm_result.valid_records,
        invalid_records=norm_result.invalid_records,
        summary=norm_result.summary,
        records=items,
    )
