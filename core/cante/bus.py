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

    async def trim_delivered(self, stream: str, group: str) -> int:
        """XTRIM entries already delivered to *group*, but only when none are pending.

        Housekeeping for the outbound stream: acked entries otherwise linger in
        the stream until evicted by the ``xadd maxlen`` cap, which makes a raw
        ``xlen``/``xrange`` staleness check report phantom hours-old messages
        (issue: ``sender.queue_stale`` false alarm).

        Safe by construction:
          - skips when the group's pending-PEL is non-empty, so an unacked entry
            (whose id is below ``last-delivered-id``) is never dropped out from
            under a consumer;
          - uses ``MINID = last-delivered-id``, so entries never delivered to the
            group (id above it) are never removed.

        Returns the number of entries trimmed (0 if nothing was removed).
        """
        try:
            # xinfo_groups takes ONLY the stream name (returns ALL groups for it);
            # we then locate *group* in the result.
            groups = await self._redis.xinfo_groups(stream)
        except redis.exceptions.ResponseError as e:
            if "NOGROUP" in str(e) or "no such key" in str(e).lower():
                return 0
            raise
        except Exception:
            return 0  # best-effort housekeeping — never break the send loop
        g = next((x for x in (groups or []) if x.get("name") == group), None)
        if not g or g.get("pending"):
            return 0
        last = g.get("last-delivered-id")
        if not last:
            return 0
        # Approximate MINID trim: drop everything strictly below the last id the
        # group consumed. pending==0 guarantees nothing in-flight sits below it.
        try:
            return await self._redis.xtrim(stream, minid=str(last), approximate=True)
        except Exception:
            return 0

    async def pending_oldest(self, stream: str, group: str) -> tuple[int, str | None]:
        """Return (pending_count, oldest_pending_id) for real staleness checks.

        ``pending_count`` is the number of delivered-but-unacked entries in the
        group (the only entries that are genuinely "stuck"). ``oldest_pending_id``
        is the lowest such id (a stream id like ``1783447427569-0``) or ``None``
        when nothing is pending. Unlike raw ``xlen``/``xrange``, this ignores
        already-acked entries that merely linger in the stream.
        """
        try:
            summary = await self._redis.xpending(stream, group)
        except redis.exceptions.ResponseError as e:
            if "NOGROUP" in str(e) or "no such key" in str(e).lower():
                return 0, None
            raise
        except Exception:
            return 0, None
        if isinstance(summary, dict):
            pending = int(summary.get("pending", 0) or 0)
            oldest = summary.get("min")
            return pending, (str(oldest) if oldest else None)
        # Fallback shape: [pending, min, max, [(consumer, count), ...]]
        try:
            return int(summary[0]), (str(summary[1]) if summary[1] else None)
        except (IndexError, TypeError):
            return 0, None
