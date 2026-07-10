"""Cante worker — agent loop with LLM + declarative tool execution. M7 complete."""

import asyncio
import json
import re
import signal
import structlog

from cante.db import build_provider_adapter, resolve_provider_api_key
from cante.bus import RedisStreamsBus
from cante.redis import get_redis, health_redis, on_redis_timeout, on_redis_success
from cante.security import assert_no_default_secrets
from cante.settings import settings
from cante.tools import DeclaredHttpTool, ToolRegistry

logger = structlog.get_logger(__name__)
running = True
S_IN, S_OUT, S_TRIG = "stream:inbound", "stream:outbound", "stream:triggers"
GROUP, CONSUMER = "agent-workers", "worker-1"

# ── Language detection ─────────────────────────────────────────────────

# Common words that strongly indicate a language (case-insensitive match)
_LANG_MARKERS = {
    "pt": [
        "olá", "ola", "bom dia", "boa tarde", "boa noite", "obrigad", "por favor",
        "tudo bem", "como estás", "como vai", "preciso", "ajuda", "quero",
        "não", "sim", "para", "com", "uma", "que", "estou", "muito", "mas",
    ],
    "en": [
        "hello", "hi", "good morning", "good afternoon", "good evening", "thank",
        "please", "how are you", "need", "help", "want",
        "not", "yes", "for", "with", "the", "that", "this", "very", "but",
    ],
    "es": [
        "hola", "buenos días", "buenas tardes", "buenas noches", "gracias",
        "por favor", "cómo estás", "necesito", "ayuda", "quiero",
        "no", "sí", "para", "con", "una", "que", "estoy", "muy", "pero",
    ],
}


def detect_language(text: str) -> str | None:
    """Return the most likely language code for *text*, or None if unclear.

    Returns 'pt-PT' or 'pt-BR' when Brazilian vs European markers are present
    (so the right variant is pinned), falling back to 'pt-PT' for generic PT.
    """
    if not text or not text.strip():
        return None
    lower = text.lower().strip()
    # Brazilian markers (você/pra/a gente/terminações em -i) vs European.
    if any(m in lower for m in ("você", "pra", "a gente", "né", "tá", "tô", "vimos")):
        return "pt-BR"
    if any(m in lower for m in ("bue", "fixe", "pá", "estás", "tens", "posso", "vou")):
        return "pt-PT"
    scores = {}
    for lang, markers in _LANG_MARKERS.items():
        scores[lang] = sum(1 for m in markers if m in lower)
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 2:
        return None
    # Generic PT detection — default to European Portuguese.
    return "pt-PT" if best == "pt" else best


_LANG_INSTRUCTION = {
    "pt-PT": (
        "FALA PORTUGUÊS DE PORTUGAL (PT-PT). Trata o utilizador por 'você'. "
        "NUNCA uses brasileirismos. Exemplos: equipa (não time), telemóvel (não celular), "
        "comboio (não trem), autocarro (não ônibus), pequeno-almoço, casa de banho, estás bom?. "
        "Sê educado, direto, sem emojis desnecessários."
    ),
    "pt-BR": (
        "Fala português do Brasil (PT-BR). Usa 'você' com conjugação brasileira. "
        "Sê caloroso e expressivo."
    ),
    "pt": (
        "FALA PORTUGUÊS DE PORTUGAL (PT-PT). NUNCA uses brasileirismos "
        "(time, celular, trem, banheiro, legal, galera, etc.). Sê educado e direto."
    ),
    "en": "Reply in English. Be clear, concise, and friendly.",
    "es": "Responde en español informal y amigable.",
}

# Map a contact's phone country code to a language variant. +351 = Portugal.
_PHONE_COUNTRY_LANG = {
    "351": "pt-PT",
    "354": "en",   # Iceland
    "353": "en",   # Ireland
    "44": "en",    # UK
    "34": "es",    # Spain
    "55": "pt-BR", # Brazil
    "33": "fr",    # France (added below if needed)
}
_LANG_INSTRUCTION.setdefault("fr", "Réponds toujours en français (informel et amical).")


def _lang_from_phone(phone: str) -> str | None:
    """Infer a language variant from the contact's phone country code.

    A +351 number is overwhelmingly a Portuguese (PT) contact, so we default to
    pt-PT even before any text-based detection. This also resolves the 'which
    Portuguese' ambiguity (PT-PT vs PT-BR) that word markers can't.
    """
    digits = re.sub(r"[^0-9]", "", phone or "")
    # Try longest country-code prefix (2-4 digits).
    for n in (3, 2, 4):
        if len(digits) >= n:
            cc = digits[:n]
            if cc in _PHONE_COUNTRY_LANG:
                return _PHONE_COUNTRY_LANG[cc]
    return None


