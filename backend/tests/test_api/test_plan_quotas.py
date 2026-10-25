"""Hosted per-plan quotas (#172)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.user import User
from app.services import entitlements as ent


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(ent.settings, "deployment_mode", "hosted")
    monkeypatch.setattr(ent.settings, "billing_trial_days", 0)
    monkeypatch.setattr(ent.settings, "hosted_plan_quotas", {})
    # Accounts are only paused once there is a way to pay (billing on).
    from app.config import get_settings

    for target in {id(ent.settings): ent.settings, id(get_settings()): get_settings()}.values():
        monkeypatch.setattr(target, "billing_provider", "lemonsqueezy")


def _user(tier="reader", **kw) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    user.tier = tier
    if tier in ("reader", "pro", "team"):
        user.plan_expires_at = datetime.now(UTC) + timedelta(days=30)
    for k, v in kw.items():
        setattr(user, k, v)
    return user


def test_selfhost_has_no_quotas(monkeypatch):
    monkeypatch.setattr(ent.settings, "deployment_mode", "selfhost")
    user = _user()
    assert all(ent.limit(user, q) == 0 for q in ent.QUOTAS)
    ent.enforce_source_limit(user, 10_000)


def test_plan_table_and_config_and_admin_override(hosted, monkeypatch):
    assert ent.limit(_user("reader"), ent.SOURCES) == 500
    assert ent.limit(_user("pro"), ent.SOURCES) == 2000
    # D-18: the trial gets Pro; with neither a plan nor a trial the account is paused.
    trial = _user("free", trial_ends_at=datetime.now(UTC) + timedelta(days=3))
    assert ent.limit(trial, ent.SOURCES) == 2000
    assert ent.is_paused(_user("free"))

    monkeypatch.setattr(ent.settings, "hosted_plan_quotas", {"reader": {"sources": 800}})
    pro = _user("reader")
    assert ent.limit(pro, ent.SOURCES) == 800
    assert ent.limit(pro, ent.LLM_SUMMARIES) == 200  # other rows kept

    pro.quota_overrides = {"sources": 5000, "llm_summaries_per_day": 0}
    assert ent.limit(pro, ent.SOURCES) == 5000
    assert ent.limit(pro, ent.LLM_SUMMARIES) == 0  # admin: unlimited


def test_paid_plan_source_cap_names_the_next_plan(hosted):
    with pytest.raises(HTTPException) as e:
        ent.enforce_source_limit(_user("reader"), 500)
    assert e.value.status_code == 402
    assert "500 sources" in e.value.detail and "Pro plan" in e.value.detail


@pytest.mark.asyncio
async def test_daily_allowance_take_and_message(hosted, monkeypatch):
    monkeypatch.setattr(
        ent.settings, "hosted_plan_quotas", {"reader": {"digest_builds_per_day": 2}}
    )
    user = _user()
    assert await ent.take(user, ent.DIGEST_BUILDS)
    assert await ent.take(user, ent.DIGEST_BUILDS)
    assert not await ent.take(user, ent.DIGEST_BUILDS)
    assert await ent.used_today(user.id, ent.DIGEST_BUILDS) == 2  # the refused one isn't counted
    assert await ent.remaining_today(user, ent.DIGEST_BUILDS) == 0
    assert "2 digest builds a day" in ent.daily_quota_message(user, ent.DIGEST_BUILDS)


@pytest.mark.asyncio
async def test_newsletter_quota_counts_and_volume(hosted, monkeypatch):
    monkeypatch.setattr(
        ent.settings,
        "hosted_plan_quotas",
        {"reader": {"newsletters_per_day": 3, "newsletter_mb_per_day": 1}},
    )
    user = _user()
    assert await ent.newsletter_allowed(user, 600_000) == (True, "")
    assert await ent.newsletter_allowed(user, 600_000) == (
        False,
        "plan's daily newsletter volume reached",
    )
    assert await ent.used_today(user.id, ent.NEWSLETTERS) == 1  # the dropped one is given back
    assert (await ent.newsletter_allowed(user, 100_000))[0]
    assert (await ent.newsletter_allowed(user, 100_000))[0]
    assert await ent.newsletter_allowed(user, 10) == (
        False,
        "plan's daily newsletter count reached",
    )


@pytest.mark.asyncio
async def test_digest_llm_summaries_capped_by_what_is_left_today(hosted, monkeypatch):
    from app.services.digest.builder import summarize_placed

    monkeypatch.setattr(
        ent.settings, "hosted_plan_quotas", {"reader": {"llm_summaries_per_day": 5}}
    )
    user = _user()
    await ent.record_use(user, ent.LLM_SUMMARIES, 3)
    seen = {}

    async def fake_ensure(items, *, limit=None):
        seen["limit"] = limit
        return limit

    with patch("app.services.summarization.lazy.ensure_llm_summaries", fake_ensure):
        assert await summarize_placed(user, []) == 2
        assert await summarize_placed(user, []) == 0
    assert seen["limit"] == 0
    assert await ent.remaining_today(user, ent.LLM_SUMMARIES) == 0


@pytest.mark.asyncio
async def test_manual_digest_builds_per_day(
    client, test_user_data, db_session, hosted, monkeypatch
):
    monkeypatch.setattr(ent.settings, "manual_digest_guard_seconds", 0)
    monkeypatch.setattr(ent.settings, "billing_trial_days", 30)  # a trial account, not paused
    monkeypatch.setattr(ent.settings, "hosted_plan_quotas", {"trial": {"digest_builds_per_day": 1}})
    reg = await client.post("/api/v1/auth/register", json={**test_user_data, "accept_terms": True})
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    with patch("app.workers.tasks.build_digest.build_digest_for_user.delay"):
        first = await client.post("/api/v1/digest/generate", headers=headers)
        second = await client.post("/api/v1/digest/generate", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 429
    assert "1 digest builds a day" in second.json()["detail"]


@pytest.mark.asyncio
async def test_billing_state_lists_quotas_on_hosted_only(
    client, test_user_data, db_session, monkeypatch
):
    reg = await client.post("/api/v1/auth/register", json={**test_user_data, "accept_terms": True})
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert (await client.get("/api/v1/billing", headers=headers)).json()["quotas"] == []

    monkeypatch.setattr(ent.settings, "deployment_mode", "hosted")
    user = (
        await db_session.execute(select(User).where(User.email == test_user_data["email"]))
    ).scalar_one()
    user.tier, user.plan_expires_at = "reader", datetime.now(UTC) + timedelta(days=5)
    await db_session.commit()
    with patch("app.services.billing.effective_plan", return_value="reader"):
        rows = (await client.get("/api/v1/billing", headers=headers)).json()["quotas"]
    by = {r["quota"]: r for r in rows}
    assert by["sources"] == {"quota": "sources", "limit": 500, "used": 0, "daily": False}
    assert by["llm_summaries_per_day"]["daily"] is True


@pytest.mark.asyncio
async def test_opening_an_item_respects_llm_quota(hosted, monkeypatch, db_session):
    from app.api import content

    monkeypatch.setattr(
        ent.settings, "hosted_plan_quotas", {"reader": {"llm_summaries_per_day": 1}}
    )
    user = _user()
    item = type("I", (), {"id": uuid.uuid4()})()
    monkeypatch.setattr(type(ent.settings), "llm_configured", property(lambda s: True))
    delay = AsyncMock()
    with (
        patch("app.services.summarization.lazy.needs_llm_summary", return_value=True),
        patch("app.workers.tasks.compute_embeddings.summarize_item_with_llm.delay", delay),
    ):
        await content._queue_llm_summary(item, user, db_session)
        item.id = uuid.uuid4()
        await content._queue_llm_summary(item, user, db_session)
    assert delay.call_count == 1


@pytest.mark.asyncio
async def test_a_paused_account_gets_nothing(hosted):
    """#341: 0 means unlimited in the quota table; a paused account must get none."""
    user = _user("free")
    assert ent.is_paused(user)
    assert await ent.remaining_today(user, ent.LLM_SUMMARIES) == 0
    assert not await ent.take(user, ent.DIGEST_BUILDS)
    assert (await ent.newsletter_allowed(user, 10))[0] is False
