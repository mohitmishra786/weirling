"""Hosted billing through a merchant of record (#173)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.billing import BillingEvent
from app.models.user import User
from app.services import billing

SECRET = "whsec_test"


@pytest.fixture
def hosted_billing(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    for key, value in {
        "deployment_mode": "hosted",
        "registration_mode": "open",
        "email_verification": "off",
        "billing_webhook_secret": SECRET,
        "billing_pro_price_ids": "111,pri_pro_month,pri_pro_year",
        "billing_team_price_ids": "222",
        "billing_checkout_monthly": "https://shop.example/checkout/buy/abc",
        "billing_trial_days": 30,
        # The trial only runs out once there is a way to pay.
        "billing_provider": "lemonsqueezy",
    }.items():
        monkeypatch.setattr(s, key, value)
    return s


async def _user(db_session, **kw) -> User:
    user = User(email=f"b{uuid.uuid4().hex[:8]}@example.com", hashed_password="x", **kw)
    db_session.add(user)
    await db_session.commit()
    return user


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ plan state


def test_effective_plan_states(hosted_billing):
    now = datetime.now(UTC)
    u = User(email="x@example.com", hashed_password="x", tier="free")
    assert billing.effective_plan(u, now=now) == "paused"
    billing.start_trial(u, now=now)
    assert billing.effective_plan(u, now=now) == "trial"
    assert billing.effective_plan(u, now=now + timedelta(days=31)) == "paused"
    u.tier, u.plan_expires_at = "pro", now + timedelta(days=10)
    assert billing.effective_plan(u, now=now + timedelta(days=5)) == "pro"
    assert billing.effective_plan(u, now=now + timedelta(days=40)) == "paused"  # lapsed
    u.tier, u.plan_expires_at = "team", None  # manually granted, no expiry
    assert billing.effective_plan(u, now=now) == "team"


def test_selfhost_is_always_selfhost():
    u = User(email="x@example.com", hashed_password="x", tier="free")
    assert billing.effective_plan(u) == "selfhost"
    billing.start_trial(u)
    assert u.trial_ends_at is None


@pytest.mark.asyncio
async def test_signup_starts_a_trial_and_the_end_of_it_pauses_the_account(
    client, hosted_billing, monkeypatch
):
    """D-18: no free tier. A trial has Pro's limits; after it, nothing can be added."""
    from app.services import entitlements

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"t{uuid.uuid4().hex[:6]}@example.com",
            "password": "TestPass123!",
            "accept_terms": True,
        },
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    state = (await client.get("/api/v1/billing", headers=headers)).json()
    assert state["plan"] == "trial" and state["trial_days_left"] in (29, 30)

    trial_user = User(email="a@example.com", hashed_password="x")
    billing.start_trial(trial_user)
    entitlements.enforce_source_limit(trial_user, current_count=1500)  # Pro limits in trial
    trial_user.trial_ends_at = datetime.now(UTC) - timedelta(seconds=1)
    assert entitlements.is_paused(trial_user)
    with pytest.raises(Exception) as exc:
        entitlements.enforce_source_limit(trial_user, current_count=0)
    assert getattr(exc.value, "status_code", None) == 402
    assert "trial has ended" in exc.value.detail


# --------------------------------------------------------------- Lemon Squeezy


def _ls(event: str, user_id, *, status: str, variant=111, updated: datetime, **attrs) -> bytes:
    body = {
        "meta": {"event_name": event, "custom_data": {"user_id": str(user_id)}},
        "data": {
            "type": "subscriptions",
            "id": "sub_77",
            "attributes": {
                "status": status,
                "customer_id": 9001,
                "variant_id": variant,
                "updated_at": _iso(updated),
                "urls": {"customer_portal": "https://shop.example/portal/x"},
                **attrs,
            },
        },
    }
    return json.dumps(body).encode()


async def _post_ls(client, raw: bytes, *, secret: str = SECRET):
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return await client.post(
        "/api/v1/billing/webhook/lemonsqueezy",
        content=raw,
        headers={"X-Signature": sig, "Content-Type": "application/json"},
    )


