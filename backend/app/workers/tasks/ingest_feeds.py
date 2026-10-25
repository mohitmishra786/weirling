from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta

from app.utils.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.ingest_feeds.ingest_all_feeds", bind=True, max_retries=3)
def ingest_all_feeds(self) -> dict:
    return asyncio.run(_ingest_all_feeds_async())


async def _insert_items(session, rows: list[dict]) -> list[uuid.UUID]:
    """Insert content rows, skipping any that already exist; return new ids.

    `content_items` has unique constraints (URL, and source+guid). Another
    user's subscription, an extension save, an import, or an aggregator that
    links the same article can all have stored the URL already. A plain
    INSERT would raise on flush and abort the whole transaction, so conflicts
    are skipped in the database instead (#100).
    """
    from sqlalchemy.dialects.postgresql import insert

    from app.models.content import ContentItem
    from app.services.takedown import is_url_blocked

    new_ids: list[uuid.UUID] = []
    for row in rows:
        if await is_url_blocked(session, row.get("url", "")):
            continue
        result = await session.execute(
            insert(ContentItem).values(**row).on_conflict_do_nothing().returning(ContentItem.id)
        )
        new_id = result.scalar_one_or_none()
        if new_id is not None:
            new_ids.append(new_id)
    return new_ids


def _enqueue_embeddings(item_ids: list[uuid.UUID]) -> None:
    """Best effort per item: a broker hiccup must not stop the run. Committed
    items whose task was lost are picked up by repair_missing_embeddings."""
    from app.workers.tasks.compute_embeddings import compute_embedding_for_item

    for item_id in item_ids:
        try:
            compute_embedding_for_item.delay(str(item_id))
        except Exception as e:
            logger.warning(f"Could not enqueue embedding for {item_id}: {e}")


async def _safe_rollback(session, what: str) -> None:
    """Roll back without letting a dead connection end the whole run."""
    try:
        await session.rollback()
    except Exception as e:
        logger.error(f"Rollback failed for {what}: {e}")


@celery_app.task(name="app.workers.tasks.ingest_feeds.ingest_user_sources", bind=True)
def ingest_user_sources(self, user_id: str) -> dict:
    """Fetch one user's sources now, regardless of schedule (first digest, #102)."""
    return asyncio.run(_ingest_all_feeds_async(user_id=uuid.UUID(user_id)))


async def _ingest_all_feeds_async(user_id: uuid.UUID | None = None) -> dict:
    """Poll due feeds once for all subscribers (#162), plus private newsletter
    sources; with `user_id`, everything that user follows, regardless of schedule."""
    from sqlalchemy import exists, or_, select

    from app.database import AsyncSessionLocal
    from app.models.feed import Feed
    from app.models.source import Source
    from app.services.ingestion.feeds import sync_feeds

    now = datetime.now(UTC)
    rng = random.Random()

    async with AsyncSessionLocal() as session:
        await sync_feeds(session, user_id=user_id)
        await session.commit()

        followed = exists().where(Source.feed_id == Feed.id, Source.is_active == True)
        feed_query = select(Feed.id).where(Feed.is_active == True, followed)
        newsletter_query = select(Source.id).where(
            Source.is_active == True, Source.source_type == "newsletter"
        )
        if user_id is not None:
            mine = exists().where(
                Source.feed_id == Feed.id, Source.user_id == user_id, Source.is_active == True
            )
            feed_query = feed_query.where(mine)
            newsletter_query = newsletter_query.where(Source.user_id == user_id)
        else:
            feed_query = feed_query.where(
                or_(Feed.next_poll_at.is_(None), Feed.next_poll_at <= now)
            )
            pro_first = _pro_followed(now)
            if pro_first is not None:  # hosted only (#246)
                feed_query = feed_query.order_by(pro_first.desc())
            feed_query = feed_query.order_by(Feed.next_poll_at.asc().nulls_first())
            newsletter_query = newsletter_query.where(
                or_(Source.next_poll_at.is_(None), Source.next_poll_at <= now)
            )
        feed_ids = [row[0] for row in (await session.execute(feed_query)).all()]
        newsletter_ids = [row[0] for row in (await session.execute(newsletter_query)).all()]
    logger.info(f"Ingesting {len(feed_ids)} feeds and {len(newsletter_ids)} newsletter sources")

    # One session and one commit per feed or source: a failure in one (bad
    # feed, constraint violation, extraction crash) can never discard
    # another's items or its poll-schedule update (#100).
    total_new = 0
    for model, ids in ((Feed, feed_ids), (Source, newsletter_ids)):
        for target_id in ids:
            new_ids: list[uuid.UUID] = []
            async with AsyncSessionLocal() as session:
                try:
                    target = await session.get(model, target_id)
                    if target is None:
                        continue
                    if model is Feed:
                        new_ids = await _ingest_feed(target, session, rng)
                    else:
                        new_ids = await _ingest_source(target, session, rng)
                    await session.commit()
                except Exception as e:
                    await _safe_rollback(session, f"{model.__name__.lower()} {target_id}")
                    logger.error(f"Failed to ingest {model.__name__.lower()} {target_id}: {e}")
                    await _record_source_failure(target_id, rng, str(e), model=model)
                    continue
            total_new += len(new_ids)
            _enqueue_embeddings(new_ids)

    logger.info(f"Ingested {total_new} new items")
    return {
        "new_items": total_new,
        "feeds_processed": len(feed_ids),
        "sources_processed": len(feed_ids) + len(newsletter_ids),
    }


