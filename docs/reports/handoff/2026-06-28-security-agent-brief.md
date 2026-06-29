# Handoff brief — Security remediation

**For:** security-specialist implementing agent.
**Repo:** `ops4ai/cante` (root: `/root/ops4ai/cante`).
**Full audit:** `docs/reports/security/2026-06-28-comprehensive-security-audit.md` (read it for full exploit chains & verification lists).
**Your scope:** the **S\*** findings only. Do **not** rewrite the worker agent loop, the bus, or the DB schema — those belong to the engineer agent (see *Coordination* below).

## Goal
Make Cante safe to ship as open-source on GitHub: the default deploy is not trivially admin-compromisable, the inbound surface is not open, and one tenant/operator cannot read or drive another's data.

## Findings to fix (priority order)

### S4 — Default secrets ship enabled, no startup guard  (🔴 CRITICAL)
- `core/cante/settings.py:29` `jwt_secret="change-me-in-production"`, `:48` `secret_encryption_key="change-me-change-me-change-me!"`, `:62` `admin_password="change-me"`.
- `docker-compose.yml:9-11` hardcodes Postgres `cante:cante`; `:117` hardcodes `AUTHENTICATION_API_KEY=evolution-secret-key`.
- `.env.example:25` ships `EVOLUTION_API_KEY=evolution-secret-key`.
- **Fix:** add `core/cante/security.py::assert_no_default_secrets()` that raises if any secret is still a default. Call it at API + worker startup. Remove hardcoded secrets from `docker-compose.yml` (read from `.env`). Helm: `required` on empty `secrets.*`.
- **Test:** app exits non-zero with defaults; starts with strong values.

### S1 — Multi-tenancy is not enforced  (🔴 CRITICAL)
- Every `models.py` table has `tenant_id`, but **no API query filters by it**; the JWT (`core/cante/auth.py:15-25`) has no `tenant_id` claim; `get_current_user` (`services/api/main.py:19-26`) returns only `sub`/`role`.
- **Fix:**
  1. `create_token(user_id, tenant_id, role)` → add `tenant_id` to the payload (read `User.tenant_id` at login).
  2. `Principal` dataclass (`user_id, tenant_id, role`) returned by `get_current_user`.
  3. **Fail-closed data-layer enforcement:** a `ContextVar` tenant id + SQLAlchemy `do_orm_execute` event on `Base` that *requires* a tenant context for `TenantScoped` models (raises `MissingTenantContext` unless an explicit `bypass_tenant()` is active). Add a `TenantScoped` mixin; make `User/Provider/Skill/Bot/Number/Route/Contact/ContactGroup/Conversation/Message/Learning/Event/AuditLog/Secret` inherit it. Provide a `with_tenant(tenant_id)` ctx mgr for endpoints + worker.
  4. Writes set `tenant_id` server-side, never from client.
- **Test:** two tenants × two users → cross-tenant read returns empty, cross-tenant write 404.

### S2 — Webhook ingress unauthenticated; trigger reuses JWT secret  (🔴 CRITICAL)
- `services/ingress/main.py:27-54` — `POST /channels/{channel_id}/webhook` has **no auth**; `channel_id` unvalidated.
- `services/api/main.py:468-478` — `/v1/triggers` checks `api_key != settings.jwt_secret` (plain `!=`, timing-unsafe, and = the JWT secret).
- **Fix:** ingress — verify a per-channel shared secret (HMAC of body, or `X-Webhook-Token` vs `Number.connection_config['webhook_secret']`); validate `channel_id` → a Number; per-IP rate limit. Triggers — add `settings.trigger_api_key`, compare with `secrets.compare_digest`.
- **Test:** forged webhook → 401; wrong trigger key → 401.

### S3 — SSRF via declared HTTP tools (`allowed_hosts` is dead)  (🔴 CRITICAL)
- `core/cante/tools.py:35` field exists; `services/worker/main.py:83-90` never passes it; `tools.py:40-61` `execute()` never checks it.
- **Fix:** new `core/cante/security.py::is_safe_url(url, allowed_hosts=None)` — reject non-http(s); resolve host DNS; block IPs in loopback, private, link-local (`169.254.0.0/16`), cloud-metadata (`169.254.169.254`), `0.0.0.0`; enforce `allowed_hosts` if set; **re-check on redirect** (or disable redirects). `DeclaredHttpTool.execute` calls it before the request; cap response size; GET/POST only. `_build_tools` populates `allowed_hosts` from skill config.
- **Test:** reject `http://169.254.169.254/...`, `http://postgres:5432`, `http://127.0.0.1:6379`, `file://`; allow allowlisted public host.

