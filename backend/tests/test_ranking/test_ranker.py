"""The vectorized ranker (#129, #155, #135, #143).

Replaces the per-signal module tests: the same behaviors are pinned against
`score_candidates`, which is pure (state in, scores out), plus DB-backed
tests of `load_state` and `rank`.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.models.content import ContentItem, UserContentInteraction
from app.models.interest_graph import InterestEdge, InterestNode
from app.models.source import Source
from app.models.user import User
from app.services.ranking.contract import SIGNALS
from app.services.ranking.learning import faithful
from app.services.ranking.ranker import (
    FeedState,
    HistoryRow,
    Interest,
    UserRankingState,
    average_linkage,
    build_interests,
    load_state,
    rank,
    score_candidates,
)

DIM = 384
NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)


def unit(*axes: float) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    for i, value in enumerate(axes):
        v[i] = value
    return v / np.linalg.norm(v)


def item(vector=None, **kw) -> ContentItem:
    defaults = {
        "id": uuid.uuid4(),
        "url": f"https://x.example/{uuid.uuid4().hex}",
        "title": "t",
        "word_count": 800,
        "reading_time_minutes": 4,
        "has_citations": False,
        "is_original_reporting": None,
        "content_depth_score": None,
        "published_at": NOW - timedelta(hours=6),
        "fetched_at": NOW - timedelta(hours=6),
        "topic_clusters": [],
        "origin": "followed",
        "source_id": None,
    }
    defaults.update(kw)
    ci = ContentItem(**defaults)
    ci.embedding = None if vector is None else vector.tolist()
    return ci


def state(**kw) -> UserRankingState:
    weights = {name: 1 / len(SIGNALS) for name in SIGNALS}
    defaults = {
        "user_id": uuid.uuid4(),
        "weights": weights,
        "history": [],
        "interests": [],
        "bridges": [],
        "feeds": {},
        "source_feed_key": {},
        "prefers_fresh": True,
        "novelty_target": 0.35,
        "now": NOW,
    }
    defaults.update(kw)
    return UserRankingState(**defaults)  # type: ignore[arg-type]


def hist(vector, *, completion=None, rating=None, opened=True, days_ago=2, feed_key=None, **kw):
    return HistoryRow(
        content_id=kw.get("content_id", uuid.uuid4()),
        vector=vector,
        feed_key=feed_key,
        words=800,
        completion=completion,
        rating=rating,
        reason=kw.get("reason"),
        saved=kw.get("saved", False),
        skipped=False,
        was_suggested=kw.get("was_suggested", False),
        opened_at=(NOW - timedelta(days=days_ago)) if opened else None,
        created_at=NOW - timedelta(days=days_ago),
        published_at=NOW - timedelta(days=days_ago, hours=3),
        title="h",
    )


# ------------------------------------------------------------- contract


def test_every_score_is_faithful_and_in_range():
    s = state(interests=[Interest(unit(1), 1.0, "rust")])
    cands = [item(unit(1, i / 10)) for i in range(5)] + [item(None)]
    for scored in score_candidates(s, cands):
        assert 0.0 <= scored.prs <= 1.0
        assert faithful(scored.contributions, scored.prs)
        assert set(scored.features) == set(SIGNALS)


def test_brand_new_user_signals_without_data_are_neutral_and_never_cited():
    scored = score_candidates(state(), [item(unit(1)), item(unit(0, 1))])
    for s in scored:
        for name in ("semantic", "reading_depth", "explicit_feedback", "novelty", "suggestion"):
            assert name in s.no_data
            assert s.features[name] == 0.5
        assert "semantic" not in s.explanation.lower()
        assert s.explanation.startswith("New to Weirling")


# ------------------------------------------------------------- semantic (#155, D-05)


def test_multi_interest_user_is_not_averaged_down():
    """Cooking and compilers: content near either one scores high, not the blend."""
    interests = build_interests([(unit(1), 1.0, "cooking"), (unit(0, 1), 1.0, "compilers")])
    assert len(interests) == 2
    s = state(interests=interests)
    near_cooking, blend = score_candidates(s, [item(unit(1)), item(unit(1, 1))])
    assert near_cooking.features["semantic"] > 0.95
    assert near_cooking.features["semantic"] > blend.features["semantic"]
    assert near_cooking.why_topics == "matches your interest in cooking"


def test_every_interest_counts_not_just_the_top_three():
    """Fourteen equal interests (an onboarding with confirmed topics): a post
    matching the tenth must score above one matching none (was flat: only
    the first three medoids were compared)."""
    vectors = [unit(*([0.0] * i + [1.0])) for i in range(14)]
    interests = build_interests([(v, 0.6, f"topic-{i}") for i, v in enumerate(vectors)])
    st = state(interests=interests)
    on_topic, off_topic = score_candidates(
        st, [item(unit(*([0.0] * 9 + [1.0]))), item(unit(*([0.0] * 20 + [1.0])))]
    )
    assert on_topic.features["semantic"] > 0.95
    assert off_topic.features["semantic"] < 0.05
    assert on_topic.why_topics == "matches your interest in topic-9"


def test_a_cluster_matches_each_of_its_topics_not_only_its_medoid():
    near = [unit(1.0, 0.3), unit(1.0, 0.0), unit(1.0, -0.3)]  # one cluster
    interests = build_interests([(v, 0.6, f"t{i}") for i, v in enumerate(near)])
    assert len(interests) == 1
    edge = score_candidates(state(interests=interests), [item(unit(1.0, -0.3))])[0]
    assert edge.features["semantic"] > 0.99
    assert edge.why_topics == "matches your interest in t2"


def test_bridge_scores_the_intersection_of_connected_topics():
    a, b = unit(1), unit(0, 1)
    interests = [Interest(a, 1.0, "compilers"), Interest(b, 1.0, "language design")]
    mid = unit(1, 1)
    with_bridge = state(interests=interests, bridges=[(mid, "compilers", "language design")])
    without = state(interests=interests)
    candidate = item(unit(1, 1))
    assert (
        score_candidates(with_bridge, [candidate])[0].features["semantic"]
        > score_candidates(without, [candidate])[0].features["semantic"]
    )
    assert "connects your interest in compilers and language design" in (
        score_candidates(with_bridge, [candidate])[0].explanation.lower()
    )


def test_average_linkage_groups_close_vectors_only():
    vectors = np.stack([unit(1), unit(1, 0.1), unit(0, 1), unit(0, 1, 0.1)])
    groups = sorted(sorted(g) for g in average_linkage(vectors, 0.45))
    assert groups == [[0, 1], [2, 3]]


# ------------------------------------------------------------- leakage


def test_candidate_never_uses_its_own_interaction():
    candidate = item(unit(1))
    own = hist(unit(1), completion=1.0, rating=1, content_id=candidate.id)
    others = [hist(unit(0, 1), completion=0.1) for _ in range(3)]
    scored = score_candidates(state(history=[own, *others]), [candidate])[0]
    # Its own perfect read must not lift its predicted depth or feedback.
    assert scored.features["reading_depth"] <= 0.5
    assert "explicit_feedback" in scored.no_data


def test_reading_depth_predicts_from_similar_items():
    history = [hist(unit(1, j / 20), completion=0.95) for j in range(4)] + [
        hist(unit(0, 1, j / 20), completion=0.1) for j in range(4)
    ]
    finish, abandon = score_candidates(state(history=history), [item(unit(1)), item(unit(0, 1))])
    assert finish.features["reading_depth"] > 0.7 > abandon.features["reading_depth"]
    assert "you finish about" in finish.explanation.lower()


# ------------------------------------------------------------- feedback, trust, quality


def test_explicit_feedback_moves_toward_rated_up_and_away_from_rated_down():
    history = [hist(unit(1), rating=1), hist(unit(0, 1), rating=-1)]
    liked, disliked = score_candidates(state(history=history), [item(unit(1)), item(unit(0, 1))])
    assert liked.features["explicit_feedback"] > 0.9
    assert disliked.features["explicit_feedback"] < 0.1


def test_source_trust_is_per_user_per_feed_and_shrinks_new_feeds():
    sid = uuid.uuid4()
    good = FeedState(
        name="Good Blog", trust_weight=0.5, successes=9, failures=1, last_outcome_at=NOW
    )
    fresh = FeedState(name="New Blog", trust_weight=0.45)
    s = state(
        feeds={"good": good, "fresh": fresh},
        source_feed_key={sid: "good", (sid2 := uuid.uuid4()): "fresh"},
    )
    trusted, unknown = score_candidates(s, [item(source_id=sid), item(source_id=sid2)])
    assert trusted.features["source_trust"] > 0.7
    assert "source_trust" not in trusted.no_data
    assert "Good Blog" in trusted.explanation or trusted.contributions["source_trust"] < max(
        trusted.contributions.values()
    )
    assert unknown.features["source_trust"] == 0.5 and "source_trust" in unknown.no_data


def test_quality_prefers_substantial_cited_pieces():
    deep = item(
        word_count=2500, has_citations=True, content_depth_score=0.9, reading_time_minutes=12
    )
    thin = item(
        word_count=120, has_citations=False, content_depth_score=0.2, reading_time_minutes=1
    )
    a, b = score_candidates(state(), [deep, thin])
    assert a.features["content_quality"] > b.features["content_quality"]


# ------------------------------------------------------------- temporal, novelty, suggestion


def test_same_event_saturation_lowers_temporal_score():
    """The 6th item on one story scores below a lone item of the same age."""
    story = [item(unit(1, k / 1000)) for k in range(6)]
    lone = item(unit(0, 1))
    scored = {s.item.id: s for s in score_candidates(state(), [*story, lone])}
    assert (
        scored[story[-1].id].features["temporal_context"]
        < scored[lone.id].features["temporal_context"]
    )


def test_novelty_needs_recent_reading_and_peaks_at_target():
    recent = [hist(unit(1, j / 50), completion=0.8, days_ago=1) for j in range(4)]
    same, other = score_candidates(state(history=recent), [item(unit(1)), item(unit(1, 1))])
    assert "novelty" not in same.no_data
    assert other.features["novelty"] > same.features["novelty"]


def test_discovery_items_use_the_users_discovery_track_record():
    finished = [hist(unit(1), completion=0.9, was_suggested=True) for _ in range(5)]
    discovery = item(unit(1), origin="discovery")
    followed = item(unit(1))
    d, f = score_candidates(state(history=finished), [discovery, followed])
    assert d.features["suggestion"] > 0.75 and "suggestion" not in d.no_data
    assert f.features["suggestion"] == 0.5 and "suggestion" in f.no_data


# ------------------------------------------------------------- DB-backed


@pytest.mark.asyncio
async def test_load_state_and_rank_from_the_database(db_session):
    user = User(email=f"rk{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    src = Source(
        user_id=user.id, url="https://rk.example", feed_url="https://rk.example/f", name="RK"
    )
    db_session.add(src)
    a = InterestNode(
        user_id=user.id, topic_label="databases", weight=0.9, topic_embedding=unit(1).tolist()
    )
    b = InterestNode(
        user_id=user.id, topic_label="storage", weight=0.8, topic_embedding=unit(0, 1).tolist()
    )
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add(
        InterestEdge(
            user_id=user.id,
            from_node_id=a.id,
            to_node_id=b.id,
            edge_weight=0.9,
            co_occurrence_count=5,
        )
    )
    on_topic = item(unit(1), source_id=src.id, title="Postgres internals")
    off_topic = item(unit(0, 0, 1), source_id=src.id, title="Celebrity gossip")
    read = item(unit(1, 0.05), source_id=src.id)
    db_session.add_all([on_topic, off_topic, read])
    await db_session.flush()
    db_session.add(
        UserContentInteraction(
            user_id=user.id,
            content_item_id=read.id,
            read_completion_pct=0.95,
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    loaded = await load_state(user, db_session)
    assert {i.label for i in loaded.interests} >= {"databases", "storage"}
    assert loaded.bridges and loaded.feeds["https://rk.example/f"].successes == 1

    ranked = await rank(user, [off_topic, on_topic], db_session)
    assert ranked[0].item.id == on_topic.id  # day-0-style prior from interests (#135)
    assert "databases" in ranked[0].explanation


@pytest.mark.asyncio
async def test_compute_prs_wrapper_matches_the_batch_ranker(db_session):
    from app.services.ranking.scorer import compute_prs

    user = User(email=f"cp{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    candidate = item(unit(1))
    db_session.add(candidate)
    await db_session.commit()

    prs, features = await compute_prs(candidate, user, db_session)
    batch = await rank(user, [candidate], db_session)
    assert prs == pytest.approx(batch[0].prs)
    assert set(features) == set(SIGNALS)


@pytest.mark.asyncio
async def test_subscribing_to_a_pack_seeds_an_embedded_interest(client, db_session):
    from sqlalchemy import select

    from app.services.cold_start.starter_packs import list_starter_packs

    data = {"email": f"pk{uuid.uuid4().hex[:6]}@example.com", "password": "TestPass123!"}
    reg = await client.post("/api/v1/auth/register", json=data)
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    pack = list_starter_packs()[0]

    resp = await client.post(f"/api/v1/sources/starter-packs/{pack.id}/subscribe", headers=headers)

    assert resp.status_code == 200
    user = (await db_session.execute(select(User).where(User.email == data["email"]))).scalar_one()
    node = (
        await db_session.execute(
            select(InterestNode).where(
                InterestNode.user_id == user.id, InterestNode.topic_label == pack.title
            )
        )
    ).scalar_one()
    assert node.weight >= 0.4 and node.topic_embedding is not None


# ------------------------------------------------- explanation faithfulness


def _only_signal_with_weight(name: str) -> dict[str, float]:
    """Weights that make `name` the dominant contribution, so _explain cites it."""
    return {s: (0.9 if s == name else 0.1 / (len(SIGNALS) - 1)) for s in SIGNALS}


def test_rated_up_claim_needs_an_item_similar_to_a_rated_up_one():
    """#321: an unrelated item was still 'similar to articles you rated up'."""
    rated_up, rated_down = unit(1, 0, 0), unit(0, 1, 0)
    st = state(
        weights=_only_signal_with_weight("explicit_feedback"),
        history=[hist(rated_up, rating=1), hist(rated_down, rating=-1)],
    )
    unrelated = item(unit(0, 0, 1))
    close_to_up = item(unit(1, 0.05, 0))
    close_to_down = item(unit(0.05, 1, 0))
    by_title = {}
    for name, ci in (("unrelated", unrelated), ("up", close_to_up), ("down", close_to_down)):
        by_title[name] = score_candidates(st, [ci])[0].explanation.lower()
    assert "rated up" in by_title["up"]
    assert "rated up" not in by_title["unrelated"]
    assert "rated up" not in by_title["down"]


