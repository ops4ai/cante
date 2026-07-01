"""Shared test fixtures (C20).

- Strong, non-default secrets are set before `cante` is imported so the S4
  startup guard and JWT/Fernet paths work.
- `pg` runs Alembic migrations once against the test Postgres (skipped if down).
- `redis_client` is a fakeredis instance injected as the app's redis singleton.
- `app` is the FastAPI app wired for in-process testing via httpx.ASGITransport.
- `admin_token` creates an admin user (seeded tenant) and returns a valid JWT.
"""

import asyncio
import os

# ── Env setup (must run before any `from cante...` import) ───────────────────
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-0123456789-abcdef")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password-12345")
os.environ.setdefault("TRIGGER_API_KEY", "test-trigger-key-12345")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://cante:cante@localhost:55432/cante_test")
os.environ.setdefault("WORKER_LLM_ENABLED", "false")  # echo mode for worker unit tests
# A valid 44-char urlsafe-b64 Fernet key (generated, not a shipped default).
from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import pytest  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

_SEEDED_TENANT = "00000000-0000-0000-0000-000000000001"


def _run_async(coro):
    """Run *coro* to completion in a fresh event loop on a worker thread.

    Always uses a thread so that nested ``asyncio.run`` calls (e.g. from
    ``run_migrations`` → ``migrations/env.py``) don't hit "cannot be called
    from a running event loop" when this is invoked inside the auto-recovery
    path of ``_isolate_db``.
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


@pytest.fixture(scope="session", autouse=True)
def _nullpool_engine():
    """Swap the async engine for a NullPool one for the whole test session.

    pytest-asyncio gives each test its own event loop; asyncpg connections are
    bound to the loop they were created in and cache prepared statements, so a
    pooled connection reused across loops (or across DDL) raises
    "another operation in progress" / stale pg_type duplicates. NullPool checks
    out a fresh connection per use. The swapped factory is also rebound into the
    API and ingress apps, whose module-level `async_session_factory` was bound
    to the original pool at import time.
    """
    try:
        import cante.db as _db
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        eng = create_async_engine(_db.settings.database_url, poolclass=NullPool)
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        _db.engine = eng
        _db.async_session_factory = factory
        import services.api.main as _api
        import services.ingress.main as _ing

        _api.async_session_factory = factory
        _ing.async_session_factory = factory
    except Exception:
        pass
    yield


def _pg_reachable() -> bool:
    try:
        import asyncpg

        from cante.settings import settings

        async def _ping() -> bool:
            conn = await asyncpg.connect(settings.database_url.replace("+asyncpg", ""))
            await conn.close()
            return True

        return _run_async(_ping())
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg():
    """Ensure migrations are applied once against the test DB; yield the url.

    Idempotent — ``alembic upgrade head`` is a no-op when already at head, and
    creates everything on a fresh DB. We deliberately do NOT drop+recreate the
    schema here: doing so in-process collides with asyncpg's prepared-statement
    type cache (duplicate pg_type rows). Per-test data isolation is handled by
    ``_isolate_db`` (truncate).
    """
    from cante.db import run_migrations
    from cante.settings import settings

    if not _pg_reachable():
        pytest.skip("Postgres unavailable")
    run_migrations()
    return settings.database_url


@pytest.fixture(autouse=True)
def _isolate_db(pg):
    """Truncate every data table before each test for DB isolation.

    Never touches ``alembic_version`` — clearing it makes the next migration
    think the DB is unmigrated and re-run CREATE TABLE (DuplicateTableError).

    If a destructive test (e.g. migration tests) dropped the schema, this
    fixture auto-recovers by re-running migrations outside the async loop
    (so the nested ``asyncio.run`` in ``migrations/env.py`` gets a fresh loop)
    and then retries the truncate.
    """
    import asyncpg

    from cante.db import run_migrations
    from cante.settings import settings

    _TRUNCATE_SQL = (
        "TRUNCATE TABLE users, providers, skills, skill_versions, bots, numbers, "
        "routes, contacts, contact_groups, group_memberships, conversations, "
        "messages, learnings, events, audit_logs, secrets RESTART IDENTITY CASCADE"
    )

    async def _truncate(retry: bool = True) -> None:
        conn = await asyncpg.connect(settings.database_url.replace("+asyncpg", ""))
        try:
            await conn.execute(_TRUNCATE_SQL)
        except asyncpg.exceptions.UndefinedTableError:
            await conn.close()
            if retry:
                # Schema was dropped by a destructive test, but alembic_version
                # may still have the head revision (e.g. `pg` fixture re-created
                # just that one table). Nuke it so `run_migrations` applies from
                # scratch.
                conn2 = await asyncpg.connect(settings.database_url.replace("+asyncpg", ""))
                try:
                    await conn2.execute("DELETE FROM alembic_version")
                except Exception:
                    pass  # table might not exist either
                await conn2.close()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    ex.submit(run_migrations).result()
                await _truncate(retry=False)
            else:
                raise
        else:
            await conn.close()

    _run_async(_truncate())


@pytest.fixture
async def redis_client():
    """A fakeredis client, installed as the app's redis singleton."""
    import cante.redis as redis_module

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_module._redis = client
    try:
        yield client
    finally:
        await client.aclose()
        redis_module._redis = None


@pytest.fixture
def app(pg, redis_client):
    """The API FastAPI app with fakeredis + migrated DB wired in."""
    from services.api.main import app as api_app

    return api_app


@pytest.fixture
async def admin_token(pg, redis_client):
    """Create an admin user in the seeded tenant and return (token, principal)."""
    from cante.auth import create_token_pair, hash_password
    from cante.db import async_session_factory
    from cante.models import User
    from cante.tenant import with_tenant
    from sqlalchemy import select

    async with async_session_factory() as session:
        with with_tenant(_SEEDED_TENANT):
            existing = (
                await session.execute(select(User).where(User.email == "admin@test.local"))
            ).scalar_one_or_none()
            if not existing:
                session.add(
                    User(
                        email="admin@test.local",
                        hashed_password=hash_password("test-admin-password-12345"),
                        role="admin",
                    )
                )
                await session.flush()
                existing = (
                    await session.execute(select(User).where(User.email == "admin@test.local"))
                ).scalar_one()
            user = existing
        await session.commit()

    token, _ = create_token_pair(user.id, _SEEDED_TENANT, "admin")
    return token


@pytest.fixture
def text_webhook():
    return json.loads((FIXTURES / "evolution_webhook_text.json").read_text())


@pytest.fixture
def media_webhook():
    return json.loads((FIXTURES / "evolution_webhook_media.json").read_text())
