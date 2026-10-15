import os
import io
import json
import glob
import subprocess
import base64
import secrets
import sys
import traceback
import uuid
import threading
import asyncio
import re
import html
import calendar
import time as _time
import hashlib as _hashlib
import hmac as _hmac
import queue as _queue
import difflib as _difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.services.calendar_engine import (
    CAL_PREP_FILE,
    GOOGLE_SCOPES,
    _CALENDAR_LOCK,
    _classify_event,
    _day_annotation,
    _drop_calendar_cache,
    _enrich_events,
    _events_for_day,
    _gap_analysis,
    _google_credentials,
    _load_json_dict,
    _load_local_events,
    _parse_dt,
    _save_json_dict,
    _save_local_events,
)  # noqa: E501
from agent_friday.services.misc_engine import (
    _enrich_calendar_event,
)  # noqa: E501
from agent_friday.services.model_router import (
    _gated_vault_control,
    _generate_text,
    _get_friday_system_prompt,
    _predict_route_provider,
)  # noqa: E501
from agent_friday.routes._errors import api_error
from agent_friday.services.visible_jobs import visible_job

calendar_bp = Blueprint('calendar', __name__)


def _calendar_health():
    """Whether Calendar is usable, across every account that has it switched on.

    Imported lazily and guarded: this is a status field on a route that is
    otherwise about events, and a health helper that can take the page down is
    worse than no health helper. A failure here reads as UNKNOWN, which is not
    healthy - failing closed, per services/connector_health.py.
    """
    try:
        from agent_friday.services.google_accounts import service_health
        return service_health("calendar")
    except Exception as e:
        from agent_friday.services import connector_health as _ch
        return _ch.unknown(detail="%s: %s" % (type(e).__name__, e),
                           source="routes.calendar")



def _read_state(day):
    """{read_failed, errors} for one day's read: whether Google could not be read at
    all, and which calendars failed, so the page never draws a failure as a free day."""
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    try:
        from agent_friday.services import calendar_engine as ce
        from agent_friday.services import calendar_views as cv
        return {"read_failed": ce.range_failed(start, end), "errors": cv.errors_for(start, end)}
    except Exception:
        return {"read_failed": False, "errors": []}


def _annotation(day, events, state):
    """The day's one-line note; never written (or cached) from a read that failed."""
    if state.get("read_failed"):
        return "Couldn't read your calendar for this day, so there is nothing to say about it yet."
    return _day_annotation(day, events)


@calendar_bp.route('/api/calendar/today')
def api_calendar_today():
    """Today's events + gap analysis + Friday's annotation."""
    today = date.today()
    events = _enrich_events(_events_for_day(today))
    state = _read_state(today)
    gaps = [] if state["read_failed"] else _gap_analysis(events, today)
    return jsonify({
        "status": "ok",
        "date": today.isoformat(),
        "events": events,
        "gaps": gaps,
        **state,
        "annotation": _annotation(today, events, state),
        # Was `_google_credentials() is not None`, which asks whether the
        # PRIMARY account can produce a credential - not whether Calendar is
        # usable across the accounts that have it switched on. See
        # google_accounts.service_health.
        "google_connected": _calendar_health().healthy,
    })


@calendar_bp.route('/api/calendar/tomorrow')
def api_calendar_tomorrow():
    """Condensed preview of tomorrow's events, flagging prep-needed items."""
    tmrw = date.today() + timedelta(days=1)
    events = _enrich_events(_events_for_day(tmrw))
    return jsonify({
        "status": "ok",
        "date": tmrw.isoformat(),
        "events": events,
        "needs_prep": [e for e in events if e.get("prep_available")],
    })


