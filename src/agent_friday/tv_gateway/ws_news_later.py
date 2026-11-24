"""The News workspace's read-later list, read for a phone (PW-G2, adapter 6).

Reads the desktop's own /api/news/read-later in process (GET only): the stories the owner saved to read later. Saved
entries are keyed by their link, which never leaves, so a story is named `later:<a short fingerprint of its link>`. It
reaches the phone as a row (headline, outlet, when it was saved) and an item (the outlet's summary). Every row and item
says `taint: "outside:news"`. tests/unit/test_tv_ws_news.py holds the rules.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from agent_friday.tv_gateway import ws_blocks
from agent_friday.tv_gateway.ws_adapter import Missing, Unavailable, desk_json, digest as _digest

PREFIX = "later:"
KIND = "saved_story"
TAINT = "outside:news"
FACETS: dict = {}
REF = re.compile(r"^later:(?P<id>[0-9a-f]{16})$")


def _ident(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _saved() -> list[dict]:
    items = desk_json("/api/news/read-later").get("items")
    if not isinstance(items, list):
        raise Unavailable("read-later")
    return [s for s in items if isinstance(s, dict) and str(s.get("url") or "")]


def _row(story: dict, sensitivity: str) -> dict:
    return {"ref": PREFIX + _ident(str(story["url"])), "kind": KIND,
            "title": ws_blocks.line(story.get("title"), 160) or "(untitled)",
            "line": ws_blocks.line(story.get("source"), 60), "state": ws_blocks.line(story.get("category"), 40) or None,
            "when": str(story.get("saved_at") or "")[:25] or None, "thumb": None, "lives_on": "pc",
            "sensitivity": sensitivity, "taint": TAINT}


def pages(sensitivity: str) -> list[dict]:
    """Newest saved first, as the desktop keeps them."""
    return [_row(s, sensitivity) for s in _saved()]


def facets(rows: list[dict]) -> list[dict]:
    return []


def rev(rows: list[dict]) -> str:
    return "l_%d_%s" % (len(rows), _digest(*[(r["ref"], r["when"], r["title"]) for r in rows]))


def search(q: str, sensitivity: str) -> list[dict]:
    needle = q.strip().lower()
    found = []
    for story in _saved():
        row, words = _row(story, sensitivity), ws_blocks.line(story.get("snippet"), 400)
        if needle in row["title"].lower() or needle in words.lower():
            found.append(dict(row, snippet=words[:160] or None))
    return found


def item(ref: str, sensitivity: str, offset: int, limit: int) -> dict:
    m = REF.match(ref or "")
    story: Optional[dict] = next((s for s in _saved() if _ident(str(s["url"])) == m.group("id")), None) if m else None
    if story is None:
        raise Missing(ref)
    row = _row(story, sensitivity)
    shown = (("Outlet", row["line"]), ("Category", row["state"]), ("Saved", row["when"]))
    meta = [{"k": k, "v": v} for k, v in shown if v]
    parsed = ws_blocks.parse(str(story.get("snippet") or ""))
    return {"rev": "l_" + _digest(ref, row["title"], story.get("snippet")), "ref": ref, "kind": KIND,
            "title": row["title"],
            "lives_on": "pc", "sensitivity": sensitivity, "taint": TAINT, "meta": meta,
            "blocks": parsed.blocks[offset:offset + limit], "relations": [],
            "sources": [{"id": "outlet", "title": row["line"], "host": row["line"]}] if row["line"] else [],
            "next": str(offset + limit) if offset + limit < len(parsed.blocks) else None, "truncated": False}
