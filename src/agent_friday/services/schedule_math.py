"""When a calendar-timed schedule fires: daily, weekly and monthly, in local time.

Pure functions, no store and no clock of their own: the scheduler passes its
own `now` (an aware local datetime in normal running, a fake one in tests).

Local wall-clock time is what the owner chose ("every Monday at 9"), so the
arithmetic is done on dates and wall-clock times, never by adding 24 hours to
a timestamp. Across a daylight-saving change the job keeps its 9:00. Two edges
of a DST change, both resolved by the zone itself:

* a time that does not exist (02:30 on the spring-forward day) runs at the
  first real moment after the gap, 03:30;
* a time that happens twice (01:30 on the fall-back day) runs once, at the
  first of the two.

Monthly: `spec.day` is 1-31, or -1 for the last day of the month. A day the
month does not have runs on its last day (the 31st runs on Feb 28 or 29), so a
monthly job never skips a month.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

CALENDAR_TRIGGERS = ("daily", "weekly", "monthly")

#: How far to look for the next or previous slot. A monthly job always has one
#: within 31 days; a year leaves room for any valid spec and stops a bad one.
_HORIZON_DAYS = 400


def _hm(spec):
    return int(spec.get("hour", 9)), int(spec.get("minute", 0))


def weekdays(spec):
    """The days a weekly schedule fires on, Monday=0 (`weekdays` list, or the
    older single `weekday`)."""
    days = spec.get("weekdays")
    if isinstance(days, (list, tuple)):
        out = sorted({int(d) for d in days if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6})
        if out:
            return out
    return [int(spec.get("weekday", 6))]


def month_day(spec, year, month) -> int:
    """The day of `year`-`month` a monthly schedule fires on."""
    last = calendar.monthrange(year, month)[1]
    try:
        want = int(spec.get("day", 1))
    except (TypeError, ValueError):
        want = 1
    if want <= 0:
        return last
    return min(want, last)


def _fires_on(trigger, spec, d: date) -> bool:
    if trigger == "daily":
        return True
    if trigger == "weekly":
        return d.weekday() in weekdays(spec)
    if trigger == "monthly":
        return d.day == month_day(spec, d.year, d.month)
    return False


def _at(d: date, spec, tz) -> datetime:
    hour, minute = _hm(spec)
    dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)
    if tz is None:
        return dt
    # Round-trip through the epoch: a wall time inside a spring-forward gap
    # comes back as the real time just after it; every other time is unchanged.
    return datetime.fromtimestamp(dt.timestamp(), tz)


def next_fire(trigger, spec, after: datetime):
    """The first slot strictly after `after`, as a datetime in its zone, or None."""
    if trigger not in CALENDAR_TRIGGERS:
        return None
    spec = spec or {}
    d = after.date()
    for _ in range(_HORIZON_DAYS):
        if _fires_on(trigger, spec, d):
            at = _at(d, spec, after.tzinfo)
            if at > after:
                return at
        d += timedelta(days=1)
    return None


def prev_fire(trigger, spec, at: datetime):
    """The latest slot at or before `at`, or None."""
    if trigger not in CALENDAR_TRIGGERS:
        return None
    spec = spec or {}
    d = at.date()
    for _ in range(_HORIZON_DAYS):
        if _fires_on(trigger, spec, d):
            when = _at(d, spec, at.tzinfo)
            if when <= at:
                return when
        d -= timedelta(days=1)
    return None
