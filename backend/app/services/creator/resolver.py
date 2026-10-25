from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.creator import Creator, CreatorPlatform
from app.utils.logging import get_logger, sanitize_log
from app.utils.ssrf import UnsafeURLError, safe_fetch, validate_public_url

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Platform domain → canonical platform name.
# ---------------------------------------------------------------------------
PLATFORM_DOMAINS = {
    "substack.com": "substack",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "medium.com": "medium",
    "linkedin.com": "linkedin",
    "spotify.com": "podcast",
    "open.spotify.com": "podcast",
    "podcasts.apple.com": "podcast",
    "feeds.simplecast.com": "podcast",
    "rss.com": "podcast",
    "reddit.com": "reddit",
    "old.reddit.com": "reddit",
    "new.reddit.com": "reddit",
    "bsky.app": "bluesky",
    "github.com": "github",
    "mastodon.social": "mastodon",
    "fosstodon.org": "mastodon",
    "hachyderm.io": "mastodon",
}

# ---------------------------------------------------------------------------
# Single source of truth for what each platform can do.
#
# `tracking_tier`:
#   - "fully_tracked": reliable public RSS / feed exists; we ingest new posts.
#   - "best_effort":   a feed may exist (e.g. podcast via iTunes lookup) but is
#                      not guaranteed; surfacing content depends on resolution.
#   - "unsupported":   no reliable public feed (closed platform API). We store
#                      the profile but warn the user it won't auto-track.
# `display_label`: short UI label for the platform chip.
# ---------------------------------------------------------------------------
PLATFORM_CAPABILITIES: dict[str, dict[str, str]] = {
    "substack": {"tracking_tier": "fully_tracked", "display_label": "Substack"},
    "medium": {"tracking_tier": "fully_tracked", "display_label": "Medium"},
    "youtube": {"tracking_tier": "fully_tracked", "display_label": "YouTube"},
    "reddit": {
        "tracking_tier": "unsupported",
        "display_label": "Reddit",
        "reason": "Reddit shuts down RSS on 13 Nov 2026",
    },
    "bluesky": {"tracking_tier": "fully_tracked", "display_label": "Bluesky"},
    "mastodon": {"tracking_tier": "fully_tracked", "display_label": "Mastodon"},
    "github": {"tracking_tier": "fully_tracked", "display_label": "GitHub"},
    "blog": {"tracking_tier": "best_effort", "display_label": "Blog / Site"},
    "podcast": {"tracking_tier": "best_effort", "display_label": "Podcast"},
    "twitter": {"tracking_tier": "unsupported", "display_label": "Twitter / X"},
    "linkedin": {"tracking_tier": "unsupported", "display_label": "LinkedIn"},
}


def get_platform_tier(platform: str) -> str:
    """Return the tracking tier for a platform; defaults to best_effort."""
    return PLATFORM_CAPABILITIES.get(platform, {}).get("tracking_tier", "best_effort")


@dataclass
class CreatorResolutionResult:
    creator: Creator
    platforms_discovered: int
    warning: str | None = None


def _strip_www(host: str) -> str:
    """Remove a leading 'www.' prefix correctly.

    str.lstrip('www.') is a common bug — it strips any *combination* of the
    characters w, . so 'wwww.x' over-strips. We want to remove the exact prefix.
    """
    if host.startswith("www."):
        return host[4:]
    return host


def _detect_platform(url: str) -> str:
    parsed = urlparse(url)
    host = _strip_www(parsed.netloc.lower())
    for domain, platform in PLATFORM_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return "blog"


async def _fetch_page(url: str) -> str | None:
    # SSRF guard: creator URLs come from user input, so validate the host and
    # every redirect hop before fetching (audit 06-2).
    try:
        validate_public_url(url)
    except UnsafeURLError as e:
        logger.warning(f"Blocked creator fetch for unsafe URL {sanitize_log(url)}: {e}")
        return None
    try:
        resp = await safe_fetch(
            url,
            timeout=15,
            headers={"User-Agent": "Weirling/1.0 (+https://weirling.com/bot)"},
        )
        return resp.text
    except UnsafeURLError as e:
        logger.warning(f"Blocked creator fetch for unsafe URL {sanitize_log(url)}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch {sanitize_log(url)}: {e}")
        return None


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return match.group(1).strip() if match else "Unknown Creator"


