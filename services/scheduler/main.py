"""Cante scheduler — leader-elected singleton, daily learning job."""
import asyncio, signal, structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from cante.redis import get_redis

logger = structlog.get_logger(__name__)
running = True
scheduler = AsyncIOScheduler()

def _sigterm(*_):
    global running; running = False

async def _daily_learning():
    logger.info("scheduler.daily_learning_stub")

async def main():
    signal.signal(signal.SIGTERM, _sigterm); signal.signal(signal.SIGINT, _sigterm)
    redis = await get_redis()
    is_leader = await redis.set("lock:scheduler:leader", "1", nx=True, ex=120)
    if is_leader:
        scheduler.add_job(_daily_learning, "cron", hour=4, minute=0, id="daily_learning")
        scheduler.start()
        logger.info("scheduler.started_leader")
    else:
        logger.info("scheduler.started_follower")
    while running:
        await asyncio.sleep(10)
        if is_leader:
            await redis.set("lock:scheduler:leader", "1", nx=False, ex=120)
    if is_leader:
        scheduler.shutdown(wait=True)
    logger.info("scheduler.stopped")

if __name__ == "__main__":
    asyncio.run(main())
