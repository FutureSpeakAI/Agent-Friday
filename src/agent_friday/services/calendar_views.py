"""Calendar views: every calendar the owner can see, in Google's own colours.

The Calendar workspace used to read one calendar: the primary calendar of the
first connected Google account. Its colours were Friday's own (career or not),
so nothing on screen matched what the owner sees in Google Calendar.

This module is the workspace's read model:

  * ``list_calendars`` reads each connected account's calendar list (the
    calendars ticked in Google, the ones hidden there, their colours and their
    default reminders). An account whose token cannot read its calendar list
    (an old connection that predates the full calendar scope) still shows its
    primary calendar, and says why the others are missing.
  * ``events_in_range`` reads the events of every calendar that is switched on
    in Friday, with the colour Google would draw (the event's own colour, else
    its calendar's), its reminder minutes, the owner's reply to an invitation,
    and whether the owner may move it.
  * which calendars are on is the owner's choice, kept in
    ``~/.friday/calendar/visible.json``; a calendar nobody has chosen for
    follows Google's own tick.

Invariants:
  * reads only. Nothing here writes to Google.
  * no new OAuth scope is asked for. A missing scope degrades one account to
    its primary calendar with a reason; it never widens consent.
  * a failed read is reported per calendar, never shown as an empty calendar.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from agent_friday.services.calendar_engine import CALENDAR_DIR

FULL_SCOPE = "https://www.googleapis.com/auth/calendar"
#: Any of these lets a token read the calendar LIST (Google's calendarList.list).
LIST_SCOPES = frozenset({
    FULL_SCOPE,
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.calendarlist",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
})

#: Google's event colour ids and the colour it documents for each (colors.get,
#: "event"). Used when an event carries its own colorId; otherwise the event
#: takes its calendar's colour from the calendar list.
EVENT_COLORS = {
    "1": "#a4bdfc", "2": "#7ae7bf", "3": "#dbadff", "4": "#ff887c",
    "5": "#fbd75b", "6": "#ffb878", "7": "#46d6db", "8": "#e1e1e1",
    "9": "#5484ed", "10": "#51b749", "11": "#dc2127",
}
#: A calendar Google gave no colour (or the legacy single-token connection).
DEFAULT_COLOR = "#9fc6e7"

VISIBLE_FILE = CALENDAR_DIR / "visible.json"
MAX_RANGE_DAYS = 62
MAX_PAGES = 4
_LOCK = threading.Lock()
_LAST_ERRORS: list[dict] = []


# ── accounts and tokens ──────────────────────────────────────────────────────

def _accounts() -> list[dict]:
    """Usable accounts with Calendar switched on, from the multi-account store.
    Empty when there is no store (a legacy single-token install)."""
    try:
        from agent_friday.services import google_accounts as ga
        if not ga.has_accounts():
            return []
        return list(ga._accounts_with("calendar"))
    except Exception:
        return []


def _service_for(account_id):
    """A Calendar client for an account id, or for the legacy token when None."""
    from agent_friday.services import calendar_write as cw
    return cw._service(account_id)


def can_list(account: dict) -> bool:
    """Whether this account's token was granted a scope that reads its calendar list."""
    return bool(set(account.get("scopes") or []) & LIST_SCOPES)


def key_of(account_id, calendar_id: str) -> str:
    """The stable key of one calendar in one account."""
    return "%s|%s" % (account_id or "legacy", calendar_id)


# ── which calendars are on ───────────────────────────────────────────────────

def _load_visible() -> dict:
    try:
        data = json.loads(VISIBLE_FILE.read_text(encoding="utf-8"))
        return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        return {}