def test_fresh_angle_claim_needs_novelty():
    """#321: novelty's phrase was used without the novelty check passing."""
    recent = [hist(unit(1, 0, 0), completion=0.9, days_ago=1) for _ in range(4)]
    st = state(weights=_only_signal_with_weight("novelty"), history=recent)
    # Cosine 0.75 to recent reading: novelty 0.25, below the 0.35 target, but
    # the novelty feature is still high (0.71), so it is the top contribution.
    close = score_candidates(st, [item(unit(1, 0.882, 0))])[0]
    assert close.features["novelty"] > 0.5 and "novelty" not in close.no_data
    assert "fresh angle" not in close.explanation.lower()


def test_discovery_claim_needs_finished_discoveries():
    st = state(weights=_only_signal_with_weight("suggestion"))
    explanation = score_candidates(st, [item(unit(1, 0, 0), origin="discovery")])[0].explanation
    assert "like ones you finished before" not in explanation


def test_parity_between_adapter_and_weirling_rank_package():
    """Assert score equality before/after between adapter and weirling-rank (#245)."""
    import weirling_rank
    from app.services.ranking.ranker import score_candidates as adapter_score_candidates

    v1 = unit(1, 0, 0)
    v2 = unit(0, 1, 0)
    v3 = unit(0.5, 0.5, 0)

    feed_source_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    st = state(
        weights={s: 1.0 / len(SIGNALS) for s in SIGNALS},
        history=[
            hist(v1, completion=0.85, rating=1, days_ago=2),
            hist(v2, completion=0.2, rating=-1, days_ago=5),
        ],
        interests=[Interest(v1, 1.0, "Tech")],
        bridges=[],
        feeds={
            "https://feed.test": FeedState(
                name="Test Feed", trust_weight=0.7, successes=5, failures=1
            )
        },
        source_feed_key={feed_source_id: "https://feed.test"},
        prefers_fresh=True,
        novelty_target=0.35,
        now=NOW,
    )

    items = [
        item(v1, source_id=feed_source_id, word_count=1200, has_citations=True),
        item(v2, word_count=300, has_citations=False),
        item(v3, origin="discovery", word_count=800),
    ]

    adapter_results = adapter_score_candidates(st, items)
    package_results = weirling_rank.score_candidates(st, items)

    assert len(adapter_results) == len(package_results) == len(items)
    for a, p in zip(adapter_results, package_results, strict=True):
        assert a.item.id == p.item.id
        assert math.isclose(a.prs, p.prs, abs_tol=1e-6)
        assert a.no_data == p.no_data
        assert a.explanation == p.explanation
        assert a.why_topics == p.why_topics
        assert a.topic == p.topic
        for signal in SIGNALS:
            assert math.isclose(a.features[signal], p.features[signal], abs_tol=1e-6)
            assert math.isclose(a.contributions[signal], p.contributions[signal], abs_tol=1e-6)
        assert a.breakdown() == p.breakdown()
