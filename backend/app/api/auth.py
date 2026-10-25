from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    EmailVerify,
    MagicLinkRequest,
    MagicLinkVerify,
    Token,
    TokenRefresh,
    UserCreate,
    UserLogin,
    UserRead,
)
from app.utils.cache import cache_delete, cache_get, cache_set
from app.utils.logging import get_logger
from app.utils.ratelimit import RateLimiter

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

# auto_error=False: browsers authenticate with the httpOnly session cookie instead (#103).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

ALGORITHM = "HS256"


def _access_secret() -> str:
    return settings.secret_key


def _refresh_secret() -> str:
    return settings.refresh_secret_key or settings.secret_key


def _refresh_allowlist_key(jti: str) -> str:
    return f"refresh:{jti}"


login_rate_limit = RateLimiter(
    max_requests=settings.rate_limit_login_per_minute,
    window_seconds=60,
    scope="login",
    fail_closed=True,
)
register_rate_limit = RateLimiter(
    max_requests=settings.rate_limit_register_per_minute,
    window_seconds=60,
    scope="register",
    fail_closed=True,
)
magic_link_rate_limit = RateLimiter(
    max_requests=settings.rate_limit_register_per_minute,
    window_seconds=60,
    scope="magic",
    fail_closed=True,
)
verify_resend_rate_limit = RateLimiter(
    max_requests=settings.rate_limit_register_per_minute,
    window_seconds=60,
    scope="verify_resend",
    fail_closed=True,
)

_DOMAIN_LIMIT_DETAIL = "Too many new accounts from this email domain. Try again in an hour."

# Precomputed bcrypt hash of a random string, used to spend the same time on a
# password check whether or not the account exists — closing the login timing
# oracle that would otherwise reveal which emails are registered (audit 06-4).
_DUMMY_HASH = bcrypt.hashpw(b"timing-oracle-mitigation", bcrypt.gensalt()).decode()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _create_access_token(user_id: uuid.UUID, *, session_id: str | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    if session_id:
        payload["sid"] = session_id
    return jwt.encode(payload, _access_secret(), algorithm=ALGORITHM)


async def _create_refresh_token(user_id: uuid.UUID) -> str:
    """Mint a refresh token and record its jti in the Redis allowlist so it can
    be rotated/revoked. A refresh token is only valid while its jti is present."""
    now = datetime.now(UTC)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_expire_days),
    }
    token = jwt.encode(payload, _refresh_secret(), algorithm=ALGORITHM)
    await cache_set(
        _refresh_allowlist_key(jti),
        str(user_id),
        ttl_seconds=settings.refresh_token_expire_days * 86400,
    )
    return token


def _decode_token(token: str, *, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    from app.api.session_cookies import ACCESS_COOKIE, enforce_csrf

    if not token:
        # Browser session (#103): access JWT in an httpOnly cookie. Mutating
        # requests must carry the double-submit CSRF token.
        token = request.cookies.get(ACCESS_COOKIE)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        enforce_csrf(request)
        request.state.auth_via = "cookie"

    # API tokens (EC-03): rp_-prefixed bearer tokens, hashed at rest, with
    # read/write scopes. JWTs remain the primary path.
    if token.startswith("rp_"):
        user = await _user_for_api_token(token, request, session)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked API token"
            )
        request.state.auth_via = "api_token"
        _enforce_demo_read_only(request, user)
        _enforce_paused_read_only(request, user)
        _enforce_api_access(request, user)
        return user

    payload = _decode_token(token, secret=_access_secret())
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )

    result = await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    request.state.session_id = payload.get("sid")
    _enforce_demo_read_only(request, user)
    _enforce_paused_read_only(request, user)
    return user


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _enforce_demo_read_only(request: Request, user: User) -> None:
    """In demo mode the demo persona can't change anything (#229).

    Only the demo account, and only with DEMO_MODE on: the operator's own
    account stays writable, and a normal instance where `make demo` seeded the
    same persona is unaffected.
    """
    request.state.is_demo = user.is_demo
    if user.is_demo and request.method in MUTATING_METHODS and not _is_demo_allowed_write(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="The demo is read-only")


