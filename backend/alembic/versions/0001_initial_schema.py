"""Initial RootLens database schema.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-10-09 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. log_sources
    op.create_table(
        "log_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("service_name", sa.String(length=100), nullable=False),
        sa.Column("environment", sa.String(length=50), server_default="production", nullable=False),
        sa.Column("format_type", sa.String(length=50), server_default="json", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "environment", name="uq_log_sources_name_env"),
    )
    op.create_index(
        "idx_log_sources_service_env", "log_sources", ["service_name", "environment"], unique=False
    )

    # 2. upload_jobs
    op.create_table(
        "upload_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("log_source_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="PENDING", nullable=False),
        sa.Column("total_lines", sa.Integer(), server_default="0", nullable=False),
        sa.Column("parsed_lines", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_lines", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["log_source_id"], ["log_sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_upload_jobs_status_created",
        "upload_jobs",
        ["status", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index("idx_upload_jobs_source", "upload_jobs", ["log_source_id"], unique=False)

    # 3. incidents
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=20), server_default="MEDIUM", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="DETECTED", nullable=False),
        sa.Column("analysis_status", sa.String(length=30), server_default="PENDING", nullable=False),
        sa.Column("primary_log_source_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("probable_cause", sa.Text(), nullable=True),
        sa.Column("confidence_score", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("is_root_cause_confirmed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("uncertainty_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["primary_log_source_id"], ["log_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_incidents_status_severity", "incidents", ["status", "severity"], unique=False
    )
    op.create_index(
        "idx_incidents_timeline", "incidents", ["started_at", "ended_at"], unique=False
    )
    op.create_index(
        "idx_incidents_analysis_status", "incidents", ["analysis_status"], unique=False
    )

    # 4. incident_affected_services
    op.create_table(
        "incident_affected_services",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("log_source_id", sa.Uuid(), nullable=False),
        sa.Column("service_name", sa.String(length=100), nullable=False),
        sa.Column("impact_level", sa.String(length=30), server_default="DOWNSTREAM_CASCADE", nullable=False),
        sa.Column("first_impacted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["log_source_id"], ["log_sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "log_source_id", name="uq_incident_affected_services"),
    )
    op.create_index(
        "idx_affected_services_incident", "incident_affected_services", ["incident_id"], unique=False
    )
    op.create_index(
        "idx_affected_services_source", "incident_affected_services", ["log_source_id"], unique=False
    )
    op.create_index(
        "idx_affected_services_name", "incident_affected_services", ["service_name"], unique=False
    )

    # 5. incident_events
    op.create_table(
        "incident_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_name", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=50), server_default="ANOMALY_SPIKE", nullable=False),
        sa.Column("severity", sa.String(length=20), server_default="INFO", nullable=False),
        sa.Column("sequence_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "attributes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "sequence_order", name="uq_incident_event_sequence"),
    )
    op.create_index(
        "idx_incident_events_timeline",
        "incident_events",
        ["incident_id", "event_timestamp"],
        unique=False,
    )
    op.create_index(
        "idx_incident_events_service",
        "incident_events",
        ["service_name", "event_timestamp"],
        unique=False,
    )

    # 6. evidence
    op.create_table(
        "evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("incident_event_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_type", sa.String(length=50), server_default="CULPRIT_LOG", nullable=False),
        sa.Column("opensearch_index", sa.String(length=100), nullable=True),
        sa.Column("opensearch_doc_id", sa.String(length=100), nullable=True),
        sa.Column("log_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("service_name", sa.String(length=100), nullable=True),
        sa.Column("log_level", sa.String(length=20), nullable=True),
        sa.Column("log_snippet", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("document_status", sa.String(length=30), server_default="INDEXED", nullable=False),
        sa.Column("relevance_score", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_event_id"], ["incident_events.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_evidence_incident_relevance",
        "evidence",
        ["incident_id", sa.text("relevance_score DESC")],
        unique=False,
    )
    op.create_index(
        "idx_evidence_os_pointer",
        "evidence",
        ["opensearch_index", "opensearch_doc_id"],
        unique=False,
    )

    # 7. recommendations
    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=50), server_default="IMMEDIATE_ACTION", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=20), server_default="MEDIUM", nullable=False),
        sa.Column("sequence_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("verification_step", sa.Text(), nullable=True),
        sa.Column("caveat", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_recommendations_incident_priority",
        "recommendations",
        ["incident_id", "priority"],
        unique=False,
    )

    # 8. reports
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="DRAFT", nullable=False),
        sa.Column("executive_summary", sa.Text(), nullable=False),
        sa.Column("timeline_summary", sa.Text(), nullable=False),
        sa.Column("root_cause_analysis", sa.Text(), nullable=False),
        sa.Column("recommendations_summary", sa.Text(), nullable=False),
        sa.Column("uncertainty_disclaimer", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "title", name="uq_reports_incident_title"),
    )
    op.create_index(
        "idx_reports_incident_generated",
        "reports",
        ["incident_id", sa.text("generated_at DESC")],
        unique=False,
    )
    op.create_index("idx_reports_status", "reports", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_table("recommendations")
    op.drop_table("evidence")
    op.drop_table("incident_events")
    op.drop_table("incident_affected_services")
    op.drop_table("incidents")
    op.drop_table("upload_jobs")
    op.drop_table("log_sources")
