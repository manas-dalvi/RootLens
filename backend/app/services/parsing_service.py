import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.log_source import LogSource
from app.models.upload_job import UploadJob
from app.parsers import get_parser_for_format
from app.schemas.log_upload import ParsedRecordItem, ParsingResultResponse

logger = logging.getLogger("rootlens.parsing")


async def parse_upload_job(
    job_id: uuid.UUID,
    session: AsyncSession,
) -> ParsingResultResponse:
    """Execute format-specific parsing on an existing upload job and update its lifecycle state.

    Workflow:
    1. Query UploadJob and associated LogSource.
    2. Verify the raw file exists in the server-managed storage directory.
    3. Transition status to PROCESSING.
    4. Resolve parser based strictly on validated format_type.
    5. Execute format-specific parser (CSV, JSON, Syslog, or Application Log).
    6. Update UploadJob line counts, timestamps, error_summary, and status:
       - COMPLETED if 0 errors.
       - PARTIALLY_FAILED if some records succeeded and some failed.
       - FAILED if all records failed or empty with errors.
    7. Commit transaction and return ParsingResultResponse.
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

    # Locate the stored raw file
    upload_dir = Path(settings.UPLOAD_DIR)
    suffix = Path(job.file_name).suffix.lower()
    target_file = upload_dir / f"{job.id}{suffix}"

    if not target_file.exists():
        # Fallback wildcard match if extension casing differs
        matches = list(upload_dir.glob(f"{job.id}*"))
        if matches:
            target_file = matches[0]
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Stored raw file for upload job '{job_id}' was not found on disk.",
            )

    # Transition to PROCESSING
    now = datetime.now(timezone.utc)
    job.status = "PROCESSING"
    job.started_at = now
    job.updated_at = now
    await session.commit()

    # Resolve parser for the verified format_type
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

    # Execute parser
    try:
        parse_result = parser.parse_file(target_file)
    except Exception as exc:
        logger.error("Parser execution failed for job %s: %s", job.id, exc, exc_info=True)
        job.status = "FAILED"
        job.completed_at = datetime.now(timezone.utc)
        job.error_summary = f"Parser execution failed: {str(exc)}"
        job.updated_at = datetime.now(timezone.utc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to parse log file.",
        )

    # Determine final lifecycle status
    if parse_result.total_records == 0:
        new_status = "COMPLETED"
    elif parse_result.failed_records == 0:
        new_status = "COMPLETED"
    elif parse_result.successful_records > 0 and parse_result.failed_records > 0:
        new_status = "PARTIALLY_FAILED"
    else:
        new_status = "FAILED"

    completion_time = datetime.now(timezone.utc)
    job.status = new_status
    job.total_lines = parse_result.total_records
    job.parsed_lines = parse_result.successful_records
    job.error_lines = parse_result.failed_records
    job.error_summary = parse_result.error_summary
    job.completed_at = completion_time
    job.updated_at = completion_time
    await session.commit()

    logger.info(
        "Job %s parsed: status=%s, total=%d, parsed=%d, errors=%d",
        job.id,
        new_status,
        parse_result.total_records,
        parse_result.successful_records,
        parse_result.failed_records,
    )

    items = [
        ParsedRecordItem(
            line_number=r.line_number,
            raw=r.raw,
            extracted=r.extracted,
            is_valid=r.is_valid,
            error=r.error,
        )
        for r in parse_result.records
    ]

    return ParsingResultResponse(
        job_id=job.id,
        status=job.status,
        format_type=source.format_type,
        total_records=parse_result.total_records,
        successful_records=parse_result.successful_records,
        failed_records=parse_result.failed_records,
        error_summary=parse_result.error_summary,
        records=items,
    )