@pytest.mark.asyncio
async def test_lemonsqueezy_purchase_cancel_expire_and_replays(
    client, db_session, hosted_billing, monkeypatch
):
    monkeypatch.setattr(hosted_billing, "billing_provider", "lemonsqueezy")
    user = await _user(db_session)
    t0 = datetime.now(UTC)
    renews = t0 + timedelta(days=30)

    created = _ls(
        "subscription_created", user.id, status="active", updated=t0, renews_at=_iso(renews)
    )
    r = await _post_ls(client, created)
    assert r.status_code == 200 and r.json()["status"] == "applied"
    await db_session.refresh(user)
    assert billing.effective_plan(user) == "pro"
    assert user.billing_subscription_id == "sub_77" and user.billing_customer_id == "9001"
    assert user.billing_portal_url == "https://shop.example/portal/x"
    assert abs((user.plan_expires_at - renews).total_seconds()) < 1

    # Exact replay: recorded once, no effect.
    assert (await _post_ls(client, created)).json()["status"] == "duplicate"
    assert (await db_session.execute(select(func.count()).select_from(BillingEvent))).scalar() == 1
    # Bad signature.
    assert (await _post_ls(client, created, secret="wrong")).status_code == 401

    # Cancel: keeps access until ends_at.
    ends = t0 + timedelta(days=12)
    cancelled = _ls(
        "subscription_cancelled",
        user.id,
        status="cancelled",
        updated=t0 + timedelta(minutes=5),
        ends_at=_iso(ends),
    )
    assert (await _post_ls(client, cancelled)).json()["status"] == "applied"
    await db_session.refresh(user)
    assert billing.effective_plan(user) == "pro"
    assert billing.effective_plan(user, now=ends + timedelta(seconds=1)) == "paused"

    # Expire: downgraded now.
    expired = _ls("subscription_expired", user.id, status="expired", updated=t0 + timedelta(days=1))
    assert (await _post_ls(client, expired)).json()["status"] == "applied"
    await db_session.refresh(user)
    assert user.tier == "free" and billing.effective_plan(user) == "paused"

    # A late, older "active" event must not resurrect the plan.
    late = _ls(
        "subscription_updated",
        user.id,
        status="active",
        updated=t0 + timedelta(minutes=1),
        renews_at=_iso(renews),
    )
    assert (await _post_ls(client, late)).json()["status"] == "stale"
    await db_session.refresh(user)
    assert billing.effective_plan(user) == "paused"


@pytest.mark.asyncio
async def test_unknown_variant_never_grants_a_plan(client, db_session, hosted_billing, monkeypatch):
    monkeypatch.setattr(hosted_billing, "billing_provider", "lemonsqueezy")
    user = await _user(db_session)
    raw = _ls(
        "subscription_created",
        user.id,
        status="active",
        variant=999,
        updated=datetime.now(UTC),
        renews_at=_iso(datetime.now(UTC) + timedelta(days=30)),
    )
    await _post_ls(client, raw)
    await db_session.refresh(user)
    assert user.tier == "free" and billing.effective_plan(user) == "paused"


@pytest.mark.asyncio
async def test_team_variant_grants_team(client, db_session, hosted_billing, monkeypatch):
    monkeypatch.setattr(hosted_billing, "billing_provider", "lemonsqueezy")
    user = await _user(db_session)
    raw = _ls(
        "subscription_created",
        user.id,
        status="active",
        variant=222,
        updated=datetime.now(UTC),
        renews_at=_iso(datetime.now(UTC) + timedelta(days=30)),
    )
    await _post_ls(client, raw)
    await db_session.refresh(user)
    assert user.tier == "team"


