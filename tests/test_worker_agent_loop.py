"""C3 — assistant tool_calls are carried into the next request's history.

A 2nd LLM iteration that drops the assistant's tool_calls is rejected by both
OpenAI and Anthropic (400). The agent loop must append ONE assistant message
carrying the turn's tool_calls, then the tool-result messages.
"""

import pytest

from cante.llm import LLMMessage, LLMResponse, LLMToolCall
from cante.tools import BuiltinTool, ToolRegistry


class _EchoTool(BuiltinTool):
    async def execute(self, arguments, context):
        return {"ok": True, "args": arguments}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_builtin(_EchoTool(
        name="tool_a", description="A", parameters={"type": "object", "properties": {}}
    ))
    reg.register_builtin(_EchoTool(
        name="tool_b", description="B", parameters={"type": "object", "properties": {}}
    ))
    return reg


class _ScriptedAdapter:
    """Returns a scripted sequence of LLMResponse and records every complete() call."""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[list[LLMMessage]] = []

    async def complete(self, messages, tools, *, temperature=0.7, max_tokens=1024, model=""):
        self.calls.append([
            LLMMessage(
                role=m.role,
                content=m.content,
                tool_call_id=m.tool_call_id,
                name=m.name,
                tool_calls=list(m.tool_calls) if m.tool_calls else None,
            )
            for m in messages
        ])
        return self._script.pop(0)


@pytest.mark.asyncio
async def test_tool_calls_carried_into_second_request(monkeypatch):
    from services.worker.main import run_agent_loop

    tc1 = LLMToolCall(id="call_1", name="tool_a", arguments={"x": 1})
    tc2 = LLMToolCall(id="call_2", name="tool_b", arguments={"y": 2})
    adapter = _ScriptedAdapter([
        LLMResponse(content="", tool_calls=[tc1, tc2]),
        LLMResponse(content="All done."),
    ])

    reply, _ctx = await run_agent_loop("hello", adapter, _registry())

    assert reply == "All done."
    assert len(adapter.calls) == 2

    second = adapter.calls[1]
    # history: system, user, assistant(tool_calls), tool, tool
    assert second[0].role == "system"
    assert second[1].role == "user"
    assistant = second[2]
    assert assistant.role == "assistant"
    assert assistant.tool_calls is not None and len(assistant.tool_calls) == 2
    assert [tc.id for tc in assistant.tool_calls] == ["call_1", "call_2"]
    assert [tc.name for tc in assistant.tool_calls] == ["tool_a", "tool_b"]

    tool_msgs = second[3:]
    assert len(tool_msgs) == 2
    assert all(m.role == "tool" for m in tool_msgs)
    assert [m.tool_call_id for m in tool_msgs] == ["call_1", "call_2"]
    assert [m.name for m in tool_msgs] == ["tool_a", "tool_b"]
