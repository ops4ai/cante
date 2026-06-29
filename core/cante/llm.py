"""LLM abstraction — two adapters cover every provider: openai_compatible and anthropic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class LLMToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMMessage:
    role: str  # system | user | assistant | tool
    content: str
    tool_call_id: str | None = None  # set for role="tool" (the result of a tool call)
    name: str | None = None  # tool name for role="tool"
    # Assistant tool calls for the turn. Set on role="assistant" messages so the
    # next request carries the model's own tool_calls back to the provider
    # (both OpenAI and Anthropic reject a follow-up request that drops them).
    tool_calls: list[LLMToolCall] | None = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    finish_reason: str = "stop"  # stop | tool_calls | length

    def __post_init__(self) -> None:
        # A response with tool calls is, by definition, a tool_calls turn unless
        # the provider explicitly said otherwise (e.g. "length").
        if self.tool_calls and self.finish_reason == "stop":
            self.finish_reason = "tool_calls"


@dataclass
class LLMToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema


class LLMAdapter(Protocol):
    """Normalised LLM interface. One concrete adapter per provider type."""

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str = "",
    ) -> LLMResponse: ...

    @staticmethod
    def supports(model: str, base_url: str) -> bool: ...


class LLMError(RuntimeError):
    """Base for all LLM failures so callers can catch uniformly."""


class LLMAPITimeout(LLMError):
    pass


class LLMAPIConnectionError(LLMError):
    pass


class LLMAPIStatusError(LLMError):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"LLM API error {status_code}: {body[:200]}")
