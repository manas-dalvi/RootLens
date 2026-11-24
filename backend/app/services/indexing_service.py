import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import HTTPException, status
from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.opensearch import (
    ensure_log_index_template,
    get_opensearch_client,
    resolve_log_index_name,
)
from app.models.log_source import LogSource
from app.models.upload_job import UploadJob
from app.normalizers import normalize_parsing_result
from app.parsers import get_parser_for_format
from app.schemas.log_indexing import LogIndexingResponse

logger = logging.getLogger("rootlens.indexing")


async def index_upload_job(
    job_id: uuid.UUID,
    session: AsyncSession,
    client: Optional[OpenSearch] = None,
) -> LogIndexingResponse:
    """Execute OpenSearch bulk indexing for an upload job.

    Workflow:
    1. Query UploadJob and joined LogSource from PostgreSQL.
    2. Locate the raw log file in the upload directory.
    3. Verify OpenSearch cluster availability.
    4. Ensure the canonical log index template exists in OpenSearch.
    5. Parse and normalize log records.
    6. Quarantine invalid records and prepare valid documents with deterministic IDs.
    7. Execute OpenSearch bulk indexing with partial-failure safety.
    8. Update UploadJob lifecycle status, line counters, error summary, and timestamps.
    9. Commit metadata changes to PostgreSQL without duplicating log documents into the DB.
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

    # Locate raw log file
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

    # Verify OpenSearch connectivity
    target_client = client or get_opensearch_client()
    try:
        is_alive = target_client.ping()
    except Exception as exc:
        logger.error("OpenSearch ping failed for job %s: %s", job.id, exc)
        is_alive = False

    if not is_alive:
        job.status = "FAILED"
        job.error_summary = "OpenSearch cluster is currently unavailable."
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSearch cluster is currently unavailable.",
        )

    # Ensure index template is installed
    try:
        ensure_log_index_template(client=target_client)
    except Exception as exc:
        logger.warning("Could not verify or install log index template: %s", exc)

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
        logger.error("Parsing failed during indexing for job %s: %s", job.id, exc, exc_info=True)
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

    # Handle empty input
    if norm_result.total_records == 0:
        completion_time = datetime.now(timezone.utc)
        job.status = "COMPLETED"
        job.total_lines = 0
        job.parsed_lines = 0
        job.error_lines = 0
        job.error_summary = None
        job.completed_at = completion_time
        job.updated_at = completion_time
        await session.commit()
        target_idx = resolve_log_index_name()
        return LogIndexingResponse(
            job_id=job.id,
            status="COMPLETED",
            target_index=target_idx,
            total_records=0,
            indexed_records=0,
            failed_records=0,
            quarantined_records=0,
            message="Log file was empty; zero documents indexed.",
        )

    # Separate valid records for indexing from quarantined invalid records
    valid_records = [r for r in norm_result.records if r.is_valid]
    quarantined_count = len(norm_result.records) - len(valid_records)

    # If all records failed validation
    if not valid_records:
        completion_time = datetime.now(timezone.utc)
        err_msg = f"All {norm_result.total_records} records failed validation and were quarantined."
        job.status = "FAILED"
        job.total_lines = norm_result.total_records
        job.parsed_lines = 0
        job.error_lines = quarantined_count
        job.error_summary = err_msg
        job.completed_at = completion_time
        job.updated_at = completion_time
        await session.commit()
        target_idx = resolve_log_index_name()
        return LogIndexingResponse(
            job_id=job.id,
            status="FAILED",
            target_index=target_idx,
            total_records=norm_result.total_records,
            indexed_records=0,
            failed_records=0,
            quarantined_records=quarantined_count,
            error_summary=err_msg,
            message="Zero records indexed; all records were quarantined due to validation failures.",
        )

    # Build bulk indexing actions with deterministic document IDs
    actions = []
    target_indices = set()
    for record in valid_records:
        idx_name = resolve_log_index_name(record.timestamp)
        target_indices.add(idx_name)
        # Deterministic document ID ensures idempotent re-indexing without duplicate records
        doc_id = f"{job.id}_{record.line_number}"
        doc_source = {
            "timestamp": (
                record.timestamp.isoformat()
                if record.timestamp
                else datetime.now(timezone.utc).isoformat()
            ),
            "service_name": record.service_name,
            "environment": record.environment,
            "log_level": record.log_level,
            "message": record.message,
            "host": record.host,
            "trace_id": record.trace_id,
            "span_id": record.span_id,
            "attributes": {
                "job_id": str(job.id),
                "line_number": record.line_number,
                "source_format": record.source_format,
                **record.attributes,
            },
            "raw_log": record.raw_log,
        }
        actions.append({
            "_op_type": "index",
            "_index": idx_name,
            "_id": doc_id,
            "_source": doc_source,
        })

    target_index_display = (
        ", ".join(sorted(target_indices)) if target_indices else resolve_log_index_name()
    )

    # Execute bulk indexing
    try:
        success_count, failed_items = bulk(
            client=target_client,
            actions=actions,
            stats_only=False,
            raise_on_error=False,
        )
    except Exception as exc:
        logger.error("OpenSearch bulk indexing execution error for job %s: %s", job.id, exc, exc_info=True)
        completion_time = datetime.now(timezone.utc)
        job.status = "FAILED"
        job.total_lines = norm_result.total_records
        job.parsed_lines = 0
        job.error_lines = norm_result.total_records
        job.error_summary = "OpenSearch bulk indexing operation failed."
        job.completed_at = completion_time
        job.updated_at = completion_time
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OpenSearch bulk indexing operation failed.",
        )

    # Calculate final indexing metrics and lifecycle state
    indexing_errors_count = len(failed_items)
    total_error_lines = quarantined_count + indexing_errors_count
    total_records = norm_result.total_records

    if success_count == total_records:
        new_status = "COMPLETED"
    elif success_count > 0 and total_error_lines > 0:
        new_status = "PARTIALLY_FAILED"
    else:
        new_status = "FAILED"

    # Construct clean error summary without sensitive data
    if total_error_lines > 0:
        parts = []
        if quarantined_count > 0:
            parts.append(f"{quarantined_count} records failed validation and were quarantined")
        if indexing_errors_count > 0:
            parts.append(f"{indexing_errors_count} records rejected by OpenSearch")
        error_summary = "; ".join(parts)
    else:
        error_summary = None

    completion_time = datetime.now(timezone.utc)
    job.status = new_status
    job.total_lines = total_records
    job.parsed_lines = success_count
    job.error_lines = total_error_lines
    job.error_summary = error_summary
    job.completed_at = completion_time
    job.updated_at = completion_time
    await session.commit()

    logger.info(
        "Job %s indexed: status=%s, total=%d, indexed=%d, errors=%d, quarantined=%d",
        job.id,
        new_status,
        total_records,
        success_count,
        indexing_errors_count,
        quarantined_count,
    )

    return LogIndexingResponse(
        job_id=job.id,
        status=new_status,
        target_index=target_index_display,
        total_records=total_records,
        indexed_records=success_count,
        failed_records=indexing_errors_count,
        quarantined_records=quarantined_count,
        error_summary=error_summary,
        message=f"Log indexing complete: {success_count} indexed, {total_error_lines} errors.",
    )