@calendar_bp.route('/api/calendar/week')
def api_calendar_week():
    """7-day overview with per-day density + interview flags."""
    start = date.today()
    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        evs = _events_for_day(d)
        timed = [e for e in evs if not e.get("all_day")]
        density = "light" if len(timed) <= 1 else "medium" if len(timed) <= 3 else "heavy"
        days.append({
            "date": d.isoformat(),
            "weekday": d.strftime("%a"),
            "day": d.day,
            "count": len(evs),
            "density": density,
            "has_career": any(e.get("type") == "career" for e in evs),
            "is_today": i == 0,
        })
    return jsonify({"status": "ok", "days": days})


@calendar_bp.route('/api/calendar/day/<day_str>')
def api_calendar_day(day_str):
    """Events for an arbitrary YYYY-MM-DD (week-strip navigation)."""
    try:
        d = date.fromisoformat(day_str)
    except Exception:
        return jsonify({"status": "error", "message": "use YYYY-MM-DD"}), 400
    events = _enrich_events(_events_for_day(d))
    state = _read_state(d)
    return jsonify({
        "status": "ok", "date": d.isoformat(), "events": events, **state,
        "gaps": [] if state["read_failed"] else _gap_analysis(events, d),
        "annotation": _annotation(d, events, state),
    })


@calendar_bp.route('/api/calendar/calendars')
def api_calendar_calendars():
    """Every calendar of every connected account, its Google colour and whether
    it is switched on in Friday; plus why an account shows only its main one."""
    from agent_friday.services import calendar_views as cv
    try:
        listing = cv.cached_calendars()
    except Exception as e:
        return api_error(e, "Couldn't read your calendars")
    return jsonify({"status": "ok", "calendars": listing.get("calendars") or [],
                    "accounts": listing.get("accounts") or [], "errors": cv.last_errors()})


@calendar_bp.route('/api/calendar/calendars/visible', methods=['POST'])
def api_calendar_calendars_visible():
    """Switch calendars on or off in Friday. Nothing is written to Google.
    Body: {changes: {<calendar key>: true|false}}."""
    from agent_friday.services import calendar_views as cv
    data = request.get_json(silent=True) or {}
    try:
        chosen = cv.set_visible(data.get("changes") or {})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    return jsonify({"status": "ok", "visible": chosen})


def ce_range_failed(start, end):
    from agent_friday.services import calendar_engine as ce
    return ce.range_failed(start, end)


def _keys_arg():
    """The calendars a view is narrowed to (Friday's "only these"), from ?keys=a,b; None for all switched on."""
    keys = [k for k in (request.args.get("keys") or "").split(",") if k and "|" in k][:50]
    return keys or None


@calendar_bp.route('/api/calendar/range')
def api_calendar_range():
    """Events of the switched-on calendars from `start` to `end` (YYYY-MM-DD,
    end exclusive, at most 62 days), for the week and month views."""
    from agent_friday.services import calendar_views as cv
    try:
        d0 = date.fromisoformat(request.args.get("start") or "")
        d1 = date.fromisoformat(request.args.get("end") or "")
    except ValueError:
        return jsonify({"status": "error", "message": "use start and end as YYYY-MM-DD"}), 400
    if d1 <= d0 or (d1 - d0).days > cv.MAX_RANGE_DAYS:
        return jsonify({"status": "error",
                        "message": "end must be after start, at most %d days" % cv.MAX_RANGE_DAYS}), 400
    start = datetime(d0.year, d0.month, d0.day)
    end = datetime(d1.year, d1.month, d1.day)
    events, errors = [], []
    if cv.multi_calendar_available():
        try:
            got = cv.events_in_range(start, end, keys=_keys_arg(), calendars=cv.cached_calendars())
            events, errors = got["events"], got["errors"]
        except Exception as e:
            return api_error(e, "Couldn't read your calendars")
    else:
        for i in range((d1 - d0).days):
            day = d0 + timedelta(days=i)
            events.extend(_events_for_day(day))
            if _read_state(day)["read_failed"]:
                # The older one-calendar connection could not be read: an error, never an empty week.
                return jsonify({"status": "error", "message": "Couldn't read your calendar."}), 502
    for le in ([] if _keys_arg() else _load_local_events()):
        sdt = _parse_dt(le.get("start_time"))
        if sdt and start <= sdt < end and le.get("id") not in {e.get("id") for e in events}:
            events.append(dict(le))
    seen, merged = set(), []
    for ev in events:
        k = ev.get("id") or (ev.get("title", "") + ev.get("start_time", ""))
        if k in seen:
            continue
        seen.add(k)
        ev = dict(ev)
        ev["type"] = ev.get("type") or _classify_event(ev)
        merged.append(ev)
    merged.sort(key=lambda e: str(e.get("start_time") or ""))
    return jsonify({"status": "ok", "start": d0.isoformat(), "end": d1.isoformat(),
                    "events": _enrich_events(merged), "errors": errors})


