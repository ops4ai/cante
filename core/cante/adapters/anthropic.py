"""Anthropic adapter — Claude via Messages API with tool-use blocks."""

import httpx
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
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    async def close(self) -> None:
        await self._client.aclose()

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
        tool_choice: dict | None = None,
    ) -> LLMResponse:
        # Convert to Anthropic format. Anthropic models tool-use as content
        # blocks: an assistant turn carries [{type: text}, {type: tool_use}];
        # a tool result is a user turn with [{type: tool_result, tool_use_id, content}].
        system_prompt = ""
        anthropic_messages = []
        for msg in messages:
            if msg.role == "system":
                system_prompt += msg.content + "\n"
                continue

            if msg.role == "assistant":
                blocks: list[dict] = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls or []:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                anthropic_messages.append({
                    "role": "assistant",
                    "content": blocks or [{"type": "text", "text": ""}],
                })
            elif msg.role == "tool":
                anthropic_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content,
                    }],
                })
            else:
                anthropic_messages.append({"role": "user", "content": msg.content})

        anthropic_tools = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ] if tools else None

        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anthropic_messages,
        }
        if system_prompt.strip():
            body["system"] = system_prompt.strip()
        if anthropic_tools:
            body["tools"] = anthropic_tools
        if tool_choice:
            body["tool_choice"] = tool_choice

        try:
            resp = await self._client.post(
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

        except httpx.TimeoutException as err:
            raise LLMAPITimeout("Anthropic API timed out") from err
        except httpx.ConnectError as err:
            raise LLMAPIConnectionError(str(err)) from err
