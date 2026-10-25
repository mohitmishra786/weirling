"""SSRF (Server-Side Request Forgery) protection for server-side URL fetches.

Any URL that originates from user input (a source URL, a feed to autodiscover,
a page to scrape) is fetched by *our* server. Without guarding, an attacker can
add `http://169.254.169.254/latest/meta-data/` (cloud metadata) or an internal
host as a "source" and exfiltrate credentials or reach internal services.

This module validates that a URL's host resolves only to public, routable IPs
and that redirects can't smuggle the request onto a private target. It is a hard
constraint on the scraping/ingestion path (audit 06-2, 08); it can be disabled
per-deployment for single-tenant homelab use via `ssrf_protection_enabled`.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

_ALLOWED_SCHEMES = {"http", "https"}

# Hostnames that never resolve to something we should reach, independent of DNS.
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",  # GCP metadata
    "metadata",
}


class UnsafeURLError(ValueError):
    """Raised when a URL fails SSRF validation (private target / bad scheme)."""


def _blocked_ip_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a human reason if `ip` must not be fetched, else None."""
    # IPv4-mapped IPv6 (::ffff:a.b.c.d) — unwrap and re-check the embedded v4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _blocked_ip_reason(ip.ipv4_mapped)
    if ip.is_loopback:
        return "loopback"
    if ip.is_private:
        return "private"
    if ip.is_link_local:
        return "link-local"  # includes 169.254.0.0/16 cloud-metadata range
    if ip.is_reserved:
        return "reserved"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    # Catch-all for anything not globally routable (e.g. 100.64/10 CGNAT, 0/8).
    if not ip.is_global:
        return "non-global"
    return None


