# Code Review — Cante v0.1.0 (Comprehensive)

| | |
|---|---|
| **Date** | 2026-06-28 |
| **Scope** | Full codebase — `core/`, `services/`, `seeds/`, `tests/`, `migrations/`, `deploy/`, `docker-compose.yml`, `Makefile`, `.github/` |
| **Stats** | 24 Python modules, ~2 380 LOC (core 1 060, services 839, tests 89, seeds 77, examples 36) |
| **Reviewer persona** | Senior engineer — focus on correctness, performance/optimization, and unit-test rigor |
| **Prior report** | `2026-06-27-initial-code-review.md` (superseded by this one where they overlap) |
| **Method** | Full read of every source file; cross-checked claims against `README.md`, `GOTCHAS.md`, `CONTRIBUTING.md`, CI |

---

## Executive summary

The architecture is sound and the service decomposition (ingress / api / worker / sender / scheduler over Redis Streams) is the right shape. But the code as written **does not run end-to-end as advertised**, and the initial review missed the biggest reasons why:

1. **There is no database schema.** `migrations/` is empty, there is no `alembic` config, and `Base.metadata.create_all` is never called. `make seed` and every API DB endpoint crash on a fresh database.
2. **The worker never calls an LLM.** `run_agent_loop(...)` is invoked with `llm=None`, which short-circuits to the M1 echo string. The agent loop, tool-calling, circuit breaker, and guards are dead code in the runtime path.
3. **The agent-loop tool-call history is reconstructed wrongly**, so even if an LLM were wired in, the second iteration of any tool call would be rejected by the provider API.
4. **Messages are acked unconditionally**, so redelivery never happens — directly contradicting `GOTCHAS.md §3`.
5. **`bus.consume` swallows every exception and returns `[]`**, which on a Redis outage becomes a 100 %-CPU busy loop with no backoff.

Beyond those, there are real performance issues (no HTTP connection reuse; 7 sequential `COUNT(*)` per dashboard load; missing indexes on every hot list path) and the **test suite does not exercise any of the broken paths above** — `make test` and CI only run `core/tests`, the 483-line API has zero coverage, and the smoke test literally cannot fail.

| Severity | Count |
|----------|-------|
| 🔴 Must fix | 7 |
| 🟡 Should fix | 8 |
| 🟢 Nice to have | 6 |

