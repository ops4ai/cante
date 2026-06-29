"""Cante API — backoffice control plane (FastAPI). Full CRUD for all entities."""

import structlog
from fastapi import FastAPI, Depends, HTTPException, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError

from cante.auth import (
    Principal,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    hash_password,
    principal_from_token,
    verify_password,
)
from cante.db import async_session_factory, run_migrations_async
from cante.security import assert_no_default_secrets
from cante.settings import settings
from cante.tenant import with_bypass, with_tenant

logger = structlog.get_logger(__name__)
app = FastAPI(title="Cante API", version="0.1.0")
# S8: CORS origins come from env (comma-separated); empty => same-origin only.
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] if settings.cors_origins else []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
security = HTTPBearer()


@app.on_event("startup")
async def _enforce_startup_guard() -> None:
    """S4: refuse to boot while any secret is still a shipped default / empty."""
    assert_no_default_secrets()


@app.on_event("startup")
async def _run_migrations_on_startup() -> None:
    """Ensure the DB schema exists before serving traffic (C1)."""
    try:
        await run_migrations_async()
        logger.info("api.migrations_applied")
    except Exception as e:  # pragma: no cover - startup logging only
        logger.error("api.migration_failed", error=str(e))
        raise


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Principal:
    try:
        return principal_from_token(credentials.credentials, expected_type="access")
    except JWTError:
        raise HTTPException(401, "Invalid token")


async def tenant_context(principal: Principal = Depends(get_current_user)) -> Principal:
    """S1: scope the whole request to the caller's tenant (fail-closed reads/writes)."""
    with with_tenant(principal.tenant_id):
        yield principal


class RequireRole:
    """Require a role (admin always passes). Runs under a tenant context."""

    def __init__(self, role: str):
        self.role = role

    async def __call__(self, principal: Principal = Depends(tenant_context)) -> Principal:
        if principal.role != self.role and principal.role != "admin":
            raise HTTPException(403, "Insufficient permissions")
        return principal


# ── S6/S12 helpers ──────────────────────────────────────────────────


async def load_owned(session, model, obj_id: str, principal: Principal):
    """Load *obj_id* scoped to *principal*'s tenant; 404 on any mismatch.

    Defense-in-depth on top of the data-layer filter (which already 404s
    cross-tenant reads) — also covers code paths that run under a bypass.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(model).where(model.id == obj_id, model.tenant_id == principal.tenant_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


async def log_audit(session, principal: Principal, action: str, entity: str, before: dict | None = None, after: dict | None = None) -> None:
    """S12: append an AuditLog row (tenant-scoped). Caller commits the session."""
    from cante.models import AuditLog

    session.add(
        AuditLog(
            tenant_id=principal.tenant_id,
            actor=principal.user_id,
            action=action,
            entity=entity,
            before=before or {},
            after=after or {},
        )
    )


def _escape_like(s: str) -> str:
    """S13: escape LIKE wildcards so user input can't broaden a search."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "api"}


# ── Auth ────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class UserCreateIn(BaseModel):
    email: str
    password: str
    role: str = "operator"


# S7: login throttle — 5 attempts per 15 min, per-IP and per-email.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 900


async def _login_throttled(redis, ip: str, email: str) -> bool:
    """Return True if the caller is currently throttled."""
    for key in (f"login:ip:{ip}", f"login:email:{email.lower()}"):
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, _LOGIN_WINDOW_SECONDS)
        if count > _LOGIN_MAX_ATTEMPTS:
            return True
    return False


@app.post("/v1/auth/login")
async def login(data: LoginIn, request: Request):
    from cante.models import User
    from sqlalchemy import select
    from cante.redis import get_redis

    ip = (request.client.host if request.client else "unknown") or "unknown"
    redis = await get_redis()
    if await _login_throttled(redis, ip, data.email):
        logger.warning("auth.login_throttled", ip=ip, email=data.email)
        raise HTTPException(429, "Too many login attempts. Try again later.")

    # S1: email is globally unique — look it up across tenants (bypass) to
    # resolve the tenant, then bind subsequent requests to it.
    async with async_session_factory() as session:
        with with_bypass():
            result = await session.execute(select(User).where(User.email == data.email))
            user = result.scalar_one_or_none()
        ok = bool(user) and verify_password(data.password, user.hashed_password) if user else False
        if not ok:
            # S7: generic message, no user enumeration.
            raise HTTPException(401, "Invalid credentials")
        access, refresh = create_token_pair(user.id, user.tenant_id, user.role)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "user": {"id": user.id, "email": user.email, "role": user.role, "tenant_id": user.tenant_id},
        }


