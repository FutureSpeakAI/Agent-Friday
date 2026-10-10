"""Spoken answers built in code, and the text clean-up every spoken line gets.

A small voice front (Ternary Bonsai 1.7B) is unreliable at structured lists:
handed three emails it listed them and then reasoned aloud for 646 words;
handed six events it narrated itself in the first person, used markdown
bullets and bold, and read out ISO dates. So a routed read whose result is a
list of records (calendar events, email messages, files) is spoken from the
records by ``structured_reply``, with no model call: short, second person,
natural times ("3:40 PM"), "today" / "tomorrow" / a weekday instead of a
date, sender and subject for email (never a body), and "and N more; want
the rest?" past ``SPOKEN_ITEMS``. The model still speaks free-text results
(news, web, notes, past conversations).

``speakable`` strips markdown (bold, headings, bullets, links, code marks)
from anything about to be spoken, whoever wrote it.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

#: Routed reads whose result is a list of records, spoken from code.
STRUCTURED_TOOLS = frozenset({"query_calendar", "check_email", "search_email", "search_files"})
#: How many records are spoken before "and N more; want the rest?".
SPOKEN_ITEMS = 5
#: Longest title, subject or name spoken from a record.
_FIELD_CHARS = 80

_LATE_PREFIX = re.compile(r"(?s)^\s*\[LATE RESULT[^\]]*\]\s*")

# ── markdown out ─────────────────────────────────────────────────────────────

_MD_LINK = re.compile(r"\[([^\]\n]{1,300})\]\((?:[^)\s]+)\)")
#: Bold and italics; a lone "_" is left alone (file names use it).
_MD_EMPH = re.compile(r"(\*\*|__|\*)(?=\S)(.+?)(?<=\S)\1")
_MD_LINE = re.compile(r"(?m)^[ \t]*(?:#{1,6}[ \t]+|[-*+•][ \t]+|>[ \t]*)")
_MD_LEFTOVER = re.compile(r"(?m)\*\*|__|`+|^[ \t]*[-*_]{3,}[ \t]*$")


def speakable(text: str) -> str:
    """`text` without markdown: what a mouth should read aloud."""
    s = str(text or "")
    if not s:
        return s
    s = _MD_LINK.sub(r"\1", s)
    s = _MD_LINE.sub("", s)
    for _ in range(2):
        s = _MD_EMPH.sub(r"\2", s)
    s = _MD_LEFTOVER.sub("", s)
    s = re.sub(r"(?<=\S)\*(?=\s|$)|(?:^|(?<=\s))\*(?=\S)", "", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip(" \t") if s.strip() else ""


# ── records to sentences ─────────────────────────────────────────────────────

def _payload(result):
    text = _LATE_PREFIX.sub("", str(result or "")).strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _field(v) -> str:
    """A record's text field, safe to speak: one line, no markdown, short."""
    s = speakable(re.sub(r"\s+", " ", str(v or ""))).strip()
    if len(s) > _FIELD_CHARS:
        s = s[:_FIELD_CHARS].rsplit(" ", 1)[0] + "…"
    return s.rstrip(" .?!;:,")


