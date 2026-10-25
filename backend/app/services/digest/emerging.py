"""Emerging-topics detector (UX-10).

A topic is "emerging" when the number of distinct sources covering it in the
last 72 h is a significant outlier against the trailing 28-day baseline
(four 7-day windows, z-score >= 2). Pure math here; SQL in the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass

EMERGING_Z_THRESHOLD = 2.0
EMERGING_MIN_SOURCES = 2
# History a reader's feeds need before anything can count as emerging.
BASELINE_DAYS = 14


@dataclass(frozen=True)
class EmergingTopic:
    topic: str
    recent_sources: int
    baseline_mean: float
    z: float


def baseline_stats(window_counts: list[float]) -> tuple[float, float]:
    """Mean and population std of the trailing windows (0.0 std when flat)."""
    if not window_counts:
        return 0.0, 0.0
    n = len(window_counts)
    mean = sum(window_counts) / n
    variance = sum((c - mean) ** 2 for c in window_counts) / n
    return mean, variance**0.5


def detect_emerging(
    recent_sources: dict[str, int],
    history_counts: dict[str, list[float]],
    *,
    z_threshold: float = EMERGING_Z_THRESHOLD,
    min_sources: int = EMERGING_MIN_SOURCES,
) -> list[EmergingTopic]:
    """recent_sources: topic -> distinct-source count over the last 72 h.
    history_counts: topic -> distinct-source counts per trailing 7-day window
    (oldest first), typically four windows over 28 days.
    """
    from app.services.summarization.extractive import is_junk_topic

    out: list[EmergingTopic] = []
    for topic, count in recent_sources.items():
        if count < min_sources or is_junk_topic(topic):
            continue
        windows = history_counts.get(topic)
        if windows is None:
            # Topic first appears in the recent window: no baseline rows at
            # all means four zero-count windows (CodeRabbit) — a burst into a
            # previously silent topic is exactly the emerging case.
            windows = [0.0, 0.0, 0.0, 0.0]
        elif len(windows) < 2:
            continue  # explicitly incomplete history: not enough baseline
        mean, std = baseline_stats(windows)
        if std == 0.0:
            # Flat baseline (steady state) — a burst is still an outlier.
            if count <= mean:
                continue
            z = float("inf")
        else:
            z = (count - mean) / std
        if z >= z_threshold:
            out.append(EmergingTopic(topic, count, mean, z))
    out.sort(key=lambda e: e.z, reverse=True)
    return out
