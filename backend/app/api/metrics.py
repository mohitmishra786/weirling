from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.services.metrics import analytics
from app.services.ranking.evaluation import evaluate_user_ranking

router = APIRouter(prefix="/metrics", tags=["metrics"])
settings = get_settings()


async def require_metrics_token(x_metrics_token: str | None = Header(default=None)) -> None:
    """Gate aggregate/operator metrics behind a shared token.

    Required outside development; open in development for convenience. Prevents
    any authenticated user from reading whole-instance analytics on a hosted
    deployment.
    """
    if not settings.metrics_token:
        if settings.app_env == "development":
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Metrics endpoint not configured"
        )
    if x_metrics_token != settings.metrics_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid metrics token"
        )


async def require_owner_or_metrics_token(
    request: Request,
    x_metrics_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Allow either instance owner session or metrics token."""
    s = get_settings()
    if s.metrics_token and x_metrics_token == s.metrics_token:
        return
    if not s.metrics_token and s.app_env == "development":
        return
    from app.api.auth import get_current_user
    from app.services import instance_health as health

    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    try:
        # A bearer token (API, MCP) or, when absent, the browser's session cookie.
        user = await get_current_user(request=request, token=bearer, session=session)
        if await health.is_instance_owner(session, user):
            return
    except HTTPException:
        pass  # not signed in: fall through to the metrics-token error below
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Owner login or metrics token required",
    )


class RankingEvalRead(BaseModel):
    n: int
    read_prediction_auc: float | None
    spearman_completion: float | None
    positives: int
    no_learning_auc: float | None = None
    oracle_auc: float | None = None
    chance_auc: float | None = None


@router.get("/llm-status")
async def llm_status(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Signed-in LLM configuration status for the settings page (UX-13).

    Never exposes the key: only which model ids are configured and whether a
    key exists at all.
    """
    s = get_settings()
    # LLM_API_KEY (or the deprecated GROQ_API_KEY it absorbs) or the OpenAI
    # fallback. Checking groq_api_key alone said "no key" on every install
    # configured the current way.
    has_primary_key = bool(s.llm_configured or s.openai_api_key)
    from app.utils.embeddings import active_embedder

    embedder = active_embedder()
    return {
        "llm_configured": has_primary_key,
        "model_primary": s.llm_model_primary,
        "model_fast": s.llm_model_fast,
        "openai_fallback_enabled": s.openai_fallback_enabled and bool(s.openai_api_key),
        # The embedder that is really running, not just the configured one (#195).
        "embedding_model": embedder["name"],
        "embedding_is_hash": embedder["is_hash"],
        "embedding_quality_note": embedder["quality_note"],
        "embedding_upgrade_hint": embedder["how_to_upgrade"],
    }


@router.get("/instance-health")
async def instance_health(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Problems for the owner's banner (#234): workers, beat, ingestion, LLM.

    Only the instance owner (first account) gets problems; everyone else gets
    an empty list, so readers on a shared instance aren't alarmed.
    """
    from app.services import instance_health as health

    if not await health.is_instance_owner(session, current_user):
        return {"owner": False, "problems": []}
    return {"owner": True, "problems": await health.check(session)}


@router.get("/interleaving", dependencies=[Depends(require_metrics_token)])
async def interleaving_metrics(days: int = 7, session: AsyncSession = Depends(get_db)) -> dict:
    """PRS vs newest-first win rate from interleaved digests (#130)."""
    from app.services.ranking.interleaving import interleaving_report

    return await interleaving_report(session, days=max(1, min(days, 90)))


@router.get("/queues", dependencies=[Depends(require_metrics_token)])
async def queue_metrics(hours: int = Query(24, ge=1, le=72)) -> dict:
    """Queue wait, task runtime and digest on-time ratio (#239)."""
    import asyncio

    from app.services.metrics.queues import queue_report
    from app.workers.queue_metrics import _redis

    return await asyncio.to_thread(queue_report, _redis(), hours=hours)


@router.get("/ranking-eval", response_model=RankingEvalRead)
async def ranking_eval(
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RankingEvalRead:
    """How well did the PRS predict *your* reads over the last N days?

    read_prediction_auc > 0.5 means the ranking beats chance at predicting what
    you open; ~0.5 means it isn't learning your behaviour yet.
    """
    result = await evaluate_user_ranking(current_user.id, session, days=days)
    return RankingEvalRead(
        n=result.n,
        read_prediction_auc=result.read_prediction_auc,
        spearman_completion=result.spearman_completion,
        positives=result.positives,
        no_learning_auc=result.no_learning_auc,
        oracle_auc=result.oracle_auc,
        chance_auc=result.chance_auc,
    )


@router.get("/north-star", dependencies=[Depends(require_metrics_token)])
async def north_star(
    days: int = Query(30, ge=1, le=365), session: AsyncSession = Depends(get_db)
) -> dict:
    """Suggestion-driven-read rate — the single flywheel/PMF indicator."""
    return await analytics.suggestion_read_rate(session, days=days)


@router.get("/cold-start-funnel", dependencies=[Depends(require_metrics_token)])
async def cold_start_funnel(session: AsyncSession = Depends(get_db)) -> dict:
    return await analytics.cold_start_funnel(session)


@router.get("/cohort-retention", dependencies=[Depends(require_metrics_token)])
async def cohort_retention(session: AsyncSession = Depends(get_db)) -> list[dict]:
    return await analytics.cohort_retention(session)


@router.get("/scraper-health", dependencies=[Depends(require_metrics_token)])
async def scraper_health(session: AsyncSession = Depends(get_db)) -> dict:
    return await analytics.scraper_health(session)


@router.get("/meta-weight-divergence", dependencies=[Depends(require_metrics_token)])
async def meta_weight_divergence(session: AsyncSession = Depends(get_db)) -> dict:
    return await analytics.meta_weight_divergence(session)


@router.get("/email-deliverability", dependencies=[Depends(require_metrics_token)])
async def email_deliverability() -> dict:
    return await analytics.email_deliverability()


@router.get("/product")
async def product_dashboard(
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """North Star and input metrics for the instance owner (#131)."""
    from app.services import instance_health as health
    from app.services import product_metrics

    if not await health.is_instance_owner(session, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the instance owner can see this"
        )
    return await product_metrics.dashboard(session)


@router.get("/time-to-value", dependencies=[Depends(require_metrics_token)])
async def time_to_value(session: AsyncSession = Depends(get_db)) -> dict:
    """CS-06: local-only signup -> first digest -> first read timing.

    No external telemetry (D-07): computed from this instance's own rows.
    """
    from sqlalchemy import text

    rows = await session.execute(
        text(
            """
            SELECT
              u.id,
              u.created_at,
              MIN(d.generated_at) AS first_digest_at,
              MIN(uci.opened_at) AS first_read_at
            FROM users u
            LEFT JOIN digests d ON d.user_id = u.id
            LEFT JOIN user_content_interactions uci ON uci.user_id = u.id
              AND uci.opened_at IS NOT NULL
            WHERE u.onboarding_complete = true
            GROUP BY u.id, u.created_at
            """
        )
    )
    signup_to_digest: list[float] = []
    digest_to_read: list[float] = []
    for row in rows.fetchall():
        created, first_digest, first_read = row[1], row[2], row[3]
        if first_digest is not None:
            signup_to_digest.append((first_digest - created).total_seconds() / 3600)
        if first_digest is not None and first_read is not None:
            digest_to_read.append((first_read - first_digest).total_seconds() / 3600)

    def _median(values: list[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return round(ordered[mid], 2)
        return round((ordered[mid - 1] + ordered[mid]) / 2, 2)

    return {
        "users_measured": len(signup_to_digest) or len(digest_to_read),
        "median_hours_signup_to_first_digest": _median(signup_to_digest),
        "median_hours_first_digest_to_first_read": _median(digest_to_read),
    }


@router.post("/recompute-scores", dependencies=[Depends(require_metrics_token)])
async def recompute_scores(session: AsyncSession = Depends(get_db)) -> dict:
    """List items the ranker version should rescore. The Celery task does the write."""
    from app.models.content import ContentItem
    from app.services.ranking.learning import RANKER_VERSION

    ids = (await session.execute(select(ContentItem.id))).scalars().all()
    return {"ranker_version": RANKER_VERSION, "items": len(ids)}


@router.get("/ingestion", dependencies=[Depends(require_owner_or_metrics_token)])
async def ingestion_metrics(session: AsyncSession = Depends(get_db)) -> dict:
    """Fetch success, extraction method mix, and ingest-to-score lag. The
    instance owner can read it from the app (Ingestion page) or an operator
    with the metrics token. Reads only the columns the report needs: loading
    every item with its full text grew without bound."""
    from types import SimpleNamespace

    from sqlalchemy import func

    from app.models.content import ContentItem
    from app.models.source import Source
    from app.services.ingestion.observe import ingestion_report
    from app.services.ingestion.retention import storage_stats

    sources = [
        SimpleNamespace(is_active=active, feed_status=feed_status)
        for active, feed_status in (
            await session.execute(select(Source.is_active, Source.feed_status))
        ).all()
    ]
    rows = (
        await session.execute(
            select(
                ContentItem.id,
                ContentItem.source_id,
                ContentItem.extraction_method,
                ContentItem.fetched_at,
                ContentItem.scored_at,
                func.coalesce(func.length(ContentItem.full_text), 0),
            )
        )
    ).all()
    items = [
        SimpleNamespace(extraction_method=method, fetched_at=fetched, scored_at=scored)
        for _id, _source, method, fetched, scored, _length in rows
    ]
    report = ingestion_report(sources, items)
    report["storage"] = storage_stats(
        [
            {"id": item_id, "source_id": source_id, "text_length": length}
            for item_id, source_id, _m, _f, _s, length in rows
        ]
    )
    return report


@router.get("/embeddings")
async def embeddings_metrics(
    request: Request,
    x_metrics_token: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Visibility into active embedding model and online re-embed progress (#244)."""
    await require_owner_or_metrics_token(request, x_metrics_token=x_metrics_token, session=session)
    from sqlalchemy import func

    from app.models.content import ContentItem
    from app.services.embeddings.reembed import get_reembed_cursor
    from app.services.embeddings.registry import active_spec

    s = get_settings()
    active = active_spec(
        model=s.embedding_model,
        cutover=s.embedding_cutover_enabled,
    )
    result = await session.execute(
        select(ContentItem.embedding_model, func.count(ContentItem.id))
        .where(ContentItem.embedding.is_not(None))
        .group_by(ContentItem.embedding_model)
    )
    counts = {row[0] or "unspecified": int(row[1]) for row in result.all()}
    total_embedded = sum(counts.values())
    active_count = counts.get(active.name, 0)
    percent_done = (
        round((active_count / total_embedded * 100.0), 2) if total_embedded > 0 else 100.0
    )

    return {
        "active_model": active.name,
        "active_dim": active.dim,
        "cutover_enabled": s.embedding_cutover_enabled,
        "counts_by_model": counts,
        "total_embedded": total_embedded,
        "percent_done": percent_done,
        "cursor": await get_reembed_cursor(),
    }
