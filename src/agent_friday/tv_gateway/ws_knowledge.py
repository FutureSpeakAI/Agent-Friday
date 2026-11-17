"""The Knowledge workspace's pages, read for a phone (PW-G2, adapter 1).

Reads the desktop's own wiki routes in process (GET only): /api/wiki/structure for the tree, /api/wiki/page for
one page, /api/knowledge-graph/search for graph hits. A page's text is parsed into plain blocks (ws_blocks); the
page's raw file text never leaves. tests/unit/test_tv_ws_content.py holds the rules.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from agent_friday.tv_gateway import ws_blocks
from agent_friday.tv_gateway.ws_adapter import Locked, Missing, Unavailable, desk_json as _desk

SECTIONS = ("pages",)                         # people and graph arrive with their own adapters
KIND = "page"
REF = re.compile(r"^page:(?P<path>[^\x00-\x1f\\:*?\"<>|]{1,200}\.(?:md|txt))$", re.I)
VAULT_LOCKED = "[vault-encrypted file"
MAX_SOURCE = 1_000_000          # characters of one page parsed; the real wiki holds a 20 MB page
PARSED_KEPT = 8
_PARSED: "OrderedDict" = OrderedDict()
_LOCK = threading.Lock()


FACETS = {"folder": lambda row: row["line"]}


def ref_path(ref: str) -> str:
    """The wiki path of a `page:` ref, or Missing. No `..` and no leading slash ever reaches the PC."""
    m = REF.match(ref or "")
    parts = m.group("path").split("/") if m else []
    if not m or any(p in ("", ".", "..") for p in parts):
        raise Missing(ref)
    return m.group("path")


def _iso(mtime) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(mtime), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _title(stem: str) -> str:
    return " ".join(re.sub(r"[-_]+", " ", stem).split()) or stem


def _row(section: str, f: dict, sensitivity: str) -> dict:
    name = str(f.get("name") or "")
    return {"ref": "page:%s/%s" % (section, f.get("filename")), "kind": KIND, "title": _title(name),
            "line": section, "state": None, "when": _iso(f.get("modified")), "thumb": None,
            "lives_on": "pc", "sensitivity": sensitivity, "taint": None}


def pages(sensitivity: str) -> list[dict]:
    """Every page the PC shows, newest first (the wiki's own tree, one row per file)."""
    structure = _desk("/api/wiki/structure").get("structure")
    if not isinstance(structure, dict):
        raise Unavailable("structure")
    rows = [_row(section, f, sensitivity) for section, files in structure.items() if isinstance(files, list)
            for f in files if isinstance(f, dict) and f.get("filename")]
    rows.sort(key=lambda r: (r["when"] or "", r["ref"]), reverse=True)
    return rows


def facets(rows: list[dict]) -> list[dict]:
    counts: dict = {}
    for r in rows:
        counts[r["line"]] = counts.get(r["line"], 0) + 1
    return [{"name": "folder", "values": [{"v": k, "n": n} for k, n in sorted(counts.items())]}]


def rev(rows: list[dict]) -> str:
    return "k_%d_%s" % (len(rows), max((r["when"] or "" for r in rows), default="")[:19].replace(":", ""))


def search(q: str, sensitivity: str) -> list[dict]:
    """Pages whose name holds the words, then pages the graph found them in; each row says why in `snippet`."""
    needle = q.strip().lower()
    rows = [dict(r, snippet=None) for r in pages(sensitivity) if needle in r["title"].lower() or needle in r["ref"].lower()]
    seen = {r["ref"] for r in rows}
    try:
        hits = _desk("/api/knowledge-graph/search", {"q": q}).get("results") or []
    except (Missing, Unavailable):
        hits = []                                   # the graph being down must not hide a page found by name
    for hit in hits:
        path = hit.get("path") if isinstance(hit, dict) else None
        ref = "page:%s" % path if isinstance(path, str) else None
        if ref and REF.match(ref) and ref not in seen:
            seen.add(ref)
            folder, _, filename = path.rpartition("/")
            rows.append(dict(_row(folder, {"name": filename.rsplit(".", 1)[0], "filename": filename}, sensitivity),
                             title=ws_blocks._clean(str(hit.get("title") or ""))[:200] or _title(filename),
                             line=ws_blocks._clean(str(hit.get("type") or folder))[:200],
                             snippet=ws_blocks._clean(str(hit.get("description") or ""))[:240] or None))
    return rows


def item(ref: str, sensitivity: str, offset: int, limit: int) -> dict:
    path = ref_path(ref)
    data = _desk("/api/wiki/page", {"path": path})
    content = data.get("content")
    if not isinstance(content, str):
        raise Unavailable("page")
    if data.get("locked") or content.startswith(VAULT_LOCKED):
        raise Locked(ref)
    folder, _, filename = path.rpartition("/")
    parsed, truncated = _parsed(path, data.get("modified"), content, folder)
    window = parsed.blocks[offset:offset + limit]
    more = offset + limit < len(parsed.blocks)
    return {"rev": "p_%s" % int(data.get("modified") or 0), "ref": ref, "kind": KIND,
            "title": parsed.title or _title(filename.rsplit(".", 1)[0]), "lives_on": "pc",
            "sensitivity": sensitivity, "taint": None,
            "meta": [{"k": "Folder", "v": folder or "wiki"}, {"k": "Updated", "v": _iso(data.get("modified")) or ""}],
            "blocks": window, "relations": [], "sources": parsed.sources if offset == 0 else [],
            "next": str(offset + limit) if more else None, "truncated": truncated}


def _parsed(path: str, modified, content: str, folder: str):
    """(Parsed, truncated) for one page version. Only the first MAX_SOURCE characters are read, cut at a line, and
    the result is kept for a few pages so paging through a long one parses it once."""
    key = (path, modified, len(content))
    with _LOCK:
        if key in _PARSED:
            _PARSED.move_to_end(key)
            return _PARSED[key]
    truncated = len(content) > MAX_SOURCE
    text = content[:MAX_SOURCE].rpartition("\n")[0] if truncated else content
    made = (ws_blocks.parse(text, folder), truncated)
    with _LOCK:
        _PARSED[key] = made
        while len(_PARSED) > PARSED_KEPT:
            _PARSED.popitem(last=False)
    return made
