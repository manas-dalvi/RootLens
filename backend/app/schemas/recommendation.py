"""Pydantic schemas for RootLens advisory troubleshooting recommendations.

Strict Invariants:
- Recommendations are strictly advisory: zero autonomous remediation.
- IMMEDIATE_ACTION is non-destructive (no restarts, rollbacks, or config changes).
- is_root_cause_confirmed is strictly False across all recommendations and classifications.
- TRACE_LINKED never implies confirmed root cause.
- Recommendations adhere to classification-scoped action-type allow-lists.
"""

from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class RecommendationGenerateRequest(BaseModel):
    """Payload to control troubleshooting recommendation generation."""

    model_config = ConfigDict(from_attributes=True)

    force_regenerate: bool = Field(
        False,
        description="If True, replaces existing recommendations with a freshly generated set.",
    )


class EvidenceCitation(BaseModel):
    """Structured evidence provenance citation."""

    model_config = ConfigDict(from_attributes=True)

    reference_type: str = Field(..., description="Citation namespace: 'LOG', 'TRACE', or 'SPAN'")
    reference_id: str = Field(..., description="Target document ID, trace ID, or span ID")
    service_name: str = Field(..., description="Associated service name")


class RecommendationResponse(BaseModel):
    """Structured representation of an advisory troubleshooting recommendation."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique recommendation UUID")
    incident_id: str = Field(..., description="Associated incident UUID")
    action_type: str = Field(
        ...,
        description="Classification-scoped action type ('TELEMETRY_GAP', 'DIAGNOSTIC_VERIFICATION', 'MITIGATION_ADVISORY', 'IMMEDIATE_ACTION')",
    )
    title: str = Field(..., max_length=255, description="Recommendation title")
    description: str = Field(..., description="Grounded diagnostic rationale and guidance")
    priority: str = Field("MEDIUM", description="Priority tier ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')")
    sequence_order: int = Field(0, description="0-based sequence execution order")
    verification_step: Optional[str] = Field(None, description="Concrete manual verification step")
    caveat: Optional[str] = Field(None, description="Explicit uncertainty, boundary notice, or correlation caveat")
    evidence_citations: List[EvidenceCitation] = Field(
        default_factory=list,
        description="Structured citations linking recommendation to verified incident telemetry",
    )
    created_at: datetime = Field(..., description="Creation timestamp in UTC")
    updated_at: datetime = Field(..., description="Update timestamp in UTC")
    is_advisory_only: bool = Field(True, description="Strictly True: zero autonomous remediation")
    is_root_cause_confirmed: bool = Field(False, description="Strictly False: correlation does not establish causation")

    @classmethod
    def from_orm_recommendation(cls, rec: Any) -> "RecommendationResponse":
        """Construct typed RecommendationResponse from ORM Recommendation model instance."""
        citations_raw = getattr(rec, "evidence_citations", None) or []
        citations: List[EvidenceCitation] = []
        for c in citations_raw:
            if isinstance(c, dict):
                citations.append(EvidenceCitation(**c))
            elif isinstance(c, EvidenceCitation):
                citations.append(c)

        return cls(
            id=str(rec.id),
            incident_id=str(rec.incident_id),
            action_type=str(rec.action_type),
            title=str(rec.title),
            description=str(rec.description),
            priority=str(rec.priority),
            sequence_order=int(rec.sequence_order),
            verification_step=rec.verification_step,
            caveat=rec.caveat,
            evidence_citations=citations,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
            is_advisory_only=True,
            is_root_cause_confirmed=False,
        )


class RecommendationListResponse(BaseModel):
    """Collection response of troubleshooting recommendations for an incident."""

    model_config = ConfigDict(from_attributes=True)

    items: List[RecommendationResponse] = Field(
        default_factory=list, description="List of generated recommendations"
    )
    total: int = Field(0, description="Total count of recommendations")
    incident_id: str = Field(..., description="Target incident UUID")
    telemetry_classification: str = Field(
        ..., description="Telemetry classification: 'TRACE_LINKED', 'TEMPORAL_ONLY', 'INSUFFICIENT'"
    )
    is_advisory_only: bool = Field(True, description="Strictly True: zero autonomous remediation")
    is_root_cause_confirmed: bool = Field(False, description="Strictly False: correlation does not establish causation")
