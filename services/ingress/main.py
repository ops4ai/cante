"""Cante ingress — receives channel webhooks, dedup, filter, enqueue.

S2: every webhook is authenticated against a per-channel shared secret stored
in ``Number.connection_config['webhook_secret']`` (HMAC of the raw body, or a
plain ``X-Webhook-Token``), the ``channel_id`` is validated to a real Number,
and callers are per-IP rate-limited.
"""

import hashlib
import hmac
import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cante.bus import RedisStreamsBus
from cante.channel import InboundMessage
from cante.db import async_session_factory
from cante.redis import get_redis
from cante.settings import settings
from cante.tenant import with_bypass

logger = structlog.get_logger(__name__)
app = FastAPI(title="Cante Ingress", version="0.1.0")

# Per-IP webhook rate limit (S2): 120 requests / 60s.
_WEBHOOK_MAX_PER_MIN = 120


def _normalize(raw: str) -> str:
    for s in ("@s.whatsapp.net", "@g.us", "@c.us"):
        raw = raw.replace(s, "")
    return raw


@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "ingress"}


def _client_ip(request: Request) -> str:
    # Trust X-Forwarded-For only when set (edge proxy sets it); else socket peer.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    return (request.client.host if request.client else "unknown") or "unknown"


async def _rate_limited(redis, ip: str) -> bool:
    key = f"webhook:ip:{ip}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    return count > _WEBHOOK_MAX_PER_MIN


async def _resolve_channel_secret(channel_id: str) -> str | None:
    """Return the configured webhook secret for *channel_id* (a Number.id).

    Returns None if the channel does not exist or has no secret configured
    (both refused at the call site — fail-closed).
    """
    from cante.models import Number
    from sqlalchemy import select

    try:
        uuid.UUID(str(channel_id))
    except (ValueError, TypeError):
        return None

    async with async_session_factory() as session:
        # System lookup, not user-scoped — bypass the tenant filter (the
        # webhook has no principal yet; the worker binds the tenant downstream).
        with with_bypass():
            result = await session.execute(select(Number).where(Number.id == str(channel_id)))
            number = result.scalar_one_or_none()
    if number is None:
        return None
    cfg = number.connection_config or {}
    secret = cfg.get("webhook_secret") or ""
    return secret or None


def _verify_signature(raw_body: bytes, secret: str, request: Request) -> bool:
    """Verify the webhook against *secret* via HMAC or a plain token."""
    token = request.headers.get("x-webhook-token", "")
    if token:
        return hmac.compare_digest(token, secret)
    signature = request.headers.get("x-webhook-signature", "")
    if signature:
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    return False


@app.post("/channels/{channel_id}/webhook")
async def webhook(channel_id: str, request: Request):
    redis = await get_redis()

    # S2: per-IP rate limit.
    ip = _client_ip(request)
    if await _rate_limited(redis, ip):
        return JSONResponse({"error": "rate limited"}, status_code=429)

    # S2: authenticate the webhook against the channel's shared secret.
    secret = await _resolve_channel_secret(channel_id)
    if secret is None:
        # Unknown channel or no secret configured → refuse, no detail leak.
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    raw_body = await request.body()
    if not _verify_signature(raw_body, secret, request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    bus = RedisStreamsBus(redis)
    messages = _parse(raw, channel_id)

    for msg in messages:
        key = f"dedup:wa:{msg.channel_message_id}"
        if not await redis.set(key, "1", nx=True, ex=settings.wa_dedup_ttl_seconds):
            continue
        if msg.meta.get("from_me") or msg.meta.get("is_group") or not msg.body:
            continue
        await bus.publish("stream:inbound", {
            "channel_message_id": msg.channel_message_id,
            "channel_type": msg.channel_type,
            "channel_id": channel_id,
            "number_phone": msg.number_phone,
            "from_phone": msg.from_phone,
            "body": msg.body,
            "meta": str(msg.meta),
        })
        logger.info("ingress.enqueued", msg_id=msg.channel_message_id)

    return JSONResponse({"status": "ok", "count": len(messages)}, status_code=200)


def _parse(raw: dict, channel_id: str) -> list[InboundMessage]:
    msgs = []
    data = raw.get("data", raw)
    if "key" in data:
        m = _extract(data, channel_id)
        if m:
            msgs.append(m)
    arr = raw.get("messages", raw.get("data", {}).get("messages", []))
    if isinstance(arr, list):
        for item in arr:
            m = _extract(item, channel_id)
            if m:
                msgs.append(m)
    return msgs


def _extract(data: dict, channel_id: str) -> InboundMessage | None:
    key = data.get("key", {})
    msg_data = data.get("message", data)
    msg_id = key.get("id", data.get("id", ""))
    if not msg_id:
        return None
    from_phone = _normalize(key.get("remoteJid", data.get("from", "")))
    if not from_phone:
        return None
    number_phone = _normalize(key.get("server", data.get("instanceId", channel_id)))
    body = ""
    if isinstance(msg_data, dict):
        mt = msg_data.get("messageType", "conversation")
        if mt == "conversation":
            body = msg_data.get("conversation", "")
        elif mt == "extendedTextMessage":
            body = msg_data.get("extendedTextMessage", {}).get("text", "")
        else:
            body = str(msg_data)[:500]
    return InboundMessage(
        channel_message_id=msg_id, channel_type="whatsapp_evolution",
        number_phone=number_phone, from_phone=from_phone, body=body,
        meta={"from_me": key.get("fromMe", False), "is_group": "@g.us" in key.get("remoteJid", "")},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("services.ingress.main:app", host="0.0.0.0", port=8001, reload=False)
