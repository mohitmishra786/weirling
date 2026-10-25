"""Extractive summary used whenever the LLM is missing, rate-limited, or broken.

Lead sentences plus a light overlap score against the title (a tiny TextRank
stand-in: sentences that share words with the title rank above pure position).
No network, no model. The digest can always ship one of these.
"""

from __future__ import annotations

import re

from app.services.summarization.groq_client import SummarizationResult

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9']+")


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE.split(text.replace("\n", " ")) if part.strip()]
    return parts


def _tokens(text: str) -> set[str]:
    return {tok for tok in _WORD.findall(text.lower()) if len(tok) > 2}


_STOP = frozenset(
    """a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers him his how i if in into is it its itself just
    me more most my no nor not now of off on once only or other our ours out over own same she
    should so some such than that the their theirs them then there these they this those through
    to too under until up very was we were what when where which while who whom why will with
    would you your yours new one two get got like make made use used using way ways many much
    via says said say per may might must shall within without across among around every
    including include includes really still even thing things lot lots time year years today
    week day days people post posts blog article read here https http www com""".split()
    # HTML that leaks through feeds with escaped markup: never a topic (#159:
    # "noopener noreferrer" showed up as an emerging topic).
    + """href rel noopener noreferrer nofollow target blank src img alt div span class style
    amp nbsp quot html org net""".split()
)
# Tags and escaped tags, stripped before counting words.
_TAG = re.compile(r"</?[a-zA-Z][^>]*>|&lt;/?[a-zA-Z].*?&gt;", re.S)


_HTML_WORDS = frozenset("href rel noopener noreferrer nofollow target blank src img".split())


def is_junk_topic(topic: str) -> bool:
    """A topic made of leaked HTML ("rel noopener"), stored before #159's fix."""
    return any(word in _HTML_WORDS for word in topic.lower().split())


def keyword_topics(title: str, full_text: str, k: int = 3) -> list[str]:
    """Up to `k` topic phrases without an LLM (#147).

    Title words count triple; a body bigram seen at least twice beats single
    words. Deterministic, so the same article always gets the same topics.
    """
    from collections import Counter
    from itertools import pairwise

    def words(text: str) -> list[str]:
        return [
            w
            for w in _WORD.findall(text.lower())
            if len(w) > 2 and w not in _STOP and not w.isdigit()
        ]

    title_words = words(title)
    body_words = words(" ".join(_TAG.sub(" ", full_text or "").split()[:600]))
    singles: Counter[str] = Counter()
    for w in title_words:
        singles[w] += 3
    for w in body_words:
        singles[w] += 1
    bigrams: Counter[str] = Counter(f"{a} {b}" for a, b in pairwise(body_words) if a != b)
    for a, b in pairwise(title_words):
        bigrams[f"{a} {b}"] += 2
    topics: list[str] = []
    for phrase, count in bigrams.most_common():
        if count < 2 or len(topics) >= k:
            break
        topics.append(phrase)
    for word, _ in singles.most_common():
        if len(topics) >= k:
            break
        if not any(word in t.split() for t in topics):
            topics.append(word)
    return topics[:k]


_REAL_TAG = re.compile(r"</?[a-zA-Z][^>]*>", re.S)


def plain_text(text: str | None) -> str:
    """Feed text without tags or entities: summaries are shown as text, so
    markup would appear literally ("<p>Article URL: <a href=…").

    With real tags present, escaped brackets are literal text (`Vec&lt;String&gt;`
    in a code sample stays "Vec<String>"); only a feed with no real tags is
    treated as double-escaped markup."""
    import html

    raw = text or ""
    stripped = _REAL_TAG.sub(" ", raw) if _REAL_TAG.search(raw) else _TAG.sub(" ", raw)
    return " ".join(html.unescape(stripped).split())


def extractive_summary(title: str, full_text: str) -> SummarizationResult:
    full_text = plain_text(full_text)
    sentences = _sentences(full_text) or ([title.strip()] if title.strip() else ["Untitled"])
    title_tokens = _tokens(title)
    ranked = sorted(
        range(len(sentences)),
        key=lambda i: (
            len(_tokens(sentences[i]) & title_tokens),
            -i,  # earlier sentences win ties
        ),
        reverse=True,
    )
    chosen = sorted(ranked[:6])
    ordered = [sentences[i] for i in chosen]
    brief = " ".join(ordered[:3])[:800]
    detailed = " ".join(ordered[:6])[:1600]
    words = len(full_text.split()) if full_text else len(title.split())
    reading_time = max(1, round(words / 230)) if words else 1
    headline = (title.strip() or sentences[0])[:180]
    return SummarizationResult(
        headline=headline,
        brief=brief or headline,
        detailed=detailed or brief or headline,
        depth_score=0.5,
        is_original_reporting=False,
        has_citations=False,
        topic_clusters=keyword_topics(title, full_text),
        reading_time_minutes=reading_time,
        summary_source="extractive",
    )
