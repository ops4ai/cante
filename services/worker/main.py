"""Cante worker — consumes stream:inbound, runs agent loop, publishes stream:outbound."""
import asyncio, signal, structlog
from cante.bus import RedisStreamsBus
from cante.redis import get_redis
from cante.settings import settings

logger = structlog.get_logger(__name__)
running = True
S_IN, S_OUT, S_TRIG = "stream:inbound", "stream:outbound", "stream:triggers"
GROUP, CONSUMER = "agent-workers", "worker-1"

def _sigterm(*_):
    global running; running = False

async def agent(data: dict) -> str:
    body = data.get("body", "")
    return f"[Cante M1 echo] Recebi: {body[:400]}"

async def process(entry, bus, redis):
    data = entry.data
    cid = data.get("from_phone", "unknown")
    lock = f"lock:conv:{cid}"
    if not await redis.set(lock, "1", nx=True, ex=60):
        return
    try:
        await asyncio.sleep(settings.debounce_ms_default / 1000.0)
        reply = await agent(data)
        await bus.publish(S_OUT, {"conversation_id": cid, "from_phone": data.get("from_phone",""),
                                   "number_phone": data.get("number_phone",""), "body": reply})
        logger.info("worker.processed", conv=cid)
    except Exception as e:
        logger.error("worker.error", error=str(e))
    finally:
        await redis.delete(lock)

async def main():
    signal.signal(signal.SIGTERM, _sigterm); signal.signal(signal.SIGINT, _sigterm)
    redis = await get_redis(); bus = RedisStreamsBus(redis)
    await bus.create_group(S_IN, GROUP); await bus.create_group(S_TRIG, GROUP)
    logger.info("worker.started")
    while running:
        try:
            for e in await bus.consume(S_IN, GROUP, CONSUMER, count=5, block_ms=5000):
                await process(e, bus, redis); await bus.ack(S_IN, GROUP, e.id)
            for e in await bus.consume(S_TRIG, GROUP, CONSUMER, count=2, block_ms=1000):
                await process(e, bus, redis); await bus.ack(S_TRIG, GROUP, e.id)
        except Exception as e:
            logger.error("worker.loop", error=str(e)); await asyncio.sleep(2)
    logger.info("worker.stopped")

if __name__ == "__main__":
    asyncio.run(main())