def _default_resolver(host: str) -> list[str]:
    """Resolve `host` to the IP strings it points at (A + AAAA), IPv4 first.

    The fetcher connects to these in order. Many hosts (Railway, plenty of
    VPS and container networks) have no outbound IPv6, so an AAAA address
    first meant "All connection attempts failed" for dual-stack sites
    (Cloudflare, Google, AWS) at random: a set has no stable order."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return prefer_ipv4(info[4][0] for info in infos)


def prefer_ipv4(ips: Iterable[str]) -> list[str]:
    """Unique IPs in their original order, IPv4 before IPv6."""
    unique = list(dict.fromkeys(ips))
    return [ip for ip in unique if ":" not in ip] + [ip for ip in unique if ":" in ip]


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an IP literal, including decimal and hex forms of an IPv4 address."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if host.isdigit():
        try:
            return ipaddress.ip_address(int(host))
        except ValueError:
            return None
    if host.lower().startswith("0x"):
        try:
            return ipaddress.ip_address(int(host, 16))
        except ValueError:
            return None
    return None


def validate_public_url(
    url: str,
    *,
    resolver: Callable[[str], Iterable[str]] = _default_resolver,
    allow_private_hosts: frozenset[str] = frozenset(),
) -> list[str]:
    """Raise `UnsafeURLError` unless `url` is an http(s) URL whose host resolves
    exclusively to public, globally-routable IP addresses.

    Returns the validated IP addresses, so the caller can connect to exactly
    those (#165): resolving again at connect time would let a DNS-rebinding
    answer swap in a private address after validation.

    `allow_private_hosts` exempts exact hostnames from the private-address
    check (homelab sidecar, #164); everything else still applies.

    `resolver` is injectable so tests can exercise host validation without real
    DNS. When `ssrf_protection_enabled` is False this is a no-op returning [].
    """
    if not settings.ssrf_protection_enabled:
        return []

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme {scheme!r} not allowed (only http/https)")
    if parsed.username or parsed.password:
        raise UnsafeURLError("credentials in URLs are not allowed")

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    host = host.rstrip(".")

    lowered = host.lower()
    exempt = lowered in allow_private_hosts
    if not exempt and (lowered in _BLOCKED_HOSTNAMES or lowered.endswith(".localhost")):
        raise UnsafeURLError(f"host {host!r} is not permitted")

    # If the host is already an IP literal, check it directly (no DNS).
    literal_ip = _literal_ip(lowered)
    if literal_ip is not None:
        reason = None if exempt else _blocked_ip_reason(literal_ip)
        if reason:
            raise UnsafeURLError(f"IP {host} is {reason}")
        return [str(literal_ip)]

    # Hostname: resolve and ensure *every* answer is public (guards round-robin
    # and DNS setups that return one public + one private record).
    try:
        addresses = list(resolver(host))
    except OSError as e:
        raise UnsafeURLError(f"could not resolve host {host!r}: {e}") from e
    if not addresses:
        raise UnsafeURLError(f"host {host!r} did not resolve")
    validated: list[str] = []
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        reason = None if exempt else _blocked_ip_reason(ip)
        if reason:
            raise UnsafeURLError(f"host {host!r} resolves to {reason} address {addr}")
        validated.append(str(ip))
    if not validated:
        raise UnsafeURLError(f"host {host!r} did not resolve to an IP address")
    return validated


def _pinned_request(
    url: str, ip: str, headers: dict[str, str] | None
) -> tuple[str, dict[str, str], dict]:
    """Rewrite `url` to connect to `ip` while keeping the original identity.

    The Host header carries the original name (virtual hosting) and, for
    HTTPS, the SNI name is the original host, so the certificate is still
    verified against it (#165).
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    ip_host = f"[{ip}]" if ":" in ip else ip
    netloc = ip_host if parsed.port is None else f"{ip_host}:{parsed.port}"
    target = parsed._replace(netloc=netloc).geturl()
    pinned_headers = dict(headers or {})
    host_header = host if parsed.port is None else f"{host}:{parsed.port}"
    pinned_headers.setdefault("Host", host_header)
    extensions = {"sni_hostname": host} if parsed.scheme.lower() == "https" else {}
    return target, pinned_headers, extensions


def is_public_url(url: str) -> bool:
    """Boolean convenience wrapper around `validate_public_url`."""
    try:
        validate_public_url(url)
        return True
    except UnsafeURLError:
        return False


# Cap a single fetched body. Feeds and articles above this are rejected rather
# than buffered without limit.
DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_REDIRECTS = 5


async def _read_capped(resp: httpx.Response, max_bytes: int) -> bytes:
    declared = resp.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise UnsafeURLError(f"response exceeded {max_bytes} bytes")
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise UnsafeURLError(f"response exceeded {max_bytes} bytes")
    return bytes(buf)


async def safe_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    require_https: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    resolver: Callable[[str], Iterable[str]] = _default_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
    allow_private_hosts: frozenset[str] = frozenset(),
    pin_ip: bool | None = None,
) -> httpx.Response:
    """Fetch `url`, validating every hop before connecting.

    This is the only function that should open a connection to a user-supplied
    URL. It enforces the scheme allowlist, blocks credentials in the URL,
    re-checks DNS (or IP literals) on every redirect, caps the body, and applies
    a timeout. Each hop connects to the IP that was validated, never to a
    fresh DNS answer (`pin_ip`; default on for real network transports, #165).
    `resolver` and `transport` are injectable for tests.
    """
    if pin_ip is None:
        pin_ip = transport is None
    current = url
    saw_redirect = False
    all_permanent = True
    client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
    )
    try:
        for _ in range(max_redirects + 1):
            # Cleartext guard: when the caller carries private payload (e.g.
            # webhook exports), every hop must be TLS (CodeRabbit CWE-319).
            # Checked before DNS so cleartext is rejected without lookups.
            if require_https and not current.lower().startswith("https://"):
                raise UnsafeURLError(f"cleartext hop rejected for secure fetch: {current!r}")
            ips = validate_public_url(
                current, resolver=resolver, allow_private_hosts=allow_private_hosts
            )
            # Connect to each validated address in turn (IPv4 first): one
            # unreachable family or address must not fail the whole fetch.
            candidates = prefer_ipv4(ips) if pin_ip and ips else [None]
            async with _open_first(client, method, current, candidates, headers, content) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    saw_redirect = True
                    if resp.status_code != 301:
                        all_permanent = False
                    # Resolve against the logical URL, not the pinned IP URL.
                    current = urljoin(current, resp.headers["location"])
                    continue
                body = await _read_capped(resp, max_bytes)
                # aiter_bytes already decoded Content-Encoding. Copying that
                # header onto the decoded body makes httpx decode it again.
                forwarded = [
                    (key, value)
                    for key, value in resp.headers.multi_items()
                    if key.lower()
                    not in {"content-encoding", "content-length", "transfer-encoding"}
                ]
                if saw_redirect and all_permanent and current.rstrip("/") != url.rstrip("/"):
                    forwarded.append(("x-weirling-permanent-url", current))
                return httpx.Response(
                    status_code=resp.status_code,
                    headers=forwarded,
                    content=body,
                    request=resp.request,
                )
        raise UnsafeURLError(f"too many redirects fetching {url!r}")
    finally:
        await client.aclose()


class _open_first:
    """Async context manager: stream `url` through the first candidate IP that
    accepts the connection. Only connection failures move on to the next IP;
    any HTTP response, even an error status, is final."""

    def __init__(self, client, method, url, candidates, headers, content):
        self._args = (client, method, url, candidates, headers, content)
        self._cm = None

    async def __aenter__(self):
        client, method, url, candidates, headers, content = self._args
        last_error: Exception | None = None
        for ip in candidates:
            target, hop_headers, extensions = url, headers, {}
            if ip is not None:
                target, hop_headers, extensions = _pinned_request(url, ip, headers)
            cm = client.stream(
                method, target, headers=hop_headers, content=content, extensions=extensions
            )
            try:
                resp = await cm.__aenter__()
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_error = e
                continue
            self._cm = cm
            return resp
        assert last_error is not None
        raise last_error

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)


async def safe_get(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    **kwargs,
) -> httpx.Response:
    """GET `url` through `safe_fetch`.

    `client` is accepted for older call sites and ignored: the shared fetcher
    owns the connection, the timeout, and the redirect policy.
    """
    del client
    timeout = float(kwargs.pop("timeout", DEFAULT_TIMEOUT_SECONDS))
    headers = kwargs.pop("headers", None)
    max_bytes = int(kwargs.pop("max_bytes", DEFAULT_MAX_BYTES))
    resolver = kwargs.pop("resolver", _default_resolver)
    transport = kwargs.pop("transport", None)
    if kwargs:
        raise TypeError(f"unexpected safe_get arguments: {sorted(kwargs)}")
    return await safe_fetch(
        url,
        headers=headers,
        timeout=timeout,
        max_redirects=max_redirects,
        max_bytes=max_bytes,
        resolver=resolver,
        transport=transport,
    )