async def _ingest_feed(feed, session, rng: random.Random) -> list[uuid.UUID]:
    """Poll a shared feed once and file its items for every subscriber (#162).

    Items carry `feed_id` (stable) and, for compatibility with per-source
    code, the oldest active subscriber's `source_id`. The resulting poll state
    is mirrored onto every subscriber's source row.
    """
    from app.services.ingestion.feeds import mirror, subscribers

    subs = await subscribers(session, feed.id)
    if not subs:
        return []
    if not await _within_budgets(feed, subs, session):
        mirror(feed, subs)
        await session.flush()
        return []
    new_ids = await _ingest_source(feed, session, rng, source_id=subs[0].id, feed_id=feed.id)
    await _apply_pro_priority(feed, subs, session)
    mirror(feed, subs)
    await session.flush()
    return new_ids


def _pro_followed(now: datetime):
    """SQL: the feed has an active subscriber with Pro features (#246).
    None on self-host, where every feed is treated the same (and a constant
    in ORDER BY is a Postgres error)."""
    from sqlalchemy import and_, exists, or_

    from app.models.feed import Feed
    from app.models.source import Source
    from app.models.user import User
    from app.services import entitlements
    from app.services.billing import PAID_TIERS

    if not entitlements.is_hosted():
        return None
    from app.services.billing import billing_enabled

    if not billing_enabled():
        # Same rule as effective_plan: with billing off every account is on
        # the trial, which gets Pro's priority fetching.
        return exists().where(
            Source.feed_id == Feed.id, Source.is_active.is_(True), Source.user_id == User.id
        )
    plan_active = or_(User.plan_expires_at.is_(None), User.plan_expires_at > now)
    paid_pro = and_(
        User.tier.in_([t for t in PAID_TIERS if t in entitlements.PRO_PLANS]), plan_active
    )
    # Matches billing.effective_plan: an active paid plan wins over a trial,
    # so a Reader whose trial date hasn't passed is not Pro.
    trialing = and_(
        User.trial_ends_at.is_not(None),
        User.trial_ends_at > now,
        ~and_(User.tier.in_(PAID_TIERS), plan_active),
    )
    return (
        exists()
        .where(Source.feed_id == Feed.id, Source.is_active.is_(True), Source.user_id == User.id)
        .where(or_(paid_pro, trialing))
    )


