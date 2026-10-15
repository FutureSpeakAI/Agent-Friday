"""Calendar reminders: Friday reminds the owner when Google would, and points at the prep for what matters.

Google keeps a reminder list for every event: the event's own ("30 minutes
before"), or its calendar's defaults when the event uses them. Friday reads that
list with the events (services/calendar_views) and, every few minutes, raises a
notification in its own tray for each pop-up reminder that has come due:
"Dentist in 30 minutes". An event that matters (it has other guests, or it is a
career event) says so, and the notification opens the event in the Calendar,
where its prep card is one click away.

Invariants:
  * reads only; nothing is written to Google, and no reminder is invented: an
    event with no reminder in Google gets no reminder from Friday. Email
    reminders stay Google's to send.
  * no model runs here. A prep card is made only when the owner opens it (the
    Calendar's "Meeting prep" job, which is visible in Activity).
  * each reminder fires once (a small record of what was raised, kept two days),
    never for an event that has started, and never for an invitation the owner
    declined.
  * the notification goes through the notice registry (emitter CR1) like every
    other notice, so quiet hours and Focus hold it the same way.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta

from agent_friday.services.calendar_engine import CALENDAR_DIR

FIRED_FILE = CALENDAR_DIR / "reminders_fired.json"
#: Longest reminder Friday raises. Google allows four weeks; the tick reads two days of events.
MAX_MINUTES = 24 * 60
#: A reminder found this late (the PC was asleep) is still useful until the event starts.
KEEP_DAYS = 2
_LOCK = threading.Lock()


def _aware(text):
    if not text or len(str(text)) == 10:
        return None
    try:
        d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.astimezone()


def matters(ev: dict) -> bool:
    """An event worth a prep card: other people are in it, or it is about the owner's career."""
    return bool([a for a in (ev.get("attendees") or []) if a]) or ev.get("type") == "career"


def due(events, now: datetime) -> list[tuple[dict, int]]:
    """(event, minutes) for every pop-up reminder whose moment has come and whose event has not started."""
    out = []
    for ev in events or []:
        if ev.get("all_day") or ev.get("self_rsvp") == "declined":
            continue
        start = _aware(ev.get("start_time"))
        if start is None or start <= now:
            continue
        for r in ev.get("reminders") or []:
            if (r.get("method") or "popup") != "popup":
                continue
            try:
                minutes = int(r.get("minutes"))
            except (TypeError, ValueError):
                continue
            if 0 <= minutes <= MAX_MINUTES and start - timedelta(minutes=minutes) <= now:
                out.append((ev, minutes))
    return out


def _key(ev: dict, minutes: int) -> str:
    return "%s|%s|%s|%d" % (ev.get("calendar_key") or "", ev.get("id") or "", ev.get("start_time") or "", minutes)


def _load_fired() -> dict:
    try:
        data = json.loads(FIRED_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_fired(fired: dict, today: date) -> None:
    keep = (today - timedelta(days=KEEP_DAYS)).isoformat()
    fired = {k: v for k, v in fired.items() if str(v) >= keep}
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FIRED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(fired, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(FIRED_FILE)


def _in_words(minutes: int) -> str:
    if minutes == 0:
        return "now"
    if minutes % (24 * 60) == 0:
        n = minutes // (24 * 60)
        return "in %d day%s" % (n, "" if n == 1 else "s")
    if minutes % 60 == 0:
        n = minutes // 60
        return "in %d hour%s" % (n, "" if n == 1 else "s")
    return "in %d minute%s" % (minutes, "" if minutes == 1 else "s")


def _push(**kw) -> None:
    """Seam over the local notification queue."""
    from agent_friday import notifications_engine as ne
    ne.push(**kw)


def notice_for(ev: dict, minutes: int) -> dict:
    """The notification for one due reminder: what, when, where it is, and the way to its prep."""
    start = _aware(ev.get("start_time"))
    when = start.strftime("%I:%M %p").lstrip("0") if start else ""
    where = ev.get("calendar_name") or ""
    body = when + ((" · " + where) if where else "")
    if ev.get("location"):
        body += " · " + str(ev["location"])[:80]
    if matters(ev):
        body += ". Its prep card is one click away in the Calendar."
    return {"title": "%s %s" % ((ev.get("title") or "An event")[:80], _in_words(minutes)),
            "body": body, "priority": "medium", "source": "calendar", "kind": "calendar_reminder",
            "emitter": "CR1", "dedupe_key": "calendar-reminder:%s:%d" % (ev.get("id") or "", minutes),
            "target": dict({"workspace": "calendar", "event": ev.get("id") or "",
                            "date": start.date().isoformat() if start else ""},
                           **({"prep": "1"} if matters(ev) else {}))}


def tick(now: datetime | None = None) -> dict:
    """Scheduler builtin (calendar_reminders): raise every reminder that has come due. Never raises."""
    now = now or datetime.now().astimezone()
    result = {"due": 0, "raised": 0, "errors": 0}
    try:
        from agent_friday.services import calendar_engine as ce
        events = []
        for d in (now.date(), now.date() + timedelta(days=1)):
            events.extend(ce._events_for_day(d))
    except Exception:
        result["errors"] += 1
        return result
    with _LOCK:
        fired = _load_fired()
        changed = False
        for ev, minutes in due(events, now):
            result["due"] += 1
            k = _key(ev, minutes)
            if k in fired:
                continue
            n = notice_for(ev, minutes)
            try:
                _push(title=n["title"], body=n["body"], priority="medium", source="calendar",
                      kind="calendar_reminder", emitter="CR1",
                      dedupe_key="calendar-reminder:%s:%d" % (ev.get("id") or "", minutes),
                      target=n["target"])
                result["raised"] += 1
            except Exception:
                result["errors"] += 1
                continue
            fired[k] = now.date().isoformat()
            changed = True
        if changed:
            try:
                _save_fired(fired, now.date())
            except Exception:
                result["errors"] += 1
    return result