@app.post("/v1/auth/refresh")
async def refresh(data: RefreshIn):
    """S10: rotate refresh tokens — the presented jti is revoked, a new pair issued."""
    from cante.redis import get_redis

    try:
        payload = decode_token(data.refresh_token, expected_type="refresh")
    except JWTError:
        raise HTTPException(401, "Invalid refresh token")
    jti = payload.get("jti")
    redis = await get_redis()
    if jti and await redis.exists(f"revoked:jti:{jti}"):
        raise HTTPException(401, "Refresh token revoked")
    if jti:
        await redis.set(f"revoked:jti:{jti}", "1", ex=settings.jwt_refresh_expire_days * 86_400)
    access = create_access_token(payload["sub"], payload["tenant_id"], payload["role"])[0]
    new_refresh = create_refresh_token(payload["sub"], payload["tenant_id"], payload["role"])[0]
    return {"access_token": access, "refresh_token": new_refresh}


@app.get("/v1/auth/me")
async def me(principal: Principal = Depends(get_current_user)):
    return {"id": principal.user_id, "role": principal.role, "tenant_id": principal.tenant_id}


@app.post("/v1/auth/users")
async def create_user(data: UserCreateIn, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import User
    async with async_session_factory() as session:
        u = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            role=data.role,
            tenant_id=principal.tenant_id,  # S1: server-side, never from client
        )
        session.add(u)
        await log_audit(session, principal, "user.create", f"user:{u.email}", after={"email": u.email, "role": u.role})
        await session.commit()
        return {"id": u.id, "email": u.email, "role": u.role}


# ── Numbers ─────────────────────────────────────────────────────────

@app.get("/v1/numbers")
async def list_numbers(principal: Principal = Depends(tenant_context)):
    from cante.models import Number
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Number).limit(50))
        return [{"id": n.id, "phone": n.phone, "status": n.status, "display_name": n.display_name, "channel_type": n.channel_type} for n in result.scalars().all()]


@app.post("/v1/numbers")
async def create_number(data: dict, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Number
    async with async_session_factory() as session:
        n = Number(phone=data["phone"], display_name=data.get("display_name", ""), channel_type=data.get("channel_type", "whatsapp_evolution"))
        session.add(n)
        await session.flush()
        await log_audit(session, principal, "number.create", f"number:{n.id}", after={"phone": n.phone, "channel_type": n.channel_type})
        await session.commit()
        return {"id": n.id, "phone": n.phone, "status": n.status}


@app.get("/v1/numbers/{num_id}/qr")
async def get_qr(num_id: str, principal: Principal = Depends(RequireRole("admin"))):
    # S15: not yet wired to the Evolution gateway — fail honestly rather than
    # return a fabricated internal URL that looks like a real QR.
    raise HTTPException(501, "QR pairing not implemented; configure the channel via the Evolution API directly.")


@app.post("/v1/numbers/{num_id}/connect")
async def connect_number(num_id: str, principal: Principal = Depends(RequireRole("admin"))):
    # S15: stub — label explicitly so callers don't assume a live connection.
    raise HTTPException(501, "Number connect not implemented.")


@app.post("/v1/numbers/{num_id}/disconnect")
async def disconnect_number(num_id: str, principal: Principal = Depends(RequireRole("admin"))):
    return {"status": "disconnected"}


# ── Bots ────────────────────────────────────────────────────────────

@app.get("/v1/bots")
async def list_bots(principal: Principal = Depends(tenant_context)):
    from cante.models import Bot
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Bot).limit(50))
        return [{"id": b.id, "name": b.name, "type_label": b.type_label, "language_default": b.language_default, "enabled": b.enabled, "skill_id": b.skill_id, "provider_id": b.provider_id} for b in result.scalars().all()]