# What the browser extension calls with its token. The extension works on
# every plan; any other use of an API token (scripts, MCP) is Pro (#246).
_EXTENSION_ROUTES = {
    ("POST", "/api/v1/extension/save"),
    ("POST", "/api/v1/sources"),
    ("POST", "/api/v1/creators"),
}


def _enforce_api_access(request: Request, user: User) -> None:
    from app.services import entitlements

    if (request.method, request.url.path.rstrip("/")) in _EXTENSION_ROUTES:
        return
    entitlements.require_pro(user, "API and MCP access", verb="are")


# What a paused hosted account may still change (#341): subscribe, sign out,
# and delete itself. Reading and the export are GETs, so they stay open.
_PAUSED_ALLOWED_PREFIXES = ("/api/v1/billing", "/api/v1/auth/")


def _enforce_paused_read_only(request: Request, user: User) -> None:
    """Hosted, trial over and no plan (D-18): the account is read-only."""
    if request.method not in MUTATING_METHODS:
        return
    path = request.url.path
    if path.startswith(_PAUSED_ALLOWED_PREFIXES):
        return
    if request.method == "DELETE" and path.rstrip("/") == "/api/v1/account":
        return
    from app.services import entitlements

    if entitlements.is_paused(user):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=entitlements.PAUSED_MESSAGE
        )


def _is_demo_allowed_write(request: Request) -> bool:
    """Allow reading telemetry/feedback and logout in demo mode (#229)."""
    path = request.url.path
    return (
        path.endswith("/feedback/interaction")
        or path.endswith("/auth/logout")
        or path.endswith("/auth/demo")
    )


async def get_current_user_jwt(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """Session-only auth: refuses API tokens (CodeRabbit CWE-269).

    Token management must never accept an rp_ token — a leaked write-scoped
    token could otherwise mint fresh tokens that outlive its revocation.
    """
    if getattr(request.state, "auth_via", None) == "api_token":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token management requires a signed-in session",
        )
    return user


async def _user_for_api_token(token: str, request: Request, session: AsyncSession) -> User | None:
    from datetime import UTC, datetime

    from app.models.api_token import ApiToken, hash_api_token

    result = await session.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_api_token(token))
    )
    row = result.scalar_one_or_none()
    if row is None or row.revoked:
        return None

    scopes = set(row.scopes or ["read"])
    if request.method in MUTATING_METHODS and "write" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API token is read-only",
        )

    user_result = await session.execute(select(User).where(User.id == row.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        return None
    row.last_used_at = datetime.now(UTC)
    row.use_count = (row.use_count or 0) + 1
    await session.flush()
    return user


async def _issue_tokens(user_id: uuid.UUID, *, session_id: str | None = None) -> Token:
    return Token(
        access_token=_create_access_token(user_id, session_id=session_id),
        refresh_token=await _create_refresh_token(user_id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def _session(
    request: Request, response: Response, user_id: uuid.UUID, *, session_id: str | None = None
) -> Token:
    """Issue tokens; in cookie mode set httpOnly cookies and omit them from the body."""
    from app.api.session_cookies import set_session_cookies, wants_cookies

    token = await _issue_tokens(user_id, session_id=session_id)
    if not wants_cookies(request):
        return token
    csrf = set_session_cookies(
        response, request, access=token.access_token, refresh=token.refresh_token
    )
    return Token(access_token="", refresh_token=None, expires_in=token.expires_in, csrf_token=csrf)


@router.post("/demo", response_model=Token, dependencies=[Depends(login_rate_limit)])
async def demo_login(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> Token:
    """Sign into the seeded demo persona (RL-03, #229). Zero-login for public exploration."""
    s = get_settings()
    if not s.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Demo mode is disabled",
        )
    result = await session.execute(select(User).where(User.email == s.demo_user_email))
    user = result.scalar_one_or_none()
    if user is None:
        from scripts.seed_demo import seed

        await seed(session, password=secrets.token_urlsafe(12))
        await session.commit()
        result = await session.execute(select(User).where(User.email == s.demo_user_email))
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Demo account could not be seeded",
            )
    session_id = uuid.uuid4().hex
    return await _session(request, response, user.id, session_id=session_id)


@router.post(
    "/register",
    response_model=Token,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_rate_limit)],
)
async def register(
    body: UserCreate,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> Token:
    s = get_settings()
    if s.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The demo is read-only",
        )
    from app.services.registration import can_register

    allowed, reason = await can_register(session, body.invite_code)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    from app.services import email_verification

    if not await email_verification.domain_signup_allowed(session, body.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=_DOMAIN_LIMIT_DETAIL
        )

    s = get_settings()
    if s.deployment_mode == "hosted" and not body.accept_terms:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Agreement to the Terms and Privacy policy is required",
        )

    now = datetime.now(UTC)
    user = User(
        email=body.email,
        hashed_password=_hash_password(body.password),
        display_name=body.display_name,
        terms_accepted_at=now if (s.deployment_mode == "hosted" or body.accept_terms) else None,
        terms_version=s.terms_version
        if (s.deployment_mode == "hosted" or body.accept_terms)
        else None,
    )
    from app.services.billing import start_trial

    start_trial(user)
    session.add(user)
    await session.flush()
    from app.services.product_metrics import track

    await track(session, user.id, "signup", via="password")
    if email_verification.verification_required():
        # Best effort: the account works in-app either way and can resend.
        try:
            await email_verification.send_verification_email(user)
        except Exception as e:
            logger.warning(f"Verification email failed for {user.id}: {e}")
    return await _session(request, response, user.id)