> **Release framing.** This codebase is intended for **open-source distribution on GitHub** as a skeleton/scaffold — it is *not* expected to be a fully functional solution yet. The bar for that release is intentionally modest: a structured database that is actually created, minimally decent control/auth, and **coherence with the documentation** (README, GOTCHAS, CONTRIBUTING, docstrings must describe what the code actually does — or be fixed to say it's a skeleton). Findings are re-ranked against that bar in the [Open-source release bar](#open-source-release-bar) section below; everything else is hardening for after the release.

---

## Open-source release bar

Three non-negotiables for the OSS release, and the findings that map to each:

1. **A structured DB that is created** → **C1** (no schema exists; `make seed`/API crash on a fresh DB). Blocks the release outright.
2. **Coherence with the documentation** (the code/docs must agree):
   - **C2** — the worker docstring says "M7 complete" and the README sells multi-LLM agent replies, but `run_agent_loop` is called with `llm=None` (echo only). Either wire the LLM or relabel the worker/README as a skeleton.
   - **C3** — the agent loop is documented as supporting tool-calling, but the history is rebuilt wrongly. If shipped as a skeleton, the loop should either be fixed or the tool-call path marked unimplemented.
   - **C4 / C5** — `GOTCHAS.md §3` promises "worker fails the stream entry for redelivery — it never fakes success"; the code acks unconditionally and `bus.consume` swallows all errors. The GOTCHAS file is a promise to OSS users — the code must match it or the GOTCHAS must be corrected.
   - **C6** — README advertises filtering conversations by number; `number_id` is a dead parameter.
   - **C7** — README/.env.example tell users to set `ADMIN_EMAIL`/`ADMIN_PASSWORD`; seeds ignore them.
   - **C20** — `CONTRIBUTING.md` says `make test`; `make test` and CI only run `core/tests`, and the smoke test can't fail. The OSS release's CI must actually run and be able to fail.
3. **Minimally decent control/auth** → cross-references the security report's **S1 / S4 / S5 / S7 / S10** (tenant scoping, default-secret guard, role split, login throttle, JWT hygiene).

Everything in **C8–C19 + C21** is legitimate hardening/performance work but does **not** block an OSS skeleton release — it can be tracked as issues for after launch. The recommendation: ship the skeleton with C1 fixed, docs/code reconciled (C2/C3/C4/C5/C6/C7 either fixed or honestly labelled), a working+failing CI (C20), and the security minimum (S1/S4/S5/S7/S10); defer C8–C19/C21 to the post-release backlog.

---

## Findings index

| ID | Sev | Location | Title |
|----|-----|----------|-------|
| C1 | 🔴 | `migrations/`, `core/cante/db.py` | No database schema is ever created |
| C2 | 🔴 | `services/worker/main.py:144` | Worker never runs the LLM (`llm=None` → echo mode) |
| C3 | 🔴 | `services/worker/main.py:116-121` | Tool-call history reconstructed wrongly → 2nd LLM iteration 400s |
| C4 | 🔴 | `services/worker/main.py:170-172`, `services/sender/main.py:32-33` | Messages acked even on failure (contradicts GOTCHAS §3) |
| C5 | 🔴 | `core/cante/bus.py:49-54`, `:72-76` | `consume`/`create_group` swallow all errors → silent loss + busy-loop |
| C6 | 🔴 | `services/api/main.py:319-331` | `list_conversations` ignores its `number_id` filter |
| C7 | 🔴 | `seeds/__main__.py:15` | Seeds ignore `ADMIN_EMAIL`/`ADMIN_PASSWORD`, hardcode defaults |
| C8 | 🟡 | `adapters/*`, `evolution.py`, `tools.py` | New `httpx.AsyncClient` per call — no connection reuse |
| C9 | 🟡 | `services/api/main.py:443-452` | `metrics_overview` fires 7 sequential `COUNT(*)` round-trips |
| C10 | 🟡 | `core/cante/models.py` | Missing indexes on every hot list/order path |
| C11 | 🟡 | `services/api/main.py` (all `list_*`) | No pagination / counts on any list endpoint |
| C12 | 🟡 | `core/cante/guards.py` vs `worker` | `GuardPipeline` implemented & tested but never called |
| C13 | 🟡 | `core/cante/tools.py:35`, `worker:83-90` | `DeclaredHttpTool.allowed_hosts` is dead code |
| C14 | 🟡 | `services/worker/main.py:139` | Per-conversation lock held across the full LLM call |
| C15 | 🟡 | `core/cante/secrets.py:6-8` | Fernet key derived with `sha256(passphrase)` — no KDF/salt |
| C16 | 🟢 | `core/cante/guards.py:83-91` | `GuardPipeline` uses `isinstance` branching, not polymorphism |
| C17 | 🟢 | `core/cante/bus.py` vs `redis.py:12` | Manual `bytes`-decode despite `decode_responses=True` |
| C18 | 🟢 | `core/cante/evolution.py:93-170` | Local dataclasses redefined inside methods, shadowing `channel.py` |
| C19 | 🟢 | `services/api/main.py` | No request models — every endpoint takes `data: dict` |
| C20 | 🟢 | `tests/smoke.py:56-62`, `Makefile:25-26` | Smoke test can't fail; `make test` skips `tests/` |
| C21 | 🟢 | `services/api/main.py:348-359` | `takeover` semantics inverted (re-activates the bot) |

---

## 🔴 Must fix

### C1 — No database schema is ever created

**Location:** `migrations/` (empty directory), `core/cante/db.py`, `core/pyproject.toml` (lists `alembic>=1.13`), `seeds/__main__.py`, `services/api/main.py`.

**Problem:** There is no `alembic.ini`, no `env.py`, no migration files, and no `Base.metadata.create_all`/init call anywhere in the codebase. The `migrations/` directory is a placeholder. As a result, on a fresh database the tables `users`, `providers`, `skills`, … do not exist.

**Impact:** The README quickstart (`make up && make seed`) and `CONTRIBUTING.md` (`make up && make seed && make test`) both fail: `make seed` (`Makefile:19-20` → `python -m seeds`) opens a session and `INSERT`s into non-existent tables → `UndefinedTable`. Every API endpoint that touches the DB (`/v1/auth/login`, `/v1/numbers`, …) returns 500. The pipeline only appears to work because ingress/worker/sender never query the DB and the worker runs in echo mode (C2).

**Fix:** Pick one mechanism and ship it. Either (a) an Alembic skeleton (`alembic init`, `env.py` wired to `cante.db.Base.metadata`, an initial `0001_create_tables.py` autogenerate) invoked by `make up`/a `db` container entrypoint; or (b) an explicit `init_db()` that runs `async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)` called from the API/scheduler startup (acceptable for v1, not for schema evolution). Add a `make migrate` target and run migrations in the Dockerfile CMD or a one-shot init container.

**Effort:** ~1 h. This is the precondition for everything else.

---

### C2 — Worker never runs the LLM (`llm=None` → echo mode)

**Location:** `services/worker/main.py:143-144`.

```python
tools = _build_tools(None)  # M7: load from Skill in DB (future)
reply, ctx_updates = await run_agent_loop(data.get("body", ""), None, tools)
```

**Problem:** `run_agent_loop` is called with `llm=None`. The function short-circuits:

```python
# services/worker/main.py:99-100
if llm is None or tools is None:
    return f"[Cante M1 echo] Recebi: {user_message[:400]}", ctx
```

So in production the worker **never** loads a Bot/Skill/Provider, never calls an LLM, never runs the guard pipeline, and never persists `context_json`. The agent loop, tool-calling, circuit breaker, and the entire `core/cante/adapters/*` are dead code in the runtime path. `_build_tools(None)` also means declared HTTP tools are skipped (`worker/main.py:81` `if skill_data and "declared" in skill_data`). The module docstring claims "M7 complete" — it is not.

**Impact:** The product does not do what it sells (multi-LLM agent replies). Every reply is the echo string.

**Fix:** In `process`, resolve the conversation/bot/skill/provider from the DB (per `GOTCHAS §1`: open session → read → close → *then* call LLM), build the tool registry from the Skill, instantiate the adapter from the Provider (`api_key_ref` → env or `Secret` table → `decrypt`), and pass the real `llm`/`tools` into `run_agent_loop`. Gate behind a setting so echo mode stays available for tests.

**Effort:** ~½ day (depends on C1). This is the core of the product.

---

### C3 — Tool-call history reconstructed wrongly → 2nd LLM iteration 400s

**Location:** `services/worker/main.py:113-121`.

```python
if response.tool_calls:
    for tc in response.tool_calls:
        result = await tools.execute(tc.name, tc.arguments, ctx, None)
        messages.append(LLMMessage(role="assistant", content=response.content or ""))
        messages.append(LLMMessage(role="tool", content=..., tool_call_id=tc.id, name=tc.name))
```

**Problem:** On a tool call, the assistant message is appended with only `content` — the `tool_calls` it actually issued are dropped. Both provider APIs require the assistant turn that requested a tool to carry the matching `tool_calls`:

- OpenAI: the `tool` message must be preceded by an assistant message whose `tool_calls` contains the same `id`s, or the request is rejected (`400 invalid_request_error`).
- Anthropic: the assistant turn must contain the `tool_use` block the subsequent `tool_result` answers.

So the first tool call succeeds, but the second `llm.complete` is built on a malformed history and will be rejected by the provider. Multi-turn tool use (the whole point of the loop) is broken.

**Impact:** Any conversation that needs more than one tool round-trip fails. Hidden today only because C2 means the LLM is never called.

**Fix:** Give `LLMMessage` an optional `tool_calls` field (or a provider-native `raw` passthrough), have the adapters populate it on responses and re-serialize it on assistant turns, and append a single assistant message carrying all of the turn's tool calls *before* the `tool` messages. Add a unit test with a mocked adapter returning two tool calls and assert the second `complete` receives a well-formed history (this test would have caught the bug).

**Effort:** ~2 h.

---

### C4 — Messages acked even on failure (contradicts GOTCHAS §3)

**Location:** `services/worker/main.py:170-172` (and `services/sender/main.py:32-33`).

```python
for e in await bus.consume(S_IN, GROUP, CONSUMER, count=5, block_ms=5000):
    await process(e, bus, redis)
    await bus.ack(S_IN, GROUP, e.id)
```

**Problem:** `process` is followed by an unconditional `ack`. `process` itself swallows every exception (`worker/main.py:153-154`), so it never raises — which means **ack always runs, even after a transient LLM/Redis/HTTP failure**. The stream entry is consumed and dropped; Redis Streams' at-least-once redelivery never triggers. `GOTCHAS.md §3` explicitly promises the opposite: *"worker fails the stream entry for redelivery — it never fakes success."*

Two related sub-issues:
- When the per-conversation lock is already held, `process` returns early (`worker/main.py:139-140`) and the entry is still acked → the debounced message is silently dropped. That may be acceptable *as debounce*, but it must be an explicit decision, not a side effect of the ack-always pattern.
- The sender (`sender/main.py:32-33`) has the identical pattern; a failed `send_text` (e.g. Evolution 5xx) acks the outbound entry → the reply is lost forever.

**Fix:** Move `ack` inside `process` and only call it on **success**. On failure, leave the entry pending so `XAUTOCLAIM` can redeliver it (see also C5 / the initial report's nice-to-have on `XAUTOCLAIM`, which becomes a *must* once ack is conditional). Differentiate "debounce-drop" (ack, by design) from "processing-failure" (don't ack). Wrap a dead-letter `stream:dead` + max-retry counter for poison messages so redelivery is bounded.

**Effort:** ~2 h (plus XAUTOCLAIM).

---

### C5 — `consume`/`create_group` swallow all errors → silent loss + busy-loop

**Location:** `core/cante/bus.py:49-54` and `:72-76`.

```python
async def consume(self, stream, group, consumer, count=5, block_ms=5000):
    try:
        results = await self._redis.xreadgroup(...)
    except Exception:
        return []
    ...

async def create_group(self, stream, group):
    try:
        await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:
        pass  # Group already exists
```

**Problem:** Both methods catch *every* exception and treat it as "no data / group exists". That conflates three very different conditions: (a) no new messages (normal), (b) `BUSYGROUP` (group already exists — benign), (c) Redis unreachable / wrong type / no such key (fatal). For case (c):

- `consume` returns `[]` instead of raising. In `worker/main.py:168-178` and `sender/main.py:30-35`, the outer `try/except` that's supposed to `sleep(2)` on error **never fires** (consume doesn't raise). On a Redis outage, `xreadgroup` raises immediately, `consume` returns `[]`, the loop spins with no sleep → **100 % CPU, no backoff, no logs**.
- `create_group` silently passing means: if the group was *not* created (e.g. transient error at startup), every later `consume` returns `[]` forever and **no message is ever processed**, with zero log lines.

