"""Every API response carries the hardening headers."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_api_responses_forbid_sniffing_and_framing(client):
    for path in ("/health", "/api/v1/setup/status", "/does-not-exist"):
        resp = await client.get(path)
        assert resp.headers["x-content-type-options"] == "nosniff", path
        assert resp.headers["x-frame-options"] == "DENY", path
        assert resp.headers["referrer-policy"] == "no-referrer", path
