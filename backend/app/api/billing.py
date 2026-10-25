"""Hosted billing endpoints (#173). Self-hosted instances report "selfhost"."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.services import billing
from app.utils.logging import get_logger

router = APIRouter(prefix="/billing", tags=["billing"])
logger = get_logger(__name__)


class CheckoutRequest(BaseModel):
    interval: Literal["month", "year"] = "month"
    plan: Literal["reader", "pro"] = "reader"


def _manage_via() -> str:
    adapter = billing.provider()
    return "portal" if adapter is None or getattr(adapter, "HAS_PORTAL", True) else "email"


@router.get("")
async def billing_state(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> dict:
    from app.services import entitlements

    plan = billing.effective_plan(user)
    trial_days_left = None
    if plan == "trial" and user.trial_ends_at is not None:
        ends = (
            user.trial_ends_at
            if user.trial_ends_at.tzinfo
            else user.trial_ends_at.replace(tzinfo=UTC)
        )
        trial_days_left = max(0, (ends - datetime.now(UTC)).days)
    return {
        "plan": plan,
        "billing_enabled": billing.billing_enabled(),
        # Shown before checkout so the reader knows it will apply (#250).
        "academic_discount": bool(billing.academic_discount_for(user)),
        "status": user.billing_status,
        "trial_ends_at": user.trial_ends_at,
        "trial_days_left": trial_days_left,
        "plan_expires_at": user.plan_expires_at,
        "can_manage": bool(
            user.billing_customer_id or user.billing_portal_url or user.billing_subscription_id
        ),
        "portal_url": user.billing_portal_url,
        # How a subscriber changes or cancels: the provider's portal, or by
        # email where the provider has none (Razorpay, #342).
        "manage_via": _manage_via(),
        "support_email": get_settings().support_email
        or get_settings().grievance_contact_email
        or None,
        # Plan quotas and today's use (#172); empty on self-host.
        "quotas": await entitlements.usage(session, user),
        # What can be bought, cheapest first, with each plan's limits (#172).
        "plans": billing.plans_on_sale(),
        # Paused (trial over, no plan, #341): when the account will be deleted.
        "deletes_at": _deletes_at(user) if plan == "paused" else None,
    }


def _deletes_at(user: User) -> datetime | None:
    from datetime import timedelta

    from app.config import get_settings

    s = get_settings()
    if s.hosted_paused_delete_days <= 0:
        return None
    due = billing.paused_since(user) + timedelta(days=s.hosted_paused_delete_days)
    sent = user.deletion_reminder_sent_at
    if sent is not None:
        sent = sent if sent.tzinfo else sent.replace(tzinfo=UTC)
        if sent >= billing.paused_since(user):
            due = max(due, sent + timedelta(days=s.hosted_delete_reminder_days))
    return due


def _provider_or_404():
    adapter = billing.provider() if billing.billing_enabled() else None
    if adapter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Billing is not enabled")
    return adapter


@router.post("/checkout")
async def checkout(body: CheckoutRequest, user: User = Depends(get_current_user)) -> dict:
    adapter = _provider_or_404()
    if not billing.checkout_target(body.plan, body.interval):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That plan isn't on sale")
    # A second checkout would start a second subscription; switching plans
    # happens in the provider's portal, which prorates.
    if (
        billing.effective_plan(user) in billing.PAID_TIERS
        and user.billing_status != billing.CANCELLED
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a plan. Change it from Manage billing.",
        )
    try:
        url = await adapter.checkout_url(user, body.interval, body.plan)
    except Exception as e:
        logger.error(f"Checkout creation failed for {user.id}: {e}")
        url = None
    if not url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Checkout is unavailable"
        )
    return {"url": url}


@router.get("/portal")
async def portal(user: User = Depends(get_current_user)) -> dict:
    adapter = _provider_or_404()
    try:
        url = await adapter.portal_url(user)
    except Exception as e:
        logger.error(f"Portal session failed for {user.id}: {e}")
        url = None
    if url is None:
        return {"url": None}
    return {"url": url}


@router.post("/webhook/{provider_name}")
async def webhook(
    provider_name: str, request: Request, session: AsyncSession = Depends(get_db)
) -> dict:
    """Signed provider webhook. Replays and stale events return 200 without effect."""
    adapter = billing.provider(provider_name)
    if adapter is None or not billing.billing_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider")
    raw = await request.body()
    if not adapter.verify(raw, request.headers):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    try:
        update = adapter.parse(raw)
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid payload: {e}")
    outcome = await billing.apply_update(session, update)
    logger.info(f"Billing webhook {update.key} ({update.event_type}): {outcome}")
    return {"status": outcome}
