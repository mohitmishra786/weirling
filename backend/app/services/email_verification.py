"""Email verification and per-domain signup limits (#175).

On hosted instances an account must prove it owns its address before
Weirling sends it anything but the verification link, and before inbound
newsletters for it are accepted. Otherwise a stranger could sign up with
someone else's address and point daily digests at them, which also burns the
sender's reputation. Self-hosted instances skip this by default: the owner
creates their own account.

A magic-link sign-in also verifies the address, since it proves inbox access.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user import User
from app.utils.logging import get_logger, sanitize_log

logger = get_logger(__name__)

ALGORITHM = "HS256"
TOKEN_TYPE = "verify_email"
# Shared providers: thousands of real users, so a much higher per-domain limit.
LARGE_PROVIDERS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "proton.me",
        "protonmail.com",
        "fastmail.com",
        "gmx.com",
        "aol.com",
    }
)
LARGE_PROVIDER_MULTIPLIER = 20


def verification_required() -> bool:
    s = get_settings()
    mode = (s.email_verification or "auto").strip().lower()
    if mode in ("required", "on", "true"):
        return True
    if mode in ("off", "false"):
        return False
    return s.deployment_mode == "hosted"


def is_verified(user: User) -> bool:
    return user.email_verified_at is not None or not verification_required()


def may_email(user: User) -> bool:
    """Whether non-verification email (digests, nudges) may go to this user."""
    return is_verified(user)


async def may_receive_newsletters(session: AsyncSession, user_id: uuid.UUID | str | None) -> bool:
    if user_id is None or not verification_required():
        return True
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        return False
    user = await session.get(User, uid)
    return user is not None and is_verified(user)


def mark_verified(user: User) -> None:
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)


def make_token(user: User, *, now: datetime | None = None) -> str:
    s = get_settings()
    now = now or datetime.now(UTC)
    payload = {
        "type": TOKEN_TYPE,
        "sub": str(user.id),
        # Bound to the address: changing the email invalidates old links.
        "email": user.email.lower(),
        "iat": now,
        "exp": now + timedelta(hours=s.email_verification_expire_hours),
    }
    return jwt.encode(payload, s.secret_key, algorithm=ALGORITHM)


async def verify_token(session: AsyncSession, token: str) -> User | None:
    """The user the token verifies, now marked verified; None if invalid."""
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != TOKEN_TYPE:
        return None
    try:
        user = await session.get(User, uuid.UUID(str(payload.get("sub"))))
    except ValueError:
        return None
    if user is None or user.email.lower() != payload.get("email"):
        return None
    mark_verified(user)
    await session.flush()
    return user


async def send_verification_email(user: User) -> bool:
    from html import escape

    from app.utils.email import send_email

    s = get_settings()
    link = f"{s.frontend_url.rstrip('/')}/auth/verify?token={make_token(user)}"
    html = (
        '<div style="font-family:sans-serif;max-width:480px;margin:0 auto">'
        '<h1 style="font-size:1.3rem;color:#1d4ed8">Confirm your email</h1>'
        "<p>Confirm this address to start receiving your Weirling digest. "
        f"The link expires in {s.email_verification_expire_hours} hours.</p>"
        f'<p><a href="{escape(link)}" style="display:inline-block;background:#1d4ed8;'
        'color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none">'
        "Confirm email</a></p>"
        '<p style="font-size:12px;color:#6b7280">If you didn\'t create a Weirling '
        "account, ignore this email and nothing more will be sent.</p>"
        "</div>"
    )
    import hashlib

    return await send_email(
        to=user.email,
        subject="Confirm your Weirling email",
        html_body=html,
        idempotency_key="verify/" + hashlib.sha256(link.encode()).hexdigest(),
    )


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower()


async def domain_signup_allowed(
    session: AsyncSession, email: str, *, now: datetime | None = None
) -> bool:
    """Per-domain hourly signup cap; only enforced when verification is required."""
    if not verification_required():
        return True
    domain = email_domain(email)
    limit = get_settings().signup_per_domain_per_hour
    if limit <= 0:
        return True
    if domain in LARGE_PROVIDERS:
        limit *= LARGE_PROVIDER_MULTIPLIER
    since = (now or datetime.now(UTC)) - timedelta(hours=1)
    recent = (
        await session.execute(
            select(func.count(User.id)).where(
                func.lower(User.email).like(f"%@{domain}"),
                User.created_at >= since,
            )
        )
    ).scalar() or 0
    if recent >= limit:
        logger.warning(
            f"Signup refused: {recent} signups from @{sanitize_log(domain)} in the last hour"
        )
        return False
    return True
