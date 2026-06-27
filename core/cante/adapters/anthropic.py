"""Anthropic adapter — Claude via Messages API with tool-use blocks."""

import structlog

from cante.llm import (
    LLMAdapter,
    LLMAPIConnectionError,
    LLMAPIStatusError,
    LLMAPITimeout,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)

logger = structlog.get_logger(__name__)


class AnthropicAdapter(LLMAdapter):
    def __init__(self, api_key: str, base_url: str | None = None):
        self._api_key = api_key
        self._base_url = base_url or "https://api.anthropic.com/v1"

    @staticmethod
    def supports(model: str, base_url: str) -> bool:
        return "claude" in model.lower() or "anthropic" in base_url.lower()

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str = "claude-sonnet-4-20250514",
    ) -> LLMResponse:
        import httpx

        # Convert to Anthropic format
        system_prompt = ""
        anthropic_messages = []
        for msg in messages:
            if msg.role == "system":
                system_prompt += msg.content + "\n"
            else:
                role = "assistant" if msg.role == "assistant" else "user"
                anthropic_messages.append({"role": role, "content": msg.content})

        anthropic_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ] if tools else None

        body = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anthropic_messages,
        }
        if system_prompt.strip():
            body["system"] = system_prompt.strip()
        if anthropic_tools:
            body["tools"] = anthropic_tools

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                resp = await client.post(
                    f"{self._base_url}/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )

                if resp.status_code == 429:
                    raise LLMAPITimeout("Rate limited by Anthropic")
                if resp.status_code >= 400:
                    raise LLMAPIStatusError(resp.status_code, resp.text)

                data = resp.json()

                content = ""
                tool_calls = []
                for block in data.get("content", []):
                    if block["type"] == "text":
                        content += block["text"]
                    elif block["type"] == "tool_use":
                        tool_calls.append(LLMToolCall(
                            id=block["id"],
                            name=block["name"],
                            arguments=block["input"],
                        ))

                return LLMResponse(
                    content=content,
                    tool_calls=tool_calls,
                    tokens_in=data.get("usage", {}).get("input_tokens", 0),
                    tokens_out=data.get("usage", {}).get("output_tokens", 0),
                    model=data.get("model", model),
                    finish_reason="tool_calls" if tool_calls else data.get("stop_reason", "stop"),
                )

        except httpx.TimeoutException:
            raise LLMAPITimeout("Anthropic API timed out")
        except httpx.ConnectError as e:
            raise LLMAPIConnectionError(str(e))
