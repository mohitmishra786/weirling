"""Starter packs are distinct: a feed belongs to one pack, once."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

import pytest

from app.services.cold_start import pack_picker
from app.services.cold_start.starter_packs import PACKS_DIR, list_starter_packs


def _key(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return f"{host}{parsed.path.rstrip('/').lower()}?{parsed.query}"


def test_no_feed_is_in_two_packs_or_twice_in_one():
    owners = Counter(_key(f["feed_url"]) for p in list_starter_packs() for f in p.feeds)
    repeated = sorted(url for url, n in owners.items() if n > 1)
    assert repeated == []


def test_feed_names_are_unique_and_every_pack_states_its_scope():
    names = Counter(f["name"].lower() for p in list_starter_packs() for f in p.feeds)
    assert [n for n, c in names.items() if c > 1] == []
    for path in PACKS_DIR.glob("*.opml"):
        assert re.search(r"<!-- Scope: .+ -->", path.read_text()), path.name


class _Summarizer:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def complete_json(self, **_kwargs):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def summarizer(monkeypatch):
    def install(reply):
        fake = _Summarizer(reply)
        monkeypatch.setattr(
            "app.services.summarization.summarizer.get_llm_summarizer", lambda: fake
        )
        monkeypatch.setattr(pack_picker, "cache_get", _none)
        monkeypatch.setattr(pack_picker, "cache_set", _none)
        monkeypatch.setattr(
            "app.config.get_settings",
            lambda: type("S", (), {"llm_configured": True})(),
        )
        return fake

    return install


async def _none(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_llm_choice_is_used_and_unknown_ids_are_dropped(summarizer):
    summarizer({"packs": ["backend-distributed", "made-up", "databases-data"]})
    picked = await pack_picker.suggest_packs(["Distributed systems", "Message queues"])
    assert picked == ["backend-distributed", "databases-data"]


@pytest.mark.parametrize("reply", [None, {"packs": ["nope"]}, RuntimeError("down")])
@pytest.mark.asyncio
async def test_falls_back_to_the_taxonomy(summarizer, reply):
    summarizer(reply)
    picked = await pack_picker.suggest_packs(["PostgreSQL", "MVCC"])
    assert picked and picked[0] == "databases-data"


@pytest.mark.asyncio
async def test_a_slow_model_does_not_hold_up_onboarding(summarizer, monkeypatch):
    import asyncio

    summarizer({"packs": ["gamedev"]})

    async def slow(*_args):
        await asyncio.sleep(1)
        return ["gamedev"]

    monkeypatch.setattr(pack_picker, "_ask_llm", slow)
    picked = await pack_picker.suggest_packs(["PostgreSQL"], deadline=0.05)
    assert picked[0] == "databases-data"
