"""Calendar WRITES — editing events, with a token that can actually do it.

Invariant: a calendar edit the user asks for must either be performed by a
real tool on a token with write scope, or be refused out loud. Offering a
substitute (a map, a navigation link) and reporting "Done" for an action that
never had a mechanism is the failure this module exists to prevent.

**Design: additive edits proceed, destructive edits confirm.**

Adding a location or a phone number cannot lose anything — the previous value of
every field touched comes back in the receipt, so an unwanted change is
reversible without a backup. Clearing a field or deleting an event can lose
something that is not recoverable from the receipt, so those ask.

That line sits where it does on purpose. The maintainer's ruling is that
confirmation gates are a cost: a gate that asks permission for something the
user never requested, while the edit they did request goes unanswered, is
worse than an interruption. A confirmation must be about the action at hand,
must not displace the request, and must not end the turn. An additive
calendar edit clears all three bars by not existing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from agent_friday.services.calendar_engine import (
    CALENDAR_WRITE_SCOPE,
    GOOGLE_TOKEN_PATH,
    _drop_calendar_cache,
    _google_credentials,
)

FULL_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"

_log = logging.getLogger("friday.calendar_write")

#: What a route may show the browser for a failed write, by the `code` the
#: result carries. A result's `error` is for the model and may name the event
#: or the accounts; a route answers with `public_error(result)`, which is only
#: ever one of these sentences. No sentence here or in any `error` is built
#: from an exception: the exception's type goes to the local log.
ERROR_MESSAGES = {
    "bad_calendar": "that is not a calendar id; nothing was changed",
    "needs_reconnect": ("Google Calendar cannot be written to right now; reconnect "
                        "Google in Settings > Connections. Nothing was changed."),
    "needs_account": "that Google account cannot write to this calendar right now; nothing was changed",
    "no_service": "Google Calendar could not be reached; nothing was changed",
    "missing_fields": "a title and a start time are required",
    "title_refused": "could not create the event: the privacy gate kept the title on this device",
    "bad_end": "could not work out an end time from the start time",
    "create_failed": "could not create the event; Google did not accept it",
    "bad_response": "a reply is one of: accepted, tentative, declined",
    "read_failed": "could not read that event from Google",
    "no_invitation": "there is no invitation for you on this event to reply to",
    "organiser": "you organise this event; there is no invitation to reply to",
    "reply_failed": "could not send the reply; Google did not accept it",
    "search_failed": "Calendar search failed",
    "update_failed": "could not update the event; Google did not accept it",
}
GENERIC_ERROR = "the calendar change could not be made; nothing was changed"


def _fail(code: str, text=None, **extra) -> dict:
    """A failed result: `error` (the model's sentence, `ERROR_MESSAGES[code]`
    unless a fuller one is given) and `code` (what the browser is told)."""
    return {"error": ERROR_MESSAGES[code] if text is None else text, "code": code, **extra}


def public_error(result) -> str:
    """The sentence a route shows for a failed `result`: the fixed message for
    its code, never the result's own `error` text."""
    code = str(result.get("code") or "") if isinstance(result, dict) else ""
    return ERROR_MESSAGES.get(code, GENERIC_ERROR)