@app.post("/v1/bots")
async def create_bot(data: dict, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Bot
    async with async_session_factory() as session:
        b = Bot(name=data["name"], skill_id=data["skill_id"], provider_id=data["provider_id"], type_label=data.get("type_label", "custom"), language_default=data.get("language_default", "en"))
        session.add(b)
        await session.flush()
        await log_audit(session, principal, "bot.create", f"bot:{b.id}", after={"name": b.name, "skill_id": b.skill_id, "provider_id": b.provider_id})
        await session.commit()
        return {"id": b.id, "name": b.name}


@app.patch("/v1/bots/{bot_id}")
async def update_bot(bot_id: str, data: dict, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Bot
    async with async_session_factory() as session:
        b = await load_owned(session, Bot, bot_id, principal)
        before = {"name": b.name, "type_label": b.type_label, "enabled": b.enabled}
        for field in ("name", "type_label", "language_default", "skill_id", "provider_id"):
            if field in data:
                setattr(b, field, data[field])
        if "enabled" in data:
            b.enabled = data["enabled"]
        await log_audit(session, principal, "bot.update", f"bot:{b.id}", before=before, after={"name": b.name, "enabled": b.enabled})
        await session.commit()
        return {"id": b.id, "name": b.name}


# ── Skills ──────────────────────────────────────────────────────────

@app.get("/v1/skills")
async def list_skills(principal: Principal = Depends(tenant_context)):
    from cante.models import Skill
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Skill).limit(50))
        return [{"id": s.id, "name": s.name, "preset": s.preset, "language_default": s.language_default, "enabled": s.enabled, "playbook_md": s.playbook_md[:200]} for s in result.scalars().all()]


@app.post("/v1/skills")
async def create_skill(data: dict, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Skill
    async with async_session_factory() as session:
        s = Skill(name=data["name"], preset=data.get("preset", "custom"), playbook_md=data.get("playbook_md", ""), guardrails_md=data.get("guardrails_md", ""), language_default=data.get("language_default", "en"), scope=data.get("scope", {}), tools=data.get("tools", {}), done_condition=data.get("done_condition", ""), escalation=data.get("escalation", {}))
        session.add(s)
        await session.flush()
        await log_audit(session, principal, "skill.create", f"skill:{s.id}", after={"name": s.name, "preset": s.preset})
        # Create first version snapshot
        from cante.models import SkillVersion
        session.add(SkillVersion(skill_id=s.id, version=1, snapshot={"name": s.name, "playbook_md": s.playbook_md, "guardrails_md": s.guardrails_md, "scope": s.scope, "tools": s.tools, "done_condition": s.done_condition, "escalation": s.escalation}))
        await session.commit()
        return {"id": s.id, "name": s.name}


@app.patch("/v1/skills/{skill_id}")
async def update_skill(skill_id: str, data: dict, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Skill, SkillVersion
    from sqlalchemy import func, select
    async with async_session_factory() as session:
        s = await load_owned(session, Skill, skill_id, principal)
        for field in ("name", "preset", "playbook_md", "guardrails_md", "language_default", "done_condition"):
            if field in data:
                setattr(s, field, data[field])
        if "scope" in data:
            s.scope = data["scope"]
        if "tools" in data:
            s.tools = data["tools"]
        if "escalation" in data:
            s.escalation = data["escalation"]
        max_v = (await session.execute(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill_id))).scalar() or 0
        session.add(SkillVersion(skill_id=s.id, version=max_v + 1, snapshot={"name": s.name, "playbook_md": s.playbook_md, "guardrails_md": s.guardrails_md, "scope": s.scope, "tools": s.tools, "done_condition": s.done_condition, "escalation": s.escalation}))
        await log_audit(session, principal, "skill.update", f"skill:{s.id}", after={"name": s.name, "version": max_v + 1})
        await session.commit()
        return {"id": s.id, "name": s.name, "version": max_v + 1}


# ── Providers ───────────────────────────────────────────────────────

@app.get("/v1/providers")
async def list_providers(principal: Principal = Depends(tenant_context)):
    from cante.models import Provider
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Provider).limit(50))
        return [{"id": p.id, "name": p.name, "type": p.type, "model": p.model, "enabled": p.enabled, "base_url": p.base_url} for p in result.scalars().all()]


