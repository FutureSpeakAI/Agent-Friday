"""Offering meeting times: free slots across every calendar, holds, booking.

The flow this serves: someone writes "can we talk next week?". Friday finds
times that are free on EVERY connected calendar (`find_free_slots`), places
tentative holds on the owner's own calendar so those times stay free while the
other person chooses (`hold_slots`), drafts the reply offering them through the
existing `draft_email` approval card, and, once a time is picked, turns that
hold into the real event with the invitation and releases the other holds
(`book_slot`).

Invariants:

* Free/busy reads every calendar-enabled account. A calendar that could not be
  read is named in the answer and the answer is marked incomplete; it is never
  treated as free.
* Working hours and working days are wall-clock times in the owner's time zone
  (settings `scheduling.timezone`, else this computer's zone). Slot arithmetic
  runs in UTC, so a day on which the clocks change still yields the offered
  wall-clock hours.
* A hold goes on ONE named account's own primary calendar. It never carries
  attendees, sends no notifications, and carries a private marker
  (extendedProperties.private) naming its series.
* Nothing is deleted unless a fresh read of the event shows Friday's active
  hold marker for that very series and no attendees. Titles are never trusted
  as a marker.
* Booking turns the marker to "booked" before any release runs, so a release
  can never delete the meeting that was booked.

Governance (see governance/action_gate.py): `find_free_slots` is internal (a
read); `hold_slots` and `book_slot` are outward; `release_holds` is internal
because it can only undo Friday's own marked holds.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone

HOLD_MARK = "friday_hold"
HOLD_SERIES = "friday_hold_series"
HOLD_ACTIVE = "1"
HOLD_BOOKED = "booked"
HOLD_PREFIX = "Hold: "
MAX_HOLDS = 10
MAX_WINDOW_DAYS = 31
STEP_MINUTES = 30

_DEFAULTS = {
    "timezone": "",
    "working_hours": {"start": "09:00", "end": "17:00"},
    "working_days": [0, 1, 2, 3, 4],
    "min_notice_hours": 12,
    "buffer_minutes": 15,
}
_SERIES_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


class SchedulingError(ValueError):
    """A request that cannot be answered as asked; the message says why."""


# ── settings and time ──────────────────────────────────────────────────────

def scheduling_settings() -> dict:
    """The `scheduling` settings block over its defaults."""
    out = dict(_DEFAULTS)
    out["working_hours"] = dict(_DEFAULTS["working_hours"])
    try:
        from agent_friday.core import _load_settings
        raw = (_load_settings() or {}).get("scheduling") or {}
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for k in _DEFAULTS:
            if k in raw and raw[k] is not None and k != "working_hours":
                out[k] = raw[k]
        wh = raw.get("working_hours")
        if isinstance(wh, dict):
            out["working_hours"].update({k: v for k, v in wh.items()
                                         if k in ("start", "end") and v})
    return out


def zone_for(name):
    """A ZoneInfo for an IANA name, or None for this computer's own zone."""
    name = str(name or "").strip()
    if not name:
        return None
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name)
    except Exception:
        raise SchedulingError("unknown time zone %r in settings "
                              "(scheduling.timezone)" % name)


def _wall(d: date, t: dtime, zone) -> datetime:
    """The instant a wall-clock time on a date names in `zone`."""
    naive = datetime.combine(d, t)
    return naive.replace(tzinfo=zone) if zone else naive.astimezone()


def _in_zone(dt: datetime, zone) -> datetime:
    return dt.astimezone(zone) if zone else dt.astimezone()


def _hhmm(value, what) -> dtime:
    try:
        h, m = str(value).strip().split(":")[:2]
        return dtime(int(h), int(m))
    except Exception:
        raise SchedulingError("working hours %s %r is not HH:MM" % (what, value))


def parse_instant(value, zone, *, end_of_day: bool = False) -> datetime:
    """An aware datetime from ISO 8601 or a bare date.

    A bare date is midnight at the start of that day in `zone`, or with
    `end_of_day` midnight at its end. A datetime without an offset is read
    as wall-clock time in `zone`.
    """
    s = str(value or "").strip()
    if not s:
        raise SchedulingError("a date or time is missing")
    try:
        if len(s) == 10:
            d = date.fromisoformat(s)
            if end_of_day:
                d += timedelta(days=1)
            return _wall(d, dtime(0, 0), zone)
        if s[-1] in "Zz":
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise SchedulingError("%r is not an ISO 8601 date or time" % value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone) if zone else dt.astimezone()
    return dt


