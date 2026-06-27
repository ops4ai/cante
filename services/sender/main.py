"""Cante sender — consumes stream:outbound, paces, sends."""
import asyncio, signal, random, structlog
from cante.bus import RedisStreamsBus
from cante.redis import get_redis
from cante.settings import settings

logger = structlog.get_logger(__name__)
running = True
S_OUT, GROUP, CONSUMER = "stream:outbound", "senders", "sender-1"

def _sigterm(*_):
    global running; running = False

class FakeChannel:
    async def send_text(self, cfg, to, text):
        logger.info("sender.fake_send", to=to, text=text[:200])
        return type("X",(),{"provider_message_id":f"fake_{random.randint(0,99999)}","channel":"fake"})()

async def send_one(data, channel):
    delay = random.uniform(settings.send_delay_min_s, settings.send_delay_max_s)
    await asyncio.sleep(delay)
    return await channel.send_text({}, data.get("from_phone",""), data.get("body",""))

async def main():
    signal.signal(signal.SIGTERM, _sigterm); signal.signal(signal.SIGINT, _sigterm)
    redis = await get_redis(); bus = RedisStreamsBus(redis)
    await bus.create_group(S_OUT, GROUP)
    channel = FakeChannel()
    logger.info("sender.started")
    while running:
        try:
            for e in await bus.consume(S_OUT, GROUP, CONSUMER, count=5, block_ms=5000):
                await send_one(e.data, channel); await bus.ack(S_OUT, GROUP, e.id)
        except Exception as e:
            logger.error("sender.loop", error=str(e)); await asyncio.sleep(2)
    logger.info("sender.stopped")

if __name__ == "__main__":
    asyncio.run(main())
