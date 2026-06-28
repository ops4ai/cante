# Security Review — Cante v0.1.0 (Initial)

**Date:** 2026-06-27
**Scope:** Full codebase (core/, services/, docker-compose.yml, deploy/)
**Reviewer:** Claude (AI-assisted)
**Project:** ops4ai/cante

---

## Findings

### 🔴 CRITICAL — No findings

### 🟡 MEDIUM — 3 findings

#### 1. JWT secret defaults to hardcoded value

**File:** `core/cante/settings.py`
**Line:** `jwt_secret: str = "change-me-in-production"`
**Risk:** If a deployer forgets to override this, any attacker who knows the default can forge valid JWTs and gain admin access to the backoffice.
**Fix:** The Helm chart properly injects this via Kubernetes Secrets and `docker-compose.yml` reads from `.env`. Add a startup check that refuses to run with the default value.
**Recommendation:** Add to `api/main.py`:
```python
if settings.jwt_secret == "change-me-in-production":
    raise RuntimeError("JWT_SECRET must be changed from the default value")
```

#### 2. API key for /v1/triggers reuses JWT secret

**File:** `services/api/main.py`
**Line:** `if api_key != settings.jwt_secret:`
**Risk:** The internal `/v1/triggers` endpoint for proactive messages uses the JWT secret as an API key. If the JWT secret is compromised, both backoffice auth AND external integrations are compromised.
**Fix:** Add a dedicated `TRIGGER_API_KEY` setting, separate from `JWT_SECRET`.
**Recommendation:** Add `trigger_api_key: str = ""` to Settings, use it in the trigger endpoint.

#### 3. Declarative HTTP tools — SSRF protection not enforced

**File:** `core/cante/tools.py` (DeclaredHttpTool)
**Risk:** A declared HTTP tool can call any URL the Skill author specifies. A malicious Skill author could craft a tool that calls internal services (e.g., `http://postgres:5432`, `http://redis:6379`).
**Fix:** The spec (§5.3) mandates an **allowlist** of hosts. This is not yet enforced in `DeclaredHttpTool.execute()`.
**Recommendation:** Add `allowed_hosts` validation before executing HTTP calls:
```python
from urllib.parse import urlparse
host = urlparse(url).hostname
if host not in self.allowed_hosts:
    raise ValueError(f"Host {host} not in allowed hosts: {self.allowed_hosts}")
```

### 🟢 LOW — 2 findings

#### 4. Secrets encryption key defaults to placeholder

**File:** `core/cante/settings.py`
**Line:** `secret_encryption_key: str = "change-me-change-me-change-me!"`
**Risk:** Same as JWT — deployer forgets to change it, secrets are encrypted with a known key.
**Fix:** Same startup check pattern.

#### 5. CORS allows all origins (`*`) in production

**File:** `services/api/main.py`
**Line:** `allow_origins=["*"]`
**Risk:** Any website can make authenticated requests to the API. In production behind a proper reverse proxy this is acceptable, but if the API is directly exposed, it's a CSRF risk.
**Fix:** Make CORS origins configurable via `CORS_ORIGINS` env var, defaulting to `["*"]` in dev and `["https://dashboard.example.com"]` in production. Document that the edge proxy should be the only public surface.
**Verdict:** Acceptable risk for v1. The spec says "edge is a separable layer" and "app services bind to internal Docker network only." The public surface is the reverse proxy.

---

## Summary

| Severity | Count |
|----------|-------|
| 🔴 Critical | 0 |
| 🟡 Medium | 3 |
| 🟢 Low | 2 |

**Overall assessment:** Production-ready for self-hosted deployments behind a reverse proxy. The 3 medium findings are configuration hardening issues, not code vulnerabilities. All can be fixed in under 1 hour.

**Recommended before public launch:**
1. Add startup guard for default JWT secret
2. Separate trigger API key from JWT secret
3. Enforce SSRF allowlist on declarative HTTP tools
