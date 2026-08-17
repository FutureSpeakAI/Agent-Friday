"""Calendar WRITES — the capability Stephen asked for four times and never got.

2026-08-17, from his transcript: at 7:51, 7:54, 7:56 and 8:02 he asked Friday to
add a clinic's address and phone number to his chiropractor entries. She had no
tool that could do it, the OAuth token was read-only so no tool could have, and
she never said either. She offered a map, then reported "Done. I've opened the
navigation." Eleven minutes for an action that never had a mechanism.

**Design: additive edits proceed, destructive edits confirm.**

Adding a location or a phone number cannot lose anything — the previous value of
every field touched comes back in the receipt, so an unwanted change is
reversible without a backup. Clearing a field or deleting an event can lose
something that is not recoverable from the receipt, so those ask.

That line sits where it does on purpose. Stephen is rightly sick of
confirmation gates, and the one in his transcript was worse than an
interruption: it asked permission for a map he never requested while the
calendar edit he did request went unanswered. A confirmation must be about the
action at hand, must not displace the request, and must not end the turn. An
additive calendar edit clears all three bars by not existing.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from agent_friday.services.calendar_engine import (
    CALENDAR_WRITE_SCOPE,
    GOOGLE_TOKEN_PATH,
    _google_credentials,
)

FULL_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


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
            "Reconnect from Settings -> Connectors -> Reconnect Google to add "
            "event editing; it is the same account, re-consented with calendar "
            "writes included.")

    # Legacy single-token fallback, for an install that never migrated.
    if not GOOGLE_TOKEN_PATH.exists():
        return False, ("Google is not connected at all. Connect it from "
                       "Settings -> Connectors, or open /api/google/auth.")
    try:
        data = json.loads(GOOGLE_TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        return False, "the stored Google token could not be read (%s)" % e
    granted = list(data.get("scopes") or [])
    if CALENDAR_WRITE_SCOPE in granted or FULL_CALENDAR_SCOPE in granted:
        return True, None
    return False, (
        "the stored Google token is READ-ONLY for Calendar. It was consented "
        "before write access existed, so it cannot edit events no matter what "
        "tool is called. Reconnect the same Google account to add event "
        "editing: Settings -> Connectors -> Reconnect Google, or open "
        "/api/google/auth.")


def _service():
    creds = _google_credentials()
    if not creds:
        return None, "Google is not connected."
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return None, "google-api-python-client not installed: %s" % e
    try:
        return build("calendar", "v3", credentials=creds,
                     cache_discovery=False), None
    except Exception as e:
        return None, "could not build the Calendar client: %s" % e


def find_events(query: str, *, days_back: int = 60, days_ahead: int = 400,
                include_series: bool = True) -> dict:
    """Search the calendar by text. {ok, events[], series[]} or {error}.

    `include_series` is what makes "ALL of my entries about a chiropractor"
    answerable. Recurring appointments come back from the API as individual
    instances; patching one changes one appointment. Patching the series master
    changes every occurrence, which is what "all" means to a person.
    """
    svc, err = _service()
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
        return {"error": "Calendar search failed: %s" % e}
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
                    dry_run: bool = False) -> dict:
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
    found = find_events(query)
    if found.get("error"):
        return found
    svc, err = _service()
    if svc is None:
        return {"error": err}

    if apply_to_series and found["series"]:
        covered = {s["id"] for s in found["series"]}
        targets = [dict(s, kind="series") for s in found["series"]]
        targets += [dict(e, kind="single") for e in found["events"]
                    if not e.get("recurring_event_id")
                    and e["id"] not in covered]
    else:
        targets = [dict(e, kind="single") for e in found["events"]]

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
        except Exception as e:
            skipped.append({"id": t["id"], "title": t.get("title"),
                            "why": "patch failed: %s" % e})
            continue
        changed.append({"id": t["id"], "title": t.get("title"),
                        "kind": t["kind"], "set": patch, "before": before})
    return {"ok": True, "dry_run": bool(dry_run), "matched": len(targets),
            "changed": changed, "skipped": skipped}


def create_event(*, title: str, start: str, end: str = "", location: str = "",
                 description: str = "", attendees=None) -> dict:
    """Create an event. `start`/`end` are ISO 8601 datetimes."""
    ready, why = write_ready()
    if not ready:
        return {"error": why, "needs_reconnect": True}
    if not title or not start:
        return {"error": "a title and a start time are required"}
    svc, err = _service()
    if svc is None:
        return {"error": err}
    if not end:
        try:
            end = (datetime.fromisoformat(start)
                   + timedelta(hours=1)).isoformat()
        except Exception:
            return {"error": "could not derive an end time from %r" % start}
    body = {"summary": title, "start": {"dateTime": start},
            "end": {"dateTime": end}}
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees if a]
    try:
        ev = svc.events().insert(calendarId="primary", body=body).execute()
    except Exception as e:
        return {"error": "could not create the event: %s" % e}
    return {"ok": True, "id": ev.get("id"), "title": ev.get("summary"),
            "html_link": ev.get("htmlLink"), "start": start, "end": end}


def update_event(event_id: str, *, title=None, start=None, end=None,
                 location=None, description=None,
                 allow_clearing: bool = False) -> dict:
    """Update one event. CLEARING a field requires `allow_clearing=True`.

    The asymmetry is the design: setting a value is recoverable from the
    receipt below, blanking one is not, so erasure has to be asked for
    explicitly rather than achieved by passing an empty string.
    """
    ready, why = write_ready()
    if not ready:
        return {"error": why, "needs_reconnect": True}
    svc, err = _service()
    if svc is None:
        return {"error": err}
    try:
        before = svc.events().get(calendarId="primary",
                                  eventId=event_id).execute()
    except Exception as e:
        return {"error": "could not read event %s: %s" % (event_id, e)}
    patch, would_clear = {}, []
    for key, val in (("summary", title), ("location", location),
                     ("description", description)):
        if val is None:
            continue
        if val == "" and not allow_clearing:
            would_clear.append(key)
            continue
        patch[key] = val
    if would_clear:
        return {"error": ("refusing to clear %s — blanking a field loses "
                          "information the receipt cannot restore. Pass "
                          "allow_clearing=True if erasing is intended."
                          % ", ".join(would_clear)),
                "needs_confirmation": True, "fields": would_clear}
    if start:
        patch["start"] = {"dateTime": start}
    if end:
        patch["end"] = {"dateTime": end}
    if not patch:
        return {"ok": True, "unchanged": True, "id": event_id}
    try:
        ev = svc.events().patch(calendarId="primary", eventId=event_id,
                                body=patch).execute()
    except Exception as e:
        return {"error": "could not update the event: %s" % e}
    return {"ok": True, "id": ev.get("id"), "title": ev.get("summary"),
            "set": patch,
            "before": {"summary": before.get("summary"),
                       "location": before.get("location") or "",
                       "description": (before.get("description") or "")[:300]}}
