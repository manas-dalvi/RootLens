import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, desc
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.incident import Incident


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="DRAFT")
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    timeline_summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_analysis: Mapped[str] = mapped_column(Text, nullable=False)
    recommendations_summary: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty_disclaimer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
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
        UniqueConstraint("incident_id", "title", name="uq_reports_incident_title"),
        Index("idx_reports_incident_generated", "incident_id", desc("generated_at")),
        Index("idx_reports_status", "status"),
    )

    incident: Mapped["Incident"] = relationship("Incident", back_populates="reports")
