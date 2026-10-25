"""Ranked feed discovery for a page URL.

Order, highest rank first: HTML `<link rel="alternate">`, a platform recipe,
a feed-looking `<a href>` in the page, a well-known path that actually looks
like a feed (under the page's own folder first, so a blog at
example.github.io/blog/ is found, then the site root), an RSSHub route when
`RSSHUB_BASE_URL` is set, then the page itself as a scrape candidate.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.utils.logging import get_logger, sanitize_log
from app.utils.ssrf import UnsafeURLError, safe_fetch, validate_public_url

logger = get_logger(__name__)

FetchText = Callable[[str], Awaitable[str | None]]

_WELL_KNOWN = (
    "/feed",
    "/feed/",
    "/rss",
    "/rss/",
    "/atom.xml",
    "/index.xml",
    "/feed.xml",
    "/rss.xml",
    "/feed/rss",
    "/feeds/posts/default",
    "/?feed=rss2",
)

# Tried under a sub-folder before the full list runs at the site root.
_WELL_KNOWN_SUBPATH = ("feed.xml", "atom.xml", "index.xml", "rss.xml", "feed/")
_FEED_SUFFIXES = (".xml", ".rss", ".atom", ".rdf")
_FEED_WORDS = {"feed", "feeds", "rss", "atom"}
_MAX_ANCHORS = 4

_RANK = {
    "link": 100,
    "platform": 80,
    "anchor": 70,
    "well_known": 60,
    "rsshub": 40,
    "scrape": 10,
}


@dataclass(frozen=True)
class FeedCandidate:
    url: str
    method: str
    rank: int
    tier: str = "fully_tracked"


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _segments(url: str) -> list[str]:
    return [part for part in urlparse(url).path.split("/") if part]


def link_candidates(page_url: str, html: str) -> list[FeedCandidate]:
    if not html:
        return []
    head = html.lstrip()[:80].lower()
    if head.startswith("<?xml") or head.startswith("<rss") or head.startswith("<feed"):
        return []
    soup = BeautifulSoup(html, "lxml")
    found: list[FeedCandidate] = []
    seen: set[str] = set()
    for tag in soup.find_all("link"):
        rel = " ".join(tag.get_attribute_list("rel")).lower()
        kind = " ".join(tag.get_attribute_list("type")).lower()
        raw_href = tag.get("href")
        if not raw_href or "alternate" not in rel:
            continue
        href = (
            raw_href
            if isinstance(raw_href, str)
            else str(raw_href[0])
            if isinstance(raw_href, list) and raw_href
            else str(raw_href)
        )
        if not href:
            continue
        if "rss" not in kind and "atom" not in kind:
            continue
        absolute = href if href.startswith("http") else urljoin(page_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append(FeedCandidate(absolute, "link", _RANK["link"]))
    return found


def anchor_candidates(page_url: str, html: str) -> list[FeedCandidate]:
    """`<a href>`s that look like feed links ("RSS" icons in a sidebar or
    footer). Many static-site themes link the feed only this way. Each one is
    confirmed by fetching it before it is used."""
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    found: list[FeedCandidate] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        href = str(tag.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        path = parsed.path.lower().rstrip("/")
        last = path.rsplit("/", 1)[-1]
        stem = last.rsplit(".", 1)[0]
        looks = last.endswith(_FEED_SUFFIXES) and (
            stem in _FEED_WORDS or stem in {"index", "posts", "all"} or "feed" in stem
        )
        if not looks and last not in _FEED_WORDS:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append(FeedCandidate(absolute, "anchor", _RANK["anchor"]))
        if len(found) >= _MAX_ANCHORS:
            break
    return found


def _hostname(url: str) -> str:
    """Parsed hostname, without a leading www. CodeQL treats this as a real host."""
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _is_host(url: str, domain: str) -> bool:
    host = _hostname(url)
    return host == domain or host.endswith("." + domain)


def platform_candidates(page_url: str, html: str) -> list[FeedCandidate]:
    parsed = urlparse(page_url)
    host = _hostname(page_url)
    parts = _segments(page_url)
    found: list[FeedCandidate] = []

    def add(url: str) -> None:
        found.append(FeedCandidate(url, "platform", _RANK["platform"]))

    if _is_host(page_url, "substack.com"):
        if host == "substack.com":
            # substack.com/@handle is a profile; its posts live on the subdomain.
            handle = next((p[1:] for p in parts if p.startswith("@") and len(p) > 1), "")
            if handle:
                add(f"https://{handle}.substack.com/feed")
        else:
            add(f"https://{parsed.netloc}/feed")
    if _is_host(page_url, "blogspot.com") or _is_host(page_url, "blogger.com"):
        add(urljoin(_origin(page_url) + "/", "feeds/posts/default"))
    if _is_host(page_url, "medium.com"):
        if host not in {"medium.com"} and host.endswith(".medium.com"):
            add(f"https://{parsed.netloc}/feed")
        else:
            username = next((part for part in parts if part.startswith("@")), "")
            publication = parts[0] if parts and not parts[0].startswith("@") else ""
            if username:
                add(f"https://medium.com/feed/{username}")
            elif publication and publication not in {"feed", "tag"}:
                add(f"https://medium.com/feed/{publication}")
    if _is_host(page_url, "bsky.app") and len(parts) >= 2 and parts[0] == "profile":
        add(f"https://bsky.app/profile/{parts[1]}/rss")
    mastodon_hosts = ("mastodon.social", "fosstodon.org", "hachyderm.io")
    if (
        parts
        and parts[0].startswith("@")
        and any(_is_host(page_url, name) for name in mastodon_hosts)
    ):
        add(f"https://{parsed.hostname}/@{parts[0].lstrip('@')}.rss")
    if host in {"youtube.com", "youtu.be", "m.youtube.com"}:
        match = re.search(
            r"(?:channel_id|channelId)[\"'\s]*[:=][\"'\s]*([UC][\w-]{8,})", html or ""
        )
        if not match:
            match = re.search(
                r"itemprop=[\"']channelId[\"'][^>]*content=[\"']([UC][\w-]{8,})[\"']",
                html or "",
                re.IGNORECASE,
            )
        if match:
            add(f"https://www.youtube.com/feeds/videos.xml?channel_id={match.group(1)}")
    if host == "github.com" and len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        add(f"https://github.com/{owner}/{repo}/releases.atom")
        add(f"https://github.com/{owner}/{repo}/commits.atom")
    if host == "arxiv.org":
        category = ""
        if parts[:1] in (["list"], ["rss"]) and len(parts) >= 2:
            category = parts[1]
        if category:
            add(f"https://rss.arxiv.org/rss/{category}")
    return found


def rsshub_candidates(page_url: str) -> list[FeedCandidate]:
    from app.services.ingestion.rsshub import TIER, candidate_urls

    return [
        FeedCandidate(url, "rsshub", _RANK["rsshub"], tier=TIER) for url in candidate_urls(page_url)
    ]


def _looks_like_feed(text: str) -> bool:
    """True only when the XML root is rss, atom feed, or rss 1.0 rdf.

    A substring check treats an HTML page that contains `<feedback>` as a feed.
    """
    if not text or not text.strip():
        return False
    from defusedxml import ElementTree as DefusedElementTree

    try:
        root = DefusedElementTree.fromstring(text.encode("utf-8", errors="replace"))
    except Exception:
        return False
    tag = root.tag.lower()
    if tag.startswith("{"):
        tag = tag.split("}", 1)[1]
    return tag in {"rss", "feed", "rdf"}


async def _confirmed(candidates: list[FeedCandidate], getter: FetchText) -> list[FeedCandidate]:
    """Keep a recipe only after its URL returns feed XML. At most two probes at once."""
    if not candidates:
        return []
    gate = asyncio.Semaphore(2)

    async def _one(candidate: FeedCandidate) -> tuple[FeedCandidate, str | None]:
        async with gate:
            return candidate, await getter(candidate.url)

    checked = await asyncio.gather(*(_one(candidate) for candidate in candidates))
    return [candidate for candidate, body in checked if body and _looks_like_feed(body)]


def _well_known_targets(page_url: str) -> list[str]:
    """Where to look for a feed, in order.

    A site published under a folder (user.github.io/Blog/) keeps its feed
    there, and the host root may belong to someone else's site, so the first
    folder is tried before the root. Only a path ending in "/", or the parent
    of a page, counts as a folder: /guide is a page, and its site feed is at
    the root. A single segment without the slash (/Blog) is tried last.
    """
    origin = _origin(page_url)
    parts = _segments(page_url)
    path = urlparse(page_url).path
    folders = parts if path.endswith("/") else parts[:-1]
    first: list[str] = []
    if folders:
        first.append(f"{origin}/{folders[0]}/")
    last: list[str] = []
    if not folders and len(parts) == 1 and "." not in parts[0]:
        last.append(f"{origin}/{parts[0]}/")
    targets = [urljoin(base, name) for base in first for name in _WELL_KNOWN_SUBPATH]
    targets += [urljoin(origin + "/", name.lstrip("/")) for name in _WELL_KNOWN]
    targets += [urljoin(base, name) for base in last for name in _WELL_KNOWN_SUBPATH]
    unique: list[str] = []
    for target in targets:
        if target not in unique:
            unique.append(target)
    return unique


async def _first_well_known(page_url: str, getter: FetchText) -> FeedCandidate | None:
    targets = _well_known_targets(page_url)
    gate = asyncio.Semaphore(2)

    async def _one(index: int, target: str) -> tuple[int, str, str | None]:
        async with gate:
            return index, target, await getter(target)

    for start in range(0, len(targets), 2):
        batch = await asyncio.gather(
            *(_one(index, target) for index, target in enumerate(targets[start : start + 2], start))
        )
        for _index, target, body in sorted(batch):
            if body and _looks_like_feed(body):
                return FeedCandidate(target, "well_known", _RANK["well_known"])
    return None


async def _fetch_text(url: str) -> str | None:
    from app.config import get_settings

    try:
        validate_public_url(url)
        # Some feeds are several MB (danluu.com: 6.6 MB); a smaller cap would
        # fail to confirm them.
        resp = await safe_fetch(url, timeout=10, max_bytes=get_settings().feed_max_bytes)
    except (UnsafeURLError, Exception) as e:
        logger.debug("discovery fetch failed for %s: %s", sanitize_log(url), e)
        return None
    if resp.status_code != 200:
        return None
    return resp.text


async def discover_feed_candidates(
    page_url: str,
    *,
    html: str | None = None,
    fetch: FetchText | None = None,
) -> list[FeedCandidate]:
    """Return candidates best-first. The page is fetched when `html` is omitted."""
    getter = fetch or _fetch_text
    if html is None:
        html = await getter(page_url) or ""

    found: list[FeedCandidate] = []
    proposed = [*link_candidates(page_url, html), *platform_candidates(page_url, html)]
    found.extend(await _confirmed(proposed, getter))
    if not found:
        found.extend((await _confirmed(anchor_candidates(page_url, html), getter))[:1])

    if not found:
        hit = await _first_well_known(page_url, getter)
        if hit is not None:
            found.append(hit)
        found.extend(await _confirmed(rsshub_candidates(page_url), getter))

    if not any(item.method != "scrape" for item in found):
        found.append(FeedCandidate(page_url, "scrape", _RANK["scrape"]))

    unique: list[FeedCandidate] = []
    seen: set[str] = set()
    for item in sorted(found, key=lambda candidate: candidate.rank, reverse=True):
        if item.url in seen:
            continue
        seen.add(item.url)
        unique.append(item)
    return unique


async def confirmed_platform_feed(
    page_url: str, html: str, *, fetch: FetchText | None = None
) -> str | None:
    """First platform recipe whose URL actually returns a feed."""
    confirmed = await _confirmed(platform_candidates(page_url, html), fetch or _fetch_text)
    return confirmed[0].url if confirmed else None


async def best_feed_url(
    page_url: str, *, html: str | None = None, fetch: FetchText | None = None
) -> str | None:
    """Highest candidate that is an actual feed. Scrape is not a feed URL."""
    for candidate in await discover_feed_candidates(page_url, html=html, fetch=fetch):
        if candidate.method != "scrape":
            return candidate.url
    return None
