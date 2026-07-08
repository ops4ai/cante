"""Cante sender — consumes stream:outbound, paces, sends via the channel.

Resolves the outbound Number's connection_config (by channel_id, the Number
UUID published by the worker) and sends the reply through the real channel
adapter (Evolution API for whatsapp_evolution). Falls back to a logged
FakeChannel only when no channel can be resolved, so a misconfiguration is
visible in logs rather than silently dropping messages.
"""
import asyncio
import random
import signal
import time

import structlog

from cante.bus import RedisStreamsBus
from cante.db import async_session_factory
from cante.evolution import EvolutionAdapter
from cante.redis import get_redis, health_redis, on_redis_timeout, on_redis_success
from cante.settings import settings

logger = structlog.get_logger(__name__)
running = True
S_OUT, GROUP, CONSUMER = "stream:outbound", "senders", "sender-1"
_last_queue_check = 0.0
QUEUE_STALE_MINUTES = 5


def _sigterm(*_):
    global running
    running = False


async def _resolve_channel_config(channel_id: str, number_phone: str) -> dict | None:
    """Load the Number's connection_config so the adapter knows the instance."""
    from cante.models import Number
    from cante.tenant import with_bypass
    from sqlalchemy import or_, select

    if not (channel_id or number_phone):
        return None
    async with async_session_factory() as session:
        with with_bypass():
            stmt = select(Number)
            if channel_id:
                stmt = stmt.where(or_(Number.id == str(channel_id), Number.phone == number_phone))
            else:
                stmt = stmt.where(Number.phone == number_phone)
            number = (await session.execute(stmt)).scalars().first()
            return dict(number.connection_config or {}) if number else None


async def send_one(data: dict, channel: EvolutionAdapter):
    """Pace, resolve the channel config, and send the reply."""
    delay = random.uniform(settings.send_delay_min_s, settings.send_delay_max_s)
    await asyncio.sleep(delay)
    cfg = await _resolve_channel_config(
        data.get("channel_id", ""),
        data.get("number_phone", ""),
    )
    if not cfg:
        logger.error(
            "sender.no_number_config",
            channel_id=data.get("channel_id", ""),
            number_phone=data.get("number_phone", ""),
            body=str(data.get("body", ""))[:200],
        )
        return
    to = data.get("from_phone", "")
    text = data.get("body", "")
    if not to or not text:
        logger.warning("sender.empty", to=to, has_body=bool(text))
        return
    try:
        await channel.send_text(cfg, to, text)
        logger.info("sender.sent", to=to, instance=cfg.get("instance", ""))
    except Exception as e:
        logger.error("sender.send_failed", to=to, error=str(e))


async def _check_queue_health(bus):
    """Check whether the consumer group has unacked (pending) entries sitting too long.

    The raw stream ``xlen``/``xrange`` is NOT a staleness signal: acked entries
    linger in the stream until trimmed, so the oldest raw entry can be hours old
    while nothing is actually stuck. The real signal is the consumer-group PEL —
    entries delivered but not acked. We report those only.
    """
    global _last_queue_check
    now = time.monotonic()
    if now - _last_queue_check < 60:
        return
    _last_queue_check = now
    try:
        pending, oldest = await bus.pending_oldest(S_OUT, GROUP)
        if pending > 0 and oldest:
            ms_timestamp = int(oldest.split("-")[0])
            age_minutes = (int(time.time() * 1000) - ms_timestamp) / 60000
            if age_minutes > QUEUE_STALE_MINUTES:
                logger.warning(
                    "sender.queue_stale",
                    queue=S_OUT,
                    pending=pending,
                    oldest_age_minutes=round(age_minutes, 1),
                )
    except Exception:
        pass  # best-effort monitoring



async def main():
    global running
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    redis = await get_redis()
    bus = RedisStreamsBus(redis)
    await bus.create_group(S_OUT, GROUP)
    channel = EvolutionAdapter()
    logger.info("sender.started")

    # ── Health check (stdlib TCP, no extra dependencies) ──────────────
    async def health_server():
        async def handler(reader, writer):
            try:
                redis_ok = await health_redis()
                status = "200" if redis_ok["ok"] else "503"
                body = f'{{"service":"sender","redis":{redis_ok}}}'
                writer.write(f"HTTP/1.1 {status} OK\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode())
                await writer.drain()
            except Exception:
                writer.write(b"HTTP/1.1 503\r\n\r\n")
            writer.close()

        server = await asyncio.start_server(handler, "0.0.0.0", 9000)
        logger.info("sender.healthz_started", port=9000)
        async with server:
            await server.serve_forever()

    _health_task = asyncio.create_task(health_server())

    while running:
        try:
            events = await bus.consume(
                S_OUT, GROUP, CONSUMER, count=5, block_ms=5000
            )
            for e in events:
                await send_one(e.data, channel)
                await bus.ack(S_OUT, GROUP, e.id)
            if events:
                # Housekeeping: drop acked entries so the stream doesn't
                # accumulate dead replies (which would otherwise make a raw
                # xlen/xrange staleness check report phantom old messages).
                await bus.trim_delivered(S_OUT, GROUP)
            on_redis_success()
        except Exception as e:
            err = str(e)
            logger.error("sender.loop", error=err)
            if "Timeout" in err:
                reset = on_redis_timeout()
                if reset:
                    logger.warning("sender.redis_reset", reason="consecutive_timeouts")
                    redis = await get_redis()
                    bus = RedisStreamsBus(redis)
                    await bus.create_group(S_OUT, GROUP)
            await asyncio.sleep(2)

        await _check_queue_health(bus)

    _health_task.cancel()
    logger.info("sender.stopped")


if __name__ == "__main__":
    asyncio.run(main())