**Impact:** A Redis blip doesn't degrade — it either busy-loops a core or silently drops all processing. This is exactly the "webhook error path is where a chat bot dies" lesson from `GOTCHAS.md`.

**Fix:** Catch only the specific `ResponseError` for `BUSYGROUP` in `create_group` (re-raise everything else). In `consume`, let connection/protocol errors propagate so the caller's backoff path runs; only swallow the "no messages" case (which isn't an exception — `xreadgroup` returns `[]`). Then ensure the worker/sender main loops actually sleep on raised errors (they already try to).

**Effort:** ~1 h.

---

### C6 — `list_conversations` ignores its `number_id` filter

**Location:** `services/api/main.py:319-331`.

```python
async def list_conversations(
    state: str = "", bot_id: str = "", number_id: str = "",
    _: dict = Depends(get_current_user),
):
    ...
    if state:
        stmt = stmt.where(Conversation.state == state)
    if bot_id:
        stmt = stmt.where(Conversation.bot_id == bot_id)
    # number_id is never used
```

**Problem:** `number_id` is declared as a filter parameter but never applied to the query. The README advertises "Filtered conversations … by bot, number, group, state". Filtering by number silently returns unfiltered results.

**Fix:** `if number_id: stmt = stmt.where(Conversation.number_id == number_id)`. Add a test that asserts the filter narrows results (would have caught it).

