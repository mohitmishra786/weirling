from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.content import ContentItem, UserContentInteraction
from app.models.digest import Digest, DigestFeedbackPrompt, DigestItem
from app.models.user import User
from app.schemas.content import ContentItemRead
from app.schemas.digest import DigestItemRead, DigestRead
from app.utils.logging import get_logger
from app.utils.signed_links import ACTIONS, verify_action
from app.utils.unsubscribe import verify_unsubscribe_token

router = APIRouter(prefix="/digest", tags=["digest"])
logger = get_logger(__name__)


async def _apply_unsubscribe(uid: str, token: str, session: AsyncSession) -> bool:
    """Verify the signed token and switch the user to in-app-only (no email).
    Returns True if a user was updated."""
    try:
        user_uuid = uuid.UUID(uid)
    except (ValueError, TypeError):
        return False
    if not verify_unsubscribe_token(uid, token):
        return False
    result = await session.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()
    if user is None:
        return False
    user.digest_frequency = "in_app_only"
    await session.flush()
    # Log the parsed UUID (validated above), never the raw request string.
    logger.info(f"User {user_uuid} unsubscribed from digest emails via email link")
    return True


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_get(
    uid: str = Query(...),
    token: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """One-click unsubscribe target for the digest email footer link."""
    ok = await _apply_unsubscribe(uid, token, session)
    if not ok:
        return HTMLResponse("<p>This unsubscribe link is invalid or expired.</p>", status_code=400)
    return HTMLResponse(
        "<p>You've been unsubscribed from Weirling digest emails. "
        "You can re-enable them anytime in your preferences.</p>"
    )


@router.post("/unsubscribe", status_code=status.HTTP_200_OK)
async def unsubscribe_post(
    uid: str = Query(...),
    token: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """RFC 8058 List-Unsubscribe-Post=One-Click endpoint (mail-client POST)."""
    ok = await _apply_unsubscribe(uid, token, session)
    if not ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
    return {"status": "unsubscribed"}


async def _mark_opened(digest: Digest, session: AsyncSession, *, via: str = "app") -> None:
    """Digest.opened was never set anywhere, so win-back emails (#238) and the
    analytics treated every reader as inactive (#131)."""
    if digest.opened:
        return
    digest.opened = True
    from app.services.product_metrics import track

    await track(session, digest.user_id, "digest_opened", via=via)


async def _mark_digest_opened_from_email(
    user: User, item_id: uuid.UUID, session: AsyncSession
) -> None:
    """A click on an item in the digest email opens that digest."""
    digest = (
        await session.execute(
            select(Digest)
            .join(DigestItem, DigestItem.digest_id == Digest.id)
            .where(Digest.user_id == user.id, DigestItem.content_item_id == item_id)
            .order_by(Digest.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if digest is not None:
        await _mark_opened(digest, session, via="email")


async def _record_email_action(
    user: User, item_id: uuid.UUID, action: str, session: AsyncSession
) -> UserContentInteraction:
    """Upsert the interaction row for a one-click email action (UX-04)."""
    result = await session.execute(
        select(UserContentInteraction).where(
            UserContentInteraction.user_id == user.id,
            UserContentInteraction.content_item_id == item_id,
        )
    )
    interaction = result.scalar_one_or_none()
    from app.services import product_metrics

    before = product_metrics.snapshot(interaction)
    if interaction is None:
        interaction = UserContentInteraction(user_id=user.id, content_item_id=item_id)
        session.add(interaction)

    if action == "open":
        if not interaction.opened_at:
            interaction.opened_at = datetime.now(UTC)
    elif action == "up":
        interaction.explicit_rating = 1
        interaction.opened_at = interaction.opened_at or datetime.now(UTC)
    elif action == "down":
        interaction.explicit_rating = -1
    elif action == "save":
        interaction.saved = True
        if interaction.saved_at is None:
            interaction.saved_at = datetime.now(UTC)

    await session.flush()
    await product_metrics.track_interaction(session, before, interaction)
    await _mark_digest_opened_from_email(user, item_id, session)
    return interaction


def _confirmation_page(
    action: str, user_id: uuid.UUID, item_id: uuid.UUID, exp: int, sig: str
) -> HTMLResponse:
    """A one-tap confirmation page for mutating email actions.

    Corporate mail scanners (Safe Links, Mimecast) prefetch every link in an
    email with plain GETs and no JS. Mutating actions therefore render this
    page on GET instead of writing state: a scanner sees inert HTML, a real
    browser auto-submits the form (and a no-JS user can press the button),
    which POSTs back to the same signed URL and records the action.

    Every interpolated value is a server-validated type (allowlisted action,
    parsed UUIDs, int expiry, HMAC-matched hex signature) and is additionally
    HTML-escaped at the sink (CodeQL py/reflective-xss).
    """
    import html

    labels = {
        "up": "Useful",
        "down": "Not for me",
        "save": "Save",
        "open": "Open the article",
    }
    label = html.escape(labels.get(action, "Confirm"))
    prompt = (
        "Opening the article…" if action == "open" else "Confirm your feedback for this article:"
    )
    target = html.escape(
        f"/api/v1/digest/e/{action}/{item_id}?uid={user_id}&exp={exp}&sig={sig}", quote=True
    )
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weirling: {label}</title>
<style>
 body {{ font-family: system-ui, sans-serif; display: flex; min-height: 100vh;
        align-items: center; justify-content: center; background: #f8fafc; margin: 0; }}
 .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
         padding: 32px 40px; text-align: center; }}
 button {{ font-size: 16px; padding: 10px 24px; border-radius: 8px; border: 1px solid #1d4ed8;
          background: #1d4ed8; color: #fff; cursor: pointer; }}
</style></head>
<body><div class="card">
<p>{prompt}</p>
<form method="post" action="{target}" id="confirm">
  <button type="submit">{label}</button>
</form>
</div>
<script>document.getElementById("confirm").submit();</script>
</body></html>"""
    )


MUTATING_ACTIONS = {"up", "down", "save"}
# Recorded only on the POST from the interstitial page, never on a GET a mail
# scanner can make. "open" too (#257): a prefetched link must not count as read.
POST_ONLY_ACTIONS = MUTATING_ACTIONS | {"open"}


async def _verify_email_action(
    action: str,
    item_id: uuid.UUID,
    uid: str,
    exp: int,
    sig: str,
    session: AsyncSession,
) -> tuple[User, ContentItem]:
    """Shared validation for the GET and POST email-action handlers."""
    # Allowlist the path parameter before anything else: every later use of
    # `action` (branching, logging) is then structurally bounded (CodeQL
    # py/log-injection).
    if action not in ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown action")

    try:
        user_uuid = uuid.UUID(uid)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bad uid")

    if not verify_action(uid, item_id, action, exp, sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link"
        )

    user_result = await session.execute(select(User).where(User.id == user_uuid))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown user")

    content_result = await session.execute(select(ContentItem).where(ContentItem.id == item_id))
    content = content_result.scalar_one_or_none()
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")
    return user, content


def _reader_redirect(content: ContentItem) -> RedirectResponse:
    # The redirect target is built from the operator-configured frontend URL
    # and the database row's canonical id — never from a raw request string
    # (CodeQL py/url-redirect).
    reader_url = f"{get_settings().frontend_url.rstrip('/')}/read/{content.id}"
    return RedirectResponse(url=reader_url, status_code=303)


# Constant map for logging: the value printed comes from this table, never
# from the request string (CodeQL py/log-injection).
_LOG_ACTIONS = {name: name for name in ACTIONS}


def _log_email_action(action: str, user_id: uuid.UUID, item_id: uuid.UUID) -> None:
    logger.info(
        "Email action %s recorded for user %s item %s",
        _LOG_ACTIONS[action],
        user_id,
        item_id,
    )


@router.get("/e/{action}/{item_id}")
async def email_action_link(
    action: str,
    item_id: uuid.UUID,
    uid: str = Query(...),
    exp: int = Query(...),
    sig: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Signed one-click email actions: open / thumbs up / thumbs down / save.

    No login: the HMAC over (user, item, action, expiry) is the authority.

    GET never records anything: mail scanners (Safe Links, Mimecast) fetch
    every link without running scripts. It renders a page whose script POSTs
    back to the same signed URL, so a real browser records the action and
    lands in the reader; a scanner sees inert HTML. That covers 'open' too
    (#257), so prefetches don't count as reads.
    """
    user, content = await _verify_email_action(action, item_id, uid, exp, sig, session)
    return _confirmation_page(action, user.id, content.id, exp, sig)


@router.post("/e/{action}/{item_id}")
async def email_action_submit(
    action: str,
    item_id: uuid.UUID,
    uid: str = Query(...),
    exp: int = Query(...),
    sig: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Record an email action (form POST from the interstitial page)."""
    if action not in POST_ONLY_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown action")
    user, content = await _verify_email_action(action, item_id, uid, exp, sig, session)

    interaction = await _record_email_action(user, content.id, action, session)
    if action not in MUTATING_ACTIONS:  # open: nothing for the interest graph
        _log_email_action(action, user.id, content.id)
        return _reader_redirect(content)
    # Commit before dispatching so the worker's own session can see the row
    # (get_db commits only after the response is returned — the task could
    # otherwise run first and find nothing).
    await session.commit()
    from app.workers.tasks.update_interest_graph import update_interest_graph_for_interaction

    update_interest_graph_for_interaction.delay(str(interaction.id))
    _log_email_action(action, user.id, content.id)
    return _reader_redirect(content)


def _topic_confirmation_page(action: str, uid: str, topic: str, exp: int, sig: str) -> HTMLResponse:
    """GET for a weekly-email topic link (#184): inert for mail scanners, a
    real browser auto-submits the POST (same pattern as _confirmation_page)."""
    import html
    from urllib.parse import urlencode

    label = "Show less of" if action == "less" else "Keep"
    query = urlencode({"uid": uid, "topic": topic, "exp": exp, "sig": sig})
    target = html.escape(f"/api/v1/digest/t/{action}?{query}", quote=True)
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Weirling</title></head>
<body style="font-family:system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center">
<form method="post" action="{target}" id="confirm">
<p>{html.escape(label)} <strong>{html.escape(topic)}</strong>?</p><button type="submit">Confirm</button></form>
<script>document.getElementById("confirm").submit();</script></body></html>"""
    )


@router.get("/t/{action}")
async def topic_action_link(
    action: str,
    uid: str = Query(...),
    topic: str = Query(..., max_length=200),
    exp: int = Query(...),
    sig: str = Query(...),
) -> Response:
    from app.utils.signed_links import verify_topic_action

    if not verify_topic_action(uid, topic, action, exp, sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link"
        )
    return _topic_confirmation_page(action, uid, topic, exp, sig)


@router.post("/t/{action}")
async def topic_action_submit(
    action: str,
    uid: str = Query(...),
    topic: str = Query(..., max_length=200),
    exp: int = Query(...),
    sig: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Weekly-email undo (#184): 'less' suppresses the topic, 'more' boosts it."""
    from app.models.interest_graph import InterestNode
    from app.utils.signed_links import verify_topic_action

    if not verify_topic_action(uid, topic, action, exp, sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link"
        )
    node = (
        await session.execute(
            select(InterestNode).where(
                InterestNode.user_id == uuid.UUID(uid), InterestNode.topic_label == topic
            )
        )
    ).scalar_one_or_none()
    if node is not None:
        delta = -0.3 if action == "less" else 0.3
        node.weight = max(0.0, min(1.0, node.weight + delta))
        if action == "more":
            node.suppressed_until = None
        await session.commit()
    return RedirectResponse(
        url=f"{get_settings().frontend_url.rstrip('/')}/interests", status_code=303
    )


@router.get("/u/{action}")
async def user_action_link(
    action: str, uid: str = Query(...), exp: int = Query(...), sig: str = Query(...)
) -> Response:
    """Win-back 'Switch to weekly' (#238): inert confirmation on GET."""
    import html

    from app.utils.signed_links import verify_user_action

    if not verify_user_action(uid, action, exp, sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link"
        )
    target = html.escape(
        f"/api/v1/digest/u/{action}?uid={uuid.UUID(uid)}&exp={int(exp)}&sig={sig}", quote=True
    )
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Weirling</title></head>
<body style="font-family:system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center">
<form method="post" action="{target}" id="confirm"><p>Switch to one digest a week?</p>
<button type="submit">Switch to weekly</button></form>
<script>document.getElementById("confirm").submit();</script></body></html>"""
    )


@router.post("/u/{action}")
async def user_action_submit(
    action: str,
    uid: str = Query(...),
    exp: int = Query(...),
    sig: str = Query(...),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    from app.utils.signed_links import verify_user_action

    if not verify_user_action(uid, action, exp, sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link"
        )
    user = await session.get(User, uuid.UUID(uid))
    if user is not None and action == "weekly":
        user.digest_frequency = "weekly"
        await session.commit()
    return RedirectResponse(
        url=f"{get_settings().frontend_url.rstrip('/')}/preferences", status_code=303
    )


async def _apply_weekly_unsubscribe(uid: str, token: str, session: AsyncSession) -> bool:
    from app.utils.unsubscribe import verify_weekly_unsubscribe_token

    try:
        user_uuid = uuid.UUID(uid)
    except (ValueError, TypeError):
        return False
    if not verify_weekly_unsubscribe_token(uid, token):
        return False
    user = await session.get(User, user_uuid)
    if user is None:
        return False
    user.weekly_email = False
    await session.flush()
    return True


@router.get("/unsubscribe-weekly", response_class=HTMLResponse)
async def unsubscribe_weekly_get(
    uid: str = Query(...), token: str = Query(...), session: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    if not await _apply_weekly_unsubscribe(uid, token, session):
        return HTMLResponse("<p>This link is invalid.</p>", status_code=400)
    return HTMLResponse("<p>The weekly summary is off. Your digest emails are unchanged.</p>")


@router.post("/unsubscribe-weekly")
async def unsubscribe_weekly_post(
    uid: str = Query(...), token: str = Query(...), session: AsyncSession = Depends(get_db)
) -> dict:
    """RFC 8058 one-click target for the weekly email."""
    if not await _apply_weekly_unsubscribe(uid, token, session):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
    return {"status": "unsubscribed"}


@router.get("/vs-newest")
async def digest_vs_newest(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Your digest vs newest-first over the last 7 days (#237). Only the
    signed-in reader's own digests and reads; nothing from other users."""
    from app.services.ranking.interleaving import user_comparison

    return await user_comparison(session, current_user.id)


@router.get("/latest", response_model=DigestRead)
async def get_latest_digest(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DigestRead:
    result = await session.execute(
        select(Digest)
        .where(Digest.user_id == current_user.id)
        .order_by(Digest.generated_at.desc())
        .limit(1)
    )
    digest = result.scalar_one_or_none()
    if not digest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No digest found")

    await _mark_viewed(digest, session)
    return await _build_digest_read(digest, session)


@router.get("/history", response_model=list[DigestRead])
async def get_digest_history(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DigestRead]:
    result = await session.execute(
        select(Digest)
        .where(Digest.user_id == current_user.id)
        .order_by(Digest.generated_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    digests = list(result.scalars().all())
    return [await _build_digest_read(d, session) for d in digests]


@router.get("/emerging", response_model=list[dict])
async def get_emerging_topics(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """Topics with a burst of distinct-source coverage in the last 72 h vs
    the trailing 28-day baseline (UX-10). Powers the "Emerging" card.

    The reader's own feeds only; per topic: distinct sources in the recent
    window compared with the four trailing 7-day windows (z >= 2). Empty
    until those feeds have BASELINE_DAYS of history.
    """

    from sqlalchemy import func

    from app.services.digest.emerging import BASELINE_DAYS, detect_emerging
    from app.services.feed_identity import (
        equivalent_source_ids,
        from_user_feeds,
        user_feed_ids,
    )

    now = datetime.now(UTC)

    # The reader's own feeds only: other readers' sources made "agent" topics
    # "emerge" for someone who follows kernel blogs.
    source_ids = await equivalent_source_ids(session, current_user.id)
    feed_ids = await user_feed_ids(session, current_user.id)
    if not source_ids and not feed_ids:
        return []
    mine = from_user_feeds(source_ids, feed_ids)

    # A burst needs a baseline: with less history than that, every topic is
    # "new this month" and the card would list noise.
    oldest = (
        await session.execute(select(func.min(ContentItem.fetched_at)).where(mine))
    ).scalar_one_or_none()
    if oldest is None or now - oldest < timedelta(days=BASELINE_DAYS):
        return []

    def distinct_sources_per_topic(start, end):
        topic = func.jsonb_array_elements_text(ContentItem.topic_clusters).label("topic")
        return (
            select(topic, func.count(func.distinct(ContentItem.source_id)).label("n"))
            .select_from(ContentItem)
            .where(
                ContentItem.fetched_at >= start,
                ContentItem.fetched_at < end,
                ContentItem.source_id.isnot(None),
                mine,
            )
            .group_by(topic)
        )

    recent_result = await session.execute(
        distinct_sources_per_topic(now - timedelta(hours=72), now)
    )
    recent = {row[0]: int(row[1]) for row in recent_result.all()}

    # Four trailing 7-day windows that END where the recent window begins —
    # overlapping the recent window would absorb the burst into the baseline
    # and mute the z-score.
    base = now - timedelta(hours=72)
    history: dict[str, list[float]] = {}
    for week in range(4):
        end = base - timedelta(days=week * 7)
        start = end - timedelta(days=7)
        rows = await session.execute(distinct_sources_per_topic(start, end))
        for row in rows.fetchall():
            history.setdefault(str(row[0]), []).append(float(cast(int, row[1])))
    # Oldest-first, one slot per 7-day window even when a week had none.
    for topic in history:
        while len(history[topic]) < 4:
            history[topic].insert(0, 0.0)

    found = detect_emerging(recent, history)
    return [
        {
            "topic": e.topic,
            "recent_sources": e.recent_sources,
            "baseline_mean": round(e.baseline_mean, 2),
            "z": round(min(e.z, 99.0), 2),
        }
        for e in found[:5]
    ]


@router.post("/generate", response_model=dict)
async def generate_digest(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    from app.services import entitlements

    # Hosted free tier: once per plan window. Self-host and paid plans: only a
    # short guard against double-clicks (#109).
    window = entitlements.digest_regeneration_window(current_user)
    recent = await session.execute(
        select(Digest.id)
        .where(
            Digest.user_id == current_user.id,
            Digest.generated_at >= datetime.now(UTC) - window,
        )
        .limit(1)
    )
    if recent.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=entitlements.digest_regeneration_message(current_user),
        )

    # Hosted: manual builds per day by plan (#172). Scheduled digests don't count.
    if not await entitlements.take(current_user, entitlements.DIGEST_BUILDS):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=entitlements.daily_quota_message(current_user, entitlements.DIGEST_BUILDS),
        )

    from app.workers.tasks.build_digest import build_digest_for_user

    build_digest_for_user.delay(str(current_user.id))
    return {"status": "queued", "message": "Digest generation started"}


@router.get("/{digest_id}", response_model=DigestRead)
async def get_digest(
    digest_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DigestRead:
    result = await session.execute(
        select(Digest).where(Digest.id == digest_id, Digest.user_id == current_user.id)
    )
    digest = result.scalar_one_or_none()
    if not digest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Digest not found")
    await _mark_viewed(digest, session)
    return await _build_digest_read(digest, session)


class FeedbackPromptRead(BaseModel):
    id: uuid.UUID
    digest_id: uuid.UUID
    content_item_id: uuid.UUID | None
    prompt_text: str
    prompt_type: str
    answered: bool
    dismissed: bool
    answer: str | None

    model_config = {"from_attributes": True}


@router.get("/{digest_id}/prompts", response_model=list[FeedbackPromptRead])
async def get_digest_prompts(
    digest_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[FeedbackPromptRead]:
    """Return feedback prompts for an early-user digest."""
    # Verify ownership
    digest_result = await session.execute(
        select(Digest).where(Digest.id == digest_id, Digest.user_id == current_user.id)
    )
    if not digest_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Digest not found")

    prompts_result = await session.execute(
        select(DigestFeedbackPrompt).where(DigestFeedbackPrompt.digest_id == digest_id)
    )
    return [FeedbackPromptRead.model_validate(p) for p in prompts_result.scalars().all()]


@router.post("/{digest_id}/prompts/{prompt_id}/answer", response_model=FeedbackPromptRead)
async def answer_digest_prompt(
    digest_id: uuid.UUID,
    prompt_id: uuid.UUID,
    body: dict,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FeedbackPromptRead:
    """Record a user's answer to a feedback prompt."""
    digest_result = await session.execute(
        select(Digest).where(Digest.id == digest_id, Digest.user_id == current_user.id)
    )
    if not digest_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Digest not found")

    prompt_result = await session.execute(
        select(DigestFeedbackPrompt).where(
            DigestFeedbackPrompt.id == prompt_id,
            DigestFeedbackPrompt.digest_id == digest_id,
        )
    )
    prompt = prompt_result.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")

    answer = str(body.get("answer", "")).strip()
    if not answer:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="answer is required"
        )

    prompt.answer = answer
    prompt.answered = True
    await session.flush()
    return FeedbackPromptRead.model_validate(prompt)


@router.post("/{digest_id}/prompts/{prompt_id}/dismiss", response_model=FeedbackPromptRead)
async def dismiss_digest_prompt(
    digest_id: uuid.UUID,
    prompt_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FeedbackPromptRead:
    """Dismiss an early-feedback prompt without answering (UX-07)."""
    digest_result = await session.execute(
        select(Digest).where(Digest.id == digest_id, Digest.user_id == current_user.id)
    )
    if not digest_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Digest not found")

    prompt_result = await session.execute(
        select(DigestFeedbackPrompt).where(
            DigestFeedbackPrompt.id == prompt_id,
            DigestFeedbackPrompt.digest_id == digest_id,
        )
    )
    prompt = prompt_result.scalar_one_or_none()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")

    prompt.dismissed = True
    await session.flush()
    return FeedbackPromptRead.model_validate(prompt)


async def _mark_viewed(digest: Digest, session: AsyncSession) -> None:
    """The owner opened this digest: its items were shown (#154).

    The learner only treats a skipped item as a negative once the digest was
    actually seen; without this flag every skip looked "unseen".
    """
    from sqlalchemy import update

    from app.models.digest import DigestImpression

    await _mark_opened(digest, session)

    await session.execute(
        update(DigestImpression)
        .where(
            DigestImpression.digest_id == digest.id,
            DigestImpression.user_id == digest.user_id,
            DigestImpression.viewed.is_(False),
        )
        .values(viewed=True)
        .execution_options(synchronize_session=False)
    )


async def _build_digest_read(digest: Digest, session: AsyncSession) -> DigestRead:
    items_result = await session.execute(
        select(DigestItem).where(DigestItem.digest_id == digest.id).order_by(DigestItem.position)
    )
    digest_items = list(items_result.scalars().all())

    content_ids = [di.content_item_id for di in digest_items]
    content_map: dict = {}
    if content_ids:
        from app.services.takedown import not_blocked_filter

        content_result = await session.execute(
            select(ContentItem).where(ContentItem.id.in_(content_ids), not_blocked_filter())
        )
        content_map = {c.id: c for c in content_result.scalars().all()}

    item_reads = []
    for di in digest_items:
        content = content_map.get(di.content_item_id)
        if not content:
            continue
        item_reads.append(
            DigestItemRead(
                id=di.id,
                digest_id=di.digest_id,
                content_item_id=di.content_item_id,
                position=di.position,
                section=di.section,
                prs_score=di.prs_score,
                signal_breakdown=di.signal_breakdown or {},
                content=ContentItemRead.model_validate(content),
            )
        )

    return DigestRead(
        id=digest.id,
        user_id=digest.user_id,
        generated_at=digest.generated_at,
        delivered_at=digest.delivered_at,
        delivery_method=digest.delivery_method,
        section_counts=digest.section_counts or {},
        opened=digest.opened,
        total_items=digest.total_items,
        items=item_reads,
        created_at=digest.created_at,
        ask_top5=await _ask_top5(digest, session),
    )


async def _top5_answered(digest_id: uuid.UUID, user_id: uuid.UUID, session: AsyncSession) -> bool:
    from app.models.product_event import ProductEvent

    found = await session.execute(
        select(ProductEvent.id).where(
            ProductEvent.user_id == user_id,
            ProductEvent.event == "top5_rating",
            ProductEvent.props["digest_id"].astext == str(digest_id),
        )
    )
    return found.first() is not None


async def _ask_top5(digest: Digest, session: AsyncSession) -> bool:
    from app.services import product_metrics

    if not product_metrics.enabled() or (digest.total_items or 0) < 5:
        return False
    return not await _top5_answered(digest.id, digest.user_id, session)


class Top5Answer(BaseModel):
    wanted: int = Field(ge=0, le=5)


@router.post("/{digest_id}/top5", response_model=dict)
async def answer_top5(
    digest_id: uuid.UUID,
    body: Top5Answer,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Top-5 precision, self-reported (#131): one answer per digest."""
    from app.services import product_metrics

    digest = await session.get(Digest, digest_id)
    if digest is None or digest.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Digest not found")
    if not product_metrics.enabled():
        return {"status": "off"}
    if await _top5_answered(digest_id, current_user.id, session):
        return {"status": "already_answered"}
    await product_metrics.track(
        session, current_user.id, "top5_rating", wanted=body.wanted, digest_id=str(digest_id)
    )
    return {"status": "recorded"}
