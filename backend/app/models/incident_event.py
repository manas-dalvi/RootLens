import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.evidence import Evidence


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="ANOMALY_SPIKE"
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[Dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
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
        UniqueConstraint("incident_id", "sequence_order", name="uq_incident_event_sequence"),
        Index("idx_incident_events_timeline", "incident_id", "event_timestamp"),
        Index("idx_incident_events_service", "service_name", "event_timestamp"),
    )

    incident: Mapped["Incident"] = relationship("Incident", back_populates="events")
    evidence: Mapped[List["Evidence"]] = relationship(
        "Evidence", back_populates="incident_event"
    )
