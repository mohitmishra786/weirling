"""Summaries in the article's language (#248)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from app.models.content import ContentItem
from app.services.ingestion.metadata import _language
from app.services.summarization.groq_client import language_instruction

TEXTS = {
    "en": "The planner chooses an index when the statistics say that it is cheaper for the query. "
    "It compares the estimated cost of a sequential scan with an index scan and picks the cheaper plan.",
    "fr": "Le planificateur choisit un index quand les statistiques disent que la requête est moins chère pour le serveur et que les données sont dans le cache.",
    "de": "Der Planer wählt einen Index, wenn die Statistik sagt, dass die Abfrage mit dem Index billiger ist und die Daten auf der Platte nicht im Speicher sind.",
}
SUMMARIES = {
    "fr": "Le planificateur choisit l'index selon les statistiques.",
    "de": "Der Planer wählt den Index anhand der Statistik.",
    "en": "The planner picks the index from the statistics.",
}


@pytest.mark.parametrize("code", ["en", "fr", "de"])
def test_detects_the_language_without_a_lang_attribute(code):
    soup = BeautifulSoup("<html><body><p>x</p></body></html>", "html.parser")
    assert _language(soup, TEXTS[code]) == code


def test_html_lang_still_wins():
    soup = BeautifulSoup('<html lang="fr-FR"><body></body></html>', "html.parser")
    assert _language(soup, TEXTS["en"]) == "fr-fr"


def test_instruction_only_for_non_english_articles():
    assert "in French" in language_instruction("fr-FR")
    assert "in German" in language_instruction("de")
    assert "topic_clusters in English" in language_instruction("de")
    assert (
        language_instruction("en") == ""
        and language_instruction(None) == ""
        and language_instruction("xx") == ""
    )


@pytest.mark.asyncio
async def test_french_and_german_articles_get_summaries_in_their_language(monkeypatch):
    from app.config import get_settings
    from app.services.summarization.lazy import ensure_llm_summaries

    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    prompts: dict[str, str] = {}

    async def fake_complete_json(self, *, model, system, user, max_tokens):
        code = "fr" if "in French" in user else "de" if "in German" in user else "en"
        prompts[code] = user
        return {
            "headline": SUMMARIES[code],
            "brief": SUMMARIES[code],
            "detailed": SUMMARIES[code],
            "depth_score": 0.5,
            "is_original_reporting": False,
            "has_citations": False,
            "topic_clusters": ["query planning"],
            "reading_time_minutes": 3,
        }

    items = {
        code: ContentItem(
            id=uuid.uuid4(),
            url=f"https://x.example/{code}",
            title=code,
            full_text=TEXTS[code],
            language=lang,
            summary_source="extractive",
        )
        for code, lang in (("en", "en"), ("fr", "fr-FR"), ("de", "de"))
    }
    with (
        patch("app.services.llm.client.LLMClient.complete_json", fake_complete_json),
        patch("app.services.summarization.summarizer._llm_summarizer", None),
    ):
        await ensure_llm_summaries(list(items.values()))

    assert set(prompts) == {"en", "fr", "de"}
    assert "The article is in" not in prompts["en"]  # English prompt unchanged
    for code in ("fr", "de"):
        assert items[code].summary_brief == SUMMARIES[code]
        assert items[code].summary_source == "llm"
        assert items[code].topic_clusters == ["query planning"]  # topics stay English
