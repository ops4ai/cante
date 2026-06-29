"""Database engine and session factory."""

import asyncio
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from cante.settings import settings

engine = create_async_engine(settings.database_url, echo=settings.debug, pool_size=20)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
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


def run_migrations() -> None:
    """Apply all Alembic migrations up to head. Sync — safe to call from CLI/scripts."""
    from alembic import command

    command.upgrade(_alembic_cfg(), "head")


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
