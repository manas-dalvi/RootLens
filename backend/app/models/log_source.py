import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.upload_job import UploadJob
    from app.models.incident import Incident
    from app.models.incident_affected_service import IncidentAffectedService


class LogSource(Base):
    __tablename__ = "log_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    environment: Mapped[str] = mapped_column(
        String(50), nullable=False, default="production"
    )
    format_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="json"
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
        UniqueConstraint("name", "environment", name="uq_log_sources_name_env"),
        Index("idx_log_sources_service_env", "service_name", "environment"),
    )

    upload_jobs: Mapped[List["UploadJob"]] = relationship(
        "UploadJob", back_populates="log_source"
    )
    incidents: Mapped[List["Incident"]] = relationship(
        "Incident", back_populates="primary_log_source"
    )
    affected_incidents: Mapped[List["IncidentAffectedService"]] = relationship(
        "IncidentAffectedService", back_populates="log_source"
    )