def set_visible(changes: dict) -> dict:
    """Switch calendars on or off in Friday (never in Google). `changes` maps a
    calendar key to True/False. Returns the whole stored choice."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("name at least one calendar to switch")
    with _LOCK:
        cur = _load_visible()
        for k, v in changes.items():
            k = str(k)
            if "|" not in k or len(k) > 400:
                raise ValueError("not a calendar key: %r" % k[:60])
            cur[k] = bool(v)
        CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
        tmp = VISIBLE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(VISIBLE_FILE)
    _drop_cache()
    return cur


def _drop_cache():
    try:
        from agent_friday.services import swr_cache
        swr_cache.invalidate("calendar.")
    except Exception:
        pass


# ── the calendar list ────────────────────────────────────────────────────────

def _reminders(raw) -> list[dict]:
    out = []
    for r in raw or []:
        try:
            out.append({"method": str(r.get("method") or "popup"), "minutes": int(r.get("minutes"))})
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def _list_one_account(acct: dict | None) -> tuple[list[dict], dict]:
    """(calendars, account note) for one account; acct None = the legacy token."""
    aid = acct.get("id") if acct else None
    label = (acct or {}).get("label") or (acct or {}).get("email") or "Google"
    note = {"account_id": aid, "label": label, "email": (acct or {}).get("email") or "",
            "can_list": bool(acct) and can_list(acct), "reason": ""}
    color = DEFAULT_COLOR
    if acct:
        try:
            from agent_friday.services import google_accounts as ga
            color = ga._account_color(acct) or DEFAULT_COLOR
        except Exception:
            pass
    primary_only = [{"key": key_of(aid, "primary"), "account_id": aid, "account_label": label,
                     "calendar_id": "primary", "name": label, "color": color, "text_color": "#000000",
                     "primary": True, "access_role": "owner", "google_selected": True,
                     "default_reminders": [], "time_zone": "", "listed": False}]
    if not note["can_list"]:
        note["reason"] = ("This connection can read only its main calendar. Reconnect Google in "
                          "Settings > Connections to see the others." if acct else
                          "The older Google connection reads only the main calendar.")
        return primary_only, note
    svc, err = _service_for(aid)
    if svc is None:
        note["reason"] = "Couldn't read the calendar list: %s" % err
        return primary_only, note
    items, token = [], None
    try:
        for _ in range(MAX_PAGES):
            kw = {"maxResults": 250, "showHidden": True}
            if token:
                kw["pageToken"] = token
            resp = svc.calendarList().list(**kw).execute()
            items.extend(resp.get("items") or [])
            token = resp.get("nextPageToken")
            if not token:
                break
    except Exception as e:
        note["reason"] = "Couldn't read the calendar list (%s)." % type(e).__name__
        return primary_only, note
    cals = []
    for it in items:
        if it.get("deleted"):
            continue
        cid = str(it.get("id") or "")
        if not cid:
            continue
        is_primary = bool(it.get("primary"))
        cals.append({
            "key": key_of(aid, "primary" if is_primary else cid), "account_id": aid,
            "account_label": label, "calendar_id": "primary" if is_primary else cid,
            "name": it.get("summaryOverride") or it.get("summary") or cid,
            "color": it.get("backgroundColor") or color, "text_color": it.get("foregroundColor") or "#000000",
            "primary": is_primary, "access_role": it.get("accessRole") or "reader",
            "google_selected": bool(it.get("selected")) and not it.get("hidden"),
            "default_reminders": _reminders(it.get("defaultReminders")),
            "time_zone": it.get("timeZone") or "", "listed": True,
        })
    cals.sort(key=lambda c: (not c["primary"], c["name"].lower()))
    return (cals or primary_only), note


def list_calendars() -> dict:
    """Every calendar of every connected account, each with `on` (shown in Friday)."""
    accts = _accounts()
    if accts:
        per = [_list_one_account(a) for a in accts]
    else:
        svc, _err = _service_for(None)
        per = [_list_one_account(None)] if svc is not None else []
    chosen = _load_visible()
    cals, notes = [], []
    for cs, note in per:
        notes.append(note)
        for c in cs:
            c = dict(c)
            c["on"] = chosen.get(c["key"], c["google_selected"] or c["primary"])
            cals.append(c)
    return {"calendars": cals, "accounts": notes}


def cached_calendars(fresh_for: float = 600) -> dict:
    from agent_friday.services import swr_cache
    value, _ = swr_cache.get("calendar.list", list_calendars, fresh_for=fresh_for, max_age=3600)
    return value


# ── events ───────────────────────────────────────────────────────────────────

def _self_rsvp(ev: dict) -> str:
    for a in ev.get("attendees") or []:
        if a.get("self"):
            return str(a.get("responseStatus") or "needsAction")
    return ""


def normalize(ev: dict, cal: dict) -> dict | None:
    """One Google event as the workspace draws it; None for a cancelled one."""
    if ev.get("status") == "cancelled":
        return None
    s, e = ev.get("start") or {}, ev.get("end") or {}
    rem = ev.get("reminders") or {}
    if rem.get("useDefault", True) and not rem.get("overrides"):
        reminders, source = list(cal.get("default_reminders") or []), "calendar"
    else:
        reminders, source = _reminders(rem.get("overrides")), "event"
    cid = str(ev.get("colorId") or "")
    organizer_self = bool((ev.get("organizer") or {}).get("self")) or not ev.get("organizer")
    role = cal.get("access_role") or "reader"
    can_edit = role in ("owner", "writer") and (organizer_self or bool(ev.get("guestsCanModify")))
    return {
        "id": str(ev.get("id") or ""),
        "title": ev.get("summary") or "(untitled)",
        "start_time": s.get("dateTime") or s.get("date") or "",
        "end_time": e.get("dateTime") or e.get("date") or "",
        "all_day": bool(s.get("date") and not s.get("dateTime")),
        "time_zone": s.get("timeZone") or cal.get("time_zone") or "",
        "location": ev.get("location") or "",
        "attendees": [a.get("email", "") for a in ev.get("attendees") or [] if a.get("email")],
        "description": (ev.get("description") or "").strip()[:1000],
        "html_link": ev.get("htmlLink") or "",
        "source": "google",
        "account_id": cal.get("account_id"),
        "calendar_id": cal.get("calendar_id") or "primary",
        "calendar_key": cal.get("key") or "",
        "calendar_name": cal.get("name") or "",
        "color": EVENT_COLORS.get(cid) or cal.get("color") or DEFAULT_COLOR,
        "color_id": cid,
        "reminders": reminders,
        "reminders_source": source,
        "self_rsvp": _self_rsvp(ev),
        "organizer_self": organizer_self,
        "can_edit": can_edit,
        "recurring": bool(ev.get("recurringEventId")),
        "busy": ev.get("transparency") != "transparent",
    }


def _read_calendar(cal: dict, t_min: str, t_max: str) -> tuple[list[dict], str]:
    svc, err = _service_for(cal.get("account_id"))
    if svc is None:
        return [], str(err or "not connected")
    out, token = [], None
    try:
        for _ in range(MAX_PAGES):
            kw = {"calendarId": cal.get("calendar_id") or "primary", "timeMin": t_min, "timeMax": t_max,
                  "singleEvents": True, "orderBy": "startTime", "maxResults": 250}
            if token:
                kw["pageToken"] = token
            resp = svc.events().list(**kw).execute()
            for raw in resp.get("items") or []:
                n = normalize(raw, cal)
                if n:
                    out.append(n)
            token = resp.get("nextPageToken")
            if not token:
                break
    except Exception as e:
        return [], "Couldn't read %s (%s)." % (cal.get("name") or "a calendar", type(e).__name__)
    return out, ""


def _bounds(start, end) -> tuple[str, str]:
    a = start if getattr(start, "tzinfo", None) else start.astimezone()
    b = end if getattr(end, "tzinfo", None) else end.astimezone()
    return a.isoformat(), b.isoformat()


def _rank(ev: dict, cal_by_key: dict) -> tuple:
    """When the same event sits on two calendars (an invitation on a shared one),
    keep the copy on a calendar the owner can edit, then the primary one."""
    cal = cal_by_key.get(ev.get("calendar_key")) or {}
    return (not ev.get("can_edit"), not cal.get("primary"), ev.get("calendar_key") or "")


def events_in_range(start: datetime, end: datetime, *, keys=None, calendars=None) -> dict:
    """Events of the switched-on calendars in [start, end). `keys` narrows to some
    calendars (still only those switched on unless named). Returns
    {events, errors, calendars}."""
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValueError("ask for at most %d days at once" % MAX_RANGE_DAYS)
    listing = calendars if calendars is not None else list_calendars()
    cals = listing.get("calendars") or []
    want = set(keys or [])
    chosen = [c for c in cals if (c["key"] in want) or (not want and c.get("on"))]
    t_min, t_max = _bounds(start, end)
    errors, found = [], []
    if chosen:
        with ThreadPoolExecutor(max_workers=min(6, len(chosen))) as pool:
            results = list(pool.map(lambda c: (c, _read_calendar(c, t_min, t_max)), chosen))
        for cal, (evs, err) in results:
            if err:
                errors.append({"calendar_key": cal["key"], "name": cal.get("name") or "", "error": err})
            found.extend(evs)
    by_key = {c["key"]: c for c in cals}
    best: dict[str, dict] = {}
    for ev in found:
        k = ev["id"] or (ev["title"] + ev["start_time"])
        if k not in best or _rank(ev, by_key) < _rank(best[k], by_key):
            best[k] = ev
    events = sorted(best.values(), key=lambda e: (str(e.get("start_time") or ""), e.get("title") or ""))
    with _LOCK:
        _LAST_ERRORS[:] = errors
    return {"events": events, "errors": errors, "calendars": cals}


def last_errors() -> list[dict]:
    with _LOCK:
        return list(_LAST_ERRORS)


def multi_calendar_available() -> bool:
    """Whether the multi-account store is in use (the legacy single-token
    install keeps the old one-calendar read)."""
    return bool(_accounts())
