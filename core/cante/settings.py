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

    # ── LLM secrets (fallback — prefer providers in DB) ─
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # ── Auth ─────────────────────────────────────
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24h

    # ── Ingestion ────────────────────────────────
    wa_dedup_ttl_seconds: int = 86400  # 24h
    debounce_ms_default: int = 3000

    # ── Worker ───────────────────────────────────
    max_tool_iterations: int = 5
    circuit_breaker_failures: int = 3
    rate_limit_per_minute: int = 10
    rate_limit_per_hour: int = 60

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

    # ── Frontend / domain ────────────────────────
    public_domain: str = "localhost"
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me"


settings = Settings()
