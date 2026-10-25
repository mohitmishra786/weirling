"""Extraction cascade: feed text, trafilatura, readability, then rendered HTML.

Listing, collection, and product pages are extracted but marked unrankable.
A visible failure is ``method="failed"`` with empty text. Playwright is the
caller's last step: pass the rendered HTML in and label it ``playwright``.
"""

from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

MIN_USEFUL_WORDS = 40
MIN_FEED_WORDS = 80
UNRANKABLE = {"listing", "collection", "product"}


@dataclass
class Extraction:
    text: str
    method: str
    confidence: float
    page_type: str

    @property
    def rankable(self) -> bool:
        return self.page_type not in UNRANKABLE and self.method != "failed"


def looks_js_only(html: str) -> bool:
    if not html:
        return False
    text = _plain(html)
    if len(text.split()) >= 20:
        return False
    lowered = html.lower()
    return 'id="__next"' in lowered or 'id="app"' in lowered or "<script" in lowered


def page_type_of(html: str) -> str:
    if not html:
        return "unknown"
    lowered = html.lower()
    if (
        'og:type" content="product"' in lowered
        or "schema.org/product" in lowered
        or "add to cart" in lowered
    ):
        return "product"
    if lowered.count("<article") >= 4 or 'class="listing"' in lowered:
        return "listing"
    if 'class="collection"' in lowered or (lowered.count("<h2") >= 6 and lowered.count("<p") < 3):
        return "collection"
    if "<article" in lowered or "<main" in lowered or lowered.count("<p") >= 2:
        return "article"
    return "unknown"


# MediaWiki's citation apparatus (#319): the reference list and the inline
# [1] markers. Extracted as text they made Wikipedia articles ~35% citations.
# Generic footnotes and data tables are content and stay.
_CITATION_SELECTORS = (
    ".mw-references-wrap",
    "ol.references",
    ".reflist",
    "sup.reference",
    ".mw-cite-backlink",
)
_CITATION_HINTS = ("mw-references-wrap", 'class="reference"', "reflist", "mw-cite-backlink")


def strip_citations(html: str) -> str:
    """Remove MediaWiki reference lists and citation markers, if present."""
    if not html or not any(hint in html for hint in _CITATION_HINTS):
        return html
    soup = BeautifulSoup(html, "html.parser")
    for selector in _CITATION_SELECTORS:
        for element in soup.select(selector):
            element.decompose()
    return str(soup)


def extract_html(
    html: str,
    url: str = "",
    *,
    feed_text: str | None = None,
    rendered_html: str | None = None,
) -> Extraction:
    kind = page_type_of(html or rendered_html or "")
    html = strip_citations(html)
    rendered_html = strip_citations(rendered_html) if rendered_html else rendered_html
    feed = (feed_text or "").strip()
    if len(feed.split()) >= MIN_FEED_WORDS:
        return Extraction(feed, "feed", 0.95, "article" if kind == "unknown" else kind)

    extracted = _trafilatura(html, url)
    if len(extracted.split()) >= MIN_USEFUL_WORDS:
        return Extraction(extracted, "trafilatura", 0.8, kind)

    readable = _readability(html)
    if len(readable.split()) >= MIN_USEFUL_WORDS:
        return Extraction(readable, "readability", 0.6, kind)

    if rendered_html:
        rendered_text = _trafilatura(rendered_html, url) or _readability(rendered_html)
        if len(rendered_text.split()) >= MIN_USEFUL_WORDS:
            return Extraction(rendered_text, "playwright", 0.5, page_type_of(rendered_html))

    return Extraction("", "failed", 0.0, kind)


_AGGREGATOR_SITES = {
    "news.ycombinator.com": "Hacker News",
    "lobste.rs": "Lobsters",
}


def aggregator_stub(html: str | None) -> str | None:
    """A link aggregator's entry (hnrss.org, Lobsters) carries no article, only
    "Article URL: … Comments URL: … Points: … # Comments: …" markup. Return one
    clean line pointing at the discussion instead, or None for a real body."""
    if not html or "Comments URL" not in html or "Article URL" not in html:
        return None
    from html import escape

    text = _plain(html)
    if len(text.split()) > 60:  # a real body that happens to quote the format
        return None
    import re

    comments = re.search(r"Comments URL:\s*(https?://\S+)", text)
    points = re.search(r"Points:\s*(\d+)", text)
    count = re.search(r"# Comments:\s*(\d+)", text)
    if not comments:
        return None
    url = comments.group(1)
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").removeprefix("www.")
    site = _AGGREGATOR_SITES.get(host, host)
    stats = ", ".join(
        part
        for part in (
            f"{points.group(1)} points" if points else "",
            f"{count.group(1)} comments" if count else "",
        )
        if part
    )
    label = f"{stats} on {site}" if stats else f"Discussion on {site}"
    return f'<p>Discussion: <a href="{escape(url)}">{escape(label)}</a></p>'


def from_feed_text(text: str | None) -> Extraction:
    body = (text or "").strip()
    words = len(body.split())
    if words >= MIN_FEED_WORDS:
        return Extraction(body, "feed", 0.9, "article")
    if words > 0:
        return Extraction(body, "feed", 0.4, "article")
    return Extraction("", "failed", 0.0, "unknown")


def _trafilatura(html: str, url: str) -> str:
    if not html:
        return ""
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            url=url or None,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception:
        return ""
    return (text or "").strip()


def _readability(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    node = soup.find("article") or soup.find("main") or soup.body or soup
    parts = [p.get_text(" ", strip=True) for p in node.find_all("p")]
    if not parts:
        return node.get_text(" ", strip=True)
    return " ".join(part for part in parts if part).strip()


def _plain(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
