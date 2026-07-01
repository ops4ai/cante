"""C2 — worker runs the real LLM (complete called, Message rows persisted).
C4 — failure leaves the entry pending for redelivery; dead-letters after N retries.
"""

import os

import pytest

from cante.bus import StreamEntry

_SEEDED = "00000000-0000-0000-0000-000000000001"


class _FakeAdapter:
    def __init__(self, response=None, raise_on=None):
        self.calls = 0
        self._response = response
        self._raise = raise_on

    async def complete(self, messages, tools, *, temperature=0.7, max_tokens=1024, model=""):
        self.calls += 1
        if self._raise:
            raise self._raise
        from cante.llm import LLMResponse

        return self._response or LLMResponse(content="Sure — here is your answer.")


async def _seed_route(number_phone: str = "911", from_phone: str = "3519"):
    from cante.db import async_session_factory
    from cante.models import Bot, Number, Provider, Route, Skill
    from cante.tenant import with_tenant

    async with async_session_factory() as session:
        with with_tenant(_SEEDED):
            provider = Provider(
                name="P", type="openai_compatible", base_url="http://x", model="m",
                api_key_ref="FAKE_WORKER_KEY",
            )
            skill = Skill(name="S", preset="custom", playbook_md="Be brief.")
            session.add_all([provider, skill])
            await session.flush()
            bot = Bot(name="B", skill_id=skill.id, provider_id=provider.id)
            number = Number(phone=number_phone, display_name="n")
            session.add_all([bot, number])
            await session.flush()
            session.add(Route(number_id=number.id, bot_id=bot.id, priority=10))
            await session.commit()


def _entry(body="hi", from_phone="3519", number_phone="911", eid="1-0"):
    return StreamEntry(id=eid, data={
        "conversation_id": from_phone, "from_phone": from_phone,
        "number_phone": number_phone, "body": body,
    })


@pytest.mark.asyncio
async def test_worker_runs_llm_and_persists_messages(pg, redis_client, monkeypatch):
    from cante.settings import settings
    import services.worker.main as w

    monkeypatch.setattr(settings, "worker_llm_enabled", True)
    monkeypatch.setenv("FAKE_WORKER_KEY", "fake-key")
    await _seed_route()

    fake = _FakeAdapter()
    monkeypatch.setattr(w, "build_provider_adapter", lambda provider, api_key: fake)

    bus = w.RedisStreamsBus(redis_client)
    await bus.create_group(w.S_IN, w.GROUP)
    await w.process(_entry(), bus, redis_client)

    # complete was actually called
    assert fake.calls == 1

    # outbound reply was published
    info = await redis_client.xinfo_stream(w.S_OUT)
    assert info["length"] == 1

    # Message rows persisted (in + out) for the conversation
    from cante.db import async_session_factory
    from cante.models import Conversation, Message
    from cante.tenant import with_tenant
    from sqlalchemy import select

    async with async_session_factory() as session:
        with with_tenant(_SEEDED):
            conv = (await session.execute(select(Conversation))).scalars().first()
            msgs = (await session.execute(
                select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
            )).scalars().all()
    directions = sorted(m.direction for m in msgs)
    assert directions == ["in", "out"]
    assert any(m.role == "assistant" and "answer" in m.body for m in msgs)


@pytest.mark.asyncio
async def test_failure_leaves_entry_pending_then_redelivered(pg, redis_client, monkeypatch):
    from cante.settings import settings
    import services.worker.main as w

    async def _boom(*a, **k):
        raise RuntimeError("unrecoverable agent failure")

    monkeypatch.setattr(settings, "worker_llm_enabled", True)
    monkeypatch.setattr(settings, "worker_claim_min_idle_ms", 0)
    monkeypatch.setattr(settings, "worker_max_retries", 3)
    monkeypatch.setenv("FAKE_WORKER_KEY", "fake-key")
    monkeypatch.setattr(w, "run_agent_loop", _boom)
    await _seed_route()

    bus = w.RedisStreamsBus(redis_client)
    await bus.create_group(w.S_IN, w.GROUP)
    await bus.publish(w.S_IN, _entry().data)

    async def _fail(entry):
        """Run process + the loop's failure handler (the _drain per-entry path)."""
        try:
            await w.process(entry, bus, redis_client)
            await bus.ack(w.S_IN, w.GROUP, entry.id)
        except Exception as e_err:
            await w._on_failure(w.S_IN, entry, bus, redis_client)

    # 1. Delivered via consume; process fails → NOT acked (stays pending).
    entries = await bus.consume(w.S_IN, w.GROUP, w.CONSUMER, count=5, block_ms=100)
    assert len(entries) == 1
    await _fail(entries[0])
    pending = await redis_client.xpending(w.S_IN, w.GROUP)
    assert pending["pending"] == 1, "failed entry must stay pending for redelivery"

    # 2. XAUTOCLAIM reclaims the pending entry → redelivered, fails again (retries=2).
    reclaimed = await bus.claim_pending(w.S_IN, w.GROUP, w.CONSUMER, 0)
    assert len(reclaimed) == 1 and reclaimed[0].id == entries[0].id
    await _fail(reclaimed[0])
    pending = await redis_client.xpending(w.S_IN, w.GROUP)
    assert pending["pending"] == 1, "still pending until max retries reached"

    # 3. One more reclaim → retries=3 >= max → dead-lettered + acked.
    reclaimed = await bus.claim_pending(w.S_IN, w.GROUP, w.CONSUMER, 0)
    assert len(reclaimed) == 1
    await _fail(reclaimed[0])
    pending = await redis_client.xpending(w.S_IN, w.GROUP)
    assert pending["pending"] == 0, "entry must be acked once dead-lettered"
    dead = await redis_client.xinfo_stream(f"{w.S_IN}:dead")
    assert dead["length"] == 1