def merge_intervals(intervals) -> list:
    """Sorted, merged (start, end) pairs; touching intervals join."""
    out = []
    for s, e in sorted((s, e) for s, e in intervals if e > s):
        if out and s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


# ── free/busy across every account ─────────────────────────────────────────

def _calendar_client(creds):
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _sources() -> tuple:
    """([(name, creds)], [error]) for every calendar Friday can read."""
    from agent_friday.services import google_accounts as ga
    try:
        accts = [a for a in (ga.list_accounts() or []) if isinstance(a, dict)]
    except Exception as e:
        return [], ["the account list could not be read (%s)" % e]
    srcs, errors = [], []
    if accts:
        for a in accts:
            if (a.get("services") or {}).get("calendar", True) is False:
                continue
            name = a.get("label") or a.get("email") or a.get("id")
            if not (a.get("health") or {}).get("healthy",
                                                a.get("status") == "connected"):
                errors.append("%s: needs reconnecting" % name)
                continue
            try:
                creds = ga.credentials_for(a.get("id"))
            except Exception as e:
                creds, why = None, str(e)
            else:
                why = "needs reconnecting"
            if not creds:
                errors.append("%s: %s" % (name, why))
                continue
            srcs.append((name, creds))
        return srcs, errors
    from agent_friday.services.calendar_engine import _google_credentials
    creds = _google_credentials()
    if creds:
        srcs.append(("Google Calendar", creds))
    return srcs, errors


def busy_across_accounts(time_min: datetime, time_max: datetime, zone) -> dict:
    """{busy: merged [(start, end)], checked: [names], errors: [str]}.

    One freebusy query per account, on its primary calendar. Busy intervals
    that come back as bare dates (whole days) are read in the owner's zone.
    """
    srcs, errors = _sources()
    busy, checked = [], []
    body = {"timeMin": time_min.astimezone(timezone.utc).isoformat(),
            "timeMax": time_max.astimezone(timezone.utc).isoformat(),
            "items": [{"id": "primary"}]}
    if zone is not None:
        body["timeZone"] = str(zone)
    for name, creds in srcs:
        try:
            resp = _calendar_client(creds).freebusy().query(body=body).execute()
        except Exception as e:
            errors.append("%s: free/busy failed (%s)" % (name, e))
            continue
        ok = True
        for cal in (resp.get("calendars") or {}).values():
            if cal.get("errors"):
                ok = False
                reasons = ", ".join(str(x.get("reason") or x)
                                    for x in cal.get("errors"))
                errors.append("%s: %s" % (name, reasons))
                continue
            for b in cal.get("busy") or []:
                try:
                    busy.append((parse_instant(b.get("start"), zone),
                                 parse_instant(b.get("end"), zone)))
                except SchedulingError:
                    ok = False
                    errors.append("%s: an unreadable busy block" % name)
        if ok:
            checked.append(name)
    return {"busy": merge_intervals(busy), "checked": checked, "errors": errors}


# ── slot generation ────────────────────────────────────────────────────────

def _clashes(s, e, busy, buffer) -> bool:
    return any(s < be + buffer and e > bs - buffer for bs, be in busy)


def generate_slots(busy, *, window_start: datetime, window_end: datetime,
                   earliest: datetime, duration_minutes: int, count: int,
                   buffer_minutes: int, working_start: dtime,
                   working_end: dtime, working_days, zone,
                   step_minutes: int = STEP_MINUTES) -> list:
    """Up to `count` (start, end) UTC pairs, spread across days.

    Candidates start on the working-day grid (every `step_minutes` from the
    start of working hours), finish within working hours and the window,
    begin no earlier than `earliest`, and keep `buffer_minutes` clear of
    every busy block. The first pick is the earliest free slot on each day in
    turn, so three offers land on three days when three days have room.
    """
    dur = timedelta(minutes=int(duration_minutes))
    buf = timedelta(minutes=int(buffer_minutes))
    step = timedelta(minutes=int(step_minutes))
    days = {int(d) for d in working_days}
    lo = max(window_start, earliest)
    per_day = []
    d = _in_zone(window_start, zone).date()
    last = _in_zone(window_end, zone).date()
    while d <= last:
        if d.weekday() in days:
            ds = _wall(d, working_start, zone).astimezone(timezone.utc)
            de = min(_wall(d, working_end, zone), window_end).astimezone(timezone.utc)
            cands, t = [], ds
            while t + dur <= de:
                if t >= lo and not _clashes(t, t + dur, busy, buf):
                    cands.append(t)
                t += step
            if cands:
                per_day.append(cands)
        d += timedelta(days=1)
    chosen = []
    while len(chosen) < count:
        progressed = False
        for cands in per_day:
            if len(chosen) >= count:
                break
            for c in cands:
                if not any(c < o + dur + buf and c + dur + buf > o for o in chosen):
                    chosen.append(c)
                    progressed = True
                    break
        if not progressed:
            break
    return [(c, c + dur) for c in sorted(chosen)]


