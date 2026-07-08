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
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]

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
    """Analyze closed conversations that had human intervention, using the bot's
    own LLM provider to diagnose failures and suggest concrete improvements.

    Runs daily at 04:00 via APScheduler. Idempotent — each conversation is
    analyzed at most once (guarded by NOT EXISTS on learnings).
    """
    import json as _json
    from datetime import date, timedelta

    from cante.db import async_session_factory, build_provider_adapter, resolve_provider_api_key
    from cante.llm import LLMMessage
    from cante.models import Bot, Conversation, Message, Provider, Skill
    from sqlalchemy import select, text

    logger.info("scheduler.daily_learning_start")
    analyzed = 0

    async with async_session_factory() as session:
        # Find conversations that were closed, have human messages, and weren't
        # analyzed yet. Limit to last 30 days and 50 per run.
        cutoff = date.today() - timedelta(days=30)
        subq = (
            select(text("1"))
            .select_from(text("learnings l2"))
            .where(text("l2.conversation_id = conversations.id"))
        )
        stmt = (
            select(Conversation)
            .where(
                Conversation.state == "closed",
                Conversation.last_activity_at >= cutoff,
                ~subq.exists(),
            )
            .order_by(Conversation.last_activity_at.desc())
            .limit(50)
        )
        result = await session.execute(stmt)
        candidates = result.scalars().all()

        for conv in candidates:
            try:
                # Load messages for this conversation
                msgs = (
                    await session.execute(
                        select(Message)
                        .where(Message.conversation_id == conv.id)
                        .order_by(Message.created_at)
                    )
                ).scalars().all()

                if not msgs:
                    continue

                # Has a human replied? Only analyze if there's human intervention.
                has_human = any(m.role == "human" for m in msgs)
                if not has_human:
                    continue

                # Load the bot + skill for context
                bot = (await session.execute(select(Bot).where(Bot.id == conv.bot_id))).scalar_one_or_none()
                skill = None
                provider = None
                if bot:
                    skill = (await session.execute(select(Skill).where(Skill.id == bot.skill_id))).scalar_one_or_none()
                    provider = (await session.execute(select(Provider).where(Provider.id == bot.provider_id))).scalar_one_or_none()

                if not (bot and skill and provider):
                    logger.warning("scheduler.learning_skip_no_skill", conv_id=conv.id)
                    continue

                # Build the transcript: show what happened before and after human takeover
                bot_msgs = [m for m in msgs if m.role in ("user", "assistant")]
                human_msgs = [m for m in msgs if m.role == "human"]
                transcript = "=== Conversation before human takeover ===\n"
                for m in bot_msgs[-10:]:
                    transcript += f"[{m.role}]: {m.body}\n"
                if human_msgs:
                    transcript += "\n=== What the human said to resolve it ===\n"
                    for m in human_msgs[-5:]:
                        transcript += f"[human]: {m.body}\n"

                # Build the analysis prompt
                system_prompt = (
                    "You are a quality analyst for a customer-service bot. "
                    "Analyze conversations where the bot escalated to a human. "
                    "Diagnose the root cause and suggest ONE concrete improvement to the bot.\n\n"
                    "Categories: missing_info, wrong_answer, out_of_scope, language_mismatch, "
                    "tone_issue, too_many_questions, misunderstood_intent, pricing_unknown, other\n\n"
                    "Suggestion types:\n"
                    "- prompt_addition: add a line to the bot's playbook (how it should behave)\n"
                    "- guardrail_addition: add a line to the bot's guardrails (what it must never do)\n"
                    "- no_action: one-off situation, not worth changing the bot\n\n"
                    "Return ONLY valid JSON, no markdown, no explanation outside the JSON:\n"
                    '{"category":"...", "diagnosis":"...", "suggestion_type":"...", '
                    '"suggestion_text":"...", "confidence":0.0}'
                )
                user_prompt = (
                    f"Current bot playbook:\n{skill.playbook_md or '(empty)'}\n\n"
                    f"Current bot guardrails:\n{skill.guardrails_md or '(empty)'}\n\n"
                    f"{transcript}"
                )

                # Call the LLM
                api_key = await resolve_provider_api_key(provider, session)
                if not api_key:
                    logger.warning("scheduler.learning_no_api_key", provider=provider.name)
                    continue

                llm = build_provider_adapter(provider, api_key)
                try:
                    response = await llm.complete(
                        messages=[
                            LLMMessage(role="system", content=system_prompt),
                            LLMMessage(role="user", content=user_prompt),
                        ],
                        tools=[],
                        temperature=0.3,
                        max_tokens=512,
                        model=provider.model,
                    )
                except Exception as llm_err:
                    logger.warning("scheduler.learning_llm_failed", conv_id=conv.id, error=str(llm_err))
                    await llm.close()
                    continue

                # Parse the JSON response
                raw_text = (response.content or "").strip()
                # Strip markdown code fences if the model wraps it
                if raw_text.startswith("```"):
                    raw_text = raw_text.split("\n", 1)[-1]
                    if raw_text.endswith("```"):
                        raw_text = raw_text[:-3]
                    raw_text = raw_text.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:].strip()
                    if raw_text.endswith("```"):
                        raw_text = raw_text[:-3].strip()

                try:
                    parsed = _json.loads(raw_text)
                except _json.JSONDecodeError:
                    logger.warning("scheduler.learning_bad_json", conv_id=conv.id, raw=raw_text[:200])
                    await llm.close()
                    continue

                await llm.close()

                # Insert the learning
                from cante.tenant import with_bypass
                with with_bypass():
                    from cante.models import Learning
                    import uuid as _uuid

                    learning = Learning(
                        id=str(_uuid.uuid4()),
                        tenant_id=conv.tenant_id or "00000000-0000-0000-0000-000000000001",
                        conversation_id=conv.id,
                        type=parsed.get("suggestion_type", "no_action"),
                        category=parsed.get("category", "other"),
                        diagnosis=parsed.get("diagnosis", ""),
                        suggestion_type=parsed.get("suggestion_type", "no_action"),
                        suggestion_payload={
                            "text": parsed.get("suggestion_text", ""),
                        },
                        suggestion_md=parsed.get("suggestion_text", "")[:200],
                        confidence=float(parsed.get("confidence", 0.0)),
                        status="pending",
                        raw_llm_response={"content": raw_text, "model": response.model or ""},
                        run_date=date.today(),
                    )
                    session.add(learning)
                    await session.commit()

                analyzed += 1
                logger.info("scheduler.learning_analyzed", conv_id=conv.id, category=learning.category)

            except Exception as e:
                logger.error("scheduler.learning_conv_failed", conv_id=conv.id, error=str(e))
                await session.rollback()
                continue

    logger.info("scheduler.daily_learning_done", analyzed=analyzed, candidates=len(candidates))


