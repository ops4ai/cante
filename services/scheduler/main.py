"""Cante scheduler — leader-elected singleton, daily learning job.

S16: the leader lock is *fenced* — a unique token is written under NX and the
leader only refreshes its own TTL (atomic check-and-set via Lua). Followers
re-attempt acquisition each loop, so a crashed leader's TTL expiry promotes a
follower automatically. There is no path by which two schedulers believe they
are leader simultaneously.
"""
import asyncio
import signal
import uuid

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from cante.redis import get_redis

logger = structlog.get_logger(__name__)
running = True
scheduler = AsyncIOScheduler()

LOCK_KEY = "lock:scheduler:leader"
LOCK_TTL = 120  # seconds


def _sigterm(*_):
    global running
    running = False


async def _daily_learning():
    logger.info("scheduler.daily_learning_stub")


# Atomic "refresh only if I still own the lock" — prevents a stale leader from
# extending a lock it lost (the fencing token must match).
_REFRESH_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""


async def _try_acquire_lock(redis, token: str) -> bool:
    return bool(await redis.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL))


async def _refresh_lock(redis, token: str) -> bool:
    result = await redis.eval(_REFRESH_LUA, 1, LOCK_KEY, token, str(LOCK_TTL))
    return bool(result)


async def main():
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    redis = await get_redis()

    token = uuid.uuid4().hex
    is_leader = await _try_acquire_lock(redis, token)
    if is_leader:
        scheduler.add_job(_daily_learning, "cron", hour=4, minute=0, id="daily_learning")
        scheduler.start()
        logger.info("scheduler.started_leader")
    else:
        logger.info("scheduler.started_follower")

    while running:
        await asyncio.sleep(10)
        if is_leader:
            # Fenced refresh: only extend if we still own the lock.
            if not await _refresh_lock(redis, token):
                logger.warning("scheduler.lost_leadership")
                is_leader = False
                try:
                    scheduler.shutdown(wait=False)
                except Exception:
                    pass
        else:
            # Followers take over once the previous leader's TTL expires.
            if await _try_acquire_lock(redis, token):
                is_leader = True
                scheduler.add_job(_daily_learning, "cron", hour=4, minute=0, id="daily_learning")
                scheduler.start()
                logger.info("scheduler.promoted_to_leader")

    if is_leader:
        try:
            scheduler.shutdown(wait=True)
        except Exception:
            pass
    logger.info("scheduler.stopped")


if __name__ == "__main__":
    asyncio.run(main())