def build_system_prompt(playbook: str, lang: str | None) -> str:
    """Build the system prompt with language instruction at both ends.

    LLMs weight the beginning and end of prompts more heavily — sandwiching
    the language rule at both ends reinforces it against a playbook that may
    be written in a different language or tone.
    """
    instruction = _LANG_INSTRUCTION.get(lang or "", "")
    if not instruction:
        return playbook
    # Sandwich: instruction at top, playbook in the middle, reminder at bottom
    return (
        f"{instruction}\n\n"
        f"---\n\n"
        f"{playbook}\n\n"
        f"---\n\n"
        f"LEMBRA-TE: {instruction}"
    )


def _sigterm(*_):
    global running
    running = False


def _build_tools(skill_data: dict | None) -> ToolRegistry:
    """Build a tool registry from a Skill's declared tools + built-in toggles."""
    registry = ToolRegistry()

    # Built-in tools
    from cante.tools import BuiltinTool

    class LookupContact(BuiltinTool):
        name = "lookup_or_create_contact"
        description = "Find or create a contact by phone number"
        parameters = {"type": "object", "properties": {"phone": {"type": "string"}, "name": {"type": "string"}}, "required": ["phone"]}
        async def execute(self, arguments, context):
            return {"contact_id": "contact-123", "phone": arguments["phone"], "name": arguments.get("name", "")}

    class EscalateHuman(BuiltinTool):
        name = "escalate_to_human"
        description = "Escalate the conversation to a human operator"
        parameters = {"type": "object", "properties": {"reason": {"type": "string"}}, "required": []}
        async def execute(self, arguments, context):
            context["_escalated"] = True
            return {"escalated": True, "reason": arguments.get("reason", "user_request")}

    class CloseConv(BuiltinTool):
        name = "close_conversation"
        description = "Close the conversation when resolved"
        parameters = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": []}
        async def execute(self, arguments, context):
            context["_closed"] = True
            return {"closed": True}

    class UpdateContext(BuiltinTool):
        name = "update_conversation_context"
        description = "Persist state between turns"
        parameters = {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}
        async def execute(self, arguments, context):
            context[arguments["key"]] = arguments["value"]
            return {"stored": True}

    class GetSummary(BuiltinTool):
        name = "get_conversation_summary"
        description = "Retrieve prior conversation context"
        parameters = {"type": "object", "properties": {}, "required": []}
        async def execute(self, arguments, context):
            return {"context": context}

    class SetContactAttr(BuiltinTool):
        name = "set_contact_attribute"
        description = "Write a structured field on the contact"
        parameters = {"type": "object", "properties": {"attribute": {"type": "string"}, "value": {"type": "string"}}, "required": ["attribute", "value"]}
        async def execute(self, arguments, context):
            return {"set": arguments["attribute"], "value": arguments["value"]}

    registry.register_builtin(LookupContact())
    registry.register_builtin(EscalateHuman())
    registry.register_builtin(CloseConv())
    registry.register_builtin(UpdateContext())
    registry.register_builtin(GetSummary())
    registry.register_builtin(SetContactAttr())

    # Declarative HTTP tools from Skill config
    if skill_data and "declared" in skill_data:
        for dt in skill_data.get("declared", []):
            registry.register_declared(DeclaredHttpTool(
                name=dt["name"], description=dt["description"],
                parameters=dt.get("input_schema", {}),
                http_method=dt["http"]["method"], http_url=dt["http"]["url"],
                http_headers=dt["http"].get("headers", {}),
                http_body=dt["http"].get("body"),
                timeout_s=dt["http"].get("timeout_s", 10),
                response_mapping=dt.get("response_mapping", "json"),
                # SSRF egress allowlist (S3): security's is_safe_url consumes this.
                allowed_hosts=dt.get("allowed_hosts") or skill_data.get("allowed_hosts") or [],
            ))
    return registry


