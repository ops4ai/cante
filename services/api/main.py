"""Cante API — backoffice control plane (FastAPI). Full CRUD for all entities."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup: enforce no-default-secrets + run migrations. Shutdown: no-op."""
    assert_no_default_secrets()
    try:
        await run_migrations_async()
        logger.info("api.migrations_applied")
    except Exception as e:  # pragma: no cover - startup logging only
        logger.error("api.migration_failed", error=str(e))
        raise
    yield


app = FastAPI(title="Cante API", version="0.1.0", lifespan=lifespan)
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


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Principal:
    try:
        return principal_from_token(credentials.credentials, expected_type="access")
    except JWTError:
        raise HTTPException(401, "Invalid token")


async def tenant_context(
    principal: Principal = Depends(get_current_user),
) -> AsyncGenerator[Principal, None]:
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


# ── C11: keyset pagination helper ────────────────────────────────────

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


async def paginate(session, model, *, cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, order_col=None, extra_where=None):
    """Keyset-paginate *model* ordered by *order_col* (default: created_at).

    Returns ``{items, total, next_cursor}``.  *cursor* is an ISO-8601
    timestamp from the previous page's ``next_cursor``.
    """
    from sqlalchemy import func, select

    limit = min(max(1, limit), MAX_PAGE_SIZE)
    col = order_col if order_col is not None else model.created_at

    # Total count
    cnt_stmt = select(func.count(model.id))
    if extra_where is not None:
        cnt_stmt = cnt_stmt.where(extra_where)
    total = (await session.execute(cnt_stmt)).scalar() or 0

    # Keyset — fetch limit+1 so we can detect whether there is a next page
    stmt = select(model).order_by(col.desc() if order_col is None else col).limit(limit + 1)
    if extra_where is not None:
        stmt = stmt.where(extra_where)
    if cursor:
        try:
            cursor_val = cursor
            stmt = stmt.where(col < cursor_val)
        except (ValueError, TypeError):
            pass  # ignore malformed cursors, just return first page

    result = await session.execute(stmt)
    rows = result.scalars().all()
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = str(getattr(last, col.key))

    return {
        "items": rows,
        "total": total,
        "next_cursor": next_cursor,
    }


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


# ── C19: Pydantic request models for CRUD endpoints ─────────────────────


class NumberCreateIn(BaseModel):
    phone: str
    display_name: str = ""
    channel_type: str = "whatsapp_evolution"


class BotCreateIn(BaseModel):
    name: str
    skill_id: str
    provider_id: str
    type_label: str = "custom"
    language_default: str = "en"


class BotUpdateIn(BaseModel):
    name: str | None = None
    type_label: str | None = None
    language_default: str | None = None
    skill_id: str | None = None
    provider_id: str | None = None
    enabled: bool | None = None


class SkillCreateIn(BaseModel):
    name: str
    preset: str = "custom"
    playbook_md: str = ""
    guardrails_md: str = ""
    language_default: str = "en"
    scope: dict = {}
    tools: dict = {}
    done_condition: str = ""
    escalation: dict = {}


class SkillUpdateIn(BaseModel):
    name: str | None = None
    preset: str | None = None
    playbook_md: str | None = None
    guardrails_md: str | None = None
    language_default: str | None = None
    scope: dict | None = None
    tools: dict | None = None
    done_condition: str | None = None
    escalation: dict | None = None


class ProviderCreateIn(BaseModel):
    name: str
    type: str
    base_url: str
    model: str
    api_key_ref: str
    params: dict = {}


class RouteCreateIn(BaseModel):
    number_id: str
    bot_id: str
    selector: str = "default"
    selector_value: str = ""
    priority: int = 0


class ContactUpdateIn(BaseModel):
    name: str | None = None
    attributes: dict | None = None


class GroupCreateIn(BaseModel):
    name: str
    number_id: str | None = None


class GroupAddMemberIn(BaseModel):
    contact_id: str


class SendAsHumanIn(BaseModel):
    body: str