def _join(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return "%s and %s" % (items[0], items[1])
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _more(n: int) -> str:
    if n <= 0:
        return ""
    return ("There's 1 more; want the rest?" if n == 1
            else "There are %d more; want the rest?" % n)


def _when(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return ("%d %s" % (h, ampm)) if dt.minute == 0 else ("%d:%02d %s" % (h, dt.minute, ampm))


def _parse(value):
    """(datetime or None, all_day) from a calendar start string."""
    s = str(value or "").strip()
    if not s:
        return None, False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day), True
        except ValueError:
            return None, False
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt, False


def _day_name(d: date, today: date) -> str:
    delta = (d - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    if 1 < delta < 7:
        return "On " + d.strftime("%A")
    return "On %s %d" % (d.strftime("%B"), d.day)


def _incomplete(data: dict) -> bool:
    return bool(data.get("error") or data.get("degraded") or data.get("not_searched")
                or any(str((a or {}).get("status") or "") in ("error", "needs_reauth")
                       for a in (data.get("accounts") or []) if isinstance(a, dict)))


_PARTIAL = "Some of your accounts couldn't be read, so that may not be everything."


def _calendar(data: dict, now: datetime) -> str | None:
    events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
    if not events:
        return None
    today = now.date()
    days: dict = {}
    order = []
    undated = []
    for e in events:
        dt, all_day = _parse(e.get("start"))
        title = _field(e.get("title")) or "something untitled"
        if dt is None:
            undated.append(title)
            continue
        key = dt.date()
        if key not in days:
            days[key] = []
            order.append(key)
        days[key].append((dt, all_day, title))
    spoken, left = [], SPOKEN_ITEMS
    told = 0
    for key in sorted(order):
        if left <= 0:
            break
        items = sorted(days[key], key=lambda x: (not x[1], x[0]))[:left]
        left -= len(items)
        told += len(items)
        parts = ["%s all day" % t if all_day else "%s at %s" % (t, _when(dt))
                 for dt, all_day, t in items]
        spoken.append("%s you have %s." % (_day_name(key, today), _join(parts)))
    if left > 0 and undated:
        extra = undated[:left]
        told += len(extra)
        spoken.append("You also have %s." % _join(extra))
    out = " ".join(spoken + [_more(len(events) - told)]).strip()
    if _incomplete(data):
        out += " " + _PARTIAL
    return out


def _sender(v) -> str:
    s = str(v or "").strip()
    m = re.match(r'^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$', s)
    if m:
        s = m.group(1)
    return _field(s) or "someone"


def _mail_item(m: dict, mark_urgent: bool) -> str:
    subj = _field(m.get("subject"))
    s = "from %s" % _sender(m.get("from"))
    if subj:
        s += " about %s" % subj
    if mark_urgent and m.get("urgent"):
        s += ", marked urgent"
    return s


def _email(data: dict, tool: str) -> str | None:
    msgs = [m for m in (data.get("messages") or []) if isinstance(m, dict)]
    if not msgs:
        return None
    if tool == "check_email":
        unread = [m for m in msgs if m.get("unread")]
        if not unread:
            out = "Nothing unread. Your latest email is %s." % _mail_item(msgs[0], True)
        else:
            shown = unread[:SPOKEN_ITEMS]
            noun = "unread email" if len(unread) == 1 else "unread emails"
            out = "You have %d %s: %s." % (len(unread), noun,
                                          _join([_mail_item(m, True) for m in shown]))
            out = (out + " " + _more(len(unread) - len(shown))).strip()
    else:
        shown = msgs[:SPOKEN_ITEMS]
        try:
            total = max(len(msgs), int(data.get("count") or 0))
        except (TypeError, ValueError):
            total = len(msgs)
        noun = "email" if total == 1 else "emails"
        out = "I found %d %s: %s." % (total, noun, _join([_mail_item(m, True) for m in shown]))
        out = (out + " " + _more(total - len(shown))).strip()
    if data.get("connected") is False:
        out += " That's from my offline copy, so it may be out of date."
    elif _incomplete(data):
        out += " " + _PARTIAL
    return out


def _files(data: dict) -> str | None:
    rows = [r for r in (data.get("results") or []) if isinstance(r, dict)]
    if not rows:
        return None
    shown = rows[:SPOKEN_ITEMS]
    names = [_field(r.get("name") or str(r.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1])
             or "an unnamed file" for r in shown]
    noun = "file" if len(rows) == 1 else "files"
    out = "I found %d %s: %s." % (len(rows), noun, _join(names))
    return (out + " " + _more(len(rows) - len(shown))).strip()


def structured_reply(result, tool: str, now: datetime | None = None) -> str | None:
    """The spoken answer for a structured routed read, or None (not a
    structured tool, nothing to list, or a shape it does not know: the
    speaker answers instead)."""
    if tool not in STRUCTURED_TOOLS:
        return None
    data = _payload(result)
    if not isinstance(data, dict):
        return None
    try:
        if tool == "query_calendar":
            return _calendar(data, now or datetime.now())
        if tool in ("check_email", "search_email"):
            return _email(data, tool)
        if tool == "search_files":
            return _files(data)
    except Exception:
        return None
    return None


__all__ = ["STRUCTURED_TOOLS", "SPOKEN_ITEMS", "speakable", "structured_reply"]
