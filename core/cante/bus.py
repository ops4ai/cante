"""Event bus abstraction — Redis Streams implementation. Swappable to NATS/Kafka later."""

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass
class StreamEntry:
    id: str
    data: dict
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    """Redis Streams with consumer groups (at-least-once delivery)."""

    def __init__(self, redis_client):
        self._redis = redis_client

    async def publish(self, stream: str, data: dict, max_len: int = 10_000) -> str:
        entry_id = await self._redis.xadd(stream, data, maxlen=max_len)
        return entry_id.decode() if isinstance(entry_id, bytes) else entry_id

    async def consume(
        self, stream: str, group: str, consumer: str, count: int = 5, block_ms: int = 5000
    ) -> list[StreamEntry]:
        try:
            results = await self._redis.xreadgroup(
                group, consumer, {stream: ">"}, count=count, block=block_ms
            )
        except Exception:
            return []

        entries = []
        for stream_name, messages in (results or []):
            for msg_id, data in messages:
                decoded = {
                    k.decode() if isinstance(k, bytes) else k: (
                        v.decode() if isinstance(v, bytes) else v
                    )
                    for k, v in (data or {}).items()
                }
                msg_id_str = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
                entries.append(StreamEntry(id=msg_id_str, data=decoded))
        return entries

    async def ack(self, stream: str, group: str, entry_id: str) -> None:
        await self._redis.xack(stream, group, entry_id)

    async def create_group(self, stream: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # Group already exists
