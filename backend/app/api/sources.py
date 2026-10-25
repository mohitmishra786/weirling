from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.content import ContentItem
from app.models.source import Source
from app.models.user import User
from app.schemas.source import SourceCreate, SourceRead, SourceUpdate
from app.services.entitlements import enforce_source_limit
from app.services.ingestion.rss_parser import _autodiscover_feed
from app.utils.logging import get_logger
from app.utils.xml_safety import UnsafeXMLError, assert_xml_safe

router = APIRouter(prefix="/sources", tags=["sources"])
logger = get_logger(__name__)


async def _enforce_capacity(session: AsyncSession, user: User, urls: list[str]) -> None:
    """Plan and abuse limits for every user-initiated way of adding sources.

    No-op in selfhost mode. In hosted mode a free-tier user gets a 402 that
    says how many more they can add (#109), and no account may pile more
    than HOSTED_MAX_SOURCES_PER_HOST feeds onto one host (#169).
    """
    adding = len(urls)
    if adding <= 0:
        return
    from app.services.abuse import enforce_sources_per_host

    await enforce_sources_per_host(session, user, urls)
    count = (
        await session.execute(
            select(func.count()).select_from(Source).where(Source.user_id == user.id)
        )
    ).scalar() or 0
    enforce_source_limit(user, count, adding=adding)


async def _seed_pack_interest(session: AsyncSession, user_id, title: str) -> None:
    """A subscribed pack is a stated interest: give the ranker an embedded
    topic for it so day-0 scores aren't flat (#135). Never lowers an
    existing node."""
    from app.services.interest_graph.graph import InterestGraphManager
    from app.utils.embeddings import get_embedding_service

    label = title.strip()
    if not label:
        return
    try:
        embedding = (await get_embedding_service().encode_batch_cached([label]))[0]
    except Exception:  # embeddings are optional polish here
        embedding = None
    node = await InterestGraphManager().get_or_create_node(
        user_id=user_id, topic_label=label, topic_embedding=embedding, session=session
    )
    node.weight = max(node.weight or 0.0, 0.4)
    if node.topic_embedding is None and embedding is not None:
        node.topic_embedding = embedding
    await session.flush()


def _fetch_new_sources_soon(user_id, session: AsyncSession) -> None:
    """Fetch a user's sources now instead of waiting for the next beat (#102).

    Queued only once the request's transaction commits: queued earlier, the
    worker could read the sources table before the new rows were visible and
    skip them. Nothing is queued if the transaction rolls back."""
    from sqlalchemy import event

    from app.workers.tasks.ingest_feeds import ingest_user_sources

    def _queue(_sync_session) -> None:
        ingest_user_sources.delay(str(user_id))

    event.listen(session.sync_session, "after_commit", _queue, once=True)


@router.get("/starter-packs")
async def list_packs(
    topics: str = "",
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """CS-02: curated topic bundles (10-25 feeds each) for one-click subscribe.

    `topics` (comma-separated) marks the packs matching the user's interests
    as suggested so the picker can pre-select them (#152); `subscribed` says
    how many of a pack's feeds the user already follows.
    """
    from app.services.cold_start.pack_picker import suggest_packs
    from app.services.cold_start.starter_packs import list_starter_packs

    wanted = [t for t in (part.strip() for part in topics.split(",")) if t]
    suggested = await suggest_packs(wanted) if wanted else []
    followed = {
        row[0]
        for row in (
            await session.execute(select(Source.url).where(Source.user_id == current_user.id))
        ).all()
    }
    return [
        {
            "id": pack.id,
            "title": pack.title,
            "description": pack.description,
            "feed_count": len(pack.feeds),
            "feeds": [{"name": f["name"], "url": f["url"]} for f in pack.feeds],
            "suggested": pack.id in suggested,
            "subscribed": sum(1 for f in pack.feeds if f["url"] in followed),
        }
        for pack in list_starter_packs()
    ]


async def _subscribe_packs(session: AsyncSession, user: User, packs: list) -> int:
    """Add every feed of `packs` the user doesn't follow yet, in one
    transaction (packs share feeds, so parallel requests would race on the
    same URL). Returns how many sources were created."""
    from sqlalchemy import text

    # One subscribe at a time per user, so two requests can't both treat a
    # feed as new or pass the capacity check with a stale count. Released at
    # commit or rollback.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"subscribe-packs:{user.id}"},
    )
    existing_urls = {
        row[0]
        for row in (await session.execute(select(Source.url).where(Source.user_id == user.id)))
    }
    new_urls = sorted({f["url"] for p in packs for f in p.feeds} - existing_urls)
    await _enforce_capacity(session, user, new_urls)

    created = 0
    for pack in packs:
        for feed in pack.feeds:
            if feed["url"] in existing_urls:
                continue
            session.add(
                Source(
                    user_id=user.id,
                    url=feed["url"],
                    name=feed["name"],
                    feed_url=feed["feed_url"],
                    source_type="rss",
                    # Seeded pack sources start slightly below default trust so
                    # the ranking engine earns its keep (same rule as onboarding).
                    trust_weight=0.45,
                )
            )
            existing_urls.add(feed["url"])
            created += 1
    await session.flush()
    for pack in packs:
        await _seed_pack_interest(session, user.id, pack.title)
    if created:
        from app.services.product_metrics import track

        await track(session, user.id, "source_added", count=created, via="starter_pack")
        _fetch_new_sources_soon(user.id, session)
    return created


