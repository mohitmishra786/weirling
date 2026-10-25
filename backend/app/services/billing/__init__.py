"""Hosted billing through a merchant of record (#173).

A merchant of record (Lemon Squeezy or Paddle) sells the subscription, so the
operator does not register for VAT/GST in every buyer's country. Weirling
only reacts to the provider's signed webhooks:

- `users.tier` is what was bought ("reader", "pro" or "team"); `plan_expires_at` is
  when it lapses. `effective_plan()` combines them with the trial, so an
  expired subscription downgrades itself without a sweep job.
- Every event is recorded in `billing_events` once; a replay is a no-op.
  Events older than the last one applied to the user are recorded but not
  applied, so out-of-order delivery can't resurrect a cancelled plan.
- The tier comes only from the configured price/variant ids, never from
  anything the buyer can put in a checkout URL.

Self-hosted instances never see any of this: `effective_plan` is "selfhost".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.billing import BillingEvent
from app.models.user import User
from app.utils.logging import get_logger

logger = get_logger(__name__)

PAID_TIERS = ("reader", "pro", "team")
# Plans offered at checkout, cheapest first (#172), with their display names.
SOLD_PLANS = ("reader", "pro")
PLAN_NAMES = {
    "reader": "Reader",
    "pro": "Pro",
    "team": "Team",
    "trial": "Trial",
    "paused": "Paused",
}
# Provider subscription statuses, normalised.
ACTIVE = "active"  # paid and current
TRIALING = "trialing"  # provider-side trial (counts as paid access)
PAST_DUE = "past_due"  # payment failed; the provider is retrying (dunning)
CANCELLED = "cancelled"  # will not renew; access until period end
ENDED = "ended"  # expired, refunded, paused, or cancelled immediately


@dataclass
class BillingUpdate:
    """One provider webhook, normalised."""

    provider: str
    key: str  # unique per event
    event_type: str
    occurred_at: datetime
    status: str | None = None  # one of the constants above; None = nothing to apply
    user_id: uuid.UUID | None = None
    customer_id: str | None = None
    subscription_id: str | None = None
    price_id: str | None = None
    period_end: datetime | None = None
    portal_url: str | None = None


def billing_enabled() -> bool:
    s = get_settings()
    return s.deployment_mode == "hosted" and bool(s.billing_provider)


def _ids(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def tier_for_price(price_id: str | None) -> str | None:
    s = get_settings()
    if price_id is None:
        return None
    if str(price_id) in _ids(s.billing_team_price_ids):
        return "team"
    if str(price_id) in _ids(s.billing_pro_price_ids) or (
        s.razorpay_plan_id_pro and str(price_id) == s.razorpay_plan_id_pro
    ):
        return "pro"
    if str(price_id) in _ids(s.billing_reader_price_ids):
        return "reader"
    return None


def checkout_target(plan: str, interval: str) -> str:
    """The checkout link (Lemon Squeezy) or price id (Paddle) for a plan; "" = not sold."""
    s = get_settings()
    yearly = interval == "year"
    if plan == "reader":
        own = s.billing_checkout_reader_yearly if yearly else s.billing_checkout_reader_monthly
        return own or (s.billing_checkout_yearly if yearly else s.billing_checkout_monthly)
    if plan == "pro":
        return s.billing_checkout_pro_yearly if yearly else s.billing_checkout_pro_monthly
    return ""


def plans_on_sale() -> list[dict]:
    """The plans a reader can buy, cheapest first, with what each includes."""
    if not billing_enabled():
        return []
    from app.services.entitlements import PRO_FEATURES, PRO_PLANS, plan_quotas

    offered = []
    for plan in SOLD_PLANS:
        intervals = [i for i in ("month", "year") if checkout_target(plan, i)]
        if intervals:
            offered.append(
                {
                    "plan": plan,
                    "name": PLAN_NAMES[plan],
                    "intervals": intervals,
                    "quotas": plan_quotas(plan),
                    "features": list(PRO_FEATURES) if plan in PRO_PLANS else [],
                }
            )
    return offered


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def effective_plan(user: User, *, now: datetime | None = None) -> str:
    """ "selfhost" | "team" | "pro" | "reader" | "trial" | "paused".

    "paused": hosted, no plan in force and no trial running. There is no free
    hosted tier (D-18); see entitlements.is_paused and paused_since.
    """
    if get_settings().deployment_mode != "hosted":
        return "selfhost"
    now = now or datetime.now(UTC)
    expires = _aware(user.plan_expires_at)
    if user.tier in PAID_TIERS and (expires is None or expires > now):
        return user.tier
    if not billing_enabled():
        # No way to pay yet (a hosted beta before BILLING_PROVIDER is set):
        # nobody's trial runs out, so nobody is paused, emailed or deleted.
        return "trial"
    trial = _aware(user.trial_ends_at)
    if trial is not None and trial > now:
        return "trial"
    return "paused"


def paused_since(user: User) -> datetime:
    """When an unpaid hosted account stopped having a plan or trial (#341)."""
    ends = [t for t in (_aware(user.trial_ends_at), _aware(user.plan_expires_at)) if t is not None]
    if ends:
        return max(ends)
    return _aware(user.created_at) or datetime.now(UTC)


def start_trial(user: User, *, now: datetime | None = None) -> None:
    """New hosted accounts get the configured trial; no card needed."""
    days = get_settings().billing_trial_days
    if get_settings().deployment_mode == "hosted" and days > 0 and user.trial_ends_at is None:
        user.trial_ends_at = (now or datetime.now(UTC)) + timedelta(days=days)


async def _find_user(session: AsyncSession, update: BillingUpdate) -> User | None:
    if update.user_id is not None:
        user = await session.get(User, update.user_id)
        if user is not None:
            return user
    clauses = []
    if update.subscription_id:
        clauses.append(User.billing_subscription_id == update.subscription_id)
    if update.customer_id:
        clauses.append(User.billing_customer_id == update.customer_id)
    if not clauses:
        return None
    return (await session.execute(select(User).where(or_(*clauses)).limit(1))).scalar_one_or_none()


def _apply(user: User, update: BillingUpdate, now: datetime) -> None:
    s = get_settings()
    user.billing_provider = update.provider
    user.billing_customer_id = update.customer_id or user.billing_customer_id
    user.billing_subscription_id = update.subscription_id or user.billing_subscription_id
    user.billing_portal_url = update.portal_url or user.billing_portal_url
    user.billing_status = update.status
    user.billing_event_at = update.occurred_at
    if update.status == ENDED:
        user.tier = "free"
        user.plan_expires_at = now
        return
    tier = tier_for_price(update.price_id)
    if tier is not None:
        user.tier = tier
    elif user.tier not in PAID_TIERS:
        logger.warning(f"Billing event {update.key}: unknown price id {update.price_id!r}")
        return
    end = update.period_end
    if update.status == PAST_DUE and end is not None:
        end = max(end, now) + timedelta(days=s.billing_past_due_grace_days)
    user.plan_expires_at = end
    if update.status in (ACTIVE, TRIALING):
        user.trial_ends_at = None  # converted; the paid plan governs access now


async def apply_update(session: AsyncSession, update: BillingUpdate) -> str:
    """Apply one webhook. Returns "applied" | "duplicate" | "stale" | "ignored" | "unknown_user"."""
    now = datetime.now(UTC)
    user = await _find_user(session, update) if update.status else None
    if update.status is None:
        outcome = "ignored"
    elif user is None:
        outcome = "unknown_user"
    elif (last := _aware(user.billing_event_at)) is not None and update.occurred_at < last:
        outcome = "stale"
    else:
        outcome = "applied"
    inserted = (
        await session.execute(
            insert(BillingEvent)
            .values(
                key=update.key,
                provider=update.provider,
                event_type=update.event_type,
                user_id=user.id if user else None,
                outcome=outcome,
            )
            .on_conflict_do_nothing()
            .returning(BillingEvent.key)
        )
    ).scalar_one_or_none()
    if inserted is None:
        return "duplicate"
    if outcome == "applied" and user is not None:
        plan_before, status_before = effective_plan(user, now=now), user.billing_status
        _apply(user, update, now)
        await _track_plan_change(session, user, plan_before, status_before, now)
    elif outcome == "unknown_user":
        logger.warning(f"Billing event {update.key} matches no account")
    await session.flush()
    return outcome


async def _track_plan_change(
    session: AsyncSession, user: User, plan_before: str, status_before: str | None, now: datetime
) -> None:
    """Product events for a billing change (#131): a first paid plan, a cancel."""
    from app.services.product_metrics import track

    plan_after = effective_plan(user, now=now)
    if plan_after in PAID_TIERS and plan_before not in PAID_TIERS:
        await track(session, user.id, "trial_converted", plan=plan_after, previous=plan_before)
    if user.billing_status in (CANCELLED, ENDED) and status_before not in (CANCELLED, ENDED):
        await track(session, user.id, "cancel", plan=plan_before)


async def cancel_for_deletion(session: AsyncSession, user: User) -> str:
    """Cancel the user's subscription before their account is erased (#221).

    Returns "none", "cancelled" or "failed". A failure never blocks the
    erasure: it is recorded for the operator as a billing_events row that
    holds only the provider and subscription id, so the subscription can be
    cancelled by hand.
    """
    if not user.billing_subscription_id or user.billing_status == ENDED:
        return "none"
    name = user.billing_provider or get_settings().billing_provider
    adapter = provider(name)
    sub = user.billing_subscription_id
    try:
        if adapter is None:
            raise RuntimeError(f"unknown billing provider {name!r}")
        await adapter.cancel_subscription(sub)
        outcome = "cancelled"
    except Exception as e:
        # Nothing from the account in the log (CodeQL: clear-text private
        # data); the billing_events row below has the provider and the
        # subscription, with outcome "failed", for the operator to act on.
        logger.error(
            f"Cancel on account deletion failed ({type(e).__name__}); "
            "see billing_events (event_type account_deleted_cancel)"
        )
        outcome = "failed"
    await session.execute(
        insert(BillingEvent)
        .values(
            key=f"{name}:deletion-cancel:{sub}",
            provider=name or "",
            event_type="account_deleted_cancel",
            user_id=None,
            outcome=f"{outcome}: subscription {sub}",
        )
        .on_conflict_do_nothing()
    )
    return outcome


def provider(name: str | None = None):
    from app.services.billing import lemonsqueezy, paddle, razorpay

    providers = {"lemonsqueezy": lemonsqueezy, "paddle": paddle, "razorpay": razorpay}
    chosen = (name or get_settings().billing_provider or "").strip().lower()
    return providers.get(chosen)


def academic_eligible(user: User) -> bool:
    """Verified email on an academic domain (#250). Suffixes match whole
    labels, so 'mit.edu' qualifies and 'edu.example.com' does not."""
    if user.email_verified_at is None or "@" not in (user.email or ""):
        return False
    domain = user.email.rsplit("@", 1)[1].lower().strip(".")
    suffixes = [x.strip().lower() for x in get_settings().billing_academic_domains.split(",")]
    return any(
        suffix and (domain.endswith(suffix) or domain == suffix.lstrip(".")) for suffix in suffixes
    )


def academic_discount_for(user: User) -> str | None:
    discount = get_settings().billing_academic_discount
    return discount if discount and academic_eligible(user) else None