async def _apply_pro_priority(feed, subs, session) -> None:
    """After a clean poll, a feed with a Pro reader is due again within
    HOSTED_PRO_MAX_POLL_MINUTES (#246). Error and rate-limit backoffs stand."""
    from sqlalchemy import select

    from app.config import get_settings
    from app.models.user import User
    from app.services import entitlements

    cap = get_settings().hosted_pro_max_poll_minutes
    if not entitlements.is_hosted() or cap <= 0 or feed.fetch_error_count:
        return
    soonest = datetime.now(UTC) + timedelta(minutes=cap)
    if feed.next_poll_at is not None and feed.next_poll_at <= soonest:
        return  # already due within the cap; no need to look up subscribers
    user_ids = list({sub.user_id for sub in subs})
    users = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars()
    if any(entitlements.pro_features(u) for u in users):
        feed.next_poll_at = soonest


async def _unpaused(session, user_ids: list) -> list:
    """The subscribers whose hosted account isn't paused (all of them on self-host)."""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import entitlements

    if not entitlements.is_hosted() or not user_ids:
        return user_ids
    users = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars()
    paused = {u.id for u in users if entitlements.is_paused(u)}
    return [uid for uid in user_ids if uid not in paused]


async def _within_budgets(feed, subs, session) -> bool:
    """Hosted abuse controls (#169). A poll is charged to every subscribing
    account and skipped only when all of them are over their hourly budget,
    so a popular feed keeps flowing for everyone else. A host polled too often
    across all accounts waits too. Skipped feeds retry next hour."""
    from app.services import abuse

    now = datetime.now(UTC)
    user_ids = list(dict.fromkeys(sub.user_id for sub in subs))
    # No free hosted tier (#341): paused accounts don't keep a feed polled.
    user_ids = await _unpaused(session, user_ids)
    if not user_ids:
        feed.next_poll_at = now + timedelta(hours=12)
        return False
    within = [uid for uid in user_ids if await abuse.charge_fetch(uid, now=now)]
    if not within:
        feed.next_poll_at = await abuse.pause_fetches(session, user_ids, now=now)
        return False
    if not await abuse.charge_host_poll(feed.feed_url or feed.url, now=now):
        feed.next_poll_at = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        logger.warning(f"Host poll cap reached for {feed.feed_url or feed.url}; retry next hour")
        return False
    return True