async def _lookup_podcast_feed(show_name: str) -> str | None:
    """Use the free, no-auth iTunes Search API to resolve a podcast RSS feed.

    Returns the first podcast result's feedUrl, if any. This is the standard
    mechanism for discovering podcast RSS from a show name (Spotify shows have
    no public RSS of their own, but the same show almost always exists in
    Apple's directory with a real feedUrl).
    """
    if not show_name:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://itunes.apple.com/search",
                params={"term": show_name, "entity": "podcast", "limit": 1},
                headers={"User-Agent": "Weirling/1.0"},
            )
            data = resp.json()
            results = data.get("results") or []
            if results:
                feed = results[0].get("feedUrl")
                if feed:
                    try:
                        validate_public_url(feed)
                    except UnsafeURLError:
                        logger.warning("iTunes feed URL failed the public-URL check")
                        return None
                    return feed
    except Exception as e:
        logger.warning(f"iTunes podcast lookup failed for '{sanitize_log(show_name)}': {e}")
    return None


def _autodiscover_feed_url(
    platform: str, profile_url: str, html: str
) -> tuple[str | None, str | None]:
    """Resolve the feed URL for a platform.

    Returns (feed_url, warning). For best-effort platforms that fail to resolve,
    a warning explains the limitation so the UI can surface it honestly.
    """
    parsed = urlparse(profile_url)
    path_segments = [s for s in parsed.path.split("/") if s]

    if platform == "substack":
        host = _strip_www(parsed.netloc.lower())
        if host != "substack.com":
            return f"https://{host}/feed", None
        # substack.com/@handle is a profile page; the posts are on the
        # handle's subdomain. Other substack.com links (redirects, the app,
        # a post's share link) don't name a publication.
        handle = next((s[1:] for s in path_segments if s.startswith("@") and len(s) > 1), "")
        if handle:
            return f"https://{handle}.substack.com/feed", None
        return None, "Use the publication's address (name.substack.com) to follow it."
    if platform == "medium":
        # Two valid Medium URL forms:
        #   1. https://medium.com/@author/post  → feed: medium.com/feed/@author
        #   2. https://author.medium.com        → feed: author.medium.com/feed (subdomain)
        host = parsed.netloc
        if host.endswith(".medium.com") and host != "www.medium.com":
            # Subdomain publication — derive the feed directly (mirrors substack).
            return f"https://{host}/feed", None
        # Otherwise look for the @-prefixed username in the path.
        username = next((s.lstrip("@") for s in path_segments if s.startswith("@")), "")
        if not username:
            return None, "Could not extract a Medium username; feed not discovered."
        return f"https://medium.com/feed/@{username}", None
    if platform == "youtube":
        # YouTube embeds the channel id in several places. Match the canonical
        # channelId attribute forms, including <meta content="UC..." itemprop="channelId">.
        channel_match = re.search(r'(?:channel_id|channelId)["\'\s]*[:=]["\'\s]*([UC][\w-]+)', html)
        if not channel_match:
            # <meta itemprop="channelId" content="UC...">
            meta_match = re.search(
                r'itemprop=["\']channelId["\'][^>]*content=["\']([UC][\w-]+)["\']',
                html,
                re.IGNORECASE,
            )
            channel_match = meta_match or re.search(
                r'content=["\']([UC][\w-]+)["\'][^>]*itemprop=["\']channelId["\']',
                html,
                re.IGNORECASE,
            )
        if channel_match:
            cid = channel_match.group(1)
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", None
        return None, "Could not extract the YouTube channel id; feed not discovered."
    if platform == "reddit":
        from app.services.ingestion.schedule import reddit_notice

        return None, reddit_notice()
    if platform in {"bluesky", "mastodon", "github"}:
        from app.services.ingestion.discover import platform_candidates

        matches = platform_candidates(profile_url, html)
        if matches:
            return matches[0].url, None
        return None, f"Could not build a {platform} feed URL."
    if platform == "podcast":
        # Podcast feed discovery is deferred to the async caller (it needs an
        # HTTP lookup); here we only signal that none was found synchronously.
        return None, None
    if platform in ("twitter", "linkedin"):
        return None, (
            f"{PLATFORM_CAPABILITIES[platform]['display_label']} provides no public RSS feed. "
            "Weirling cannot automatically track new posts from this profile. "
            "Consider adding the creator's Substack, newsletter, blog, Bluesky, or Mastodon instead."
        )

    # Generic blog: try RSS autodiscovery from the page's <link> tags.
    feed_match = re.search(
        r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*href=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if feed_match:
        href = feed_match.group(1)
        if href.startswith("http"):
            return href, None
        return urljoin(profile_url, href), None
    return None, "No RSS feed autodiscovered; content tracking may be limited."


async def resolve_creator(
    name_or_url: str, user_id: uuid.UUID, session: AsyncSession
) -> CreatorResolutionResult:
    is_url = name_or_url.startswith("http")
    platforms_data: list[dict] = []
    display_name = name_or_url
    warning: str | None = None

    if is_url:
        primary_platform = _detect_platform(name_or_url)

        html = await _fetch_page(name_or_url)
        fetch_failed = False
        if html:
            display_name = _extract_title(html)
        else:
            fetch_failed = True

        if primary_platform == "github":
            from app.services.ingestion.discover import confirmed_platform_feed

            feed_url = await confirmed_platform_feed(name_or_url, html or "")
            feed_warning = None if feed_url else "Could not confirm a GitHub feed."
        elif primary_platform == "blog":
            # The same ranked, confirmed discovery as adding a source: <link>
            # tags, feed links in the page, then common paths under the
            # page's own folder and the site root.
            from app.services.ingestion.discover import best_feed_url

            feed_url = await best_feed_url(name_or_url, html=html or "")
            feed_warning = (
                None if feed_url else "No RSS feed found; content tracking may be limited."
            )
        else:
            feed_url, feed_warning = _autodiscover_feed_url(
                primary_platform, name_or_url, html or ""
            )

        # Best-effort podcast lookup via iTunes Search API.
        if primary_platform == "podcast" and not feed_url:
            feed_url = await _lookup_podcast_feed(display_name)
            if not feed_url:
                feed_warning = (
                    "Could not resolve a podcast RSS feed for this show. "
                    "You can still add the podcast's direct RSS URL if you have it."
                )

        # Only warn about a failed profile fetch if we ALSO failed to resolve a
        # feed — for platforms whose feed construction doesn't need the HTML
        # (substack, reddit, medium subdomain), a transient fetch failure still
        # yields a working feed and shouldn't show a scary warning.
        if feed_url:
            warning = None
        elif fetch_failed and not feed_warning:
            warning = f"Could not fetch profile page: {name_or_url}"
        else:
            warning = feed_warning or warning

        platforms_data.append(
            {
                "platform": primary_platform,
                "url": name_or_url,
                "feed_url": feed_url,
                "is_verified": feed_url is not None,
            }
        )

        # Discover other platform links from bio.
        if html:
            additional = _find_additional_platforms(html, name_or_url)
            platforms_data.extend(additional)
    else:
        display_name = name_or_url
        warning = "Name-only creator added without URL. No feed discovered."

    # Create Creator
    creator = Creator(
        user_id=user_id,
        display_name=display_name[:200],
        resolved=len(platforms_data) > 0 and any(p["feed_url"] for p in platforms_data),
    )
    session.add(creator)
    await session.flush()

    # Create CreatorPlatform records
    for pd in platforms_data:
        cp = CreatorPlatform(
            creator_id=creator.id,
            platform=pd["platform"],
            platform_url=pd["url"],
            feed_url=pd.get("feed_url"),
            is_verified=pd.get("is_verified", False),
        )
        session.add(cp)
    await session.flush()

    if not any(p.get("feed_url") for p in platforms_data) and not warning:
        warning = "No RSS feed discovered. Content tracking may be limited."

    logger.info(f"Resolved creator {creator.id}: {len(platforms_data)} platforms")
    return CreatorResolutionResult(
        creator=creator,
        platforms_discovered=len(platforms_data),
        warning=warning,
    )


def _find_additional_platforms(html: str, source_url: str) -> list[dict]:
    """Scan bio/about section for links to other platforms."""
    additional: list[dict] = []
    source_host = urlparse(source_url).netloc

    link_pattern = re.compile(r'href=["\']((https?://)[^"\']+)["\']', re.IGNORECASE)
    for match in link_pattern.finditer(html):
        url = match.group(1)
        parsed = urlparse(url)
        host = _strip_www(parsed.netloc.lower())
        if host == _strip_www(source_host.lower()):
            continue
        platform = None
        for domain, pname in PLATFORM_DOMAINS.items():
            if host == domain or host.endswith("." + domain):
                platform = pname
                break
        if platform and not any(p["url"] == url for p in additional):
            feed_url, _ = _autodiscover_feed_url(platform, url, "")
            additional.append(
                {
                    "platform": platform,
                    "url": url,
                    "feed_url": feed_url,
                    "is_verified": feed_url is not None,
                }
            )
        if len(additional) >= 4:
            break
    return additional
