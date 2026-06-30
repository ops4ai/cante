"""Shared test fixtures for the Cante core test suite.

Tests that touch the database need a real Postgres (the models use PG-only
``JSONB``/``UUID`` dialects) and the tenant enforcement is wired through
SQLAlchemy ORM events, so an in-memory SQLite DB won't exercise it. The CI
workflow provides ``DATABASE_URL`` and ``REDIS_URL``; locally, point them at
throwaway containers.

Importing modules that call :func:`cante.security.assert_no_default_secrets`
at startup would fail under the shipped default secrets, so we set
``CANTE_SKIP_STARTUP_GUARD=1`` for the whole suite unless a test is expressly
exercising the guard.
"""
from __future__ import annotations

import os

import pytest

# Strong, non-default values so any code that constructs Settings in tests
# passes the guard without the test having to set every var individually.
os.environ.setdefault("CANTE_SKIP_STARTUP_GUARD", "1")
os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-very-long-and-not-a-default-0123456789abcdef"
)
os.environ.setdefault("TRIGGER_API_KEY", "test-trigger-key-not-default")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password-not-default")
os.environ.setdefault(
    "SECRET_ENCRYPTION_KEY", "piaHPpe_QOK_8VyqxXr6XNsM05WVKIHFR0TLeAIDHIA="
)
os.environ.setdefault("EVOLUTION_API_KEY", "test-evolution-not-default")


@pytest.fixture
def fernet_key() -> str:
    """A valid 44-char urlsafe-b64 Fernet key for round-trip tests."""
    return "piaHPpe_QOK_8VyqxXr6XNsM05WVKIHFR0TLeAIDHIA="


@pytest.fixture(scope="session", autouse=True)
def _nullpool_engine():
    """Swap the module engine for a NullPool one in tests.

    pytest-asyncio gives each test its own event loop; asyncpg connections are
    bound to the loop they were created in, so a pooled connection checked out
    in one loop and reused in another raises "another operation is in progress".
    NullPool checks out a fresh connection per use, sidestepping the issue.
    """
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        import cante.db as _db

        eng = create_async_engine(_db.settings.database_url, poolclass=NullPool)
        _db.engine = eng
        _db.async_session_factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    except Exception:
        pass
    yield
