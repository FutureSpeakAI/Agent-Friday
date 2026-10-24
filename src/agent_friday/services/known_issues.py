"""The known issues of this release, from the shipped KNOWN_ISSUES.md, for the
Doctor page, safe mode and the Doctor report (north star §36.0-7).

KNOWN_ISSUES.md sits at the root of the install, beside static/ (the installer
ships the tracked tree). It is read, never written, and parsed into sections
("## 1. Upgrading and moving"). Each "- " bullet, with its continuation lines,
is an issue; a paragraph that is not a bullet is a note of its section, shown
but not counted. Markdown links become their text; emphasis and code marks go.
A missing or unreadable file says so plainly: it is never an empty list that
reads as "no known issues".
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Optional

MAX_BYTES = 512 * 1024
FILE_NAME = "KNOWN_ISSUES.md"

_SECTION = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_LINK = re.compile(r"\[([^\]]{0,300})\]\([^)]{0,500}\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]{0,300})`")
_FIRST_BOLD = re.compile(r"^\*\*(?P<head>.+?)\*\*\s*(?P<rest>.*)$")


def _candidates() -> list:
    here = Path(__file__).resolve()
    # the install root first: a copy in whatever folder the server was started
    # from is not this release's list
    return [here.parents[3] / FILE_NAME, Path(os.path.abspath(FILE_NAME))]


def path() -> Optional[Path]:
    for p in _candidates():
        if p.is_file():
            return p
    return None


def _plain(s: str) -> str:
    s = _LINK.sub(r"\1", s)
    s = _BOLD.sub(r"\1", s)
    s = _CODE.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def _ascii(s: Optional[str]) -> Optional[str]:
    """For the Doctor report, whose strings are plain ASCII (™ and the like go)."""
    if s is None:
        return None
    s = s.replace("™", "").replace("®", "")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").strip()


def parse(text: str) -> dict:
    """{release, sections: [{title, items: [{title, text}], notes: [text]}]}."""
    release = None
    sections: list = []
    cur = None
    block: Optional[list] = None          # the lines of the bullet or paragraph being read
    is_bullet = False

    def close():
        nonlocal block
        if block is not None and cur is not None:
            raw = " ".join(x.strip() for x in block).strip()
            if is_bullet:
                m = _FIRST_BOLD.match(raw)
                cur["items"].append({"title": _plain(m.group("head")), "text": _plain(m.group("rest"))}
                                    if m else {"title": "", "text": _plain(raw)})
            elif raw:
                cur["notes"].append(_plain(raw))
        block = None

    for line in text.splitlines():
        if release is None and line.startswith("**For "):
            release = _plain(line).rstrip(".")
        m = _SECTION.match(line)
        if m or line.startswith("#"):
            close()
            if m:
                cur = {"title": _plain(m.group("title")), "items": [], "notes": []}
                sections.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if not s or s.startswith("---"):
            close()
        elif line.startswith("- "):
            close()
            block, is_bullet = [line[2:]], True
        elif block is not None:
            block.append(line)                 # a continuation: indented or not, nested bullets too
        else:
            block, is_bullet = [line], False
    close()
    return {"release": release, "sections": [s for s in sections if s["items"] or s["notes"]]}


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
    """For the Doctor report: how many issues, by section, and the release."""
    k = load()
    if not k.get("available"):
        return {"available": False}
    return {"available": True, "release": _ascii(k.get("release")), "count": k["count"],
            "by_section": [{"section": _ascii(s["title"]), "count": len(s["items"])}
                           for s in k["sections"] if s["items"]]}