@pytest.mark.asyncio
async def test_checkout_link_carries_the_account_and_selfhost_has_no_billing(
    client, hosted_billing, monkeypatch
):
    monkeypatch.setattr(hosted_billing, "billing_provider", "lemonsqueezy")
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"c{uuid.uuid4().hex[:6]}@example.com",
            "password": "TestPass123!",
            "accept_terms": True,
        },
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    url = (await client.post("/api/v1/billing/checkout", json={}, headers=headers)).json()["url"]
    assert url.startswith("https://shop.example/checkout/buy/abc?")
    assert f"checkout%5Bcustom%5D%5Buser_id%5D={me['id']}" in url

    monkeypatch.setattr(hosted_billing, "deployment_mode", "selfhost")
    assert (
        await client.post("/api/v1/billing/checkout", json={}, headers=headers)
    ).status_code == 404
    assert (await client.get("/api/v1/billing", headers=headers)).json()["plan"] == "selfhost"
    assert (
        await client.post("/api/v1/billing/webhook/lemonsqueezy", content=b"{}")
    ).status_code == 404


@pytest.mark.asyncio
async def test_reader_and_pro_on_sale_with_their_own_checkouts(
    client, db_session, hosted_billing, monkeypatch
):
    """Two plans to choose from (#172); the old single link is Reader's fallback."""
    monkeypatch.setattr(hosted_billing, "billing_provider", "lemonsqueezy")
    monkeypatch.setattr(
        hosted_billing, "billing_checkout_pro_yearly", "https://shop.example/buy/pro-y"
    )
    monkeypatch.setattr(hosted_billing, "billing_reader_price_ids", "333")
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"p{uuid.uuid4().hex[:6]}@example.com",
            "password": "TestPass123!",
            "accept_terms": True,
        },
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    plans = (await client.get("/api/v1/billing", headers=headers)).json()["plans"]
    assert [(p["plan"], p["name"], p["intervals"]) for p in plans] == [
        ("reader", "Reader", ["month"]),
        ("pro", "Pro", ["year"]),
    ]
    assert plans[0]["quotas"]["sources"] == 500 and plans[1]["quotas"]["sources"] == 2000

    async def buy(plan, interval):
        return await client.post(
            "/api/v1/billing/checkout", json={"plan": plan, "interval": interval}, headers=headers
        )

    assert (await buy("pro", "year")).json()["url"].startswith("https://shop.example/buy/pro-y?")
    assert (
        (await buy("reader", "month"))
        .json()["url"]
        .startswith("https://shop.example/checkout/buy/abc?")
    )
    assert (await buy("pro", "month")).status_code == 404
    assert billing.tier_for_price("333") == "reader"

    # Already paying: change plans in the portal, not with a second subscription.
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    user = await db_session.get(User, uuid.UUID(me["id"]))
    user.tier, user.plan_expires_at = "reader", datetime.now(UTC) + timedelta(days=20)
    user.billing_status = billing.ACTIVE
    await db_session.commit()
    assert (await buy("pro", "year")).status_code == 409


# ---------------------------------------------------------------------- Paddle


def _paddle(event: str, user_id, *, status: str, occurred: datetime, **data) -> bytes:
    body = {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "event_type": event,
        "occurred_at": _iso(occurred),
        "data": {
            "id": "sub_p1",
            "status": status,
            "customer_id": "ctm_1",
            "custom_data": {"user_id": str(user_id)},
            "items": [{"price": {"id": "pri_pro_year"}}],
            **data,
        },
    }
    return json.dumps(body).encode()