@calendar_bp.route('/api/calendar/freebusy')
def api_calendar_freebusy():
    """Busy blocks (calendar names, never titles) and the free time inside
    working hours, per day, from `start` to `end` (YYYY-MM-DD, end exclusive, at
    most 14 days). Worked out from the switched-on calendars' events on this PC;
    no free/busy query is sent anywhere."""
    from agent_friday.services import calendar_views as cv
    try:
        d0 = date.fromisoformat(request.args.get("start") or "")
        d1 = date.fromisoformat(request.args.get("end") or "")
    except ValueError:
        return jsonify({"status": "error", "message": "use start and end as YYYY-MM-DD"}), 400
    if d1 <= d0 or (d1 - d0).days > cv.MAX_FREEBUSY_DAYS:
        return jsonify({"status": "error",
                        "message": "end must be after start, at most %d days" % cv.MAX_FREEBUSY_DAYS}), 400
    start = datetime(d0.year, d0.month, d0.day)
    end = datetime(d1.year, d1.month, d1.day)
    errors = []
    if cv.multi_calendar_available():
        try:
            got = cv.events_in_range(start, end, keys=_keys_arg(), calendars=cv.cached_calendars())
        except Exception as e:
            return api_error(e, "Couldn't read your calendars")
        events, errors = got["events"], got["errors"]
        read = [c for c in got["calendars"] if (c["key"] in (_keys_arg() or [])) or (not _keys_arg() and c.get("on"))]
        if errors and not events and len(errors) >= len(read):
            # Every calendar failed: an error, never a free week.
            return jsonify({"status": "error", "message": "Couldn't read any of your calendars, so free time "
                            "can't be worked out.", "errors": errors}), 502
    else:
        events = []
        for i in range((d1 - d0).days):
            day = d0 + timedelta(days=i)
            events.extend(_events_for_day(day))
            dstart = datetime(day.year, day.month, day.day)
            if ce_range_failed(dstart, dstart + timedelta(days=1)):
                return jsonify({"status": "error", "message": "Couldn't read your calendar, so free time "
                                "can't be worked out."}), 502
    out = cv.free_busy(events, start.astimezone(), end.astimezone())
    return jsonify({"status": "ok", "start": d0.isoformat(), "end": d1.isoformat(),
                    "errors": errors, **out})


@calendar_bp.route('/api/calendar/event/<event_id>')
def api_calendar_event(event_id):
    """Single event detail (searches today + the next 7 days + local)."""
    for i in range(8):
        d = date.today() + timedelta(days=i)
        for ev in _enrich_events(_events_for_day(d)):
            if str(ev.get("id")) == str(event_id):
                return jsonify({"status": "ok", "event": ev})
    return jsonify({"status": "not_found", "event_id": event_id}), 404