@router.post("/login", response_model=Token, dependencies=[Depends(login_rate_limit)])
async def login(
    body: UserLogin,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> Token:
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    # Always run a bcrypt verification (against a dummy hash when the user is
    # absent) so response time doesn't reveal whether the email exists.
    password_ok = _verify_password(body.password, user.hashed_password if user else _DUMMY_HASH)
    if not user or not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return await _session(request, response, user.id)


def _magic_secret() -> str:
    return settings.secret_key


async def _create_magic_token(email: str) -> str:
    """Mint a single-use, short-lived magic-link token (jti tracked in Redis)."""
    now = datetime.now(UTC)
    jti = uuid.uuid4().hex
    payload = {
        "email": email,
        "type": "magic",
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(minutes=settings.magic_link_expire_minutes),
    }
    token = jwt.encode(payload, _magic_secret(), algorithm=ALGORITHM)
    await cache_set(f"magic:{jti}", email, ttl_seconds=settings.magic_link_expire_minutes * 60)
    return token


@router.post(
    "/magic-link/request",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(magic_link_rate_limit)],
)
async def magic_link_request(
    body: MagicLinkRequest, session: AsyncSession = Depends(get_db)
) -> dict:
    """Email a passwordless sign-in link. Always returns 202 (non-enumerating):
    the response is identical whether or not the email has an account."""
    s = get_settings()
    if s.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The demo is read-only",
        )
    email = body.email.lower()
    # Create the account lazily if it doesn't exist (magic link doubles as signup),
    # but only when registration is allowed (#153). Either way the response is
    # the same 202, so the endpoint never reveals which emails have accounts.
    existing = await session.execute(select(User).where(User.email == email))
    user = existing.scalar_one_or_none()
    if user is None:
        from app.services.email_verification import domain_signup_allowed
        from app.services.registration import can_register

        allowed, _ = await can_register(session)
        if not allowed or not await domain_signup_allowed(session, email):
            return {"status": "sent"}
        # On hosted, no account without consent (#181). Same 202 either way.
        hosted = get_settings().deployment_mode == "hosted"
        if hosted and not body.accept_terms:
            return {"status": "sent"}
        consented = hosted or body.accept_terms
        user = User(
            email=email,
            hashed_password=_hash_password(uuid.uuid4().hex),  # unusable random pw
            display_name=body.display_name,
            terms_accepted_at=datetime.now(UTC) if consented else None,
            terms_version=get_settings().terms_version if consented else None,
        )
        from app.services.billing import start_trial

        start_trial(user)
        session.add(user)
        await session.flush()
        from app.services.product_metrics import track

        await track(session, user.id, "signup", via="magic_link")

    token = await _create_magic_token(email)
    link = f"{settings.frontend_url.rstrip('/')}/auth/magic?token={token}"
    html = (
        '<div style="font-family:sans-serif;max-width:480px;margin:0 auto">'
        '<h1 style="font-size:1.3rem;color:#1d4ed8">Sign in to Weirling</h1>'
        f"<p>Click to sign in. This link expires in {settings.magic_link_expire_minutes} "
        "minutes and can be used once.</p>"
        f'<p><a href="{link}" style="display:inline-block;background:#1d4ed8;color:#fff;'
        'padding:10px 18px;border-radius:6px;text-decoration:none">Sign in</a></p>'
        '<p style="font-size:12px;color:#6b7280">If you didn\'t request this, ignore it.</p>'
        "</div>"
    )
    import hashlib

    from app.utils.email import send_email

    # One email per link, even if the send is retried (keyed on the link, not the token).
    await send_email(
        to=email,
        subject="Your Weirling sign-in link",
        html_body=html,
        idempotency_key="magic-link/" + hashlib.sha256(link.encode()).hexdigest(),
    )
    return {"status": "sent"}


