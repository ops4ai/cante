# Security Audit — Cante v0.1.0 (Comprehensive)

| | |
|---|---|
| **Date** | 2026-06-28 |
| **Scope** | Full codebase — `core/`, `services/`, `seeds/`, `docker-compose.yml`, `deploy/helm/`, `.env.example`, `.github/` |
| **Reviewer persona** | Application security specialist — threat-model-driven, exploit-prevention focus |
| **Prior report** | `2026-06-27-initial-security-review.md` (superseded; that report rated 0 criticals — this audit disagrees) |
| **Threat model** | Self-hosted multi-tenant control plane + runtime. Trusted: the operator (admin). Untrusted: inbound webhooks, skill authors (operators), proactive-trigger callers, LLM output, end users (contacts). Network: services are *intended* to be internal behind an edge proxy, but `docker-compose.yml` publishes ports to the host. |
| **Method** | Per-file review; traced each untrusted input to its sink; checked every `select`/HTTP call/auth check against the multi-tenant and least-privilege model. |

---

## Executive summary

The initial security review reported **0 criticals**. That is wrong. There are **4 critical, exploitable issues**, any one of which gives an attacker the system:

1. **Multi-tenancy is not enforced** (S1). `tenant_id` exists on every table but no query filters by it and the JWT carries no `tenant_id` claim. Any authenticated user reads and writes every tenant's data.
2. **The webhook ingress and the proactive-trigger endpoint are unauthenticated / use the JWT secret as a shared API key** (S2). Anyone can inject inbound messages (cost abuse, impersonation, phishing) and, with the default secret, forge admin tokens.
3. **Declared HTTP tools are an open SSRF** (S3). `allowed_hosts` is dead code; any operator-level skill author can drive the worker to hit `http://169.254.169.254`, `http://postgres:5432`, or the Evolution API itself.
4. **Default secrets ship enabled with no startup guard** (S4): JWT secret, secrets-encryption key, admin password, Evolution key, and the Postgres/Redis credentials in `docker-compose.yml`. This is the precondition that turns S1–S3 from "needs a foothold" into "trivially exploitable on a default deploy."

The remaining findings (S5–S18) are authorization, IDOR, auth-hardening, audit, and deployment-hygiene issues. **Before public launch, S1–S4 + S5 + S9 + S12 must be closed and re-audited.**

| Severity | Count |
|----------|-------|
| 🔴 Critical | 4 |
| 🟡 Medium | 7 |
| 🟢 Low | 7 |

> **Release framing.** This codebase is intended for **open-source distribution on GitHub** as a skeleton/scaffold — it is *not* expected to be a fully functional solution yet, and "production-hardened" is not the bar. The bar for the OSS release is: a structured DB that is created, **minimally decent control/authentication**, and coherence with the docs. Translated to security, "minimally decent" means: the default deploy is not trivially admin-compromisable, the inbound surface is not open, and one tenant/operator cannot read or drive another's data. Findings are re-ranked against that bar in [Open-source release bar](#open-source-release-bar) below; deeper hardening (KDF, refresh rotation, audit, CORS tuning, etc.) can follow after launch.

---

## Open-source release bar

The OSS release is blocked until the "minimally decent control/auth" bar is met. The release-blocking security findings are:

- **S4** — default secrets enabled with no startup guard. *The* minimum: the app must refuse to start with default `JWT_SECRET`/`SECRET_ENCRYPTION_KEY`/`ADMIN_PASSWORD`, and hardcoded secrets must leave `docker-compose.yml`. Otherwise the GitHub-published quickstart ships a remotely-admin-compromisable default deploy.
- **S1** — multi-tenancy not enforced. The minimum: `tenant_id` in the JWT and a data-layer scope so one user can't read/write another tenant's data. Without it, "multi-tenant control plane" in the README is a false claim.
- **S2** — unauthenticated webhook + trigger key == JWT secret. The minimum: a per-channel webhook secret/signature and a dedicated, constant-time- compared `TRIGGER_API_KEY`. An open inbound endpoint in an OSS release will be abused (cost/impersonation) the moment users connect a real LLM.
- **S5** — operators can do admin-level actions (create skills/providers/routes, human-send). The minimum: skill/provider/route/number management is admin-only; an operator must not be able to escalate themselves via a Skill.
- **S7** — login has no throttle and 500s on bad input. The minimum: 422 on bad input + per-IP/per-email rate limiting. Credential stuffing on an OSS default deploy is the obvious attack.
- **S10 (light)** — at minimum, `decode_token` must enforce token type and there must be a way to revoke. Full refresh rotation can come later, but accepting a refresh token where an access token is required is a release bug.

