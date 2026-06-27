"""Cante API — backoffice control plane (FastAPI)."""
import structlog
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError

from cante.auth import create_token, decode_token, hash_password, verify_password
from cante.db import async_session_factory
from cante.settings import settings

logger = structlog.get_logger(__name__)
app = FastAPI(title="Cante API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
security = HTTPBearer()


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            raise HTTPException(401, "Invalid token type")
        return payload
    except JWTError:
        raise HTTPException(401, "Invalid token")


class RequireRole:
    def __init__(self, role: str):
        self.role = role

    async def __call__(self, user: dict = Depends(get_current_user)):
        if user.get("role") != self.role and user.get("role") != "admin":
            raise HTTPException(403, "Insufficient permissions")
        return user


@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "api"}


# ── Auth ────────────────────────────────────────────────────────────

@app.post("/v1/auth/login")
async def login(data: dict):
    """Login with email + password, return JWT tokens."""
    from cante.models import User
    from sqlalchemy import select

    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == data["email"]))
        user = result.scalar_one_or_none()
        if not user or not verify_password(data["password"], user.hashed_password):
            raise HTTPException(401, "Invalid credentials")

        access, refresh = create_token(user.id, user.role)
        return {"access_token": access, "refresh_token": refresh, "user": {"id": user.id, "email": user.email, "role": user.role}}


@app.get("/v1/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"id": user["sub"], "role": user["role"]}


# ── Numbers (WhatsApp connections) ──────────────────────────────────

@app.get("/v1/numbers")
async def list_numbers(user: dict = Depends(get_current_user)):
    from cante.models import Number
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Number).limit(50))
        return [{"id": n.id, "phone": n.phone, "status": n.status, "display_name": n.display_name} for n in result.scalars().all()]


@app.post("/v1/numbers")
async def create_number(data: dict, user: dict = Depends(get_current_user)):
    from cante.models import Number
    async with async_session_factory() as session:
        n = Number(phone=data["phone"], display_name=data.get("display_name", ""))
        session.add(n)
        await session.commit()
        return {"id": n.id, "phone": n.phone, "status": n.status}


# ── Bots ────────────────────────────────────────────────────────────

@app.get("/v1/bots")
async def list_bots(user: dict = Depends(get_current_user)):
    from cante.models import Bot
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Bot).limit(50))
        return [{"id": b.id, "name": b.name, "type_label": b.type_label, "enabled": b.enabled} for b in result.scalars().all()]


@app.post("/v1/bots")
async def create_bot(data: dict, user: dict = Depends(get_current_user)):
    from cante.models import Bot
    async with async_session_factory() as session:
        b = Bot(name=data["name"], skill_id=data["skill_id"], provider_id=data["provider_id"], type_label=data.get("type_label", "custom"))
        session.add(b)
        await session.commit()
        return {"id": b.id, "name": b.name}


# ── Skills ──────────────────────────────────────────────────────────

@app.get("/v1/skills")
async def list_skills(user: dict = Depends(get_current_user)):
    from cante.models import Skill
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Skill).limit(50))
        return [{"id": s.id, "name": s.name, "preset": s.preset, "language_default": s.language_default} for s in result.scalars().all()]


@app.post("/v1/skills")
async def create_skill(data: dict, user: dict = Depends(get_current_user)):
    from cante.models import Skill
    async with async_session_factory() as session:
        s = Skill(name=data["name"], preset=data.get("preset", "custom"), playbook_md=data.get("playbook_md", ""))
        session.add(s)
        await session.commit()
        return {"id": s.id, "name": s.name}


# ── Providers (LLM) ─────────────────────────────────────────────────

@app.get("/v1/providers")
async def list_providers(user: dict = Depends(get_current_user)):
    from cante.models import Provider
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Provider).limit(50))
        return [{"id": p.id, "name": p.name, "type": p.type, "model": p.model, "enabled": p.enabled} for p in result.scalars().all()]


@app.post("/v1/providers")
async def create_provider(data: dict, user: dict = Depends(get_current_user)):
    from cante.models import Provider
    async with async_session_factory() as session:
        p = Provider(name=data["name"], type=data["type"], base_url=data["base_url"], model=data["model"], api_key_ref=data["api_key_ref"])
        session.add(p)
        await session.commit()
        return {"id": p.id, "name": p.name}


# ── Conversations ────────────────────────────────────────────────────

@app.get("/v1/conversations")
async def list_conversations(
    state: str = "", bot_id: str = "", number_id: str = "",
    user: dict = Depends(get_current_user),
):
    from cante.models import Conversation
    from sqlalchemy import select
    async with async_session_factory() as session:
        stmt = select(Conversation).order_by(Conversation.last_activity_at.desc()).limit(50)
        if state:
            stmt = stmt.where(Conversation.state == state)
        if bot_id:
            stmt = stmt.where(Conversation.bot_id == bot_id)
        result = await session.execute(stmt)
        return [{"id": c.id, "state": c.state, "language_detected": c.language_detected, "last_activity_at": str(c.last_activity_at)} for c in result.scalars().all()]


@app.post("/v1/conversations/{conv_id}/takeover")
async def takeover(conv_id: str, user: dict = Depends(get_current_user)):
    """Take over a conversation as a human operator."""
    from cante.models import Conversation
    from sqlalchemy import select
    async with async_session_factory() as session:
        result = await session.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one_or_none()
        if not conv:
            raise HTTPException(404, "Conversation not found")
        conv.state = "active"  # Human now active
        await session.commit()
        return {"id": conv.id, "state": conv.state}


# ── Metrics ──────────────────────────────────────────────────────────

@app.get("/v1/metrics/overview")
async def metrics_overview(user: dict = Depends(get_current_user)):
    from cante.models import Bot, Conversation, Number
    from sqlalchemy import func, select
    async with async_session_factory() as session:
        total_conv = (await session.execute(select(func.count(Conversation.id)))).scalar() or 0
        escalated = (await session.execute(select(func.count(Conversation.id)).where(Conversation.state == "needs_human"))).scalar() or 0
        total_bots = (await session.execute(select(func.count(Bot.id)))).scalar() or 0
        total_numbers = (await session.execute(select(func.count(Number.id)))).scalar() or 0
        return {"total_conversations": total_conv, "escalated": escalated, "total_bots": total_bots, "total_numbers": total_numbers}


# ── Triggers (proactive) ─────────────────────────────────────────────

@app.post("/v1/triggers")
async def create_trigger(data: dict, request: Request):
    """Enqueue an outbound-initiated conversation. API-key auth."""
    api_key = request.headers.get("X-API-Key", "")
    if api_key != settings.jwt_secret:  # Use JWT secret as API key for v1 simplicity
        raise HTTPException(401, "Invalid API key")

    from cante.bus import RedisStreamsBus
    from cante.redis import get_redis
    redis = await get_redis()
    bus = RedisStreamsBus(redis)
    await bus.publish("stream:triggers", {
        "from_phone": data.get("to_phone", ""),
        "number_phone": data.get("from_number", ""),
        "body": data.get("body", ""),
        "conversation_id": data.get("conversation_id", ""),
    })
    return {"status": "queued"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.api.main:app", host="0.0.0.0", port=8000, reload=False)