**Effort:** 5 min.

---

### C7 — Seeds ignore `ADMIN_EMAIL`/`ADMIN_PASSWORD`, hardcode defaults

**Location:** `seeds/__main__.py:13-16`.

```python
existing = (await session.execute(select(User).where(User.email == "admin@example.com"))).scalar_one_or_none()
if not existing:
    session.add(User(email="admin@example.com", hashed_password=hash_password("change-me"), role="admin"))
```

**Problem:** The seed hardcodes `admin@example.com` / `change-me`. `settings.admin_email` and `settings.admin_password` (`.env.example:9-11`) are never read. The README quickstart tells users to "set at least … `ADMIN_EMAIL`, `ADMIN_PASSWORD`" — those values have no effect. (Security cross-list: default creds are a finding in the security report.)

**Fix:** `email=settings.admin_email`, `hashed_password=hash_password(settings.admin_password)`; refuse to seed if `admin_password == "change-me"`. Test that the seeded user matches settings.

**Effort:** 10 min.

---

## 🟡 Should fix (performance & hardening)

### C8 — New `httpx.AsyncClient` per call (no connection reuse)

**Location:** `core/cante/adapters/anthropic.py:70`, `adapters/openai_compatible.py:64`, `core/cante/evolution.py:78,113,126,151`, `core/cante/tools.py:54`.

