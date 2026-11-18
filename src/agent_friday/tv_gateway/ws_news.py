"""The News workspace's latest stories, read for a phone (PW-G2, adapter 5).

Reads the desktop's own /api/news/archive in process (GET only): the stories Friday has already fetched and kept on the
PC. It never asks the PC to fetch anything from the web, so opening News on a phone costs the PC no network. A story
reaches the phone as a row (headline, outlet, category, when) and an item (the outlet's own summary and where it came
from). Every row and item says `taint: "outside:news"`: the words are an outlet's, not Friday's. No article link
leaves, only the outlet's name. tests/unit/test_tv_ws_news.py holds the rules.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from agent_friday.tv_gateway import ws_blocks
from agent_friday.tv_gateway.ws_adapter import Missing, Unavailable, desk_json, digest as _digest

PREFIX = "news:"
KIND = "story"
TAINT = "outside:news"
FACETS = {"category": lambda row: row["state"] or ""}
LIST_DAYS = 3
FIND_DAYS = 7                                  # a story a phone opened a few days ago can still be opened
STORIES_MAX = 200                              # the desktop's own page limit
REF = re.compile(r"^news:(?P<id>[A-Za-z0-9_\-]{1,64})$")


def _stories(days: int) -> list[dict]:
    first = (date.today() - timedelta(days=days - 1)).isoformat()
    data = desk_json("/api/news/archive", {"date_from": first, "limit": STORIES_MAX})
    items = data.get("items")
    if not isinstance(items, list):
        raise Unavailable("news")
    return [a for a in items if isinstance(a, dict)]


def _row(story: dict, sensitivity: str) -> Optional[dict]:
    ident = str(story.get("id") or "")
    if not REF.match(PREFIX + ident):
        return None
    return {"ref": PREFIX + ident, "kind": KIND, "title": ws_blocks.line(story.get("title"), 160) or "(untitled)",
            "line": ws_blocks.line(story.get("source") or story.get("domain"), 60), "state": ws_blocks.line(story.get("category"), 40) or None,
            "when": str(story.get("published_at") or story.get("fetched_at") or "")[:25] or None, "thumb": None,
            "lives_on": "pc", "sensitivity": sensitivity, "taint": TAINT}


def pages(sensitivity: str) -> list[dict]:
    """The last three days of kept stories, newest first."""
    rows = [r for r in (_row(s, sensitivity) for s in _stories(LIST_DAYS)) if r]
    rows.sort(key=lambda r: (r["when"] or "", r["ref"]), reverse=True)
    return rows


def facets(rows: list[dict]) -> list[dict]:
    counts: dict = {}
    for r in rows:
        name = FACETS["category"](r)
        counts[name] = counts.get(name, 0) + 1
    return [{"name": "category", "values": [{"v": k, "n": n} for k, n in sorted(counts.items())]}]


def rev(rows: list[dict]) -> str:
    return "n_%d_%s" % (len(rows), _digest(*[(r["ref"], r["when"], r["title"]) for r in rows]))


def search(q: str, sensitivity: str) -> list[dict]:
    needle = q.strip().lower()
    found = []
    for story in _stories(FIND_DAYS):
        row = _row(story, sensitivity)
        words = ws_blocks.line(story.get("snippet"), 400)
        if row and (needle in row["title"].lower() or needle in words.lower()):
            found.append(dict(row, snippet=words[:160] or None))
    found.sort(key=lambda r: (r["when"] or "", r["ref"]), reverse=True)
    return found


def item(ref: str, sensitivity: str, offset: int, limit: int) -> dict:
    m = REF.match(ref or "")
    if not m:
        raise Missing(ref)
    story = next((s for s in _stories(FIND_DAYS) if str(s.get("id")) == m.group("id")), None)
    row = _row(story, sensitivity) if story else None
    if row is None:
        raise Missing(ref)
    outlet = row["line"]
    meta = [{"k": "Outlet", "v": outlet}] if outlet else []
    if row["state"]:
        meta.append({"k": "Category", "v": row["state"]})
    if row["when"]:
        meta.append({"k": "Published", "v": row["when"]})
    parsed = ws_blocks.parse(str(story.get("snippet") or ""))
    window = parsed.blocks[offset:offset + limit]
    return {"rev": "s_" + _digest(ref, row["when"], row["title"], story.get("snippet")), "ref": ref, "kind": KIND,
            "title": row["title"], "lives_on": "pc", "sensitivity": sensitivity, "taint": TAINT, "meta": meta,
            "blocks": window, "relations": [], "sources": [{"id": "outlet", "title": outlet, "host": outlet}] if outlet else [],
            "next": str(offset + limit) if offset + limit < len(parsed.blocks) else None, "truncated": False}
