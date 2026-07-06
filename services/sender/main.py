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

import structlog

from cante.bus import RedisStreamsBus
from cante.db import async_session_factory
from cante.evolution import EvolutionAdapter
from cante.redis import get_redis
from cante.settings import settings

logger = structlog.get_logger(__name__)
running = True
S_OUT, GROUP, CONSUMER = "stream:outbound", "senders", "sender-1"


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


async def main():
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    redis = await get_redis()
    bus = RedisStreamsBus(redis)
    await bus.create_group(S_OUT, GROUP)
    channel = EvolutionAdapter()
    logger.info("sender.started")
    while running:
        try:
            for e in await bus.consume(
                S_OUT, GROUP, CONSUMER, count=5, block_ms=5000
            ):
                await send_one(e.data, channel)
                await bus.ack(S_OUT, GROUP, e.id)
        except Exception as e:
            logger.error("sender.loop", error=str(e))
            await asyncio.sleep(2)
    logger.info("sender.stopped")


if __name__ == "__main__":
    asyncio.run(main())
