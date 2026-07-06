"""Centralised settings loaded from environment / .env file."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # ── App ──────────────────────────────────────
    app_name: str = "Cante"
    debug: bool = False
    default_language: str = "en"

    # ── Postgres ─────────────────────────────────
    database_url: str = "postgresql+asyncpg://cante:cante@postgres:5432/cante"

    @property
    def database_url_sync(self) -> str:
        return self.database_url.replace("+asyncpg", "+psycopg2")

    # ── Redis ────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"
    redis_password: str = ""  # when set, must match Redis ACL / --requirepass

    # ── LLM secrets (fallback — prefer providers in DB) ─
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ── Auth ─────────────────────────────────────
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24h
    jwt_refresh_expire_days: int = 7
    # Separate secret for the machine-to-machine /triggers endpoint. MUST be set.
    trigger_api_key: str = ""

    # ── CORS ─────────────────────────────────────
    cors_origins: str = ""  # comma-separated; empty => same-origin only

    # ── Ingestion ────────────────────────────────
    wa_dedup_ttl_seconds: int = 86400  # 24h
    debounce_ms_default: int = 3000

    # ── Worker ───────────────────────────────────
    max_tool_iterations: int = 5
    circuit_breaker_failures: int = 3
    rate_limit_per_minute: int = 10
    rate_limit_per_hour: int = 60
    # When False the worker short-circuits to an echo reply (no LLM, no DB) —
    # useful for smoke tests / dev without a configured provider.
    worker_llm_enabled: bool = True
    # Per-entry redelivery: claim pending stream entries older than this (seconds)
    # and move them to stream:dead after this many failures.
    worker_claim_min_idle_ms: int = 60_000
    worker_max_retries: int = 5
    # Per-conversation debounce/claim lock TTL. Must exceed worst-case LLM latency
    # (C14); a heartbeat renews it during the call so a slow LLM doesn't drop it.
    worker_lock_ttl: int = 120

    # ── Sender ───────────────────────────────────
    send_delay_min_s: float = 3.0
    send_delay_max_s: float = 15.0

    # ── Secrets encryption ───────────────────────
    secret_encryption_key: str = "change-me-change-me-change-me!"

    # ── Observability ────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # ── Evolution API (channel) ──────────────────
    evolution_base_url: str = "http://evolution:8080"
    evolution_api_key: str = ""

    # Base URL the Evolution API uses to call the cante ingress for incoming
    # messages (set per instance in .env, e.g. http://cante-cds-ingress:8001).
    ingress_base_url: str = "http://cante-ingress:8001"

    # ── Frontend / domain ────────────────────────
    public_domain: str = "localhost"
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me"


settings = Settings()
