"""A6 — authoritative clock (Incident 2, F3; seats-and-transparency).

2026-08-13: the seated model labeled 8/14 a "Thursday" (it is a Friday) and
defended the error when challenged. Root cause: there was no authoritative
clock in context at all — the only date injections were date-only strings
inside TIER_2 sections that vault gating REDACTS for cloud providers — so
models did weekday arithmetic themselves, badly.

Three rules, enforced here:
1. The server injects the current datetime, weekday, and timezone into every
   turn's context as a TIER_1 section (survives vault gating on any seat).
2. Every date appearing in a tool result carries a CODE-COMPUTED weekday
   annotation — models never derive weekdays.
3. The injected block instructs the model to trust only this clock and the
   annotations.
"""
from __future__ import annotations

import re
from datetime import date, datetime

_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                  "Saturday", "Sunday")


def now_local() -> datetime:
    """The one clock. Timezone-aware local time."""
    return datetime.now().astimezone()


def weekday_of(year: int, month: int, day: int):
    """Code-computed weekday name, or None for an invalid date."""
    try:
        return date(year, month, day).strftime("%A")
    except ValueError:
        return None


def clock_context_block(now: datetime = None) -> str:
    """The == AUTHORITATIVE CLOCK == prompt section, TIER_1 by contract."""
    now = now or now_local()
    tz = now.tzname() or "local"
    offset = now.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"
    return (
        "== AUTHORITATIVE CLOCK ==\n"
        f"Current datetime: {now.strftime('%Y-%m-%d %H:%M')} "
        f"({now.strftime('%A')}), timezone {tz} ({offset}).\n"
        "This clock is authoritative — use it for any date, time, or weekday "
        "question. NEVER derive a weekday yourself: dates in tool results "
        "carry a code-computed weekday in parentheses. If a date has no "
        "weekday annotation, give the date without naming a weekday. If the "
        "user disputes a date or weekday, re-read this clock and the "
        "annotations instead of defending your arithmetic."
    )


# ISO dates always; US-style only when a 4-digit year is present ("3/4 cup"
# and bare "8/14" are too ambiguous to annotate — the prompt rule covers
# them by forbidding unannotated weekday naming).
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

_ALREADY_ANNOTATED = re.compile(
    r"^\s*\(?(?:%s)" % "|".join(_WEEKDAY_NAMES), re.IGNORECASE)


def annotate_weekdays(text: str) -> str:
    """Append a code-computed ' (Weekday)' after every unambiguous date in
    `text` that isn't already followed by a weekday name. Invalid dates are
    left untouched. Idempotent."""
    if not text or ("-" not in text and "/" not in text):
        return text

    def _iso(m):
        wd = weekday_of(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if not wd or _ALREADY_ANNOTATED.match(text[m.end():m.end() + 12]):
            return m.group(0)
        return f"{m.group(0)} ({wd})"

    def _us(m):
        wd = weekday_of(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if not wd or _ALREADY_ANNOTATED.match(text[m.end():m.end() + 12]):
            return m.group(0)
        return f"{m.group(0)} ({wd})"

    text = _ISO_DATE_RE.sub(_iso, text)
    text = _US_DATE_RE.sub(_us, text)
    return text
