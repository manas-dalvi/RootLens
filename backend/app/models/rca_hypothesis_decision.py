import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, Uuid, desc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

if TYPE_CHECKING:
    from app.models.incident import Incident


class RCAHypothesisDecision(Base):
    """Immutable audit record of an operator decision (CONFIRM/REJECT) on an RCA hypothesis."""

    __tablename__ = "rca_hypothesis_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    hypothesis_id: Mapped[str] = mapped_column(String(255), nullable=False)
    hypothesis_text: Mapped[str] = mapped_column(Text, nullable=False)
    operator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operator_email: Mapped[str] = mapped_column(String(255), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # "CONFIRM" | "REJECT"
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_citations: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    previous_decision: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_rca_decisions_incident_decided", "incident_id", desc("decided_at")),
        Index("idx_rca_decisions_incident_hyp", "incident_id", "hypothesis_id"),
    )

    incident: Mapped["Incident"] = relationship("Incident", back_populates="hypothesis_decisions")
