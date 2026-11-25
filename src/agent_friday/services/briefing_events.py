"""The events section of Start My Day: two windows, built from calendar entries that carry their own link.

Window A is the next 7 days. Window B is the next 6 months (trips, burns, festivals, with deadlines). The groups
inside each window and the way they are worded come from the section's skill, not from this module; this
module only fixes the two windows, numbers the calendar entries the tools returned ([C1], [C2], ...) so the
briefing can cite them, and hands the skill's instructions to the model beside them. A calendar entry with no URL is
still listed, but the link validator (services/briefing_links) drops any item that cannot cite a linked one.

tests/unit/test_briefing_events.py holds the windows, the numbering and the Sunday case.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

WINDOW_A_DAYS = 7
WINDOW_B_DAYS = 183          # six months


@dataclass(frozen=True)
class EventsContext:
    text: str
    calendar: list          # [{"id": "C1", "title", "url", "start", "window": "A"|"B"}]


def windows(now: datetime):
    """((a_start, a_end), (b_start, b_end)) as dates: A is today through the next 7 days, B the six months after."""
    today = now.date()
    a_end = today + timedelta(days=WINDOW_A_DAYS)
    return (today, a_end), (a_end + timedelta(days=1), today + timedelta(days=WINDOW_B_DAYS))


def _day(value) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _sorted(rows, now: datetime):
    (a_from, a_to), (b_from, b_to) = windows(now)
    placed = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        day = _day(row.get("start_time") or row.get("start"))
        if day is None:
            continue
        if a_from <= day <= a_to:
            placed.append(("A", day, row))
        elif b_from <= day <= b_to:
            placed.append(("B", day, row))
    placed.sort(key=lambda p: (p[1], str(p[2].get("title") or "")))
    return placed


def build(now: datetime, calendar_rows, first_index: int = 1) -> EventsContext:
    """The two calendar windows as prompt text, and the calendar entries they numbered. Skill-agnostic: which
    skill writes a section from them is decided by services/briefing_sections."""
    (a_from, a_to), (b_from, b_to) = windows(now)
    numbered, lines = [], {"A": [], "B": []}
    for window, day, row in _sorted(calendar_rows, now):
        cid = "C%d" % (first_index + len(numbered))
        title = str(row.get("title") or "Untitled")
        numbered.append({"id": cid, "title": title, "url": str(row.get("url") or row.get("html_link") or ""),
                         "start": day.isoformat(), "window": window})
        lines[window].append("[%s] %s: %s" % (cid, day.isoformat(), title))
    text = "\n".join([
        "CALENDAR WINDOWS (cite an entry by its id in square brackets, like [C1]; never write a web address)",
        "WINDOW A: the next %d days (%s to %s)." % (WINDOW_A_DAYS, a_from.isoformat(), a_to.isoformat()),
        "\n".join(lines["A"]) or "(no calendar entries in this window)",
        "WINDOW B: the next 6 months (%s to %s)." % (b_from.isoformat(), b_to.isoformat()),
        "\n".join(lines["B"]) or "(no calendar entries in this window)",
    ])
    return EventsContext(text, numbered)
