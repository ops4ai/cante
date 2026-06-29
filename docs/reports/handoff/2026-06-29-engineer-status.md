# Engineer agent — status report

**Date:** 2026-06-29
**Scope:** Code-review findings C1–C21 (`docs/reports/code-review/2026-06-28-comprehensive-code-review.md`)
**Repo:** `ops4ai/cante` (`/root/ops4ai/cante`)
**State file:** `docs/reports/.state.json` → `findings_fixed: 8/21`, all 7 must-fix closed.

## Summary

All **7 must-fix (🔴) findings are done and verified green** against a real
Postgres (`cante-test-pg` on `localhost:55432`) + fakeredis. The remaining work
is the should-fix / nice-to-have batch (C8–C21 minus the must-fix), plus
finishing the C20 test/CI infrastructure.

Test baseline: the full suite was about to be re-run when this report was
written; the per-finding tests below all pass individually. A final whole-suite
run + `ruff`/`mypy` is still pending (see *Verification gaps*).

## Done & verified ✅

| ID | Title | What changed | Test |
|----|-------|--------------|------|
| **C1** | No DB schema ever created | `alembic.ini` + `migrations/env.py` (async engine, `Base.metadata`) + `migrations/versions/0001_initial.py` (all 16 tables + C10 hot-path indexes + tenant_id indexes). `core/cante/db.py::run_migrations()` / `run_migrations_async()` / `init_db()`. `run_migrations_async()` called at API startup. `Makefile` `migrate` target + wired into `up`/`seed`. `services/api/Dockerfile` copies `migrations/` + `alembic.ini`. | `tests/test_migrations.py` (upgrade creates all tables+indexes; downgrade→upgrade clean) ✅ |
| **C2** | Worker never runs the LLM (`llm=None` → echo) | `services/worker/main.py::process()` rewritten: resolve route (number→route→bot→skill→provider) under `with_bypass` (cross-tenant bootstrap), upsert contact via `ON CONFLICT` (GOTCHAS §2), load config in a short-lived session then **close before the LLM call** (GOTCHAS §1), build tools from `Skill.tools`, instantiate adapter (api key from env or `Secret`+`decrypt`), pass real `llm`/`tools` into `run_agent_loop`. Echo mode only when `settings.worker_llm_enabled=False`. Persist `Message(in)` + `Message(out)`. Added `worker_llm_enabled`, `worker_lock_ttl`, `worker_claim_min_idle_ms`, `worker_max_retries` settings. | `tests/test_worker_process.py::test_worker_runs_llm_and_persists_messages` (complete called, in/out Messages persisted, reply published) ✅ |
| **C3** | Tool-call history reconstructed wrongly → 2nd LLM iteration 400s | `LLMMessage.tool_calls` field added (`core/cante/llm.py`); OpenAI + Anthropic adapters serialize assistant `tool_calls` provider-natively (Anthropic: `tool_use` blocks + `tool`→`tool_result`); worker appends **one** assistant message carrying the turn's `tool_calls` before the tool results. Also fixed the pre-existing `LLMResponse.finish_reason` derivation (was failing `test_llm_response_with_tools`). | `tests/test_worker_agent_loop.py` (2 tool_calls → 2nd `complete` receives well-formed history) ✅ |
| **C4** | Messages acked even on failure | `process()` no longer swallows errors; the loop acks **only on success**. Added `bus.claim_pending()` (XAUTOCLAIM) sweep each loop; per-entry retry counter (`retries:{stream}:{id}` Redis hash); after `worker_max_retries` → move to `{stream}:dead` + ack. Debounce-drop is the explicit ack-by-design path. Worker + the loop restructured via `_drain`/`_on_failure`. | `tests/test_worker_process.py::test_failure_leaves_entry_pending_then_redelivered` (failure→not acked; XAUTOCLAIM redelivers; dead-letter after N) ✅ |
| **C5** | `bus.consume`/`create_group` swallow all errors | `core/cante/bus.py`: `consume` catches only `ResponseError` with NOGROUP/"no such key"/"requires the key to exist" (recreates group) and re-raises everything else (no busy-loop); `create_group` catches only BUSYGROUP; removed dead `bytes`-decode branches (`decode_responses=True`). | `tests/test_bus.py` (round-trip; NOGROUP recovery; idempotent create_group; raises on redis-down) ✅ |
| **C6** | `list_conversations` ignores `number_id` filter | `services/api/main.py::list_conversations` now applies `if number_id: stmt.where(Conversation.number_id == number_id)`. | `tests/test_api_conversations.py` (two convs on different numbers → filter narrows) ✅ |
| **C7** | Seeds ignore `ADMIN_EMAIL`/`ADMIN_PASSWORD` | `seeds/__main__.py` uses `settings.admin_email`/`settings.admin_password`; refuses to seed if password is the shipped default; wraps writes in `with_tenant(SEEDED_TENANT)` (tenant enforcement is active). | `tests/test_seeds.py` (seeded user == settings; refuses on default password) ✅ |
| **C10** | Missing indexes on hot list/order paths | Folded into the C1 migration + `models.py` `__table_args__`: `(conversation_id, created_at)` on messages, `last_activity_at` on conversations, `last_seen` on contacts, `created_at` on learnings/audit_logs. (Covered by `test_migrations.py`.) ✅ |