def _logged(what: str, exc: BaseException) -> None:
    """Log a Google or library failure by its type (and HTTP status) only: the
    message can quote a request, a token or event text, and stays out of
    results and logs."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    _log.warning("%s: %s%s", what, type(exc).__name__,
                 (" (HTTP %s)" % status) if isinstance(status, int) else "")


def _cal(calendar_id) -> str:
    """The calendar a write lands on: a Google calendar id, "primary" when none is
    named, "" for something that cannot be one (refused, never redirected)."""
    if calendar_id is None or str(calendar_id).strip() == "":
        return "primary"
    cid = str(calendar_id).strip()
    if len(cid) > 400 or any(c in cid for c in "\r\n/\\"):
        return ""
    return cid


def write_ready() -> tuple:
    """(ready, reason). Does the STORED token hold the write scope?

    Checked against the token's own recorded scopes, not the constant the code
    asks for — those are precisely the two things that were allowed to disagree
    here. `routes/calendar.py` has been testing for a write scope since before
    one was ever requested.
    """
    # The MULTI-ACCOUNT store is where the live connection is, and it is checked
    # first. `~/.friday/google_token.json` does not exist on this machine —
    # google_accounts.py migrated it, encrypted it, and deleted the plaintext —
    # so a scope check that only read that file would have reported "not
    # connected" while Gmail and Calendar were demonstrably working.
    try:
        from agent_friday.services import google_accounts as ga
        accts = [a for a in (ga.list_accounts() or [])
                 if isinstance(a, dict) and a.get("status") == "connected"]
    except Exception:
        accts = []
    for a in accts:
        granted = list(a.get("scopes") or [])
        if CALENDAR_WRITE_SCOPE in granted or FULL_CALENDAR_SCOPE in granted:
            return True, None
    if accts:
        return False, (
            "the connected Google account(s) hold Calendar READ-ONLY access. "
            "Reconnect from Settings -> Connections -> Reconnect Google to add "
            "event editing; it is the same account, re-consented with calendar "
            "writes included.")

    # Legacy single-token fallback, for an install that never migrated.
    if not GOOGLE_TOKEN_PATH.exists():
        return False, ("Google is not connected at all. Connect it from "
                       "Settings -> Connections, or open /api/google/auth.")
    try:
        data = json.loads(GOOGLE_TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        _logged("reading the stored Google token", e)
        return False, "the stored Google token could not be read"
    granted = list(data.get("scopes") or [])
    if CALENDAR_WRITE_SCOPE in granted or FULL_CALENDAR_SCOPE in granted:
        return True, None
    return False, (
        "the stored Google token is READ-ONLY for Calendar. It was consented "
        "before write access existed, so it cannot edit events no matter what "
        "tool is called. Reconnect the same Google account to add event "
        "editing: Settings -> Connections -> Reconnect Google, or open "
        "/api/google/auth.")


def writable_accounts() -> list:
    """Connected accounts whose token can write events and whose Calendar
    service is switched on. Public, token-free records."""
    try:
        from agent_friday.services import google_accounts as ga
        accts = ga.list_accounts() or []
    except Exception:
        return []
    out = []
    for a in accts:
        if not isinstance(a, dict) or a.get("status") != "connected":
            continue
        if (a.get("services") or {}).get("calendar", True) is False:
            continue
        granted = list(a.get("scopes") or [])
        if CALENDAR_WRITE_SCOPE in granted or FULL_CALENDAR_SCOPE in granted:
            out.append(a)
    return out


def describe_accounts(accts) -> str:
    """'Work <w@example.com> (account_id abc123)', comma-separated."""
    parts = []
    for a in accts or []:
        label = a.get("label") or ""
        email = a.get("email") or ""
        who = ("%s <%s>" % (label, email)) if label and email else (label or email)
        parts.append("%s (account_id %s)" % (who or "account", a.get("id")))
    return ", ".join(parts)


def resolve_write_account(account_id=None) -> tuple:
    """(account_id | None, error | None) for a calendar WRITE.

    Invariant: a write names the calendar it lands on. With more than one
    account able to write, an unnamed write is refused and the refusal lists
    the accounts, because an event on the wrong calendar is seen by the wrong
    people. With exactly one writable account, that account is the answer.
    With none in the multi-account store, (None, None) selects the legacy
    single-token path.

    `account_id` matches an account's id, email or label, case-insensitively.
    """
    accts = writable_accounts()
    want = str(account_id or "").strip().lower()
    if want:
        for a in accts:
            keys = {str(a.get(k) or "").strip().lower()
                    for k in ("id", "email", "label")}
            if want in keys:
                return a.get("id"), None
        return None, ("no connected account named %r can write to a calendar. "
                      "Nothing was changed. Accounts that can: %s"
                      % (str(account_id), describe_accounts(accts) or "none"))
    if len(accts) > 1:
        return None, ("more than one Google account can write to a calendar: "
                      "%s. Say which calendar this goes on (pass account_id). "
                      "Nothing was changed." % describe_accounts(accts))
    if len(accts) == 1:
        return accts[0].get("id"), None
    return None, None


def _service(account_id=None):
    """A Calendar client for `account_id`, or for the legacy single-token
    connection when no account is named."""
    if account_id:
        try:
            from agent_friday.services import google_accounts as ga
            creds = ga.credentials_for(account_id)
        except Exception as e:
            _logged("loading an account's Google credentials", e)
            return None, "could not load that account's credentials"
        if not creds:
            return None, ("that Google account needs reconnecting; its "
                          "credentials could not be used.")
    else:
        creds = _google_credentials()
    if not creds:
        return None, "Google is not connected."
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        _logged("importing google-api-python-client", e)
        return None, "google-api-python-client is not installed"
    try:
        return build("calendar", "v3", credentials=creds,
                     cache_discovery=False), None
    except Exception as e:
        _logged("building the Calendar client", e)
        return None, "could not build the Calendar client"


def find_events(query: str, *, days_back: int = 60, days_ahead: int = 400,
                include_series: bool = True, account_id=None) -> dict:
    """Search the calendar by text. {ok, events[], series[]} or {error}.

    `include_series` is what makes "ALL of my entries about a dentist"
    answerable. Recurring appointments come back from the API as individual
    instances; patching one changes one appointment. Patching the series master
    changes every occurrence, which is what "all" means to a person.

    `account_id` selects the account searched; unnamed reads the primary one.
    """
    svc, err = _service(account_id)
    if svc is None:
        return {"error": err}
    now = datetime.now().astimezone()
    try:
        resp = svc.events().list(
            calendarId="primary",
            timeMin=(now - timedelta(days=days_back)).isoformat(),
            timeMax=(now + timedelta(days=days_ahead)).isoformat(),
            q=query, singleEvents=True, orderBy="startTime", maxResults=250,
        ).execute()
    except Exception as e:
        _logged("searching the calendar", e)
        return _fail("search_failed")
    events, master_ids = [], set()
    for ev in resp.get("items", []):
        rid = ev.get("recurringEventId")
        if rid:
            master_ids.add(rid)
        events.append({
            "id": ev.get("id"),
            "recurring_event_id": rid,
            "title": ev.get("summary", "(untitled)"),
            "start": ((ev.get("start") or {}).get("dateTime")
                      or (ev.get("start") or {}).get("date") or ""),
            "location": ev.get("location") or "",
            "description": ev.get("description") or "",
        })
    series = []
    if include_series:
        for rid in sorted(master_ids):
            try:
                m = svc.events().get(calendarId="primary",
                                     eventId=rid).execute()
            except Exception:
                continue
            series.append({
                "id": m.get("id"),
                "is_series_master": True,
                "title": m.get("summary", "(untitled)"),
                "recurrence": m.get("recurrence") or [],
                "location": m.get("location") or "",
                "description": m.get("description") or "",
            })
    return {"ok": True, "events": events, "series": series}


def _already_there(current: str, addition: str) -> bool:
    """Is `addition` effectively already present in `current`?

    A plain substring test is not enough, and that was proved against a real
    calendar: the existing location already held a fuller form of the same
    address — "<Clinic> - <Neighbourhood>, <number> <Street> Ste C-7, <City>,
    <ST> <ZIP>, USA" — while the address being added lacked the neighbourhood
    segment in the middle. Not a substring, so it appended, and the field ended
    up naming the same place twice.

    So: compare on the DISTINCTIVE tokens — the street number and street name —
    rather than the whole string. If the existing value already points at the
    same street address, there is nothing to add.
    """
    cur = " ".join((current or "").lower().split())
    add = " ".join((addition or "").lower().split())
    if not add:
        return True
    if add in cur:
        return True
    nums = [w for w in add.replace(",", " ").split() if w.isdigit() and len(w) >= 3]
    words = [w.strip(",.") for w in add.replace(",", " ").split()
             if w.isalpha() and len(w) > 3]
    if nums and all(n in cur for n in nums[:1]):
        # Same street number AND at least one shared street/name word.
        if any(w in cur for w in words[:6]):
            return True
    return False


def annotate_events(query: str, *, location: str = "", phone: str = "",
                    note: str = "", apply_to_series: bool = True,
                    dry_run: bool = False, account_id=None, event_id=None) -> dict:
    """ADD a location / phone / note to every event matching `query`.

    Strictly additive. A field that already contains the text is left alone; a
    field with different content gets the new text appended rather than
    replaced. Every previous value is returned, so nothing here needs a
    confirmation to be safe to undo.

    This is the function that answers the request in the transcript.
    """
    ready, why = write_ready()
    if not ready:
        return {"error": why, "needs_reconnect": True}
    if not (location or phone or note):
        return {"error": "nothing to add — give a location, a phone, or a note"}
    aid, acct_err = resolve_write_account(account_id)
    if acct_err:
        return {"error": acct_err, "needs_account": True}
    found = find_events(query, account_id=aid)
    if found.get("error"):
        return found
    svc, err = _service(aid)
    if svc is None:
        return {"error": err}

    if event_id:
        # One event by its id (a meeting's own calendar event): never the other events that share its title.
        found = dict(found, events=[e for e in found["events"] if e["id"] == event_id], series=[])
        if not found["events"]:
            return {"error": "that calendar event was not found, so nothing was added"}
        apply_to_series = False
    if apply_to_series and found["series"]:
        covered = {s["id"] for s in found["series"]}
        targets = [dict(s, kind="series") for s in found["series"]]
        targets += [dict(e, kind="single") for e in found["events"]
                    if not e.get("recurring_event_id")
                    and e["id"] not in covered]
    else:
        targets = [dict(e, kind="single") for e in found["events"]]

    # Classification, same as create_event/update_event: these strings are
    # model-authored and land in Google Calendar, so this function uses the
    # same gate as its siblings.
    if location:
        location, err = _gate_calendar_field(location, "location")
        if err:
            return {"error": "location refused: %s" % err}
    if phone:
        phone, err = _gate_calendar_field(phone, "phone")
        if err:
            return {"error": "phone refused: %s" % err}
    if note:
        note, err = _gate_calendar_field(note, "description")
        if err:
            return {"error": "note refused: %s" % err}
    if not (location or phone or note):
        return {"error": "nothing left to add after the privacy gate"}

    additions = []
    if phone:
        additions.append("Phone: %s" % phone)
    if note:
        additions.append(note)

    changed, skipped = [], []
    for t in targets:
        patch, before = {}, {}
        if location:
            cur = (t.get("location") or "").strip()
            before["location"] = cur
            if not cur:
                patch["location"] = location
            elif not _already_there(cur, location):
                patch["location"] = "%s | %s" % (cur, location)
        if additions:
            cur_d = (t.get("description") or "").strip()
            before["description"] = cur_d
            missing = [a for a in additions if a.lower() not in cur_d.lower()]
            if missing:
                patch["description"] = ("%s\n%s" % (cur_d, "\n".join(missing))
                                        ).strip()
        if not patch:
            skipped.append({"id": t["id"], "title": t.get("title"),
                            "why": "already has this information"})
            continue
        if dry_run:
            changed.append({"id": t["id"], "title": t.get("title"),
                            "kind": t["kind"], "would_set": patch,
                            "before": before})
            continue
        try:
            svc.events().patch(calendarId="primary", eventId=t["id"],
                               body=patch).execute()
            _drop_calendar_cache()
        except Exception as e:
            _logged("adding to a calendar event", e)
            skipped.append({"id": t["id"], "title": t.get("title"),
                            "why": "Google did not accept the change"})
            continue
        changed.append({"id": t["id"], "title": t.get("title"),
                        "kind": t["kind"], "set": patch, "before": before})
    return {"ok": True, "dry_run": bool(dry_run), "matched": len(targets),
            "account_id": aid, "changed": changed, "skipped": skipped}


def _gate_calendar_field(text: str, field: str) -> tuple[str, str]:
    """Gate calendar event prose before it reaches Google Calendar.

    security-boundary.md §19 row 7: the write itself already sits behind the
    approvals queue's outward-action card, so consent is covered — this
    closes CLASSIFICATION. Returns (gated_text, error) — error is "" unless
    the gate could not run or blocked outright, in which case the caller
    must refuse rather than send unclassified or garbled text.
    """
    if not text:
        return text, ""
    try:
        from agent_friday.services import egress_gate as _eg
    except Exception as e:
        _logged("loading the privacy gate", e)
        return "", "the privacy gate could not be reached"
    try:
        gated = _eg._gate_text(text, "google", f"calendar.{field}")
    except _eg.NeverSendBlocked as nb:
        return "", str(nb)
    except Exception as e:
        _logged("gating calendar text", e)
        return "", "the privacy gate failed"
    return gated, ""


def create_event(*, title: str, start: str, end: str = "", location: str = "",
                 description: str = "", attendees=None,
                 account_id=None, calendar_id: str = "primary") -> dict:
    """Create an event. `start`/`end` are ISO 8601 datetimes.

    `account_id` names the account; see `resolve_write_account`. `calendar_id`
    names one of that account's calendars (its primary one by default)."""
    if not _cal(calendar_id):
        return _fail("bad_calendar")
    ready, why = write_ready()
    if not ready:
        return _fail("needs_reconnect", why, needs_reconnect=True)
    if not title or not start:
        return _fail("missing_fields")
    aid, acct_err = resolve_write_account(account_id)
    if acct_err:
        return _fail("needs_account", acct_err, needs_account=True)
    svc, err = _service(aid)
    if svc is None:
        return _fail("no_service", err)
    title, title_err = _gate_calendar_field(title, "title")
    if title_err or not title:
        return _fail("title_refused", "could not create the event: title " +
                     (title_err or "contained only private content that stays "
                                   "on this device"))
    location, _le = _gate_calendar_field(location, "location")
    description, _de = _gate_calendar_field(description, "description")
    if not end:
        try:
            end = (datetime.fromisoformat(start)
                   + timedelta(hours=1)).isoformat()
        except Exception:
            return _fail("bad_end", "could not derive an end time from %r" % start)
    body = {"summary": title, "start": {"dateTime": start},
            "end": {"dateTime": end}}
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees if a]
    try:
        ev = svc.events().insert(calendarId=_cal(calendar_id), body=body).execute()
    except Exception as e:
        _logged("creating a calendar event", e)
        return _fail("create_failed")
    _drop_calendar_cache()
    return {"ok": True, "id": ev.get("id"), "title": ev.get("summary"),
            "account_id": aid, "calendar_id": _cal(calendar_id), "html_link": ev.get("htmlLink"),
            "start": start, "end": end}