@router.post("/magic-link/verify", response_model=Token)
async def magic_link_verify(
    body: MagicLinkVerify,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> Token:
    """Verify a magic-link token (single-use) and issue an access+refresh pair."""
    payload = _decode_token(body.token, secret=_magic_secret())
    if payload.get("type") != "magic":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid link")
    email = payload.get("email")
    jti = payload.get("jti")
    if not email or not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid link")
    # Single-use: the jti must still be present; consume it.
    stored = await cache_get(f"magic:{jti}")
    if stored != email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Link already used or expired"
        )
    await cache_delete(f"magic:{jti}")

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    # The link reached this inbox, so the address is proven (#175).
    from app.services.email_verification import mark_verified

    mark_verified(user)
    await session.flush()
    return await _session(request, response, user.id)


@router.post("/verify-email")
async def verify_email(body: EmailVerify, session: AsyncSession = Depends(get_db)) -> dict:
    """Confirm an address from the emailed link (#175). Idempotent."""
    from app.services.email_verification import verify_token

    user = await verify_token(session, body.token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This confirmation link is invalid or has expired.",
        )
    return {"verified": True}


@router.post(
    "/verify-email/resend",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_resend_rate_limit)],
)
async def resend_verification(user: User = Depends(get_current_user)) -> dict:
    from app.services.email_verification import send_verification_email
    from app.utils.cache import cache_set_nx

    if user.email_verified_at is not None:
        return {"status": "already_verified"}
    # One email per account per 5 minutes, whatever the IP.
    if await cache_set_nx(f"verify_resend:{user.id}", "1", ttl_seconds=300):
        await send_verification_email(user)
    return {"status": "sent"}


