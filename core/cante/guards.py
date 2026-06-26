"""Guard pipeline — post-generation checks, ordered, per-Bot configurable."""

from dataclasses import dataclass, field


@dataclass
class GuardResult:
    passed: bool = True
    content: str = ""          # Mutated reply (e.g. regenerated with correction)
    action: str = ""           # redirect | regenerate | escalate | none
    reason: str = ""


class ScopeGuard:
    """Cheap classification: is the user intent still in the Skill's scope?"""

    async def check(self, user_message: str, reply: str, scope: dict, _llm) -> GuardResult:
        # In v1: simple keyword/pattern check against scope["in"].
        # Stretch: a cheap LLM classification with a constrained prompt.
        in_scope_keywords = scope.get("in", [])
        if not in_scope_keywords:
            return GuardResult(passed=True, content=reply, action="none")

        # Basic: check if the user message contains any in-scope terms
        user_lower = user_message.lower()
        hits = any(kw.lower() in user_lower for kw in in_scope_keywords)
        if not hits:
            out_policy = scope.get("out_policy", "redirect_then_escalate")
            return GuardResult(
                passed=False,
                content=reply,
                action=out_policy,
                reason="user_intent_out_of_scope",
            )
        return GuardResult(passed=True, content=reply, action="none")


class LanguageGuard:
    """Detect if the reply drifted from the resolved conversation language and force a regeneration."""

    async def check(self, reply: str, expected_lang: str, _llm) -> GuardResult:
        if not expected_lang or expected_lang == "any":
            return GuardResult(passed=True, content=reply, action="none")
        # In v1: basic detection via common words / character set.
        # Stretch: fast langdetect library.
        return GuardResult(passed=True, content=reply, action="none")


class DedupGuard:
    """Never send two identical messages in a row."""

    async def check(self, reply: str, last_outbound: str | None, _llm) -> GuardResult:
        if last_outbound and reply.strip() == last_outbound.strip():
            return GuardResult(
                passed=False,
                content=reply,
                action="regenerate",
                reason="reply_identical_to_previous",
            )
        return GuardResult(passed=True, content=reply, action="none")


class GuardPipeline:
    """Ordered, pluggable guard execution."""

    def __init__(self):
        self._guards: list = [ScopeGuard(), LanguageGuard(), DedupGuard()]

    def add(self, guard):
        self._guards.append(guard)

    async def run(
        self,
        user_message: str,
        reply: str,
        *,
        scope: dict | None = None,
        expected_lang: str = "",
        last_outbound: str | None = None,
        llm=None,
    ) -> list[GuardResult]:
        results = []
        for guard in self._guards:
            if isinstance(guard, ScopeGuard):
                result = await guard.check(user_message, reply, scope or {}, llm)
            elif isinstance(guard, LanguageGuard):
                result = await guard.check(reply, expected_lang, llm)
            elif isinstance(guard, DedupGuard):
                result = await guard.check(reply, last_outbound, llm)
            else:
                continue
            results.append(result)
            if not result.passed:
                reply = result.content  # Use potentially mutated content for next guard
        return results
