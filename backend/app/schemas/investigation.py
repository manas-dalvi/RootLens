"""Pydantic schemas for RootLens AI explanation and natural-language investigation.

Strict Invariants:
- is_root_cause_confirmed is strictly False across all responses.
- is_grounded is explicitly assigned by the service only after grounding verification passes.
- Citations reuse the structured EvidenceCitation schema and isolated namespaces.
"""

from typing import List
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.recommendation import EvidenceCitation


class IncidentExplanationResponse(BaseModel):
    """Structured read-only diagnostic explanation for an incident."""

    model_config = ConfigDict(from_attributes=True)

    incident_id: str = Field(..., description="Unique incident UUID")
    classification: str = Field(
        ...,
        description="Deterministic sufficiency classification ('TRACE_LINKED', 'TEMPORAL_ONLY', 'INSUFFICIENT')",
    )
    explanation: str = Field(..., description="Grounded diagnostic narrative")
    is_root_cause_confirmed: bool = Field(
        False, description="Strictly False: correlation does not establish causation"
    )
    evidence_references: List[EvidenceCitation] = Field(
        default_factory=list, description="Verifiable telemetry evidence citations"
    )
    missing_telemetry: List[str] = Field(
        default_factory=list, description="Missing telemetry signals or gaps"
    )
    uncertainty_statement: str = Field(
        ..., description="Explicit diagnostic uncertainty statement"
    )
    omitted_timeline_events_count: int = Field(
        0, description="Count of timeline events omitted due to prompt context bounds"
    )
    omitted_evidence_records_count: int = Field(
        0, description="Count of evidence records omitted due to prompt context bounds"
    )
    model_used: str = Field(..., description="Model identifier used for synthesis")
    provider_used: str = Field(
        ..., description="Provider identifier ('deterministic', 'offline', 'generic_http')"
    )


class IncidentInvestigationRequest(BaseModel):
    """Natural-language investigation query payload."""

    model_config = ConfigDict(from_attributes=True)

    query: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        description="Natural-language question regarding incident timeline, evidence, services, or propagation.",
    )

    @field_validator("query", mode="before")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        if v is None:
            raise ValueError("Query cannot be None.")
        clean = str(v).strip()
        if len(clean) < 3:
            raise ValueError("Query must be at least 3 characters long after trimming.")
        return clean


class IncidentInvestigationResponse(BaseModel):
    """Grounded diagnostic response to a natural-language investigation inquiry."""

    model_config = ConfigDict(from_attributes=True)

    incident_id: str = Field(..., description="Associated incident UUID")
    query: str = Field(..., description="Original user inquiry")
    answer: str = Field(..., description="Grounded diagnostic answer")
    classification: str = Field(
        ...,
        description="Telemetry classification ('TRACE_LINKED', 'TEMPORAL_ONLY', 'INSUFFICIENT')",
    )
    is_root_cause_confirmed: bool = Field(
        False, description="Strictly False: correlation does not establish causation"
    )
    cited_references: List[EvidenceCitation] = Field(
        default_factory=list, description="Strictly verified evidence citations"
    )
    unobserved_entities_mentioned: List[str] = Field(
        default_factory=list,
        description="Entities queried by user that have no telemetry in the incident evidence bundle",
    )
    is_grounded: bool = Field(
        ...,
        description="Explicitly set to True only after grounding validation passes; never trusted from model output",
    )
    model_used: str = Field(..., description="Model identifier used for response")
    provider_used: str = Field(
        ..., description="Provider identifier ('deterministic', 'offline', 'generic_http')"
    )