class TriggerCreateIn(BaseModel):
    conversation_id: str = ""
    to_phone: str = ""
    from_number: str = ""
    body: str = ""


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
        if not ok or user is None:
            # S7: generic message, no user enumeration.
            raise HTTPException(401, "Invalid credentials")
        access, refresh = create_token_pair(user.id, user.tenant_id, user.role)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "user": {
                "id": user.id,
                "email": user.email,
                "role": user.role,
                "tenant_id": user.tenant_id,
            },
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
async def list_numbers(cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, principal: Principal = Depends(tenant_context)):
    from cante.models import Number
    async with async_session_factory() as session:
        page = await paginate(session, Number, cursor=cursor, limit=limit)
        return {
            "items": [{"id": n.id, "phone": n.phone, "status": n.status, "display_name": n.display_name, "channel_type": n.channel_type} for n in page["items"]],
            "total": page["total"],
            "next_cursor": page["next_cursor"],
        }


@app.post("/v1/numbers")
async def create_number(data: NumberCreateIn, principal: Principal = Depends(RequireRole("admin"))):
    from cante.evolution import EvolutionAdapter, instance_name_for
    from cante.models import Number
    async with async_session_factory() as session:
        instance = instance_name_for(data.phone)
        connection_config = {"instance": instance, "phone": data.phone}
        n = Number(
            phone=data.phone,
            display_name=data.display_name,
            channel_type=data.channel_type,
            connection_config=connection_config,
        )
        session.add(n)
        await session.flush()
        # Provision the Evolution instance now so /qr (instance/connect) works.
        # Provisioning failures are surfaced as a 502 (don't silently save a
        # Number whose QR can never be fetched).
        try:
            adapter = EvolutionAdapter()
            await adapter.create_instance(instance)
        except Exception as exc:
            await session.rollback()
            raise HTTPException(502, f"Evolution instance create failed: {exc}") from exc
        await log_audit(session, principal, "number.create", f"number:{n.id}", after={"phone": n.phone, "channel_type": n.channel_type, "instance": instance})
        await session.commit()
        return {"id": n.id, "phone": n.phone, "status": n.status, "instance": instance}


@app.get("/v1/numbers/{num_id}/qr")
async def get_qr(num_id: str, principal: Principal = Depends(RequireRole("admin"))):
    """Fetch the current QR code for WhatsApp Web pairing.

    Calls the Evolution API ``/instance/connect/{instance}`` endpoint which
    returns a base64-encoded QR code when the instance is waiting for a scan.
    """
    from cante.evolution import EvolutionAdapter
    from cante.models import Number

    async with async_session_factory() as session:
        number = await load_owned(session, Number, num_id, principal)
        adapter = EvolutionAdapter()
        try:
            result = await adapter.connect(number.connection_config)
            return {
                "qr_code": result.qr_code,
                "status": result.status,
            }
        except Exception as exc:
            raise HTTPException(502, f"Evolution API unreachable: {exc}") from exc


@app.post("/v1/numbers/{num_id}/connect")
async def connect_number(num_id: str, principal: Principal = Depends(RequireRole("admin"))):
    """Initiate WhatsApp Web pairing for *num_id* via the Evolution gateway.

    Returns the base64-encoded QR code the user must scan with WhatsApp.
    """
    from cante.evolution import EvolutionAdapter
    from cante.models import Number

    async with async_session_factory() as session:
        number = await load_owned(session, Number, num_id, principal)
        adapter = EvolutionAdapter()
        try:
            result = await adapter.connect(number.connection_config)
            return {
                "qr_code": result.qr_code,
                "status": result.status,
            }
        except Exception as exc:
            raise HTTPException(502, f"Evolution API unreachable: {exc}") from exc


@app.post("/v1/numbers/{num_id}/disconnect")
async def disconnect_number(num_id: str, principal: Principal = Depends(RequireRole("admin"))):
    """Disconnect *num_id* from the WhatsApp gateway.

    The Evolution API does not expose a direct "disconnect" endpoint, so this
    returns the current connection state as reported by the gateway.
    """
    from cante.evolution import EvolutionAdapter
    from cante.models import Number

    async with async_session_factory() as session:
        number = await load_owned(session, Number, num_id, principal)
        adapter = EvolutionAdapter()
        try:
            state = await adapter.status(number.connection_config)
            return {
                "status": state.status,
                "phone": state.phone,
                "instance_id": state.instance_id,
            }
        except Exception as exc:
            raise HTTPException(502, f"Evolution API unreachable: {exc}") from exc


