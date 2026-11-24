import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class NormalizedLogItem(BaseModel):
    """Schema for an individual normalized and validated log document item."""

    model_config = ConfigDict(from_attributes=True)

    line_number: int = Field(..., description="1-indexed source line or entry position")
    timestamp: Optional[datetime] = Field(None, description="Normalized UTC timestamp")
    service_name: str = Field(..., description="Canonical service identifier")
    environment: str = Field(..., description="Target deployment environment")
    log_level: Optional[str] = Field(None, description="Canonical severity level")
    message: str = Field(..., description="Extracted log message payload")
    host: Optional[str] = Field(None, description="Originating host or node identifier")
    trace_id: Optional[str] = Field(None, description="Distributed correlation trace ID")
    span_id: Optional[str] = Field(None, description="Distributed span ID")
    attributes: Dict[str, Any] = Field(
        default_factory=dict, description="Structured application metadata"
    )
    raw_log: str = Field(..., description="Original raw line or event text")
    source_format: str = Field(..., description="Detected source format (csv, json, syslog, etc.)")
    is_valid: bool = Field(..., description="Whether record adhered to structure and validation")
    validation_errors: List[str] = Field(
        default_factory=list, description="Validation issues and warnings"
    )
    raw_timestamp: Optional[str] = Field(None, description="Original unparsed timestamp string")
    raw_level: Optional[str] = Field(None, description="Original unparsed level or code")
    original_fields: Dict[str, Any] = Field(
        default_factory=dict, description="Original key-value fields prior to normalization"
    )


class NormalizationResponse(BaseModel):
    """Schema returned after executing log normalization and validation on an upload job."""

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID = Field(..., description="Upload job identifier")
    status: str = Field(
        ...,
        description="Updated lifecycle status (COMPLETED, PARTIALLY_FAILED, FAILED)",
    )
    format_type: str = Field(..., description="Source log format applied")
    total_records: int = Field(..., description="Total records evaluated")
    valid_records: int = Field(..., description="Successfully normalized and validated records")
    invalid_records: int = Field(..., description="Malformed or invalid records")
    summary: Optional[str] = Field(None, description="Overall summary of normalization results")
    records: List[NormalizedLogItem] = Field(
        default_factory=list, description="Collection of normalized log records"
    )