def _paddle_sig(raw: bytes, ts: int | None = None, secret: str = SECRET) -> str:
    ts = ts or int(time.time())
    h1 = hmac.new(secret.encode(), f"{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


async def _post_paddle(client, raw: bytes, sig: str | None = None):
    return await client.post(
        "/api/v1/billing/webhook/paddle",
        content=raw,
        headers={"Paddle-Signature": sig or _paddle_sig(raw), "Content-Type": "application/json"},
    )


@pytest.mark.asyncio
async def test_paddle_lifecycle(client, db_session, hosted_billing, monkeypatch):
    monkeypatch.setattr(hosted_billing, "billing_provider", "paddle")
    monkeypatch.setattr(hosted_billing, "billing_past_due_grace_days", 3)
    user = await _user(db_session)
    t0 = datetime.now(UTC)
    period_end = t0 + timedelta(days=365)
    activated = _paddle(
        "subscription.activated",
        user.id,
        status="active",
        occurred=t0,
        current_billing_period={"ends_at": _iso(period_end)},
    )
    assert (await _post_paddle(client, activated)).json()["status"] == "applied"
    assert (await _post_paddle(client, activated)).json()["status"] == "duplicate"
    await db_session.refresh(user)
    assert billing.effective_plan(user) == "pro" and user.billing_customer_id == "ctm_1"

    # Old or forged signatures are refused.
    stale_sig = _paddle_sig(activated, ts=int(time.time()) - 3600)
    assert (await _post_paddle(client, activated, stale_sig)).status_code == 401
    assert (await _post_paddle(client, activated, "ts=1;h1=00")).status_code == 401

    # Payment failed: access continues through a short grace period.
    past_due = _paddle(
        "subscription.past_due",
        user.id,
        status="past_due",
        occurred=t0 + timedelta(minutes=1),
        current_billing_period={"ends_at": _iso(t0)},
    )
    await _post_paddle(client, past_due)
    await db_session.refresh(user)
    assert billing.effective_plan(user, now=t0 + timedelta(days=2)) == "pro"
    assert billing.effective_plan(user, now=t0 + timedelta(days=4)) == "paused"

    # Scheduled cancel keeps access until it takes effect; canceled ends it.
    effective = t0 + timedelta(days=20)
    scheduled = _paddle(
        "subscription.updated",
        user.id,
        status="active",
        occurred=t0 + timedelta(minutes=2),
        scheduled_change={"action": "cancel", "effective_at": _iso(effective)},
        current_billing_period={"ends_at": _iso(period_end)},
    )
    await _post_paddle(client, scheduled)
    await db_session.refresh(user)
    assert user.billing_status == "cancelled"
    assert abs((user.plan_expires_at - effective).total_seconds()) < 1
    ended = _paddle(
        "subscription.canceled", user.id, status="canceled", occurred=t0 + timedelta(minutes=3)
    )
    await _post_paddle(client, ended)
    await db_session.refresh(user)
    assert billing.effective_plan(user) == "paused"


@pytest.mark.asyncio
async def test_event_for_an_unknown_account_is_recorded_not_applied(
    client, db_session, hosted_billing, monkeypatch
):
    monkeypatch.setattr(hosted_billing, "billing_provider", "paddle")
    raw = _paddle(
        "subscription.activated", uuid.uuid4(), status="active", occurred=datetime.now(UTC)
    )
    assert (await _post_paddle(client, raw)).json()["status"] == "unknown_user"
    # Non-subscription events are acknowledged and ignored.
    other = json.dumps(
        {"event_id": "evt_tx", "event_type": "transaction.completed", "data": {}}
    ).encode()
    assert (await _post_paddle(client, other)).json()["status"] == "ignored"


def test_without_billing_the_trial_never_runs_out(hosted_billing, monkeypatch):
    """A hosted beta before BILLING_PROVIDER is set: nobody can pay, so nobody
    is paused, emailed about a trial ending, or deleted."""
    monkeypatch.setattr(hosted_billing, "billing_provider", "")
    now = datetime.now(UTC)
    u = User(email="x@example.com", hashed_password="x", tier="free")
    billing.start_trial(u, now=now)
    assert billing.effective_plan(u, now=now + timedelta(days=400)) == "trial"
    u.tier, u.plan_expires_at = "pro", None  # a granted plan still wins
    assert billing.effective_plan(u, now=now) == "pro"


@pytest.mark.asyncio
async def test_billing_state_has_no_trial_end_without_billing(client, hosted_billing, monkeypatch):
    monkeypatch.setattr(hosted_billing, "billing_provider", "")
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"beta{uuid.uuid4().hex[:6]}@example.com",
            "password": "TestPass123!",
            "accept_terms": True,
        },
    )
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    state = (await client.get("/api/v1/billing", headers=headers)).json()
    assert state["plan"] == "trial"
    assert state["billing_enabled"] is False
    assert state["trial_ends_at"] is None and state["trial_days_left"] is None
