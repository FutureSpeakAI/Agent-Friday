"""The Code workspace's repositories, read for a phone (PW-G2, adapter 8).

Reads the desktop's own /api/repos/scan (the cards it already keeps for 20 seconds) and /api/repos/<name>/status in
process, GET only. A repository reaches the phone as a row (name, branch, how many files changed, ahead and behind) and
an item (those, the last commit, and the names of the changed files). No file's contents, no diff and no folder path
leave: the phone learns what is going on in a repository, never reads its code. tests/unit/test_tv_ws_code.py holds the
rules.
"""
from __future__ import annotations

import re
from typing import Optional

from agent_friday.tv_gateway import ws_blocks
from agent_friday.tv_gateway.ws_adapter import Missing, Unavailable, desk_json, digest as _digest

PREFIX = "repo:"
KIND = "repo"
FACETS = {"state": lambda row: row["state"] or ""}
FILES_MAX = 200
REF = re.compile(r"^repo:(?P<name>[A-Za-z0-9][A-Za-z0-9._\-]{0,99})$")


def _file_name(name) -> str:
    """A changed file's name as the repository has it: only control characters and length are cut, because the
    words cleaner would turn src/__init__.py into src/init.py and docs/www.notes.md into docs/."""
    return "".join(c for c in str(name or "") if c.isprintable())[:200]


def _row(card: dict, sensitivity: str) -> Optional[dict]:
    name = str(card.get("name") or "")
    if not REF.match(PREFIX + name):
        return None
    counts = (card.get("dirty"), card.get("ahead"), card.get("behind"))
    dirty, ahead, behind = (n if isinstance(n, int) and not isinstance(n, bool) else 0 for n in counts)
    changed = "%d changed" % dirty if dirty else "clean"
    moved = ("%d ahead" % ahead if ahead else "", "%d behind" % behind if behind else "")
    sync = " · ".join(part for part in moved if part)
    state = "error" if card.get("error") else ("clean" if changed == "clean" else "changed")
    return {"ref": PREFIX + name, "kind": KIND, "title": name,
            "line": " · ".join(part for part in (ws_blocks.line(card.get("branch"), 80), changed, sync) if part),
            "state": state, "when": None, "thumb": None, "lives_on": "pc", "sensitivity": sensitivity, "taint": None}


def _cards() -> list[dict]:
    raw = desk_json("/api/repos/scan").get("repos")
    if not isinstance(raw, list):
        raise Unavailable("repos")
    return [c for c in raw if isinstance(c, dict)]


def pages(sensitivity: str) -> list[dict]:
    """The repositories under the projects folder, by name."""
    rows = [r for r in (_row(c, sensitivity) for c in _cards()) if r]
    rows.sort(key=lambda r: (r["title"].lower(), r["ref"]))
    return rows


def facets(rows: list[dict]) -> list[dict]:
    counts: dict = {}
    for r in rows:
        name = FACETS["state"](r)
        counts[name] = counts.get(name, 0) + 1
    return [{"name": "state", "values": [{"v": k, "n": n} for k, n in sorted(counts.items())]}]


def rev(rows: list[dict]) -> str:
    return "r_%d_%s" % (len(rows), _digest(*[(r["ref"], r["line"], r["state"]) for r in rows]))


def search(q: str, sensitivity: str) -> list[dict]:
    needle = q.strip().lower()
    return [dict(r, snippet=None) for r in pages(sensitivity)
            if needle in r["title"].lower() or needle in r["line"].lower()]


def item(ref: str, sensitivity: str, offset: int, limit: int) -> dict:
    m = REF.match(ref or "")
    if not m:
        raise Missing(ref)
    card = desk_json("/api/repos/%s/status" % m.group("name")).get("repo")
    row = _row(card, sensitivity) if isinstance(card, dict) else None
    if row is None:
        raise Missing(ref)
    meta = [{"k": "Branch", "v": ws_blocks.line(card.get("branch"), 80) or "?"}, {"k": "State", "v": row["state"]}]
    if card.get("last_commit"):
        meta.append({"k": "Last commit", "v": ws_blocks.line(card.get("last_commit"), 200)})
    if card.get("last_when"):
        meta.append({"k": "Committed", "v": ws_blocks.line(card.get("last_when"), 40)})
    files = [f for f in (card.get("files") or []) if isinstance(f, dict) and f.get("file")]
    shown = [[ws_blocks.line(f.get("code"), 4), _file_name(f.get("file"))] for f in files[:FILES_MAX]]
    blocks = [{"t": "table", "columns": ["Change", "File"], "rows": shown}] if shown else []
    if len(files) > FILES_MAX:
        blocks.append({"t": "para", "text": "%d more changed files are not listed." % (len(files) - FILES_MAX)})
    names = [str(f.get("file")) for f in files]
    return {"rev": "r_" + _digest(ref, row["line"], *names), "ref": ref, "kind": KIND, "title": row["title"],
            "lives_on": "pc", "sensitivity": sensitivity, "taint": None, "meta": meta,
            "blocks": blocks[offset:offset + limit], "relations": [], "sources": [],
            "next": str(offset + limit) if offset + limit < len(blocks) else None, "truncated": len(files) > FILES_MAX}
