"""The Ingestion page: the instance owner reads it from the app (#381)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.config import get_settings


async def _signup(client):
    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"ing{uuid.uuid4().hex[:8]}@example.com",
            "password": "TestPass123!",
            "accept_terms": True,
        },
    )
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return uuid.UUID(me["id"]), headers


@pytest.mark.asyncio
async def test_owner_session_or_metrics_token_reads_ingestion(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "metrics_token", "t" * 40)
    owner_id, owner = await _signup(client)
    _, other = await _signup(client)
    with patch(
        "app.services.instance_health.is_instance_owner",
        AsyncMock(side_effect=lambda session, user: user.id == owner_id),
    ):
        assert (await client.get("/api/v1/metrics/ingestion", headers=other)).status_code == 401
        body = (await client.get("/api/v1/metrics/ingestion", headers=owner)).json()
    assert {"sources", "fetch_success_rate", "extraction_by_method", "storage"} <= body.keys()

    token = await client.get("/api/v1/metrics/ingestion", headers={"X-Metrics-Token": "t" * 40})
    assert token.status_code == 200
    assert (await client.get("/api/v1/metrics/ingestion")).status_code == 401
