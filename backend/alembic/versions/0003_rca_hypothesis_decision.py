"""Create rca_hypothesis_decisions table for human-in-the-loop operator confirmation.

Revision ID: 0003_rca_hypothesis_decision
Revises: 0002_recommendation_citations
Create Date: 2026-11-18 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_rca_hypothesis_decision"
down_revision: Union[str, None] = "0002_recommendation_citations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        json_col = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
        citations_default = sa.text("'[]'::jsonb")
    else:
        json_col = sa.JSON()
        citations_default = sa.text("'[]'")

    op.create_table(
        "rca_hypothesis_decisions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "incident_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hypothesis_id", sa.String(length=255), nullable=False),
        sa.Column("hypothesis_text", sa.Text(), nullable=False),
        sa.Column("operator_id", sa.String(length=255), nullable=False),
        sa.Column("operator_name", sa.String(length=255), nullable=False),
        sa.Column("operator_email", sa.String(length=255), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column(
            "evidence_citations",
            json_col,
            server_default=citations_default,
            nullable=False,
        ),
        sa.Column("previous_decision", sa.String(length=20), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        "idx_rca_decisions_incident_decided",
        "rca_hypothesis_decisions",
        ["incident_id", "decided_at"],
    )
    op.create_index(
        "idx_rca_decisions_incident_hyp",
        "rca_hypothesis_decisions",
        ["incident_id", "hypothesis_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_rca_decisions_incident_hyp", table_name="rca_hypothesis_decisions")
    op.drop_index("idx_rca_decisions_incident_decided", table_name="rca_hypothesis_decisions")
    op.drop_table("rca_hypothesis_decisions")