**S3 (SSRF) deserves a release-time decision, not a silent ship:** declared HTTP tools with a dead `allowed_hosts` is a footgun OSS users will hit. Either enforce the allowlist (with IP/egress filtering) before release, or **ship declared HTTP tools disabled-by-default** behind a setting + a clear warning, so the SSRF surface isn't on by default for every person who clones the repo.

The rest (**S6, S8, S9, S11, S12–S18**) is real hardening but does not block a *skeleton* OSS release; it should be filed as post-release issues, with S9 (KDF), S12 (audit), and S6 (object-level ownership) prioritized right after.

---

## Findings index

| ID | Sev | Location | Title |
|----|-----|----------|-------|
| S1 | 🔴 | `services/api/main.py` (all `select`), `auth.py`, `models.py` | Multi-tenancy not enforced — full cross-tenant read/write |
| S2 | 🔴 | `services/ingress/main.py:27-54`, `services/api/main.py:468-478` | Webhook unauthenticated; trigger reuses JWT secret as API key (timing-unsafe) |
| S3 | 🔴 | `core/cante/tools.py:35,40-61`, `worker:83-90` | SSRF via declared HTTP tools — `allowed_hosts` is dead |
| S4 | 🔴 | `settings.py:29,48,62`, `docker-compose.yml:9-11,117`, `.env.example` | Default secrets enabled, no startup guard |
| S5 | 🟡 | `services/api/main.py` (role deps) | Operators can perform admin-level actions (skills, providers, routes, human-send) |
| S6 | 🟡 | `services/api/main.py` (`/{id}` endpoints) | IDOR — no ownership/tenant check on object-id endpoints |
| S7 | 🟡 | `services/api/main.py:46-56` | Login: no throttle/lockout, 500 on missing fields |
| S8 | 🟡 | `services/api/main.py:15` | CORS `allow_origins=["*"]` |
| S9 | 🟡 | `core/cante/secrets.py:6-8` | Weak key derivation (no KDF/salt) |
| S10 | 🟡 | `core/cante/auth.py` | Refresh token issued but no rotation/revocation; `decode_token` doesn't enforce type |
| S11 | 🟡 | `services/api/main.py:362-376` | Human-send has no audit trail / tenant guard |
| S12 | 🟢 | `models.py:259-269`, `api/main.py:457-463` | `AuditLog` never written anywhere |
| S13 | 🟢 | `services/api/main.py:263` | LIKE search unescaped — wildcard DoS |
| S14 | 🟢 | `services/api/main.py` | Unhandled `KeyError` → 500 leaks field names |
| S15 | 🟢 | `services/api/main.py:95-107` | `get_qr`/`connect`/`disconnect` are stubs returning internal hostnames |
| S16 | 🟢 | `services/scheduler/main.py:19,29` | Leader lock renewable with `nx=False` — can be stolen |
| S17 | 🟢 | `docker-compose.yml`, `deploy/helm/values.yaml` | DB/Redis ports published; Redis has no auth |
| S18 | 🟢 | `.gitignore`, `docs/reports/README.md` | `docs/reports/` is gitignored despite README claiming reports are committed |

---

## 🔴 Critical

### S1 — Multi-tenancy is not enforced (full cross-tenant read/write)

**Location:** `core/cante/models.py` (every table has `tenant_id`), `services/api/main.py` (every `select`), `core/cante/auth.py:15-28`.

**Problem:** The models declare `tenant_id` on every entity ("multi-tenant seam", `models.py:1`), but **no API query filters by it**, and the JWT contains no `tenant_id` claim:

