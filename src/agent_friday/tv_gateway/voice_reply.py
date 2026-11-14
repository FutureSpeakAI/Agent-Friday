"""What a remote voice turn says and shows (Stephen, 2026-11-14, talking to the paired tablet).

* ``speech_text``: the reply as it should be SPOKEN: no asterisks, hashes, bullets, links, tables or code
  fences read aloud. The formatted text stays for the screen; the device gets both.
* ``progress_words``: plain words for what the PC is doing while a long run works ("Checking your
  calendar"), never a tool name, an argument or a stale "Listening to the recording".
* ``fast_voice_model``: the model a voice turn defaults to when the device did not pick one: the
  fastest listed cloud model this PC has a key for, offered as a recommendation and labelled as such.
  A device's own pick always wins; nothing here is a hidden switch.
"""
from __future__ import annotations

import re
from typing import Optional

_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`([^`]*)`")
_IMG = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_RULE = re.compile(r"^\s*([-*_=])\1{2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*")
_QUOTE = re.compile(r"^\s{0,3}>+\s?")
_BULLET = re.compile(r"^\s*(?:[-*+•▪◦]|\d{1,3}[.)])\s+")
_EMPH = re.compile(r"(\*\*|__|~~|\*|(?<![A-Za-z0-9])_|_(?![A-Za-z0-9]))")


def speech_text(text: str) -> str:
    """The text with every bit of markdown removed, so a speech engine reads words and nothing else."""
    t = str(text or "")
    t = _FENCE.sub(" ", t)
    t = _IMG.sub(r"\1", t)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub("the link", t)
    t = _INLINE.sub(r"\1", t)
    t = _TAG.sub("", t)
    out = []
    for line in t.splitlines():
        if _RULE.match(line) or _TABLE_SEP.match(line):
            continue
        line = _HEAD.sub("", line)
        line = _QUOTE.sub("", line)
        bullet = bool(_BULLET.match(line))
        line = _BULLET.sub("", line)
        if "|" in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|") if c.strip()]
            line = ", ".join(cells)
        line = _EMPH.sub("", line)
        line = line.replace("#", "").replace("*", "").strip()
        if not line:
            out.append("")
            continue
        if bullet and line[-1] not in ".!?:;,":
            line += "."
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


_WORDS = (
    (("calendar", "event", "schedule", "agenda", "meeting"), "Checking your calendar"),
    (("mail", "gmail", "inbox", "email", "message"), "Reading your mail"),
    (("wiki", "graph", "people", "person", "contact", "memory", "recall", "knowledge"), "Looking in your notes"),
    (("search", "web", "browse", "fetch", "news"), "Searching the web"),
    (("weather",), "Checking the weather"),
    (("task", "todo", "reminder"), "Checking your tasks"),
    (("file", "read_", "open_", "document", "drive"), "Looking at your files"),
    (("briefing", "start_my_day", "day"), "Putting your day together"),
    (("music", "play", "radio"), "Getting the music ready"),
    (("write", "draft", "compose"), "Writing it up"),
)
THINKING = "Thinking it over…"
WORKING = "Working on it…"


def progress_words(tool) -> str:
    """Plain words for what is happening, from a tool's name or label. Never the raw name."""
    if isinstance(tool, dict):
        tool = tool.get("label") or tool.get("name") or tool.get("tool") or ""
    t = str(tool or "").lower()
    for keys, words in _WORDS:
        if any(k in t for k in keys):
            return words + "…"
    return WORKING


FAST_VOICE_PREFERENCE = ("claude-haiku-5-5", "claude-haiku-4-5-20251001")


def fast_voice_model(rows) -> Optional[dict]:
    """The catalog row of the fastest preferred model this PC can use now, or None."""
    by_id = {r.get("id"): r for r in rows or [] if isinstance(r, dict) and r.get("available") is not False}
    for mid in FAST_VOICE_PREFERENCE:
        if mid in by_id:
            return by_id[mid]
    return None