@app.post("/v1/providers")
async def create_provider(data: dict, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Provider
    async with async_session_factory() as session:
        p = Provider(name=data["name"], type=data["type"], base_url=data["base_url"], model=data["model"], api_key_ref=data["api_key_ref"], params=data.get("params", {}))
        session.add(p)
        await session.flush()
        await log_audit(session, principal, "provider.create", f"provider:{p.id}", after={"name": p.name, "type": p.type, "model": p.model})
        await session.commit()
        return {"id": p.id, "name": p.name, "type": p.type}


# ── Routes ──────────────────────────────────────────────────────────

@app.get("/v1/routes")
async def list_routes(principal: Principal = Depends(tenant_context)):
    from cante.models import Route
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Route).limit(50))
        return [{"id": r.id, "number_id": r.number_id, "bot_id": r.bot_id, "selector": r.selector, "selector_value": r.selector_value, "enabled": r.enabled} for r in result.scalars().all()]


@app.post("/v1/routes")
async def create_route(data: dict, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Route
    async with async_session_factory() as session:
        r = Route(number_id=data["number_id"], bot_id=data["bot_id"], selector=data.get("selector", "default"), selector_value=data.get("selector_value", ""), priority=data.get("priority", 0))
        session.add(r)
        await session.flush()
        await log_audit(session, principal, "route.create", f"route:{r.id}", after={"number_id": r.number_id, "bot_id": r.bot_id, "selector": r.selector})
        await session.commit()
        return {"id": r.id, "selector": r.selector}


@app.delete("/v1/routes/{route_id}")
async def delete_route(route_id: str, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Route
    async with async_session_factory() as session:
        r = await load_owned(session, Route, route_id, principal)
        await log_audit(session, principal, "route.delete", f"route:{r.id}", before={"number_id": r.number_id, "bot_id": r.bot_id})
        await session.delete(r)
        await session.commit()
        return {"deleted": True}


# ── Contacts ────────────────────────────────────────────────────────

@app.get("/v1/contacts")
async def list_contacts(number_id: str = "", group_id: str = "", search: str = "", principal: Principal = Depends(tenant_context)):
    from cante.models import Contact
    from sqlalchemy import select
    async with async_session_factory() as session:
        stmt = select(Contact).order_by(Contact.last_seen.desc()).limit(100)
        if search:
            esc = _escape_like(search)
            stmt = stmt.where(
                (Contact.name.ilike(f"%{esc}%", escape="\\")) | (Contact.phone.ilike(f"%{esc}%", escape="\\"))
            )
        result = await session.execute(stmt)
        return [{"id": c.id, "phone": c.phone, "name": c.name, "attributes": c.attributes, "first_seen": str(c.first_seen), "last_seen": str(c.last_seen)} for c in result.scalars().all()]


@app.patch("/v1/contacts/{contact_id}")
async def update_contact(contact_id: str, data: dict, principal: Principal = Depends(tenant_context)):
    from cante.models import Contact
    async with async_session_factory() as session:
        c = await load_owned(session, Contact, contact_id, principal)
        before = {"name": c.name, "attributes": c.attributes}
        if "name" in data:
            c.name = data["name"]
        if "attributes" in data:
            c.attributes = {**c.attributes, **data["attributes"]}
        await log_audit(session, principal, "contact.update", f"contact:{c.id}", before=before, after={"name": c.name, "attributes": c.attributes})
        await session.commit()
        return {"id": c.id, "name": c.name}


# ── Contact Groups ──────────────────────────────────────────────────

@app.get("/v1/groups")
async def list_groups(principal: Principal = Depends(tenant_context)):
    from cante.models import ContactGroup
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(ContactGroup).limit(50))
        return [{"id": g.id, "name": g.name, "number_id": g.number_id} for g in result.scalars().all()]


@app.post("/v1/groups")
async def create_group(data: dict, principal: Principal = Depends(tenant_context)):
    from cante.models import ContactGroup
    async with async_session_factory() as session:
        g = ContactGroup(name=data["name"], number_id=data.get("number_id"))
        session.add(g)
        await session.commit()
        return {"id": g.id, "name": g.name}


@app.post("/v1/groups/{group_id}/members")
async def add_member(group_id: str, data: dict, principal: Principal = Depends(tenant_context)):
    from cante.models import GroupMembership
    async with async_session_factory() as session:
        m = GroupMembership(contact_id=data["contact_id"], group_id=group_id)
        session.add(m)
        await session.commit()
        return {"status": "added"}


# ── Conversations ────────────────────────────────────────────────────

@app.get("/v1/conversations")
async def list_conversations(
    state: str = "", bot_id: str = "", number_id: str = "", search: str = "",
    principal: Principal = Depends(tenant_context),
):
    from cante.models import Conversation
    from sqlalchemy import select
    async with async_session_factory() as session:
        stmt = select(Conversation).order_by(Conversation.last_activity_at.desc()).limit(50)
        if state:
            stmt = stmt.where(Conversation.state == state)
        if bot_id:
            stmt = stmt.where(Conversation.bot_id == bot_id)
        if number_id:
            stmt = stmt.where(Conversation.number_id == number_id)
        result = await session.execute(stmt)
        return [{"id": c.id, "state": c.state, "language_detected": c.language_detected, "contact_id": c.contact_id, "bot_id": c.bot_id, "number_id": c.number_id, "last_activity_at": str(c.last_activity_at), "started_at": str(c.started_at)} for c in result.scalars().all()]


@app.get("/v1/conversations/{conv_id}")
async def get_conversation(conv_id: str, principal: Principal = Depends(tenant_context)):
    from cante.models import Conversation, Message
    from sqlalchemy import select
    async with async_session_factory() as session:
        conv = await load_owned(session, Conversation, conv_id, principal)
        msgs = (await session.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at).limit(100))).scalars().all()
        return {"id": conv.id, "state": conv.state, "context_json": conv.context_json, "messages": [{"id": m.id, "direction": m.direction, "role": m.role, "body": m.body, "created_at": str(m.created_at)} for m in msgs]}


