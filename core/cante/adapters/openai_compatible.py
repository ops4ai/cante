"""OpenAI-compatible adapter — covers OpenAI, DeepSeek, OpenRouter, Ollama, Groq, etc."""

import json

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


class OpenAICompatibleAdapter(LLMAdapter):
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def supports(model: str, base_url: str) -> bool:
        return True  # Everything else is OpenAI-compatible

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str = "gpt-4o",
    ) -> LLMResponse:
        api_messages = []
        for msg in messages:
            api_msg: dict = {"role": msg.role, "content": msg.content}
            if msg.tool_call_id:
                api_msg["tool_call_id"] = msg.tool_call_id
            if msg.name:
                api_msg["name"] = msg.name
            # Echo the assistant's own tool_calls back so the follow-up request
            # is well-formed (OpenAI rejects a tool result without the matching
            # assistant tool_calls preceding it).
            if msg.role == "assistant" and msg.tool_calls:
                api_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            api_messages.append(api_msg)

        api_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ] if tools else None

        body: dict = {
            "model": model,
            "messages": api_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if api_tools:
            body["tools"] = api_tools
            body["tool_choice"] = "auto"

        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )

            if resp.status_code == 429:
                raise LLMAPITimeout("Rate limited")
            if resp.status_code >= 400:
                raise LLMAPIStatusError(resp.status_code, resp.text)

            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]

            tool_calls = []
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    args = tc["function"]["arguments"]
                    if isinstance(args, str):
                        args = json.loads(args)
                    tool_calls.append(LLMToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=args,
                    ))

            return LLMResponse(
                content=msg.get("content", "") or "",
                tool_calls=tool_calls,
                tokens_in=data.get("usage", {}).get("prompt_tokens", 0),
                tokens_out=data.get("usage", {}).get("completion_tokens", 0),
                model=data.get("model", model),
                finish_reason="tool_calls" if tool_calls else choice.get("finish_reason", "stop"),
            )

        except httpx.TimeoutException as err:
            raise LLMAPITimeout("OpenAI API timed out") from err
        except httpx.ConnectError as err:
            raise LLMAPIConnectionError(str(err)) from err