def get_event(event_id: str, *, account_id=None, calendar_id: str = "primary") -> dict:
    """One event as the planner needs it: its title, when it starts and ends, its time zone, whether it is an
    all-day entry, how many guests it has, and whether this calendar's copy may change its time
    (`organizer_self`: the calendar it is read from organises it; `guests_can_modify`). Reads only."""
    if not _cal(calendar_id):
        return {"error": "that is not a calendar id; nothing was changed"}
    ready, why = write_ready()
    if not ready:
        return {"error": why, "needs_reconnect": True}
    aid, acct_err = resolve_write_account(account_id)
    if acct_err:
        return {"error": acct_err, "needs_account": True}
    svc, err = _service(aid)
    if svc is None:
        return {"error": err}
    try:
        ev = svc.events().get(calendarId=_cal(calendar_id), eventId=event_id).execute()
    except Exception as e:
        _logged("reading a calendar event", e)
        return _fail("read_failed", "could not read event %s" % event_id)
    st, en = ev.get("start") or {}, ev.get("end") or {}
    return {"ok": True, "id": ev.get("id") or event_id, "title": ev.get("summary") or "(no title)",
            "start": st.get("dateTime") or "", "end": en.get("dateTime") or "",
            "tz": st.get("timeZone") or "", "all_day": not st.get("dateTime"),
            "guests": len(ev.get("attendees") or []), "account_id": aid, "calendar_id": _cal(calendar_id),
            "organizer_self": bool((ev.get("organizer") or {}).get("self")) or not ev.get("organizer"),
            "guests_can_modify": bool(ev.get("guestsCanModify"))}