async def run_agent_loop(
    user_message: str, llm=None, tools=None, *, system_prompt: str | None = None,
    history: list[dict] | None = None, tool_context: dict | None = None,
    secrets: dict | None = None,
) -> tuple[str, dict]:
    """Execute the agent loop with tool calling. Returns (reply, context_updates).

    *history* is a list of prior turns ``{role, content}`` (role: user|assistant)
    so the LLM has conversation context — this lets it vary its wording across
    turns instead of repeating the same reply to similar messages.

    *tool_context* carries contact/conversation metadata (from_phone, number_id,
    conversation_id, contact_id, tenant_id) that declared HTTP tools can inject
    in their URL/headers/body via ``{{context.xxx}}`` — so an external endpoint
    knows who's calling without the LLM having to repeat the phone number.

    *secrets* carries secret values (e.g. API tokens) that declared HTTP tools
    can inject in their URL/headers/body via ``{{secret:name}}``.
    """
    from cante.llm import LLMMessage, LLMToolDefinition

    ctx: dict[str, object] = {}
    # Seed the tool context (prefixed with _ so it doesn't leak to the LLM as
    # conversation state; DeclaredHttpTool._resolve_template reads these).
    for k, v in (tool_context or {}).items():
        ctx[f"_{k}"] = v
    if llm is None or tools is None:
        return f"[Cante M1 echo] Recebi: {user_message[:400]}", ctx

    messages = [
        LLMMessage(
            role="system",
            content=system_prompt or "You are a helpful assistant. Be concise. Use tools when needed.",
        ),
    ]
    # Recent conversation turns (oldest first) so the model knows what it already
    # said and can vary phrasing instead of echoing an earlier reply.
    for turn in history or []:
        role = turn.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        messages.append(LLMMessage(role=role, content=turn.get("content", "")))
    messages.append(LLMMessage(role="user", content=user_message))

    tool_defs = [LLMToolDefinition(name=t.name, description=t.description, parameters=t.parameters) for t in tools.list_tools()]
    logger.info("worker.llm_call", tool_count=len(tool_defs), tool_names=[t.name for t in tool_defs])

    failures = 0
    called_tools: set[str] = set()
    hallucination_retries = 0
    for _ in range(settings.max_tool_iterations):
        try:
            response = await llm.complete(messages, tool_defs, temperature=0.7, max_tokens=1024)
            if response.tool_calls:
                logger.info("worker.llm_tool_calls", tool_calls=[tc.name for tc in response.tool_calls])
                # Carry the assistant's own tool_calls into history ONCE, before the
                # tool results — both OpenAI and Anthropic reject a follow-up request
                # that drops the assistant tool_calls the results refer to (C3).
                messages.append(
                    LLMMessage(role="assistant", content=response.content or "", tool_calls=response.tool_calls)
                )
                for tc in response.tool_calls:
                    called_tools.add(tc.name)
                    result = await tools.execute(tc.name, tc.arguments, ctx, secrets)
                    logger.info("worker.tool_executed", tool=tc.name, success=result.success, error=result.error)
                    messages.append(LLMMessage(
                        role="tool",
                        content=json.dumps({"result": result.result}) if result.success else f"Error: {result.error}",
                        tool_call_id=tc.id, name=tc.name,
                    ))
            else:
                content = response.content or ""
                # Guard: detect when the LLM fabricates a numeric code in its text
                # response instead of calling a tool to obtain it. LLMs sometimes
                # invent plausible-looking values (PIN codes, OTP tokens, etc.)
                # rather than invoking the tool that would generate a real one.
                # If the model responded with a 6-digit number but never called
                # any tool, force a retry instructing it to use a tool.
                if (
                    not called_tools
                    and hallucination_retries < 2
                    and re.search(r'\b\d{6}\b', content)
                ):
                    hallucination_retries += 1
                    logger.warning(
                        "worker.hallucinated_code_detected",
                        retry=hallucination_retries,
                        called_tools=list(called_tools),
                    )
                    messages.append(LLMMessage(role="assistant", content=content))
                    messages.append(LLMMessage(
                        role="user",
                        content=(
                            "STOP. You just sent a 6-digit code without calling a "
                            "tool. That code is FAKE and will not work. You MUST "
                            "call the appropriate tool to get a real code. Call "
                            "the tool now — do not respond with text."
                        ),
                    ))
                    continue
                logger.info("worker.llm_text_response", content_length=len(content))
                return content or "Desculpa, não percebi.", ctx
            failures = 0
        except Exception as e:
            failures += 1
            logger.warning("worker.llm_failure", error=str(e), failures=failures)
            if failures >= settings.circuit_breaker_failures:
                return "Desculpa, estou com dificuldades técnicas. Vou pedir a um humano para te ajudar.", {"_escalated": True}
            continue

    return "Vou pedir a um humano para continuar esta conversa.", {"_escalated": True}