**Problem:** Every LLM call, every Evolution call, and every declared-tool call does `async with httpx.AsyncClient(...)` — a fresh TLS handshake + connection pool per request. Under load this is material latency (Anthropic TLS ≈ 100–300 ms on top of every call) and file-descriptor churn.

**Fix:** Construct one long-lived `httpx.AsyncClient` per process (or per adapter instance) with sensible `limits`/`keepalive`, inject it, and close it on shutdown. The adapters already take `api_key`/`base_url` in `__init__` — add the client there.

**Effort:** ~1 h.

---

### C9 — `metrics_overview` fires 7 sequential `COUNT(*)` round-trips

**Location:** `services/api/main.py:443-452`.

**Problem:** Seven separate `await session.execute(select(func.count(...)))` calls, executed serially. Every dashboard load pays 7× DB round-trip latency.

**Fix:** Collapse into one query using conditional aggregation:

```python
counts = (await session.execute(select(
    func.count(Conversation.id).label("total"),
    func.count(Conversation.id).filter(Conversation.state == "needs_human").label("escalated"),
    func.count(Conversation.id).filter(Conversation.state == "active").label("active"),
    func.count(Conversation.id).filter(Conversation.state == "closed").label("closed"),
    func.count(Bot.id).label("bots"),
    func.count(Number.id).label("numbers"),
    func.count(Message.id).label("messages"),
))).one()
```

**Effort:** 15 min.

---

### C10 — Missing indexes on every hot list/order path

**Location:** `core/cante/models.py`.