# ── Bots ────────────────────────────────────────────────────────────

@app.get("/v1/bots")
async def list_bots(cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, principal: Principal = Depends(tenant_context)):
    from cante.models import Bot
    async with async_session_factory() as session:
        page = await paginate(session, Bot, cursor=cursor, limit=limit)
        return {
            "items": [{"id": b.id, "name": b.name, "type_label": b.type_label, "language_default": b.language_default, "enabled": b.enabled, "skill_id": b.skill_id, "provider_id": b.provider_id} for b in page["items"]],
            "total": page["total"],
            "next_cursor": page["next_cursor"],
        }


@app.post("/v1/bots")
async def create_bot(data: BotCreateIn, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Bot
    async with async_session_factory() as session:
        b = Bot(name=data.name, skill_id=data.skill_id, provider_id=data.provider_id, type_label=data.type_label, language_default=data.language_default)
        session.add(b)
        await session.flush()
        await log_audit(session, principal, "bot.create", f"bot:{b.id}", after={"name": b.name, "skill_id": b.skill_id, "provider_id": b.provider_id})
        await session.commit()
        return {"id": b.id, "name": b.name}


@app.patch("/v1/bots/{bot_id}")
async def update_bot(bot_id: str, data: BotUpdateIn, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Bot
    async with async_session_factory() as session:
        b = await load_owned(session, Bot, bot_id, principal)
        before = {"name": b.name, "type_label": b.type_label, "enabled": b.enabled}
        updates = data.model_dump(exclude_unset=True)
        for field in ("name", "type_label", "language_default", "skill_id", "provider_id"):
            if field in updates:
                setattr(b, field, updates[field])
        if "enabled" in updates:
            b.enabled = updates["enabled"]
        await log_audit(session, principal, "bot.update", f"bot:{b.id}", before=before, after={"name": b.name, "enabled": b.enabled})
        await session.commit()
        return {"id": b.id, "name": b.name}


# ── Skills ──────────────────────────────────────────────────────────

@app.get("/v1/skills")
async def list_skills(cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, principal: Principal = Depends(tenant_context)):
    from cante.models import Skill
    async with async_session_factory() as session:
        page = await paginate(session, Skill, cursor=cursor, limit=limit)
        return {
            "items": [{"id": s.id, "name": s.name, "preset": s.preset, "language_default": s.language_default, "enabled": s.enabled, "playbook_md": s.playbook_md[:200]} for s in page["items"]],
            "total": page["total"],
            "next_cursor": page["next_cursor"],
        }


@app.post("/v1/skills")
async def create_skill(data: SkillCreateIn, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Skill
    async with async_session_factory() as session:
        s = Skill(name=data.name, preset=data.preset, playbook_md=data.playbook_md, guardrails_md=data.guardrails_md, language_default=data.language_default, scope=data.scope, tools=data.tools, done_condition=data.done_condition, escalation=data.escalation)
        session.add(s)
        await session.flush()
        await log_audit(session, principal, "skill.create", f"skill:{s.id}", after={"name": s.name, "preset": s.preset})
        # Create first version snapshot
        from cante.models import SkillVersion
        session.add(SkillVersion(skill_id=s.id, version=1, snapshot={"name": s.name, "playbook_md": s.playbook_md, "guardrails_md": s.guardrails_md, "scope": s.scope, "tools": s.tools, "done_condition": s.done_condition, "escalation": s.escalation}))
        await session.commit()
        return {"id": s.id, "name": s.name}


@app.patch("/v1/skills/{skill_id}")
async def update_skill(skill_id: str, data: SkillUpdateIn, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Skill, SkillVersion
    from sqlalchemy import func, select
    async with async_session_factory() as session:
        s = await load_owned(session, Skill, skill_id, principal)
        updates = data.model_dump(exclude_unset=True)
        for field in ("name", "preset", "playbook_md", "guardrails_md", "language_default", "done_condition"):
            if field in updates:
                setattr(s, field, updates[field])
        if "scope" in updates:
            s.scope = updates["scope"]
        if "tools" in updates:
            s.tools = updates["tools"]
        if "escalation" in updates:
            s.escalation = updates["escalation"]
        max_v = (await session.execute(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill_id))).scalar() or 0
        session.add(SkillVersion(skill_id=s.id, version=max_v + 1, snapshot={"name": s.name, "playbook_md": s.playbook_md, "guardrails_md": s.guardrails_md, "scope": s.scope, "tools": s.tools, "done_condition": s.done_condition, "escalation": s.escalation}))
        await log_audit(session, principal, "skill.update", f"skill:{s.id}", after={"name": s.name, "version": max_v + 1})
        await session.commit()
        return {"id": s.id, "name": s.name, "version": max_v + 1}


# ── Providers ───────────────────────────────────────────────────────

@app.get("/v1/providers")
async def list_providers(cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, principal: Principal = Depends(tenant_context)):
    from cante.models import Provider
    async with async_session_factory() as session:
        page = await paginate(session, Provider, cursor=cursor, limit=limit)
        return {
            "items": [{"id": p.id, "name": p.name, "type": p.type, "model": p.model, "enabled": p.enabled, "base_url": p.base_url} for p in page["items"]],
            "total": page["total"],
            "next_cursor": page["next_cursor"],
        }


@app.post("/v1/providers")
async def create_provider(data: ProviderCreateIn, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Provider
    async with async_session_factory() as session:
        p = Provider(name=data.name, type=data.type, base_url=data.base_url, model=data.model, api_key_ref=data.api_key_ref, params=data.params)
        session.add(p)
        await session.flush()
        await log_audit(session, principal, "provider.create", f"provider:{p.id}", after={"name": p.name, "type": p.type, "model": p.model})
        await session.commit()
        return {"id": p.id, "name": p.name, "type": p.type}


@app.post("/v1/providers/{provider_id}/test")
async def test_provider(provider_id: str, principal: Principal = Depends(RequireRole("admin"))):
    """Test connectivity to a provider's LLM with a minimal API call.

    Sends a single message ("Hi") with ``max_tokens=10``, ``temperature=0``.
    Returns ``{ok: true, model, latency_ms, tokens_in, tokens_out}`` on success,
    or ``{ok: false, error, hint}`` with a human-readable hint on failure.
    """
    from cante.db import build_provider_adapter, resolve_provider_api_key
    from cante.llm import LLMAPIConnectionError, LLMAPIStatusError, LLMAPITimeout, LLMMessage
    from cante.models import Provider
    import time as _time

    async with async_session_factory() as session:
        provider = await load_owned(session, Provider, provider_id, principal)

        api_key = await resolve_provider_api_key(provider, session)
        if not api_key:
            return {
                "ok": False,
                "error": "API key not configured",
                "hint": (
                    f"Set the environment variable '{provider.api_key_ref}' "
                    f"or create a Secret named '{provider.api_key_ref}'."
                ),
            }

        adapter = build_provider_adapter(provider, api_key)
        try:
            start = _time.monotonic()
            resp = await adapter.complete(
                messages=[LLMMessage(role="user", content="Hi")],
                tools=[],
                temperature=0,
                max_tokens=10,
                model=provider.model,
            )
            elapsed_ms = round((_time.monotonic() - start) * 1000)
            return {
                "ok": True,
                "model": resp.model or provider.model,
                "latency_ms": elapsed_ms,
                "tokens_in": resp.tokens_in,
                "tokens_out": resp.tokens_out,
            }
        except LLMAPIConnectionError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "hint": (
                    "Could not reach the provider. Check that the base URL is "
                    "correct and the server is reachable from this network."
                ),
            }
        except LLMAPITimeout as exc:
            return {
                "ok": False,
                "error": str(exc),
                "hint": (
                    "The request timed out. The provider may be slow or a "
                    "firewall may be blocking the connection."
                ),
            }
        except LLMAPIStatusError as exc:
            if exc.status_code in (401, 403):
                hint = "Invalid API key. Verify the key is correct and has not expired."
            elif exc.status_code == 404:
                hint = "Endpoint not found. Check that the base URL is correct (e.g. OpenRouter needs /api/v1, OpenAI needs /v1)."
            elif exc.status_code == 429:
                hint = "Rate limited by the provider. Try again later."
            else:
                hint = f"Unexpected HTTP {exc.status_code}. Check the provider configuration."
            return {"ok": False, "error": str(exc), "hint": hint}
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "hint": "An unexpected error occurred. Check the provider configuration.",
            }
        finally:
            try:
                await adapter.close()
            except Exception:
                pass  # best-effort cleanup


# ── Routes ──────────────────────────────────────────────────────────

@app.get("/v1/routes")
async def list_routes(cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, principal: Principal = Depends(tenant_context)):
    from cante.models import Route
    async with async_session_factory() as session:
        page = await paginate(session, Route, cursor=cursor, limit=limit)
        return {
            "items": [{"id": r.id, "number_id": r.number_id, "bot_id": r.bot_id, "selector": r.selector, "selector_value": r.selector_value, "enabled": r.enabled} for r in page["items"]],
            "total": page["total"],
            "next_cursor": page["next_cursor"],
        }


@app.post("/v1/routes")
async def create_route(data: RouteCreateIn, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import Route
    async with async_session_factory() as session:
        r = Route(number_id=data.number_id, bot_id=data.bot_id, selector=data.selector, selector_value=data.selector_value, priority=data.priority)
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
async def list_contacts(
    cursor: str = "", limit: int = DEFAULT_PAGE_SIZE,
    search: str = "", principal: Principal = Depends(tenant_context),
):
    from cante.models import Contact
    from sqlalchemy import func, select

    async with async_session_factory() as session:
        limit = min(max(1, limit), MAX_PAGE_SIZE)
        col = Contact.last_seen

        # Count
        cnt_stmt = select(func.count(Contact.id))
        if search:
            esc = _escape_like(search)
            cnt_stmt = cnt_stmt.where(
                (Contact.name.ilike(f"%{esc}%", escape="\\"))
                | (Contact.phone.ilike(f"%{esc}%", escape="\\"))
            )
        total = (await session.execute(cnt_stmt)).scalar() or 0

        # Keyset
        stmt = select(Contact).order_by(col.desc()).limit(limit + 1)
        if search:
            esc = _escape_like(search)
            stmt = stmt.where(
                (Contact.name.ilike(f"%{esc}%", escape="\\"))
                | (Contact.phone.ilike(f"%{esc}%", escape="\\"))
            )
        if cursor:
            stmt = stmt.where(col < cursor)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        return {
            "items": [{"id": c.id, "phone": c.phone, "name": c.name, "attributes": c.attributes, "first_seen": str(c.first_seen), "last_seen": str(c.last_seen)} for c in rows],
            "total": total,
            "next_cursor": str(rows[-1].last_seen) if has_more and rows else None,
        }


@app.patch("/v1/contacts/{contact_id}")
async def update_contact(contact_id: str, data: ContactUpdateIn, principal: Principal = Depends(tenant_context)):
    from cante.models import Contact
    async with async_session_factory() as session:
        c = await load_owned(session, Contact, contact_id, principal)
        before = {"name": c.name, "attributes": c.attributes}
        if data.name is not None:
            c.name = data.name
        if data.attributes is not None:
            c.attributes = {**c.attributes, **data.attributes}
        await log_audit(session, principal, "contact.update", f"contact:{c.id}", before=before, after={"name": c.name, "attributes": c.attributes})
        await session.commit()
        return {"id": c.id, "name": c.name}


# ── Contact Groups ──────────────────────────────────────────────────

@app.get("/v1/groups")
async def list_groups(cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, principal: Principal = Depends(tenant_context)):
    from cante.models import ContactGroup
    async with async_session_factory() as session:
        page = await paginate(session, ContactGroup, cursor=cursor, limit=limit)
        return {
            "items": [{"id": g.id, "name": g.name, "number_id": g.number_id} for g in page["items"]],
            "total": page["total"],
            "next_cursor": page["next_cursor"],
        }


@app.post("/v1/groups")
async def create_group(data: GroupCreateIn, principal: Principal = Depends(tenant_context)):
    from cante.models import ContactGroup
    async with async_session_factory() as session:
        g = ContactGroup(name=data.name, number_id=data.number_id)
        session.add(g)
        await session.commit()
        return {"id": g.id, "name": g.name}


@app.post("/v1/groups/{group_id}/members")
async def add_member(group_id: str, data: GroupAddMemberIn, principal: Principal = Depends(tenant_context)):
    from cante.models import GroupMembership
    async with async_session_factory() as session:
        m = GroupMembership(contact_id=data.contact_id, group_id=group_id)
        session.add(m)
        await session.commit()
        return {"status": "added"}


# ── Conversations ────────────────────────────────────────────────────

@app.get("/v1/conversations")
async def list_conversations(
    cursor: str = "", limit: int = DEFAULT_PAGE_SIZE,
    state: str = "", bot_id: str = "", number_id: str = "",
    principal: Principal = Depends(tenant_context),
):
    from cante.models import Conversation
    from sqlalchemy import func, select

    async with async_session_factory() as session:
        limit = min(max(1, limit), MAX_PAGE_SIZE)
        col = Conversation.last_activity_at

        # Count
        cnt_stmt = select(func.count(Conversation.id))
        if state:
            cnt_stmt = cnt_stmt.where(Conversation.state == state)
        if bot_id:
            cnt_stmt = cnt_stmt.where(Conversation.bot_id == bot_id)
        if number_id:
            cnt_stmt = cnt_stmt.where(Conversation.number_id == number_id)
        total = (await session.execute(cnt_stmt)).scalar() or 0

        # Keyset
        stmt = select(Conversation).order_by(col.desc()).limit(limit + 1)
        if state:
            stmt = stmt.where(Conversation.state == state)
        if bot_id:
            stmt = stmt.where(Conversation.bot_id == bot_id)
        if number_id:
            stmt = stmt.where(Conversation.number_id == number_id)
        if cursor:
            stmt = stmt.where(col < cursor)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        return {
            "items": [{"id": c.id, "state": c.state, "language_detected": c.language_detected, "contact_id": c.contact_id, "bot_id": c.bot_id, "number_id": c.number_id, "last_activity_at": str(c.last_activity_at), "started_at": str(c.started_at)} for c in rows],
            "total": total,
            "next_cursor": str(rows[-1].last_activity_at) if has_more and rows else None,
        }


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
    """C21: takeover sets human_active so the worker backs off."""
    from cante.models import Conversation
    async with async_session_factory() as session:
        conv = await load_owned(session, Conversation, conv_id, principal)
        before = {"state": conv.state}
        conv.state = "human_active"
        await log_audit(session, principal, "conversation.takeover", f"conversation:{conv.id}", before=before, after={"state": conv.state})
        await session.commit()
        return {"id": conv.id, "state": conv.state}


@app.post("/v1/conversations/{conv_id}/send")
async def send_as_human(conv_id: str, data: SendAsHumanIn, principal: Principal = Depends(tenant_context)):
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
        await bus.publish("stream:outbound", {"conversation_id": conv_id, "from_phone": "", "number_phone": number_phone, "body": data.body})
        await log_audit(session, principal, "conversation.send_as_human", f"conversation:{conv.id}", after={"body": data.body, "number_phone": number_phone})
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
async def list_learnings(cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, status: str = "", principal: Principal = Depends(tenant_context)):
    from cante.models import Learning
    from sqlalchemy import func, select

    async with async_session_factory() as session:
        limit = min(max(1, limit), MAX_PAGE_SIZE)
        col = Learning.created_at

        cnt_stmt = select(func.count(Learning.id))
        if status:
            cnt_stmt = cnt_stmt.where(Learning.status == status)
        total = (await session.execute(cnt_stmt)).scalar() or 0

        stmt = select(Learning).order_by(col.desc()).limit(limit + 1)
        if status:
            stmt = stmt.where(Learning.status == status)
        if cursor:
            stmt = stmt.where(col < cursor)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        return {
            "items": [
                {
                    "id": learning.id,
                    "type": learning.type,
                    "suggestion_md": learning.suggestion_md[:200],
                    "category": learning.category,
                    "status": learning.status,
                }
                for learning in rows
            ],
            "total": total,
            "next_cursor": str(rows[-1].created_at) if has_more and rows else None,
        }


@app.post("/v1/learnings/{learning_id}/approve")
async def approve_learning(learning_id: str, principal: Principal = Depends(tenant_context)):
    from cante.models import Learning
    async with async_session_factory() as session:
        learning = await load_owned(session, Learning, learning_id, principal)
        before = {"status": learning.status}
        learning.status = "approved"
        learning.reviewed_by = principal.user_id
        await log_audit(
            session, principal, "learning.approve",
            f"learning:{learning.id}",
            before=before, after={"status": learning.status},
        )
        await session.commit()
        return {"id": learning.id, "status": "approved"}


@app.post("/v1/learnings/{learning_id}/reject")
async def reject_learning(learning_id: str, principal: Principal = Depends(tenant_context)):
    from cante.models import Learning
    async with async_session_factory() as session:
        learning = await load_owned(session, Learning, learning_id, principal)
        before = {"status": learning.status}
        learning.status = "rejected"
        learning.reviewed_by = principal.user_id
        await log_audit(
            session, principal, "learning.reject",
            f"learning:{learning.id}",
            before=before, after={"status": learning.status},
        )
        await session.commit()
        return {"id": learning.id, "status": "rejected"}


# ── Metrics ──────────────────────────────────────────────────────────

@app.get("/v1/metrics/overview")
async def metrics_overview(principal: Principal = Depends(tenant_context)):
    """Single-query dashboard counters (C9 — collapsed from 7 queries to 1)."""
    from cante.models import Bot, Conversation, Message, Number
    from sqlalchemy import case, func, select

    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(
                    func.count(Conversation.id).label("total_conversations"),
                    func.count(
                        case((Conversation.state == "needs_human", 1))
                    ).label("escalated"),
                    func.count(
                        case((Conversation.state == "active", 1))
                    ).label("active"),
                    func.count(
                        case((Conversation.state == "closed", 1))
                    ).label("closed"),
                    select(func.count(Bot.id))
                    .correlate(None)
                    .scalar_subquery()
                    .label("total_bots"),
                    select(func.count(Number.id))
                    .correlate(None)
                    .scalar_subquery()
                    .label("total_numbers"),
                    select(func.count(Message.id))
                    .correlate(None)
                    .scalar_subquery()
                    .label("total_messages"),
                )
            )
        ).one()
        return {
            "total_conversations": row.total_conversations or 0,
            "escalated": row.escalated or 0,
            "active": row.active or 0,
            "closed": row.closed or 0,
            "total_bots": row.total_bots or 0,
            "total_numbers": row.total_numbers or 0,
            "total_messages": row.total_messages or 0,
        }


# ── Audit ───────────────────────────────────────────────────────────

@app.get("/v1/audit")
async def list_audit(cursor: str = "", limit: int = DEFAULT_PAGE_SIZE, principal: Principal = Depends(RequireRole("admin"))):
    from cante.models import AuditLog
    async with async_session_factory() as session:
        page = await paginate(session, AuditLog, cursor=cursor, limit=limit)
        return {
            "items": [{"id": a.id, "actor": a.actor, "action": a.action, "entity": a.entity, "created_at": str(a.created_at)} for a in page["items"]],
            "total": page["total"],
            "next_cursor": page["next_cursor"],
        }


# ── Triggers ────────────────────────────────────────────────────────

@app.post("/v1/triggers")
async def create_trigger(data: TriggerCreateIn, request: Request):
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
    await bus.publish("stream:triggers", {"conversation_id": data.conversation_id, "from_phone": data.to_phone, "number_phone": data.from_number, "body": data.body})
    return {"status": "queued"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.api.main:app", host="0.0.0.0", port=8000, reload=False)
