from datetime import datetime
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class TimeBucketResponse(BaseModel):
    """Schema for an individual timestamped bucket within a time-series aggregation."""

    timestamp: datetime = Field(..., description="Bucket start timestamp in UTC (ISO-8601)")
    doc_count: int = Field(..., description="Total log records falling within this time bucket")
    severities: Dict[str, int] = Field(
        default_factory=dict,
        description="Distribution of canonical severity levels (e.g. {'INFO': 10, 'ERROR': 2})",
    )


class LogTimeSeriesResponse(BaseModel):
    """Schema for time-bucketed log aggregation response."""

    interval: str = Field(..., description="Time bucket interval (e.g. '1m', '2m', '5m', '15m', '1h')")
    start_time: Optional[datetime] = Field(None, description="Start timestamp boundary in UTC")
    end_time: Optional[datetime] = Field(None, description="End timestamp boundary in UTC")
    total_logs: int = Field(..., description="Total log records matched across all buckets")
    buckets: List[TimeBucketResponse] = Field(
        default_factory=list,
        description="Chronological sequence of time buckets",
    )


class EventEvidence(BaseModel):
    """Concrete evidence linking a detected significant event to underlying log records."""

    log_ids: List[str] = Field(
        default_factory=list,
        description="Representative OpenSearch log document IDs as concrete evidence",
    )
    sample_messages: List[str] = Field(
        default_factory=list,
        description="Sample unmanipulated log message snippets from the detected event",
    )


class SignificantEventResponse(BaseModel):
    """Schema for a deterministically detected significant log event."""

    event_id: str = Field(..., description="Deterministic unique identifier for this event")
    rule_name: str = Field(..., description="Name of the deterministic rule that triggered this event")
    severity: str = Field(..., description="Event severity rating ('CRITICAL', 'HIGH', 'MEDIUM')")
    service_name: str = Field(..., description="Service identifier associated with this event")
    count: int = Field(..., description="Number of matching log occurrences in this window")
    window_start: datetime = Field(..., description="Start timestamp of the bounded detection window (UTC)")
    window_end: datetime = Field(..., description="End timestamp of the bounded detection window (UTC)")
    description: str = Field(..., description="Human-readable explanation of why this event was flagged")
    evidence: EventEvidence = Field(..., description="Verifiable evidence references")


class SignificantEventsResponse(BaseModel):
    """Schema for list of detected significant events."""

    total_events: int = Field(..., description="Total count of distinct detected events")
    events: List[SignificantEventResponse] = Field(
        default_factory=list,
        description="List of detected significant events",
    )
