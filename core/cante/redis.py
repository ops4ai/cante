"""Redis client factory — single connection for the app lifetime, with auto-recovery."""

import redis.asyncio as aioredis

from cante.settings import settings

_redis: aioredis.Redis | None = None
_consecutive_timeouts: int = 0
MAX_CONSECUTIVE_TIMEOUTS = 3


async def get_redis() -> aioredis.Redis:
    """Return the shared Redis connection, creating it if needed.

    Connection uses ``socket_timeout=None`` so that blocking stream operations
    (xreadgroup with ``block``) are not killed by a socket-level timeout.
    """
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=None,
            socket_connect_timeout=5,
        )
    return _redis


async def health_redis() -> dict:
    """Check Redis connectivity. Returns {"ok": True, "latency_ms": ...} or {"ok": False, "error": ...}."""
    import time
    try:
        r = await get_redis()
        t0 = time.monotonic()
        await r.ping()
        latency = (time.monotonic() - t0) * 1000
        return {"ok": True, "latency_ms": round(latency, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def on_redis_timeout() -> bool:
    """Call after a Redis TimeoutError. Returns True if the connection was reset.

    After MAX_CONSECUTIVE_TIMEOUTS consecutive timeouts, the shared connection
    is discarded so the next ``get_redis()`` call creates a fresh one.
    """
    global _redis, _consecutive_timeouts
    _consecutive_timeouts += 1
    if _consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
        _redis = None
        _consecutive_timeouts = 0
        return True
    return False


def on_redis_success():
    """Call after a successful Redis operation to reset the timeout counter."""
    global _consecutive_timeouts
    _consecutive_timeouts = 0