def _find_packs(ids: list[str]) -> list:
    from app.services.cold_start.starter_packs import list_starter_packs

    by_id = {p.id: p for p in list_starter_packs()}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown starter pack: {', '.join(unknown)}",
        )
    return [by_id[i] for i in dict.fromkeys(ids)]


@router.post("/starter-packs/{pack_id}/subscribe", response_model=dict)
async def subscribe_pack(
    pack_id: str,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """One-click subscribe to a starter pack. Idempotent per feed URL."""
    try:
        (pack,) = _find_packs([pack_id])
    except HTTPException:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown starter pack")
    created = await _subscribe_packs(session, current_user, [pack])
    return {
        "status": "ok",
        "pack": pack.id,
        "subscribed": created,
        "already_had": len(pack.feeds) - created,
    }


class PackSubscribeRequest(BaseModel):
    pack_ids: list[str] = Field(min_length=1, max_length=30)


@router.post("/starter-packs/subscribe", response_model=dict)
async def subscribe_packs(
    body: PackSubscribeRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Subscribe to several starter packs in one request: one transaction,
    one fetch of the new feeds. Idempotent per feed URL."""
    packs = _find_packs(body.pack_ids)
    created = await _subscribe_packs(session, current_user, packs)
    return {"status": "ok", "packs": [p.id for p in packs], "subscribed": created}


@router.get("/discover-suggestions")
async def get_discover_suggestions(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    """EC-06: pending source suggestions with reasons; refreshes lazily."""
    from app.models.suggestion import SourceSuggestion
    from app.services.cold_start.discover_sources import refresh_suggestions

    await refresh_suggestions(current_user, session)
    rows = await session.execute(
        select(SourceSuggestion)
        .where(SourceSuggestion.user_id == current_user.id, SourceSuggestion.status == "pending")
        .order_by(SourceSuggestion.created_at.desc())
        .limit(5)
    )
    return [
        {"id": str(row.id), "url": row.url, "name": row.name, "reason": row.reason}
        for row in rows.scalars()
    ]


@router.post(
    "/discover-suggestions/{suggestion_id}/accept", response_model=SourceRead, status_code=201
)
async def accept_discover_suggestion(
    suggestion_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SourceRead:
    from datetime import UTC, datetime

    from app.models.suggestion import SourceSuggestion

    result = await session.execute(
        select(SourceSuggestion).where(
            SourceSuggestion.id == suggestion_id, SourceSuggestion.user_id == current_user.id
        )
    )
    suggestion = result.scalar_one_or_none()
    if suggestion is None or suggestion.status != "pending":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    await _enforce_capacity(session, current_user, [suggestion.url])

    source = Source(
        user_id=current_user.id,
        url=suggestion.url,
        name=suggestion.name,
        feed_url=suggestion.url,
        source_type="rss",
        trust_weight=0.45,  # suggested sources start low like seeded packs
    )
    session.add(source)
    suggestion.status = "accepted"
    suggestion.decided_at = datetime.now(UTC)
    await session.flush()
    return SourceRead.model_validate(source)


@router.post("/discover-suggestions/{suggestion_id}/dismiss", response_model=dict)
async def dismiss_discover_suggestion(
    suggestion_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    from datetime import UTC, datetime

    from app.models.suggestion import SourceSuggestion

    result = await session.execute(
        select(SourceSuggestion).where(
            SourceSuggestion.id == suggestion_id, SourceSuggestion.user_id == current_user.id
        )
    )
    suggestion = result.scalar_one_or_none()
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    suggestion.status = "dismissed"
    suggestion.decided_at = datetime.now(UTC)
    await session.flush()
    return {"status": "dismissed", "id": str(suggestion_id)}


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def add_source(
    body: SourceCreate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SourceRead:
    from app.services.ingestion.schedule import is_reddit_feed, reddit_notice

    # New Reddit sources are refused now: they would die on 13 Nov 2026 (#107).
    if is_reddit_feed(body.url):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reddit_notice())

    await _enforce_capacity(session, current_user, [body.url])
    from app.services.abuse import require_fetch_budget

    await require_fetch_budget(session, current_user)  # autodiscovery fetches the page

    # Autodiscover feed URL
    feed_url = await _autodiscover_feed(body.url)
    source_type = "rss" if feed_url else "scraped"

    source = Source(
        user_id=current_user.id,
        url=body.url,
        feed_url=feed_url,
        source_type=source_type,
        priority=body.priority,
        topics=body.topics,
        initial_backfill_done=False,
    )
    session.add(source)
    await session.flush()
    from app.services.product_metrics import track

    await track(session, current_user.id, "source_added", count=1, via="manual")

    # Fetch this user's sources now. Never the whole instance: on hosted any
    # user could otherwise trigger a poll of every feed (#169).
    _fetch_new_sources_soon(current_user.id, session)

    return SourceRead.model_validate(source)


@router.get("", response_model=list[SourceRead])
async def list_sources(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SourceRead]:
    result = await session.execute(select(Source).where(Source.user_id == current_user.id))
    sources = list(result.scalars().all())
    counts = await _items_per_week(session, sources)
    rows = []
    for source in sources:
        row = SourceRead.model_validate(source)
        row.items_per_week = counts.get(source.id, 0)
        rows.append(row)
    return rows


async def _items_per_week(session: AsyncSession, sources: list[Source]) -> dict:
    """Items per week for each of the user's sources, counted per feed (#108).

    A shared item is stored under one subscriber's source row, so counting by
    `source_id` showed 0 for every later subscriber of the same feed.
    """
    if not sources:
        return {}
    from app.services.feed_identity import feed_key

    keys = {s.feed_url or s.url for s in sources}
    cutoff = datetime.now(UTC) - timedelta(days=7)
    result = await session.execute(
        select(feed_key(), func.count(ContentItem.id))
        .join(ContentItem, ContentItem.source_id == Source.id)
        .where(
            feed_key().in_(keys),
            ContentItem.fetched_at >= cutoff,
            ContentItem.owner_user_id.is_(None),
        )
        .group_by(feed_key())
    )
    per_key = {row[0]: int(row[1]) for row in result.all()}
    return {s.id: per_key.get(s.feed_url or s.url, 0) for s in sources}


# Static path must be registered before /{source_id} to prevent shadowing
@router.post("/import-opml", response_model=dict)
async def import_opml(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    content = await file.read()
    limits = get_settings()
    try:
        opml_bytes = assert_xml_safe(
            content,
            max_bytes=limits.xml_max_bytes,
            max_outlines=limits.opml_max_outlines,
        )
    except UnsafeXMLError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    opml_text = opml_bytes.decode("utf-8", errors="replace")

    try:
        import listparser

        result = listparser.parse(opml_text)
        urls = {feed.url or feed.feed or "" for feed in result.feeds} - {""}
        await _enforce_capacity(session, current_user, sorted(urls))
        created = 0
        for feed in result.feeds:
            url = feed.url or feed.feed or ""
            if not url:
                continue
            source = Source(
                user_id=current_user.id,
                url=url,
                name=feed.title or url,
                feed_url=url,
                source_type="rss",
            )
            session.add(source)
            created += 1
        await session.flush()
        if created:
            from app.services.product_metrics import track

            await track(session, current_user.id, "source_added", count=created, via="opml")
            _fetch_new_sources_soon(current_user.id, session)
        return {"created": created}
    except ImportError:
        raise HTTPException(status_code=500, detail="listparser not installed")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OPML parse error: {e}")


@router.post("/import-opml-v2", response_model=dict)
async def import_opml_tagged(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Folders become tags, duplicate URLs are skipped, large files are queued."""
    content = await file.read()
    limits = get_settings()
    try:
        opml_bytes = assert_xml_safe(
            content,
            max_bytes=limits.xml_max_bytes,
            max_outlines=limits.opml_max_outlines,
        )
    except UnsafeXMLError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    from app.services.ingestion.opml_io import plan_import

    existing = await session.execute(select(Source.url).where(Source.user_id == current_user.id))
    plan = plan_import(opml_bytes.decode("utf-8", errors="replace"), {row[0] for row in existing})
    # A bad outline is reported, not fatal (#150).
    errors = [
        {"url": feed.url[:200], "error": "not an http(s) feed URL"}
        for feed in plan.feeds
        if not feed.url.startswith(("http://", "https://"))
    ]
    plan.feeds = [f for f in plan.feeds if f.url.startswith(("http://", "https://"))]
    plan.created = len(plan.feeds)
    await _enforce_capacity(session, current_user, [feed.url for feed in plan.feeds])
    if plan.queued:
        # Large file (#202): insert in a background job; poll its progress.
        from app.workers.tasks.import_jobs import create_job, run_opml_import

        job_id = await create_job(
            current_user.id,
            [{"url": f.url, "title": f.title, "tags": f.tags} for f in plan.feeds],
        )
        run_opml_import.delay(job_id)
        return {
            "created": 0,
            "skipped": plan.skipped,
            "queued": True,
            "job_id": job_id,
            "total": len(plan.feeds),
            "progress": 0.0,
            "errors": errors,
        }
    for feed in plan.feeds:
        session.add(
            Source(
                user_id=current_user.id,
                url=feed.url,
                name=feed.title,
                feed_url=feed.url,
                source_type="rss",
                tags=feed.tags,
                initial_backfill_done=False,
            )
        )
    await session.flush()
    if plan.feeds:
        from app.services.product_metrics import track

        await track(session, current_user.id, "source_added", count=plan.created, via="opml")
        _fetch_new_sources_soon(current_user.id, session)
    return {
        "created": plan.created,
        "skipped": plan.skipped,
        "queued": False,
        "progress": 1.0,
        "errors": errors,
    }


@router.get("/import-jobs/{job_id}", response_model=dict)
async def import_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Progress of a background OPML import (#202). Owner only."""
    from app.utils.cache import cache_get
    from app.workers.tasks.import_jobs import job_key

    state = await cache_get(job_key(job_id))
    if not state or state.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import job not found")
    total = max(int(state.get("total", 0)), 1)
    return {
        "job_id": job_id,
        "status": state.get("status"),
        "total": state.get("total", 0),
        "done": state.get("done", 0),
        "created": state.get("created", 0),
        "progress": round(min(int(state.get("done", 0)) / total, 1.0), 3),
        "error": state.get("error"),
    }


@router.get("/export-opml")
async def export_opml_file(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    from app.services.ingestion.opml_io import OpmlFeed, export_opml

    result = await session.execute(select(Source).where(Source.user_id == current_user.id))
    feeds = [
        OpmlFeed(
            url=source.feed_url or source.url,
            title=source.name or source.url,
            tags=list(source.tags or []),
        )
        for source in result.scalars()
    ]
    body = export_opml(feeds)
    return Response(content=body, media_type="application/xml")


@router.post("/import-saved", response_model=dict)
async def import_saved(
    kind: str = "auto",
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Import saved articles: starred OPML, Pocket HTML, or CSV (#150).

    `kind` is "opml", "pocket", one of the CSV shapes, or "auto" (from the
    file name and contents). Each row is imported on its own: a bad row is
    reported in `errors` and the rest still import.
    """
    from app.models.content import UserContentInteraction
    from app.services.content_lookup import get_or_create_private_item
    from app.services.ingestion import importers

    raw = (await file.read()).decode("utf-8", errors="replace")
    name = (file.filename or "").lower()
    if kind == "auto":
        if name.endswith((".html", ".htm")) or "<a " in raw[:5000].lower():
            kind = "pocket"
        elif name.endswith((".opml", ".xml")) or raw.lstrip().startswith("<?xml"):
            kind = "opml"
        else:
            kind = importers.detect_csv_kind(raw)
    if kind == "opml":
        imported = importers.import_opml_starred(raw)
    elif kind == "pocket":
        imported = importers.import_pocket_html(raw)
    elif kind in importers.CSV_KINDS:
        imported = importers.import_csv(raw, kind)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown format {kind}"
        )

    created = existing = 0
    errors: list[dict] = []
    for index, item in enumerate(imported, start=1):
        if not item.url.startswith(("http://", "https://")):
            errors.append({"row": index, "url": item.url[:200], "error": "not an http(s) URL"})
            continue
        try:
            async with session.begin_nested():  # one bad row can't undo the others
                # Reuse the public row or this user's own row; another user's
                # private import of the same URL must neither block nor be
                # revealed (#101).
                row, is_new = await get_or_create_private_item(
                    session,
                    current_user.id,
                    item.url,
                    title=item.title[:500],
                    origin="import",
                    full_text=item.note,
                )
                ix = (
                    await session.execute(
                        select(UserContentInteraction).where(
                            UserContentInteraction.user_id == current_user.id,
                            UserContentInteraction.content_item_id == row.id,
                        )
                    )
                ).scalar_one_or_none()
                if ix is None:
                    session.add(
                        UserContentInteraction(
                            user_id=current_user.id,
                            content_item_id=row.id,
                            saved=True,
                            saved_at=datetime.now(UTC),
                            explicit_rating=1,
                            tags=item.tags or None,
                        )
                    )
                elif item.tags:
                    ix.tags = sorted(set(ix.tags or []) | set(item.tags))
        except Exception as e:
            # Type only: database errors can include bound parameters, i.e. the
            # reader's imported notes (CWE-532). Nothing in the response either.
            logger.warning(f"Saved-item import row {index} failed ({type(e).__name__})")
            errors.append({"row": index, "url": item.url[:200], "error": "could not import"})
            continue
        if is_new:
            created += 1
        else:
            existing += 1
    await session.flush()
    return {
        "kind": kind,
        "origin": "import",
        "created": created,
        "already_had": existing,
        "errors": errors,
    }


@router.post("/{source_id}/fix")
async def fix_source(
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """One-click repair for a dead or failing feed (#212).

    Re-runs feed autodiscovery on the site. If the site now advertises a
    different feed (a moved or renamed feed is the usual cause), switch the
    subscription to it, clear the failure state and re-attach it to the right
    shared feed. Otherwise say so, so the UI can offer removal instead.
    """
    from urllib.parse import urlparse

    from app.services.abuse import require_fetch_budget
    from app.services.ingestion.feeds import sync_feeds

    source = (
        await session.execute(
            select(Source).where(Source.id == source_id, Source.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if source.source_type == "newsletter":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Newsletters have no feed to fix"
        )
    await require_fetch_budget(session, current_user)

    parts = urlparse(source.url)
    site_root = f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else None
    candidates = [source.url] + ([site_root] if site_root and site_root != source.url else [])
    found = None
    for page in candidates:
        found = await _autodiscover_feed(page)
        if found and found != source.feed_url:
            break
        found = None
    if not found:
        return {"status": "no_new_feed", "feed_url": source.feed_url, "suggestion": "remove"}

    source.feed_url = found
    source.source_type = "rss"
    source.feed_id = None  # re-attached to the shared feed for the new URL below
    source.is_active = True
    source.feed_status = "healthy"
    source.fetch_error_count = 0
    source.failure_since = None
    source.last_error = None
    source.last_error_at = None
    source.dead_notice_pending = False
    source.next_poll_at = None
    await session.flush()
    await sync_feeds(session, user_id=current_user.id)
    _fetch_new_sources_soon(current_user.id, session)
    return {"status": "fixed", "feed_url": found}


@router.put("/{source_id}", response_model=SourceRead)
async def update_source(
    source_id: uuid.UUID,
    body: SourceUpdate,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SourceRead:
    result = await session.execute(
        select(Source).where(Source.id == source_id, Source.user_id == current_user.id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    if body.priority is not None:
        source.priority = body.priority
    if body.trust_weight is not None:
        source.trust_weight = max(0.0, min(1.0, body.trust_weight))
    if body.is_active is not None:
        source.is_active = body.is_active
    if body.topics is not None:
        source.topics = body.topics

    await session.flush()
    return SourceRead.model_validate(source)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_source(
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    result = await session.execute(
        select(Source).where(Source.id == source_id, Source.user_id == current_user.id)
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    source.is_active = False
    await session.flush()
