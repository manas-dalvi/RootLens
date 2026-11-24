import uuid
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class LogIndexingResponse(BaseModel):
    """Schema returned after executing OpenSearch log indexing on an upload job."""

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID = Field(..., description="Unique upload job identifier")
    status: str = Field(
        ...,
        description="Updated lifecycle status (COMPLETED, PARTIALLY_FAILED, FAILED)",
    )
    target_index: str = Field(..., description="Target OpenSearch index name or pattern")
    total_records: int = Field(..., description="Total log records processed")
    indexed_records: int = Field(..., description="Successfully indexed document count")
    failed_records: int = Field(..., description="Failed or rejected document count in OpenSearch")
    quarantined_records: int = Field(
        ..., description="Records skipped due to parser/validation failures"
    )
    error_summary: Optional[str] = Field(None, description="Summary of errors if any occurred")
    message: str = Field(
        default="Log indexing complete.", description="Human-readable status summary"
    )