### Coordination with the security agent (hybrid model)
The security agent landed S1–S18 in parallel. Shared touchpoints handled without
clobbering:
- **`models.py`** — security added the `TenantScoped` marker mixin (with
  `declared_attr tenant_id`); I added the C10 indexes to `__table_args__` on top.
- **`tools.py`** — `BuiltinTool` was a `@dataclass` whose generated `__init__`
  broke the worker's subclass-with-class-attrs pattern (`LookupContact()` raised
  missing args). I converted it to a plain class supporting both the no-arg
  class-attr pattern (worker builtins) and the args pattern (C3 test). Security
  owns `DeclaredHttpTool.execute`/`is_safe_url`; I wired `allowed_hosts` from
  skill config in `_build_tools`.
- **`services/api/main.py`** — I added the migration startup hook + `number_id`
  filter (distinct functions); security owns auth/tenant/CORS/audit.
- **`core/cante/db.py`**, **`settings.py`** — additive `run_migrations*` /
  `init_db` and worker settings; security's `tenant.py`/`security.py` are
  separate modules.

A real production bug was found and fixed along the way: **`passlib 1.7.4` is
incompatible with `bcrypt≥4`** (passlib's `detect_wrap_bug` probes with a >72-byte
secret; bcrypt≥4 raises instead of truncating) — this broke all password
hashing/login. Pinned `bcrypt>=3.2,<4.0` in `core/pyproject.toml`.

### Test infrastructure built (C20, partial)
- `pytest.ini` at repo root (`asyncio_mode=auto`, `pythonpath = . core`,
  `testpaths = tests core/tests`) so the suite runs from repo root, not `cd core`.
