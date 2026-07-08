"""All SQLAlchemy ORM models for Cante.  Every core table carries a tenant_id as multi-tenant seam."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from cante.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


SEEDED_TENANT = "00000000-0000-0000-0000-000000000001"


class TenantScoped:
    """Mixin: this table is isolated by ``tenant_id``.

    The data-layer enforcement in :mod:`cante.tenant` requires a tenant context
    for every SELECT against these tables (fail-closed) and stamps writes
    server-side from that context. The column is declared here so the
    ``with_loader_criteria`` enforcement can resolve ``cls.tenant_id`` against
    the base during SQLAlchemy's lambda-SQL analysis.
    """

    __tenant_scoped__ = True

    @declared_attr
    @classmethod
    def tenant_id(cls) -> Mapped[str]:
        return mapped_column(UUID(as_uuid=False), default=SEEDED_TENANT)


# ── Auth ──────────────────────────────────────────────────────────────


class User(Base, TenantScoped):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="operator")  # admin | operator
    language_ui: Mapped[str] = mapped_column(String(10), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── LLM ───────────────────────────────────────────────────────────────


class Provider(Base, TenantScoped):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)  # openai_compatible | anthropic
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key_ref: Mapped[str] = mapped_column(String(255), nullable=False)  # Secret.name or env var
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── Skills ────────────────────────────────────────────────────────────


class Skill(Base, TenantScoped):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    preset: Mapped[str] = mapped_column(String(30), default="custom")  # see seeds for presets
    language_default: Mapped[str] = mapped_column(String(10), default="en")
    playbook_md: Mapped[str] = mapped_column(Text, default="")
    guardrails_md: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[dict] = mapped_column(JSONB, default=dict)
    tools: Mapped[dict] = mapped_column(JSONB, default=dict)  # builtin toggles + declared HTTP tools
    done_condition: Mapped[str] = mapped_column(Text, default="")
    escalation: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    skill_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("skills.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    skill = relationship("Skill", backref="versions")


# ── Bots ──────────────────────────────────────────────────────────────


class Bot(Base, TenantScoped):
    __tablename__ = "bots"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type_label: Mapped[str] = mapped_column(String(50), default="custom")
    skill_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("skills.id"), nullable=False)
    provider_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("providers.id"), nullable=False)
    language_default: Mapped[str] = mapped_column(String(10), default="en")
    guard_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    skill = relationship("Skill")
    provider = relationship("Provider")


# ── Numbers & routing ─────────────────────────────────────────────────


class Number(Base, TenantScoped):
    __tablename__ = "numbers"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(30), default="whatsapp_evolution")
    connection_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="disconnected")
    display_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Route(Base, TenantScoped):
    __tablename__ = "routes"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    number_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("numbers.id"), nullable=False)
    bot_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("bots.id"), nullable=False)
    selector: Mapped[str] = mapped_column(String(30), default="default")  # default|contact_group|keyword_prefix
    selector_value: Mapped[str] = mapped_column(String(255), default="")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    number = relationship("Number")
    bot = relationship("Bot")


# ── Contacts ──────────────────────────────────────────────────────────


class Contact(Base, TenantScoped):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    phone: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | blocked
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "phone", name="uq_contact_phone"),
        Index("idx_contact_last_seen", "last_seen"),
    )


class ContactGroup(Base, TenantScoped):
    __tablename__ = "contact_groups"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    number_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("numbers.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    number = relationship("Number")


class GroupMembership(Base):
    __tablename__ = "group_memberships"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    contact_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("contacts.id"), nullable=False)
    group_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("contact_groups.id"), nullable=False)

    __table_args__ = (UniqueConstraint("contact_id", "group_id", name="uq_membership"),)


# ── Conversations ─────────────────────────────────────────────────────


class Conversation(Base, TenantScoped):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    number_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("numbers.id"), nullable=False)
    bot_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("bots.id"), nullable=False)
    contact_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("contacts.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="active")  # active|needs_human|paused|blocked|closed
    context_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    language_detected: Mapped[str] = mapped_column(String(10), default="")
    llm_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships (eager-loaded by the API to show phone/name in the UI).
    number = relationship("Number")
    bot = relationship("Bot")
    contact = relationship("Contact")

    __table_args__ = (
        Index("idx_conv_state", "state"),
        Index("idx_conv_bot", "bot_id"),
        Index("idx_conv_last_activity", "last_activity_at"),
    )


class Message(Base, TenantScoped):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("conversations.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(5), nullable=False)  # in | out
    role: Mapped[str] = mapped_column(String(10), default="user")  # user|assistant|system|tool
    body: Mapped[str] = mapped_column(Text, default="")
    wa_message_id: Mapped[str] = mapped_column(String(100), default="")
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (Index("idx_msg_conv_created", "conversation_id", "created_at"),)

    conversation = relationship("Conversation", backref="messages")


# ── Learning ──────────────────────────────────────────────────────────


class Learning(Base, TenantScoped):
    __tablename__ = "learnings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("conversations.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(30), default="no_action")
    suggestion_md: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # New fields for automated learning
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    suggestion_type: Mapped[str] = mapped_column(String(30), default="no_action")  # prompt|guardrail|none
    suggestion_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float] = mapped_column(default=0.0)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_llm_response: Mapped[dict] = mapped_column(JSONB, default=dict)
    run_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    reviewed_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)

    __table_args__ = (Index("idx_learning_created", "created_at"),)


# ── Events / Audit / Secrets ──────────────────────────────────────────


class Event(Base, TenantScoped):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    bot_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    number_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (Index("idx_events_type", "type"),)


class AuditLog(Base, TenantScoped):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    actor: Mapped[str] = mapped_column(String(100), default="")
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), default="")
    before: Mapped[dict] = mapped_column(JSONB, default=dict)
    after: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (Index("idx_audit_created", "created_at"),)


class Secret(Base, TenantScoped):
    __tablename__ = "secrets"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    # tenant_id is contributed by the TenantScoped mixin (see top of file).
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, default="")
    env_ref: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ── Metrics ────────────────────────────────────────────────────────────


class DailyMetrics(Base):
    """Pre-computed daily metrics cache.

    Past days are computed once (static), today is recomputed periodically.
    Scoped per tenant via ``tenant_id``. Array columns hold per-conversation
    values for percentile calculations (e.g. P50/P95 response times).
    """

    __tablename__ = "daily_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)  # noqa: F821
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), default=SEEDED_TENANT)
    date: Mapped[datetime] = mapped_column(Date, nullable=False)
    # Conversation states
    conversations_total: Mapped[int] = mapped_column(Integer, default=0)
    conversations_escalated: Mapped[int] = mapped_column(Integer, default=0)
    conversations_closed: Mapped[int] = mapped_column(Integer, default=0)
    conversations_active: Mapped[int] = mapped_column(Integer, default=0)
    # Message counts
    messages_in: Mapped[int] = mapped_column(Integer, default=0)
    messages_out: Mapped[int] = mapped_column(Integer, default=0)
    # Tokens
    tokens_total: Mapped[int] = mapped_column(BigInteger, default=0)  # noqa: F821
    # Arrays for percentiles (PostgreSQL array of floats/ints)
    first_reply_seconds: Mapped[list] = mapped_column(ARRAY(Float), default=list)  # noqa: F821
    resolution_seconds: Mapped[list] = mapped_column(ARRAY(Float), default=list)  # noqa: F821
    message_counts: Mapped[list] = mapped_column(ARRAY(Integer), default=list)  # noqa: F821
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "date", name="uq_daily_metrics_date"),
        Index("idx_dm_tenant_date", "tenant_id", "date"),
    )