@calendar_bp.route('/api/calendar/prep/<event_id>', methods=['POST'])
@visible_job('Meeting prep', kind='meeting_prep', workspace='calendar')
def api_calendar_prep(event_id):
    """Generate (and cache) a Friday prep card for an event with attendees."""
    # Locate the event across the upcoming week.
    target = None
    for i in range(8):
        d = date.today() + timedelta(days=i)
        for ev in _events_for_day(d):
            if str(ev.get("id")) == str(event_id):
                target = ev
                break
        if target:
            break
    if not target:
        return jsonify({"status": "not_found", "event_id": event_id}), 404

    cache = _load_json_dict(CAL_PREP_FILE)
    force = (request.get_json(silent=True) or {}).get("refresh")
    if not force and event_id in cache:
        return jsonify({"status": "ok", "prep": cache[event_id], "cached": True})

    attendees = ", ".join(target.get("attendees", [])) or "no external attendees listed"
    prompt = (
        "Build a concise meeting prep card. Use this exact markdown structure:\n"
        "**Attendees & context** — who they are and our relationship\n"
        "**Last interaction** — what I last discussed with them (if known)\n"
        "**Talking points** — 3-4 sharp bullets\n"
        "**Watch-outs** — anything to be careful about\n\n"
        f"Event: {target.get('title')}\n"
        f"When: {target.get('start_time')}\n"
        f"Location/link: {target.get('location') or 'n/a'}\n"
        f"Attendees: {attendees}\n"
        f"Notes: {target.get('description') or 'none'}\n"
    )
    try:
        _kw = target.get("title", "") + " " + attendees
        system = _get_friday_system_prompt(
            keywords=_kw, workspace="task",
            provider=_predict_route_provider(keywords=_kw, workspace="task"),
            vault_control=_gated_vault_control())
        prep = _generate_text([{"role": "user", "content": prompt}],
                              system=system, max_tokens=1400, workspace='calendar')
        cache[event_id] = prep
        try:
            _save_json_dict(CAL_PREP_FILE, cache)
        except Exception:
            pass
        return jsonify({"status": "ok", "prep": prep, "cached": False})
    except Exception as e:
        return api_error(e, "Couldn't prepare for the event")


@calendar_bp.route('/api/calendar/quick-add', methods=['POST'])
@visible_job('Quick add event', kind='calendar_quick_add', workspace='calendar')
def api_calendar_quick_add():
    """Read a quick-add sentence into an event the owner can check. WRITES NOTHING.

    The sentence is read by code first (services/calendar_quickadd); a model is
    asked, with a tiny prompt, only for what code could not find. The answer is
    a preview with the calendars the event can go on; the owner's Add sends it
    to POST /api/calendar/events. Before this, quick add wrote to Google at
    once, from a model's reading the owner never saw. Body: {text}."""
    from agent_friday.services import calendar_quickadd as cq
    data = request.get_json(silent=True) or {}
    text = " ".join(str(data.get("text") or "").split())[:500]
    if not text:
        return jsonify({"status": "error", "message": "text required"}), 400
    got = cq.parse(text)
    fields, parsed_by = dict(got["fields"]), "code"
    if not got["complete"]:
        now = datetime.now()
        prompt = (
            "Parse this into a calendar event. Return ONLY a JSON object with keys: "
            "title (string), start_time (ISO 8601 local, no timezone), end_time "
            "(ISO 8601 local; default 1 hour after start), location (string, may be "
            "empty). Assume the current "
            f"date/time is {now.isoformat(timespec='minutes')}. If a weekday is "
            "named, pick the next future occurrence. Return nothing but the JSON.\n\n"
            f"Request: {text}"
        )
        try:
            raw = _generate_text([{"role": "user", "content": prompt}],
                                 system="You turn one sentence into one calendar event as JSON.",
                                 max_tokens=300, workspace='calendar')
            m = re.search(r"\{.*\}", raw or "", re.S)
            model = json.loads(m.group(0)) if m else {}
        except Exception:
            model = {}
        for k in ("title", "start_time", "end_time", "location"):
            if not fields.get(k) and isinstance(model.get(k), str):
                fields[k] = model[k][:200]
        parsed_by = "code+model"
    sdt = _local_dt(fields.get("start_time"))
    if not sdt:
        return jsonify({"status": "unparsed", "message": "Say a day and a time, like \u201clunch with Dana Tuesday 12pm\u201d.",
                        "fields": fields})
    edt = _local_dt(fields.get("end_time"))
    if not edt or edt <= sdt:
        edt = sdt + timedelta(hours=1)
    preview = {"title": (fields.get("title") or text)[:200],
               "start_time": sdt.astimezone().isoformat(timespec="minutes"),
               "end_time": edt.astimezone().isoformat(timespec="minutes"),
               "location": fields.get("location") or "",
               "attendees": [a for a in (fields.get("attendees") or []) if a][:20]}
    return jsonify({"status": "ok", "preview": preview, "parsed_by": parsed_by,
                    "calendars": _writable_calendars()})


