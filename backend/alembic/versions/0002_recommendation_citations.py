"""Add evidence_citations JSON column to recommendations table.

Revision ID: 0002_recommendation_citations
Revises: 0001_initial_schema
Create Date: 2026-11-18 12:56:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_recommendation_citations"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        server_default = sa.text("'[]'::jsonb")
    else:
        server_default = sa.text("'[]'")

    op.add_column(
        "recommendations",
        sa.Column(
            "evidence_citations",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            server_default=server_default,
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("recommendations", "evidence_citations")
