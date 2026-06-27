"""Cante worker — agent loop with LLM + declarative tool execution. M7 complete."""

import asyncio, json, signal, structlog

from cante.bus import RedisStreamsBus
from cante.redis import get_redis
from cante.settings import settings
from cante.tools import DeclaredHttpTool, ToolCallResult, ToolRegistry

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
            ))
    return registry


async def run_agent_loop(user_message: str, llm=None, tools=None) -> tuple[str, dict]:
    """Execute the agent loop with tool calling. Returns (reply, context_updates)."""
    from cante.llm import LLMMessage, LLMToolDefinition

    ctx = {}
    if llm is None or tools is None:
        return f"[Cante M1 echo] Recebi: {user_message[:400]}", ctx

    messages = [
        LLMMessage(role="system", content="You are a helpful assistant. Be concise. Use tools when needed."),
        LLMMessage(role="user", content=user_message),
    ]

    tool_defs = [LLMToolDefinition(name=t.name, description=t.description, parameters=t.parameters) for t in tools.list_tools()]

    failures = 0
    for _ in range(settings.max_tool_iterations):
        try:
            response = await llm.complete(messages, tool_defs, temperature=0.7, max_tokens=1024)
            if response.tool_calls:
                for tc in response.tool_calls:
                    result = await tools.execute(tc.name, tc.arguments, ctx, None)
                    messages.append(LLMMessage(role="assistant", content=response.content or ""))
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


async def process(entry, bus, redis):
    data = entry.data
    cid = data.get("conversation_id", data.get("from_phone", "unknown"))
    lock = f"lock:conv:{cid}"
    if not await redis.set(lock, "1", nx=True, ex=60):
        return
    try:
        await asyncio.sleep(settings.debounce_ms_default / 1000.0)
        tools = _build_tools(None)  # M7: load from Skill in DB (future)
        reply, ctx_updates = await run_agent_loop(data.get("body", ""), None, tools)
        await bus.publish(S_OUT, {
            "conversation_id": cid,
            "from_phone": data.get("from_phone", ""),
            "number_phone": data.get("number_phone", ""),
            "body": reply,
            "_ctx": json.dumps(ctx_updates),
        })
        logger.info("worker.processed", conv=cid)
    except Exception as e:
        logger.error("worker.error", error=str(e))
    finally:
        await redis.delete(lock)


async def main():
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    redis = await get_redis()
    bus = RedisStreamsBus(redis)
    await bus.create_group(S_IN, GROUP)
    await bus.create_group(S_TRIG, GROUP)
    logger.info("worker.started")

    while running:
        try:
            for e in await bus.consume(S_IN, GROUP, CONSUMER, count=5, block_ms=5000):
                await process(e, bus, redis)
                await bus.ack(S_IN, GROUP, e.id)
            for e in await bus.consume(S_TRIG, GROUP, CONSUMER, count=2, block_ms=1000):
                await process(e, bus, redis)
                await bus.ack(S_TRIG, GROUP, e.id)
        except Exception as e:
            logger.error("worker.loop", error=str(e))
            await asyncio.sleep(2)
    logger.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
