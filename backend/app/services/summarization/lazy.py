"""Spend LLM calls only on items someone will see (#147).

Ingest writes an extractive summary (with keyword topics) for every item.
The model is asked only for items placed in a digest, right before the
digest is stored and emailed, and for items a user opens in the reader.
That cuts LLM calls from every candidate (~90/user/day) to the ~12-15 a
user actually sees, with the summary shared by everyone who sees the item.
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.models.content import ContentItem
from app.utils.logging import get_logger

logger = get_logger(__name__)

CONCURRENCY = 4


# Below this, there is nothing for a model to summarize (an aggregator's
# link stub, a one-line excerpt); the extract is as good and costs nothing.
MIN_WORDS_FOR_LLM = 40


def needs_llm_summary(item: ContentItem) -> bool:
    if not item.full_text or item.summary_source == "llm":
        return False
    from app.services.summarization.extractive import plain_text

    return len(plain_text(item.full_text).split()) >= MIN_WORDS_FOR_LLM


async def ensure_llm_summaries(
    items: list[ContentItem], *, limit: int | None = None, summarizer=None
) -> int:
    """Summarize `items` lacking an LLM summary, in place; returns the call count.

    Model calls run concurrently (the LLM client still applies its own rate
    limits); results are written onto the items, which the caller's session
    persists. Failures keep the extractive summary.
    """
    # `summarizer`: a reader's own key (#255); otherwise the instance's.
    if summarizer is None and not get_settings().llm_configured:
        return 0
    from app.services.summarization.summarizer import apply_summary, get_llm_summarizer

    todo = [item for item in items if needs_llm_summary(item)]
    if limit is not None:
        todo = todo[:limit]
    if not todo:
        return 0
    gate = asyncio.Semaphore(CONCURRENCY)
    summarizer = summarizer or get_llm_summarizer()

    async def one(item: ContentItem):
        async with gate:
            try:
                return await summarizer.summarize(
                    item.title or "",
                    item.full_text or "",
                    language=item.language,
                    is_transcript=bool(item.transcript_url),
                )
            except Exception as e:  # never block the digest on the model
                logger.info(f"LLM summary failed for {item.id}: {e}")
                return None

    results = await asyncio.gather(*(one(item) for item in todo))
    for item, result in zip(todo, results, strict=True):
        if result is not None and result.summary_source == "llm":
            apply_summary(item, result)
    return len(todo)
