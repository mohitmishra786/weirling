from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.content import ContentItem
from app.models.digest import Digest, DigestItem
from app.models.source import Source
from app.models.user import User
from app.utils.email import send_email
from app.utils.logging import get_logger
from app.utils.signed_links import action_url
from app.utils.unsubscribe import unsubscribe_url

logger = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"

_jinja_env: Environment | None = None


def _get_jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
    return _jinja_env


# Mirrors frontend/src/lib/signals.ts::SIGNAL_LABELS — keep the two in sync
# (audit 09-6). Used for the email digest's "why ranked" labels.
SIGNAL_LABELS = {
    "semantic": "matches your interests",
    "reading_depth": "matches your reading depth preference",
    "suggestion": "similar to content you've discovered",
    "explicit_feedback": "aligns with your ratings",
    "source_trust": "from a highly trusted source",
    "content_quality": "high quality content",
    "temporal_context": "matches your current focus",
    "novelty": "expands your usual reading",
}


def _top_signals(breakdown: dict, n: int = 2) -> list[str]:
    """Return the top-n signal labels with their relative contribution share.

    Contribution is each signal's share of the total unweighted signal strength
    — honest about which signals drove the ranking without exposing the
    per-user learned weights.
    """
    entries = [
        (k, v) for k, v in breakdown.items() if not k.startswith("_") and isinstance(v, int | float)
    ]
    total = sum(v for _, v in entries)
    sorted_signals = sorted(entries, key=lambda x: x[1], reverse=True)
    out: list[str] = []
    for k, v in sorted_signals[:n]:
        label = SIGNAL_LABELS.get(k, k)
        if total > 0:
            pct = round((v / total) * 100)
            out.append(f"{label} ({pct}%)")
        else:
            out.append(label)
    return out


async def _pending_dead_sources(user_id, session: AsyncSession) -> list[str]:
    result = await session.execute(
        select(Source).where(Source.user_id == user_id, Source.dead_notice_pending.is_(True))
    )
    return [source.name or source.url for source in result.scalars().all()]


async def _clear_dead_notices(user_id, session: AsyncSession) -> None:
    result = await session.execute(
        select(Source).where(Source.user_id == user_id, Source.dead_notice_pending.is_(True))
    )
    for source in result.scalars().all():
        source.dead_notice_pending = False


