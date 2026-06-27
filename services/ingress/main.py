"""Cante ingress — receives channel webhooks, dedup, filter, enqueue."""

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cante.bus import RedisStreamsBus
from cante.channel import InboundMessage
from cante.redis import get_redis
from cante.settings import settings

logger = structlog.get_logger(__name__)
app = FastAPI(title="Cante Ingress", version="0.1.0")


def _normalize(raw: str) -> str:
    for s in ("@s.whatsapp.net", "@g.us", "@c.us"):
        raw = raw.replace(s, "")
    return raw


@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "ingress"}


@app.post("/channels/{channel_id}/webhook")
async def webhook(channel_id: str, request: Request):
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    redis = await get_redis()
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
        if m: msgs.append(m)
    arr = raw.get("messages", raw.get("data", {}).get("messages", []))
    if isinstance(arr, list):
        for item in arr:
            m = _extract(item, channel_id)
            if m: msgs.append(m)
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