def _event_record(ev: dict, calendar_id: str) -> dict:
    """What an event was, kept with its delete so the record says what went: title, when, where, guests."""
    st, en = ev.get("start") or {}, ev.get("end") or {}
    return {"title": ev.get("summary") or "(no title)",
            "start": st.get("dateTime") or st.get("date") or "",
            "end": en.get("dateTime") or en.get("date") or "",
            "all_day": not st.get("dateTime"),
            "location": ev.get("location") or "",
            "description": (ev.get("description") or "")[:300],
            "guests": len(ev.get("attendees") or []),
            "recurring_event_id": ev.get("recurringEventId") or "",
            "calendar_id": calendar_id,
            "html_link": ev.get("htmlLink") or ""}


def read_event(event_id: str, *, calendar_id: str = "primary", account_id=None) -> dict:
    """{ok, id, account_id, before:{...}} for one event, or {error}. Reads only: the delete card names the event
    from this, so the owner approves "the dentist on Tuesday at 10", never an opaque id."""
    eid = str(event_id or "").strip()
    if not eid:
        return {"error": "an event id is required"}
    cal = str(calendar_id or "").strip() or "primary"
    ready, why = write_ready()
    if not ready:
        return {"error": why, "needs_reconnect": True}
    aid, acct_err = resolve_write_account(account_id)
    if acct_err:
        return {"error": acct_err, "needs_account": True}
    svc, err = _service(aid)
    if svc is None:
        return {"error": err}
    try:
        ev = svc.events().get(calendarId=cal, eventId=eid).execute()
    except Exception as e:
        from agent_friday.user_errors import exception_text
        status = getattr(getattr(e, "resp", None), "status", None)
        return {"error": exception_text(e, "could not read event %s: %%s" % eid.replace("%", "%%")),
                "not_found": str(status) in ("404", "410")}
    if (ev or {}).get("status") == "cancelled":
        return {"error": "that event is already deleted", "gone": True}
    return {"ok": True, "id": ev.get("id") or eid, "account_id": aid, "before": _event_record(ev, cal)}


