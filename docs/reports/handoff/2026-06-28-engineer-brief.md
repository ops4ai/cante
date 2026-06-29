# Handoff brief — Code remediation (senior engineer)

**For:** senior-programmer implementing agent (performance & code-quality focus, rigorous about unit tests).
**Repo:** `ops4ai/cante` (root: `/root/ops4ai/cante`).
**Full review:** `docs/reports/code-review/2026-06-28-comprehensive-code-review.md` (read it for the full test-coverage matrix & scorecard).
**Your scope:** the **C\*** findings only. Do **not** touch auth/JWT, tenant scoping, the SSRF egress filter, secrets/crypto, or the webhook auth — those belong to the security agent (see *Coordination* below).

## Goal
Make Cante actually run end-to-end and recover from failure, with decent performance and a test suite that catches the bugs below.

## Findings to fix (priority order)

### C1 — No database schema is ever created  (🔴 MUST FIX)
- `migrations/` is empty; no `alembic` config; no `Base.metadata.create_all` anywhere. `make seed` + every API DB endpoint crash on a fresh DB.
- **Fix:** `alembic init` at repo root → `alembic.ini` + `migrations/env.py` wired to `cante.db.Base.metadata` via the async engine. Add `migrations/versions/0001_initial.py` (autogenerate) creating all tables **plus** the missing indexes from C10. Add `core/cante/db.py::run_migrations()` (alembic `upgrade head`) + `init_db()` dev helper; call `run_migrations()` at API startup. `Makefile`: `migrate` target; wire into `up`/`seed`.
- **Test:** `tests/test_migrations.py` — `upgrade head` creates all tables; `downgrade base` → `upgrade head` is clean.

### C2 — Worker never runs the LLM (`llm=None` → echo)  (🔴 MUST FIX)
- `services/worker/main.py:143-144` calls `run_agent_loop(data.get("body",""), None, tools)`; `:99-100` short-circuits to the echo string. `_build_tools(None)` skips declared tools.
- **Fix:** in `process`, resolve conversation via route (from_phone + number_phone) → upsert contact with `ON CONFLICT` (GOTCHAS §2) → load bot→skill→provider (open session → read → **close** → then LLM, GOTCHAS §1) → build tools from the Skill → instantiate adapter (api key from env or `Secret`+`decrypt`) → pass the **real** `llm`/`tools` into `run_agent_loop`. Echo mode only when `settings.worker_llm_enabled=False`.
- **Test:** with a mocked adapter, assert `complete` is actually called and `Message(in/out)` rows are persisted.

### C3 — Tool-call history reconstructed wrongly → 2nd LLM iteration 400s  (🔴 MUST FIX)
- `services/worker/main.py:113-121`: on a tool call, appends `LLMMessage(role="assistant", content=response.content or "")` — dropping the `tool_calls`. Both OpenAI and Anthropic reject the next request.
- **Fix:** add `tool_calls: list[LLMToolCall] | None` to `LLMMessage` (`core/cante/llm.py`). Adapters serialize assistant `tool_calls` provider-natively and parse them back. Append **one** assistant message carrying the turn's `tool_calls` *before* the `tool` messages.
- **Test:** mock adapter returns 2 tool_calls → assert the 2nd `complete` receives a well-formed history (assistant turn with matching tool_calls, then tool messages).

### C4 — Messages acked even on failure (contradicts GOTCHAS §3)  (🔴 MUST FIX)
- `services/worker/main.py:170-172`, `services/sender/main.py:32-33`: `ack` runs unconditionally; `process` swallows all errors (`worker/main.py:153-154`).
- **Fix:** ack **only on success**; on failure leave the entry pending for redelivery. Add an `XAUTOCLAIM` sweep (claim entries pending > 60 s) each loop. Per-entry retry counter (Redis hash `retries:{stream}:{id}`); after N failures move to `stream:dead` and ack. Debounce-drop is a separate, explicit path (ack by design).
- **Test:** LLM failure → entry NOT acked; after XAUTOCLAIM it's redelivered.

