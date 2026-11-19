"""Markdown to the plain blocks a phone draws (PHONE_WORKSPACES_PARITY section 3, contract R4).

A wiki page is parsed on the PC into a short list of block types and nothing else leaves: no HTML, no image,
no URL to follow in the text (a code block is kept as written). An external link becomes a source (its host only); an internal link becomes a link block
with a page ref. tests/unit/test_tv_ws_blocks.py holds each rule below and walks hostile inputs.

Block types: heading, para, list, table, quote, code, link.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

TEXT_MAX = 4096                    # contract: any text field at most 4 KB
LIST_ITEMS_MAX = 200
TABLE_ROWS_MAX = 200

_FRONT = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+\"[^\"]*\")?\)")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_TAG = re.compile(r"<[^>]+>")
_EMPHASIS = re.compile(r"(\*\*|__|~~|`)")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_PAGE_FILE = re.compile(r"\.(?:md|txt)$", re.I)


@dataclass
class Parsed:
    blocks: list = field(default_factory=list)
    sources: list = field(default_factory=list)       # {id, title, host}
    title: Optional[str] = None


def page_ref(folder: str, target: str) -> Optional[str]:
    """The `page:` ref a relative markdown link points at, or None when it leaves the wiki or is not a page."""
    target = target.split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    if not target or target.startswith("/") or _SCHEME.match(target) or not _PAGE_FILE.search(target):
        return None
    joined = posixpath.normpath(posixpath.join(folder, target))
    if joined.startswith("..") or joined.startswith("/") or joined == ".":
        return None
    return "page:" + joined


_BARE_URL = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z][A-Za-z0-9+.\-]*://|www\.)\S+", re.I)


def _clean(text: str) -> str:
    """Plain words: images dropped, a link keeps only its label, markup and any URL left in the text removed.
    The URL strip comes last: emphasis marks such as _https://x_ must be gone first, or they hide the address."""
    text = _TAG.sub("", _LINK.sub(lambda m: m.group(1), _IMAGE.sub("", text)))
    text = _WIKILINK.sub(lambda m: m.group(2) or m.group(1), text)
    return _BARE_URL.sub("", _EMPHASIS.sub("", text))


def parse(markdown: str, folder: str = "") -> Parsed:
    """The blocks, sources and title of one page. Never raises on odd input; unknown syntax is dropped."""
    out = Parsed()
    text = _FRONT.sub("", markdown or "", count=1)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif _FENCE.match(line):
            i = _code(lines, i, out)
        elif _HEADING.match(line):
            i = _heading(lines, i, out)
        elif _TABLE_ROW.match(line) and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]):
            i = _table(lines, i, out)
        elif _QUOTE.match(line):
            i = _quote(lines, i, out, folder)
        elif _LIST.match(line):
            i = _list(lines, i, out, folder)
        else:
            i = _para(lines, i, out, folder)
    return out


def _flat(raw: str, out: Parsed, folder: str) -> str:
    """One line of inline markdown as plain text; its links go to `out` (sources) and are returned for blocks."""
    def take(m):
        label, target = m.group(1), m.group(2)
        if _SCHEME.match(target):
            host = (urlsplit(target).hostname or "") if not target.lower().startswith("mailto:") else ""
            if host and not any(s["host"] == host and s["title"] == _clean(label)[:200] for s in out.sources):
                out.sources.append({"id": "src_%d" % (len(out.sources) + 1), "title": _clean(label)[:200], "host": host})
        return label
    return " ".join(_clean(_LINK.sub(take, _IMAGE.sub("", raw))).split())[:TEXT_MAX]


def _link_blocks(raw: str, folder: str) -> list:
    found, seen = [], set()
    for label, target in _LINK.findall(_IMAGE.sub("", raw)):
        ref = page_ref(folder, target)
        if ref and ref not in seen:
            seen.add(ref)
            found.append({"t": "link", "text": _clean(label).strip()[:200], "ref": ref})
    return found


def _heading(lines, i, out) -> int:
    m = _HEADING.match(lines[i])
    text = " ".join(_clean(_LINK.sub(lambda x: x.group(1), _IMAGE.sub("", m.group(2)))).split())[:TEXT_MAX]
    if text:
        out.blocks.append({"t": "heading", "level": len(m.group(1)), "text": text})
        if out.title is None and len(m.group(1)) == 1:
            out.title = text
    return i + 1


def _code(lines, i, out) -> int:
    fence = _FENCE.match(lines[i]).group(1)
    body, i = [], i + 1
    while i < len(lines) and not lines[i].lstrip().startswith(fence):
        body.append(lines[i])
        i += 1
    out.blocks.append({"t": "code", "text": "\n".join(body)[:TEXT_MAX]})
    return i + 1


def _table(lines, i, out) -> int:
    def cells(row):
        return [_clean(c).strip()[:200] for c in row.strip().strip("|").split("|")]
    columns, rows, i = cells(lines[i]), [], i + 2
    while i < len(lines) and _TABLE_ROW.match(lines[i]) and len(rows) < TABLE_ROWS_MAX:
        rows.append(cells(lines[i]))
        i += 1
    out.blocks.append({"t": "table", "columns": columns, "rows": rows})
    return i


def _quote(lines, i, out, folder) -> int:
    body, links = [], []
    while i < len(lines) and _QUOTE.match(lines[i]):
        raw = _QUOTE.match(lines[i]).group(1)
        body.append(_flat(raw, out, folder))
        links += _link_blocks(raw, folder)
        i += 1
    text = " ".join(b for b in body if b)[:TEXT_MAX]
    if text:
        out.blocks.append({"t": "quote", "text": text})
    out.blocks += links
    return i


def _list(lines, i, out, folder) -> int:
    items, links = [], []
    while i < len(lines) and _LIST.match(lines[i]):
        raw = _LIST.match(lines[i]).group(1)
        flat = _flat(raw, out, folder)
        if flat and len(items) < LIST_ITEMS_MAX:
            items.append(flat)
        links += _link_blocks(raw, folder)
        i += 1
    if items:
        out.blocks.append({"t": "list", "items": items})
    out.blocks += links
    return i


def _para(lines, i, out, folder) -> int:
    raws = []
    while i < len(lines) and lines[i].strip() and not (
            _HEADING.match(lines[i]) or _FENCE.match(lines[i]) or _LIST.match(lines[i]) or _QUOTE.match(lines[i])
            or (_TABLE_ROW.match(lines[i]) and i + 1 < len(lines) and _TABLE_RULE.match(lines[i + 1]))):
        raws.append(lines[i])
        i += 1
    raw = " ".join(r.strip() for r in raws)
    text = _flat(raw, out, folder)
    if text:
        out.blocks.append({"t": "para", "text": text})
    out.blocks += _link_blocks(raw, folder)
    return i
