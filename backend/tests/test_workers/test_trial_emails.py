"""Trial start, ending-soon and ended emails (#235)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.config import get_settings
from app.models.user import User
from app.workers.tasks import trial_emails
from app.workers.tasks.trial_emails import ENDED, ENDING, STARTED, due_stage
from tests.conftest import TestingSessionLocal

START = datetime(2026, 10, 1, 12, tzinfo=UTC)
ENDS = START + timedelta(days=30)


@pytest.fixture(autouse=True)
def hosted(monkeypatch):
    monkeypatch.setattr(get_settings(), "deployment_mode", "hosted")
    monkeypatch.setattr(get_settings(), "trial_ending_notice_days", 5)
    monkeypatch.setattr(get_settings(), "billing_provider", "lemonsqueezy")


@pytest.mark.parametrize(
    ("day", "stage", "paid", "expected"),
    [
        (0, 0, False, STARTED),
        (3, STARTED, False, None),
        (25, STARTED, False, ENDING),  # day 25 of 30
        (26, ENDING, False, None),
        (30, ENDING, False, ENDED),
        (31, 0, False, ENDED),  # missed stages are skipped, never sent together
        (25, STARTED, True, None),  # bought a plan
        (0, 0, True, STARTED),
    ],
)
def test_each_transition(day, stage, paid, expected):
    now = START + timedelta(days=day)
    assert due_stage(trial_ends_at=ENDS, current_stage=stage, paid=paid, now=now) == expected


async def _user(db_session, **kw):
    kw = {"email_verified_at": START, "trial_ends_at": ENDS, **kw}
    user = User(email=f"tr{uuid.uuid4().hex[:6]}@example.com", hashed_password="x", **kw)
    db_session.add(user)
    await db_session.commit()
    return user


async def _run(now):
    outbox: list[dict] = []

    async def fake_send(**kwargs):
        outbox.append(kwargs)
        return True

    with (
        patch("app.database.AsyncSessionLocal", TestingSessionLocal),
        patch("app.utils.email.send_email", fake_send),
    ):
        await trial_emails._run(now=now)
    return outbox


@pytest.mark.asyncio
async def test_three_emails_once_each_over_the_trial(db_session):
    user = await _user(db_session)
    seen = []
    for hour in range(0, 40 * 24, 6):
        now = START + timedelta(hours=hour)
        seen += [(hour // 24, m) for m in await _run(now) if m["to"] == user.email]
    assert [(d, m["subject"]) for d, m in seen] == [
        (0, "Your Weirling trial runs until October 31"),
        (25, "Your Weirling trial ends on October 31"),
        (30, "Your Weirling trial has ended"),
    ]
    ended = seen[-1][1]
    assert "/preferences" in ended["text_body"] and "Export my data" in ended["html_body"]
    # No free tier (D-18): the warning and the final email say when it is deleted.
    assert "deleted on November 30" in seen[1][1]["text_body"]
    assert "paused" in ended["text_body"] and "deleted on November 30" in ended["text_body"]
    for _, mail in seen:
        assert mail["extra_headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


@pytest.mark.asyncio
async def test_unverified_paid_and_selfhost_accounts(db_session, monkeypatch):
    unverified = await _user(db_session, email_verified_at=None)
    paid = await _user(
        db_session,
        trial_email_stage=STARTED,
        tier="reader",
        plan_expires_at=ENDS + timedelta(days=300),
    )
    paid_id = paid.id
    monkeypatch.setattr(get_settings(), "email_verification", "required")
    outbox = await _run(ENDS + timedelta(days=1))
    assert not [m for m in outbox if m["to"] in (unverified.email, paid.email)]
    async with TestingSessionLocal() as s:
        assert (await s.get(User, paid_id)).trial_email_stage == ENDED

    monkeypatch.setattr(get_settings(), "deployment_mode", "selfhost")
    assert await trial_emails._run(now=START) == {"sent": 0}


@pytest.mark.asyncio
async def test_export_works_after_the_trial_ends(client, db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "registration_mode", "open")
    monkeypatch.setattr(get_settings(), "email_verification", "off")
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"ex{uuid.uuid4().hex[:6]}@example.com",
            "password": "TestPass123!",
            "accept_terms": True,
        },
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    user.trial_ends_at = datetime.now(UTC) - timedelta(days=10)
    await db_session.commit()
    assert (await client.get("/api/v1/billing", headers=headers)).json()["plan"] == "paused"
    assert (await client.get("/api/v1/account/export", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_no_trial_emails_while_billing_is_off(db_session, monkeypatch):
    """Nothing to buy and nothing runs out, so "your trial ends" would be untrue."""
    monkeypatch.setattr(get_settings(), "billing_provider", "")
    user = await _user(db_session)
    for day in (0, 26, 31):
        assert not [m for m in await _run(START + timedelta(days=day)) if m["to"] == user.email]
