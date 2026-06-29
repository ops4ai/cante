"""Security-focused API integration tests (S2/S5/S6/S7/S10/S11/S12).

Reuses the engineer's `app`/`admin_token`/`pg`/`redis_client` fixtures and the
httpx.ASGITransport in-process pattern from tests/test_api_conversations.py.
"""
import pytest

TENANT_A = "00000000-0000-0000-0000-000000000001"  # seeded tenant
TENANT_B = "00000000-0000-0000-0000-000000000002"


# ── helpers ──────────────────────────────────────────────────────────────────


async def _operator_token(email: str = "op@test.local", tenant: str = TENANT_A) -> str:
    from cante.auth import create_token_pair, hash_password
    from cante.db import async_session_factory
    from cante.models import User
    from cante.tenant import with_tenant

    async with async_session_factory() as session:
        with with_tenant(tenant):
            session.add(User(email=email, hashed_password=hash_password("pw"), role="operator"))
            await session.flush()
            uid = (await session.execute(__import__("sqlalchemy").select(User).where(User.email == email))).scalar_one().id
        await session.commit()
    token, _ = create_token_pair(uid, tenant, "operator")
    return token


async def _seed_tenant_b_conversation() -> str:
    """Create a full conversation graph in tenant B; return its id."""
    from cante.db import async_session_factory
    from cante.models import Bot, Contact, Conversation, Number, Provider, Skill
    from cante.tenant import with_tenant

    async with async_session_factory() as session:
        with with_tenant(TENANT_B):
            provider = Provider(name="PB", type="openai_compatible", base_url="http://x", model="m", api_key_ref="K")
            skill = Skill(name="SB", preset="custom")
            session.add_all([provider, skill])
            await session.flush()
            bot = Bot(name="BB", skill_id=skill.id, provider_id=provider.id)
            number = Number(phone="+B", connection_config={"webhook_secret": "secB"})
            contact = Contact(phone="+Bcontact", name="cb")
            session.add_all([bot, number, contact])
            await session.flush()
            conv = Conversation(number_id=number.id, bot_id=bot.id, contact_id=contact.id)
            session.add(conv)
            await session.flush()
            cid = conv.id
        await session.commit()
    return cid


async def _seed_number_with_webhook(tenant: str = TENANT_A, secret: str = "secA") -> str:
    from cante.db import async_session_factory
    from cante.models import Number
    from cante.tenant import with_tenant

    async with async_session_factory() as session:
        with with_tenant(tenant):
            n = Number(phone="+A", connection_config={"webhook_secret": secret})
            session.add(n)
            await session.flush()
            nid = n.id
        await session.commit()
    return nid


# ── S7: login ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_missing_field_returns_422(app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/auth/login", json={})
    assert r.status_code == 422  # Pydantic validation, not a 500


@pytest.mark.asyncio
async def test_login_wrong_credentials_generic_401(app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/auth/login", json={"email": "nobody@test.local", "password": "x"})
    assert r.status_code == 401
    assert "Invalid credentials" in r.json()["detail"]
    # No user enumeration.
    assert "nobody" not in r.text


@pytest.mark.asyncio
async def test_login_throttles_after_five(app, admin_token):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        codes = []
        for _ in range(6):
            r = await client.post(
                "/v1/auth/login", json={"email": "admin@test.local", "password": "wrong"}
            )
            codes.append(r.status_code)
    # First five are 401, the sixth is throttled (429).
    assert codes[:5] == [401] * 5
    assert 429 in codes[5:]


# ── S5: role enforcement ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operator_cannot_create_skill(app):
    import httpx

    token = await _operator_token()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/skills", json={"name": "X"}, headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_create_skill(app, admin_token):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/skills", json={"name": "AdminSkill"}, headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert r.status_code == 200


# ── S6: IDOR / cross-tenant ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_tenant_conversation_404(app, admin_token):
    import httpx

    conv_b = await _seed_tenant_b_conversation()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # admin_token is tenant A; tenant B's conversation must be invisible.
        r = await client.get(
            f"/v1/conversations/{conv_b}", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert r.status_code == 404


# ── S12: audit on write ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_logged_on_number_create(app, admin_token):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/numbers",
            json={"phone": "+audit"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert r.status_code == 200
        audit = await client.get(
            "/v1/audit", headers={"Authorization": f"Bearer {admin_token}"}
        )
    actions = {row["action"] for row in audit.json()}
    assert "number.create" in actions


# ── S2: trigger auth ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_missing_key_401(app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/v1/triggers", json={"body": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_trigger_wrong_key_401(app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/triggers", json={"body": "hi"}, headers={"X-API-Key": "wrong"}
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_trigger_correct_key_200(app):
    import httpx

    from cante.settings import settings

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/v1/triggers", json={"body": "hi"}, headers={"X-API-Key": settings.trigger_api_key}
        )
    assert r.status_code == 200


# ── S2: webhook auth ─────────────────────────────────────────────────────────


@pytest.fixture
def ingress_app(pg, redis_client):
    """The ingress FastAPI app (webhook endpoint) with fakeredis + migrated DB."""
    from services.ingress.main import app as ing_app

    return ing_app


@pytest.mark.asyncio
async def test_webhook_forged_401(ingress_app):
    import httpx

    num_id = await _seed_number_with_webhook()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ingress_app), base_url="http://test") as client:
        r = await client.post(f"/channels/{num_id}/webhook", json={})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_wrong_token_401(ingress_app):
    import httpx

    num_id = await _seed_number_with_webhook(secret="secA")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ingress_app), base_url="http://test") as client:
        r = await client.post(
            f"/channels/{num_id}/webhook", json={}, headers={"X-Webhook-Token": "wrong"}
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_unknown_channel_401(ingress_app):
    import httpx

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ingress_app), base_url="http://test") as client:
        r = await client.post("/channels/not-a-uuid/webhook", json={})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_valid_token_200(ingress_app):
    import httpx

    num_id = await _seed_number_with_webhook(secret="secA")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ingress_app), base_url="http://test") as client:
        r = await client.post(
            f"/channels/{num_id}/webhook",
            json={},
            headers={"X-Webhook-Token": "secA"},
        )
    assert r.status_code == 200


# ── S10: refresh rotation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_rotation_revokes_old(app, admin_token):
    """The presented refresh is single-use: reusing it after rotation fails."""
    import httpx

    # admin_token is an access token; get a refresh by logging in.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/v1/auth/login", json={"email": "admin@test.local", "password": "test-admin-password-12345"}
        )
        refresh = login.json()["refresh_token"]

        # First refresh → new pair.
        r1 = await client.post("/v1/auth/refresh", json={"refresh_token": refresh})
        assert r1.status_code == 200

        # Reuse the old refresh → revoked → 401.
        r2 = await client.post("/v1/auth/refresh", json={"refresh_token": refresh})
    assert r2.status_code == 401
