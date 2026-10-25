"""Starter packs for a reader's interests, chosen by the LLM (#152).

The taxonomy's word matching puts "system design case studies" in the UI
design pack and finds nothing for "service meshes". One small model call
that sees the reader's topics and every pack (title and a few of its feeds)
ranks them far better. Results are cached by topic set; with no model, a
slow model, or a reply naming no known pack, the taxonomy order is used.
"""

from __future__ import annotations

import asyncio
import hashlib

from app.services.cold_start.starter_packs import StarterPack, list_starter_packs
from app.services.cold_start.taxonomy import packs_for_topics
from app.utils.cache import cache_get, cache_set
from app.utils.logging import get_logger

logger = get_logger(__name__)

MAX_PACKS = 4
# The onboarding picker waits on this; past it the taxonomy answers.
DEADLINE_SECONDS = 5.0
_CACHE_TTL = 7 * 24 * 3600
_FEEDS_SHOWN = 6

_SYSTEM = (
    "You match a reader to curated reading lists. Return only a JSON object "
    '{"packs": ["pack-id", ...]}: up to 4 ids from the catalog, best match '
    "first. Pick a pack only when most of its feeds suit the reader; fewer "
    "good picks beat a weak one."
)


def _catalog(packs: list[StarterPack]) -> str:
    lines = []
    for pack in packs:
        names = ", ".join(feed["name"] for feed in pack.feeds[:_FEEDS_SHOWN])
        lines.append(f"- {pack.id}: {pack.title} (e.g. {names})")
    return "\n".join(lines)


def _cache_key(topics: list[str]) -> str:
    joined = "\n".join(sorted(t.strip().lower() for t in topics if t.strip()))
    return "packs:v1:" + hashlib.sha256(joined.encode()).hexdigest()[:32]


async def _ask_llm(topics: list[str], packs: list[StarterPack]) -> list[str] | None:
    from app.config import get_settings
    from app.services.summarization.summarizer import get_llm_summarizer

    if not get_settings().llm_configured:
        return None
    user = (
        "Reader's interests:\n"
        + "\n".join(f"- {t}" for t in topics[:15])
        + "\n\nCatalog:\n"
        + _catalog(packs)
    )
    data = await get_llm_summarizer().complete_json(
        task="interests", system=_SYSTEM, user=user, max_tokens=200
    )
    if not data or not isinstance(data.get("packs"), list):
        return None
    known = {pack.id for pack in packs}
    picked: list[str] = []
    for pack_id in data["packs"]:
        if isinstance(pack_id, str) and pack_id in known and pack_id not in picked:
            picked.append(pack_id)
    return picked[:MAX_PACKS] or None


async def suggest_packs(topics: list[str], *, deadline: float = DEADLINE_SECONDS) -> list[str]:
    """Pack ids for `topics`, best first, at most MAX_PACKS."""
    wanted = [t.strip() for t in topics if t and t.strip()]
    if not wanted:
        return []
    key = _cache_key(wanted)
    cached = await cache_get(key)
    if isinstance(cached, list) and cached:
        return [str(p) for p in cached][:MAX_PACKS]

    packs = list_starter_packs()
    picked: list[str] | None = None
    try:
        picked = await asyncio.wait_for(_ask_llm(wanted, packs), timeout=deadline)
    except TimeoutError:
        logger.info("Pack choice took over %.0fs; using the taxonomy", deadline)
    except Exception as e:  # never block onboarding on the model
        logger.info("Pack choice fell back to the taxonomy: %s", type(e).__name__)
    if picked:
        await cache_set(key, picked, ttl_seconds=_CACHE_TTL)
        return picked
    return packs_for_topics(wanted)[:MAX_PACKS]
