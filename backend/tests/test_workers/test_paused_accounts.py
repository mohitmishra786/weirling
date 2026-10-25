"""No free hosted tier: pause after the trial, reminder, deletion (D-18, #341)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.config import get_settings
from app.models.source import Source
from app.models.user import User
from app.services import entitlements
from app.workers.tasks import paused_accounts
from app.workers.tasks.paused_accounts import schedule
from tests.conftest import TestingSessionLocal

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)  # the trial ended


@pytest.fixture
def hosted(monkeypatch):
    s = get_settings()
    values = {
        "deployment_mode": "hosted",
        "registration_mode": "open",
        "email_verification": "off",
        "billing_trial_days": 30,
        "hosted_paused_delete_days": 30,
        "hosted_delete_reminder_days": 7,
        "billing_provider": "lemonsqueezy",
    }
    # entitlements keeps the settings object it imported; an earlier test may
    # have cleared get_settings' cache, so patch both when they differ.
    for target in {id(s): s, id(entitlements.settings): entitlements.settings}.values():
        for key, value in values.items():
            monkeypatch.setattr(target, key, value)
    return s


# ------------------------------------------------------------------ schedule


@pytest.mark.parametrize(
    ("day", "sent_day", "expected"),
    [
        (10, None, (False, False)),  # paused, nothing due yet
        (23, None, (True, False)),  # 7 days before deletion: remind
        (23, 23, (False, False)),  # reminded, waiting
        (30, 23, (False, True)),  # deletion day
        (35, None, (True, False)),  # task missed the reminder: remind first...
        (40, 35, (False, False)),  # ...and still give the full 7 days
        (42, 35, (False, True)),
        (23, -5, (True, False)),  # a reminder from an earlier pause doesn't count
    ],
)
def test_schedule(hosted, day, sent_day, expected):
    sent = T0 + timedelta(days=sent_day) if sent_day is not None else None
    assert (
        schedule(paused_since=T0, reminder_sent_at=sent, now=T0 + timedelta(days=day)) == expected
    )


def test_deletion_can_be_turned_off(hosted, monkeypatch):
    monkeypatch.setattr(hosted, "hosted_paused_delete_days", 0)
    assert schedule(paused_since=T0, reminder_sent_at=None, now=T0 + timedelta(days=400)) == (
        False,
        False,
    )


# ----------------------------------------------------------- end to end


async def _signup(client, db_session):
    email = f"p{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "TestPass123!", "accept_terms": True},
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return await db_session.get(User, uuid.UUID(me["id"])), headers


@pytest.mark.asyncio
async def test_a_paused_account_is_read_only_but_can_pay_export_and_leave(
    client, db_session, hosted
):
    user, headers = await _signup(client, db_session)
    user.trial_ends_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    blocked = await client.put("/api/v1/preferences", json={"digest_max_items": 9}, headers=headers)
    assert blocked.status_code == 402 and "trial has ended" in blocked.json()["detail"]
    assert (await client.post("/api/v1/digest/generate", headers=headers)).status_code == 402
    assert (await client.get("/api/v1/account/export", headers=headers)).status_code == 200
    state = (await client.get("/api/v1/billing", headers=headers)).json()
    assert state["plan"] == "paused" and state["deletes_at"] is not None
    with patch("app.utils.email.send_email", AsyncMock(return_value=True)):
        assert (await client.delete("/api/v1/account", headers=headers)).status_code == 204


@pytest.mark.asyncio
async def test_feeds_of_paused_accounts_are_not_polled_and_no_digest_is_built(db_session, hosted):
    from app.workers.tasks.build_digest import _build_digest_async
    from app.workers.tasks.ingest_feeds import _unpaused

    paused = User(email=f"a{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    paused.trial_ends_at = datetime.now(UTC) - timedelta(days=1)
    active = User(email=f"b{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    active.trial_ends_at = datetime.now(UTC) + timedelta(days=5)
    db_session.add_all([paused, active])
    await db_session.commit()

    assert await _unpaused(db_session, [paused.id, active.id]) == [active.id]
    assert await _unpaused(db_session, [paused.id]) == []
    with patch("app.database.AsyncSessionLocal", TestingSessionLocal):
        assert (await _build_digest_async(paused.id))["status"] == "paused"


@pytest.mark.asyncio
async def test_reminder_then_deletion(db_session, hosted):
    user = User(
        email=f"d{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        email_verified_at=T0,
        trial_ends_at=T0,
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(Source(user_id=user.id, url="https://example.com/feed"))
    await db_session.commit()
    uid, email = user.id, user.email
    outbox: list[dict] = []

    async def fake_send(**kwargs):
        outbox.append(kwargs)
        return True

    async def run(day):
        with (
            patch("app.database.AsyncSessionLocal", TestingSessionLocal),
            patch("app.utils.email.send_email", fake_send),
        ):
            return await paused_accounts._run(now=T0 + timedelta(days=day))

    assert await run(10) == {"reminded": 0, "deleted": 0}
    assert await run(23) == {"reminded": 1, "deleted": 0}
    assert outbox[-1]["to"] == email and "December 1" in outbox[-1]["subject"]
    assert await run(30) == {"reminded": 0, "deleted": 1}
    assert "has been deleted" in outbox[-1]["subject"]
    async with TestingSessionLocal() as s:
        assert await s.get(User, uid) is None


@pytest.mark.asyncio
async def test_paying_stops_the_clock(db_session, hosted):
    user = User(email=f"q{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    user.trial_ends_at = T0
    user.tier, user.plan_expires_at = "reader", T0 + timedelta(days=365)
    db_session.add(user)
    await db_session.commit()
    assert not entitlements.is_paused(user)
    with (
        patch("app.database.AsyncSessionLocal", TestingSessionLocal),
        patch("app.utils.email.send_email", AsyncMock(return_value=True)),
    ):
        assert await paused_accounts._run(now=T0 + timedelta(days=40)) == {
            "reminded": 0,
            "deleted": 0,
        }


@pytest.mark.asyncio
async def test_nobody_is_deleted_while_billing_is_off(db_session, hosted, monkeypatch):
    for target in {id(hosted): hosted, id(entitlements.settings): entitlements.settings}.values():
        monkeypatch.setattr(target, "billing_provider", "")
    user = User(
        email=f"b{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        email_verified_at=T0,
        trial_ends_at=T0,
    )
    db_session.add(user)
    await db_session.commit()
    from app.workers.tasks.paused_accounts import _run

    result = await _run(T0 + timedelta(days=90))
    assert result["deleted"] == 0 and result.get("skipped") == "billing off"
    assert await db_session.get(User, user.id) is not None