async def _ingest_source(
    source,
    session,
    rng: random.Random,
    *,
    source_id: uuid.UUID | None = None,
    feed_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Fetch one source (or shared feed), store its new items, and update its
    poll schedule."""
    from app.services.ingestion.dispatcher import dispatch_source
    from app.services.ingestion.schedule import (
        apply_poll_result,
        is_dead_reddit_feed,
        mark_reddit_feed_dead,
    )

    url = getattr(source, "feed_url", None) or getattr(source, "url", None)
    if is_dead_reddit_feed(url):
        mark_reddit_feed_dead(source, now=datetime.now(UTC))
        await session.flush()
        return []

    raw_items = await dispatch_source(source, session)
    # Newsletter content is private to the forwarding user; tag it so it never
    # enters another user's discovery pool (audit 06-6).
    owner_user_id = source.user_id if source.source_type == "newsletter" else None
    rows: list[dict] = []
    for raw in raw_items:
        row = {
            "source_id": source_id or source.id,
            "feed_id": feed_id,
            "owner_user_id": owner_user_id,
            "url": raw.url,
            "title": raw.title,
            "author": raw.author,
            "published_at": raw.published_at,
            "full_text": raw.full_text,
            "word_count": raw.word_count,
            "transcript_url": raw.transcript_url,
            "guid": raw.guid,
            "simhash": raw.simhash,
            "extraction_method": raw.extraction_method,
            "extraction_confidence": raw.extraction_confidence,
            "page_type": raw.page_type,
            "language": raw.language,
            "lead_image_url": raw.lead_image_url,
            "paywalled": raw.paywalled,
            "rankable": raw.rankable,
            "origin": raw.origin or "followed",
            "reading_time_minutes": raw.reading_time_minutes,
        }
        # EC-08: podcast episodes rank on their transcript when the feed
        # carries a Podcast 2.0 transcript link — even when feed text exists
        # (the transcript is richer).
        if raw.transcript_url:
            from app.services.ingestion.transcripts import fetch_transcript_text

            transcript = await fetch_transcript_text(raw.transcript_url)
            if transcript:
                combined = transcript
                if row["full_text"]:
                    combined = f"{transcript}\n\n{row['full_text']}"
                stored = combined[:100_000]
                row["full_text"] = stored
                # Metadata from the STORED text (post-truncation).
                word_count = len(stored.split())
                row["word_count"] = word_count
                row["reading_time_minutes"] = max(1, round(word_count / 200))
        rows.append(row)

    new_ids = await _insert_items(session, rows)

    status = getattr(source, "last_http_status", None)
    moment = datetime.now(UTC)
    failed = status == 429 or (isinstance(status, int) and status >= 500)
    gone = status == 410
    if failed or gone:
        source.last_error_at = moment
        source.last_error = f"HTTP {status}"
    apply_poll_result(
        source,
        now=moment,
        rng=rng,
        new_items=bool(new_ids) and not failed and not gone,
        error=failed,
        gone=gone,
        retry_after=getattr(source, "retry_after", None),
        gap_seconds=_seconds_since(source.last_fetched_at, moment),
    )
    if not failed and not gone:
        source.last_fetched_at = moment
        source.last_error = None
        source.last_error_at = None
    await session.flush()
    return new_ids


async def _record_source_failure(source_id, rng: random.Random, error: str, *, model=None) -> None:
    """Persist backoff for a source whose ingest raised, in a fresh session.

    Without this the source keeps `next_poll_at` unchanged and fails again on
    every beat tick.
    """
    from app.database import AsyncSessionLocal
    from app.models.source import Source
    from app.services.ingestion.schedule import apply_poll_result

    try:
        async with AsyncSessionLocal() as session:
            source = await session.get(model or Source, source_id)
            if source is None:
                return
            moment = datetime.now(UTC)
            source.last_error_at = moment
            source.last_error = error[:500]
            apply_poll_result(source, now=moment, rng=rng, error=True)
            from app.models.feed import Feed

            if isinstance(source, Feed):
                from app.services.ingestion.feeds import mirror, subscribers

                mirror(source, await subscribers(session, source.id))
            await session.commit()
    except Exception as e:  # never let bookkeeping take down the run
        logger.error(f"Could not record failure for source {source_id}: {e}")


def _seconds_since(then: datetime | None, now: datetime) -> int:
    if then is None:
        return 0
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return max(0, int((now - then).total_seconds()))


@celery_app.task(name="app.workers.tasks.ingest_feeds.ingest_creator_feeds", bind=True)
def ingest_creator_feeds(self) -> dict:
    return asyncio.run(_ingest_creator_feeds_async())


async def _ingest_creator_feeds_async() -> dict:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.creator import Creator
    from app.services.creator.tracker import fetch_creator_content

    async with AsyncSessionLocal() as session:
        creator_ids = [row[0] for row in (await session.execute(select(Creator.id))).all()]

    # Same isolation as feed ingest (#100): the tracker only dedups within one
    # fetch, so every hourly run re-sees the previous items. They are skipped
    # by ON CONFLICT instead of aborting the run.
    total_new = 0
    for creator_id in creator_ids:
        new_ids: list[uuid.UUID] = []
        async with AsyncSessionLocal() as session:
            try:
                creator = await session.get(Creator, creator_id)
                if creator is None:
                    continue
                raw_items = await fetch_creator_content(creator, session)
                rows = [
                    {
                        "creator_platform_id": (
                            uuid.UUID(raw.creator_platform_id) if raw.creator_platform_id else None
                        ),
                        "url": raw.url,
                        "title": raw.title,
                        "author": raw.author,
                        "published_at": raw.published_at,
                        "full_text": raw.full_text,
                        "word_count": raw.word_count,
                    }
                    for raw in raw_items
                ]
                new_ids = await _insert_items(session, rows)
                await session.commit()
            except Exception as e:
                await _safe_rollback(session, f"creator {creator_id}")
                logger.error(f"Failed to ingest creator {creator_id}: {e}")
                continue
        total_new += len(new_ids)
        _enqueue_embeddings(new_ids)

    return {"new_items": total_new, "creators_processed": len(creator_ids)}
