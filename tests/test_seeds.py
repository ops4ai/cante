"""C7 — seeds use settings.ADMIN_EMAIL / ADMIN_PASSWORD, not hardcoded defaults."""

import pytest


@pytest.mark.asyncio
async def test_seed_creates_admin_from_settings(pg):
    from cante.db import async_session_factory
    from cante.models import SEEDED_TENANT, User
    from cante.settings import settings
    from cante.tenant import with_tenant
    from seeds import __main__ as seeds_mod
    from sqlalchemy import select
    from cante.auth import verify_password

    await seeds_mod.seed()

    async with async_session_factory() as session:
        with with_tenant(SEEDED_TENANT):
            user = (
                await session.execute(select(User).where(User.email == settings.admin_email))
            ).scalar_one()

    assert user.email == settings.admin_email
    assert user.role == "admin"
    assert verify_password(settings.admin_password, user.hashed_password)


@pytest.mark.asyncio
async def test_seed_refuses_default_password(pg, monkeypatch):
    from cante.settings import settings
    from seeds import __main__ as seeds_mod

    monkeypatch.setattr(settings, "admin_password", "change-me")
    with pytest.raises(SystemExit, match="ADMIN_PASSWORD"):
        await seeds_mod.seed()
