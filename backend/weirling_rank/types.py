"""Data structures and types for candidate scoring and ranking (#245)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np


@dataclass
class Candidate:
    """Features of a content item presented to the ranker."""

    id: Any
    title: str = ""
    url: str = ""
    embedding: list[float] | np.ndarray | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None
    word_count: int | None = None
    reading_time_minutes: int | None = None
    has_citations: bool = False
    is_original_reporting: bool | None = None
    content_depth_score: float | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    topic_clusters: list[str] | None = None
    origin: str | None = None
    source_id: Any | None = None
    feed_id: Any | None = None


@dataclass
class HistoryRow:
    """Historical interaction between a user and a content item."""

    content_id: Any
    vector: np.ndarray | None
    feed_key: str | None = None
    words: int = 0
    completion: float | None = None
    rating: int | None = None
    reason: str | None = None
    saved: bool = False
    skipped: bool = False
    was_suggested: bool = False
    opened_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None
    title: str = ""


@dataclass
class Interest:
    """A user interest cluster: its medoid, plus every topic in it."""

    vector: np.ndarray
    importance: float
    label: str
    # The cluster's own topic vectors and labels. Semantic scoring compares an
    # item with each of them: a cluster of "Rust", "Linux" and "Schedulers"
    # must match a Rust post as well as its medoid does. Empty: the medoid only.
    members: np.ndarray | None = None
    member_labels: list[str] = field(default_factory=list)


@dataclass
class FeedState:
    """User-specific outcome tracking for a feed."""

    name: str
    trust_weight: float
    successes: float = 0.0
    failures: float = 0.0
    last_outcome_at: datetime | None = None


@dataclass
class UserRankingState:
    """Full user state needed to score candidates without database queries."""

    user_id: Any
    weights: dict[str, float]
    history: list[HistoryRow] = field(default_factory=list)
    interests: list[Interest] = field(default_factory=list)
    bridges: list[tuple[np.ndarray, str, str]] = field(default_factory=list)
    feeds: dict[str, FeedState] = field(default_factory=dict)
    source_feed_key: dict[Any, str] = field(default_factory=dict)
    prefers_fresh: bool = True
    novelty_target: float = 0.35
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    feed_id_key: dict[Any, str] = field(default_factory=dict)


@dataclass
class Scored:
    """Scored item with personalized rank score and signal decomposition."""

    item: Any
    prs: float
    features: dict[str, float]
    no_data: list[str]
    contributions: dict[str, float]
    explanation: str
    why_topics: str | None = None
    topic: str | None = None
    vector: np.ndarray | None = None

    def breakdown(self) -> dict:
        """Return rounded per-signal feature values, contributions, and explanation."""
        from weirling_rank.contract import SIGNALS

        out: dict = {name: round(self.features[name], 6) for name in SIGNALS}
        out["contributions"] = self.contributions
        out["no_data"] = self.no_data
        out["explanation"] = self.explanation
        if self.why_topics:
            out["why_topics"] = self.why_topics
        return out
