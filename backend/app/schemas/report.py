"""Pydantic schemas for RootLens post-mortem report generation and retrieval.

Strict invariants:
- Reports start in DRAFT status.
- is_root_cause_confirmed is strictly False.
- No invented confidence percentages or speculative causal claims.
"""

from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


def assemble_report_markdown(report: Any) -> str:
    """Deterministically assemble full printable Markdown document from report sections."""
    lines = [
        f"# {report.title}",
        "",
        f"**Report Status:** {report.status}  ",
        f"**Incident ID:** {report.incident_id}  ",
        f"**Report ID:** {report.id}  ",
        f"**Generated At:** {report.generated_at.isoformat() if hasattr(report.generated_at, 'isoformat') else report.generated_at}  ",
        "**Root Cause Confirmed:** False (Correlation is not confirmed causation)  ",
        "**Diagnostic Confidence Score:** 0.0  ",
        "",
        "---",
        "",
        "## 1. Executive Summary",
        str(report.executive_summary or "").strip(),
        "",
        "---",
        "",
        "## 2. Chronological Timeline",
        str(report.timeline_summary or "").strip(),
        "",
        "---",
        "",
        "## 3. Root Cause Analysis & Diagnostic Findings",
        str(report.root_cause_analysis or "").strip(),
        "",
        "---",
        "",
        "## 4. Advisory Recommendations & Verification Steps",
        str(report.recommendations_summary or "").strip(),
        "",
        "---",
        "",
        "## 5. Diagnostic Uncertainty & Telemetry Bounds",
        str(report.uncertainty_disclaimer or "No explicit uncertainty recorded.").strip(),
        "",
    ]
    return "\n".join(lines)


class ReportCreateRequest(BaseModel):
    """Payload to trigger evidence-grounded post-mortem report generation."""

    model_config = ConfigDict(from_attributes=True)

    title: Optional[str] = Field(
        None,
        max_length=255,
        description="Optional custom report title. Defaults to 'Post-Mortem: <incident.title>'.",
    )

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v_str = str(v).strip()
            return v_str if v_str else None
        return None


class ReportResponse(BaseModel):
    """Structured response representation of an incident post-mortem report."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique report UUID")
    incident_id: str = Field(..., description="Associated incident UUID")
    title: str = Field(..., description="Report title")
    status: str = Field("DRAFT", description="Report lifecycle status (strictly DRAFT upon generation)")
    executive_summary: str = Field(..., description="High-level incident and blast-radius summary")
    timeline_summary: str = Field(..., description="Chronological milestone timeline narrative")
    root_cause_analysis: str = Field(..., description="Evidence-backed RCA findings and qualified hypotheses")
    recommendations_summary: str = Field(..., description="Advisory diagnostic and verification steps")
    uncertainty_disclaimer: Optional[str] = Field(None, description="Explicit uncertainty, gaps, and budget notices")
    generated_at: datetime = Field(..., description="Report generation timestamp in UTC")
    created_at: datetime = Field(..., description="Record creation timestamp in UTC")
    updated_at: datetime = Field(..., description="Record update timestamp in UTC")
    is_root_cause_confirmed: bool = Field(False, description="Strictly False: correlation does not establish causation")
    markdown_content: str = Field(..., description="Full assembled, printable Markdown document")

    @classmethod
    def from_orm_report(cls, report: Any) -> "ReportResponse":
        """Construct typed ReportResponse from an ORM Report model instance."""
        return cls(
            id=str(report.id),
            incident_id=str(report.incident_id),
            title=str(report.title),
            status=str(report.status),
            executive_summary=str(report.executive_summary or ""),
            timeline_summary=str(report.timeline_summary or ""),
            root_cause_analysis=str(report.root_cause_analysis or ""),
            recommendations_summary=str(report.recommendations_summary or ""),
            uncertainty_disclaimer=report.uncertainty_disclaimer,
            generated_at=report.generated_at,
            created_at=report.created_at,
            updated_at=report.updated_at,
            is_root_cause_confirmed=False,
            markdown_content=assemble_report_markdown(report),
        )


class ReportListResponse(BaseModel):
    """Collection response of post-mortem reports associated with an incident."""

    model_config = ConfigDict(from_attributes=True)

    items: List[ReportResponse] = Field(
        default_factory=list, description="List of generated reports for this incident"
    )
    total: int = Field(0, description="Total number of reports for this incident")
    incident_id: str = Field(..., description="Target incident UUID")