def _label(s: datetime, e: datetime) -> str:
    return "%s %d %s, %s-%s %s" % (s.strftime("%a"), s.day, s.strftime("%b"),
                                   s.strftime("%H:%M"), e.strftime("%H:%M"),
                                   s.tzname() or "")


def find_free_slots(*, duration_minutes: int = 30, window_start=None,
                    window_end=None, count: int = 3, min_notice_hours=None,
                    buffer_minutes=None, now: datetime | None = None) -> dict:
    """Times free on every connected calendar. Read-only."""
    try:
        cfg = scheduling_settings()
        zone = zone_for(cfg.get("timezone"))
        duration = int(duration_minutes or 30)
        count = int(count or 3)
        if not 5 <= duration <= 480:
            raise SchedulingError("duration must be between 5 and 480 minutes")
        if not 1 <= count <= MAX_HOLDS:
            raise SchedulingError("count must be between 1 and %d" % MAX_HOLDS)
        notice = float(cfg["min_notice_hours"] if min_notice_hours is None
                       else min_notice_hours)
        buffer = int(cfg["buffer_minutes"] if buffer_minutes is None
                     else buffer_minutes)
        if notice < 0 or buffer < 0:
            raise SchedulingError("notice and buffer cannot be negative")
        wh = cfg["working_hours"]
        w_start, w_end = _hhmm(wh.get("start"), "start"), _hhmm(wh.get("end"), "end")
        if w_end <= w_start:
            raise SchedulingError("working hours end before they start")
        now = now or datetime.now(timezone.utc)
        ws = parse_instant(window_start, zone) if window_start else now
        we = (parse_instant(window_end, zone, end_of_day=True) if window_end
              else ws + timedelta(days=7))
        if we <= ws:
            raise SchedulingError("the window ends before it starts")
        if we - ws > timedelta(days=MAX_WINDOW_DAYS):
            raise SchedulingError("the window is longer than %d days"
                                  % MAX_WINDOW_DAYS)
    except SchedulingError as e:
        return {"error": str(e)}
    except (TypeError, ValueError) as e:
        return {"error": "bad request: %s" % e}

    fb = busy_across_accounts(ws, we, zone)
    if not fb["checked"]:
        return {"error": "no calendar could be read, so no time can be called "
                         "free: %s" % ("; ".join(fb["errors"])
                                       or "Google is not connected"),
                "unreadable": fb["errors"]}
    pairs = generate_slots(fb["busy"], window_start=ws, window_end=we,
                           earliest=now + timedelta(hours=notice),
                           duration_minutes=duration, count=count,
                           buffer_minutes=buffer, working_start=w_start,
                           working_end=w_end,
                           working_days=cfg.get("working_days") or [],
                           zone=zone)
    slots = []
    for s, e in pairs:
        ls, le = _in_zone(s, zone), _in_zone(e, zone)
        slots.append({"start": ls.isoformat(), "end": le.isoformat(),
                      "label": _label(ls, le)})
    out = {"ok": True, "slots": slots,
           "timezone": str(zone) if zone else "this computer's time zone",
           "calendars_checked": fb["checked"],
           "complete": not fb["errors"]}
    if fb["errors"]:
        out["unreadable"] = fb["errors"]
        out["warning"] = ("these times do not account for the calendars "
                          "listed in 'unreadable'; say so before offering them")
    if not slots:
        out["note"] = ("no free time in that window within working hours; "
                       "try a wider window or a shorter meeting")
    return out


# ── holds ──────────────────────────────────────────────────────────────────

def _marker(ev: dict) -> dict:
    return ((ev or {}).get("extendedProperties") or {}).get("private") or {}