**Problem:** The tables grow unbounded (no retention) and the list endpoints order/filter on unindexed columns:
- `Message` — no index on `(conversation_id, created_at)`; `get_conversation` does `WHERE conversation_id=? ORDER BY created_at` → seq scan per conversation.
- `Conversation` — `idx_conv_state`, `idx_conv_bot` exist, but `list_conversations` orders by `last_activity_at` (no index) and filters by `bot_id`+`state` (no composite).
- `Contact` — `list_contacts` orders by `last_seen` and `ilike`s `name`/`phone` (no index, no trigram).
- `Learning` — ordered by `created_at` (no index); `AuditLog` same.

**Fix:** Add `Index("idx_msg_conv_created", "conversation_id", "created_at)`, `idx_conv_last_activity`, `idx_contact_last_seen`, `idx_learning_created`, `idx_audit_created`. Consider a partial index `WHERE state='active'` for the common dashboard case. (These belong in the migration from C1.)

**Effort:** ~30 min (with the migration).

---

### C11 — No pagination / counts on any list endpoint

**Location:** every `list_*` in `services/api/main.py` — all are `.limit(50)`/`.limit(100)` with no `offset`, no cursor, no `total`.

**Problem:** No way to page past the first 50 rows; the frontend can't know there's more. The README promises filtering by date + full-text search — there's no pagination contract to support it.

**Fix:** Keyset pagination on the ordering column (e.g. `?before=<last_activity_at>`), plus a `total` count (cache it). Avoid `OFFSET` for large tables.

**Effort:** ~2 h across endpoints.

---

### C12 — `GuardPipeline` implemented and tested but never called

**Location:** `core/cante/guards.py` (full impl + unit tests) vs `services/worker/main.py` (no reference).

**Problem:** The scope/language/dedup guard pipeline exists and is unit-tested, but the worker never constructs or runs it on a reply. Even after C2 wires the LLM, guards won't run unless this is wired. (Dedup against the last outbound is also moot because the worker doesn't read the last outbound.)

**Fix:** After `run_agent_loop` returns a reply, run `GuardPipeline().run(...)` with `last_outbound` loaded from the last `Message(direction='out')` for the conversation, and honour `action` (`redirect`/`regenerate`/`escalate`). Unit-test the wiring.

**Effort:** ~1 h.

---

### C13 — `DeclaredHttpTool.allowed_hosts` is dead code

**Location:** `core/cante/tools.py:35` (field), `services/worker/main.py:83-90` (never passed), `core/cante/tools.py:40-61` (never checked).

**Problem:** The `allowed_hosts` allowlist mandated by the spec exists as a dataclass field and is *never* populated by the worker and *never* enforced in `execute()`. This is both a correctness gap (feature is fake) and a security hole — see **S3** in the security report (SSRF). Fixing it here means making the field functional and *also* resolving DNS to block link-local/metadata IPs (hostname allowlists alone are vulnerable to DNS rebinding and `http://169.254.169.254`).

**Effort:** ~1 h (cross-listed with S3).

---

### C14 — Per-conversation lock held across the full LLM call

**Location:** `services/worker/main.py:139-156`.

**Problem:** `lock:conv:{id}` is set with `ex=60`, then `process` sleeps the debounce *and* awaits the entire LLM loop inside the lock. A slow LLM call (30–60 s) approaches the 60 s TTL; if it exceeds it, the lock expires mid-processing and a second worker can claim the same conversation → duplicate replies. This also serializes all turns for a conversation behind the LLM latency (acceptable for dedup, but the TTL is the bug).

**Fix:** Either raise the TTL above worst-case LLM latency *and* renew it (a heartbeat) during the call, or hold the lock only for the claim+debounce and rely on the stream's per-conversation ordering for the LLM phase. The former is simpler.

**Effort:** ~30 min.

---

### C15 — Fernet key derived with `sha256(passphrase)`, no KDF/salt

**Location:** `core/cante/secrets.py:6-8`.

```python
key = hashlib.sha256(settings.secret_encryption_key.encode()).digest()
return Fernet(base64.urlsafe_b64encode(key))
```

