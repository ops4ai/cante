"""Enrich the learnings table with LLM diagnosis, auto-apply payload, and tracking.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("learnings", sa.Column("diagnosis", sa.Text(), nullable=False, server_default=sa.text("''")))
    op.add_column("learnings", sa.Column("suggestion_type", sa.String(30), nullable=False, server_default=sa.text("'no_action'")))
    op.add_column("learnings", sa.Column("suggestion_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("learnings", sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.0")))
    op.add_column("learnings", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("learnings", sa.Column("applied_ref", sa.String(255), nullable=True))
    op.add_column("learnings", sa.Column("raw_llm_response", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("learnings", sa.Column("run_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("learnings", "run_date")
    op.drop_column("learnings", "raw_llm_response")
    op.drop_column("learnings", "applied_ref")
    op.drop_column("learnings", "applied_at")
    op.drop_column("learnings", "confidence")
    op.drop_column("learnings", "suggestion_payload")
    op.drop_column("learnings", "suggestion_type")
    op.drop_column("learnings", "diagnosis")
