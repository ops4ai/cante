"""Optional Langfuse / OpenTelemetry observability toggle."""
import os
from cante.settings import settings

_langfuse_available = False
try:
    if settings.langfuse_public_key and settings.langfuse_host:
        import langfuse
        _langfuse = langfuse.Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        _langfuse_available = True
except Exception:
    _langfuse = None


def trace_llm_call(model: str, prompt_tokens: int, completion_tokens: int, latency_ms: float, success: bool):
    """Record an LLM call for observability. No-op if Langfuse not configured."""
    if not _langfuse_available:
        return
    try:
        trace = _langfuse.trace(name="llm_call")
        trace.generation(
            name="chat_completion",
            model=model,
            usage={"input": prompt_tokens, "output": completion_tokens},
            metadata={"latency_ms": latency_ms, "success": success},
        )
    except Exception:
        pass