async def _resolve_route(from_phone: str, number_phone: str, channel_id: str = ""):
    """Resolve the routing for an inbound message.

    Returns (tenant_id, bot, skill, provider, contact_id, conversation_id,
    number_config, number_phone) or None if no route matches, or "blocked" if
    the contact is blocked. Reads happen in a
    short-lived session (closed before the LLM call — GOTCHAS §1) and the contact
    is upserted with ON CONFLICT (§2).

    The Number is resolved by ``channel_id`` (its UUID, set by the ingress from
    the webhook path) first, falling back to ``number_phone`` for older channels.
    """
    from cante.db import async_session_factory
    from cante.models import Bot, Contact, Conversation, Number, Provider, Route, Skill
    from cante.tenant import with_bypass, with_tenant
    from sqlalchemy import select, or_
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        # Routing resolution is cross-tenant bootstrap (like login): the tenant
        # isn't known until we walk number→route→bot. Read under a bypass, then
        # switch to the resolved tenant for the contact/conversation writes.
        with with_bypass():
            num_stmt = select(Number)
            if channel_id:
                num_stmt = num_stmt.where(or_(Number.id == str(channel_id), Number.phone == number_phone))
            else:
                num_stmt = num_stmt.where(Number.phone == number_phone)
            number = (await session.execute(num_stmt)).scalars().first()
            if not number:
                return None
            route = (
                await session.execute(
                    select(Route).where(Route.number_id == number.id, Route.enabled.is_(True)).order_by(Route.priority.desc())
                )
            ).scalars().first()
            if not route:
                return None
            bot = (await session.execute(select(Bot).where(Bot.id == route.bot_id))).scalar_one()
            if not bot.enabled:
                return None
            skill = (await session.execute(select(Skill).where(Skill.id == bot.skill_id))).scalar_one()
            provider = (await session.execute(select(Provider).where(Provider.id == bot.provider_id))).scalar_one()
            api_key = await resolve_provider_api_key(provider, session)

        tenant_id = bot.tenant_id or "00000000-0000-0000-0000-000000000001"
        with with_tenant(tenant_id):
            # Upsert contact (ON CONFLICT — two concurrent webhooks for the same
            # phone both pass a SELECT; the upsert is race-free, GOTCHAS §2).
            stmt = pg_insert(Contact).values(
                tenant_id=tenant_id, phone=from_phone, name="", attributes={}, first_seen=now, last_seen=now
            ).on_conflict_do_update(index_elements=["phone"], set_={"last_seen": now})
            contact_id = (await session.execute(stmt.returning(Contact.id))).scalar_one()

            # Blocked contacts: silently drop — don't create conversation, don't reply.
            contact_status = (
                await session.execute(
                    select(Contact.status).where(Contact.id == contact_id)
                )
            ).scalar()
            if contact_status == "blocked":
                await session.commit()
                return "blocked"

            conv = (
                await session.execute(
                    select(Conversation).where(
                        Conversation.number_id == number.id,
                        Conversation.contact_id == contact_id,
                        Conversation.state == "active",
                    )
                )
            ).scalars().first()
            if conv is None:
                conv = Conversation(
                    tenant_id=tenant_id, number_id=number.id, bot_id=bot.id, contact_id=contact_id
                )
                session.add(conv)
                await session.flush()
            await session.commit()
        return tenant_id, bot, skill, provider, api_key, contact_id, conv.id, dict(number.connection_config or {}), number.phone


async def _load_history(conv_id: str, tenant_id: str, limit: int = 8) -> tuple[list[dict], str | None]:
    """Load recent turns of the conversation for LLM context + dedup.

    Returns (history, last_outbound):
    - history: up to *limit* prior turns as ``{role, content}`` (role: user for
      inbound, assistant for outbound), oldest-first, so the LLM knows what it
      already said and can vary its wording.
    - last_outbound: the body of the most recent outbound assistant message, for
      the DedupGuard to detect identical consecutive replies.
    """
    from cante.db import async_session_factory
    from cante.models import Message
    from cante.tenant import with_tenant
    from sqlalchemy import select

    async with async_session_factory() as session:
        with with_tenant(tenant_id):
            rows = (
                await session.execute(
                    select(Message.role, Message.direction, Message.body)
                    .where(Message.conversation_id == conv_id)
                    .order_by(Message.created_at.desc())
                    .limit(limit)
                )
            ).all()
    # rows are newest-first. Find the most recent outbound (for the dedup guard)
    # before reversing to oldest-first for the LLM message list.
    last_outbound: str | None = None
    for _role, direction, body in rows:
        if direction == "out":
            last_outbound = body or ""
            break
    history: list[dict] = []
    for _role, direction, body in reversed(rows):
        history.append({
            "role": "assistant" if direction == "out" else "user",
            "content": body or "",
        })
    return history, last_outbound


