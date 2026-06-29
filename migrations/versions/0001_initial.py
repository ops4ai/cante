"""Initial schema — all Cante tables, hot-path + tenant_id indexes.

Revision ID: 0001
Revises:
Create Date: 2026-06-28

Mirrors core/cante/models.py. Every core table carries tenant_id (multi-tenant
seam). Indexes cover the hot list/order paths (C10) and per-tenant queries.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Auth ──────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("language_ui", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("idx_users_tenant", "users", ["tenant_id"])

    # ── LLM ───────────────────────────────────────────────────────────
    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("api_key_ref", sa.String(255), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_providers_tenant", "providers", ["tenant_id"])

    # ── Skills ────────────────────────────────────────────────────────
    op.create_table(
        "skills",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("preset", sa.String(30), nullable=False),
        sa.Column("language_default", sa.String(10), nullable=False),
        sa.Column("playbook_md", sa.Text, nullable=False),
        sa.Column("guardrails_md", sa.Text, nullable=False),
        sa.Column("scope", postgresql.JSONB, nullable=False),
        sa.Column("tools", postgresql.JSONB, nullable=False),
        sa.Column("done_condition", sa.Text, nullable=False),
        sa.Column("escalation", postgresql.JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_skills_tenant", "skills", ["tenant_id"])

    op.create_table(
        "skill_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("snapshot", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Bots ──────────────────────────────────────────────────────────
    op.create_table(
        "bots",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type_label", sa.String(50), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("language_default", sa.String(10), nullable=False),
        sa.Column("guard_config", postgresql.JSONB, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_bots_tenant", "bots", ["tenant_id"])

    # ── Numbers & routing ─────────────────────────────────────────────
    op.create_table(
        "numbers",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("channel_type", sa.String(30), nullable=False),
        sa.Column("connection_config", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_numbers_tenant", "numbers", ["tenant_id"])

    op.create_table(
        "routes",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("number_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("bot_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("selector", sa.String(30), nullable=False),
        sa.Column("selector_value", sa.String(255), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.ForeignKeyConstraint(["number_id"], ["numbers.id"]),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_routes_tenant", "routes", ["tenant_id"])

    # ── Contacts ──────────────────────────────────────────────────────
    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("attributes", postgresql.JSONB, nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("phone", name="contacts_phone_key"),
        sa.UniqueConstraint("tenant_id", "phone", name="uq_contact_phone"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_contact_last_seen", "contacts", ["last_seen"])  # C10
    op.create_index("idx_contacts_tenant", "contacts", ["tenant_id"])

    op.create_table(
        "contact_groups",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("number_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["number_id"], ["numbers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_contact_groups_tenant", "contact_groups", ["tenant_id"])

    op.create_table(
        "group_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("contact_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["group_id"], ["contact_groups.id"]),
        sa.UniqueConstraint("contact_id", "group_id", name="uq_membership"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Conversations ─────────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("number_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("bot_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("contact_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("context_json", postgresql.JSONB, nullable=False),
        sa.Column("language_detected", sa.String(10), nullable=False),
        sa.Column("llm_metadata", postgresql.JSONB, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["number_id"], ["numbers.id"]),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_conv_state", "conversations", ["state"])
    op.create_index("idx_conv_bot", "conversations", ["bot_id"])
    op.create_index("idx_conv_last_activity", "conversations", ["last_activity_at"])  # C10
    op.create_index("idx_conversations_tenant", "conversations", ["tenant_id"])

    # ── Messages ──────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("wa_message_id", sa.String(100), nullable=False),
        sa.Column("tokens", sa.Integer, nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_msg_conv_created", "messages", ["conversation_id", "created_at"])  # C10
    op.create_index("idx_messages_tenant", "messages", ["tenant_id"])

    # ── Learning ──────────────────────────────────────────────────────
    op.create_table(
        "learnings",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("suggestion_md", sa.Text, nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_learning_created", "learnings", ["created_at"])  # C10
    op.create_index("idx_learnings_tenant", "learnings", ["tenant_id"])

    # ── Events / Audit / Secrets ──────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("bot_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("number_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_events_type", "events", ["type"])
    op.create_index("idx_events_tenant", "events", ["tenant_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("entity", sa.String(50), nullable=False),
        sa.Column("before", postgresql.JSONB, nullable=False),
        sa.Column("after", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_created", "audit_logs", ["created_at"])  # C10
    op.create_index("idx_audit_logs_tenant", "audit_logs", ["tenant_id"])

    op.create_table(
        "secrets",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("value_encrypted", sa.Text, nullable=False),
        sa.Column("env_ref", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="secrets_name_key"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_secrets_tenant", "secrets", ["tenant_id"])


def downgrade() -> None:
    for table in (
        "secrets",
        "audit_logs",
        "events",
        "learnings",
        "messages",
        "conversations",
        "group_memberships",
        "contact_groups",
        "contacts",
        "routes",
        "numbers",
        "bots",
        "skill_versions",
        "skills",
        "providers",
        "users",
    ):
        op.drop_table(table)
