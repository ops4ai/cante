"""C5 — bus error handling: round-trip, NOGROUP recovery, no silent busy-loop."""

import pytest
import redis.exceptions

from cante.bus import RedisStreamsBus


@pytest.fixture
async def bus():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield RedisStreamsBus(client)
    await client.aclose()


@pytest.mark.asyncio
async def test_publish_consume_ack_roundtrip(bus):
    await bus.create_group("s", "g")
    entry_id = await bus.publish("s", {"body": "hi", "from": "x"})
    entries = await bus.consume("s", "g", "c1", count=5, block_ms=100)
    assert len(entries) == 1
    assert entries[0].id == entry_id
    assert entries[0].data["body"] == "hi"

    # After ack, a second consume returns nothing.
    await bus.ack("s", "g", entry_id)
    again = await bus.consume("s", "g", "c1", count=5, block_ms=100)
    assert again == []


@pytest.mark.asyncio
async def test_nogroup_recovery(bus):
    # Consume without first creating the group: redis raises NOGROUP, the bus
    # recreates the group and returns [] instead of crashing.
    entries = await bus.consume("s", "g", "c1", count=5, block_ms=100)
    assert entries == []
    # After recovery, publish+consume works.
    await bus.publish("s", {"body": "after-recovery"})
    entries = await bus.consume("s", "g", "c1", count=5, block_ms=100)
    assert len(entries) == 1
    assert entries[0].data["body"] == "after-recovery"


@pytest.mark.asyncio
async def test_create_group_idempotent(bus):
    await bus.create_group("s", "g")
    await bus.create_group("s", "g")  # BUSYGROUP — must not raise
    await bus.publish("s", {"x": "1"})
    entries = await bus.consume("s", "g", "c1", count=5, block_ms=100)
    assert len(entries) == 1


class _DownRedis:
    """A redis-like client whose stream ops always fail (simulates redis down)."""

    async def xreadgroup(self, *a, **k):
        raise redis.exceptions.ConnectionError("redis is down")

    async def xadd(self, *a, **k):
        raise redis.exceptions.ConnectionError("redis is down")


@pytest.mark.asyncio
async def test_consume_raises_when_redis_down_no_busy_loop():
    bus = RedisStreamsBus(_DownRedis())
    # The caller relies on this raising so its backoff runs — a silent []
    # would busy-loop forever hammering a dead redis (C5).
    with pytest.raises(redis.exceptions.ConnectionError):
        await bus.consume("s", "g", "c1", count=5, block_ms=100)
