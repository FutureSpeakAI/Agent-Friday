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
    _generate_text,
    _get_friday_system_prompt,
)  # noqa: E501

calendar_bp = Blueprint('calendar', __name__)



@calendar_bp.route('/api/calendar/today')
def api_calendar_today():
    """Today's events + gap analysis + Friday's annotation."""
    today = date.today()
    events = _enrich_events(_events_for_day(today))
    gaps = _gap_analysis(events, today)
    return jsonify({
        "status": "ok",
        "date": today.isoformat(),
        "events": events,
        "gaps": gaps,
        "annotation": _day_annotation(today, events),
        "google_connected": _google_credentials() is not None,
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
    return jsonify({
        "status": "ok", "date": d.isoformat(), "events": events,
        "gaps": _gap_analysis(events, d),
        "annotation": _day_annotation(d, events),
    })


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
        system = _get_friday_system_prompt(
            keywords=target.get("title", "") + " " + attendees, workspace="task")
        prep = _generate_text([{"role": "user", "content": prompt}],
                              system=system, max_tokens=1400, workspace='calendar')
        cache[event_id] = prep
        try:
            _save_json_dict(CAL_PREP_FILE, cache)
        except Exception:
            pass
        return jsonify({"status": "ok", "prep": prep, "cached": False})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@calendar_bp.route('/api/calendar/quick-add', methods=['POST'])
def api_calendar_quick_add():
    """Natural-language event creation. Parses with Claude, then writes to
    Google Calendar if a write scope is available, else stores locally so the
    event still appears on the timeline. Body: {text}."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"status": "error", "message": "text required"}), 400
    now = datetime.now()
    prompt = (
        "Parse this into a calendar event. Return ONLY a JSON object with keys: "
        "title (string), start_time (ISO 8601 local, no timezone), end_time "
        "(ISO 8601 local; default 1 hour after start), location (string, may be "
        "empty), attendees (array of strings, may be empty). Assume the current "
        f"date/time is {now.isoformat(timespec='minutes')}. If a weekday is "
        "named, pick the next future occurrence. Return nothing but the JSON.\n\n"
        f"Request: {text}"
    )
    try:
        system = _get_friday_system_prompt(keywords=text, workspace="task")
        raw = _generate_text([{"role": "user", "content": prompt}],
                             system=system, max_tokens=400, workspace='calendar')
        m = re.search(r"\{.*\}", raw, re.S)
        parsed = json.loads(m.group(0)) if m else {}
    except Exception as e:
        return jsonify({"status": "error", "message": f"parse failed: {e}"}), 500

    title = (parsed.get("title") or text)[:200]
    start_time = parsed.get("start_time") or ""
    end_time = parsed.get("end_time") or ""
    sdt = _parse_dt(start_time)
    if not end_time and sdt:
        end_time = (sdt + timedelta(hours=1)).isoformat(timespec="minutes")

    event = {
        "id": "local-" + uuid.uuid4().hex[:12],
        "title": title,
        "start_time": start_time,
        "end_time": end_time,
        "location": parsed.get("location") or "",
        "attendees": [a for a in (parsed.get("attendees") or []) if a],
        "description": f"Created via Quick Add: \"{text}\"",
        "all_day": False,
        "source": "local",
    }
    event["type"] = _classify_event(event)

    # Try Google insert only if a write scope was granted (read-only by default,
    # so this normally no-ops and we fall back to local — never silently expand
    # the OAuth consent the user agreed to).
    created_in_google = False
    if "https://www.googleapis.com/auth/calendar.events" in GOOGLE_SCOPES:
        creds = _google_credentials()
        if creds is not None and sdt:
            try:
                from googleapiclient.discovery import build
                svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
                body = {
                    "summary": title,
                    "location": event["location"],
                    "description": event["description"],
                    "start": {"dateTime": sdt.isoformat()},
                    "end": {"dateTime": (_parse_dt(end_time) or sdt + timedelta(hours=1)).isoformat()},
                }
                gev = svc.events().insert(calendarId="primary", body=body).execute()
                event["id"] = gev.get("id", event["id"])
                event["source"] = "google"
                created_in_google = True
            except Exception:
                created_in_google = False

    if not created_in_google:
        with _CALENDAR_LOCK:
            events = _load_local_events()
            events.append(event)
            _save_local_events(events)
    return jsonify({"status": "ok", "event": event,
                    "created_in_google": created_in_google})


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
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
