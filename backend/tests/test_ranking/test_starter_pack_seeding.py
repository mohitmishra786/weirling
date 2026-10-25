"""Starter packs replace the hard-coded seed list (#152)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.source import Source
from app.models.user import User
from app.services.cold_start.onboarding import _seed_starter_sources, _seeds_from_packs
from app.services.cold_start.starter_packs import list_starter_packs
from app.services.cold_start.taxonomy import _topics, packs_for_topics


def test_the_hard_coded_seed_list_is_gone():
    root = Path(__file__).resolve().parents[2] / "app" / "services" / "cold_start"
    assert not (root / "starter_sources.py").exists()


def test_every_taxonomy_pack_is_a_real_starter_pack():
    pack_ids = {p.id for p in list_starter_packs()}
    assert {t["pack"] for t in _topics()} <= pack_ids


def test_topics_map_to_their_packs():
    assert set(packs_for_topics(["Rust", "PostgreSQL"])[:2]) == {
        "programming-languages",
        "databases-data",
    }
    assert packs_for_topics(["Distributed systems", "System design"])[0] == "backend-distributed"
    assert packs_for_topics(["Kubernetes", "Observability", "Rust"])[0] == "devops-cloud"
    assert packs_for_topics(["zxqv"]) == []


def test_seeds_come_from_matching_packs_and_are_capped():
    pack_urls = {
        f["url"] for p in list_starter_packs() if p.id == "databases-data" for f in p.feeds
    }
    seeds = _seeds_from_packs(["PostgreSQL", "MVCC"], max_sources=5)
    assert len(seeds) == 5 and {s["url"] for s in seeds} <= pack_urls


@pytest.mark.asyncio
async def test_onboarding_seeds_only_when_the_user_picked_nothing(db_session):
    user = User(email=f"sp{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    assert await _seed_starter_sources(user.id, ["PostgreSQL"], db_session) > 0

    other = User(email=f"sp{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        Source(user_id=other.id, url="https://chosen.example", feed_url="https://chosen.example/f")
    )
    await db_session.flush()
    assert await _seed_starter_sources(other.id, ["PostgreSQL"], db_session) == 0
    rows = (await db_session.execute(select(Source).where(Source.user_id == other.id))).scalars()
    assert [s.url for s in rows] == ["https://chosen.example"]


@pytest.mark.asyncio
async def test_pack_list_marks_suggestions_and_subscriptions(client):
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": f"pk{uuid.uuid4().hex[:6]}@example.com", "password": "TestPass123!"},
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    packs = (
        await client.get("/api/v1/sources/starter-packs?topics=Rust,PostgreSQL", headers=headers)
    ).json()
    suggested = {p["id"] for p in packs if p["suggested"]}
    assert {"programming-languages", "databases-data"} <= suggested
    assert all(p["subscribed"] == 0 for p in packs)
