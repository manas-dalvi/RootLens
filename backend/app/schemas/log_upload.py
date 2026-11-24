import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class LogUploadResponse(BaseModel):
    """Schema returned immediately upon successful log file upload acceptance."""

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID = Field(..., description="Unique ingestion job identifier")
    status: str = Field(..., description="Initial ingestion status (e.g. PENDING)")
    file_name: str = Field(..., description="Original filename safely recorded")
    file_size_bytes: int = Field(..., description="Total size in bytes")
    format_type: str = Field(..., description="Detected log source format")
    log_source_id: uuid.UUID = Field(..., description="Associated LogSource identifier")
    service_name: str = Field(..., description="Originating service identifier")
    environment: str = Field(..., description="Deployment environment")
    created_at: datetime = Field(..., description="Job registration timestamp")
    message: str = Field(
        default="Log file uploaded and queued for processing.",
        description="Human-readable status summary",
    )


class UploadJobDetailResponse(BaseModel):
    """Detailed metadata and progress tracking schema for an upload job."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique ingestion job identifier")
    log_source_id: uuid.UUID = Field(..., description="Associated LogSource identifier")
    service_name: str = Field(..., description="Associated service name")
    environment: str = Field(..., description="Associated environment")
    file_name: str = Field(..., description="Stored original filename")
    file_size_bytes: int = Field(..., description="File size in bytes")
    status: str = Field(..., description="Ingestion lifecycle status")
    total_lines: int = Field(..., description="Total lines parsed")
    parsed_lines: int = Field(..., description="Successfully indexed lines")
    error_lines: int = Field(..., description="Quarantined or dropped lines")
    started_at: Optional[datetime] = Field(None, description="Processing start timestamp")
    completed_at: Optional[datetime] = Field(None, description="Processing finish timestamp")
    error_summary: Optional[str] = Field(None, description="Summary of errors if failed")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class ParsedRecordItem(BaseModel):
    """Schema for an individual parsed record item."""

    line_number: int = Field(..., description="1-indexed source line or entry position")
    raw: str = Field(..., description="Original raw line or serialized record")
    extracted: dict = Field(default_factory=dict, description="Extracted key-value attributes")
    is_valid: bool = Field(..., description="Whether record adhered to format structure")
    error: Optional[str] = Field(None, description="Error message if malformed")


class ParsingResultResponse(BaseModel):
    """Schema returned after executing format-specific log parsing on an upload job."""

    job_id: uuid.UUID = Field(..., description="Upload job identifier")
    status: str = Field(
        ...,
        description="Updated lifecycle status (COMPLETED, PARTIALLY_FAILED, FAILED)",
    )
    format_type: str = Field(..., description="Applied parser format")
    total_records: int = Field(..., description="Total records parsed")
    successful_records: int = Field(..., description="Successfully parsed records")
    failed_records: int = Field(..., description="Failed or malformed records")
    error_summary: Optional[str] = Field(None, description="Summary of encountered errors")
    records: list[ParsedRecordItem] = Field(
        default_factory=list, description="Parsed record instances"
    )