- `create_token(user_id, role)` (`auth.py:15-25`) mints tokens with only `sub` + `role` + `type` — no `tenant_id`.
- `get_current_user` (`api/main.py:19-26`) returns that payload — no tenant context anywhere.
- `list_conversations`, `list_contacts`, `list_bots`, `list_numbers`, `list_skills`, `list_providers`, `list_routes`, `list_groups`, `list_learnings`, `get_conversation`, `metrics_overview`, `list_audit` all do `select(Entity)` with **no `where(Entity.tenant_id == …)`**.
- Writes are the same: `create_bot`, `create_skill`, `create_provider`, `create_route`, `takeover`, `send_as_human`, `close_conv`, `update_contact`, `approve_learning`, `reject_learning` neither read nor set `tenant_id` from the caller.

**Exploit:** Operator in tenant A logs in (or, with S4, anyone using the default admin creds). They call `GET /v1/conversations` and receive **every conversation across all tenants**, then `POST /v1/conversations/{any-uuid}/send` to inject messages into tenant B's contacts, `POST /v1/conversations/{any-uuid}/close` to kill tenant B's active chats, `PATCH /v1/contacts/{any-uuid}` to alter tenant B's contact records. There is no tenant boundary at all.

**Fix (defense in depth, all required):**
1. Add `tenant_id` to the JWT in `create_token` (read from `User.tenant_id` at login).
2. Make `get_current_user` return a typed principal with `tenant_id`; add a `Depends` that every endpoint uses.
3. Enforce tenant scoping at the data layer, not per-endpoint: either a SQLAlchemy `with_loader_criteria`/`before_compile` event that injects `Entity.tenant_id == principal.tenant_id` on every query for tenant-scoped models, or a `select(...).where(tenant)` helper used everywhere with a lint rule. Per-endpoint filtering will be forgotten.
4. On writes, set `tenant_id = principal.tenant_id` server-side (never trust the client).
5. Add a test matrix: two tenants, two users; assert cross-tenant reads return empty and cross-tenant writes 404.

**Effort:** ~1 day. This is the highest-priority fix.

---

### S2 — Webhook ingress unauthenticated; trigger endpoint reuses the JWT secret

**Location:** `services/ingress/main.py:27-54` and `services/api/main.py:468-478`.

**Problem (a — ingress):** `POST /channels/{channel_id}/webhook` has **no authentication at all** — no signature check, no shared secret, no `channel_id` validation. The `channel_id` is a path param taken at face value. Anyone can POST a forged Evolution payload:

```python
# ingress/main.py:27-54
@app.post("/channels/{channel_id}/webhook")
async def webhook(channel_id: str, request: Request):
    raw = await request.json()
    ...  # dedup + publish to stream:inbound
```

A forged `from_phone` + `body` is enqueued and reaches the worker (which, per C2, echoes today, but will call the LLM once wired) → **unbounded LLM cost abuse**, contact impersonation, conversation pollution, and the ability to drive the bot to phish contacts via tool calls. Because dedup keys on `channel_message_id`, an attacker who varies that field bypasses dedup entirely.

**Problem (b — triggers):** `POST /v1/triggers` authenticates with:

```python
# api/main.py:470-472
api_key = request.headers.get("X-API-Key", "")
if api_key != settings.jwt_secret:
    raise HTTPException(401, "Invalid API key")
```

Three problems: (1) it uses the **JWT secret** as the trigger API key — compromising the trigger key = forging admin JWTs; (2) it's a **plain `!=` string comparison**, which is timing-attack-vulnerable for a secret; (3) the default value is `"change-me-in-production"` (S4).

**Fix:**
- Ingress: verify a per-channel shared secret / HMAC signature. Evolution supports webhook signing; validate `X-Hub-Signature-256`-style headers (or a `?token=` per-channel secret stored in `Number.connection_config`). Reject unknown `channel_id`s. Add `make`-level rate limiting per IP.
- Triggers: add a dedicated `trigger_api_key` setting (random, not the JWT secret); compare with `secrets.compare_digest` (constant time); rotate independently.
- Tests: forged webhook without signature → 401; trigger with wrong key → 401 and constant-time.