async def deliver_digest(digest: Digest, user: User, session: AsyncSession) -> bool:
    # Delivery is idempotent: a task retry (or a duplicate enqueue from the
    # build-recovery path) must not send a second email (CodeRabbit).
    if digest.delivered_at is not None:
        logger.info(f"Digest {digest.id} already delivered at {digest.delivered_at}; skipping")
        return True
    from app.services.email_verification import may_email

    if not may_email(user):
        # Readable in the app; no email until the address is confirmed (#175).
        logger.info(f"Digest {digest.id} not emailed: {user.id} has not verified their email")
        return False

    # Load digest items with content
    items_result = await session.execute(
        select(DigestItem).where(DigestItem.digest_id == digest.id).order_by(DigestItem.position)
    )
    digest_items = list(items_result.scalars().all())

    content_ids = [di.content_item_id for di in digest_items]
    content_result = await session.execute(
        select(ContentItem).where(ContentItem.id.in_(content_ids))
    )
    content_map = {c.id: c for c in content_result.scalars().all()}

    # Group by section
    sections: dict[str, list[dict]] = {}
    for di in digest_items:
        content = content_map.get(di.content_item_id)
        if not content:
            continue
        section = di.section
        if section not in sections:
            sections[section] = []
        sections[section].append(
            {
                "content": content,
                "prs_score": di.prs_score,
                "signal_breakdown": di.signal_breakdown,
                "why_ranked": _top_signals(di.signal_breakdown or {}),
                "is_discovery": section == "discovery",
                # Where a discovery item came from, shown in its badge (#141, #217).
                "discovered_via": (di.signal_breakdown or {}).get("discovered_via")
                or getattr(content, "discovered_via", None),
                "position": di.position,
                # Signed one-click actions (UX-04): title click-through goes
                # through the instance so reading telemetry can follow.
                "open_url": action_url(user.id, content.id, "open"),
                "up_url": action_url(user.id, content.id, "up"),
                "down_url": action_url(user.id, content.id, "down"),
                "save_url": action_url(user.id, content.id, "save"),
            }
        )

    # Working preferences + unsubscribe links (audit 08-5); the old template
    # hard-coded http://localhost:3000, which is broken in production.
    settings = get_settings()
    preferences_url = f"{settings.frontend_url.rstrip('/')}/preferences"
    unsub_url = unsubscribe_url(user.id)

    # Build email HTML
    env = _get_jinja_env()
    try:
        template = env.get_template("digest_email.html")
        html_body = template.render(
            user=user,
            digest=digest,
            sections=sections,
            generated_at=digest.generated_at.strftime("%B %d, %Y"),
            preferences_url=preferences_url,
            unsubscribe_url=unsub_url,
            physical_address=settings.email_physical_address,
            # Inbox preview line (#217): the top story's title.
            lead_title=next(
                (
                    row["content"].title
                    for name in ("lead", "more")
                    for row in sections.get(name, [])
                ),
                None,
            ),
            dead_sources=await _pending_dead_sources(user.id, session),
            sources_url=f"{settings.frontend_url.rstrip('/')}/sources",
            # 180px source shown at 28px: sharp on high-density screens.
            logo_url=f"{settings.frontend_url.rstrip('/')}/apple-touch-icon.png",
        )
    except Exception as e:
        logger.error(f"Failed to render digest email template: {e}")
        html_body = _fallback_html(user, sections)

    # Plain text fallback
    text_body = _build_text_body(user, sections, preferences_url, unsub_url)

    # RFC 8058 one-click unsubscribe + mailto fallback.
    extra_headers = {
        "List-Unsubscribe": f"<{unsub_url}>, <mailto:{settings.sender_email}?subject=unsubscribe>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }

    subject = f"Your Weirling Digest: {digest.generated_at.strftime('%B %d, %Y')}"
    success = await send_email(
        to=user.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        extra_headers=extra_headers,
    )

    if success:
        digest.delivered_at = datetime.now(UTC)
        from app.services.product_metrics import track

        await track(session, user.id, "digest_sent", method="email")
        await _clear_dead_notices(user.id, session)
        await session.flush()

    return success


def _fallback_html(user: User, sections: dict) -> str:
    # Escape every interpolated value: titles/summaries/URLs come from ingested
    # third-party content and would otherwise inject HTML into the email (XSS,
    # audit 06-7). The Jinja template autoescapes; this hand-built fallback must
    # do so explicitly.
    parts = ["<h1>Your Weirling Digest</h1>"]
    for section_name, items in sections.items():
        parts.append(f"<h2>{escape(section_name.replace('_', ' ').title())}</h2>")
        for item_data in items:
            content = item_data["content"]
            parts.append(
                f'<div><h3><a href="{escape(content.url, quote=True)}">'
                f"{escape(content.title)}</a></h3>"
            )
            if content.summary_brief:
                label = (
                    "<small>AI summary:</small> "
                    if getattr(content, "summary_source", None) == "llm"
                    else ""
                )
                parts.append(f"<p>{label}{escape(content.summary_brief)}</p>")
            parts.append("</div>")
    return "".join(parts)


def _build_text_body(
    user: User, sections: dict, preferences_url: str = "", unsubscribe: str = ""
) -> str:
    lines = ["Your Weirling Digest\n", "=" * 40]
    for section_name, items in sections.items():
        lines.append(f"\n{section_name.upper().replace('_', ' ')}\n" + "-" * 30)
        for item_data in items:
            content = item_data["content"]
            lines.append(f"\n{content.title}")
            if content.summary_brief:
                label = "AI summary: " if getattr(content, "summary_source", None) == "llm" else ""
                lines.append(f"{label}{content.summary_brief}")
            lines.append(f"Read: {item_data['open_url']}")
            lines.append(
                f"Rate: Useful {item_data['up_url']}  ·  Not for me {item_data['down_url']}  ·  "
                f"Save: {item_data['save_url']}"
            )
    lines.append("\n" + "-" * 40)
    if preferences_url:
        lines.append(f"Manage preferences: {preferences_url}")
    if unsubscribe:
        lines.append(f"Unsubscribe: {unsubscribe}")
    return "\n".join(lines)
