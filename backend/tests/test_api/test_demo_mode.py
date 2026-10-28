from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.content import ContentItem, UserContentInteraction
from app.models.user import User


@pytest.mark.asyncio
async def test_demo_login_disabled_when_demo_mode_false(client: AsyncClient, monkeypatch):
    import app.api.auth as auth_mod

    monkeypatch.setattr(get_settings(), "demo_mode", False)
    monkeypatch.setattr(auth_mod.settings, "demo_mode", False)

    resp = await client.post("/api/v1/auth/demo")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Demo mode is disabled"

    reg_resp = await client.get("/api/v1/auth/registration")
    assert reg_resp.status_code == 200
    assert reg_resp.json().get("demo_mode", False) is False


@pytest.mark.asyncio
async def test_demo_login_and_write_endpoints_blocked(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    import app.api.auth as auth_mod
    import app.api.feedback as feedback_mod

    settings = get_settings()
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(auth_mod.settings, "demo_mode", True)
    monkeypatch.setattr(feedback_mod._settings, "demo_mode", True)

    # Demo registration/state reflects demo mode
    reg_resp = await client.get("/api/v1/auth/registration")
    assert reg_resp.status_code == 200
    assert reg_resp.json()["demo_mode"] is True
    assert reg_resp.json()["open"] is False

    # Attempting to register in demo mode returns 403 "The demo is read-only"
    reg_attempt = await client.post(
        "/api/v1/auth/register",
        json={"email": "newuser@example.com", "password": "Password123!"},
    )
    assert reg_attempt.status_code == 403
    assert reg_attempt.json()["detail"] == "The demo is read-only"

    # POST /api/v1/auth/demo signs into the demo account
    resp = await client.post("/api/v1/auth/demo")
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Verify user profile via /auth/me
    me_resp = await client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    me = me_resp.json()
    assert me["email"] == settings.demo_user_email
    assert me["is_demo"] is True

    # Mutating write endpoints return 403 "The demo is read-only"
    # 1. Sources POST
    src_resp = await client.post(
        "/api/v1/sources",
        headers=headers,
        json={"url": "https://example.com/feed.xml", "source_type": "rss"},
    )
    assert src_resp.status_code == 403
    assert src_resp.json()["detail"] == "The demo is read-only"

    # 2. Preferences PUT
    pref_resp = await client.put(
        "/api/v1/preferences",
        headers=headers,
        json={"digest_frequency": "weekly"},
    )
    assert pref_resp.status_code == 403
    assert pref_resp.json()["detail"] == "The demo is read-only"

    # 3. Tokens POST
    tok_resp = await client.post(
        "/api/v1/tokens",
        headers=headers,
        json={"name": "test-token", "scopes": ["read"]},
    )
    assert tok_resp.status_code == 403
    assert tok_resp.json()["detail"] == "The demo is read-only"


@pytest.mark.asyncio
async def test_demo_telemetry_and_feedback_sandboxed(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    import app.api.auth as auth_mod
    import app.api.feedback as feedback_mod

    settings = get_settings()
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(auth_mod.settings, "demo_mode", True)
    monkeypatch.setattr(feedback_mod._settings, "demo_mode", True)

    login_resp = await client.post("/api/v1/auth/demo")
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Use a seeded content item to interact with
    content = (await db_session.execute(select(ContentItem).limit(1))).scalar_one()

    # Reading telemetry & feedback is allowed for the demo user
    fb_resp = await client.post(
        "/api/v1/feedback/interaction",
        headers=headers,
        json={
            "content_item_id": str(content.id),
            "read_completion_pct": 0.95,
            "time_on_page_seconds": 120,
            "explicit_rating": 5,
            "explicit_rating_reason": "thorough",
            "saved": True,
        },
    )
    assert fb_resp.status_code == 200
    fb_data = fb_resp.json()
    assert fb_data["read_completion_pct"] == 0.95
    assert fb_data["explicit_rating"] == 5
    assert fb_data["saved"] is True

    # But GET /interaction/{content_id} returns the sandboxed session data
    get_resp = await client.get(
        f"/api/v1/feedback/interaction/{content.id}",
        headers=headers,
    )
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data is not None
    assert get_data["explicit_rating"] == 5
    assert get_data["saved"] is True


@pytest.mark.asyncio
async def test_nightly_reset_demo_task(db_session, monkeypatch):
    import app.workers.tasks.demo as demo_task
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.database.AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(demo_task, "AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(get_settings(), "demo_mode", True)

    summary = await demo_task._reset_demo_account_async()
    assert summary["email"] == "demo@weirling.local"
    assert summary["history"] > 0
    assert summary["fresh"] > 0
    assert summary["digest_items"] > 0


@pytest.mark.asyncio
async def test_read_only_is_only_for_the_demo_account_on_a_demo_instance(
    client: AsyncClient, db_session: AsyncSession, monkeypatch
):
    """#229 review: `make demo` seeds the same persona on normal instances, and
    the operator of a demo instance must keep a writable account."""
    from app.api.auth import _create_access_token

    settings = get_settings()
    demo = User(email=settings.demo_user_email, hashed_password="x", onboarding_complete=True)
    operator = User(email=f"op{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    db_session.add_all([demo, operator])
    await db_session.commit()

    def headers(user):
        return {"Authorization": f"Bearer {_create_access_token(user.id)}"}

    async def write(user):
        return await client.put(
            "/api/v1/preferences", json={"digest_max_items": 9}, headers=headers(user)
        )

    monkeypatch.setattr(settings, "demo_mode", False)
    assert (await write(demo)).status_code == 200  # normal instance: just a seeded account

    monkeypatch.setattr(settings, "demo_mode", True)
    assert (await write(demo)).status_code == 403
    assert (await write(operator)).status_code == 200


@pytest.mark.asyncio
async def test_demo_reset_does_nothing_without_demo_mode(db_session, monkeypatch):
    """The nightly reset created the demo persona on every instance."""
    import app.workers.tasks.demo as demo_task
    from app.models.user import User
    from app.workers.celery_app import celery_app
    from app.workers.schedules import setup_beat_schedule
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(demo_task, "AsyncSessionLocal", TestingSessionLocal)
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    assert (await demo_task._reset_demo_account_async()) == {"skipped": "DEMO_MODE is off"}
    found = (
        await db_session.execute(select(User).where(User.email == "demo@weirling.local"))
    ).scalar_one_or_none()
    assert found is None

    setup_beat_schedule(celery_app)
    assert "reset-demo-account" not in celery_app.conf.beat_schedule
    monkeypatch.setattr(get_settings(), "demo_mode", True)
    setup_beat_schedule(celery_app)
    assert "reset-demo-account" in celery_app.conf.beat_schedule
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    setup_beat_schedule(celery_app)
