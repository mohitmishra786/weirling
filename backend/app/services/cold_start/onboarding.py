from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.source import Source
from app.models.user import User
from app.services.interest_graph.graph import InterestGraphManager
from app.services.summarization.groq_client import LLMSummarizer
from app.utils.embeddings import get_embedding_service
from app.utils.logging import get_logger
from app.utils.xml_safety import UnsafeXMLError, assert_xml_safe

logger = get_logger(__name__)
graph_manager = InterestGraphManager()


@dataclass
class SampleRating:
    article_url: str
    title: str
    rating: int  # 1=would read, 0=maybe, -1=not for me


async def process_onboarding(
    user: User,
    interest_text: str,
    sample_ratings: list[SampleRating],
    source_opml: str | None,
    session: AsyncSession,
    confirmed_topics: list[str] | None = None,
) -> None:
    embedding_service = get_embedding_service()

    # 1. Topics: the confirmed set from the expand/confirm step (CS-01) wins;
    #    otherwise extract from the interest text (LLM, then keyword fallback).
    if confirmed_topics:
        topics = [t.strip() for t in confirmed_topics if t.strip()][:15]
    else:
        summarizer = LLMSummarizer()
        topics = await summarizer.extract_topics(interest_text)
        if not topics:
            topics = _fallback_topic_extract(interest_text)

    # 2. Embed topics and create initial nodes. Pack-name interests added while
    #    the reader picked packs earlier in this flow stand in for topics they
    #    hadn't confirmed yet; with confirmed topics they only add noise.
    if confirmed_topics:
        await _drop_pack_name_interests(user.id, topics, session)
    topic_embeddings = await embedding_service.encode_batch_cached(topics)
    for label, emb in zip(topics, topic_embeddings, strict=False):
        node = await graph_manager.get_or_create_node(
            user_id=user.id,
            topic_label=label,
            topic_embedding=emb,
            session=session,
        )
        # The reader's own wording wins over a pack title matched by case.
        node.topic_label = label
        # Start above neutral — confirmed interest from onboarding
        node.weight = 0.6
        await session.flush()

    # 3. Process sample ratings as interaction signals
    for sr in sample_ratings:
        if sr.rating == 0:
            continue
        signal = 0.8 if sr.rating == 1 else -0.4
        # Encode the title as a topic proxy
        emb = await embedding_service.encode_single(sr.title)
        node = await graph_manager.get_or_create_node(
            user_id=user.id,
            topic_label=f"sample:{sr.title[:40]}",
            topic_embedding=emb,
            session=session,
        )
        await graph_manager.reinforce_node(node, signal, session)

    # 4. Parse OPML and create sources
    if source_opml:
        await _import_opml(user.id, source_opml, session)

    # 5. Seed starter sources matched to the user's interests so the first
    # digest isn't empty. This is the primary cold-start mitigation: until the
    # product has a collaborative-warmup network effect, a curated pack is what
    # makes day-1 feel smart.
    seeded = await _seed_starter_sources(user.id, topics, session)

    # 6. Mark onboarding complete
    first_time = not user.onboarding_complete
    user.onboarding_complete = True
    user.interest_text = interest_text
    if first_time:
        from app.services.product_metrics import track

        await track(session, user.id, "onboarding_finished", topics=len(topics))
    await session.flush()
    logger.info(
        f"Onboarding complete for user {user.id}: {len(topics)} topics, "
        f"{seeded} starter sources seeded"
    )


async def _drop_pack_name_interests(
    user_id: uuid.UUID, topics: list[str], session: AsyncSession
) -> None:
    """Remove interests named after a starter pack that are still at their
    seed weight and aren't one of the reader's own topics."""
    from sqlalchemy import delete, func

    from app.models.interest_graph import InterestNode
    from app.services.cold_start.starter_packs import list_starter_packs

    pack_titles = {p.title.strip().lower() for p in list_starter_packs()}
    keep = {t.strip().lower() for t in topics}
    drop = sorted(pack_titles - keep)
    if not drop:
        return
    await session.execute(
        delete(InterestNode).where(
            InterestNode.user_id == user_id,
            func.lower(InterestNode.topic_label).in_(drop),
            InterestNode.weight <= 0.4,
        )
    )
    await session.flush()


