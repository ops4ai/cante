"""Event bus abstraction — Redis Streams implementation. Swappable to NATS/Kafka later."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import redis.exceptions


@dataclass
class StreamEntry:
    id: str
    data: dict
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventBus(ABC):
    """Abstract event bus. Exactly one implementation for v1: RedisStreamsBus."""

    @abstractmethod
    async def publish(self, stream: str, data: dict, max_len: int = 10_000) -> str: ...

    @abstractmethod
    async def consume(
        self, stream: str, group: str, consumer: str, count: int = 5, block_ms: int = 5000
    ) -> list[StreamEntry]: ...

    @abstractmethod
    async def ack(self, stream: str, group: str, entry_id: str) -> None: ...

    @abstractmethod
    async def create_group(self, stream: str, group: str) -> None: ...


class RedisStreamsBus(EventBus):
    """Redis Streams with consumer groups (at-least-once delivery).

    The redis client is configured with ``decode_responses=True`` (see
    cante.redis), so all stream field values are ``str`` — no manual decoding.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def publish(self, stream: str, data: dict, max_len: int = 10_000) -> str:
        entry_id = await self._redis.xadd(stream, data, maxlen=max_len)
        return entry_id

    async def consume(
        self, stream: str, group: str, consumer: str, count: int = 5, block_ms: int = 5000
    ) -> list[StreamEntry]:
        try:
            results = await self._redis.xreadgroup(
                group, consumer, {stream: ">"}, count=count, block=block_ms
            )
        except redis.exceptions.ResponseError as e:
            # NOGROUP (real Redis) / "requires the key to exist" (fakeredis):
            # the stream or consumer group is missing. Recreate and let the
            # next loop pick up; any other ResponseError is a real failure and
            # must propagate.
            msg = str(e).lower()
            if "nogroup" in msg or "no such key" in msg or "requires the key to exist" in msg:
                await self.create_group(stream, group)
                return []
            raise
        # Connection errors, timeouts, etc. propagate to the caller so its
        # backoff runs — never swallow them into a silent busy-loop (C5).

        entries = []
        for _stream_name, messages in results or []:
            for msg_id, data in messages:
                entries.append(StreamEntry(id=msg_id, data=dict(data or {})))
        return entries

    async def ack(self, stream: str, group: str, entry_id: str) -> None:
        await self._redis.xack(stream, group, entry_id)

    async def create_group(self, stream: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.exceptions.ResponseError as e:
            # BUSYGROUP means the group already exists — expected and harmless.
            # Anything else is a real failure and must propagate (C5).
            if "BUSYGROUP" in str(e):
                return
            raise

    async def claim_pending(
        self, stream: str, group: str, consumer: str, min_idle_ms: int, count: int = 10
    ) -> list[StreamEntry]:
        """Re-claim entries delivered but not acked for > min_idle_ms (XAUTOCLAIM).

        Used by the worker's redelivery sweep (C4): entries left pending by a
        crashed/slow consumer are handed to this consumer for reprocessing.
        Returns the newly claimed entries.
        """
        try:
            _next_id, messages, _deleted = await self._redis.xautoclaim(
                stream, group, consumer, min_idle_time=min_idle_ms, count=count
            )
        except redis.exceptions.ResponseError as e:
            if "NOGROUP" in str(e):
                await self.create_group(stream, group)
                return []
            raise
        entries = []
        for msg_id, data in messages or []:
            entries.append(StreamEntry(id=msg_id, data=dict(data or {})))
        return entries
