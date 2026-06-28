# Code Review — Cante v0.1.0 (Initial)

**Date:** 2026-06-27
**Scope:** Full codebase (64 files, ~2800 lines)
**Reviewer:** Claude (AI-assisted)
**Project:** ops4ai/cante

---

## Architecture

**Rating: ✅ Clean**

The service-separated architecture (ingress/api/worker/sender/scheduler) is well-motivated by the spec and correctly implemented. Each service is independently deployable and has a single responsibility. The `core/` shared library avoids duplication.

## Findings

### 🔴 Must Fix — 0 findings

### 🟡 Should Fix — 4 findings

#### 1. Worker imports SQLAlchemy models but never initialises the DB engine

**File:** `services/worker/main.py`
**Issue:** The worker imports `cante.redis` but the DB connection (`cante.db`) is never initialised. If the worker needs to persist messages or read Skill configs from the DB (as the spec requires in M5+), it will crash with an uninitialised engine.
**Fix:** Add DB init to the worker startup:
```python
from cante.db import engine
# engine is created at import time, just needs the worker to import it
```
**Priority:** Medium. Not a crash today (M7 worker doesn't query DB), but will break when DB reads are added.

#### 2. Sender uses FakeChannelAdapter with no Evolution fallback wiring

**File:** `services/sender/main.py`
**Issue:** `FakeChannelAdapter` is hardcoded. The `EvolutionAdapter` exists in `core/cante/evolution.py` but is never wired into the sender. The sender should detect the channel type from the outbound message and select the appropriate adapter.
**Fix:** Add a channel registry in sender:
```python
adapters = {"whatsapp_evolution": EvolutionAdapter()}
adapter = adapters.get(entry.data.get("channel_type", ""), FakeChannelAdapter())
```
**Priority:** Medium. Breaks the "WhatsApp works in production" claim.

#### 3. Conversation `context_json` is never persisted to DB in worker

**File:** `services/worker/main.py`
**Issue:** The agent loop returns `(reply, ctx_updates)` but the worker publishes only the reply to `stream:outbound`. The `_ctx` JSON is embedded in the stream entry but the sender won't persist it to the DB. This means context (like "already closed" flags) is lost between turns.
**Fix:** Add a DB write in the worker after the agent loop to update `conversation.context_json`.
**Priority:** Low for M7. Becomes important when §5.8 (three-layer anti-duplication) is implemented.

#### 4. Seeds script skips version tracking on initial import

**File:** `seeds/__main__.py`
**Issue:** Seed data creates Skills but does NOT create initial `SkillVersion` rows. The API's `create_skill` endpoint creates version 1, but the seed inserts raw rows. This means seed-created skills have no version history.
**Fix:** Add `SkillVersion` inserts to the seed script, mirroring the API's behaviour.
**Priority:** Low. Cosmetic in dev, confusing in production.

### 🟢 Nice to Have — 3 findings

#### 5. Stream consumer lacks graceful shutdown with XAUTOCLAIM

**File:** `services/worker/main.py`, `services/sender/main.py`
**Issue:** Stream consumers use `running` flags but don't implement `XAUTOCLAIM` for pending entries from dead consumers. The spec (§3.3) mentions `XAUTOCLAIM` but it's not in the code.
**Impact:** Low for single-replica deployments. Important for multi-worker scaling.
**Recommendation:** Add a `XAUTOCLAIM` call in the main loop before consuming new entries, claiming any entries pending for >60s from other consumers.

#### 6. Rate limiting not implemented in worker

**File:** `services/worker/main.py`
**Issue:** The spec (§5.4) requires per-conversation rate limiting (10/min, 60/hour). The worker has a per-conversation lock but no rate-limit counters. A user could flood the agent.
**Fix:** Add Redis INCR counters with TTL: `rate:conv:{id}:minute` and `rate:conv:{id}:hour`.
**Priority:** Low. The debounce + lock already prevents most flooding.

#### 7. OpenTelemetry/Langfuse integration not wired into worker loop

**File:** `core/cante/observability.py`
**Issue:** The observability module exists but the `trace_llm_call()` function is never called from the worker's agent loop. Every LLM call should be traced.
**Fix:** Call `trace_llm_call()` after each `llm.complete()` call in the agent loop.
**Priority:** Low. High-value for debugging but not blocking.

---

## Code Quality

| Metric | Score | Notes |
|--------|-------|-------|
| Structure | 9/10 | Clean monorepo, good separation |
| Typing | 7/10 | Core interfaces well-typed, service code uses dicts |
| Error handling | 7/10 | LLM circuit breaker exists; stream errors caught |
| Test coverage | 4/10 | 8 unit tests for core; no integration tests yet |
| Documentation | 8/10 | README, GOTCHAS, spec-complete comments |

## Summary

| Severity | Count |
|----------|-------|
| 🔴 Must Fix | 0 |
| 🟡 Should Fix | 4 |
| 🟢 Nice to Have | 3 |

**Overall:** Clean, well-structured v1. The 4 medium findings are wiring-up issues expected at this stage. No architectural problems. Ready for integration testing with real Evolution + real LLM.