def notify_guests_default() -> bool:
    """Whether "notify guests" starts ticked when deleting an event (Settings:
    calendar_delete_notify_guests, off unless the owner chose otherwise)."""
    try:
        from agent_friday.services import owner_choices as _oc
        return _oc.calendar_delete_notify_guests()
    except Exception:
        return False


def delete_event(event_id: str, *, calendar_id: str = "primary", account_id=None,
                 notify_guests: bool | None = None) -> dict:
    """Delete one event. {ok, id, title, guests_notified, before:{...}} or {error}.

    The event is read first: a delete that cannot say what it removed does not happen, and `before` keeps
    the title, time, place and guests for the record (Google keeps no undo the API can reach). Guests are
    told only when `notify_guests` is ticked (Google's sendUpdates=all: a cancellation email in the owner's
    name); otherwise sendUpdates=none. None means the owner's default (`notify_guests_default`). Whether
    the delete may run at all is decided before this is called: the delete_calendar_event tool is outward
    and asks first, and the Calendar route is the owner's own click."""
    got = read_event(event_id, calendar_id=calendar_id, account_id=account_id)
    if not got.get("ok"):
        return got
    tell = notify_guests_default() if notify_guests is None else bool(notify_guests)
    cal = got["before"]["calendar_id"]
    svc, err = _service(got["account_id"])
    if svc is None:
        return {"error": err}
    try:
        svc.events().delete(calendarId=cal, eventId=got["id"],
                            sendUpdates="all" if tell else "none").execute()
    except Exception as e:
        from agent_friday.user_errors import exception_text
        return {"error": exception_text(e, "could not delete the event: %s")}
    _drop_calendar_cache()
    guests = int(got["before"].get("guests") or 0)
    return {"ok": True, "id": got["id"], "title": got["before"]["title"], "account_id": got["account_id"],
            "calendar_id": cal, "guests_notified": bool(tell and guests), "notify_guests": tell,
            "guests_told": (("Its %d guest(s) were sent a cancellation." % guests) if tell and guests else
                            ("Its %d guest(s) were not told." % guests) if guests else "It had no guests."),
            "before": got["before"]}


