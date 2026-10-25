"""safe_fetch connects to the validated IP (#165) and the homelab exemption (#164)."""

from __future__ import annotations

import httpx
import pytest

from app.utils.ssrf import UnsafeURLError, safe_fetch, validate_public_url

PUBLIC = "93.184.216.34"


class _Recorder(httpx.AsyncBaseTransport):
    def __init__(self, responses: dict[str, httpx.Response] | None = None):
        self.requests: list[httpx.Request] = []
        self.responses = responses or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.get(request.url.path, httpx.Response(200, text="ok"))


class _CountingResolver:
    """First answer public, every later answer private: the rebinding attack."""

    def __init__(self):
        self.calls = 0

    def __call__(self, host: str):
        self.calls += 1
        return [PUBLIC] if self.calls == 1 else ["127.0.0.1"]


@pytest.mark.asyncio
async def test_connects_to_the_validated_ip_with_original_host_and_sni():
    transport = _Recorder()
    resolver = _CountingResolver()

    resp = await safe_fetch(
        "https://blog.example.com/feed?x=1", resolver=resolver, transport=transport, pin_ip=True
    )

    assert resp.status_code == 200
    req = transport.requests[0]
    assert req.url.host == PUBLIC  # the connection goes to the IP we checked
    assert req.url.path == "/feed" and req.url.query == b"x=1"
    assert req.headers["host"] == "blog.example.com"
    assert req.extensions.get("sni_hostname") == "blog.example.com"
    assert resolver.calls == 1  # no second lookup that could be rebound


@pytest.mark.asyncio
async def test_relative_redirect_is_resolved_against_the_hostname_and_revalidated():
    def resolver(host: str):
        return {"blog.example.com": [PUBLIC], "cdn.example.net": ["8.8.8.8"]}[host]

    transport = _Recorder(
        {
            "/start": httpx.Response(302, headers={"location": "/next"}),
            "/next": httpx.Response(301, headers={"location": "https://cdn.example.net/final"}),
            "/final": httpx.Response(200, text="done"),
        }
    )
    resp = await safe_fetch(
        "https://blog.example.com/start", resolver=resolver, transport=transport, pin_ip=True
    )

    assert resp.text == "done"
    hosts = [(r.url.host, r.headers["host"]) for r in transport.requests]
    assert hosts == [
        (PUBLIC, "blog.example.com"),
        (PUBLIC, "blog.example.com"),
        ("8.8.8.8", "cdn.example.net"),
    ]


@pytest.mark.asyncio
async def test_redirect_to_a_private_host_is_still_blocked_when_pinned():
    def resolver(host: str):
        return {"blog.example.com": [PUBLIC], "internal.example.com": ["10.0.0.5"]}[host]

    transport = _Recorder(
        {"/start": httpx.Response(302, headers={"location": "http://internal.example.com/admin"})}
    )
    with pytest.raises(UnsafeURLError, match="private"):
        await safe_fetch(
            "https://blog.example.com/start", resolver=resolver, transport=transport, pin_ip=True
        )
    assert len(transport.requests) == 1


def test_validate_returns_the_checked_addresses():
    assert validate_public_url("https://x.example.com", resolver=lambda h: [PUBLIC]) == [PUBLIC]
    assert validate_public_url("http://8.8.4.4/") == ["8.8.4.4"]


def test_private_exemption_is_exact_host_only():
    resolver = {"nas.home": ["192.168.1.10"], "evil.home": ["192.168.1.11"]}.__getitem__
    allowed = frozenset({"nas.home"})
    assert validate_public_url(
        "http://nas.home:8080/v1/feeds", resolver=resolver, allow_private_hosts=allowed
    ) == ["192.168.1.10"]
    with pytest.raises(UnsafeURLError):
        validate_public_url("http://evil.home/", resolver=resolver, allow_private_hosts=allowed)
    with pytest.raises(UnsafeURLError):
        validate_public_url("http://nas.home/", resolver=resolver)


@pytest.mark.parametrize(
    ("deployment", "flag", "expected"),
    [
        (
            "selfhost",
            True,
            {"require_https": False, "allow_private_hosts": frozenset({"nas.home"})},
        ),
        ("selfhost", False, {"require_https": True}),
        ("hosted", True, {"require_https": True}),  # never on the hosted service
    ],
)
def test_sidecar_network_policy(monkeypatch, deployment, flag, expected):
    from app.config import get_settings
    from app.services.integrations.sidecar import _network_policy

    monkeypatch.setattr(get_settings(), "deployment_mode", deployment)
    monkeypatch.setattr(get_settings(), "sidecar_allow_private_http", flag)
    assert _network_policy("http://NAS.home:8080/v1/feeds") == expected


def test_ipv4_addresses_come_first_and_order_is_stable():
    from app.utils.ssrf import prefer_ipv4

    ips = ["2606:4700::6810:84e5", "104.16.132.229", "2606:4700::6810:85e5", "104.16.133.229"]
    assert prefer_ipv4(ips) == [
        "104.16.132.229",
        "104.16.133.229",
        "2606:4700::6810:84e5",
        "2606:4700::6810:85e5",
    ]
    assert prefer_ipv4(["1.1.1.1", "1.1.1.1"]) == ["1.1.1.1"]


class _Unreachable(httpx.AsyncBaseTransport):
    """Refuses connections to some IPs, the way a host without IPv6 does."""

    def __init__(self, dead: set[str]):
        self.dead = dead
        self.tried: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.tried.append(request.url.host)
        if request.url.host in self.dead:
            raise httpx.ConnectError("All connection attempts failed", request=request)
        return httpx.Response(200, text="ok")


@pytest.mark.asyncio
async def test_connects_over_ipv4_when_the_host_also_has_ipv6():
    transport = _Unreachable(dead={"2606:4700::6810:84e5"})
    resp = await safe_fetch(
        "https://blog.example.com/feed",
        resolver=lambda h: ["2606:4700::6810:84e5", PUBLIC],
        transport=transport,
        pin_ip=True,
    )
    assert resp.status_code == 200
    assert transport.tried == [PUBLIC]


@pytest.mark.asyncio
async def test_falls_back_to_the_next_validated_ip_and_fails_only_when_all_do():
    transport = _Unreachable(dead={PUBLIC})
    resp = await safe_fetch(
        "https://blog.example.com/feed",
        resolver=lambda h: [PUBLIC, "8.8.8.8"],
        transport=transport,
        pin_ip=True,
    )
    assert resp.status_code == 200
    assert transport.tried == [PUBLIC, "8.8.8.8"]

    everything_down = _Unreachable(dead={PUBLIC, "8.8.8.8"})
    with pytest.raises(httpx.ConnectError):
        await safe_fetch(
            "https://blog.example.com/feed",
            resolver=lambda h: [PUBLIC, "8.8.8.8"],
            transport=everything_down,
            pin_ip=True,
        )