**Effort:** ~3 h.

---

### S3 — SSRF via declared HTTP tools (`allowed_hosts` is dead code)

**Location:** `core/cante/tools.py:35` (field), `:40-61` (execute), `services/worker/main.py:83-90` (never passes it).

**Problem:** `DeclaredHttpTool` has an `allowed_hosts` field — but the worker never populates it when building tools from a Skill, and `execute()` never checks it. So a Skill's declared HTTP tool can call **any URL the worker container can reach**:

```python
# tools.py:40-57 — url built from skill-controlled http_url, no host check
url = self.http_url
for key, val in arguments.items():
    url = url.replace(f"{{{key}}}", str(val))
async with httpx.AsyncClient(...) as client:
    resp = await client.request(self.http_method, url, headers=resolved_headers)
```

Who can create a Skill? Today (see S5) **any authenticated user** can `POST /v1/skills`. So any operator can declare a tool like:

```json
{"http": {"method": "GET", "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"}}
```

or `http://postgres:5432` (port-scan / protocol confusion), `http://redis:6379` (Redis over HTTP can issue `FLUSHALL`-shaped payloads), or `http://evolution:8080/message/sendText/<instance>` to **drive the victim's WhatsApp account** from the worker. The tool's URL also accepts path-templated `{key}` from the LLM, so even a "benign" URL can be redirected by an attacker-controlled argument (the LLM is untrusted input).

This is a server-side request forgery primitive that pivots from "operator" to "internal network + cloud metadata + the WhatsApp account".

**Fix (defense in depth):**
1. Make `allowed_hosts` *required and enforced*: resolve the URL host, reject if not in the allowlist. **Also resolve DNS and reject link-local (169.254.0.0/16), loopback, private ranges intended to be blocked, and cloud-metadata endpoints** — a hostname allowlist alone is vulnerable to DNS rebinding and to `http://169.254.169.254` (which has no hostname).
2. Pin the scheme to `https` (or `http` only for explicitly-allowed internal services).
3. Strip request headers that could reach internal services; cap response size and method (`GET`/`POST` only).
4. Restrict Skill creation to admin (S5), and review declared tools at save time.
5. Test: a tool pointing at `169.254.169.254` is rejected; an LLM-supplied `{key}` cannot change the host.

**Effort:** ~1 day (the DNS/egress logic is the real work; a naive hostname check is insufficient).

---

### S4 — Default secrets enabled, no startup guard

**Location:** `core/cante/settings.py:29` (`jwt_secret="change-me-in-production"`), `:48` (`secret_encryption_key="change-me-change-me-change-me!"`), `:62` (`admin_password="change-me"`); `docker-compose.yml:9-11` (Postgres `cante:cante`), `:117` (`AUTHENTICATION_API_KEY=evolution-secret-key`); `.env.example:25` (`EVOLUTION_API_KEY=evolution-secret-key`); `deploy/helm/values.yaml` (`secrets.jwtSecret: ""`, etc.).

**Problem:** Every secret has a publicly-known default, and **nothing refuses to start** if the default is left in place. On a default `make up`:
- `jwt_secret` is `"change-me-in-production"` → anyone can forge an admin JWT (then S1/S5/S6 give them everything).
- `secret_encryption_key` is known → every `Secret.value_encrypted` and any provider key stored via the Secret table is decryptable by anyone with DB read access.
- `admin_password` is `"change-me"` and seeds hardcode it (C7) → known admin login.
- Evolution `AUTHENTICATION_API_KEY=evolution-secret-key` is in the compose file → anyone who can reach `:8088` drives the WhatsApp instance.
- Postgres `cante:cante` and Redis (no auth, S17) are published to the host.

The initial review flagged the JWT/encryption defaults as "medium" and called them "config hardening, not code vulnerabilities." That understates it: with these defaults the system is **remote-admin-compromisable on a default deploy**, which is critical.

