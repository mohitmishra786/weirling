"""Paused hosted accounts: reminder, then deletion (D-18, #341).

There is no free hosted tier. When the trial (or a paid plan) ends without a
new plan, the account is paused (see entitlements.is_paused). This daily task:

1. emails a reminder HOSTED_DELETE_REMINDER_DAYS before deletion;
2. deletes the account and all its data HOSTED_PAUSED_DELETE_DAYS after the
   pause began, and emails a confirmation.

A reader always gets the full reminder period: if the reminder goes out late,
deletion moves back with it. Subscribing at any point ends the pause, and a
later pause starts the clock again. HOSTED_PAUSED_DELETE_DAYS=0 turns
deletion off. Accounts that can't be emailed (unverified) are still deleted
on schedule.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.utils.logging import get_logger
from app.workers.celery_app import celery_app
from app.workers.tasks.reengagement import _button, _shell

logger = get_logger(__name__)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def schedule(
    *, paused_since: datetime, reminder_sent_at: datetime | None, now: datetime
) -> tuple[bool, bool]:
    """(send the reminder now, delete now). Pure; tested per case."""
    s = get_settings()
    if s.hosted_paused_delete_days <= 0:
        return False, False
    remind_days = max(0, min(s.hosted_delete_reminder_days, s.hosted_paused_delete_days))
    delete_at = paused_since + timedelta(days=s.hosted_paused_delete_days)
    # A reminder from an earlier pause doesn't count for this one.
    sent = reminder_sent_at if reminder_sent_at and reminder_sent_at >= paused_since else None
    if sent is None:
        return now >= delete_at - timedelta(days=remind_days), False
    return False, now >= max(delete_at, sent + timedelta(days=remind_days))


def _day(moment: datetime) -> str:
    return f"{moment:%B} {moment.day}"


def render_reminder(
    *, frontend: str, unsubscribe: str, delete_on: datetime
) -> tuple[str, str, str]:
    plans = f"{frontend}/preferences#plan"
    export = f"{frontend}/preferences"
    subject = f"Your Weirling account will be deleted on {_day(delete_on)}"
    lines = [
        f"Your account has been paused since your trial or plan ended. On {_day(delete_on)} it will be "
        "deleted, with your sources, saved articles, ratings and reading history.",
        "To keep it, choose a plan before then. To keep a copy of your data, export it.",
    ]
    buttons = [(plans, "See plans"), (export, "Export my data")]
    body = "".join(f"<p>{line}</p>" for line in lines) + "".join(
        _button(u, label) for u, label in buttons
    )
    text = "\n\n".join(lines) + "\n\n" + "\n".join(f"{label}: {u}" for u, label in buttons)
    return subject, _shell(body, unsubscribe), f"{text}\n\nStop digest emails: {unsubscribe}"


async def _run(now: datetime | None = None) -> dict:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.user import User
    from app.services import billing, entitlements
    from app.services.account.gdpr import delete_user_account
    from app.services.email_verification import may_email
    from app.utils.email import send_email
    from app.utils.unsubscribe import unsubscribe_url

    settings = get_settings()
    if settings.deployment_mode != "hosted" or settings.hosted_paused_delete_days <= 0:
        return {"reminded": 0, "deleted": 0}
    if not billing.billing_enabled():
        # Nobody could have paid, so nobody is deleted for not paying.
        return {"reminded": 0, "deleted": 0, "skipped": "billing off"}
    now = now or datetime.now(UTC)
    frontend = settings.frontend_url.rstrip("/")
    reminded = deleted = 0
    async with AsyncSessionLocal() as session:
        # Candidates: no paid plan in force and no trial running.
        paid = User.tier.in_(billing.PAID_TIERS) & (
            User.plan_expires_at.is_(None) | (User.plan_expires_at > now)
        )
        trialing = User.trial_ends_at.is_not(None) & (User.trial_ends_at > now)
        users = list((await session.execute(select(User).where(~paid, ~trialing))).scalars())
        for user in users:
            # Same clock as the candidate query above.
            if billing.effective_plan(user, now=now) != entitlements.PAUSED:
                continue
            since = billing.paused_since(user)
            remind, delete = schedule(
                paused_since=since,
                reminder_sent_at=_aware(user.deletion_reminder_sent_at),
                now=now,
            )
            if remind:
                delete_on = max(
                    since + timedelta(days=settings.hosted_paused_delete_days),
                    now + timedelta(days=settings.hosted_delete_reminder_days),
                )
                if may_email(user):
                    unsubscribe = unsubscribe_url(user.id)
                    subject, html_body, text_body = render_reminder(
                        frontend=frontend, unsubscribe=unsubscribe, delete_on=delete_on
                    )
                    await send_email(
                        to=user.email,
                        subject=subject,
                        html_body=html_body,
                        text_body=text_body,
                        extra_headers={
                            "List-Unsubscribe": f"<{unsubscribe}>",
                            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                        },
                    )
                # Recorded even when the address can't be emailed, so the
                # deletion still happens on schedule.
                user.deletion_reminder_sent_at = now
                reminded += 1
            elif delete:
                email = user.email if may_email(user) else None
                await billing.cancel_for_deletion(session, user)
                await delete_user_account(session, user.id)
                await session.commit()
                deleted += 1
                logger.info("Deleted a paused hosted account after the notice period")
                if email:
                    await send_email(
                        to=email,
                        subject="Your Weirling account has been deleted",
                        html_body=(
                            "<p>Your account had no plan for the notice period, so your "
                            "Weirling account and all its data have now been deleted, as our "
                            "earlier email said.</p>"
                            "<p>You're welcome back any time with a new account.</p>"
                        ),
                        text_body=(
                            "Your account had no plan for the notice period, so your Weirling "
                            "account and all its data have now been deleted, as our earlier "
                            "email said.\n\nYou're welcome back any time with a new account."
                        ),
                    )
        await session.commit()
    logger.info(f"Paused accounts: {reminded} reminded, {deleted} deleted")
    return {"reminded": reminded, "deleted": deleted}


@celery_app.task(name="app.workers.tasks.paused_accounts.process_paused_accounts")
def process_paused_accounts() -> dict:
    return asyncio.run(_run())
