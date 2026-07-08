"""Add status column to contacts (active | blocked) + daily_metrics table.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
    )
    op.create_table(
        "daily_metrics",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversations_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("conversations_escalated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("conversations_closed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("conversations_active", sa.Integer, nullable=False, server_default="0"),
        sa.Column("messages_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("messages_out_bot", sa.Integer, nullable=False, server_default="0"),
        sa.Column("messages_out_human", sa.Integer, nullable=False, server_default="0"),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("date", "tenant_id", name="uq_daily_metrics_date_tenant"),
    )


def downgrade() -> None:
    op.drop_table("daily_metrics")
    op.drop_column("contacts", "status")