**Fix:**
1. Startup guard in the API (and worker) `main`:
   ```python
   _INSECURE = {"change-me-in-production", "change-me-change-me-change-me!", "change-me"}
   if settings.jwt_secret in _INSECURE or settings.secret_encryption_key in _INSECURE:
       raise RuntimeError("Refusing to start: JWT_SECRET / SECRET_ENCRYPTION_KEY are still defaults")
   ```
2. Remove hardcoded secrets from `docker-compose.yml` (read everything from `.env`, which is gitignored).
3. Generate strong defaults for dev (e.g. `secrets.token_urlsafe(32)` written to a local `.env.dev`) instead of known strings.
4. Helm: fail `helm install` if `secrets.jwtSecret`/`encryptionKey` are empty (a `required` template).
5. Test: app exits non-zero with defaults; starts with strong values.

**Effort:** ~1 h.

---

## 🟡 Medium

### S5 — Operators can perform admin-level actions

**Location:** `services/api/main.py` — role dependencies. Only `create_user` (`:64-65`) and `list_audit` (`:457-458`) use `RequireRole("admin")`. **Every other endpoint** uses `Depends(get_current_user)`, i.e. any authenticated user.

**Problem:** An operator can: create/update/delete Numbers, Bots, Skills (→ inject SSRF tools, S3), Providers (store `api_key_ref`/`params`), Routes; `send_as_human` into any conversation; `takeover`/`close` any conversation; approve/reject Learnings. Managing skills/providers/routes is admin-level — an operator who can edit a Skill can add an SSRF tool and pivot (S3).

**Fix:** Split permissions: skill/provider/route/number management and user management → admin-only; conversation actions (takeover/close/send) → admin or assigned-operator with tenant+ownership checks (S1/S6). Consider a proper role/permission table rather than two hardcoded roles.

**Effort:** ~2 h (+ tests).

---

### S6 — IDOR: no ownership/tenant check on object-id endpoints

**Location:** `services/api/main.py` — `get_conversation`, `takeover`, `send_as_human`, `close_conv` (`:335-390`), `update_contact` (`:268-282`), `delete_route` (`:240-251`), `approve_learning`/`reject_learning` (`:407-434`), `update_bot`/`update_skill`.

**Problem:** Each takes a bare `{id}` and acts with no verification the object belongs to the caller's tenant (or that the caller has any business with it). Combined with S1, this is full cross-tenant read/write; even within one tenant, UUIDs are enumerable and there's no per-object authorization. `send_as_human` (`:362-376`) is especially dangerous: it publishes an arbitrary `body` to `stream:outbound` for *any* conversation UUID, with `from_phone`/`number_phone` sent empty (mis-routed, but still enqueued).

**Fix:** Centralize a `load_owned(session, model, id, tenant_id)` that 404s on mismatch; use it everywhere. For `send_as_human`, also require the conversation to be in a human-handoff state and write an audit row (S12).