async def _resolve_language(conv_id: str, contact_id: str, tenant_id: str, body: str, from_phone: str = "") -> str | None:
    """Determine the language for this conversation.

    Priority:
    1. The contact's saved ``preferred_language`` (sticky across conversations).
    2. The contact's phone country code (a +351 number → pt-PT). This is the
       strongest signal for the PT-PT vs PT-BR ambiguity that text markers can't
       reliably resolve.
    3. Text-based detection from *body* (refines the variant when markers exist).
    4. None if inconclusive (the bot's default language applies).
    """
    from cante.db import async_session_factory
    from cante.models import Contact, Conversation
    from cante.tenant import with_tenant
    from sqlalchemy import select, update

    async with async_session_factory() as session:
        with with_tenant(tenant_id):
            # Check contact's preferred language first
            contact = (await session.execute(
                select(Contact).where(Contact.id == contact_id)
            )).scalar_one_or_none()
            if contact:
                attrs = contact.attributes or {}
                stored = attrs.get("preferred_language")
                if stored:
                    # Already known — just update the conversation
                    await session.execute(
                        update(Conversation).where(Conversation.id == conv_id).values(language_detected=stored)
                    )
                    await session.commit()
                    return stored

            # Phone country code (e.g. +351 -> pt-PT) — strongest variant signal.
            phone_lang = _lang_from_phone(from_phone)
            # Text detection refines the variant (pt-BR markers override +55 etc.).
            detected = detect_language(body) or phone_lang
            # If text didn't contradict the phone variant, prefer the phone one
            # so a +351 number always gets PT-PT even with a generic "ola".
            if phone_lang and (not detected or detected.split("-")[0] == phone_lang.split("-")[0]):
                detected = phone_lang
            if detected:
                # Save to conversation
                await session.execute(
                    update(Conversation).where(Conversation.id == conv_id).values(language_detected=detected)
                )
                # Save to contact for future conversations
                if contact:
                    attrs = dict(contact.attributes or {})
                    attrs["preferred_language"] = detected
                    await session.execute(
                        update(Contact).where(Contact.id == contact_id).values(attributes=attrs)
                    )
                await session.commit()
                return detected

            await session.commit()
            return None


async def _persist_message(conversation_id: str, tenant_id: str, direction: str, role: str, body: str, tokens: int = 0) -> None:
    """Persist one Message row in a short-lived session (GOTCHAS §1)."""
    from cante.db import async_session_factory
    from cante.models import Message
    from cante.tenant import with_tenant

    async with async_session_factory() as session:
        with with_tenant(tenant_id):
            session.add(Message(
                tenant_id=tenant_id, conversation_id=conversation_id, direction=direction,
                role=role, body=body, tokens=tokens,
            ))
            await session.commit()