@router.post("/refresh", response_model=Token)
async def refresh(request: Request, response: Response, body: TokenRefresh | None = None) -> Token:
    """Rotate a refresh token: verify it, revoke it (single-use), and issue a
    fresh access+refresh pair. A revoked/rotated/unknown jti is rejected, so a
    leaked refresh token can be killed via logout and can't be replayed.

    Cookie-mode clients send no body; the token comes from the httpOnly
    `rp_refresh` cookie (#103)."""
    from app.api.session_cookies import REFRESH_COOKIE, clear_session_cookies

    raw = body.refresh_token if body and body.refresh_token else request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token"
        )
    try:
        payload = _decode_token(raw, secret=_refresh_secret())
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            )
        user_id = payload.get("sub")
        jti = payload.get("jti")
        if not user_id or not jti:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            )
        # The jti must still be in the allowlist (not rotated out or revoked).
        stored = await cache_get(_refresh_allowlist_key(jti))
        if stored != str(user_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked or expired"
            )
    except HTTPException:
        clear_session_cookies(response)
        raise
    await cache_delete(_refresh_allowlist_key(jti))  # rotate: single-use
    return await _session(request, response, uuid.UUID(user_id))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(request: Request, response: Response, body: TokenRefresh | None = None) -> None:
    """Revoke a refresh token server-side so it can no longer be rotated, and
    clear the browser session cookies."""
    from app.api.session_cookies import REFRESH_COOKIE, clear_session_cookies

    clear_session_cookies(response)
    raw = body.refresh_token if body and body.refresh_token else request.cookies.get(REFRESH_COOKIE)
    if not raw:
        return
    try:
        payload = jwt.decode(
            raw,
            _refresh_secret(),
            algorithms=[ALGORITHM],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return  # nothing to revoke on a malformed token
    jti = payload.get("jti")
    if jti:
        await cache_delete(_refresh_allowlist_key(jti))


@router.get("/registration")
async def registration_state(session: AsyncSession = Depends(get_db)) -> dict:
    """What the sign-up page should offer (#153)."""
    from app.services.registration import can_register, effective_mode

    mode = effective_mode()
    allowed, _ = await can_register(session)
    from app.services.registration import has_users

    s = get_settings()
    res: dict[str, Any] = {
        "mode": mode,
        "open": allowed and not s.demo_mode,
        "invite_required": mode == "invite" and not allowed,
        # No accounts yet: the next sign-up becomes the instance owner (#220).
        "first_account": not await has_users(session),
        "hosted": s.deployment_mode == "hosted",
    }
    if s.demo_mode:
        res["demo_mode"] = True
    return res


@router.get("/session")
async def session_state(
    request: Request, response: Response, session: AsyncSession = Depends(get_db)
) -> dict:
    """Is there a valid browser session? Returns the CSRF token for it (#103).

    Lets the web app restore its in-memory CSRF token after a reload without
    reading cookies (which also works when the API is on another subdomain).

    "Valid" means what every other endpoint means: a good access token for an
    account that still exists. A token for a deleted or re-seeded account
    used to count as signed in here while every API call returned 401, so the
    app bounced between /digest and /login forever (#159). Such cookies are
    cleared.
    """
    from app.api.session_cookies import (
        ACCESS_COOKIE,
        CSRF_COOKIE,
        REFRESH_COOKIE,
        clear_session_cookies,
    )

    access = request.cookies.get(ACCESS_COOKIE)
    valid = False
    if access:
        try:
            payload = _decode_token(access, secret=_access_secret())
            user_id = payload.get("sub") if payload.get("type") == "access" else None
            if user_id:
                valid = await session.get(User, uuid.UUID(user_id)) is not None
        except (HTTPException, ValueError):
            valid = False
    refreshable = bool(request.cookies.get(REFRESH_COOKIE))
    if access and not valid and refreshable and not await _refresh_user_exists(request, session):
        clear_session_cookies(response)
        refreshable = False
    elif access and not valid and not refreshable:
        clear_session_cookies(response)
    return {
        "authenticated": valid,
        "refreshable": refreshable,
        "csrf_token": request.cookies.get(CSRF_COOKIE) if valid else None,
    }


async def _refresh_user_exists(request: Request, session: AsyncSession) -> bool:
    """Whether the refresh cookie belongs to an account that still exists."""
    from app.api.session_cookies import REFRESH_COOKIE

    try:
        payload = _decode_token(request.cookies.get(REFRESH_COOKIE) or "", secret=_refresh_secret())
        user_id = payload.get("sub")
        return bool(user_id) and await session.get(User, uuid.UUID(user_id)) is not None
    except (HTTPException, ValueError):
        return False


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)
