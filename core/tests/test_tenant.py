"""Multi-tenant data-layer enforcement (S1) — fail-closed reads/writes.

Requires a reachable Postgres at DATABASE_URL (the models use PG-only types).
Skipped automatically when no DB is available.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

import cante.db as cante_db
from cante.db import Base
from cante.models import Number, SkillVersion
from cante.tenant import MissingTenantContextError, TenantMismatchError, with_bypass, with_tenant

TENANT_A = "00000000-0000-0000-0000-000000000001"
TENANT_B = "00000000-0000-0000-0000-000000000002"


def _db_available() -> bool:
    try:
        import os

        url = os.environ.get("DATABASE_URL")
        if not url:
            return False
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(not _db_available(), reason="DATABASE_URL not set")


async def _setup():
    async with cante_db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
async def _clean_db():
    await _setup()
    # Seed one Number per tenant via bypass (system bootstrap).
    async with cante_db.async_session_factory() as session:
        with with_bypass():
            session.add(Number(phone="t0", tenant_id=TENANT_A))
            session.add(Number(phone="t1", tenant_id=TENANT_B))
            await session.commit()
    yield
    async with cante_db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def test_fail_closed_without_context():
    async with cante_db.async_session_factory() as session:
        with pytest.raises(MissingTenantContextError):
            await session.execute(select(Number))


async def test_tenant_a_sees_only_own_rows():
    async with cante_db.async_session_factory() as session:
        with with_tenant(TENANT_A):
            rows = [n.phone for n in (await session.execute(select(Number))).scalars().all()]
            count = (await session.execute(select(func.count(Number.id)))).scalar()
    assert rows == ["t0"]
    assert count == 1


async def test_tenant_b_sees_only_own_rows():
    async with cante_db.async_session_factory() as session:
        with with_tenant(TENANT_B):
            rows = [n.phone for n in (await session.execute(select(Number))).scalars().all()]
    assert rows == ["t1"]


async def test_write_stamps_tenant_server_side():
    async with cante_db.async_session_factory() as session:
        with with_tenant(TENANT_A):
            n = Number(phone="newA")
            session.add(n)
            await session.commit()
            assert n.tenant_id == TENANT_A


async def test_write_overrides_client_tenant():
    """A client-supplied tenant_id on a new row is overridden by the context."""
    async with cante_db.async_session_factory() as session:
        with with_tenant(TENANT_A):
            n = Number(phone="evil", tenant_id=TENANT_B)
            session.add(n)
            await session.commit()
            assert n.tenant_id == TENANT_A  # server-side stamp wins


async def test_dirty_tenant_tamper_rejected():
    """Mutating an existing row's tenant_id to another tenant is refused."""
    async with cante_db.async_session_factory() as session:
        with with_tenant(TENANT_A):
            n = (await session.execute(select(Number))).scalars().first()
            assert n is not None
            n.tenant_id = TENANT_B
            with pytest.raises(TenantMismatchError):
                await session.commit()


async def test_non_tenant_scoped_entity_unaffected_without_context():
    """SkillVersion is not TenantScoped — querying it needs no tenant context."""
    async with cante_db.async_session_factory() as session:
        # Must not raise MissingTenantContextError.
        await session.execute(select(SkillVersion))


async def test_bypass_sees_all():
    async with cante_db.async_session_factory() as session:
        with with_bypass():
            rows = [n.phone for n in (await session.execute(select(Number))).scalars().all()]
    assert sorted(rows) == ["t0", "t1"]
