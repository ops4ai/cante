"""LLM adapter implementations — one per provider family."""

from cante.adapters.anthropic import AnthropicAdapter
from cante.adapters.openai_compatible import OpenAICompatibleAdapter

__all__ = ["AnthropicAdapter", "OpenAICompatibleAdapter"]
