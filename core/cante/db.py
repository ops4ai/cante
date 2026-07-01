"""Database engine and session factory."""

import asyncio
import os
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from cante.settings import settings

engine = create_async_engine(settings.database_url, echo=settings.debug, pool_size=20)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


# ── Schema management ────────────────────────────────────────────────────────


def _alembic_cfg():
    """Build an Alembic Config pointing at this repo's migrations."""
    from alembic.config import Config

    ini = os.environ.get("CANTE_ALEMBIC_INI")
    if not ini:
        # core/cante/db.py → parents[2] is the repo root (alembic.ini lives there).
        ini = str(Path(__file__).resolve().parents[2] / "alembic.ini")
    cfg = Config(ini)
    cfg.set_main_option("script_location", str(Path(ini).resolve().parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def _run_migrations_sync() -> None:
    """Run ``command.upgrade`` — always safe regardless of the calling thread."""
    from alembic import command
    command.upgrade(_alembic_cfg(), "head")


def run_migrations() -> None:
    """Apply all Alembic migrations up to head.

    When called from within a running event loop, ``command.upgrade`` is
    dispatched to a fresh thread so the ``asyncio.run`` inside
    ``migrations/env.py`` gets its own loop.
    """
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _run_migrations_sync()
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(_run_migrations_sync).result()


async def run_migrations_async() -> None:
    """Apply migrations from within a running event loop (e.g. API startup).

    ``command.upgrade`` drives migrations/env.py which calls ``asyncio.run``;
    running it in a worker thread avoids clashing with the caller's loop.
    """
    await asyncio.to_thread(run_migrations)


async def init_db() -> None:
    """Dev helper: create all tables from the ORM metadata (no Alembic needed)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ── Provider helpers (shared by worker + API) ─────────────────────────────────


async def resolve_provider_api_key(provider, session) -> str:
    """Resolve a provider's API key: env var named by *api_key_ref*, else a
    Secret row (decrypted at rest), else the Secret's *env_ref* indirection.

    Returns ``""`` when nothing is configured — callers should surface a clear
    error rather than making an API call with an empty key.
    """
    import os

    env_val = os.environ.get(provider.api_key_ref, "")
    if env_val:
        return env_val
    # Look up a Secret row by name and decrypt at rest.
    from cante.models import Secret
    from cante.secrets import decrypt
    from sqlalchemy import select

    secret = (
        await session.execute(select(Secret).where(Secret.name == provider.api_key_ref))
    ).scalar_one_or_none()
    if secret and secret.value_encrypted:
        return decrypt(secret.value_encrypted)
    if secret and secret.env_ref:
        return os.environ.get(secret.env_ref, "")
    return ""


def build_provider_adapter(provider, api_key: str):
    """Instantiate the right LLM adapter for *provider*.

    Returns an ``AnthropicAdapter`` when *provider.type* is ``"anthropic"``
    (or the model / base_url otherwise matches Anthropic patterns), otherwise
    an ``OpenAICompatibleAdapter`` (covers OpenAI, DeepSeek, OpenRouter, Groq,
    LiteLLM, and any other OpenAI-compatible API).
    """
    from cante.adapters import AnthropicAdapter, OpenAICompatibleAdapter

    if provider.type == "anthropic" or AnthropicAdapter.supports(provider.model, provider.base_url):
        return AnthropicAdapter(api_key=api_key, base_url=provider.base_url or None)
    return OpenAICompatibleAdapter(api_key=api_key, base_url=provider.base_url)