def update_event(event_id: str, *, title=None, start=None, end=None,
                 location=None, description=None,
                 allow_clearing: bool = False, account_id=None,
                 calendar_id: str = "primary") -> dict:
    """Update one event. CLEARING a field requires `allow_clearing=True`.

    The asymmetry is the design: setting a value is recoverable from the
    receipt below, blanking one is not, so erasure has to be asked for
    explicitly rather than achieved by passing an empty string.

    `account_id` names the calendar the event lives on; see
    `resolve_write_account`.
    """
    if not _cal(calendar_id):
        return {"error": "that is not a calendar id; nothing was changed"}
    ready, why = write_ready()
    if not ready:
        return {"error": why, "needs_reconnect": True}
    aid, acct_err = resolve_write_account(account_id)
    if acct_err:
        return {"error": acct_err, "needs_account": True}
    svc, err = _service(aid)
    if svc is None:
        return {"error": err}
    try:
        before = svc.events().get(calendarId=_cal(calendar_id),
                                  eventId=event_id).execute()
    except Exception as e:
        _logged("reading a calendar event", e)
        return _fail("read_failed", "could not read event %s" % event_id)
    patch, would_clear = {}, []
    for key, val in (("summary", title), ("location", location),
                     ("description", description)):
        if val is None:
            continue
        if val == "" and not allow_clearing:
            would_clear.append(key)
            continue
        gated_val, gate_err = _gate_calendar_field(val, key)
        if gate_err or (not gated_val and val):
            return {"error": "could not update the event: %s %s"
                    % (key, gate_err or "contained only private content "
                                        "that stays on this device")}
        patch[key] = gated_val
    if would_clear:
        return {"error": ("refusing to clear %s — blanking a field loses "
                          "information the receipt cannot restore. Pass "
                          "allow_clearing=True if erasing is intended."
                          % ", ".join(would_clear)),
                "needs_confirmation": True, "fields": would_clear}
    # A patch replaces the whole start/end object: keep the event's own time zone, or a move (and its
    # Undo) would leave the event in the calendar's zone instead of the one it was made in.
    if start:
        patch["start"] = dict({"dateTime": start}, **({"timeZone": (before.get("start") or {})["timeZone"]}
                                                       if (before.get("start") or {}).get("timeZone") else {}))
    if end:
        patch["end"] = dict({"dateTime": end}, **({"timeZone": (before.get("end") or {})["timeZone"]}
                                                   if (before.get("end") or {}).get("timeZone") else {}))
    if not patch:
        return {"ok": True, "unchanged": True, "id": event_id}
    try:
        ev = svc.events().patch(calendarId=_cal(calendar_id), eventId=event_id,
                                body=patch).execute()
    except Exception as e:
        _logged("updating a calendar event", e)
        return _fail("update_failed")
    _drop_calendar_cache()
    return {"ok": True, "id": ev.get("id"), "title": ev.get("summary"),
            "account_id": aid, "calendar_id": _cal(calendar_id), "set": patch,
            "before": {"summary": before.get("summary"),
                       "location": before.get("location") or "",
                       "description": (before.get("description") or "")[:300]}}