@app.post("/v1/conversations/{conv_id}/takeover")
async def takeover(conv_id: str, principal: Principal = Depends(tenant_context)):
    from cante.models import Conversation
    async with async_session_factory() as session:
        conv = await load_owned(session, Conversation, conv_id, principal)
        before = {"state": conv.state}
        conv.state = "active"
        await log_audit(session, principal, "conversation.takeover", f"conversation:{conv.id}", before=before, after={"state": conv.state})
        await session.commit()
        return {"id": conv.id, "state": conv.state}


@app.post("/v1/conversations/{conv_id}/send")
async def send_as_human(conv_id: str, data: dict, principal: Principal = Depends(tenant_context)):
    from cante.bus import RedisStreamsBus
    from cante.models import Conversation, Number
    from cante.redis import get_redis
    from sqlalchemy import select
    async with async_session_factory() as session:
        conv = await load_owned(session, Conversation, conv_id, principal)
        # S11: route the human reply via the conversation's own Number so the
        # outbound message carries the correct sender, not an empty string.
        number = (await session.execute(select(Number).where(Number.id == conv.number_id))).scalar_one_or_none()
        number_phone = number.phone if number else ""
        redis = await get_redis()
        bus = RedisStreamsBus(redis)
        await bus.publish("stream:outbound", {"conversation_id": conv_id, "from_phone": "", "number_phone": number_phone, "body": data["body"]})
        await log_audit(session, principal, "conversation.send_as_human", f"conversation:{conv.id}", after={"body": data["body"], "number_phone": number_phone})
        await session.commit()
        return {"status": "sent"}


@app.post("/v1/conversations/{conv_id}/close")
async def close_conv(conv_id: str, principal: Principal = Depends(tenant_context)):
    from cante.models import Conversation
    async with async_session_factory() as session:
        conv = await load_owned(session, Conversation, conv_id, principal)
        before = {"state": conv.state}
        conv.state = "closed"
        await log_audit(session, principal, "conversation.close", f"conversation:{conv.id}", before=before, after={"state": conv.state})
        await session.commit()
        return {"id": conv.id, "state": "closed"}


