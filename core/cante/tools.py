"""Tool registry — built-in tools + declarative HTTP tools. Exported as LLM function schemas."""

from dataclasses import dataclass, field
from typing import Any, Protocol


class Tool(Protocol):
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class BuiltinTool:
    name: str
    description: str
    parameters: dict

    async def execute(self, arguments: dict, context: dict) -> Any:
        raise NotImplementedError


@dataclass
class DeclaredHttpTool:
    """A Skill-defined HTTP integration — config, not code."""

    name: str
    description: str
    parameters: dict  # JSON Schema (input_schema from Skill)
    http_method: str
    http_url: str
    http_headers: dict = field(default_factory=dict)
    timeout_s: int = 10
    response_mapping: str = "json"
    allowed_hosts: list = field(default_factory=list)

    async def execute(self, arguments: dict, secrets: dict | None = None) -> Any:
        import httpx

        url = self.http_url
        for key, val in arguments.items():
            url = url.replace(f"{{{key}}}", str(val))

        headers = dict(self.http_headers)
        # Resolve secret references: {{secret:integration_token}}
        resolved_headers = {}
        for k, v in headers.items():
            if v.startswith("{{secret:") and secrets:
                secret_name = v[len("{{secret:"):-2]
                resolved_headers[k] = secrets.get(secret_name, v)
            else:
                resolved_headers[k] = v

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s)) as client:
            resp = await client.request(
                self.http_method, url, headers=resolved_headers
            )
            resp.raise_for_status()
            if self.response_mapping == "json":
                return resp.json()
            return resp.text


@dataclass
class ToolCallResult:
    name: str
    success: bool
    result: Any
    error: str = ""


class ToolRegistry:
    """Holds built-in tools + dynamically registered declared tools for one agent invocation."""

    def __init__(self):
        self._builtins: dict[str, BuiltinTool] = {}
        self._declared: dict[str, DeclaredHttpTool] = {}

    def register_builtin(self, tool: BuiltinTool):
        self._builtins[tool.name] = tool

    def register_declared(self, tool: DeclaredHttpTool):
        self._declared[tool.name] = tool

    def get(self, name: str):
        return self._builtins.get(name) or self._declared.get(name)

    def list_tools(self) -> list:
        return list(self._builtins.values()) + list(self._declared.values())

    def to_llm_schema(self) -> list[dict]:
        schemas = []
        for t in self.list_tools():
            schemas.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            })
        return schemas

    async def execute(self, name: str, arguments: dict, context: dict, secrets: dict | None = None) -> ToolCallResult:
        tool = self.get(name)
        if not tool:
            return ToolCallResult(name=name, success=False, result=None, error=f"Unknown tool: {name}")
        try:
            if isinstance(tool, DeclaredHttpTool):
                result = await tool.execute(arguments, secrets)
            else:
                result = await tool.execute(arguments, context)
            return ToolCallResult(name=name, success=True, result=result)
        except Exception as e:
            return ToolCallResult(name=name, success=False, result=None, error=str(e))
