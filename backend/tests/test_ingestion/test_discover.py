"""Twenty site shapes. HTML is a short fixture, not a copied page."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.ingestion.discover import discover_feed_candidates

FEED = "<rss><channel><title>Feed</title></channel></rss>"


def _link(href: str, type_first: bool = True) -> str:
    if type_first:
        tag = f'<link rel="alternate" type="application/rss+xml" href="{href}"/>'
    else:
        tag = f'<link rel="alternate" href="{href}" type="application/atom+xml"/>'
    return f"<!DOCTYPE html><html><head>{tag}</head><body></body></html>"


CASES = [
    (
        "wordpress",
        "https://blog.example.com/2024/01/hello",
        _link("/feed/"),
        "https://blog.example.com/feed/",
        "link",
    ),
    (
        "wordpress.com",
        "https://name.wordpress.com/",
        _link("https://name.wordpress.com/feed/"),
        "https://name.wordpress.com/feed/",
        "link",
    ),
    (
        "ghost",
        "https://ghost.example.com/post",
        _link("/rss/"),
        "https://ghost.example.com/rss/",
        "link",
    ),
    (
        "ghost-href-first",
        "https://ghost.example.com/",
        _link("/rss/", type_first=False),
        "https://ghost.example.com/rss/",
        "link",
    ),
    (
        "substack",
        "https://name.substack.com/p/hello",
        "",
        "https://name.substack.com/feed",
        "platform",
    ),
    (
        "substack-custom",
        "https://newsletter.example.com/",
        _link("https://newsletter.example.com/feed"),
        "https://newsletter.example.com/feed",
        "link",
    ),
    (
        "blogger",
        "https://name.blogspot.com/2024/01/post.html",
        "",
        "https://name.blogspot.com/feeds/posts/default",
        "platform",
    ),
    (
        "hugo",
        "https://hugo.example.com/posts/one",
        _link("/index.xml"),
        "https://hugo.example.com/index.xml",
        "link",
    ),
    (
        "jekyll",
        "https://jekyll.example.com/2024/01/01/post.html",
        _link("/feed.xml"),
        "https://jekyll.example.com/feed.xml",
        "link",
    ),
    (
        "medium-user",
        "https://medium.com/@octocat/a-post",
        "",
        "https://medium.com/feed/@octocat",
        "platform",
    ),
    (
        "medium-publication",
        "https://medium.com/some-publication",
        "",
        "https://medium.com/feed/some-publication",
        "platform",
    ),
    (
        "medium-subdomain",
        "https://pub.medium.com/story",
        "",
        "https://pub.medium.com/feed",
        "platform",
    ),
    (
        "youtube",
        "https://www.youtube.com/@Google",
        '<html><meta itemprop="channelId" content="UCxxxxxxxxxxxxxxxx"/></html>',
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxxxxxxxxxxxxx",
        "platform",
    ),
    (
        "mastodon-fosstodon",
        "https://fosstodon.org/@alice",
        "",
        "https://fosstodon.org/@alice.rss",
        "platform",
    ),
    (
        "github",
        "https://github.com/owner/repo",
        "",
        "https://github.com/owner/repo/releases.atom",
        "platform",
    ),
    (
        "arxiv",
        "https://arxiv.org/list/cs.LG/recent",
        "",
        "https://rss.arxiv.org/rss/cs.LG",
        "platform",
    ),
    (
        "hugo-probe",
        "https://docs.example.com/guide",
        "<html><body>no links</body></html>",
        "https://docs.example.com/index.xml",
        "well_known",
    ),
    (
        "jekyll-probe",
        "https://notes.example.com/",
        "<html></html>",
        "https://notes.example.com/atom.xml",
        "well_known",
    ),
    (
        "js-only",
        "https://app.example.com/article",
        '<html><body><div id="root"></div><noscript>enable js</noscript></body></html>',
        "https://app.example.com/article",
        "scrape",
    ),
    (
        "rsshub-x",
        "https://x.com/someuser",
        "<html></html>",
        "https://rsshub.example/twitter/user/someuser",
        "rsshub",
    ),
]


def _probe_factory(kind: str, expected: str):
    async def _fetch(url: str) -> str | None:
        if url.split("#")[0] == expected.split("#")[0]:
            return FEED
        if kind == "hugo-probe" and url.endswith("/index.xml"):
            return FEED
        if kind == "jekyll-probe" and url.endswith("/atom.xml"):
            return FEED
        return None

    return _fetch


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "page", "html", "expected", "method"), CASES)
async def test_fixture_site_resolves(kind, page, html, expected, method, monkeypatch):
    if kind == "rsshub-x":
        monkeypatch.setattr(get_settings(), "rsshub_base_url", "https://rsshub.example")
    else:
        monkeypatch.setattr(get_settings(), "rsshub_base_url", "")
    found = await discover_feed_candidates(page, html=html, fetch=_probe_factory(kind, expected))
    assert found, kind
    assert found[0].url == expected
    assert found[0].method == method


@pytest.mark.asyncio
async def test_reddit_recipe_removed_does_not_resolve():
    """Reddit RSS shuts down on 2026-11-13; platform_candidates returns no feed for Reddit URLs (#107)."""
    from app.services.ingestion.discover import platform_candidates

    assert platform_candidates("https://www.reddit.com/r/MachineLearning", "") == []
    assert platform_candidates("https://www.reddit.com/r/top", "") == []
    assert platform_candidates("https://www.reddit.com/user/spez", "") == []


def test_link_candidates_with_multi_value_and_list_attributes():
    from app.services.ingestion.discover import link_candidates

    html = """<!DOCTYPE html>
    <html>
    <head>
      <link rel="alternate extra-rel" type="application/rss+xml" href="/rss.xml" />
      <link rel="alternate" type="application/atom+xml" href="https://example.com/atom.xml" />
    </head>
    <body></body>
    </html>"""
    candidates = link_candidates("https://example.com/blog", html)
    urls = [c.url for c in candidates]
    assert "https://example.com/rss.xml" in urls
    assert "https://example.com/atom.xml" in urls


def _only(feed_url: str):
    seen: list[str] = []

    async def _fetch(url: str) -> str | None:
        seen.append(url)
        return FEED if url == feed_url else None

    return _fetch, seen


@pytest.mark.asyncio
async def test_blog_in_a_subfolder_is_found_without_a_link_tag(monkeypatch):
    """A GitHub Pages project site (user.github.io/Blog/) has its feed under
    the folder, not the host root, and themes often skip <link rel=alternate>."""
    monkeypatch.setattr(get_settings(), "rsshub_base_url", "")
    fetch, seen = _only("https://user.github.io/Blog/feed.xml")
    html = "<html><head></head><body><a href='/Blog/about'>About</a></body></html>"
    found = await discover_feed_candidates("https://user.github.io/Blog/", html=html, fetch=fetch)
    assert found[0].url == "https://user.github.io/Blog/feed.xml"
    assert found[0].method == "well_known"
    # The folder is tried before the site root.
    assert seen.index("https://user.github.io/Blog/feed.xml") < len(seen)
    assert "https://user.github.io/feed.xml" not in seen


@pytest.mark.asyncio
async def test_feed_link_in_the_page_body_is_used_once_confirmed(monkeypatch):
    monkeypatch.setattr(get_settings(), "rsshub_base_url", "")
    fetch, _ = _only("https://user.github.io/Blog/feed.xml")
    html = (
        "<html><body><a href='/Blog/sitemap.xml'>map</a>"
        "<a href='/Blog/feed.xml'><i class='rss'></i></a></body></html>"
    )
    found = await discover_feed_candidates(
        "https://user.github.io/Blog/posts/hello/", html=html, fetch=fetch
    )
    assert found[0].url == "https://user.github.io/Blog/feed.xml"
    assert found[0].method == "anchor"


def test_substack_profile_url_maps_to_the_publication():
    from app.services.creator.resolver import _autodiscover_feed_url
    from app.services.ingestion.discover import platform_candidates

    assert _autodiscover_feed_url("substack", "https://substack.com/@lenny", "") == (
        "https://lenny.substack.com/feed",
        None,
    )
    feed, warning = _autodiscover_feed_url("substack", "https://substack.com/redirect/abc", "")
    assert feed is None and warning
    assert [c.url for c in platform_candidates("https://substack.com/@lenny", "")] == [
        "https://lenny.substack.com/feed"
    ]
    assert platform_candidates("https://substack.com/inbox", "") == []


def test_well_known_order_folder_root_then_bare_segment():
    from app.services.ingestion.discover import _well_known_targets

    project = _well_known_targets("https://user.github.io/Blog/posts/x/")
    assert project[0] == "https://user.github.io/Blog/feed.xml"
    assert not any("/Blog/posts/" in t for t in project)
    page = _well_known_targets("https://docs.example.com/guide")
    assert page[0].startswith("https://docs.example.com/") and "/guide/" in page[-1]
    assert page.index("https://docs.example.com/index.xml") < page.index(
        "https://docs.example.com/guide/index.xml"
    )
