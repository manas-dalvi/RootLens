import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, desc
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.log_source import LogSource


class UploadJob(Base):
    __tablename__ = "upload_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    log_source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("log_sources.id", ondelete="RESTRICT"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    total_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
        Index("idx_upload_jobs_status_created", "status", desc("created_at")),
        Index("idx_upload_jobs_source", "log_source_id"),
    )

    log_source: Mapped["LogSource"] = relationship("LogSource", back_populates="upload_jobs")
