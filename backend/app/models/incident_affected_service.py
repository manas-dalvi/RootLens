import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.log_source import LogSource


class IncidentAffectedService(Base):
    __tablename__ = "incident_affected_services"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    log_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("log_sources.id", ondelete="RESTRICT"), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    impact_level: Mapped[str] = mapped_column(
        String(30), nullable=False, default="DOWNSTREAM_CASCADE"
    )
    first_impacted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

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
        UniqueConstraint("incident_id", "log_source_id", name="uq_incident_affected_services"),
        Index("idx_affected_services_incident", "incident_id"),
        Index("idx_affected_services_source", "log_source_id"),
        Index("idx_affected_services_name", "service_name"),
    )

    incident: Mapped["Incident"] = relationship("Incident", back_populates="affected_services")
    log_source: Mapped["LogSource"] = relationship("LogSource", back_populates="affected_incidents")
