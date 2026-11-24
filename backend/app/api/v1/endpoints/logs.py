from datetime import datetime
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.log_aggregations import (
    LogTimeSeriesResponse,
    SignificantEventsResponse,
)
from app.schemas.log_anomaly import (
    LogBERTBatchInferenceRequest,
    LogBERTBatchInferenceResponse,
    LogBERTInferenceRequest,
    LogBERTInferenceResponse,
    LogBERTModelInfoResponse,
)
from app.schemas.log_indexing import LogIndexingResponse
from app.schemas.log_normalization import NormalizationResponse
from app.schemas.log_search import LogDocumentResponse, LogSearchResponse
from app.schemas.log_upload import (
    LogUploadResponse,
    ParsingResultResponse,
    UploadJobDetailResponse,
)
from app.services.aggregation_service import (
    detect_significant_events,
    get_log_time_series,
)
from app.services.indexing_service import index_upload_job
from app.services.logbert_service import get_logbert_engine
from app.services.normalization_service import normalize_upload_job
from app.services.parsing_service import parse_upload_job
from app.services.search_service import get_log_by_id, search_logs
from app.services.upload_service import get_upload_job_detail, process_log_upload

router = APIRouter()


@router.post(
    "/upload",
    response_model=LogUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and ingest log file",
    description="Accepts a log file (.csv, .json, .log, .txt, .syslog) and registers an ingestion job.",
)
async def upload_log_file(
    file: UploadFile = File(..., description="Log file to upload"),
    service_name: Optional[str] = Form(None, description="Originating service identifier"),
    environment: Optional[str] = Form(None, description="Target environment (e.g. production)"),
    source_name: Optional[str] = Form(None, description="Optional custom log source name"),
    log_source_id: Optional[uuid.UUID] = Form(None, description="Optional existing log source ID"),
    session: AsyncSession = Depends(get_db),
) -> LogUploadResponse:
    """Accept and validate a log file upload, registering an ingestion job."""
    return await process_log_upload(
        file=file,
        session=session,
        service_name=service_name,
        environment=environment,
        source_name=source_name,
        log_source_id=log_source_id,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=UploadJobDetailResponse,
    summary="Get upload job metadata and status",
    description="Retrieve processing status, line metrics, and metadata for a specific upload job.",
)
async def get_upload_job_status(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> UploadJobDetailResponse:
    """Retrieve current metadata and status for an upload job."""
    return await get_upload_job_detail(job_id=job_id, session=session)


@router.post(
    "/jobs/{job_id}/parse",
    response_model=ParsingResultResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger format-specific parsing on uploaded log job",
    description="Parses the uploaded file according to its validated format, updating line counts and lifecycle status.",
)
async def parse_log_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> ParsingResultResponse:
    """Execute format-specific parsing for an uploaded log job."""
    return await parse_upload_job(job_id=job_id, session=session)


@router.post(
    "/jobs/{job_id}/normalize",
    response_model=NormalizationResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger log normalization and validation on uploaded log job",
    description="Normalizes parsed records into the canonical RootLens schema, validating fields, timestamps, and severity levels.",
)
async def normalize_log_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> NormalizationResponse:
    """Execute log normalization and validation for an uploaded log job."""
    return await normalize_upload_job(job_id=job_id, session=session)


@router.post(
    "/jobs/{job_id}/index",
    response_model=LogIndexingResponse,
    status_code=status.HTTP_200_OK,
    summary="Index normalized logs into OpenSearch",
    description="Parses, normalizes, validates, and bulk indexes log documents into OpenSearch with deterministic IDs.",
)
async def index_log_job(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> LogIndexingResponse:
    """Execute OpenSearch bulk indexing for an uploaded log job."""
    return await index_upload_job(job_id=job_id, session=session)


@router.get(
    "",
    response_model=LogSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Search indexed logs",
    description="Search and filter indexed log documents across time-partitioned OpenSearch indices.",
)
@router.get(
    "/",
    response_model=LogSearchResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def search_log_documents(
    q: Optional[str] = Query(None, description="Free-text search query across log message, service, and host"),
    start_time: Optional[datetime] = Query(None, description="Start timestamp filter (ISO-8601)"),
    end_time: Optional[datetime] = Query(None, description="End timestamp filter (ISO-8601)"),
    service: Optional[str] = Query(None, description="Filter by service name"),
    severity: Optional[str] = Query(None, description="Filter by canonical log level (e.g. INFO, ERROR)"),
    level: Optional[str] = Query(None, description="Alias for severity filter"),
    source: Optional[str] = Query(None, description="Filter by source format or job ID"),
    user_id: Optional[str] = Query(None, description="Filter by user ID in structured attributes"),
    api: Optional[str] = Query(None, description="Filter by API/endpoint in structured attributes"),
    errors_only: bool = Query(False, description="Filter only error and fatal logs (ERROR, FATAL, CRITICAL)"),
    page: int = Query(1, description="One-based page number"),
    page_size: int = Query(50, description="Page size limit (max 100)"),
    sort_order: str = Query("desc", description="Sort order by timestamp ('asc' or 'desc')"),
) -> LogSearchResponse:
    """Execute parameterized search across indexed log documents in OpenSearch."""
    return search_logs(
        q=q,
        start_time=start_time,
        end_time=end_time,
        service=service,
        severity=severity,
        level=level,
        source=source,
        user_id=user_id,
        api=api,
        errors_only=errors_only,
        page=page,
        page_size=page_size,
        sort_order=sort_order,
    )


@router.get(
    "/aggregations",
    response_model=LogTimeSeriesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get time-bucketed log volume and severity aggregations",
    description="Calculates time-series histograms with per-severity distribution using OpenSearch date_histogram.",
)
def get_log_aggregations(
    interval: str = Query("1h", description="Time bucket interval (e.g. '1m', '2m', '5m', '15m', '1h', '1d')"),
    start_time: Optional[datetime] = Query(None, description="Start timestamp filter (ISO-8601)"),
    end_time: Optional[datetime] = Query(None, description="End timestamp filter (ISO-8601)"),
    service: Optional[str] = Query(None, description="Filter aggregation by service name"),
    environment: Optional[str] = Query(None, description="Filter aggregation by environment"),
) -> LogTimeSeriesResponse:
    """Execute time-bucketed log aggregation across OpenSearch monthly partitions."""
    return get_log_time_series(
        interval=interval,
        start_time=start_time,
        end_time=end_time,
        service=service,
        environment=environment,
    )


@router.get(
    "/events",
    response_model=SignificantEventsResponse,
    status_code=status.HTTP_200_OK,
    summary="Detect significant log events and error bursts",
    description="Detects deterministic significant error and fatal events grouped by service in bounded time windows.",
)
def get_significant_events(
    start_time: Optional[datetime] = Query(None, description="Start timestamp filter (ISO-8601)"),
    end_time: Optional[datetime] = Query(None, description="End timestamp filter (ISO-8601)"),
    service: Optional[str] = Query(None, description="Filter events by service name"),
    error_threshold: Optional[int] = Query(None, description="Minimum error count in window to trigger an event"),
    window_minutes: Optional[int] = Query(None, description="Detection window width in minutes (default: 15)"),
) -> SignificantEventsResponse:
    """Detect significant log events and extract concrete evidence references."""
    return detect_significant_events(
        start_time=start_time,
        end_time=end_time,
        service=service,
        error_threshold=error_threshold,
        window_minutes=window_minutes,
    )


@router.post(
    "/anomalies/detect",
    response_model=LogBERTInferenceResponse,
    status_code=status.HTTP_200_OK,
    summary="Detect sequence anomalies using LogBERT",
    description="Evaluates a sequence of log events using the self-supervised LogBERT model.",
)
def detect_sequence_anomaly_endpoint(
    req: LogBERTInferenceRequest,
) -> LogBERTInferenceResponse:
    """Evaluate a single sequence of log events for anomalies."""
    engine = get_logbert_engine()
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LogBERT inference engine is currently unavailable or disabled.",
        )
    result = engine.predict_sequence(
        events=req.events,
        session_id=req.session_id,
    )
    return LogBERTInferenceResponse(**result.to_dict())


@router.post(
    "/anomalies/batch-detect",
    response_model=LogBERTBatchInferenceResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch sequence anomaly detection using LogBERT",
    description="Evaluates multiple log event sequences using the self-supervised LogBERT model.",
)
def detect_batch_sequence_anomalies_endpoint(
    req: LogBERTBatchInferenceRequest,
) -> LogBERTBatchInferenceResponse:
    """Evaluate multiple log event sequences for anomalies in batch."""
    engine = get_logbert_engine()
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LogBERT inference engine is currently unavailable or disabled.",
        )
    sequences = [item.events for item in req.sequences]
    session_ids = [item.session_id for item in req.sequences]
    results = engine.predict_batch(sequences=sequences, session_ids=session_ids)
    anom_count = sum(1 for r in results if r.is_anomaly)
    return LogBERTBatchInferenceResponse(
        total_evaluated=len(results),
        total_anomalies=anom_count,
        results=[LogBERTInferenceResponse(**r.to_dict()) for r in results],
    )


@router.get(
    "/anomalies/model-info",
    response_model=LogBERTModelInfoResponse,
    status_code=status.HTTP_200_OK,
    summary="Get LogBERT model status and metadata",
    description="Retrieve non-sensitive architecture and runtime metadata for the LogBERT model.",
)
def get_logbert_model_info_endpoint() -> LogBERTModelInfoResponse:
    """Retrieve operational status, architecture parameters, and active decision threshold."""
    engine = get_logbert_engine()
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LogBERT inference engine is currently unavailable or disabled.",
        )
    info = engine.get_model_info()
    return LogBERTModelInfoResponse(**info)


@router.get(
    "/{id}",
    response_model=LogDocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get single indexed log document",
    description="Retrieve a single indexed log record by its unique document ID across OpenSearch indices.",
)
def get_single_log_document(
    id: str,
) -> LogDocumentResponse:
    """Retrieve an individual indexed log document by its unique document ID."""
    return get_log_by_id(log_id=id)



