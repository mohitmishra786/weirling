"""Link-aggregator entries (hnrss.org) carry no article, only link markup."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.ingestion.extract import aggregator_stub
from app.services.summarization.extractive import extractive_summary, plain_text
from app.services.summarization.lazy import needs_llm_summary

HN = (
    '<p>Article URL: <a href="https://www.tylerhogge.com/blog/intensity/">'
    "https://www.tylerhogge.com/blog/intensity/</a></p>"
    '<p>Comments URL: <a href="https://news.ycombinator.com/item?id=50232035">'
    "https://news.ycombinator.com/item?id=50232035</a></p>"
    "<p>Points: 16</p><p># Comments: 3</p>"
)


def test_hn_entry_becomes_a_clean_discussion_link():
    stub = aggregator_stub(HN)
    assert stub == (
        '<p>Discussion: <a href="https://news.ycombinator.com/item?id=50232035">'
        "16 points, 3 comments on Hacker News</a></p>"
    )
    assert aggregator_stub("<p>A normal article about Comments URL handling.</p>") is None
    assert aggregator_stub(None) is None


def test_extractive_summary_never_shows_markup():
    result = extractive_summary("A few thoughts on intensity", HN)
    for text in (result.brief, result.detailed, result.headline):
        assert "<" not in text and "href" not in text
    assert plain_text("<p>Tom &amp; Jerry&#39;s</p>") == "Tom & Jerry's"


def test_a_stub_is_not_sent_to_the_model():
    stub = SimpleNamespace(full_text=aggregator_stub(HN), summary_source="extractive")
    article = SimpleNamespace(full_text="<p>" + "word " * 60 + "</p>", summary_source="extractive")
    assert not needs_llm_summary(stub)
    assert needs_llm_summary(article)