### C5 — `bus.consume`/`create_group` swallow all errors → silent loss + busy-loop  (🔴 MUST FIX)
- `core/cante/bus.py:49-54` catches every exception → `[]`; `:72-76` swallows everything.
- **Fix:** `consume` catches only `redis.exceptions.ResponseError` with `NOGROUP` (re-create group) and re-raises everything else (so the caller's backoff runs). `create_group` catches only `BUSYGROUP`. Remove the dead `bytes`-decode branches (C17) — `decode_responses=True` already returns `str`.
- **Test:** `tests/test_bus.py` (fakeredis): publish/consume/ack round-trip; NOGROUP recovery; **raises** on redis-down (no busy-loop).

### C6 — `list_conversations` ignores its `number_id` filter  (🔴 MUST FIX)
- `services/api/main.py:319-331`: `number_id` declared, never applied.
- **Fix:** `if number_id: stmt = stmt.where(Conversation.number_id == number_id)`.
- **Test:** two conversations on different numbers → filter narrows results.

### C7 — Seeds ignore `ADMIN_EMAIL`/`ADMIN_PASSWORD`, hardcode defaults  (🔴 MUST FIX)
- `seeds/__main__.py:15` hardcodes `admin@example.com` / `change-me`.
- **Fix:** use `settings.admin_email` / `settings.admin_password`; refuse to seed if password is default. (Note: the security agent owns the secret guard; you just wire seeds to settings.)
- **Test:** seeded user's email/password equal settings.

### C8 — New `httpx.AsyncClient` per call (no connection reuse)  (🟡 SHOULD FIX)
- `adapters/anthropic.py:70`, `openai_compatible.py:64`, `evolution.py:78,113,126,151`, `tools.py:54`.
- **Fix:** one long-lived `httpx.AsyncClient` per process/adapter with sensible `limits`/keepalive; close on shutdown.
- **Test:** assert a single client is reused across calls (mock the constructor).

### C9 — `metrics_overview` fires 7 sequential `COUNT(*)`  (🟡 SHOULD FIX)
- `services/api/main.py:443-452`.
- **Fix:** one query with conditional aggregates (`func.count(...).filter(...)`).
- **Test:** one SQL statement executed (assert via `mock`/event).

### C10 — Missing indexes on hot list/order paths  (🟡 SHOULD FIX)
- `core/cante/models.py`: `Message` no `(conversation_id, created_at)`; `Conversation` no `last_activity_at`; `Contact` no `last_seen`; `Learning`/`AuditLog` no `created_at`.
- **Fix:** add the indexes in the C1 migration (you own migrations).

### C11 — No pagination / counts on any list endpoint  (🟡 SHOULD FIX)
- All `list_*` are `.limit(50)` with no offset/cursor/total.
- **Fix:** keyset pagination on the ordering column + a `total` count.

### C12 — `GuardPipeline` implemented & tested but never called  (🟡 SHOULD FIX)
- `core/cante/guards.py` vs `services/worker/main.py` (no reference).
- **Fix:** after `run_agent_loop` returns, run `GuardPipeline().run(...)` with `last_outbound` from the last outbound `Message`; honour `action` (redirect/regenerate/escalate).

### C14 — Per-conversation lock held across the full LLM call  (🟡 SHOULD FIX)
- `services/worker/main.py:139`: `ex=60` held during debounce + LLM.
- **Fix:** raise TTL above worst-case LLM latency + heartbeat renewal during the call (or hold only for claim+debounce).

### Also yours (lower priority): C16 (guards polymorphic `check(ctx)`), C18 (`evolution.py` use shared dataclasses from `channel.py`), C19 (Pydantic request models for every endpoint → 422 not 500), C20 (smoke test can't fail; `make test`+CI only run `core/tests`), C21 (`takeover` → `human_active`). See the full review.

## Test & CI work (C20 — high priority for an OSS release)
- `tests/conftest.py`: fixtures — `pg` (dockerized asyncpg, run migrations), `redis` (`fakeredis`), `app` (`httpx.ASGITransport`), `principal` per-tenant. Fix fragile relative paths.
- `tests/smoke.py:56-62`: real assertions, no swallow-and-pass; `make smoke` exits non-zero on failure.
- `Makefile` `test` + `.github/workflows/ci.yml`: run `pytest` from repo root (not `cd core`); run `ruff`/`mypy` over `core/` **and** `services/`; add `--cov-fail-under` (≥70 % core, ≥50 % services).
- `core/tests/test_channel.py`: actually call `EvolutionAdapter.parse_webhook` / `ingress._parse` against fixtures (today it only checks fixture JSON shape).
- Add the tests named under each finding above — they're the regression net.

## Coordination with the security agent (avoid collisions)
- **You own:** `migrations/`, `core/cante/db.py`, `core/cante/bus.py`, `core/cante/llm.py`, `core/cante/adapters/*`, `core/cante/guards.py`, `core/cante/evolution.py`, `core/cante/tools.py` (the wiring, not the SSRF filter), `services/worker/main.py`, `services/sender/main.py`, request models + CRUD bodies in `services/api/main.py`, `tests/`, `Makefile`, CI, `seeds/__main__.py`.
- **Security agent owns:** `core/cante/security.py` (new), `auth.py`, `secrets.py`, tenant scoping, webhook auth, scheduler lock, `docker-compose.yml` secrets, `.env.example`, `.gitignore`.
- **Shared touchpoints — agree before editing:**
  - `TenantScoped` mixin + `models.py` — security defines the mixin; you add it to models + the migration. Include `tenant_id` indexes in C1.
  - `DeclaredHttpTool.execute` in `tools.py` — security adds `is_safe_url`; you wire `_build_tools` to pass `allowed_hosts`. Don't both rewrite `execute()`.
  - `services/api/main.py` — you handle request models + CRUD bodies + pagination + metrics; security handles auth/tenant/role/audit/CORS. Edit different functions.
- After your work, update `docs/reports/.state.json`: set `findings_fixed` for the code-review report and flip `status` to `remediated` once C1–C7 are closed.

## Verification
1. `uv venv && uv pip install -e "core[dev]" fakeredis httpx` → `pytest -q` from repo root (unit + api suites with `httpx.MockTransport`/`fakeredis`).
2. `docker compose up -d postgres redis` → run DB-layer tests against real PG.
3. `make up && make migrate && make seed` → fresh DB, schema created, admin from `.env`.
4. `make smoke` → asserts a reply lands in `stream:outbound`; exits non-zero on failure.
5. CI green locally: `ruff`, `mypy` (core+services), `pytest --cov-fail-under`.
