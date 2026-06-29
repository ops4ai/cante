"""C6 — list_conversations honours its number_id filter."""

import pytest


async def _seed_two_conversations_on_different_numbers():
    """Create two conversations, each on its own number, under the seeded tenant."""
    from cante.db import async_session_factory
    from cante.models import Bot, Contact, Conversation, Number, Provider, Skill
    from cante.tenant import with_tenant

    async with async_session_factory() as session:
        with with_tenant("00000000-0000-0000-0000-000000000001"):
            provider = Provider(
                name="P", type="openai_compatible", base_url="http://x", model="m", api_key_ref="K"
            )
            skill = Skill(name="S", preset="custom")
            session.add_all([provider, skill])
            await session.flush()
            bot = Bot(name="B", skill_id=skill.id, provider_id=provider.id)
            session.add(bot)
            await session.flush()
            n1 = Number(phone="111", display_name="one")
            n2 = Number(phone="222", display_name="two")
            session.add_all([n1, n2])
            await session.flush()
            c = Contact(phone="333", name="c")
            session.add(c)
            await session.flush()
            session.add_all([
                Conversation(number_id=n1.id, bot_id=bot.id, contact_id=c.id),
                Conversation(number_id=n2.id, bot_id=bot.id, contact_id=c.id),
            ])
            await session.commit()  # commit inside the tenant context (enforcement is active)
        return n1.id, n2.id


@pytest.mark.asyncio
async def test_number_id_filter_narrows_results(app, admin_token):
    import httpx

    n1_id, _n2_id = await _seed_two_conversations_on_different_numbers()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # No filter → both conversations.
        r_all = await client.get("/v1/conversations", headers={"Authorization": f"Bearer {admin_token}"})
        assert r_all.status_code == 200
        assert len(r_all.json()) == 2

        # Filter by n1 → only the conversation on n1.
        r_filtered = await client.get(
            "/v1/conversations",
            params={"number_id": n1_id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r_filtered.status_code == 200
        rows = r_filtered.json()
        assert len(rows) == 1
        assert rows[0]["number_id"] == n1_id