### S5 — Operators can do admin-level actions  (🟡 MEDIUM)
- Only `create_user` (`api/main.py:64-65`) and `list_audit` (`:457-458`) require admin. Everything else is `Depends(get_current_user)`.
- **Fix:** `RequireRole("admin")` on create/update/delete of Number/Skill/Provider/Route/User. Operators keep conversation actions **with ownership checks** (see S6).
- **Test:** operator cannot `POST /v1/skills`.

### S6 — IDOR: no ownership/tenant check on `{id}` endpoints  (🟡 MEDIUM)
- `get_conversation`, `takeover`, `send_as_human`, `close_conv`, `update_contact`, `delete_route`, `approve/reject_learning`.
- **Fix:** `load_owned(session, model, id, principal)` that 404s on tenant/ownership mismatch; use everywhere. `send_as_human` must also write an audit row (S11/S12) and route via the conversation's Number.
- **Test:** operator accessing another tenant's conversation → 404.

### S7 — Login: no throttle/lockout; 500 on missing fields  (🟡 MEDIUM)
- `services/api/main.py:46-56` — `data["email"]`/`data["password"]` direct indexing → 500.
- **Fix:** Pydantic `LoginIn` → 422; Redis per-IP + per-email `INCR`+TTL throttle (e.g. 5/15 min); generic 401 message; log attempts.
- **Test:** 422 on missing field; throttle triggers after N tries.

### S10 — JWT: refresh issued but no rotation/revocation; `decode_token` ignores type  (🟡 MEDIUM)
- `core/cante/auth.py:15-28`.
- **Fix:** `decode_token(token, expected_type)` enforcing type; `/v1/auth/refresh` that rotates (new refresh, revoke old `jti` in a Redis set with TTL); add `jti`/`iat`/`nbf`.
- **Test:** refresh token rejected on access-only endpoint; revoked refresh fails.

### S9 — Weak key derivation for secrets at rest  (🟡 MEDIUM, also code C15)
- `core/cante/secrets.py:6-8` — `sha256(passphrase)`, no salt/KDF.
- **Fix:** require `SECRET_ENCRYPTION_KEY` to be a valid 44-char urlsafe-b64 Fernet key (`Fernet.generate_key()`); `_get_fernet` validates; startup guard (S4) rejects weak keys. `.env.example` shows the generate command.
- **Test:** round-trip; weak key rejected at startup.

### S12 — `AuditLog` is never written  (🟢 LOW)
- `models.py:259-269` defined; `list_audit` reads; nothing writes.
- **Fix:** `log_audit(session, principal, action, entity, before, after)`; call from every mutating endpoint (skill/provider/route/number create-update-delete, send_as_human, takeover/close, learning approve/reject, user create).
- **Test:** audit row present after each write.

### Also yours (lower priority): S8 (CORS from env `cors_origins`), S11 (human-send audit+tenant guard), S13 (escape LIKE metachars in `list_contacts`), S15 (`get_qr`/connect stubs → implement or label), S16 (fenced scheduler leader lock), S17 (compose ports to `127.0.0.1`, Redis ACL), S18 (remove `docs/reports/` from `.gitignore`). See the full audit.

## Coordination with the engineer agent (avoid collisions)
- **You own:** `core/cante/security.py` (new), `core/cante/auth.py`, `core/cante/secrets.py`, `services/api/main.py` auth/role/tenant/audit parts, `services/ingress/main.py` webhook auth, `services/scheduler/main.py` lock, `docker-compose.yml` secrets, `.env.example`, `.gitignore`.
- **Engineer owns:** `migrations/`, `core/cante/db.py`, `core/cante/bus.py`, `core/cante/llm.py`, `core/cante/adapters/*`, `core/cante/guards.py`, `core/cante/evolution.py`, `services/worker/main.py`, `services/sender/main.py`, request models + CRUD bodies in `services/api/main.py`, tests/CI.
- **Shared touchpoints — agree before editing:**
  - `TenantScoped` mixin lives in `core/cante/db.py`/`models.py` — you define it, engineer adds it to models + the migration. Coordinate so the migration includes `tenant_id` indexes.
  - `DeclaredHttpTool` in `core/cante/tools.py` — you add `is_safe_url`; engineer wires `_build_tools` to pass `allowed_hosts`. Don't both rewrite `execute()`.
  - `services/api/main.py` is large — you handle auth/tenant/role/audit/CORS; engineer handles request models + CRUD bodies + pagination + metrics. Edit different functions.
- After your work, update `docs/reports/.state.json`: set `findings_fixed` for the security report and flip `status` to `remediated` once S1–S4 are closed.

## Verification (from the audit)
Re-run: (a) two-tenant isolation test, (b) SSRF metadata/internal rejection, (c) default-secret startup refusal, (d) forged-webhook 401, (e) audit-on-write present.
