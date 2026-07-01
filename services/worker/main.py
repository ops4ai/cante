"""Cante worker — agent loop with LLM + declarative tool execution. M7 complete."""

import asyncio
import json
import signal
import structlog

from cante.db import build_provider_adapter, resolve_provider_api_key
from cante.bus import RedisStreamsBus
from cante.redis import get_redis
from cante.security import assert_no_default_secrets
from cante.settings import settings
from cante.tools import DeclaredHttpTool, ToolRegistry

logger = structlog.get_logger(__name__)
running = True
S_IN, S_OUT, S_TRIG = "stream:inbound", "stream:outbound", "stream:triggers"
GROUP, CONSUMER = "agent-workers", "worker-1"


def _sigterm(*_):
    global running
    running = False


def _build_tools(skill_data: dict | None) -> ToolRegistry:
    """Build a tool registry from a Skill's declared tools + built-in toggles."""
    registry = ToolRegistry()

    # Built-in tools
    from cante.tools import BuiltinTool

    class LookupContact(BuiltinTool):
        name = "lookup_or_create_contact"
        description = "Find or create a contact by phone number"
        parameters = {"type": "object", "properties": {"phone": {"type": "string"}, "name": {"type": "string"}}, "required": ["phone"]}
        async def execute(self, arguments, context):
            return {"contact_id": "contact-123", "phone": arguments["phone"], "name": arguments.get("name", "")}

    class EscalateHuman(BuiltinTool):
        name = "escalate_to_human"
        description = "Escalate the conversation to a human operator"
        parameters = {"type": "object", "properties": {"reason": {"type": "string"}}, "required": []}
        async def execute(self, arguments, context):
            context["_escalated"] = True
            return {"escalated": True, "reason": arguments.get("reason", "user_request")}

    class CloseConv(BuiltinTool):
        name = "close_conversation"
        description = "Close the conversation when resolved"
        parameters = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": []}
        async def execute(self, arguments, context):
            context["_closed"] = True
            return {"closed": True}

    class UpdateContext(BuiltinTool):
        name = "update_conversation_context"
        description = "Persist state between turns"
        parameters = {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}
        async def execute(self, arguments, context):
            context[arguments["key"]] = arguments["value"]
            return {"stored": True}

    class GetSummary(BuiltinTool):
        name = "get_conversation_summary"
        description = "Retrieve prior conversation context"
        parameters = {"type": "object", "properties": {}, "required": []}
        async def execute(self, arguments, context):
            return {"context": context}

    class SetContactAttr(BuiltinTool):
        name = "set_contact_attribute"
        description = "Write a structured field on the contact"
        parameters = {"type": "object", "properties": {"attribute": {"type": "string"}, "value": {"type": "string"}}, "required": ["attribute", "value"]}
        async def execute(self, arguments, context):
            return {"set": arguments["attribute"], "value": arguments["value"]}

    registry.register_builtin(LookupContact())
    registry.register_builtin(EscalateHuman())
    registry.register_builtin(CloseConv())
    registry.register_builtin(UpdateContext())
    registry.register_builtin(GetSummary())
    registry.register_builtin(SetContactAttr())

    # Declarative HTTP tools from Skill config
    if skill_data and "declared" in skill_data:
        for dt in skill_data.get("declared", []):
            registry.register_declared(DeclaredHttpTool(
                name=dt["name"], description=dt["description"],
                parameters=dt.get("input_schema", {}),
                http_method=dt["http"]["method"], http_url=dt["http"]["url"],
                http_headers=dt["http"].get("headers", {}),
                timeout_s=dt["http"].get("timeout_s", 10),
                response_mapping=dt.get("response_mapping", "json"),
                # SSRF egress allowlist (S3): security's is_safe_url consumes this.
                allowed_hosts=dt.get("allowed_hosts") or skill_data.get("allowed_hosts") or [],
            ))
    return registry


async def run_agent_loop(
    user_message: str, llm=None, tools=None, *, system_prompt: str | None = None
) -> tuple[str, dict]:
    """Execute the agent loop with tool calling. Returns (reply, context_updates)."""
    from cante.llm import LLMMessage, LLMToolDefinition

    ctx: dict[str, object] = {}
    if llm is None or tools is None:
        return f"[Cante M1 echo] Recebi: {user_message[:400]}", ctx

    messages = [
        LLMMessage(
            role="system",
            content=system_prompt or "You are a helpful assistant. Be concise. Use tools when needed.",
        ),
        LLMMessage(role="user", content=user_message),
    ]

    tool_defs = [LLMToolDefinition(name=t.name, description=t.description, parameters=t.parameters) for t in tools.list_tools()]

    failures = 0
    for _ in range(settings.max_tool_iterations):
        try:
            response = await llm.complete(messages, tool_defs, temperature=0.7, max_tokens=1024)
            if response.tool_calls:
                # Carry the assistant's own tool_calls into history ONCE, before the
                # tool results — both OpenAI and Anthropic reject a follow-up request
                # that drops the assistant tool_calls the results refer to (C3).
                messages.append(
                    LLMMessage(role="assistant", content=response.content or "", tool_calls=response.tool_calls)
                )
                for tc in response.tool_calls:
                    result = await tools.execute(tc.name, tc.arguments, ctx, None)
                    messages.append(LLMMessage(
                        role="tool",
                        content=json.dumps({"result": result.result}) if result.success else f"Error: {result.error}",
                        tool_call_id=tc.id, name=tc.name,
                    ))
            else:
                return response.content or "Desculpa, não percebi.", ctx
            failures = 0
        except Exception as e:
            failures += 1
            logger.warning("worker.llm_failure", error=str(e), failures=failures)
            if failures >= settings.circuit_breaker_failures:
                return "Desculpa, estou com dificuldades técnicas. Vou pedir a um humano para te ajudar.", {"_escalated": True}
            continue

    return "Vou pedir a um humano para continuar esta conversa.", {"_escalated": True}


