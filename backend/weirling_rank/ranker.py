"""Vectorized, explainable scoring engine for candidate content items (#245)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from weirling_rank.contract import (
    SIGNALS,
    beta_trust,
    clamp01,
    length_bucket,
    temporal_score,
)
from weirling_rank.types import (
    HistoryRow,
    Interest,
    Scored,
    UserRankingState,
)

NEUTRAL = 0.5
HISTORY_LIMIT = 500
MAX_INTEREST_VECTORS = 80
CLUSTER_DISTANCE = 0.45
BRIDGE_EDGE_WEIGHT = 0.5
KNN = 15
SAME_EVENT_COSINE = 0.85
NOVELTY_WINDOW_DAYS = 30
RECENT_EVENT_HOURS = 72


def _vec(raw: Any) -> np.ndarray | None:
    if raw is None:
        return None
    v = np.asarray(raw, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    if not math.isfinite(norm) or norm < 1e-8:
        return None
    return v / norm


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _is_success(row: HistoryRow) -> bool:
    return bool(
        (row.rating is not None and row.rating > 0)
        or row.saved
        or (row.completion is not None and row.completion >= 0.6)
    )


def _is_failure(row: HistoryRow) -> bool:
    return bool(
        (row.rating is not None and row.rating < 0)
        or (row.completion is not None and row.completion < 0.2 and row.opened_at is not None)
        or (row.skipped and row.opened_at is not None)
    )


def average_linkage(vectors: np.ndarray, threshold: float) -> list[list[int]]:
    """Agglomerative clustering, average linkage on cosine distance.

    Lance-Williams updates on an n x n distance matrix: O(n^2) per merge,
    fast for the <= 80 interest vectors a user has.
    """
    n = len(vectors)
    if n == 0:
        return []
    dist = 1.0 - np.clip(vectors @ vectors.T, -1.0, 1.0)
    np.fill_diagonal(dist, np.inf)
    sizes = np.ones(n)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    active = np.ones(n, dtype=bool)
    while active.sum() > 1:
        masked = np.where(np.outer(active, active), dist, np.inf)
        flat = int(np.argmin(masked))
        i, j = divmod(flat, n)
        if not math.isfinite(masked[i, j]) or masked[i, j] > threshold:
            break
        if i > j:
            i, j = j, i
        merged = (sizes[i] * dist[i] + sizes[j] * dist[j]) / (sizes[i] + sizes[j])
        dist[i, :] = merged
        dist[:, i] = merged
        dist[i, i] = np.inf
        dist[j, :] = np.inf
        dist[:, j] = np.inf
        sizes[i] += sizes[j]
        members[i].extend(members.pop(j))
        active[j] = False
    return [members[i] for i in range(n) if active[i]]


def build_interests(
    candidates: list[tuple[np.ndarray, float, str]],
) -> list[Interest]:
    """Cluster interest vectors and keep one medoid per cluster."""
    if not candidates:
        return []
    candidates = sorted(candidates, key=lambda c: c[1], reverse=True)[:MAX_INTEREST_VECTORS]
    matrix = np.stack([c[0] for c in candidates])
    interests: list[Interest] = []
    for group in average_linkage(matrix, CLUSTER_DISTANCE):
        sub = matrix[group]
        centroid = sub.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 1e-8:
            centroid = centroid / norm
            # Medoid: the input vector closest to the group centroid.
            cos = sub @ centroid
            best_idx = group[int(np.argmax(cos))]
            # Weight: sum of importances in the cluster.
            total_imp = float(sum(candidates[i][1] for i in group))
            labels = [candidates[i][2] for i in group if candidates[i][2]]
            label = max(set(labels), key=labels.count) if labels else ""
            interests.append(
                Interest(
                    vector=matrix[best_idx],
                    importance=total_imp,
                    label=label,
                    members=sub,
                    member_labels=[candidates[i][2] for i in group],
                )
            )
    interests.sort(key=lambda it: it.importance, reverse=True)
    return interests


def _quality(item: Any, bucket_completion: float | None) -> float:
    words = int(getattr(item, "word_count", 0) or 0)
    length = 0.2 if words < 200 else 0.7 if words < 2000 else 0.85
    has_citations = bool(getattr(item, "has_citations", False))
    density = 0.6 if has_citations else 0.2
    cds = getattr(item, "content_depth_score", None)
    depth = float(cds) if cds is not None else 0.5
    is_orig = bool(getattr(item, "is_original_reporting", False))
    original = 0.1 if is_orig else 0.0
    raw = clamp01(0.45 * length + 0.2 * density + 0.25 * depth + original)
    if bucket_completion is None:
        return raw
    return clamp01(0.7 * raw + 0.3 * bucket_completion)


_PHRASES = {
    "semantic": None,  # uses why_topics
    "reading_depth": "you usually finish articles like this",
    "suggestion": "a discovery pick",
    "explicit_feedback": None,
    "source_trust": "from a source you read",
    "content_quality": "a substantial read",
    "temporal_context": "timely",
    "novelty": None,
}


def _explain(
    contributions: dict[str, float],
    have: set[str],
    notes: dict[str, str],
    why_topics: str | None,
) -> str:
    """Name the one or two strongest signals that actually had evidence."""
    learned = [name for name in have if name not in ("content_quality", "temporal_context")]
    if not learned:
        return "New to Weirling: ranked by freshness and depth while it learns what you read"
    ordered = sorted(have, key=lambda name: contributions[name], reverse=True)
    parts: list[str] = []
    for name in ordered:
        phrase: str | None
        if name == "semantic" and why_topics:
            phrase = why_topics
        elif name == "reading_depth" and name in notes:
            phrase = f"you finish about {notes[name]} of similar articles"
        else:
            phrase = notes.get(name) or _PHRASES.get(name)
        if phrase and phrase not in parts:
            parts.append(phrase)
        if len(parts) == 2:
            break
    text = " · ".join(parts)
    return text[:1].upper() + text[1:]


def score_candidates(
    state: UserRankingState,
    candidates: list[Any],
    *,
    is_same_model: Callable[[str | None, int | None], bool] | None = None,
) -> list[Scored]:
    """Pure vectorized feature computation, linear combination, and explanation generation."""
    if not candidates:
        return []
    now = state.now

    vecs: list[np.ndarray | None] = []
    for c in candidates:
        emb = getattr(c, "embedding", None)
        model = getattr(c, "embedding_model", None)
        dim = getattr(c, "embedding_dim", None)
        if emb is not None:
            if is_same_model is not None and not is_same_model(model, dim):
                vecs.append(None)
            else:
                vecs.append(_vec(emb))
        else:
            vecs.append(None)

    dim = next((v.shape[0] for v in vecs if v is not None), None)

    hist = state.history
    hist_vec_rows = [r for r in hist if r.vector is not None and dim and r.vector.shape[0] == dim]
    H = np.stack([np.asarray(r.vector) for r in hist_vec_rows]) if hist_vec_rows else None
    hist_ids = [r.content_id for r in hist_vec_rows]

    completions = (
        np.array([r.completion if r.completion is not None else np.nan for r in hist_vec_rows])
        if hist_vec_rows
        else np.array([])
    )
    ratings = np.array([r.rating or 0 for r in hist_vec_rows]) if hist_vec_rows else np.array([])
    recent_cut = now - timedelta(days=NOVELTY_WINDOW_DAYS)
    recent_mask = (
        np.array(
            [
                (r.opened_at or r.created_at) >= recent_cut and r.opened_at is not None
                for r in hist_vec_rows
            ]
        )
        if hist_vec_rows
        else np.array([], dtype=bool)
    )
    event_cut = now - timedelta(hours=RECENT_EVENT_HOURS)
    event_mask = (
        np.array([r.opened_at is not None and r.opened_at >= event_cut for r in hist_vec_rows])
        if hist_vec_rows
        else np.array([], dtype=bool)
    )

    # Length-bucket completion, for quality shrinkage.
    bucket_vals: dict[str, list[float]] = {}
    for r in hist:
        if r.completion is not None and r.opened_at is not None:
            bucket_vals.setdefault(length_bucket(r.words), []).append(float(r.completion))
    bucket_completion = {k: float(np.mean(v)) for k, v in bucket_vals.items() if len(v) >= 3}

    suggested = [r for r in hist if r.was_suggested and r.opened_at is not None]
    sugg_success = sum(1 for r in suggested if _is_success(r))

    # One row per interest topic (every cluster member), each weighted by the
    # square root of its cluster's share of the strongest cluster: a minor
    # interest still counts, just less. Comparing with only the top three
    # medoids made every item score alike when a reader had many equal
    # interests (a Rust post never met the "Rust" interest).
    interest_rows: list[np.ndarray] = []
    interest_row_w: list[float] = []
    interest_row_label: list[str] = []
    top_imp = max((it.importance for it in state.interests), default=0.0)
    for it in state.interests:
        weight = math.sqrt(max(it.importance, 0.0) / top_imp) if top_imp > 0 else 1.0
        rows = it.members if it.members is not None and len(it.members) else it.vector[None, :]
        names = it.member_labels if len(it.member_labels) == len(rows) else [it.label] * len(rows)
        for row, name in zip(rows, names, strict=True):
            interest_rows.append(np.asarray(row))
            interest_row_w.append(weight)
            interest_row_label.append(name or it.label)
    interest_matrix = np.stack(interest_rows) if interest_rows else None
    interest_imp = np.array(interest_row_w) if interest_rows else None
    bridge_matrix = np.stack([b[0] for b in state.bridges]) if state.bridges else None

    cand_matrix_rows = [i for i, v in enumerate(vecs) if v is not None and v.shape[0] == dim]
    C = np.stack([np.asarray(vecs[i]) for i in cand_matrix_rows]) if cand_matrix_rows else None
    cand_sim = C @ C.T if C is not None else None
    cand_pos = {i: k for k, i in enumerate(cand_matrix_rows)}

    results: list[Scored] = []
    for idx, item in enumerate(candidates):
        f: dict[str, float] = {name: NEUTRAL for name in SIGNALS}
        have: set[str] = set()
        notes: dict[str, str] = {}
        v = vecs[idx] if idx in cand_pos else None
        clusters = getattr(item, "topic_clusters", None) or []
        topic: str | None = clusters[0] if clusters else None
        why_topics: str | None = None

        item_id = getattr(item, "id", None)
        keep = (
            np.array([cid != item_id for cid in hist_ids], dtype=bool)
            if (hist_ids and item_id is not None)
            else None
        )
        sims = (H @ v) if (H is not None and v is not None) else None

        # semantic
        if (
            v is not None
            and interest_matrix is not None
            and interest_imp is not None
            and interest_matrix.shape[1] == v.shape[0]
        ):
            cos = np.clip(interest_matrix @ v, 0.0, 1.0)
            weighted = cos * interest_imp
            best = int(np.argmax(weighted))
            score = float(weighted[best])
            label = interest_row_label[best]
            if bridge_matrix is not None and bridge_matrix.shape[1] == v.shape[0]:
                bcos = np.clip(bridge_matrix @ v, 0.0, 1.0)
                bbest = int(np.argmax(bcos))
                if bcos[bbest] > score + 0.02:
                    score = float(bcos[bbest])
                    _, a, b = state.bridges[bbest]
                    why_topics = f"connects your interest in {a} and {b}"
                    topic = topic or a
            if why_topics is None and label:
                why_topics = f"matches your interest in {label}"
                topic = topic or label
            f["semantic"] = clamp01(score)
            have.add("semantic")

        # reading depth (kNN over opened items with completion)
        if sims is not None and keep is not None:
            mask = keep & ~np.isnan(completions)
            if mask.sum() >= 3:
                s = np.where(mask, np.clip(sims, 0.0, 1.0), -1.0)
                order = np.argsort(-s)[:KNN]
                order = order[s[order] > 0]
                if len(order):
                    w = s[order]
                    mean = float(np.dot(w, completions[order]) / (w.sum() + 1e-8))
                    shrink = w.sum() / (w.sum() + 2.0)
                    f["reading_depth"] = clamp01(shrink * mean + (1 - shrink) * NEUTRAL)
                    have.add("reading_depth")
                    notes["reading_depth"] = f"{round(mean * 100)}%"

        # suggestion
        if getattr(item, "origin", None) == "discovery":
            f["suggestion"] = clamp01((2.0 + sugg_success) / (3.0 + len(suggested)))
            have.add("suggestion")
            if sugg_success > 0:
                notes["suggestion"] = "a discovery pick like ones you finished before"

        # explicit feedback
        if sims is not None and keep is not None:
            pos = keep & (ratings > 0)
            neg = keep & (ratings < 0)
            if pos.any() or neg.any():
                p = float(np.clip(sims[pos], 0, 1).max()) if pos.any() else 0.0
                n = float(np.clip(sims[neg], 0, 1).max()) if neg.any() else 0.0
                value = 0.5 + 0.5 * p - 0.5 * n
                reasons = {r.reason for r in hist if r.rating is not None and r.rating < 0}
                content_depth = getattr(item, "content_depth_score", None)
                if (
                    "too_basic" in reasons
                    and content_depth is not None
                    and float(content_depth) < 0.4
                ):
                    value -= 0.1
                f["explicit_feedback"] = clamp01(value)
                have.add("explicit_feedback")
                if p >= n and p > 0.5:
                    notes["explicit_feedback"] = "similar to articles you rated up"

        # source trust (per user, per feed)
        source_id = getattr(item, "source_id", None)
        feed_id = getattr(item, "feed_id", None)
        key = state.source_feed_key.get(source_id) if source_id else None
        if key is None and feed_id is not None:
            key = state.feed_id_key.get(feed_id)
        feed = state.feeds.get(key) if key else None
        if feed is not None:
            age = (now - feed.last_outcome_at).days if feed.last_outcome_at else 0.0
            prior_mean = clamp01(feed.trust_weight)
            trust = beta_trust(
                feed.successes,
                feed.failures,
                prior_success=4.0 * prior_mean,
                prior_failure=4.0 * (1.0 - prior_mean),
                age_days=age,
            )
            outcomes = feed.successes + feed.failures
            if outcomes > 0 or abs(feed.trust_weight - 0.5) > 0.06:
                f["source_trust"] = trust.mean
                have.add("source_trust")
                if outcomes >= 3:
                    rate = feed.successes / outcomes
                    notes["source_trust"] = (
                        f"from {feed.name}, which you finish {round(rate * 100)}% of the time"
                    )
                else:
                    notes["source_trust"] = f"from {feed.name}"

        # content quality (always available)
        bucket = length_bucket(getattr(item, "word_count", None))
        f["content_quality"] = _quality(item, bucket_completion.get(bucket))
        have.add("content_quality")
        reading_time = getattr(item, "reading_time_minutes", 0) or 0
        if reading_time >= 8:
            notes["content_quality"] = f"an in-depth {reading_time}-minute read"

        # temporal context (always available)
        published_at = getattr(item, "published_at", None)
        fetched_at = getattr(item, "fetched_at", None)
        published = _aware(published_at) or _aware(fetched_at) or now
        same_event = 1
        if idx in cand_pos and cand_sim is not None:
            same_event += int((cand_sim[cand_pos[idx]] >= SAME_EVENT_COSINE).sum()) - 1
        if sims is not None and event_mask.size:
            same_event += int(((sims >= SAME_EVENT_COSINE) & event_mask).sum())
        f["temporal_context"] = temporal_score(
            published_at=published,
            now=now,
            same_event_count=same_event,
            user_prefers_fresh=state.prefers_fresh,
        )
        have.add("temporal_context")
        hours = max(0.0, (now - published).total_seconds() / 3600)
        if hours <= 24:
            notes["temporal_context"] = "published in the last day"

        # novelty
        if sims is not None and keep is not None and recent_mask.size:
            mask = keep & recent_mask
            if mask.sum() >= 3:
                nearest = float(np.clip(sims[mask], 0, 1).max())
                novelty = 1.0 - nearest
                target = state.novelty_target or 0.35
                f["novelty"] = clamp01(1.0 - abs(novelty - target) / target)
                have.add("novelty")
                if novelty >= target:
                    notes["novelty"] = "a fresh angle compared with your recent reading"

        prs = float(sum(state.weights[name] * f[name] for name in SIGNALS))
        contributions = {name: state.weights[name] * f[name] for name in SIGNALS}
        no_data = [name for name in SIGNALS if name not in have]
        explanation = _explain(contributions, have, notes, why_topics)
        results.append(
            Scored(
                item=item,
                prs=clamp01(prs),
                features=f,
                no_data=no_data,
                contributions=contributions,
                explanation=explanation,
                why_topics=why_topics,
                topic=topic,
                vector=v,
            )
        )
    return results


def rank_candidates(
    state: UserRankingState,
    candidates: list[Any],
    *,
    is_same_model: Callable[[str | None, int | None], bool] | None = None,
) -> list[Scored]:
    """Score candidates and return them sorted by personalized rank score (PRS) descending."""
    scored = score_candidates(state, candidates, is_same_model=is_same_model)
    scored.sort(key=lambda s: s.prs, reverse=True)
    return scored