**Problem:** No salt, no KDF, no per-deployment randomness → identical passphrase always yields the identical key, and offline brute-force of the (default!) passphrase is cheap. This is primarily a security issue (see **S9**); listed here because the fix is a code change: use `scrypt`/`pbkdf2_hmac` with a stored salt, or require a 32-byte base64 key directly (`Fernet(...)` accepts one).

**Effort:** ~30 min.

---

## 🟢 Nice to have / quality

### C16 — `GuardPipeline.run` uses `isinstance` branching, not polymorphism
`guards.py:83-91` passes different args per guard type via `isinstance`. Adding a guard means editing the pipeline. Normalize on one `check(ctx: GuardContext)` signature and iterate. ~20 min.

### C17 — Manual `bytes`-decode despite `decode_responses=True`
`core/cante/redis.py:12` sets `decode_responses=True`, so `xreadgroup` already returns `str`. The `isinstance(..., bytes)` branches in `bus.py:44,60-64` are dead. Pick one mode; the dual handling is a trap if the flag ever flips. ~10 min.

### C18 — `evolution.py` redefines local dataclasses inside methods
`evolution.py:93-103,133-143,159-170` define `SentMessage`/`ConnectResult`/`ConnectionStatus` *inside* methods, shadowing the canonical ones in `core/cante/channel.py`, and re-create them on every call. Use the shared definitions from `channel.py`. ~15 min.

### C19 — No request models — every endpoint takes `data: dict`
`services/api/main.py`. Missing fields raise `KeyError` → 500 (not 422), there's no documented contract, and nothing bounds mass-assignment. Use Pydantic request models per endpoint (also fixes the 422/leak issue from the security report). ~1 h.

### C20 — Smoke test can't fail; `make test` skips `tests/`
`tests/smoke.py:56-62`: the stream-length `assert` is inside a `try/except` that prints and continues, so a missing `stream:outbound` still prints "PASSED". Also `Makefile:25-26` and `.github/workflows/ci.yml` run only `cd core && pytest`, so `tests/smoke.py` and `tests/conftest.py` are never executed by `make test` or CI. Fix the smoke assertion and run the whole `tests/` tree in CI. ~20 min.

### C21 — `takeover` semantics inverted
`services/api/main.py:348-359` sets `state="active"` on takeover. A human takeover should move the conversation *out* of the bot's hands (e.g. `human_active`), not re-activate the bot. ~10 min.

---

## Test coverage — what the suite does *not* catch

The user asked specifically that unit tests not "escape". Today they don't catch the issues above because **the broken code is untested**. The suite is 8 tests in `core/tests/` (guards 4, llm dataclasses 3, channel fixture-shape 1); CI and `make test` run *only* that directory.

| Module | LOC | Tested? | Critical untested behavior |
|--------|-----|----------|----------------------------|
| `core/cante/auth.py` | 28 | ❌ none | `create_token`/`decode_token` round-trip, expiry, wrong-secret, `type` enforcement, `verify_password` negative |
| `core/cante/secrets.py` | 14 | ❌ none | encrypt/decrypt round-trip, tamper, wrong-key |
| `core/cante/tools.py` | 115 | ❌ none | URL templating, `{{secret:…}}` resolution, `allowed_hosts` (absent), unknown-tool + exception paths |
| `core/cante/evolution.py` | 176 | ❌ none | `parse_webhook` for text/image/audio/extended/unknown, `_normalize`, `send_text` request shape |
| `core/cante/bus.py` | 76 | ❌ none | publish/consume/ack round-trip, group idempotency, **error swallowing (C5)** |
| `core/cante/guards.py` | 95 | ⚠️ partial | individual guards only; `GuardPipeline.run` ordering/mutation/short-circuit untested |
| `core/cante/adapters/*` | 220 | ❌ none | `complete()` parsing, tool_call `json.loads`, 429/4xx→exception mapping, timeout→`LLMAPITimeout`, `supports()` |
| `services/worker/main.py` | 183 | ❌ none | echo path, **tool-call loop (C3)**, circuit-breaker trip, max-iteration escalation, lock contention |
| `services/api/main.py` | 483 | ❌ none | auth, role enforcement, **tenant isolation (S1)**, every CRUD, 422 handling, **`number_id` filter (C6)** |
| `services/ingress/main.py` | 99 | ❌ none | `_parse`/`_extract` shapes, dedup set/expire, from_me/is_group filtering |
| `services/sender/main.py` | 39 | ❌ none | pacing bounds, ack-on-success-only |
| `services/scheduler/main.py` | 35 | ❌ none | leader election, renewal, follower no-op |

