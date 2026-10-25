"""Pro-only features on hosted (D-19, #246)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import get_settings
from app.models.api_token import ApiToken, new_api_token
from app.models.feed import Feed
from app.models.source import Source
from app.models.user import User
from app.services import entitlements as ent

NOW = datetime.now(UTC)


@pytest.fixture
def hosted(monkeypatch):
    values = {
        "deployment_mode": "hosted",
        "registration_mode": "open",
        "email_verification": "off",
        "billing_trial_days": 30,
        "hosted_pro_max_poll_minutes": 60,
        # Trials only end, and Pro only differs from the trial, with billing on.
        "billing_provider": "lemonsqueezy",
    }
    # entitlements keeps the settings object it imported; patch both if they differ.
    s = get_settings()
    for target in {id(s): s, id(ent.settings): ent.settings}.values():
        for key, value in values.items():
            monkeypatch.setattr(target, key, value)
    return s


def _user(tier="free", *, trial_days=None, **kw) -> User:
    user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", hashed_password="x", **kw)
    user.tier = tier
    user.created_at = NOW
    if tier in ("reader", "pro", "team"):
        user.plan_expires_at = NOW + timedelta(days=30)
    if trial_days is not None:
        user.trial_ends_at = NOW + timedelta(days=trial_days)
    return user


# ------------------------------------------------------------- who has Pro


def test_pro_features_by_plan(hosted, monkeypatch):
    assert ent.pro_features(_user("pro"))
    assert ent.pro_features(_user("team"))
    assert ent.pro_features(_user(trial_days=5))  # the trial gets Pro (D-18)
    assert not ent.pro_features(_user("reader"))
    assert not ent.pro_features(_user(trial_days=-1))  # paused
    monkeypatch.setattr(ent.settings, "deployment_mode", "selfhost")
    assert ent.pro_features(_user("reader"))  # self-host has everything


def test_twice_daily_runs_as_daily_without_pro(hosted):
    from app.workers.tasks.build_digest import _preferred_slots

    def slots(tier, **kw):
        user = _user(tier, **kw)
        user.digest_frequency, user.digest_time_morning = "twice_daily", time(7, 0)
        return _preferred_slots(user)

    assert slots("pro") == [time(7, 0), time(19, 0)]
    assert slots("reader") == [time(7, 0)]  # e.g. after a downgrade


def test_plans_on_sale_list_pro_features(hosted, monkeypatch):
    from app.services import billing

    monkeypatch.setattr(get_settings(), "billing_provider", "lemonsqueezy")
    monkeypatch.setattr(get_settings(), "billing_checkout_reader_monthly", "https://x/r")
    monkeypatch.setattr(get_settings(), "billing_checkout_pro_monthly", "https://x/p")
    by = {p["plan"]: p for p in billing.plans_on_sale()}
    assert by["reader"]["features"] == []
    assert "API and MCP access" in by["pro"]["features"]


# --------------------------------------------------------------- endpoints


async def _account(client, db_session, tier):
    email = f"pf{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "TestPass123!", "accept_terms": True},
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    if tier != "trial":
        user.trial_ends_at = NOW - timedelta(days=1)
        user.tier, user.plan_expires_at = tier, NOW + timedelta(days=30)
    await db_session.commit()
    return user, headers


@pytest.mark.asyncio
async def test_twice_daily_needs_pro(client, db_session, hosted):
    _, reader = await _account(client, db_session, "reader")
    _, trial = await _account(client, db_session, "trial")
    body = {"digest_frequency": "twice_daily"}
    denied = await client.put("/api/v1/preferences", json=body, headers=reader)
    assert denied.status_code == 402 and "Pro" in denied.json()["detail"]
    assert (await client.put("/api/v1/preferences", json=body, headers=trial)).status_code == 200


@pytest.mark.asyncio
async def test_downgraded_reader_can_still_save_with_twice_daily_stored(client, db_session, hosted):
    """CodeRabbit on #347: the page resends digest_frequency on every save."""
    user, headers = await _account(client, db_session, "reader")
    user.digest_frequency = "twice_daily"
    await db_session.commit()
    resp = await client.put(
        "/api/v1/preferences",
        json={"digest_frequency": "twice_daily", "serendipity_percentage": 20},
        headers=headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_tokens_are_pro_except_for_the_extension(client, db_session, hosted):
    async def token_for(tier):
        user, _ = await _account(client, db_session, tier)
        plaintext, token_hash, prefix = new_api_token()
        db_session.add(
            ApiToken(
                user_id=user.id,
                name="t",
                token_hash=token_hash,
                prefix=prefix,
                scopes=["read", "write"],
            )
        )
        await db_session.commit()
        return {"Authorization": f"Bearer {plaintext}"}

    reader, pro = await token_for("reader"), await token_for("pro")
    denied = await client.get("/api/v1/sources", headers=reader)
    assert denied.status_code == 402 and "API and MCP access" in denied.json()["detail"]
    assert (await client.get("/api/v1/sources", headers=pro)).status_code == 200

    # The extension's own calls work on every plan.
    with (
        patch("app.api.sources._autodiscover_feed", return_value=None),
        patch("app.workers.tasks.ingest_feeds.ingest_user_sources.delay"),
    ):
        added = await client.post(
            "/api/v1/sources", json={"url": "https://ext.example.com/feed"}, headers=reader
        )
    assert added.status_code == 201


@pytest.mark.asyncio
async def test_creator_story_synthesis_needs_pro(client, db_session, hosted):
    _, reader = await _account(client, db_session, "reader")
    resp = await client.get(f"/api/v1/creators/{uuid.uuid4()}/summary", headers=reader)
    assert resp.status_code == 402 and "Story synthesis" in resp.json()["detail"]


# --------------------------------------------------------- priority fetching


@pytest.mark.asyncio
async def test_pro_feeds_go_first_and_are_checked_at_least_hourly(db_session, hosted):
    from sqlalchemy import select

    from app.workers.tasks.ingest_feeds import _apply_pro_priority, _pro_followed

    reader, pro = _user("reader"), _user("pro")
    # A Reader whose trial date hasn't passed is still Reader, not Pro.
    reader.trial_ends_at = NOW + timedelta(days=10)
    db_session.add_all([reader, pro])
    await db_session.flush()
    plain = Feed(key=f"https://a{uuid.uuid4().hex[:6]}.example/feed", url="https://a.example")
    starred = Feed(key=f"https://b{uuid.uuid4().hex[:6]}.example/feed", url="https://b.example")
    db_session.add_all([plain, starred])
    await db_session.flush()
    db_session.add_all(
        [
            Source(user_id=reader.id, url=plain.url, feed_id=plain.id),
            Source(user_id=reader.id, url=starred.url, feed_id=starred.id),
            Source(user_id=pro.id, url=starred.url, feed_id=starred.id),
        ]
    )
    await db_session.commit()

    ordered = (
        (
            await db_session.execute(
                select(Feed.id)
                .where(Feed.id.in_([plain.id, starred.id]))
                .order_by(_pro_followed(NOW).desc())
            )
        )
        .scalars()
        .all()
    )
    assert ordered == [starred.id, plain.id]

    later = NOW + timedelta(hours=20)
    subs = [SimpleNamespace(user_id=reader.id), SimpleNamespace(user_id=pro.id)]
    starred.next_poll_at, starred.fetch_error_count = later, 0
    await _apply_pro_priority(starred, subs, db_session)
    assert starred.next_poll_at <= datetime.now(UTC) + timedelta(minutes=61)

    plain.next_poll_at = later
    await _apply_pro_priority(plain, [SimpleNamespace(user_id=reader.id)], db_session)
    assert plain.next_poll_at == later  # no Pro reader: adaptive schedule stands

    starred.next_poll_at, starred.fetch_error_count = later, 3
    await _apply_pro_priority(starred, subs, db_session)
    assert starred.next_poll_at == later  # error backoff is never shortened
