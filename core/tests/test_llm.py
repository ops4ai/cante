"""Test LLM adapter interface and message types."""
from cante.llm import LLMMessage, LLMResponse, LLMToolCall, LLMToolDefinition


def test_message_creation():
    msg = LLMMessage(role="user", content="Hello")
    assert msg.role == "user"
    assert msg.content == "Hello"
    assert msg.tool_call_id is None

def test_llm_response_with_tools():
    tc = LLMToolCall(id="1", name="lookup", arguments={"phone": "123"})
    resp = LLMResponse(content="Let me check", tool_calls=[tc], tokens_in=100, tokens_out=50, model="claude-3")
    assert len(resp.tool_calls) == 1
    assert resp.finish_reason == "tool_calls"

def test_tool_definition_schema():
    td = LLMToolDefinition(name="test", description="A test tool", parameters={"type": "object", "properties": {"x": {"type": "integer"}}})
    assert td.name == "test"
    assert td.parameters["type"] == "object"
