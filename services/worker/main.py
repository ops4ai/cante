"""Cante worker — consumes stream:inbound, runs agent loop, publishes stream:outbound."""

import asyncio
import json
import signal

import structlog

from cante.bus import RedisStreamsBus
from cante.redis import get_redis
from cante.settings import settings

logger = structlog.get_logger(__name__)
running = True
S_IN, S_OUT, S_TRIG = "stream:inbound", "stream:outbound", "stream:triggers"
GROUP, CONSUMER = "agent-workers", "worker-1"


def _sigterm(*_):
    global running
    running = False


# ── Agent runtime ─────────────────────────────────────────────────────


async def run_agent_loop(user_message: str, llm=None, tools=None) -> str:
    """Execute the agent loop: LLM ⇄ tools, max N iterations, circuit breaker."""
    from cante.llm import LLMMessage, LLMToolDefinition

    if llm is None or tools is None:
        return f"[Cante M1 echo] Recebi: {user_message[:400]}"

    system_prompt = (
        "You are a helpful assistant. Be concise and friendly. "
        "Use tools when needed to help the user."
    )

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_message),
    ]

    tool_defs = [
        LLMToolDefinition(name=t.name, description=t.description, parameters=t.parameters)
        for t in tools.list_tools()
    ]

    failures = 0
    for _ in range(settings.max_tool_iterations):
        try:
            response = await llm.complete(
                messages,
                tool_defs,
                temperature=0.7,
                max_tokens=1024,
            )

            if response.tool_calls:
                # Execute tools and feed results back into the loop
                for tc in response.tool_calls:
                    result = await tools.execute(tc.name, tc.arguments, {}, None)
                    messages.append(LLMMessage(
                        role="assistant",
                        content=response.content or "",
                    ))
                    messages.append(LLMMessage(
                        role="tool",
                        content=json.dumps({"result": result.result}) if result.success else f"Error: {result.error}",
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))
            else:
                # No tool calls — this is the final reply
                return response.content or "Desculpa, não percebi."

            failures = 0  # Reset on success
        except Exception as e:
            failures += 1
            logger.warning("worker.llm_failure", error=str(e), failures=failures)
            if failures >= settings.circuit_breaker_failures:
                return "Desculpa, estou com dificuldades técnicas. Vou pedir a um humano para te ajudar."
            continue

    return "Vou pedir a um humano para continuar esta conversa."


async def process(entry, bus, redis):
    data = entry.data
    cid = data.get("from_phone", "unknown")
    lock = f"lock:conv:{cid}"
    if not await redis.set(lock, "1", nx=True, ex=60):
        return

    try:
        await asyncio.sleep(settings.debounce_ms_default / 1000.0)
        reply = await run_agent_loop(data.get("body", ""))
        await bus.publish(S_OUT, {
            "conversation_id": cid,
            "from_phone": data.get("from_phone", ""),
            "number_phone": data.get("number_phone", ""),
            "body": reply,
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