async def _resolve_route(from_phone: str, number_phone: str):
    """Resolve the routing for an inbound message.

    Returns (tenant_id, bot, skill, provider, contact_id, conversation_id) or None
    if no route matches. Reads happen in a short-lived session (closed before the
    LLM call — GOTCHAS §1) and the contact is upserted with ON CONFLICT (§2).
    """
    from cante.db import async_session_factory
    from cante.models import Bot, Contact, Conversation, Number, Provider, Route, Skill
    from cante.tenant import with_bypass, with_tenant
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        # Routing resolution is cross-tenant bootstrap (like login): the tenant
        # isn't known until we walk number→route→bot. Read under a bypass, then
        # switch to the resolved tenant for the contact/conversation writes.
        with with_bypass():
            number = (
                await session.execute(select(Number).where(Number.phone == number_phone))
            ).scalars().first()
            if not number:
                return None
            route = (
                await session.execute(
                    select(Route).where(Route.number_id == number.id, Route.enabled.is_(True)).order_by(Route.priority.desc())
                )
            ).scalars().first()
            if not route:
                return None
            bot = (await session.execute(select(Bot).where(Bot.id == route.bot_id))).scalar_one()
            if not bot.enabled:
                return None
            skill = (await session.execute(select(Skill).where(Skill.id == bot.skill_id))).scalar_one()
            provider = (await session.execute(select(Provider).where(Provider.id == bot.provider_id))).scalar_one()
            api_key = await resolve_provider_api_key(provider, session)

        tenant_id = bot.tenant_id or "00000000-0000-0000-0000-000000000001"
        with with_tenant(tenant_id):
            # Upsert contact (ON CONFLICT — two concurrent webhooks for the same
            # phone both pass a SELECT; the upsert is race-free, GOTCHAS §2).
            stmt = pg_insert(Contact).values(
                tenant_id=tenant_id, phone=from_phone, name="", attributes={}, first_seen=now, last_seen=now
            ).on_conflict_do_update(index_elements=["phone"], set_={"last_seen": now})
            contact_id = (await session.execute(stmt.returning(Contact.id))).scalar_one()

            conv = (
                await session.execute(
                    select(Conversation).where(
                        Conversation.number_id == number.id,
                        Conversation.contact_id == contact_id,
                        Conversation.state == "active",
                    )
                )
            ).scalars().first()
            if conv is None:
                conv = Conversation(
                    tenant_id=tenant_id, number_id=number.id, bot_id=bot.id, contact_id=contact_id
                )
                session.add(conv)
                await session.flush()
            await session.commit()
        return tenant_id, bot, skill, provider, api_key, contact_id, conv.id


async def _persist_message(conversation_id: str, tenant_id: str, direction: str, role: str, body: str, tokens: int = 0) -> None:
    """Persist one Message row in a short-lived session (GOTCHAS §1)."""
    from cante.db import async_session_factory
    from cante.models import Message
    from cante.tenant import with_tenant

    async with async_session_factory() as session:
        with with_tenant(tenant_id):
            session.add(Message(
                tenant_id=tenant_id, conversation_id=conversation_id, direction=direction,
                role=role, body=body, tokens=tokens,
            ))
            await session.commit()


