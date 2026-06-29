"""C1 — Alembic migrations create the full schema and cycle cleanly.

Requires a real Postgres (DATABASE_URL). Skipped when the DB is unreachable so
the rest of the suite still runs in environments without one.
"""

import asyncio

import pytest

EXPECTED_TABLES = {
    "users",
    "providers",
    "skills",
    "skill_versions",
    "bots",
    "numbers",
    "routes",
    "contacts",
    "contact_groups",
    "group_memberships",
    "conversations",
    "messages",
    "learnings",
    "events",
    "audit_logs",
    "secrets",
}

EXPECTED_INDEXES = {
    "idx_msg_conv_created",       # C10: messages hot path
    "idx_conv_last_activity",     # C10: conversations ordering
    "idx_contact_last_seen",      # C10: contacts ordering
    "idx_learning_created",       # C10: learnings ordering
    "idx_audit_created",          # C10: audit ordering
    "idx_conversations_tenant",   # tenant_id seam
    "idx_messages_tenant",
}


def _can_connect() -> bool:
    try:
        import asyncpg

        from cante.settings import settings

        async def _ping() -> bool:
            conn = await asyncpg.connect(settings.database_url.replace("+asyncpg", ""))
            await conn.close()
            return True

        return asyncio.run(_ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _can_connect(), reason="Postgres unavailable")


def _reset_schema() -> None:
    import asyncpg

    from cante.settings import settings

    async def _drop() -> None:
        conn = await asyncpg.connect(settings.database_url.replace("+asyncpg", ""))
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        await conn.close()

    asyncio.run(_drop())


def _table_and_index_names() -> tuple[set[str], set[str]]:
    import asyncpg

    from cante.settings import settings

    async def _fetch() -> tuple[set[str], set[str]]:
        conn = await asyncpg.connect(settings.database_url.replace("+asyncpg", ""))
        tables = {
            r[0]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        indexes = {
            r[0]
            for r in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            )
        }
        await conn.close()
        return tables, indexes

    return asyncio.run(_fetch())


def test_upgrade_creates_all_tables_and_indexes():
    from cante.db import run_migrations

    _reset_schema()
    run_migrations()  # upgrade head

    tables, indexes = _table_and_index_names()
    assert EXPECTED_TABLES <= tables
    assert EXPECTED_INDEXES <= indexes


def test_downgrade_then_upgrade_is_clean():
    from alembic import command

    from cante.db import _alembic_cfg

    _reset_schema()
    command.upgrade(_alembic_cfg(), "head")
    command.downgrade(_alembic_cfg(), "base")
    tables, _ = _table_and_index_names()
    # After downgrade to base, no app tables remain (only alembic_version is dropped too).
    assert not (EXPECTED_TABLES & tables)

    # Re-upgrading must succeed from a clean base.
    command.upgrade(_alembic_cfg(), "head")
    tables, indexes = _table_and_index_names()
    assert EXPECTED_TABLES <= tables
    assert EXPECTED_INDEXES <= indexes
