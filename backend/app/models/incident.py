import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.log_source import LogSource
    from app.models.incident_affected_service import IncidentAffectedService
    from app.models.incident_event import IncidentEvent
    from app.models.evidence import Evidence
    from app.models.recommendation import Recommendation
    from app.models.report import Report
    from app.models.rca_hypothesis_decision import RCAHypothesisDecision


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="DETECTED")
    analysis_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING"
    )
    primary_log_source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("log_sources.id", ondelete="SET NULL"), nullable=True
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    probable_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_root_cause_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    uncertainty_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_incidents_status_severity", "status", "severity"),
        Index("idx_incidents_timeline", "started_at", "ended_at"),
        Index("idx_incidents_analysis_status", "analysis_status"),
    )

    primary_log_source: Mapped[Optional["LogSource"]] = relationship(
        "LogSource", back_populates="incidents"
    )
    affected_services: Mapped[List["IncidentAffectedService"]] = relationship(
        "IncidentAffectedService", back_populates="incident", cascade="all, delete-orphan"
    )
    events: Mapped[List["IncidentEvent"]] = relationship(
        "IncidentEvent",
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="IncidentEvent.sequence_order",
    )
    evidence: Mapped[List["Evidence"]] = relationship(
        "Evidence", back_populates="incident", cascade="all, delete-orphan"
    )
    recommendations: Mapped[List["Recommendation"]] = relationship(
        "Recommendation",
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="Recommendation.sequence_order",
    )
    reports: Mapped[List["Report"]] = relationship(
        "Report", back_populates="incident", cascade="all, delete-orphan"
    )
    hypothesis_decisions: Mapped[List["RCAHypothesisDecision"]] = relationship(
        "RCAHypothesisDecision",
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="desc(RCAHypothesisDecision.decided_at)",
    )