def is_friday_hold(ev: dict, series_id: str) -> bool:
    """Is this event an ACTIVE Friday hold of this series, with nobody
    invited? The only test that permits deleting an event."""
    m = _marker(ev)
    return (bool(series_id) and m.get(HOLD_MARK) == HOLD_ACTIVE
            and m.get(HOLD_SERIES) == series_id
            and not (ev or {}).get("attendees")
            and (ev or {}).get("status") != "cancelled")


def _check_series(series_id) -> str:
    sid = str(series_id or "").strip()
    if not _SERIES_RE.match(sid):
        raise SchedulingError("a hold series id is letters, digits, _ and - "
                              "(the series_id hold_slots returned)")
    return sid


def _write_setup(account_id):
    """(aid, svc) for a write, or raise SchedulingError with the reason."""
    from agent_friday.services import calendar_write as cw
    ready, why = cw.write_ready()
    if not ready:
        raise SchedulingError(why or "calendar writes are not available")
    aid, err = cw.resolve_write_account(account_id)
    if err:
        raise SchedulingError(err)
    svc, err = cw._service(aid)
    if svc is None:
        raise SchedulingError(err or "the Calendar client could not be built")
    return aid, svc


def _series_events(svc, series_id) -> list:
    items, token = [], None
    while True:
        kw = {"calendarId": "primary", "singleEvents": True, "maxResults": 100,
              "privateExtendedProperty": "%s=%s" % (HOLD_SERIES, series_id)}
        if token:
            kw["pageToken"] = token
        resp = svc.events().list(**kw).execute()
        items += resp.get("items") or []
        token = resp.get("nextPageToken")
        if not token or len(items) >= 500:
            return items


def hold_slots(*, title: str, slots, account_id=None, series_id=None) -> dict:
    """Place tentative "Hold: <title>" events on the owner's own calendar.

    No attendees, no notifications, private visibility, and the hold marker.
    Returns the series id that `book_slot` and `release_holds` take.
    """
    from agent_friday.services import calendar_write as cw
    try:
        if not isinstance(slots, list) or not slots:
            raise SchedulingError("give the slots to hold, each with a start "
                                  "and an end")
        if len(slots) > MAX_HOLDS:
            raise SchedulingError("at most %d holds at once" % MAX_HOLDS)
        zone = zone_for(scheduling_settings().get("timezone"))
        pairs = []
        for sl in slots:
            if not isinstance(sl, dict) or not sl.get("start") or not sl.get("end"):
                raise SchedulingError("every slot needs a start and an end")
            s, e = parse_instant(sl["start"], zone), parse_instant(sl["end"], zone)
            if e <= s:
                raise SchedulingError("a slot ends before it starts")
            pairs.append((s, e))
        series = _check_series(series_id) if series_id else "hs" + uuid.uuid4().hex[:12]
        gated, gerr = cw._gate_calendar_field(str(title or "meeting").strip(), "title")
        if gerr or not gated:
            raise SchedulingError("the hold title was refused: %s"
                                  % (gerr or "it held only private content"))
        aid, svc = _write_setup(account_id)
    except SchedulingError as e:
        return {"error": str(e)}
    holds = []
    for s, e in pairs:
        body = {
            "summary": HOLD_PREFIX + gated,
            "start": {"dateTime": s.isoformat()},
            "end": {"dateTime": e.isoformat()},
            "status": "tentative",
            "transparency": "opaque",
            "visibility": "private",
            "description": ("Tentative hold placed by Friday while a time is "
                            "chosen. Friday releases it when another time is "
                            "booked."),
            "extendedProperties": {"private": {HOLD_MARK: HOLD_ACTIVE,
                                               HOLD_SERIES: series}},
        }
        try:
            ev = svc.events().insert(calendarId="primary", body=body,
                                     sendUpdates="none").execute()
        except Exception as ex:
            cw._drop_calendar_cache()
            return {"error": "placing a hold failed: %s" % ex, "partial": True,
                    "series_id": series, "account_id": aid, "holds": holds}
        holds.append({"id": ev.get("id"), "start": body["start"]["dateTime"],
                      "end": body["end"]["dateTime"]})
    cw._drop_calendar_cache()
    return {"ok": True, "series_id": series, "account_id": aid, "holds": holds}


