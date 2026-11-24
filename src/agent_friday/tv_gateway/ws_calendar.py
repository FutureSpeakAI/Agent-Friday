"""The Calendar workspace's agenda, read for a phone (PW-G2, adapter 2).

Reads the desktop's own calendar routes in process (GET only): /api/calendar/range for the agenda window and
/api/calendar/event/<id> for one event. An event reaches the phone as a row (title, time and place, day) and an item
(its time, place, calendar and a plain description); attendee addresses, conference links and the description's URLs
never leave, only how many people are invited. tests/unit/test_tv_ws_calendar.py holds the rules.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import quote

from agent_friday.tv_gateway import ws_blocks
from agent_friday.tv_gateway.ws_adapter import Missing, Unavailable, desk_json, digest as _digest

PREFIX = "event:"
KIND = "event"
FACETS = {"day": lambda row: (row["when"] or "")[:10]}
AGENDA_DAYS = 14
SEARCH_DAYS = 62                               # the desktop's own range limit
REF = re.compile(r"^event:(?P<id>[A-Za-z0-9_@.\-]{1,200})$")


def _event_id(ref: str) -> str:
    m = REF.match(ref or "")
    if not m or m.group("id") in (".", ".."):
        raise Missing(ref)
    return m.group("id")


def _when(value) -> Optional[str]:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text + "T00:00:00"
    return text[:25] or None


def _clock(value) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except ValueError:
        return ""


def _line(ev: dict) -> str:
    if ev.get("all_day") or not ev.get("start_time") or "T" not in str(ev.get("start_time")):
        span = "All day"
    else:
        start, end = _clock(ev.get("start_time")), _clock(ev.get("end_time"))
        span = "%s-%s" % (start, end) if start and end else start
    place = ws_blocks.line(ev.get("location"), 80)
    return " · ".join(part for part in (span, place) if part)


def _row(ev: dict, sensitivity: str) -> Optional[dict]:
    ident = str(ev.get("id") or "")
    if not REF.match("event:" + ident):
        return None
    return {"ref": "event:" + ident, "kind": KIND, "title": ws_blocks.line(ev.get("title"), 120) or "(untitled)",
            "line": _line(ev), "state": ws_blocks.line(ev.get("type"), 40) or None, "when": _when(ev.get("start_time")),
            "thumb": None, "lives_on": "pc", "sensitivity": sensitivity, "taint": None}


def _window(days: int) -> list[dict]:
    first = date.today()
    data = desk_json("/api/calendar/range", {"start": first.isoformat(), "end": (first + timedelta(days=days)).isoformat()})
    events = data.get("events")
    if not isinstance(events, list) or data.get("errors") and not events:
        raise Unavailable("calendar")                 # a calendar that could not be read is never a free week
    return [e for e in events if isinstance(e, dict)]


def pages(sensitivity: str) -> list[dict]:
    """The next two weeks, in time order."""
    rows = [r for r in (_row(e, sensitivity) for e in _window(AGENDA_DAYS)) if r]
    rows.sort(key=lambda r: (r["when"] or "", r["ref"]))
    return rows


def facets(rows: list[dict]) -> list[dict]:
    counts: dict = {}
    for r in rows:
        day = FACETS["day"](r)
        counts[day] = counts.get(day, 0) + 1
    return [{"name": "day", "values": [{"v": k, "n": n} for k, n in sorted(counts.items())]}]


def rev(rows: list[dict]) -> str:
    return "c_%d_%s" % (len(rows), _digest(*[(r["ref"], r["when"], r["title"], r["line"]) for r in rows]))


def search(q: str, sensitivity: str) -> list[dict]:
    needle = q.strip().lower()
    rows = [r for r in (_row(e, sensitivity) for e in _window(SEARCH_DAYS)) if r]
    found = [dict(r, snippet=None) for r in rows if needle in r["title"].lower() or needle in r["line"].lower()]
    found.sort(key=lambda r: (r["when"] or "", r["ref"]))
    return found


def item(ref: str, sensitivity: str, offset: int, limit: int) -> dict:
    ident = _event_id(ref)
    ev = desk_json("/api/calendar/event/" + quote(ident, safe="")).get("event")
    if not isinstance(ev, dict):
        raise Unavailable("event")
    row = _row(ev, sensitivity)
    if row is None:
        raise Missing(ref)
    parsed = ws_blocks.parse(str(ev.get("description") or ""))
    invited = [a for a in ev.get("attendees") or [] if a]
    meta = [{"k": "When", "v": row["line"].split(" · ")[0]}, {"k": "Day", "v": FACETS["day"](row)}]
    if ws_blocks.line(ev.get("location"), 80):
        meta.append({"k": "Where", "v": ws_blocks.line(ev.get("location"), 80)})
    if invited:
        meta.append({"k": "Invited", "v": "%d people" % len(invited)})
    window = parsed.blocks[offset:offset + limit]
    return {"rev": "e_" + _digest(ident, row["when"], row["title"], row["line"], ev.get("description")),
            "ref": ref, "kind": KIND, "title": row["title"], "lives_on": "pc", "sensitivity": sensitivity,
            "taint": None, "meta": meta, "blocks": window, "relations": [], "sources": [],
            "next": str(offset + limit) if offset + limit < len(parsed.blocks) else None, "truncated": False}