# Atomic "refresh only if I still own the lock" — prevents a stale leader from
# extending a lock it lost (the fencing token must match).
_REFRESH_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""


async def _check_numbers_health():
    """Periodically check Evolution status for all 'connected' numbers.

    If Evolution reports the instance as disconnected/logged out, update the
    number status in cante so the UI reflects reality.
    """
    from cante.db import async_session_factory
    from cante.evolution import EvolutionAdapter
    from cante.models import Number
    from sqlalchemy import select, update

    adapter = EvolutionAdapter()
    async with async_session_factory() as session:
        result = await session.execute(
            select(Number).where(Number.status == "connected")
        )
        numbers = result.scalars().all()

    updated = 0
    for n in numbers:
        cfg = n.connection_config or {}
        instance = cfg.get("instance", "")
        if not instance:
            continue
        try:
            real = await adapter.status(cfg)
            if real.status in ("close", "disconnected"):
                async with async_session_factory() as s:
                    await s.execute(
                        update(Number).where(Number.id == n.id).values(status="disconnected")
                    )
                    await s.commit()
                updated += 1
                logger.info("scheduler.number_disconnected", phone=n.phone, instance=instance)
        except Exception:
            pass  # Evolution unreachable — skip, will retry next cycle

    if updated:
        logger.info("scheduler.health_check_done", updated=updated, total=len(numbers))


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
        scheduler.add_job(_check_numbers_health, "interval", seconds=60, id="numbers_health")
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
                scheduler.add_job(_check_numbers_health, "interval", seconds=60, id="numbers_health")
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