def _local_dt(text):
    """A time as this PC's wall clock: an offset or Z is converted (a model may answer in UTC), a bare
    time is already local. None for something that is not a time."""
    if not text:
        return None
    try:
        d = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return _parse_dt(text)
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


def _writable_calendars():
    """The calendars an event can be added to or moved on: ones the owner may
    write on an account whose token can write. Empty when Google cannot write."""
    from agent_friday.services import calendar_views as cv
    from agent_friday.services import calendar_write as cw
    ok, _why = cw.write_ready()
    if not ok:
        return []
    writable = {a.get("id") for a in cw.writable_accounts()}
    try:
        cals = cv.cached_calendars().get("calendars") or []
    except Exception:
        cals = []
    out = [{"key": c["key"], "name": c.get("name") or "", "color": c.get("color") or "",
            "primary": bool(c.get("primary")), "on": bool(c.get("on"))}
           for c in cals
           if c.get("access_role") in ("owner", "writer")
           and (c.get("account_id") in writable or (c.get("account_id") is None and not writable))]
    out.sort(key=lambda c: (not c["primary"], not c["on"], c["name"].lower()))
    return out


def _calendar_of(key):
    """(account_id, calendar_id) for a calendar key from /api/calendar/calendars."""
    from agent_friday.services import calendar_views as cv
    key = str(key or "")
    for c in (cv.cached_calendars().get("calendars") or []):
        if c.get("key") == key:
            return c.get("account_id"), c.get("calendar_id") or "primary"
    return None, None


@calendar_bp.route('/api/calendar/events', methods=['POST'])
def api_calendar_create_event():
    """The owner's Add on a quick-add preview: create exactly the event shown.

    Requires confirmed: true (the owner pressed Add on the preview) and a
    calendar key from the preview's list. With no Google write access the event
    is kept on this PC and the answer says so. Body: {title, start_time,
    end_time, location, attendees, calendar_key, confirmed}."""
    from agent_friday.services import calendar_write as cw
    data = request.get_json(silent=True) or {}
    if data.get("confirmed") is not True:
        return jsonify({"status": "error", "message": "an event is added only from the owner's Add"}), 400
    title = " ".join(str(data.get("title") or "").split())[:200]
    sdt, edt = _local_dt(data.get("start_time")), _local_dt(data.get("end_time"))
    if not title or not sdt:
        return jsonify({"status": "error", "message": "a title and a start time are required"}), 400
    if not edt or edt <= sdt:
        edt = sdt + timedelta(hours=1)
    attendees = [str(a)[:200] for a in (data.get("attendees") or []) if a][:20]
    location = str(data.get("location") or "")[:300]
    writable = _writable_calendars()
    if data.get("calendar_key") and not writable:
        # The preview offered Google calendars and Google can no longer be written to: say so, never
        # quietly keep on this PC an event she asked to put on a calendar.
        return jsonify({"status": "error", "message": "Google Calendar cannot be written to right now; "
                        "reconnect Google in Settings > Connections. Nothing was added."}), 409
    if writable:
        if data.get("calendar_key") not in {c["key"] for c in writable}:
            return jsonify({"status": "error", "message": "choose a calendar you can add to"}), 400
        aid, cid = _calendar_of(data.get("calendar_key"))
        if cid is None:
            return jsonify({"status": "error", "message": "choose a calendar from the list"}), 400
        out = cw.create_event(title=title, start=sdt.astimezone().isoformat(),
                              end=edt.astimezone().isoformat(), location=location,
                              description="", attendees=attendees, account_id=aid, calendar_id=cid)
        if out.get("error"):
            return jsonify({"status": "error", "message": str(out["error"])[:300]}), 409
        _drop_calendar_cache()
        return jsonify({"status": "ok", "created_in_google": True,
                        "event": {"id": out.get("id"), "title": out.get("title") or title,
                                  "start_time": out.get("start"), "end_time": out.get("end"),
                                  "calendar_key": data.get("calendar_key")}})
    event = {"id": "local-" + uuid.uuid4().hex[:12], "title": title,
             "start_time": sdt.isoformat(timespec="minutes"), "end_time": edt.isoformat(timespec="minutes"),
             "location": location, "attendees": attendees, "description": "", "all_day": False,
             "source": "local"}
    event["type"] = _classify_event(event)
    with _CALENDAR_LOCK:
        events = _load_local_events()
        events.append(event)
        _save_local_events(events)
    return jsonify({"status": "ok", "created_in_google": False, "event": event})


