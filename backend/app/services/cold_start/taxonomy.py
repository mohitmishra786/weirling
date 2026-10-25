"""Offline interest expansion from a curated topic taxonomy (#196).

Without an LLM key, onboarding used to split free text into keywords, so
'rust, postgres internals' became two chips. The taxonomy (built around the
starter packs) maps aliases to topics and each topic to related ones, so the
same text expands to a useful set without any network call.

Matching is per phrase (comma, semicolon, slash, newline or 'and'): an exact
phrase match beats a whole-word key in the phrase, which beats a single word,
and only the best matches for a phrase count, so one loose word cannot drag
in unrelated topics.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

TAXONOMY_PATH = Path(__file__).parent.parent.parent / "data" / "topic_taxonomy.json"
MAX_TOPICS = 15
_SPLIT = re.compile(r"[,;/\n]|\band\b|\b&\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")


@lru_cache(maxsize=1)
def _topics() -> list[dict]:
    data = json.loads(TAXONOMY_PATH.read_text())
    out = []
    for topic in data["topics"]:
        keys = {topic["label"].lower(), *(alias.lower() for alias in topic.get("aliases", []))}
        out.append({**topic, "_keys": keys})
    return out


def known_labels() -> list[str]:
    """Every taxonomy topic name, in file order."""
    return [t["label"] for t in _topics()]


def _labels() -> dict[str, str]:
    return {t["label"].lower(): t["label"] for t in _topics()}


def _score(phrase: str, words: set[str], keys: set[str]) -> int:
    best = 0
    for key in keys:
        if phrase == key:
            return 3
        if " " in key and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", phrase):
            best = max(best, 2)
        elif key in words:
            best = max(best, 2 if len(words) == 1 else 1)
    return best


def expand(text: str) -> list[str]:
    """Taxonomy topics for free text: direct matches first, then related ones."""
    direct: list[str] = []
    for raw in _SPLIT.split(text or ""):
        phrase = " ".join(raw.lower().split())
        if len(phrase) < 2:
            continue
        words = set(_WORD.findall(phrase))
        scored = [(_score(phrase, words, t["_keys"]), t) for t in _topics()]
        top = max((s for s, _ in scored), default=0)
        if top == 0:
            continue
        direct.extend(t["label"] for s, t in scored if s == top)

    labels = _labels()
    ordered: list[str] = []
    for label in direct:
        if label not in ordered:
            ordered.append(label)
    for label in list(ordered):
        topic = next(t for t in _topics() if t["label"] == label)
        for related in topic.get("related", []):
            name = labels.get(related.lower(), related)
            if name not in ordered:
                ordered.append(name)
    return ordered[:MAX_TOPICS]


def packs_for_topics(topics: list[str]) -> list[str]:
    """Starter-pack ids for confirmed topics, best match first (#152).

    A topic counts for its pack when it is a taxonomy label or expands to
    taxonomy topics; packs are ranked by how many topics point at them.
    """
    by_label = {t["label"].lower(): t["pack"] for t in _topics()}
    votes: dict[str, int] = {}
    for topic in topics:
        label = topic.strip().lower()
        packs = (
            [by_label[label]]
            if label in by_label
            else [by_label[m.lower()] for m in expand(topic)[:3] if m.lower() in by_label]
        )
        for pack in packs:
            votes[pack] = votes.get(pack, 0) + 1
    return [pack for pack, _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))]
