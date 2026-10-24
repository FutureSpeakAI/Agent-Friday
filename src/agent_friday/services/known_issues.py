"""The known issues of this release, from the shipped KNOWN_ISSUES.md, for the
Doctor page, safe mode and the Doctor report (north star §36.0-7).

KNOWN_ISSUES.md sits at the root of the install, beside static/ (the installer
ships the tracked tree). It is read, never written, and parsed into sections
("## 1. Upgrading and moving") and their items (a "- " bullet and its
continuation lines). Markdown links become their text; emphasis marks go.
A missing or unreadable file says so plainly: it is never an empty list that
reads as "no known issues".
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

MAX_BYTES = 512 * 1024
FILE_NAME = "KNOWN_ISSUES.md"

_SECTION = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_LINK = re.compile(r"\[([^\]]{0,300})\]\([^)]{0,500}\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_FIRST_BOLD = re.compile(r"^\*\*(?P<head>.+?)\*\*\s*(?P<rest>.*)$")


def _candidates() -> list:
    here = Path(__file__).resolve()
    return [Path(os.path.abspath(FILE_NAME)), here.parents[3] / FILE_NAME]


def path() -> Optional[Path]:
    for p in _candidates():
        if p.is_file():
            return p
    return None


def _plain(s: str) -> str:
    s = _LINK.sub(r"\1", s)
    s = _BOLD.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def parse(text: str) -> dict:
    """{release, sections: [{title, items: [{title, text}]}]} from the file."""
    release = None
    sections: list = []
    cur = None
    item: Optional[list] = None
    in_list = False

    def close_item():
        nonlocal item
        if item is not None and cur is not None:
            raw = " ".join(x.strip() for x in item).strip()
            m = _FIRST_BOLD.match(raw)
            if m:
                cur["items"].append({"title": _plain(m.group("head")), "text": _plain(m.group("rest"))})
            elif raw:
                cur["items"].append({"title": "", "text": _plain(raw)})
        item = None

    for line in text.splitlines():
        if release is None and line.startswith("**For "):
            release = _plain(line).rstrip(".")
        m = _SECTION.match(line)
        if m:
            close_item()
            cur = {"title": _plain(m.group("title")), "items": []}
            sections.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("- "):
            close_item()
            item = [line[2:]]
        elif item is not None and line.strip() and (line.startswith("  ") or not in_list):
            item.append(line)                    # a bullet's or a paragraph's next line
        elif not line.strip():
            close_item()
            in_list = False
            continue
        elif item is None and line.strip() and not line.startswith(("#", "---", "|")):
            item = [line]                        # a paragraph is an item too
        if line.startswith("- "):
            in_list = True
    close_item()
    return {"release": release, "sections": [s for s in sections if s["items"]]}


def load() -> dict:
    """The known issues, or {available: false, why} when the file is not
    there or cannot be read."""
    p = path()
    if p is None:
        return {"available": False, "why": "The list of known issues is not in this install (%s)." % FILE_NAME}
    try:
        if p.stat().st_size > MAX_BYTES:
            return {"available": False, "why": "The list of known issues is too large to show."}
        out = parse(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return {"available": False, "why": "The list of known issues could not be read (%s)." % type(e).__name__}
    out["available"] = True
    out["count"] = sum(len(s["items"]) for s in out["sections"])
    return out


def summary() -> dict:
    """For the Doctor report: how many, by section title, and the release."""
    k = load()
    if not k.get("available"):
        return {"available": False}
    return {"available": True, "release": k.get("release"), "count": k["count"],
            "by_section": [{"section": s["title"], "count": len(s["items"])} for s in k["sections"]]}