RSVP_RESPONSES = ("accepted", "tentative", "declined")


def _account_email(account_id) -> str:
    """The connected account's own address, lowercased; '' when it is not known."""
    try:
        from agent_friday.services import google_accounts as ga
        for a in ga.list_accounts() or []:
            if isinstance(a, dict) and a.get("id") == account_id:
                return str(a.get("email") or "").lower()
    except Exception:
        pass
    return ""


def respond_to_event(event_id: str, response: str, *, account_id=None,
                     calendar_id: str = "primary") -> dict:
    """The owner's reply to an invitation: going, maybe or not going.

    Sends only the owner's own row of the guest list (the attendee Google
    marks `self`) with `attendeesOmitted`, so the other guests' rows are never
    written back from an earlier read; Google tells the organiser. Refused when the event has
    no invitation for the owner (her own event, or one she is not on).
    `before` is the reply it replaced, so the page can say what changed."""
    if not _cal(calendar_id):
        return _fail("bad_calendar")
    response = str(response or "").strip().lower()
    if response not in RSVP_RESPONSES:
        return _fail("bad_response")
    ready, why = write_ready()
    if not ready:
        return _fail("needs_reconnect", why, needs_reconnect=True)
    aid, acct_err = resolve_write_account(account_id)
    if acct_err:
        return _fail("needs_account", acct_err, needs_account=True)
    svc, err = _service(aid)
    if svc is None:
        return _fail("no_service", err)
    try:
        ev = svc.events().get(calendarId=_cal(calendar_id), eventId=event_id).execute()
    except Exception as e:
        _logged("reading a calendar event", e)
        return _fail("read_failed", "could not read event %s" % event_id)
    attendees = [dict(a) for a in (ev.get("attendees") or [])]
    # Google's `self` is "the calendar this copy sits on". On a calendar shared with her that is someone
    # else, so the row is hers only on her own primary calendar or when it carries her own address.
    own = _account_email(aid)
    mine = [a for a in attendees if a.get("self") and (_cal(calendar_id) == "primary"
                                                       or (own and str(a.get("email") or "").lower() == own))]
    if not mine:
        return _fail("no_invitation", "there is no invitation for you on “%s” to reply to"
                     % (ev.get("summary") or "this event"))
    org = ev.get("organizer") or {}
    if org.get("self") and (_cal(calendar_id) == "primary" or (own and str(org.get("email") or "").lower() == own)
                            or mine[0].get("organizer")):
        return _fail("organiser", "you organise “%s”; there is no invitation to reply to"
                     % (ev.get("summary") or "this event"))
    before = mine[0].get("responseStatus") or "needsAction"
    if before == response:
        return {"ok": True, "unchanged": True, "id": event_id, "response": response, "before": before}
    row = dict(mine[0], responseStatus=response)
    # sendUpdates takes only all, externalOnly or none: there is no organiser-only setting. A patch that
    # carries only her own row with attendeesOmitted changes only her reply, and Google's notice of a reply
    # goes to the organiser; externalOnly would skip an organiser in her own domain and none would tell
    # nobody, so the reply keeps "all".
    try:
        out = svc.events().patch(calendarId=_cal(calendar_id), eventId=event_id,
                                 body={"attendees": [row], "attendeesOmitted": True}, sendUpdates="all").execute()
    except Exception as e:
        _logged("sending an invitation reply", e)
        return _fail("reply_failed")
    _drop_calendar_cache()
    return {"ok": True, "id": out.get("id") or event_id, "title": out.get("summary") or ev.get("summary"),
            "response": response, "before": before, "account_id": aid, "calendar_id": _cal(calendar_id)}