**Effort:** ~2 h (mostly mechanical, after S1's tenant principal exists).

---

### S7 — Login: no throttle/lockout; 500 on missing fields

**Location:** `services/api/main.py:46-56`.

**Problem:** `data["email"]`/`data["password"]` are direct dict lookups → a missing field raises `KeyError` → 500 (not 422), leaking the missing field name. There is no per-IP or per-email rate limit or lockout; bcrypt verify is online and un-throttled → credential stuffing is unchecked. (`verify_password` itself is constant-time via bcrypt; the `not user or not verify_password(...)` short-circuit is acceptable.)

**Fix:** Pydantic `LoginIn` model → 422 on bad input; add `slowapi`/Redis per-IP+per-email throttling (e.g. 5/15 min) with exponential lockout; return a generic 401 message; log attempts to audit.

**Effort:** ~1 h.

---

### S8 — CORS `allow_origins=["*"]`

**Location:** `services/api/main.py:15`.

**Problem:** Lower severity than the initial review implied — auth is a Bearer `Authorization` header, not cookies, and `allow_credentials` defaults to `False`, so browsers won't attach the victim's token automatically. But `*` still lets any origin *read* API responses if a script can obtain a token (e.g. from local storage on a compromised page) and construct the request, and it signals zero origin discipline.

**Fix:** `allow_origins` from a `CORS_ORIGINS` env (comma-separated), defaulting to the dashboard origin in prod; keep `allow_credentials=False` since auth is header-based.

**Effort:** 15 min.

---

### S9 — Weak key derivation for secrets at rest

**Location:** `core/cante/secrets.py:6-8`.

**Problem:** `Fernet(base64.urlsafe_b64encode(sha256(passphrase)))` — no salt, no KDF, no per-deployment randomness. Identical passphrase → identical key; offline brute-force of the default (S4) is trivial. Anyone with DB read access + the default key decrypts every stored secret.

**Fix:** Use `hashlib.scrypt` (or `pbkdf2_hmac`) with a random salt stored alongside the ciphertext (or a per-deployment salt in settings), high cost; or require a 32-byte urlsafe-b64 key directly (`Fernet(key)`). Rotate-and-re-encrypt path for key changes.

**Effort:** ~30 min (plus migration for any existing ciphertexts).

---

### S10 — JWT: refresh issued but no rotation/revocation; `decode_token` ignores type

**Location:** `core/cante/auth.py:15-28`.

**Problem:** `create_token` issues a 7-day refresh token (`:21-24`) but there's **no `/refresh` endpoint and no revocation list** — a leaked refresh token is valid for a week with no way to kill it. `decode_token` (`:27-28`) doesn't enforce `type`; only `get_current_user` checks `type == "access"` (`api/main.py:22`). Any future code path that calls `decode_token` directly will accept a refresh token as an access token.

**Fix:** Add `/v1/auth/refresh` that rotates (new refresh, blacklist the old) and a Redis revocation set keyed by `jti`; add `jti`/`iat`/`nbf` claims; centralize type enforcement inside `decode_token` (accept a `expected_type` arg). Test that a refresh token is rejected on access-only endpoints.

**Effort:** ~2 h.

---

### S11 — Human-send has no audit trail and no tenant guard

**Location:** `services/api/main.py:362-376`.

**Problem:** `POST /v1/conversations/{conv_id}/send` lets any operator (S5) publish an arbitrary `body` to any conversation (S6), with no `AuditLog` written. An operator can impersonate the bot to any contact with **no record**. The `AuditLog` table exists (`models.py:259-269`) but is never written anywhere (S12).

**Fix:** Require admin (or assigned operator) + tenant ownership; write an `AuditLog(actor, action="human_send", entity="conversation", after={body})`; route the message with the real `from_phone`/`number_phone` from the conversation's Number, not empty strings.

**Effort:** ~1 h.

---

## 🟢 Low

### S12 — `AuditLog` is never written
`models.py:259-269` defines it, `list_audit` (`api/main.py:457-463`) reads it, but no endpoint inserts a row. For a control plane, every mutating action (skill/provider/route create/update/delete, send_as_human, takeover/close, learning approve/reject, user create) must be audited. Add a `log_audit(session, actor, action, entity, before, after)` helper and call it from every write path. ~2 h.

### S13 — LIKE search unescaped (wildcard DoS)
`api/main.py:263` `Contact.name.ilike(f"%{search}%")`. Bound (not injectable) but the user controls `%`/`_`, enabling expensive leading-`%` scans. Escape metachars or use `pg_trgm`/full-text. ~15 min.

### S14 — `KeyError` → 500 leaks field names
Every endpoint using `data["field"]` returns 500 with a message naming the missing field on bad input. Use Pydantic models → uniform 422. (Cross-listed with C19.) ~1 h across endpoints.

### S15 — `get_qr`/`connect`/`disconnect` are stubs returning internal hostnames
`api/main.py:95-107`: `get_qr` returns `https://evolution:8080/instance/qr/{num_id}` (non-functional, leaks internal hostname); connect/disconnect return hardcoded `{"status": "qr_pending"}`/`"disconnected"`. An operator may believe a number is connected when it isn't — a safety gap for a WhatsApp sender. Implement against `EvolutionAdapter.connect`/`status`, or mark the endpoints clearly as stubs. ~1 h.

### S16 — Scheduler leader lock can be stolen
`services/scheduler/main.py:19,29`: leader renews with `nx=False` (unconditional `SET`). If the leader stalls > 120 s, the lock expires, a follower takes over, then the stalled original wakes and overwrites the lock → two schedulers briefly active (double daily-learning runs). Use a fenced token (set a unique value, renew only with `SET … NX` compare-and-set on that value) and fence jobs with the token. ~30 min.

### S17 — DB/Redis ports published; Redis has no auth
`docker-compose.yml:12-13,26-27,46-47,64-65,119-120` publishes Postgres 5432, Redis 6379, ingress 8001, api 8000, evolution 8088 to the host; `deploy/helm/values.yaml` sets `redis.auth.enabled: false`. In any shared/host deploy this is an open Redis (no auth) and Postgres (`cante:cante`). Bind to `127.0.0.1` in compose, enable Redis ACL, and in Helm enable auth + don't expose service ports. ~30 min.

### S18 — `docs/reports/` is gitignored despite README claiming reports are committed
`.gitignore` ignores `docs/reports/`; `docs/reports/README.md` says reports are "committed directly in the repository." New reports won't be tracked, so security findings silently don't reach consumers of the repo. Remove the ignore line (or scope it to a draft subfolder). ~5 min.

---

## Attack chains (why the severities compound)

- **Default-deploy takeover (S4 → S1 → S5):** Attacker knows `jwt_secret` is `change-me-in-production` → forges an admin JWT → S1 gives every tenant's data; no startup guard stops it.
- **Skill-author SSRF pivot (S5 → S3):** Any operator creates a Skill with a declared tool `http://169.254.169.254/…` → worker fetches cloud metadata → cloud-account takeover. Or `http://evolution:8080/…` → drive the victim's WhatsApp.
- **Unauthenticated message injection (S2):** Forged webhook → unlimited LLM cost + impersonation; with C2 not yet wired it's "only" pollution, but the moment the LLM is connected (C2) it becomes cost abuse and tool-driven phishing.
- **Silent insider abuse (S5 + S6 + S11 + S12):** An operator sends arbitrary messages as the bot to any contact, across tenants, with no audit row. Detectable only after the fact, and only if someone adds logging.

---

## Remediation checklist (ordered)

**Before any public launch (must):**
1. **S1** — `tenant_id` in JWT + data-layer scoping + write-side setting + test matrix.
2. **S4** — startup guard refusing defaults; remove hardcoded secrets from compose; Helm `required`.
3. **S2** — webhook signature/per-channel secret; dedicated `TRIGGER_API_KEY` + `compare_digest`.
4. **S3** — enforce `allowed_hosts` **with DNS/IP egress filtering** (block link-local/metadata); pin scheme; admin-only skill creation.
5. **S5 + S6** — admin-only for skill/provider/route/number; ownership/tenant checks on every `{id}` endpoint.
6. **S9** — strong KDF + salt for secrets at rest.
7. **S12** — write `AuditLog` on every mutating endpoint.
8. **S10** — refresh rotation + revocation; centralize token-type enforcement.

**Hardening pass (should):** S7 (login throttle + 422), S8 (CORS allowlist), S11 (audit + tenant-guard human-send), S13–S17.

**Hygiene:** S18 (un-ignore `docs/reports/`).

**Verification after fixes:** re-run this audit; specifically (a) two-tenant isolation test, (b) SSRF test with metadata + internal hosts, (c) default-secret startup-refusal test, (d) forged-webhook 401 test, (e) audit-log-present-on-write test.

---

**Overall assessment:** Not production-ready. The architecture is internally clean, but the trust boundaries are not implemented: multi-tenancy is decorative, the inbound surface is open, skill tools are an SSRF, and default secrets are unguarded. The initial report's "0 criticals / production-ready behind a reverse proxy" conclusion does not hold — a reverse proxy does not fix S1, S3, or S4. Close S1–S4 (and ideally S5/S9/S12) before exposing the system to any untrusted traffic, then re-audit.