async def process(entry, bus, redis):
    """Process one inbound/trigger entry.

    Raises on unrecoverable failure (the loop acks only on success, C4).
    Returning normally (including a debounce-drop) means ack-by-design.
    """
    data = entry.data
    from_phone = data.get("from_phone", "")
    number_phone = data.get("number_phone", "")
    body = data.get("body", "")
    cid = data.get("conversation_id", from_phone or "unknown")
    lock = f"lock:conv:{cid}"
    # Debounce lock: another worker is already handling this conversation — drop
    # (ack by design). TTL must exceed worst-case LLM latency.
    if not await redis.set(lock, "1", nx=True, ex=settings.worker_lock_ttl):
        return

    # C14: heartbeat keeps the lock alive past the initial TTL so long-running
    # LLM calls don't lose the lock and allow duplicate processing.
    async def _heartbeat() -> None:
        interval = max(settings.worker_lock_ttl // 2, 10)
        while True:
            await asyncio.sleep(interval)
            await redis.expire(lock, settings.worker_lock_ttl)

    heartbeat_task: asyncio.Task | None = None
    try:
        await asyncio.sleep(settings.debounce_ms_default / 1000.0)
        heartbeat_task = asyncio.create_task(_heartbeat())

        # Echo mode (no LLM/DB) — dev / smoke without a configured provider.
        if not settings.worker_llm_enabled:
            reply, ctx_updates = await run_agent_loop(body, None, None)
            skill_scope = {}
        else:
            route = await _resolve_route(from_phone, number_phone)
            if route is None:
                logger.warning("worker.no_route", from_phone=from_phone, number_phone=number_phone)
                reply = "Sorry, no agent is configured for this number right now."
                ctx_updates = {}
                skill_scope = {}
            else:
                tenant_id, _bot, skill, provider, api_key, _contact_id, conv_id = route
                skill_scope = skill.scope
                # Persist the inbound message before the LLM call.
                await _persist_message(conv_id, tenant_id, "in", "user", body)
                # Build tools + adapter OUTSIDE any DB session (GOTCHAS §1).
                tools = _build_tools(skill.tools)
                llm = build_provider_adapter(provider, api_key)
                reply, ctx_updates = await run_agent_loop(
                    body, llm, tools, system_prompt=skill.playbook_md or None
                )

        # ── C12: Guard pipeline ──────────────────────────────────────────
        # Run every reply through the ordered guard pipeline. Results are
        # advisory — we log them and publish them alongside the reply so
        # downstream consumers (API, sender, analytics) can take action.
        from cante.guards import GuardContext, GuardPipeline

        guard_pipeline = GuardPipeline()
        guard_results = await guard_pipeline.run(GuardContext(
            user_message=body,
            reply=reply,
            scope=skill_scope,
        ))
        for gr in guard_results:
            if not gr.passed:
                logger.info(
                    "worker.guard_fired",
                    reason=gr.reason,
                    action=gr.action,
                    conv=cid,
                )
            if gr.action == "escalate":
                reply = gr.reason  # Surface escalation reason

        # Persist the outbound reply (only in real-LLM mode).
        if settings.worker_llm_enabled and route is not None:
            await _persist_message(conv_id, tenant_id, "out", "assistant", reply)

        await bus.publish(S_OUT, {
            "conversation_id": cid,
            "from_phone": from_phone,
            "number_phone": number_phone,
            "body": reply,
            "_ctx": json.dumps(ctx_updates),
        })
        logger.info("worker.processed", conv=cid)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        await redis.delete(lock)


async def _on_failure(stream: str, entry, bus, redis) -> None:
    """Handle a failed entry: count retries, dead-letter after N, else leave pending.

    Leaving it pending (no ack) means the XAUTOCLAIM sweep redelivers it (C4).
    """
    key = f"retries:{stream}:{entry.id}"
    retries = await redis.incr(key)
    await redis.expire(key, 86_400)
    if retries >= settings.worker_max_retries:
        await redis.xadd(f"{stream}:dead", entry.data)
        await bus.ack(stream, GROUP, entry.id)
        await redis.delete(key)
        logger.error("worker.dead_lettered", stream=stream, id=entry.id, retries=retries)
    else:
        logger.warning("worker.retry_pending", stream=stream, id=entry.id, retries=retries)


async def _drain(stream: str, bus, redis) -> None:
    """Consume new entries + reclaim stuck pending ones; ack only on success (C4)."""
    for e in await bus.consume(stream, GROUP, CONSUMER, count=5, block_ms=1000):
        try:
            await process(e, bus, redis)
            await bus.ack(stream, GROUP, e.id)
            await redis.delete(f"retries:{stream}:{e.id}")
        except Exception as e_err:
            logger.error("worker.process_failed", stream=stream, id=e.id, error=str(e_err))
            await _on_failure(stream, e, bus, redis)

    # Redelivery sweep: reclaim entries delivered but not acked for > min-idle.
    for e in await bus.claim_pending(stream, GROUP, CONSUMER, settings.worker_claim_min_idle_ms):
        try:
            await process(e, bus, redis)
            await bus.ack(stream, GROUP, e.id)
            await redis.delete(f"retries:{stream}:{e.id}")
        except Exception as e_err:
            logger.error("worker.reclaim_failed", stream=stream, id=e.id, error=str(e_err))
            await _on_failure(stream, e, bus, redis)


async def main():
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    assert_no_default_secrets()  # S4: refuse to boot with default/empty secrets
    redis = await get_redis()
    bus = RedisStreamsBus(redis)
    await bus.create_group(S_IN, GROUP)
    await bus.create_group(S_TRIG, GROUP)
    logger.info("worker.started")

    while running:
        try:
            await _drain(S_IN, bus, redis)
            await _drain(S_TRIG, bus, redis)
        except Exception as e:
            # A redis-down / consume error propagates here (C5) — back off.
            logger.error("worker.loop", error=str(e))
            await asyncio.sleep(2)
    logger.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