def _release(svc, series_id, keep_event_id=None) -> dict:
    released, left = [], []
    for item in _series_events(svc, series_id):
        eid = item.get("id")
        if not eid or eid == keep_event_id:
            continue
        try:
            fresh = svc.events().get(calendarId="primary", eventId=eid).execute()
        except Exception as e:
            left.append({"id": eid, "why": "could not be re-read (%s)" % e})
            continue
        if not is_friday_hold(fresh, series_id):
            left.append({"id": eid, "why": "not an active Friday hold of this "
                                           "series; left alone"})
            continue
        try:
            svc.events().delete(calendarId="primary", eventId=eid,
                                sendUpdates="none").execute()
        except Exception as e:
            left.append({"id": eid, "why": "delete failed (%s)" % e})
            continue
        released.append({"id": eid, "start": ((fresh.get("start") or {})
                                              .get("dateTime") or "")})
    return {"released": released, "left_alone": left}


def release_holds(*, series_id, account_id=None) -> dict:
    """Delete the still-active holds of one series, verified one by one."""
    from agent_friday.services import calendar_write as cw
    try:
        series = _check_series(series_id)
        aid, svc = _write_setup(account_id)
        res = _release(svc, series)
    except SchedulingError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": "releasing holds failed: %s" % e}
    cw._drop_calendar_cache()
    return dict(res, ok=True, series_id=series, account_id=aid)


def book_slot(*, series_id, title: str, hold_event_id=None, start=None,
              attendees=None, description: str = "", location: str = "",
              account_id=None) -> dict:
    """Turn one hold into the real event, invite the attendees, and release
    the rest of the series."""
    from agent_friday.services import calendar_write as cw
    try:
        series = _check_series(series_id)
        emails = []
        for a in ([attendees] if isinstance(attendees, str) else attendees or []):
            for part in str(a).replace(";", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                if not _EMAIL_RE.match(part):
                    raise SchedulingError("%r is not an email address" % part)
                if part.lower() not in {x.lower() for x in emails}:
                    emails.append(part)
        if not str(title or "").strip():
            raise SchedulingError("the meeting needs a title")
        gt, gerr = cw._gate_calendar_field(str(title).strip(), "title")
        if gerr or not gt:
            raise SchedulingError("the title was refused: %s"
                                  % (gerr or "it held only private content"))
        gd, derr = cw._gate_calendar_field(description or "", "description")
        gl, lerr = cw._gate_calendar_field(location or "", "location")
        if derr or lerr:
            raise SchedulingError("the details were refused: %s" % (derr or lerr))
        aid, svc = _write_setup(account_id)
        if not hold_event_id:
            if not start:
                raise SchedulingError("name the hold to book: hold_event_id, "
                                      "or the start time it was placed at")
            want = parse_instant(start, zone_for(scheduling_settings().get("timezone")))
            matches = []
            for ev in _series_events(svc, series):
                st = (ev.get("start") or {}).get("dateTime")
                try:
                    if st and parse_instant(st, None) == want and is_friday_hold(ev, series):
                        matches.append(ev.get("id"))
                except SchedulingError:
                    continue
            if len(matches) != 1:
                raise SchedulingError("no single active hold of this series "
                                      "starts at %s" % start)
            hold_event_id = matches[0]
        ev = svc.events().get(calendarId="primary", eventId=hold_event_id).execute()
        if not is_friday_hold(ev, series):
            raise SchedulingError("event %s is not an active Friday hold of "
                                  "series %s; nothing was changed"
                                  % (hold_event_id, series))
    except SchedulingError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": "booking failed before anything changed: %s" % e}

    patch = {"summary": gt, "status": "confirmed", "transparency": "opaque",
             "visibility": "default", "description": gd or "",
             "extendedProperties": {"private": {HOLD_MARK: HOLD_BOOKED,
                                                HOLD_SERIES: series}}}
    if gl:
        patch["location"] = gl
    if emails:
        patch["attendees"] = [{"email": a} for a in emails]
    try:
        booked = svc.events().patch(
            calendarId="primary", eventId=hold_event_id, body=patch,
            sendUpdates="all" if emails else "none").execute()
    except Exception as e:
        return {"error": "booking failed: %s" % e}
    try:
        rel = _release(svc, series, keep_event_id=hold_event_id)
    except Exception as e:
        rel = {"released": [], "left_alone": [],
               "error": "the other holds could not be released (%s)" % e}
    cw._drop_calendar_cache()
    return {"ok": True, "account_id": aid, "series_id": series,
            "event": {"id": booked.get("id") or hold_event_id,
                      "title": booked.get("summary") or gt,
                      "start": (booked.get("start") or ev.get("start") or {}).get("dateTime"),
                      "end": (booked.get("end") or ev.get("end") or {}).get("dateTime"),
                      "html_link": booked.get("htmlLink")},
            "invited": emails, **rel}
