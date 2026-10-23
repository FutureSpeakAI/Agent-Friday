"""Receipts and Undo for Friday's own Google Calendar and Google Tasks changes (F2; BD-06,
NS-34.11-2).

Before this, creating, changing or deleting a calendar event or a Google Task left no receipt in
the action journal, so "undo that" had nothing to put back, and the only record was the
checkpoint's decision. Now each change the model makes through those tools writes one receipt
(services/action_journal) that says what changed and keeps what Undo needs:

  calendar  create  -> Undo deletes the event Friday created (guests are not told)
            update  -> Undo sets back the fields the change touched, as they were
            delete  -> Undo makes the event again from what it was (a new event: Google keeps
                       no undo for a delete; guests are not invited again, and the receipt says so)
            move    -> (organize_calendar's existing rows) Undo moves it back
  tasks     create  -> Undo deletes the task Friday created
            update / complete -> Undo sets back the fields as they were (complete -> not done)
            delete  -> Undo makes the task again from what it was (a new task)

Putting back writes to Google, so it goes through one approval card exactly as the calendar
move's Undo does (services/item_actions.undo). A receipt that could not keep what Undo needs (the
item could not be read first) says so, and has nothing to undo.

A failure to write a receipt never undoes or blocks the change itself: it is logged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger("friday.change_receipts")

#: Fields of an event that Undo can set back (the receipt keeps them whole, not the 300 characters
#: the tool result shows).
EVENT_FIELDS = ("summary", "location", "description", "start", "end")
TASK_FIELDS = ("title", "notes", "due", "status")


def _journal():
    from agent_friday.services import action_journal
    return action_journal


def _record(tool: str, action: str, domain: str, *, items: list, summary: str, said: str,
            undo_rows: List[dict], conversation_id: Optional[str]) -> Optional[dict]:
    try:
        return _journal().record(tool, action, domain, items=items, summary=summary,
                                 status="complete", undo={domain: undo_rows} if undo_rows else {},
                                 conversation_id=conversation_id, extra={"said": said})
    except Exception as e:  # noqa: BLE001 - the change happened; only its receipt is missing
        _log.warning("receipt not recorded for %s: %s", tool, type(e).__name__)
        return None


def _title(s: Any, fallback: str) -> str:
    s = str(s or "").strip()
    return (s[:80] + "…") if len(s) > 80 else (s or fallback)


# ── Calendar ────────────────────────────────────────────────────────────────

def calendar_created(res: dict, *, conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("ok") and res.get("id")):
        return None
    t = _title(res.get("title"), "an event")
    return _record("create_calendar_event", "create", "calendar",
                   items=[{"id": res["id"], "title": t, "start": res.get("start")}],
                   summary="Created “%s” on the calendar." % t, said="Created an event.",
                   undo_rows=[{"op": "delete_created", "id": res["id"], "title": t,
                               "account": res.get("account_id") or "",
                               "calendar": res.get("calendar_id") or "primary"}],
                   conversation_id=conversation_id)


def calendar_updated(res: dict, *, conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("ok") and res.get("id")) or res.get("unchanged"):
        return None
    before = res.get("undo_before") or {}
    changed = [k for k in EVENT_FIELDS if k in (res.get("set") or {})]
    fields = {k: before.get(k, "") for k in changed if k in before}
    t = _title(before.get("summary") or res.get("title"), "an event")
    # an all-day event's times are dates, which a change of times cannot set back
    timed = all("T" in str(fields.get(k) or "") for k in ("start", "end") if k in fields)
    rows = ([{"op": "restore_fields", "id": res["id"], "title": t, "fields": fields,
              "account": res.get("account_id") or "", "calendar": res.get("calendar_id") or "primary"}]
            if fields and len(fields) == len(changed) and timed else [])
    return _record("update_calendar_event", "update", "calendar",
                   items=[{"id": res["id"], "title": t, "changed": changed}],
                   summary="Changed %s of “%s”%s." % (", ".join(changed) or "nothing", t,
                                                             "" if rows else " (it cannot be undone: "
                                                             "the event could not be read first)"),
                   said="Changed an event.", undo_rows=rows, conversation_id=conversation_id)


def calendar_deleted(res: dict, *, conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("ok") and res.get("id")) or res.get("already_gone"):
        return None
    b = res.get("undo_before") or res.get("before") or {}
    t = _title(b.get("summary") or b.get("title"), "an event")
    can = bool(b.get("start")) and "T" in str(b.get("start")) and "T" in str(b.get("end") or "")
    guests = int((res.get("before") or {}).get("guests") or 0)
    rows = ([{"op": "recreate", "id": res["id"], "title": t,
              "fields": {k: b.get(k, "") for k in EVENT_FIELDS if k != "summary"} | {"summary": t},
              "guests": guests, "account": res.get("account_id") or "",
              "calendar": res.get("calendar_id") or "primary"}] if can else [])
    note = ("" if can else " (an all-day event cannot be made again by Undo)") + (
        " Undo makes it again without its %d guest(s)." % guests if can and guests else "")
    return _record("delete_calendar_event", "delete", "calendar",
                   items=[{"id": res["id"], "title": t}],
                   summary="Deleted “%s” from the calendar.%s" % (t, note),
                   said="Deleted an event.", undo_rows=rows, conversation_id=conversation_id)


def undo_calendar_row(cw, r: dict) -> bool:
    """Put back one calendar change. True when Google confirmed it."""
    op = r.get("op") or "move"
    acct, cal = r.get("account") or None, r.get("calendar") or "primary"
    if op == "move":
        res = cw.update_event(r["id"], start=r["start"], end=r["end"], account_id=acct, calendar_id=cal)
        return not res.get("error")
    if op == "delete_created":
        res = cw.delete_event(r["id"], calendar_id=cal, account_id=acct, notify_guests=False)
        return bool(res.get("ok") or res.get("gone"))     # already deleted by the owner: put back
    if op == "restore_fields":
        f = r.get("fields") or {}
        res = cw.update_event(r["id"], title=f.get("summary"), location=f.get("location"),
                              description=f.get("description"), start=f.get("start") or None,
                              end=f.get("end") or None, allow_clearing=True,
                              account_id=acct, calendar_id=cal)
        return bool(res.get("ok"))
    if op == "recreate":
        f = r.get("fields") or {}
        res = cw.create_event(title=f.get("summary") or r.get("title") or "(no title)",
                              start=f.get("start") or "", end=f.get("end") or "",
                              location=f.get("location") or "", description=f.get("description") or "",
                              account_id=acct, calendar_id=cal)
        return bool(res.get("ok"))
    return False


# ── Google Tasks ────────────────────────────────────────────────────────────

def task_created(res: dict, *, tasklist_id: str, conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("id") and not res.get("error")):
        return None
    t = _title(res.get("title"), "a task")
    return _record("create_task", "create", "tasks",
                   items=[{"id": res["id"], "title": t}],
                   summary="Added the task “%s”." % t, said="Added a task.",
                   undo_rows=[{"op": "delete_created", "id": res["id"], "title": t,
                               "account": res.get("account_id") or "", "tasklist": tasklist_id}],
                   conversation_id=conversation_id)


def task_changed(res: dict, before: Optional[dict], *, tool: str, changed: List[str], tasklist_id: str,
                 conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("id") and not res.get("error")):
        return None
    t = _title((before or {}).get("title") or res.get("title"), "a task")
    fields = {k: (before or {}).get(k, "") for k in changed} if before else {}
    if "status" in fields and fields["status"] not in ("needsAction", "completed"):
        fields["status"] = "needsAction"
    rows = ([{"op": "restore", "id": res["id"], "title": t, "fields": fields,
              "account": res.get("account_id") or "", "tasklist": tasklist_id}] if fields else [])
    what = "Marked “%s” done" % t if tool == "complete_task" else "Changed %s of “%s”" % (
        ", ".join(changed), t)
    return _record(tool, "complete" if tool == "complete_task" else "update", "tasks",
                   items=[{"id": res["id"], "title": t, "changed": changed}],
                   summary=what + ("." if rows else " (it cannot be undone: the task could not be read first)."),
                   said="Changed a task.", undo_rows=rows, conversation_id=conversation_id)


def task_deleted(res: dict, before: Optional[dict], *, tasklist_id: str,
                 conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("deleted")) or res.get("already_gone"):
        return None
    t = _title((before or {}).get("title"), "a task")
    rows = ([{"op": "recreate", "id": res.get("id"), "title": t,
              "fields": {k: (before or {}).get(k, "") for k in TASK_FIELDS},
              "account": res.get("account_id") or "", "tasklist": tasklist_id}] if before else [])
    return _record("delete_task", "delete", "tasks", items=[{"id": res.get("id"), "title": t}],
                   summary="Deleted the task “%s”%s." % (
                       t, "" if rows else " (it cannot be undone: the task could not be read first)"),
                   said="Deleted a task.", undo_rows=rows, conversation_id=conversation_id)


def undo_task_row(ga, r: dict) -> bool:
    """Put back one Google Tasks change. True when Google confirmed it."""
    op, acct, tl = r.get("op"), r.get("account") or "", r.get("tasklist") or "@default"
    f = r.get("fields") or {}
    if op == "delete_created":
        res = ga.delete_task(account_id=acct, tasklist_id=tl, task_id=r["id"])
        return bool(res.get("deleted"))
    if op == "restore":
        res = ga.update_task(account_id=acct, tasklist_id=tl, task_id=r["id"],
                             title=f.get("title") if "title" in f else None,
                             notes=f.get("notes") if "notes" in f else None,
                             due=f.get("due") if "due" in f else None,
                             status=f.get("status") if "status" in f else None)
        return bool(res.get("id")) and not res.get("error")
    if op == "recreate":
        res = ga.create_task(account_id=acct, title=f.get("title") or r.get("title") or "(untitled)",
                             tasklist_id=tl, notes=f.get("notes") or "", due=f.get("due") or "")
        if res.get("error") or not res.get("id"):
            return False
        if f.get("status") == "completed":
            done = ga.complete_task(account_id=acct, tasklist_id=tl, task_id=res["id"])
            return bool(done.get("id")) and not done.get("error")
        return True
    return False


def undo_rows(receipt: dict, domain: str) -> List[dict]:
    return list(((receipt or {}).get("undo") or {}).get(domain) or [])
