"""Guard pipeline — post-generation checks, ordered, per-Bot configurable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class GuardResult:
    passed: bool = True
    content: str = ""          # Mutated reply (e.g. regenerated with correction)
    action: str = ""           # redirect | regenerate | escalate | none
    reason: str = ""


# ── C16: uniform guard context ─────────────────────────────────────────────

@dataclass
class GuardContext:
    """Single context object passed to every guard via ``check(ctx)``.

    This replaces the old isinstance-dispatch in GuardPipeline.run() with a
    polymorphic interface — each guard inspects only the fields it needs.
    """

    user_message: str = ""
    reply: str = ""
    scope: dict | None = None
    expected_lang: str = ""
    last_outbound: str | None = None
    llm: Any = None


class Guard(Protocol):
    """Uniform guard interface (C16)."""

    async def check(self, ctx: GuardContext) -> GuardResult: ...


# ── Built-in guards ────────────────────────────────────────────────────────


class ScopeGuard:
    """Cheap classification: is the user intent still in the Skill's scope?"""

    async def check(self, ctx: GuardContext) -> GuardResult:
        scope = ctx.scope or {}
        in_scope_keywords = scope.get("in", [])
        if not in_scope_keywords:
            return GuardResult(passed=True, content=ctx.reply, action="none")

        user_lower = ctx.user_message.lower()
        hits = any(kw.lower() in user_lower for kw in in_scope_keywords)
        if not hits:
            out_policy = scope.get("out_policy", "redirect_then_escalate")
            return GuardResult(
                passed=False,
                content=ctx.reply,
                action=out_policy,
                reason="user_intent_out_of_scope",
            )
        return GuardResult(passed=True, content=ctx.reply, action="none")


class LanguageGuard:
    """Detect if the reply drifted from the resolved conversation language."""

    async def check(self, ctx: GuardContext) -> GuardResult:
        if not ctx.expected_lang or ctx.expected_lang == "any":
            return GuardResult(passed=True, content=ctx.reply, action="none")
        # v1: pass-through. Stretch: fast langdetect library.
        return GuardResult(passed=True, content=ctx.reply, action="none")


class DedupGuard:
    """Never send two identical messages in a row."""

    async def check(self, ctx: GuardContext) -> GuardResult:
        if (
            ctx.last_outbound
            and ctx.reply.strip() == ctx.last_outbound.strip()
        ):
            return GuardResult(
                passed=False,
                content=ctx.reply,
                action="regenerate",
                reason="reply_identical_to_previous",
            )
        return GuardResult(passed=True, content=ctx.reply, action="none")


# ── Pipeline ───────────────────────────────────────────────────────────────


class GuardPipeline:
    """Ordered, pluggable guard execution (C12 + C16).

    Usage::

        pipeline = GuardPipeline()
        results = await pipeline.run(GuardContext(
            user_message=..., reply=..., scope=...,
            expected_lang=..., last_outbound=..., llm=...,
        ))
        for r in results:
            if r.action == "escalate":
                ...
    """

    def __init__(self):
        self._guards: list = [ScopeGuard(), LanguageGuard(), DedupGuard()]

    def add(self, guard):
        self._guards.append(guard)

    async def run(self, ctx: GuardContext) -> list[GuardResult]:
        results: list[GuardResult] = []
        current_reply = ctx.reply
        for guard in self._guards:
            # Build a fresh context with the possibly-mutated reply from
            # the previous guard.
            guard_ctx = GuardContext(
                user_message=ctx.user_message,
                reply=current_reply,
                scope=ctx.scope,
                expected_lang=ctx.expected_lang,
                last_outbound=ctx.last_outbound,
                llm=ctx.llm,
            )
            result = await guard.check(guard_ctx)
            results.append(result)
            if not result.passed:
                current_reply = result.content
        return results
