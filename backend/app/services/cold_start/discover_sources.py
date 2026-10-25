"""Discover-source engine (EC-06): candidate mining + accept/dismiss loop."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentItem, UserContentInteraction
from app.models.source import Source
from app.models.suggestion import SourceSuggestion
from app.models.user import User
from app.utils.logging import get_logger

logger = get_logger(__name__)

MAX_PENDING = 5


async def refresh_suggestions(user: User, session: AsyncSession) -> int:
    """Insert new pending suggestions; returns the number added.

    Sources of discovery items the user fully read come first (the spec's
    suggestion loop), then starter-pack feeds matching top interest clusters.
    URLs with ANY prior row (pending/accepted/dismissed) never re-enter —
    dismissals are permanent. Inserts are ON CONFLICT DO NOTHING so two
    concurrent refreshes cannot trip the unique constraint.
    """
    existing = await session.execute(
        select(SourceSuggestion.url).where(SourceSuggestion.user_id == user.id)
    )
    known_urls = {row[0] for row in existing.fetchall()}

    followed = await session.execute(select(Source.url).where(Source.user_id == user.id))
    known_urls |= {row[0] for row in followed.fetchall()}

    candidates: list[tuple[str, str, str]] = []  # (url, name, reason)

    # (a) Unfollowed sources whose discovery items were fully read.
    #     Filtering happens IN SQL against known URLs, and the LIMIT applies
    #     after distinct-source selection (CodeRabbit: no premature limit,
    #     no N+1 source lookups).
    discovery_sources = await session.execute(
        select(Source.url, Source.feed_url, Source.name, ContentItem.title)
        .join(ContentItem, ContentItem.source_id == Source.id)
        .join(
            UserContentInteraction,
            (UserContentInteraction.content_item_id == ContentItem.id)
            & (UserContentInteraction.user_id == user.id),
        )
        .where(
            ContentItem.origin == "discovery",
            UserContentInteraction.read_completion_pct >= 0.85,
            Source.user_id != user.id,
        )
        .distinct(Source.id)
        .limit(10)
    )
    for url, feed_url, name, title in discovery_sources.fetchall():
        # Prefer the actual feed URL: Source.url can be a human page while
        # feed_url is what ingestion should poll (CodeRabbit).
        subscribe_url = feed_url or url
        if subscribe_url in known_urls or url in known_urls:
            continue
        candidates.append((subscribe_url, name or subscribe_url, f"You fully read {title[:60]}"))
        known_urls.add(subscribe_url)

    # (b) Starter-pack feeds matching the user's top interest clusters.
    from app.models.interest_graph import InterestNode

    nodes = await session.execute(
        select(InterestNode.topic_label)
        .where(InterestNode.user_id == user.id)
        .order_by(InterestNode.weight.desc())
        .limit(5)
    )
    top_topics = [row[0].lower() for row in nodes.fetchall()]

    from app.services.cold_start.pack_picker import suggest_packs
    from app.services.cold_start.starter_packs import list_starter_packs

    # The LLM's pick for the top interests (taxonomy without a model). The
    # old pack-id word match let "design" in "system design" pull in UI design.
    by_id = {pack.id: pack for pack in list_starter_packs()}
    if top_topics:
        chosen = [by_id[i] for i in await suggest_packs(top_topics) if i in by_id]
    else:
        chosen = list(by_id.values())
    for pack in chosen:
        for feed in pack.feeds:
            if feed["url"] not in known_urls and len(candidates) < 30:
                candidates.append(
                    (feed["url"], feed["name"], f"Matches your {pack.title} interests")
                )
                known_urls.add(feed["url"])
        if len(candidates) >= 30:
            break

    pending_count = (
        await session.execute(
            select(SourceSuggestion.id).where(
                SourceSuggestion.user_id == user.id, SourceSuggestion.status == "pending"
            )
        )
    ).fetchall()
    room = max(0, MAX_PENDING - len(pending_count))

    added = 0
    for url, name, reason in candidates[:room]:
        stmt = (
            pg_insert(SourceSuggestion)
            .values(user_id=user.id, url=url, name=name[:200], reason=reason[:200])
            .on_conflict_do_nothing(constraint="uq_source_suggestions_user_url")
        )
        result = await session.execute(stmt)
        added += int(getattr(result, "rowcount", 0) or 0)
    if added:
        await session.flush()
        logger.info(f"Added {added} source suggestions for user {user.id}")
    return added
