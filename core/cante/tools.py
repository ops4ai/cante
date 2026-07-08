"""Tool registry — built-in tools + declarative HTTP tools. Exported as LLM function schemas."""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


class Tool(Protocol):
    name: str
    description: str
    parameters: dict  # JSON Schema


# ── Shared HTTP client for DeclaredHttpTool ──────────────────────────────────
# One long-lived client with connection pooling and redirect-following disabled
# (each DeclaredHttpTool is always an egress call to an external API, and
# redirect-following is a SSRF vector). The per-request timeout is passed at
# call time from self.timeout_s.
_tools_client: httpx.AsyncClient | None = None


def _get_tools_http_client() -> httpx.AsyncClient:
    global _tools_client
    if _tools_client is None:
        _tools_client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
    return _tools_client


async def _close_tools_http_client() -> None:
    global _tools_client
    if _tools_client is not None:
        await _tools_client.aclose()
        _tools_client = None


class BuiltinTool:
    """Base for code-defined tools.

    Subclasses set ``name``/``description``/``parameters`` as class attributes
    and override ``execute``; they are instantiated with no arguments. Callers
    may also pass overrides positionally (used by tests).
    """

    name: str = ""
    description: str = ""
    parameters: dict = {}  # JSON Schema; read-only config, shared default is fine

    def __init__(self, name: str | None = None, description: str | None = None, parameters: dict | None = None) -> None:
        if name is not None:
            self.name = name
        if description is not None:
            self.description = description
        if parameters is not None:
            self.parameters = parameters

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
    http_body: str | None = None  # optional JSON template for POST body
    timeout_s: int = 10
    response_mapping: str = "json"
    allowed_hosts: list = field(default_factory=list)

    # Only these methods may be invoked by a model-driven HTTP tool. Anything
    # else (PUT/DELETE/PATCH) is a privilege-escalation footgun for an LLM.
    _SAFE_METHODS = frozenset({"GET", "POST"})

    # Hard cap on bytes read from a tool response — prevents a malicious or
    # misconfigured endpoint from exhausting worker memory.
    _MAX_RESPONSE_BYTES = 1_000_000  # 1 MiB

    @staticmethod
    def _resolve_template(value: str, arguments: dict, context: dict, secrets: dict) -> str:
        """Resolve {arg}, {{context.xxx}}, {{secret:xxx}} in a string."""
        # LLM arguments: {date} -> value
        for key, val in arguments.items():
            value = value.replace(f"{{{key}}}", str(val))
        # Context vars: {{context.from_phone}} -> value (keys prefixed with _ in ctx)
        for key, val in context.items():
            if key.startswith("_"):
                clean = key[1:]  # _from_phone -> from_phone
                value = value.replace(f"{{{{context.{clean}}}}}", str(val))
        # Secrets: {{secret:name}} -> value
        for sk, sv in secrets.items():
            value = value.replace(f"{{{{secret:{sk}}}}}", str(sv))
        return value

    async def execute(self, arguments: dict, context: dict | None = None, secrets: dict | None = None) -> Any:
        import httpx

        method = (self.http_method or "GET").upper()
        if method not in self._SAFE_METHODS:
            raise ValueError(f"DeclaredHttpTool refuses method {method!r} (GET/POST only)")

        ctx = context or {}
        _secrets = secrets or {}

        # URL: substitute {arg} (LLM) + {{context.xxx}} + {{secret:xxx}}
        url = self._resolve_template(self.http_url, arguments, ctx, _secrets)

        # S3: SSRF egress filter — reject internal/metadata/file:// before any
        # request leaves the process. allowed_hosts (per-skill allowlist) is
        # honoured when set.
        from cante.security import is_safe_url

        if not is_safe_url(url, allowed_hosts=self.allowed_hosts):
            raise ValueError(f"DeclaredHttpTool refused unsafe URL: {url!r}")

        # Headers: substitute {{context.xxx}} + {{secret:xxx}} + {arg}
        resolved_headers: dict[str, str] = {}
        for k, v in self.http_headers.items():
            resolved_headers[k] = self._resolve_template(str(v), arguments, ctx, _secrets)
        # Default content-type for POST with a body
        if method == "POST" and "Content-Type" not in resolved_headers:
            resolved_headers["Content-Type"] = "application/json"

        # Body: if http_body template is set, resolve it; elif POST, send arguments as JSON
        json_body: str | None = None
        if self.http_body:
            json_body = self._resolve_template(self.http_body, arguments, ctx, _secrets)
        elif method == "POST":
            # Default: send the LLM-extracted arguments as the JSON body, plus
            # context vars so the endpoint knows who's calling.
            payload = dict(arguments)
            for key, val in ctx.items():
                if key.startswith("_"):
                    payload[key[1:]] = val
            json_body = json.dumps(payload)

        # Redirects are disabled so a safe public host cannot bounce us to an
        # internal address. A 3xx is surfaced as an error rather than followed.
        client = _get_tools_http_client()
        request_kwargs: dict = {"headers": resolved_headers, "timeout": httpx.Timeout(self.timeout_s)}
        if json_body is not None:
            request_kwargs["content"] = json_body
        resp = await client.request(method, url, **request_kwargs)
        if resp.is_redirect:
            raise ValueError(
                f"DeclaredHttpTool refused redirect to {resp.headers.get('location')!r}"
            )
        resp.raise_for_status()

        # Cap response size before parsing.
        raw = await resp.aread()
        if len(raw) > self._MAX_RESPONSE_BYTES:
            raise ValueError(
                f"DeclaredHttpTool response too large: {len(raw)} > {self._MAX_RESPONSE_BYTES} bytes"
            )
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
                result = await tool.execute(arguments, context, secrets)
            else:
                result = await tool.execute(arguments, context)
            return ToolCallResult(name=name, success=True, result=result)
        except Exception as e:
            return ToolCallResult(name=name, success=False, result=None, error=str(e))