**Specific tests that would have caught the must-fixes:**
- A test feeding `run_agent_loop` a mocked adapter that returns two `tool_calls` and asserting the history passed to the second `complete` is well-formed → catches **C3**.
- A test injecting a broken Redis into `bus.consume` and asserting it raises (not returns `[]`) → catches **C5** and the busy-loop.
- A test calling `list_conversations(number_id=X)` with two conversations on different numbers → catches **C6**.
- A test asserting the seeded user's email/password equals `settings.admin_email`/`admin_password` → catches **C7**.
- A test asserting `DeclaredHttpTool.execute` rejects `http://169.254.169.254` → catches **C13/S3**.
- A test asserting the worker does **not** ack on LLM failure → catches **C4**.

**Existing-test quality issues:**
- `core/tests/test_channel.py` only asserts the *fixture JSON shape*, never calls `EvolutionAdapter.parse_webhook`/`ingress._parse`. Rename and rewrite to exercise the parser.
- `tests/conftest.py` defines `text_webhook`/`media_webhook` fixtures via fragile relative paths; `media_webhook` is never used.
- `tests/smoke.py` "PASSED" output is not backed by a real assertion (C20).

**CI gaps (`.github/workflows/ci.yml`):**
- Runs `cd core && pytest` only — `tests/` (smoke + conftest) and all of `services/` are never run, despite CI already provisioning Postgres + Redis that an integration test could use.
- `mypy cante/` strict runs on `core/` only; `services/` (full of `dict`) is unchecked.
- `--cov-report=term-missing` prints coverage but there's **no coverage gate** — a PR can drop coverage to zero and still pass.
- No integration test exercises ingress → worker → sender against the real Redis.

---

## Code-quality scorecard

| Dimension | Score | Notes |
|-----------|-------|-------|
| Architecture | 9/10 | Right decomposition; clean `core/` seam |
| Correctness | 3/10 | No schema (C1), LLM never called (C2), tool loop broken (C3), ack-always (C4) |
| Performance | 5/10 | No HTTP reuse (C8), 7× COUNT (C9), no indexes (C10), no pagination (C11) |
| Error handling | 4/10 | `bus` swallow-all (C5), `dict` 500s (C19), contradicts GOTCHAS §3/§4 |
| Test coverage | 2/10 | 8 tests, none on services; CI skips most of the tree; smoke can't fail |
| Typing | 6/10 | `core/` well-typed; `services/` uses raw `dict`, untyped by mypy |
| Documentation | 7/10 | Good README/GOTCHAS, but they describe behaviour the code doesn't have |

---

## Recommended fix order

1. **C1** (schema) — unblocks everything.
2. **C2** (wire the LLM) — the product's core.
3. **C3 + C4 + C5** (agent loop, ack semantics, bus errors) — reliability core; pair each with the unit test listed above.
4. **C6, C7** (quick correctness wins).
5. **C8–C11** (performance pass).
6. **C12, C13, C15** (guards, SSRF allowlist, KDF) — overlap with the security report.
7. Test infrastructure: run `tests/` + `services/` in CI, add a coverage gate, fix the smoke test.

**Overall:** The bones are good; the v0.1.0 tag is premature. The four reliability must-fixes (C1–C5) are each small, but until they land the system neither runs nor recovers from failure. None of the must-fixes is architectural — they're wiring and error-path work, all independently testable.
