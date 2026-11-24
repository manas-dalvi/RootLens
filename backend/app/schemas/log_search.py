from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LogDocumentResponse(BaseModel):
    """Schema for a single normalized log document retrieved from OpenSearch."""

    id: str = Field(..., description="Unique document ID in OpenSearch")
    index: Optional[str] = Field(None, description="Target OpenSearch index name")
    timestamp: Optional[datetime] = Field(None, description="Log event timestamp in UTC")
    service_name: Optional[str] = Field(None, description="Originating service identifier")
    environment: Optional[str] = Field(None, description="Deployment environment")
    log_level: Optional[str] = Field(None, description="Canonical severity level")
    message: Optional[str] = Field(None, description="Log message body")
    host: Optional[str] = Field(None, description="Host or node identifier")
    trace_id: Optional[str] = Field(None, description="Distributed tracing trace ID")
    span_id: Optional[str] = Field(None, description="Distributed tracing span ID")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Structured attributes and metadata")
    raw_log: Optional[str] = Field(None, description="Original unparsed log record")


class LogSearchResponse(BaseModel):
    """Schema for paginated log search results."""

    items: List[LogDocumentResponse] = Field(
        default_factory=list,
        description="List of matching log documents for current page",
    )
    page: int = Field(..., description="Current 1-based page number")
    page_size: int = Field(..., description="Page size limit")
    total: int = Field(..., description="Total number of matching log documents")
    total_pages: int = Field(..., description="Total number of pages")
