"""Emerging-topics detector (UX-10): burst detected, steady state not."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.content import ContentItem
from app.models.source import Source
from app.models.user import User
from app.services.digest.emerging import detect_emerging


def test_synthetic_burst_is_detected():
    # Topic "ai": baseline ~1 source/week; last 72h has 5 sources.
    recent = {"ai": 5}
    history = {"ai": [1.0, 1.0, 1.0, 1.0]}
    found = detect_emerging(recent, history)
    assert [e.topic for e in found] == ["ai"]
    assert found[0].recent_sources == 5
    assert found[0].z >= 2


def test_steady_state_is_not_detected():
    recent = {"ai": 1}
    history = {"ai": [1.0, 1.0, 1.0, 1.0]}
    assert detect_emerging(recent, history) == []


def test_flat_baseline_burst_is_an_outlier():
    """Zero variance does not hide a burst (std == 0 branch)."""
    found = detect_emerging({"rust": 3}, {"rust": [0.0, 0.0, 0.0, 0.0]})
    assert [e.topic for e in found] == ["rust"]
    assert found[0].z == float("inf")


def test_needs_two_baseline_windows_and_min_sources():
    # Explicitly incomplete history (one window) is not enough.
    assert detect_emerging({"ai": 5}, {"ai": [1.0]}) == []
    # One source in 72h: below min_sources even with flat baseline.
    assert detect_emerging({"ai": 1}, {"ai": [0.0, 0.0, 0.0, 0.0]}) == []


def test_topic_absent_from_history_uses_zero_windows():
    """A topic appearing only in the recent window IS emerging (CodeRabbit)."""
    found = detect_emerging({"ai": 5}, {})  # no history rows at all
    assert [e.topic for e in found] == ["ai"]
    assert found[0].z == float("inf")


@pytest.mark.asyncio
async def test_emerging_endpoint_finds_a_burst(
    client: AsyncClient, test_user_data: dict, db_session
):
    resp = await client.post("/api/v1/auth/register", json=test_user_data)
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    user = (
        await db_session.execute(select(User).where(User.email == test_user_data["email"]))
    ).scalar_one()

    now = datetime.now(UTC)
    # 5 distinct sources covering "ai" in the last 72h.
    for i in range(5):
        src = Source(user_id=user.id, url=f"https://burst-{i}.example/feed")
        db_session.add(src)
        await db_session.flush()
        db_session.add(
            ContentItem(
                source_id=src.id,
                url=f"https://burst-{i}.example/{i}",
                title=f"burst-{i}",
                topic_clusters=["ai"],
                fetched_at=now - timedelta(hours=i + 1),
            )
        )
    # Baseline weeks: quiet (one source in week 1 only, for "ai").
    quiet = Source(user_id=user.id, url="https://quiet.example/feed")
    db_session.add(quiet)
    await db_session.flush()
    db_session.add(
        ContentItem(
            source_id=quiet.id,
            url="https://quiet.example/a",
            title="quiet",
            topic_clusters=["ai"],
            fetched_at=now - timedelta(days=25),
        )
    )
    await db_session.commit()

    result = await client.get("/api/v1/digest/emerging", headers=headers)
    assert result.status_code == 200
    topics = [row["topic"] for row in result.json()]
    assert "ai" in topics


@pytest.mark.asyncio
async def test_emerging_endpoint_with_no_baseline_rows(
    client: AsyncClient, test_user_data: dict, db_session
):
    """A topic with no baseline rows of its own still emerges once the
    reader's feeds have history (a burst into a silent topic)."""
    resp = await client.post("/api/v1/auth/register", json=test_user_data)
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    user = (
        await db_session.execute(select(User).where(User.email == test_user_data["email"]))
    ).scalar_one()

    now = datetime.now(UTC)
    for i in range(3):
        src = Source(user_id=user.id, url=f"https://fresh-{i}.example/feed")
        db_session.add(src)
        await db_session.flush()
        db_session.add(
            ContentItem(
                source_id=src.id,
                url=f"https://fresh-{i}.example/{i}",
                title=f"fresh-{i}",
                topic_clusters=["wasm"],
                fetched_at=now - timedelta(hours=i + 1),
            )
        )
    # History from 20 days ago on another topic: the feeds are not brand new.
    db_session.add(
        ContentItem(
            source_id=src.id,
            url="https://fresh-0.example/old",
            title="old",
            topic_clusters=["databases"],
            fetched_at=now - timedelta(days=20),
        )
    )
    await db_session.commit()

    result = await client.get("/api/v1/digest/emerging", headers=headers)
    assert result.status_code == 200
    topics = [row["topic"] for row in result.json()]
    assert "wasm" in topics


@pytest.mark.asyncio
async def test_emerging_needs_history_and_ignores_other_readers_feeds(
    client: AsyncClient, test_user_data: dict, db_session
):
    """Brand-new feeds show nothing; another reader's burst never shows."""
    resp = await client.post("/api/v1/auth/register", json=test_user_data)
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    user = (
        await db_session.execute(select(User).where(User.email == test_user_data["email"]))
    ).scalar_one()
    other = User(email=f"other-{uuid.uuid4().hex[:6]}@example.com", hashed_password="x")
    db_session.add(other)
    await db_session.flush()
    now = datetime.now(UTC)
    mine = Source(user_id=user.id, url="https://mine.example/feed")
    db_session.add(mine)
    await db_session.flush()
    db_session.add(
        ContentItem(
            source_id=mine.id,
            url="https://mine.example/1",
            title="m",
            topic_clusters=["rust"],
            fetched_at=now - timedelta(hours=2),
        )
    )
    for i in range(3):
        src = Source(user_id=other.id, url=f"https://theirs-{i}.example/feed")
        db_session.add(src)
        await db_session.flush()
        db_session.add(
            ContentItem(
                source_id=src.id,
                url=f"https://theirs-{i}.example/a",
                title="t",
                topic_clusters=["agents"],
                fetched_at=now - timedelta(hours=i + 1),
            )
        )
    await db_session.commit()
    result = await client.get("/api/v1/digest/emerging", headers=headers)
    assert result.status_code == 200
    assert result.json() == []