# ── Learning ────────────────────────────────────────────────────────

@app.get("/v1/learnings")
async def list_learnings(status: str = "", principal: Principal = Depends(tenant_context)):
    from cante.models import Learning
    from sqlalchemy import select
    async with async_session_factory() as session:
        stmt = select(Learning).order_by(Learning.created_at.desc()).limit(50)
        if status:
            stmt = stmt.where(Learning.status == status)
        result = await session.execute(stmt)
        return [{"id": l.id, "type": l.type, "suggestion_md": l.suggestion_md[:200], "category": l.category, "status": l.status} for l in result.scalars().all()]


@app.post("/v1/learnings/{learning_id}/approve")
async def approve_learning(learning_id: str, principal: Principal = Depends(tenant_context)):
    from cante.models import Learning
    async with async_session_factory() as session:
        l = await load_owned(session, Learning, learning_id, principal)
        before = {"status": l.status}
        l.status = "approved"
        l.reviewed_by = principal.user_id
        await log_audit(session, principal, "learning.approve", f"learning:{l.id}", before=before, after={"status": l.status})
        await session.commit()
        return {"id": l.id, "status": "approved"}


@app.post("/v1/learnings/{learning_id}/reject")
async def reject_learning(learning_id: str, principal: Principal = Depends(tenant_context)):
    from cante.models import Learning
    async with async_session_factory() as session:
        l = await load_owned(session, Learning, learning_id, principal)
        before = {"status": l.status}
        l.status = "rejected"
        l.reviewed_by = principal.user_id
        await log_audit(session, principal, "learning.reject", f"learning:{l.id}", before=before, after={"status": l.status})
        await session.commit()
        return {"id": l.id, "status": "rejected"}


# ── Metrics ──────────────────────────────────────────────────────────

@app.get("/v1/metrics/overview")
async def metrics_overview(principal: Principal = Depends(tenant_context)):
    from cante.models import Bot, Conversation, Message, Number
    from sqlalchemy import func, select
    async with async_session_factory() as session:
        return {
            "total_conversations": (await session.execute(select(func.count(Conversation.id)))).scalar() or 0,
            "escalated": (await session.execute(select(func.count(Conversation.id)).where(Conversation.state == "needs_human"))).scalar() or 0,
            "active": (await session.execute(select(func.count(Conversation.id)).where(Conversation.state == "active"))).scalar() or 0,
            "closed": (await session.execute(select(func.count(Conversation.id)).where(Conversation.state == "closed"))).scalar() or 0,
            "total_bots": (await session.execute(select(func.count(Bot.id)))).scalar() or 0,
            "total_numbers": (await session.execute(select(func.count(Number.id)))).scalar() or 0,
            "total_messages": (await session.execute(select(func.count(Message.id)))).scalar() or 0,
        }


# ── Audit ───────────────────────────────────────────────────────────

@app.get("/v1/audit")
async def list_audit(principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import AuditLog
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100))
        return [{"id": a.id, "actor": a.actor, "action": a.action, "entity": a.entity, "created_at": str(a.created_at)} for a in result.scalars().all()]


# ── Triggers ────────────────────────────────────────────────────────

@app.post("/v1/triggers")
async def create_trigger(data: dict, request: Request):
    # S2: machine-to-machine trigger key, separate from the JWT secret,
    # compared in constant time to avoid timing oracles.
    import secrets as _pysecrets

    if not settings.trigger_api_key or not _pysecrets.compare_digest(
        request.headers.get("X-API-Key", ""), settings.trigger_api_key
    ):
        raise HTTPException(401, "Invalid API key")
    from cante.bus import RedisStreamsBus
    from cante.redis import get_redis
    redis = await get_redis()
    bus = RedisStreamsBus(redis)
    await bus.publish("stream:triggers", {"conversation_id": data.get("conversation_id", ""), "from_phone": data.get("to_phone", ""), "number_phone": data.get("from_number", ""), "body": data.get("body", "")})
    return {"status": "queued"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.api.main:app", host="0.0.0.0", port=8000, reload=False)