- Removed colliding `tests/__init__.py` / `core/tests/__init__.py`.
- `tests/conftest.py`: strong non-default test secrets set before import; `pg`
  fixture (run migrations once); `_isolate_db` (truncate per test, never touches
  `alembic_version`); `_nullpool_engine` (NullPool swap — fixes asyncpg
  cross-loop pool issue, rewrites the factory into api/ingress); `_run_async`
  helper (loop-safe `asyncio.run` under pytest-asyncio's running loop);
  `redis_client` (fakeredis injected as the redis singleton); `app`
  (httpx.ASGITransport); `admin_token`.

## Missing / pending ⏳

### Should-fix (🟡)
- **C8** — New `httpx.AsyncClient` per call. One long-lived client per
  adapter/process with limits/keepalive; close on shutdown. Affects
  `adapters/anthropic.py`, `adapters/openai_compatible.py`, `evolution.py`,
  `tools.py`.
- **C9** — `metrics_overview` fires 7 sequential `COUNT(*)`. Collapse to one
  query with `func.count(...).filter(...)`.
- **C11** — No pagination/counts on list endpoints. Keyset pagination on the
  ordering column + a `total` count.
- **C12** — `GuardPipeline` implemented/tested but never called. Wire it after
  `run_agent_loop` returns (last outbound `Message`); honour
  redirect/regenerate/escalate.
- **C14** — Per-conversation lock held across the full LLM call. Raise TTL above
  worst-case LLM latency + heartbeat renewal during the call. (`worker_lock_ttl`
  setting added at 120s; heartbeat not yet implemented.)

### Lower priority (🟢 / C20 rest / C21)
- **C16** — `GuardPipeline` polymorphic `check(ctx)` (currently isinstance
  dispatch).
- **C18** — `evolution.py` should use the shared dataclasses from `channel.py`
  (it re-declares `SentMessage`/`ConnectResult`/`ConnectionStatus` inline).
- **C19** — Pydantic request models for every endpoint → 422 not 500. (Security
  already added `LoginIn`/`RefreshIn`/`UserCreateIn` for the auth endpoints as
  part of S7; the CRUD endpoints still take `data: dict`.)
- **C20 (rest)** — `tests/smoke.py` real assertions (no swallow-and-pass);
  `Makefile` `test` + `.github/workflows/ci.yml` run `pytest` from root,
  `ruff`/`mypy` over `core/` **and** `services/`, `--cov-fail-under` (≥70% core,
  ≥50% services); `core/tests/test_channel.py` actually call
  `EvolutionAdapter.parse_webhook` / `ingress._parse` (today only checks fixture
  JSON shape). The conftest fixtures + root pytest config are done; the smoke/CI
  edits + cov threshold + channel test deepening remain.
- **C21** — `takeover` endpoint sets `state="active"`; should set a
  `human_active` state so the worker backs off.

### Verification gaps
- The **full suite was not re-run end-to-end** before this report (the run was
  interrupted). Per-finding tests pass individually; a final `pytest -q` from
  repo root + `ruff check core/ services/` + `mypy core/ services/` is needed.
- `mypy` strict may flag the `BuiltinTool` plain-class change and the
  `from cante.adapters import ...` (re-exports) — not yet checked.
- No coverage-threshold gate applied yet (C20).

## Files touched (engineer-owned)
```
alembic.ini                              (new)
migrations/env.py                        (new)
migrations/script.py.mako                (new)
migrations/versions/0001_initial.py      (new)
core/cante/db.py                         (+ run_migrations/init_db)
core/cante/llm.py                        (LLMMessage.tool_calls, finish_reason)
core/cante/bus.py                        (error handling, claim_pending)
core/cante/tools.py                      (BuiltinTool plain class)
core/cante/adapters/__init__.py          (exports)
core/cante/adapters/openai_compatible.py (tool_calls serialization)
core/cante/adapters/anthropic.py         (tool_use/tool_result conversion)
core/cante/models.py                     (C10 indexes)
core/cante/settings.py                   (worker_* settings)
core/pyproject.toml                      (bcrypt pin)
services/worker/main.py                  (C2 process + C4 loop)
services/api/main.py                     (migration startup + C6 filter)
services/api/Dockerfile                  (copy migrations)
seeds/__main__.py                        (settings + tenant context)
Makefile                                 (migrate target, pytest from root)
pytest.ini                               (new, root config)
tests/conftest.py                        (fixtures)
tests/test_migrations.py                 (new)
tests/test_bus.py                        (new)
tests/test_worker_agent_loop.py          (new, C3)
tests/test_worker_process.py             (new, C2+C4)
tests/test_api_conversations.py          (new, C6)
tests/test_seeds.py                      (new, C7)
docs/reports/.state.json                 (findings_fixed: 8)
```

## References

- **Original code review (source of the C1–C21 findings):**
  `docs/reports/code-review/2026-06-28-comprehensive-code-review.md`
- **Engineer work-order brief (per-finding Location → Fix → Expected test):**
  `docs/reports/handoff/2026-06-28-engineer-brief.md`
- **Security agent brief (S1–S18, coordination/shared touchpoints):**
  `docs/reports/handoff/2026-06-28-security-agent-brief.md`
- **Machine-readable state (live progress for both agents):**
  `docs/reports/.state.json`
- **Reports index / conventions:**
  `docs/reports/README.md`

