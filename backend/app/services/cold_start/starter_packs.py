"""Starter packs (CS-02): curated OPML bundles under app/data/starter_packs.

Each pack is a topic bundle of 10-25 feeds (URLs + titles only). A weekly CI
job runs scripts/check_starter_packs.py and fails when more than 10% of the
feeds are dead, keeping the >= 90%-live acceptance honest.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

PACKS_DIR = Path(__file__).parent.parent.parent / "data" / "starter_packs"


@dataclass(frozen=True)
class StarterPack:
    id: str
    title: str
    feeds: list[dict]
    # One line on what belongs in this pack, from the file's "Scope:" comment.
    description: str = ""


def _parse_opml(path: Path) -> StarterPack:
    root = ET.parse(path).getroot()
    body_outline = root.find("body/outline")
    title = (
        (body_outline.get("title") or body_outline.get("text") or path.stem)
        if body_outline is not None
        else path.stem
    )
    feeds: list[dict] = []
    if body_outline is not None:
        for outline in body_outline.findall("outline"):
            url = outline.get("xmlUrl") or outline.get("htmlUrl") or ""
            if not url:
                continue
            feeds.append(
                {
                    "name": outline.get("title") or outline.get("text") or url,
                    "url": url,
                    "feed_url": outline.get("xmlUrl") or url,
                }
            )
    scope = re.search(r"<!--\s*Scope:\s*(.+?)\.?\s+Each feed", path.read_text())
    description = scope.group(1).strip() if scope else ""
    return StarterPack(id=path.stem, title=title, feeds=feeds, description=description)


def list_starter_packs() -> list[StarterPack]:
    packs = [_parse_opml(path) for path in sorted(PACKS_DIR.glob("*.opml")) if path.is_file()]
    return [pack for pack in packs if pack.feeds]
