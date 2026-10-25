"""Instance health for the owner's banner (#234).

Self-hosters have no paging, so the app itself says when something stopped:

- the beat scheduler (or the digest worker that runs its heartbeat) is down;
- a worker queue (scrape, embed, digest) hasn't run its heartbeat lately;
- a queue is backed up;
- feeds produced nothing for HEALTH_INGEST_STALE_HOURS;
- the most recent LLM call failed (bad key, quota, retired model).

Only the instance owner (the first account, see services/registration.py)
sees it. Messages never include secrets.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.registration import is_instance_owner

__all__ = ["check", "is_instance_owner", "record_llm_outcome"]

# Grace period after an API restart: heartbeats may not have run yet.
_STARTED = time.time()

LLM_OK_KEY = "llm:last_ok"
LLM_ERROR_KEY = "llm:last_error"
LLM_STATUS_TTL_SECONDS = 2 * 86400


async def record_llm_outcome(ok: bool, reason: str = "") -> None:
    """Best effort: the last LLM success and failure, for the banner."""
    from app.utils.cache import cache_set

    if ok:
        await cache_set(LLM_OK_KEY, int(time.time()), ttl_seconds=LLM_STATUS_TTL_SECONDS)
    else:
        await cache_set(
            LLM_ERROR_KEY,
            {"at": int(time.time()), "reason": reason[:200]},
            ttl_seconds=LLM_STATUS_TTL_SECONDS,
        )


def _problem(key: str, message: str, hint: str) -> dict:
    return {"key": key, "message": message, "hint": hint}


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 120:
        return f"{minutes} minutes"
    return f"{minutes // 60} hours"


async def check(
    session: AsyncSession,
    *,
    now: float | None = None,
    started: float | None = None,
    redis=None,
) -> list[dict]:
    from app.models.content import ContentItem
    from app.models.source import Source
    from app.utils.cache import cache_get, get_redis
    from app.workers.tasks.heartbeat import HEARTBEAT_KEY, WORKER_QUEUES, worker_key

    s = get_settings()
    now = now or time.time()
    stale = s.health_worker_stale_minutes * 60
    warming_up = now - (started or _STARTED) < stale
    problems: list[dict] = []
    redis = redis or get_redis()

    beat_alive = await cache_get(HEARTBEAT_KEY) is not None
    if not beat_alive and not warming_up:
        problems.append(
            _problem(
                "beat",
                "Scheduled jobs aren't running: no feed fetches or digests are being queued.",
                "Check that the beat and digest worker containers are up (docker compose ps).",
            )
        )
    for queue in WORKER_QUEUES:
        try:
            backlog = int(await redis.llen(queue))
        except Exception:
            backlog = 0
        if backlog > s.health_queue_backlog:
            problems.append(
                _problem(
                    f"backlog:{queue}",
                    f"{backlog} jobs are waiting on the {queue} queue.",
                    "Add a worker for that queue, or check it isn't stuck.",
                )
            )
        if queue == "digest" and not beat_alive:
            continue  # same symptom as above
        seen = await cache_get(worker_key(queue))
        if seen is None:
            if warming_up or not beat_alive:
                continue
            age = None
        else:
            age = now - float(seen)
            if age <= stale:
                continue
        problems.append(
            _problem(
                f"worker:{queue}",
                f"The {queue} worker hasn't processed jobs"
                + (f" for {_ago(age)}." if age is not None else " since the app started."),
                f"Check the worker-{queue} container and its logs.",
            )
        )

    has_sources = (
        await session.execute(select(func.count(Source.id)).where(Source.is_active.is_(True)))
    ).scalar()
    latest = (await session.execute(select(func.max(ContentItem.fetched_at)))).scalar()
    if has_sources and latest is not None:
        latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        quiet = datetime.fromtimestamp(now, UTC) - latest
        if quiet > timedelta(hours=s.health_ingest_stale_hours):
            problems.append(
                _problem(
                    "ingest",
                    f"No new articles for {_ago(quiet.total_seconds())}.",
                    "Check the scrape worker logs and your sources' status on the Sources page.",
                )
            )

    error = await cache_get(LLM_ERROR_KEY)
    last_ok = await cache_get(LLM_OK_KEY)
    if (
        s.llm_configured
        and isinstance(error, dict)
        and float(error.get("at", 0)) > float(last_ok or 0)
    ):
        problems.append(
            _problem(
                "llm",
                f"The LLM provider is failing ({error.get('reason') or 'unknown error'}). "
                "Summaries fall back to extracts.",
                "Check ANTHROPIC_API_KEY or LLM_API_KEY, your credit or quota and the model names in .env.",
            )
        )
    return problems