def _fallback_topic_extract(text: str) -> list[str]:
    """Topics from interest text without an LLM: the curated taxonomy first
    (#196), then any keyword phrases it did not recognise."""
    from app.services.cold_start.taxonomy import MAX_TOPICS, expand

    topics = expand(text)
    known = {t.lower() for t in topics}
    for phrase in _keyword_topics(text):
        if len(topics) >= MAX_TOPICS:
            break
        if phrase not in known and not any(phrase in k or k in phrase for k in known):
            topics.append(phrase)
            known.add(phrase)
    return topics


def _keyword_topics(text: str) -> list[str]:
    """Extract topic phrases from interest text without an LLM.

    Captures both capitalized phrases (proper nouns) and significant lowercase
    words/n-grams. The old version only matched capitalized words, which missed
    lowercase interest descriptions like 'machine learning, distributed systems'.
    """
    import re

    topics: list[str] = []
    # Multi-word phrases (e.g. "machine learning", "distributed systems").
    for m in re.findall(r"\b[a-zA-Z]{3,}(?:\s+[a-zA-Z]{3,}){0,2}\b", text):
        topics.append(m.strip().lower())

    # Single significant words (capitalized = proper noun).
    for m in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text):
        topics.append(m.strip().lower())

    # Dedupe, drop common stop words, and cap.
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "your",
        "about",
        "into",
        "they",
        "will",
        "their",
        "what",
        "when",
        "are",
    }
    seen: set[str] = set()
    out: list[str] = []
    for t in topics:
        if t in stop or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= 10:
            break
    return out


SEED_FEEDS_MAX = 8


def _seeds_from_packs(
    topics: list[str], max_sources: int = SEED_FEEDS_MAX, *, pack_ids: list[str] | None = None
) -> list[dict]:
    """Feeds from the starter packs that match the user's topics (#152).
    `pack_ids` is the LLM's choice; the taxonomy order is used without it."""
    from app.services.cold_start.starter_packs import list_starter_packs
    from app.services.cold_start.taxonomy import packs_for_topics

    packs = {pack.id: pack for pack in list_starter_packs()}
    seeds: list[dict] = []
    seen: set[str] = set()
    for pack_id in pack_ids if pack_ids is not None else packs_for_topics(topics):
        for feed in packs[pack_id].feeds if pack_id in packs else []:
            if feed["url"] in seen:
                continue
            seen.add(feed["url"])
            seeds.append(feed)
            if len(seeds) >= max_sources:
                return seeds
    return seeds


async def _seed_starter_sources(
    user_id: uuid.UUID, topics: list[str], session: AsyncSession
) -> int:
    """Seed feeds from matching starter packs, only if the user has no
    sources yet (they skipped the pack picker and imports). Returns the
    number of new sources created."""
    existing_result = await session.execute(select(Source.url).where(Source.user_id == user_id))
    existing_urls = {row[0] for row in existing_result.fetchall()}
    if existing_urls:
        return 0  # they chose packs or imported feeds themselves
    from app.services.cold_start.pack_picker import suggest_packs

    seeds = _seeds_from_packs(topics, pack_ids=await suggest_packs(topics))
    if not seeds:
        return 0

    created = 0
    for src in seeds:
        if src["url"] in existing_urls:
            continue
        source = Source(
            user_id=user_id,
            url=src["url"],
            name=src["name"],
            feed_url=src["feed_url"],
            source_type="rss",
            # Seeded sources start slightly below the default trust so the
            # ranking engine earns its keep — the user's explicit signal is
            # what should raise a source's weight over time.
            trust_weight=0.45,
        )
        session.add(source)
        created += 1
    if created:
        await session.flush()
        logger.info(f"Seeded {created} starter sources for user {user_id}")
    return created


async def _import_opml(user_id: uuid.UUID, opml_content: str, session: AsyncSession) -> None:
    try:
        import listparser

        limits = get_settings()
        safe = assert_xml_safe(
            opml_content,
            max_bytes=limits.xml_max_bytes,
            max_outlines=limits.opml_max_outlines,
        )
        result = listparser.parse(safe.decode("utf-8", errors="replace"))
        for feed in result.feeds:
            url = feed.url or feed.feed or ""
            if not url:
                continue
            source = Source(
                user_id=user_id,
                url=url,
                name=feed.title or url,
                feed_url=url,
                source_type="rss",
            )
            session.add(source)
        await session.flush()
        logger.info(f"Imported {len(result.feeds)} OPML sources for user {user_id}")
    except ImportError:
        logger.warning("listparser not installed, OPML import skipped")
    except UnsafeXMLError as e:
        logger.warning(f"OPML rejected for user {user_id}: {e}")
    except Exception as e:
        logger.error(f"OPML import failed: {e}")
