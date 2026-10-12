"""Calendar quick-add: read the sentence with code first.

"interview with Acme Tuesday 2pm" names its own day and time. Code can read
that in microseconds; a model call (with the standing persona-and-vault
prompt, about 26k tokens) is only worth making when the sentence is missing
a day or a time. `parse` returns what it found and says whether that is
enough to write an event; the route asks a model, with a tiny prompt, only
when it is not.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_MONTHS = ("january|february|march|april|may|june|july|august|september|october|"
           "november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec")
_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_AMPM = r"(?:am|pm|a\.m\.|p\.m\.)"

# An address starts where a run of address characters starts: a scan never retries
# the same run from each of its characters, so a long run without an "@" costs one pass.
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_RANGE_RE = re.compile(
    r"(?:\b(?:at|from)\s+|@\s*)?\b(\d{1,2})(?::(\d{2}))?\s*(" + _AMPM + r")?\s*"
    r"(?:-|–|to|until)\s*(\d{1,2})(?::(\d{2}))?\s*(" + _AMPM + r")(?![\w])", re.I)
_TIME12_RE = re.compile(
    r"(?:\b(?:at|from)\s+|@\s*)?\b(\d{1,2})(?::(\d{2}))?\s*(" + _AMPM + r")(?![\w])", re.I)
_TIME24_RE = re.compile(
    r"(?:\b(?:at|from)\s+|@\s*)?\b([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*" + _AMPM + ")", re.I)
_NOON_RE = re.compile(r"(?:\b(?:at|from)\s+)?\bnoon\b", re.I)
_DURATION_RE = re.compile(
    r"\bfor\s+(?:(an?|half an)\s+hour|(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m))\b", re.I)
_DATE_RES = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b(?:on\s+)?(?:(?:this|next)\s+)?(?:" + _DAYS + r")\b", re.I),
    re.compile(r"\b(?:today|tomorrow|yesterday)\b", re.I),
    re.compile(r"\b(?:on\s+)?(?:" + _MONTHS + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?\b", re.I),
    re.compile(r"\b(?:on\s+)?\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + _MONTHS + r")\b\.?", re.I),
    re.compile(r"\b\d{1,2}/\d{1,2}\b"),
)
_EDGE_WORDS = {"on", "at", "for", "by", "from", "to", "until", "with", "and", "@", "-", "–", ",", ";", ":"}


def _hm(h, m, mer):
    """12-hour (or 24-hour when `mer` is empty) clock -> (hour, minute) or None."""
    h, m = int(h), int(m or 0)
    if m > 59:
        return None
    if mer:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if mer.lower().startswith("p") else 0)
    elif h > 23:
        return None
    return h, m


def _blank(text, span):
    return text[:span[0]] + " " + text[span[1]:]


def _strip_edges(title):
    words = title.split()
    while words and words[0].lower() in _EDGE_WORDS:
        words.pop(0)
    while words and words[-1].lower() in _EDGE_WORDS:
        words.pop()
    return " ".join(words).strip(" ,;:-–")


def parse(text, now=None):
    """Read `text` as a calendar event without a model.

    Returns {"complete": bool, "fields": {title, start_time, end_time,
    location, attendees}}. `complete` is True only when a day, a start time
    and a title were all found; `fields` carries whatever was found (empty
    strings for the rest) so the route can hand a model only what is missing.
    """
    from agent_friday.services.desktop_targets import _parse_day
    now = now or datetime.now()
    raw = " ".join(str(text or "").split())
    rest = raw
    attendees = []
    for m in _EMAIL_RE.finditer(rest):
        if m.group(0) not in attendees:
            attendees.append(m.group(0))
    rest = _EMAIL_RE.sub(" ", rest)

    start_hm = end_hm = None
    m = _RANGE_RE.search(rest)
    if m:
        a_mer = m.group(3) or m.group(6)
        start_hm, end_hm = _hm(m.group(1), m.group(2), a_mer), _hm(m.group(4), m.group(5), m.group(6))
        if start_hm and end_hm:
            rest = _blank(rest, m.span())
        else:
            start_hm = end_hm = None
    if start_hm is None:
        for rx in (_TIME12_RE, _TIME24_RE):
            m = rx.search(rest)
            if m:
                start_hm = _hm(m.group(1), m.group(2), m.group(3) if rx is _TIME12_RE else "")
                if start_hm:
                    rest = _blank(rest, m.span())
                    break
        if start_hm is None:
            m = _NOON_RE.search(rest)
            if m:
                start_hm = (12, 0)
                rest = _blank(rest, m.span())
    minutes = None
    m = _DURATION_RE.search(rest)
    if m:
        if m.group(1):
            minutes = 30 if m.group(1).lower().startswith("half") else 60
        else:
            n = float(m.group(2))
            minutes = int(n * 60) if m.group(3).lower().startswith("h") else int(n)
        rest = _blank(rest, m.span())

    day = _parse_day(rest, today=now.date())
    if (day is not None and day < now.date()
            and not re.search(r"\b(?:yesterday|\d{4}-\d{2}-\d{2})\b", raw, re.I)):
        # "3/5" said in October means next March, not a date already gone.
        try:
            day = day.replace(year=day.year + 1)
        except ValueError:
            day = None
    for rx in _DATE_RES:
        rest = rx.sub(" ", rest)
    title = _strip_edges(" ".join(rest.split()))
    if title:
        title = title[0].upper() + title[1:]

    start_time = end_time = ""
    if day is not None and start_hm is not None:
        sdt = datetime(day.year, day.month, day.day, start_hm[0], start_hm[1])
        # A weekday that is today but already past means the next one.
        if (sdt < now and re.search(r"\b(?:" + _DAYS + r")\b", raw, re.I)
                and not re.search(r"\b(?:today|\d{4}-\d{2}-\d{2})\b", raw, re.I)):
            sdt += timedelta(days=7)
        if end_hm is not None:
            edt = sdt.replace(hour=end_hm[0], minute=end_hm[1])
            if edt <= sdt:
                edt += timedelta(days=1)
        else:
            edt = sdt + timedelta(minutes=minutes or 60)
        start_time = sdt.isoformat(timespec="minutes")
        end_time = edt.isoformat(timespec="minutes")
    return {
        "complete": bool(start_time and title),
        "fields": {"title": title, "start_time": start_time, "end_time": end_time,
                   "location": "", "attendees": attendees},
    }
