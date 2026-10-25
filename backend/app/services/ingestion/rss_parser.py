from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser

from app.config import get_settings
from app.services.ingestion.extract import aggregator_stub
from app.utils.logging import get_logger, sanitize_log
from app.utils.sanitize import sanitize_stored_html
from app.utils.ssrf import UnsafeURLError, safe_fetch, validate_public_url
from app.utils.xml_safety import UnsafeXMLError, assert_xml_safe

# Honest identity plus compression. httpx decodes gzip/deflate always, and
# brotli when the brotli package is installed.
_FEED_UA = "Weirling/1.0 (+https://weirling.com/bot)"
_FEED_HEADERS = {
    "User-Agent": _FEED_UA,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

logger = get_logger(__name__)


@dataclass
class FeedFetchResult:
    """One conditional fetch. `not_modified` means HTTP 304: do not parse or ingest."""

    items: list[RawContentItem]
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None
    permanent_url: str | None = None


@dataclass
class _HttpFeed:
    status: int
    body: bytes | None
    etag: str | None
    last_modified: str | None
    permanent_url: str | None = None


def _conditional_headers(etag: str | None, last_modified: str | None) -> dict[str, str]:
    headers = dict(_FEED_HEADERS)
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


@dataclass
class RawContentItem:
    url: str
    title: str
    author: str | None = None
    published_at: datetime | None = None
    full_text: str | None = None
    word_count: int | None = None
    source_feed_url: str | None = None
    creator_platform_id: str | None = None
    transcript_url: str | None = None
    guid: str | None = None
    simhash: str | None = None
    extraction_method: str | None = None
    extraction_confidence: float | None = None
    page_type: str | None = None
    language: str | None = None
    lead_image_url: str | None = None
    paywalled: bool = False
    rankable: bool = True
    origin: str = "followed"
    reading_time_minutes: int | None = None


def _count_words(text: str) -> int:
    return len(text.split()) if text else 0


def _extract_text(entry: feedparser.FeedParserDict) -> str:
    # Feed content/summary is raw third-party HTML; strip executable markup
    # before it is stored (audit 06-7 defense in depth).
    if hasattr(entry, "content") and entry.content:
        return sanitize_stored_html(entry.content[0].get("value", ""))
    if hasattr(entry, "summary"):
        return sanitize_stored_html(entry.summary or "")
    return ""


def _parse_date(entry: feedparser.FeedParserDict) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
        if value:
            try:
                import calendar

                # struct_time from feedparser is in UTC; convert to aware datetime
                ts = calendar.timegm(value)
                return datetime.fromtimestamp(ts, tz=UTC)
            except Exception:
                pass
    return None


async def _autodiscover_feed(page_url: str) -> str | None:
    """Best feed URL for a site, or None when the only option is to scrape it."""
    try:
        validate_public_url(page_url)
    except UnsafeURLError as e:
        logger.warning(
            "Blocked feed autodiscovery for unsafe URL %s: %s", sanitize_log(page_url), e
        )
        return None
    from app.services.ingestion.discover import best_feed_url

    async def _fetch(url: str) -> str | None:
        try:
            resp = await safe_fetch(
                url, headers=_FEED_HEADERS, timeout=3, max_bytes=get_settings().feed_max_bytes
            )
        except Exception as e:
            logger.debug("Feed probe failed for %s: %s", sanitize_log(url), e)
            return None
        if resp.status_code != 200:
            return None
        return resp.text

    return await best_feed_url(page_url, fetch=_fetch)


def _validators_from(resp) -> tuple[str | None, str | None]:
    etag = resp.headers.get("etag")
    last_modified = resp.headers.get("last-modified")
    return (etag.strip() if etag else None, last_modified.strip() if last_modified else None)


async def _load_feed_bytes(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> _HttpFeed | None:
    """Fetch a feed through safe_fetch. feedparser must not fetch the URL itself.

    A 304 is returned as status 304 with no body. Callers must not parse it.
    """
    try:
        validate_public_url(url)
    except UnsafeURLError as e:
        logger.warning("Blocked feed fetch for unsafe URL %s: %s", sanitize_log(url), e)
        return None
    try:
        resp = await safe_fetch(
            url,
            headers=_conditional_headers(etag, last_modified),
            timeout=20,
            max_bytes=get_settings().feed_max_bytes,
        )
    except UnsafeURLError as e:
        logger.warning("Blocked feed fetch for %s: %s", sanitize_log(url), e)
        return None
    except Exception as e:
        logger.error("Failed to fetch feed %s: %s", sanitize_log(url), e)
        return None
    response_etag, response_modified = _validators_from(resp)
    if resp.status_code == 304:
        return _HttpFeed(304, None, response_etag or etag, response_modified or last_modified)
    if resp.status_code >= 400:
        logger.warning("Feed %s returned HTTP %s", sanitize_log(url), resp.status_code)
        return _HttpFeed(resp.status_code, None, response_etag, response_modified)
    try:
        body = assert_xml_safe(resp.content, max_bytes=get_settings().feed_max_bytes)
    except UnsafeXMLError as e:
        logger.warning("Rejected unsafe feed XML from %s: %s", sanitize_log(url), e)
        return None
    permanent = resp.headers.get("x-weirling-permanent-url")
    return _HttpFeed(resp.status_code, body, response_etag, response_modified, permanent)


_PODCAST_NS = "https://podcastindex.org/namespace/1.0"
_TRANSCRIPT_TAG = re.compile(
    r"<(?:(?P<prefix>[\w.-]+):)?transcript\b[^>]*\burl=[\"'](?P<url>[^\"']+)[\"']",
    re.IGNORECASE,
)


def _transcript_urls_from_xml(xml: str) -> list[str | None]:
    """First transcript URL in each `<item>`, before feedparser keeps only the last tag."""
    parts = re.split(r"<item\b", xml, flags=re.IGNORECASE)
    found: list[str | None] = []
    for part in parts[1:]:
        urls = [match.group("url") for match in _TRANSCRIPT_TAG.finditer(part)]
        found.append(urls[0] if urls else None)
    return found


def _transcript_url(entry, feed=None) -> str | None:
    """Fallback when the raw XML was not available. Honors any prefix of the podcast namespace."""
    prefixes = ["podcast"]
    namespaces = getattr(feed, "namespaces", None) or {}
    if hasattr(namespaces, "items"):
        for prefix, uri in namespaces.items():
            if uri == _PODCAST_NS and prefix not in prefixes:
                prefixes.append(prefix)
    for prefix in prefixes:
        value = None
        key = f"{prefix}_transcript"
        if hasattr(entry, "get"):
            value = entry.get(key)
        if value is None:
            value = getattr(entry, key, None)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                url = item.get("url") or item.get("href")
                if url:
                    return str(url)
    return None


def _items_from_feed(feed, source_url: str, raw_xml: str | None = None) -> list[RawContentItem]:
    from_xml = _transcript_urls_from_xml(raw_xml) if raw_xml else []
    items: list[RawContentItem] = []
    for index, entry in enumerate(feed.entries):
        link = getattr(entry, "link", None)
        title = getattr(entry, "title", "Untitled")
        if not link:
            continue
        text = _extract_text(entry)
        # Hacker News / Lobsters entries hold only link metadata, not the
        # article: keep a clean pointer to the discussion instead of markup.
        text = aggregator_stub(text, source_url) or text
        word_count = _count_words(text) if text else None
        guid = getattr(entry, "id", None) or getattr(entry, "guid", None)
        items.append(
            RawContentItem(
                url=link,
                title=title,
                author=getattr(entry, "author", None),
                published_at=_parse_date(entry),
                full_text=text or None,
                word_count=word_count,
                source_feed_url=source_url,
                transcript_url=from_xml[index]
                if index < len(from_xml)
                else _transcript_url(entry, feed),
                guid=str(guid) if guid else None,
            )
        )
    return items


async def fetch_feed(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedFetchResult:
    """Fetch and parse a feed. A 304 returns no items and does not parse."""
    try:
        primary = await _load_feed_bytes(url, etag=etag, last_modified=last_modified)
        if primary is not None and primary.status == 304:
            return FeedFetchResult(
                items=[],
                not_modified=True,
                etag=primary.etag,
                last_modified=primary.last_modified,
                status_code=304,
                permanent_url=primary.permanent_url,
            )
        if (
            primary is not None
            and primary.status in {410, 429}
            or (primary is not None and primary.status >= 500)
        ):
            return FeedFetchResult(
                items=[],
                etag=primary.etag,
                last_modified=primary.last_modified,
                status_code=primary.status,
                permanent_url=primary.permanent_url,
            )
        feed = feedparser.parse(primary.body) if primary and primary.body else None
        # A site homepage is not a feed. A 304 must not reach this branch.
        if feed is None or not feed.entries:
            discovered = await _autodiscover_feed(url)
            if discovered and discovered != url:
                secondary = await _load_feed_bytes(discovered)
                if secondary and secondary.status == 304:
                    return FeedFetchResult(
                        items=[],
                        not_modified=True,
                        etag=secondary.etag,
                        last_modified=secondary.last_modified,
                    )
                if secondary and secondary.body:
                    feed = feedparser.parse(secondary.body)
                    if feed is not None:
                        return FeedFetchResult(
                            items=_items_from_feed(
                                feed, discovered, secondary.body.decode("utf-8", errors="replace")
                            ),
                            etag=secondary.etag,
                            last_modified=secondary.last_modified,
                        )
        if feed is None:
            return FeedFetchResult(items=[], etag=primary.etag if primary else None)
        return FeedFetchResult(
            items=_items_from_feed(
                feed,
                url,
                primary.body.decode("utf-8", errors="replace")
                if primary and primary.body
                else None,
            ),
            etag=primary.etag if primary else None,
            last_modified=primary.last_modified if primary else None,
            permanent_url=primary.permanent_url if primary else None,
        )
    except Exception as e:
        logger.error(f"Failed to parse feed {url}: {e}")
        return FeedFetchResult(items=[])


async def parse_feed(url: str) -> list[RawContentItem]:
    return (await fetch_feed(url)).items