@calendar_bp.route('/api/calendar/events/<event_id>/move', methods=['POST'])
def api_calendar_move_event(event_id):
    """The owner's confirmed drag: move one event to the times shown in the
    confirmation. Answers with the receipt whose Undo puts it back.
    Body: {calendar_key, start_time, end_time, confirmed}."""
    from agent_friday.services import item_actions
    data = request.get_json(silent=True) or {}
    if data.get("confirmed") is not True:
        return jsonify({"status": "error", "message": "an event is moved only after the owner confirms"}), 400
    if not event_id or len(event_id) > 300:
        return jsonify({"status": "error", "message": "not an event id"}), 400
    aid, cid = _calendar_of(data.get("calendar_key"))
    if cid is None:
        return jsonify({"status": "error", "message": "that calendar is not one of the calendars shown here"}), 400
    try:
        out = item_actions.move_event_by_owner(event_id, start=str(data.get("start_time") or ""),
                                               end=str(data.get("end_time") or ""), calendar_id=cid,
                                               account=aid or "")
    except item_actions.Refused as e:
        return jsonify({"status": "error", "message": e.user_message}), 409
    if out.get("status") != "complete":
        err = next((i.get("error") for i in out.get("items") or [] if i.get("error")), "")
        return jsonify({"status": "error", "message": err or "Google did not take the change",
                        "receipt_id": out.get("receipt_id")}), 409
    _drop_calendar_cache()
    return jsonify({"status": "ok", "receipt_id": out["receipt_id"], "text": out.get("text") or ""})


# ═══════════════════════════════════════════════════════════════
#  CALENDAR & COUNTDOWNS
# ═══════════════════════════════════════════════════════════════

@calendar_bp.route('/api/calendar')
def get_calendar():
    """Placeholder for Google Calendar integration."""
    return jsonify({"status": "placeholder", "events": []})


@calendar_bp.route('/api/calendar/enrich', methods=['POST'])
def calendar_enrich():
    """Enrich a Google Calendar event with meeting prep research.

    POST JSON:
    {
      "event_id": "google calendar event ID",
      "research": "the attendee research / meeting prep content"
    }
    """
    data = request.get_json(silent=True) or {}
    event_id = data.get('event_id', '').strip()
    research = data.get('research', '').strip()

    if not event_id:
        return jsonify({"status": "error", "message": "No event_id provided"}), 400
    if not research:
        return jsonify({"status": "error", "message": "No research content provided"}), 400

    try:
        result = _enrich_calendar_event(event_id, research)
        return jsonify({"status": "ok", **result})
    except ValueError as e:
        return api_error(e, "Couldn't enrich the calendar event", 400)
    except Exception as e:
        return api_error(e, "Couldn't enrich the calendar")