async def process(entry, bus, redis):
    """Process one inbound/trigger entry.

    Raises on unrecoverable failure (the loop acks only on success, C4).
    Returning normally (including a debounce-drop) means ack-by-design.
    """
    data = entry.data
    from_phone = data.get("from_phone", "")
    number_phone = data.get("number_phone", "")
    channel_id = data.get("channel_id", "")
    body = data.get("body", "")
    cid = data.get("conversation_id", from_phone or "unknown")
    lock = f"lock:conv:{cid}"
    # Debounce lock: another worker is already handling this conversation — drop
    # (ack by design). TTL must exceed worst-case LLM latency.
    if not await redis.set(lock, "1", nx=True, ex=settings.worker_lock_ttl):
        return

    # C14: heartbeat keeps the lock alive past the initial TTL so long-running
    # LLM calls don't lose the lock and allow duplicate processing.
    async def _heartbeat() -> None:
        interval = max(settings.worker_lock_ttl // 2, 10)
        while True:
            await asyncio.sleep(interval)
            await redis.expire(lock, settings.worker_lock_ttl)

    heartbeat_task: asyncio.Task | None = None
    try:
        await asyncio.sleep(settings.debounce_ms_default / 1000.0)
        heartbeat_task = asyncio.create_task(_heartbeat())

        # Echo mode (no LLM/DB) — dev / smoke without a configured provider.
        if not settings.worker_llm_enabled:
            reply, ctx_updates = await run_agent_loop(body, None, None)
            skill_scope = {}
        else:
            route = await _resolve_route(from_phone, number_phone, channel_id)
            if route == "blocked":
                logger.info("worker.contact_blocked", from_phone=from_phone)
                return  # silently drop — no reply, no conversation
            if route is None:
                logger.warning("worker.no_route", from_phone=from_phone, number_phone=number_phone, channel_id=channel_id)
                reply = "Sorry, no agent is configured for this number right now."
                ctx_updates = {}
                skill_scope = {}
            else:
                tenant_id, _bot, skill, provider, api_key, _contact_id, conv_id, number_cfg, _num_phone = route
                skill_scope = skill.scope
                # Persist the inbound message before the LLM call.
                await _persist_message(conv_id, tenant_id, "in", "user", body)

                # ── Typing indicator ──────────────────────────────────────
                if number_cfg:
                    try:
                        from cante.evolution import EvolutionAdapter
                        evo = EvolutionAdapter()
                        await evo.send_presence(number_cfg, from_phone, "composing")
                    except Exception:
                        pass  # best-effort: never block the reply for a typing indicator

                # ── Language detection & contact preference ────────────
                pref_lang = await _resolve_language(conv_id, _contact_id, tenant_id, body, from_phone)

                # Load recent turns so the LLM has context (varies wording across
                # turns) and the dedup guard knows the last outbound reply.
                history, last_outbound = await _load_history(conv_id, tenant_id)

                # Metadata passed to declared HTTP tools via {{context.xxx}} so
                # an external endpoint (e.g. a backend API) knows who's calling.
                tool_context = {
                    "from_phone": from_phone,
                    "number_id": channel_id,
                    "conversation_id": conv_id,
                    "contact_id": _contact_id,
                    "tenant_id": tenant_id,
                }

                # Secrets passed to declared HTTP tools for {{secret:xxx}} template
                # resolution. Keys match the {{secret:NAME}} placeholders in skill
                # tool configs. Forks add their own secrets here.
                _secrets: dict[str, str] = {}

                # Build tools + adapter OUTSIDE any DB session (GOTCHAS §1).
                tools = _build_tools(skill.tools)
                llm = build_provider_adapter(provider, api_key)
                system_prompt = build_system_prompt(skill.playbook_md or "", pref_lang)
                reply, ctx_updates = await run_agent_loop(
                    body, llm, tools,
                    system_prompt=system_prompt,
                    history=history,
                    tool_context=tool_context,
                    secrets=_secrets,
                )

        # ── C12: Guard pipeline ──────────────────────────────────────────
        # Run every reply through the ordered guard pipeline. Results are
        # advisory — we log them and publish them alongside the reply so
        # downstream consumers (API, sender, analytics) can take action.
        from cante.guards import GuardContext, GuardPipeline

        guard_pipeline = GuardPipeline()
        guard_results = await guard_pipeline.run(GuardContext(
            user_message=body,
            reply=reply,
            scope=skill_scope,
            last_outbound=last_outbound,
        ))
        # If a guard asked to regenerate (e.g. dedup: reply identical to the
        # previous one), re-run the LLM ONCE with an instruction to reformulate.
        # Generic — applies to any skill; the model varies wording, keeps info.
        if any(gr.action == "regenerate" and not gr.passed for gr in guard_results):
            logger.info("worker.regenerate", conv=cid, reason="dedup_or_guard")
            vary_prompt = (
                system_prompt
                + "\n\nIMPORTANT: Your previous reply was identical (or near-identical) to "
                "one you sent moments ago in this same conversation. Reformulate it: vary "
                "the wording, sentence order, and phrasing while keeping EXACTLY the same "
                "information and intent. Do not repeat the same sentences."
            )
            try:
                reply, ctx_updates = await run_agent_loop(
                    body, llm, tools, system_prompt=vary_prompt, history=history,
                    tool_context=tool_context,
                    secrets=_secrets,
                )
            except Exception as e:
                logger.warning("worker.regenerate_failed", conv=cid, error=str(e))
        for gr in guard_results:
            if not gr.passed:
                logger.info(
                    "worker.guard_fired",
                    reason=gr.reason,
                    action=gr.action,
                    conv=cid,
                )
            if gr.action == "escalate":
                reply = gr.reason  # Surface escalation reason

        # Persist the outbound reply (only in real-LLM mode).
        if settings.worker_llm_enabled and route is not None:
            await _persist_message(conv_id, tenant_id, "out", "assistant", reply)

        await bus.publish(S_OUT, {
            "conversation_id": cid,
            "from_phone": from_phone,
            "number_phone": number_phone,
            "channel_id": channel_id,
            "body": reply,
            "_ctx": json.dumps(ctx_updates),
        })
        logger.info("worker.processed", conv=cid)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        await redis.delete(lock)


async def _on_failure(stream: str, entry, bus, redis) -> None:
    """Handle a failed entry: count retries, dead-letter after N, else leave pending.

    Leaving it pending (no ack) means the XAUTOCLAIM sweep redelivers it (C4).
    """
    key = f"retries:{stream}:{entry.id}"
    retries = await redis.incr(key)
    await redis.expire(key, 86_400)
    if retries >= settings.worker_max_retries:
        await redis.xadd(f"{stream}:dead", entry.data)
        await bus.ack(stream, GROUP, entry.id)
        await redis.delete(key)
        logger.error("worker.dead_lettered", stream=stream, id=entry.id, retries=retries)
    else:
        logger.warning("worker.retry_pending", stream=stream, id=entry.id, retries=retries)


async def _drain(stream: str, bus, redis) -> None:
    """Consume new entries + reclaim stuck pending ones; ack only on success (C4)."""
    for e in await bus.consume(stream, GROUP, CONSUMER, count=5, block_ms=1000):
        try:
            await process(e, bus, redis)
            await bus.ack(stream, GROUP, e.id)
            await redis.delete(f"retries:{stream}:{e.id}")
        except Exception as e_err:
            logger.error("worker.process_failed", stream=stream, id=e.id, error=str(e_err))
            await _on_failure(stream, e, bus, redis)

    # Redelivery sweep: reclaim entries delivered but not acked for > min-idle.
    for e in await bus.claim_pending(stream, GROUP, CONSUMER, settings.worker_claim_min_idle_ms):
        try:
            await process(e, bus, redis)
            await bus.ack(stream, GROUP, e.id)
            await redis.delete(f"retries:{stream}:{e.id}")
        except Exception as e_err:
            logger.error("worker.reclaim_failed", stream=stream, id=e.id, error=str(e_err))
            await _on_failure(stream, e, bus, redis)


async def main():
    global running
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    assert_no_default_secrets()  # S4: refuse to boot with default/empty secrets
    redis = await get_redis()
    bus = RedisStreamsBus(redis)
    await bus.create_group(S_IN, GROUP)
    await bus.create_group(S_TRIG, GROUP)
    logger.info("worker.started")

    # ── Health check (stdlib TCP, no extra dependencies) ──────────────
    async def health_server():
        async def handler(reader, writer):
            try:
                redis_ok = await health_redis()
                status = "200" if redis_ok["ok"] else "503"
                body = f'{{"service":"worker","redis":{redis_ok}}}'
                writer.write(f"HTTP/1.1 {status} OK\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode())
                await writer.drain()
            except Exception:
                writer.write(b"HTTP/1.1 503\r\n\r\n")
            writer.close()

        server = await asyncio.start_server(handler, "0.0.0.0", 9001)
        logger.info("worker.healthz_started", port=9001)
        async with server:
            await server.serve_forever()

    _health_task = asyncio.create_task(health_server())

    while running:
        try:
            await _drain(S_IN, bus, redis)
            await _drain(S_TRIG, bus, redis)
            on_redis_success()
        except Exception as e:
            err = str(e)
            logger.error("worker.loop", error=err)
            if "Timeout" in err:
                reset = on_redis_timeout()
                if reset:
                    logger.warning("worker.redis_reset", reason="consecutive_timeouts")
                    redis = await get_redis()
                    bus = RedisStreamsBus(redis)
                    await bus.create_group(S_IN, GROUP)
                    await bus.create_group(S_TRIG, GROUP)
            await asyncio.sleep(2)

    _health_task.cancel()
    logger.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())
