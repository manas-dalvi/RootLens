import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional
from sqlalchemy import DateTime, Float, ForeignKey, Index, JSON, String, Text, Uuid, desc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.incident_event import IncidentEvent


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    incident_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incident_events.id", ondelete="SET NULL"), nullable=True
    )
    evidence_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="CULPRIT_LOG"
    )
    opensearch_index: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    opensearch_doc_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    log_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    service_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    log_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    log_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    document_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="INDEXED"
    )
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[Dict[str, Any]] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
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
        Index("idx_evidence_incident_relevance", "incident_id", desc("relevance_score")),
        Index("idx_evidence_os_pointer", "opensearch_index", "opensearch_doc_id"),
    )

    incident: Mapped["Incident"] = relationship("Incident", back_populates="evidence")
    incident_event: Mapped[Optional["IncidentEvent"]] = relationship(
        "IncidentEvent", back_populates="evidence"
    )
