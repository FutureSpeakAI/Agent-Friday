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


def _safe(fn):
    """A recorder never turns a change that happened into an error: anything it raises is logged."""
    import functools

    @functools.wraps(fn)
    def wrapped(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            _log.warning("receipt not recorded (%s): %s", fn.__name__, type(e).__name__)
            return None
    return wrapped


def _sealed(fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The item's own words for Undo, sealed (services/undo_store): the row keeps a token and the
    field names, never the values. None when they cannot be kept encrypted: then no Undo."""
    from agent_friday.services import undo_store
    token = undo_store.keep(fields)
    return {"sealed": token, "field_names": sorted(fields)} if token else None


NOT_SEALED = " (it cannot be undone: what it was could not be kept encrypted)"


def _fields(r: dict) -> Optional[Dict[str, Any]]:
    """An Undo row's fields: opened from the sealed store (older rows kept them inline)."""
    if r.get("sealed"):
        from agent_friday.services import undo_store
        return undo_store.read(r["sealed"])
    f = r.get("fields")
    return dict(f) if isinstance(f, dict) else None


def _title(s: Any, fallback: str) -> str:
    s = str(s or "").strip()
    return (s[:80] + "…") if len(s) > 80 else (s or fallback)


#: How a field is named in a receipt: the receipt says WHAT changed, never what it said.
FIELD_WORDS = {"summary": "title", "location": "place", "description": "description", "start": "time",
               "end": "time", "title": "title", "notes": "notes", "due": "due date", "status": "status"}


def seal_title(title: Any) -> Optional[str]:
    """An event's or a task's title, sealed as the Undo data is (release-lead call, 2026-10-24:
    titles are not kept readable in receipts or the ledger; the screen opens them to show them).
    The token, or None when it cannot be sealed: then the receipt names no title at all."""
    s = str(title or "").strip()
    if not s:
        return None
    from agent_friday.services import undo_store
    return undo_store.keep({"title": s})


def title_of(obj: Any, fallback: str = "") -> str:
    """The title a receipt item or an Undo row stands for, opened for the screen (the receipts
    page, the live card). Older entries kept it inline."""
    if isinstance(obj, dict):
        if obj.get("title_sealed"):
            from agent_friday.services import undo_store
            got = undo_store.read(obj["title_sealed"]) or {}
            return _title(got.get("title"), fallback) if got.get("title") else fallback
        if obj.get("title"):
            return str(obj["title"])
    return fallback


#: Tools whose arguments carry an event's or a task's title (kept sealed, never in the ledger).
TITLE_TOOLS = frozenset({"create_calendar_event", "update_calendar_event", "delete_calendar_event",
                         "hold_slots", "book_slot", "create_task", "update_task", "complete_task",
                         "delete_task", "organize_calendar", "annotate_calendar_events"})

#: Argument names that carry an event's or a task's own words (kept off decided cards and out of
#: the text the dissent check records).
WORD_ARGS = ("title", "notes", "description", "location", "summary", "query", "note", "why")


def untitled_args(inp: Any) -> Any:
    """A tool's arguments without the item's own words, for text that is kept (a card's action
    description, which the dissent check may write to the ledger)."""
    if not isinstance(inp, dict):
        return inp
    return {k: ("<%s>" % k if k in WORD_ARGS and v else v) for k, v in inp.items()}


def scrub_card(approval_id: str) -> None:
    """Once a calendar or task card is decided (approved and run, declined, expired) the card on
    screen is gone, and its stored copy keeps no title or other words of the item (release-lead
    call 2026-10-24): the title is sealed, the lines and the tool's result are dropped, the card's
    texts become generic. A pending card is never touched: that is the live card. Never raises."""
    try:
        from agent_friday.services import approvals as ap
        rec = ap.get_approval(approval_id)
        if not rec or rec.get("status") == "pending":
            return
        payload = dict(rec.get("payload") or {})
        inp = payload.get("input")
        if isinstance(inp, dict) and any(inp.get(k) for k in WORD_ARGS):
            tok = seal_title(inp.get("title") or inp.get("summary"))
            payload["input"] = dict({k: v for k, v in inp.items() if k not in WORD_ARGS}, **_ts(tok))
        if payload.get("lines"):
            payload["lines"] = []
        if isinstance(payload.get("ops"), list):
            payload["ops"] = [{k: v for k, v in o.items() if k not in WORD_ARGS} if isinstance(o, dict) else o
                              for o in payload["ops"]]
        detail = rec.get("used_detail")
        payload.pop("why", None)
        fields = {"payload": payload, "title": "A calendar or task change (decided)", "provenance": None,
                  "description": "", "action_description": str(payload.get("tool") or payload.get("domain") or "")}
        if isinstance(detail, dict) and "result" in detail:
            fields["used_detail"] = {k: v for k, v in detail.items() if k != "result"}
        ap._patch(approval_id, **fields)
    except Exception as e:  # noqa: BLE001
        _log.warning("decided card not scrubbed: %s", type(e).__name__)


def _ts(token: Optional[str]) -> Dict[str, Any]:
    return {"title_sealed": token} if token else {}


def changed_words(changed: List[str]) -> str:
    words: List[str] = []
    for k in changed:
        w = FIELD_WORDS.get(k, k)
        if w not in words:
            words.append(w)
    if not words:
        return "nothing"
    return words[0] if len(words) == 1 else "%s and %s" % (", ".join(words[:-1]), words[-1])


# ── Calendar ────────────────────────────────────────────────────────────────

@_safe
def calendar_created(res: dict, *, conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("ok") and res.get("id")):
        return None
    tok = seal_title(res.get("title"))
    return _record("create_calendar_event", "create", "calendar",
                   items=[dict({"id": res["id"], "start": res.get("start")}, **_ts(tok))],
                   summary="Created an event on the calendar.", said="Created an event.",
                   undo_rows=[dict({"op": "delete_created", "id": res["id"],
                               "guests": int(res.get("guests") or 0),
                               "account": res.get("account_id") or "",
                               "calendar": res.get("calendar_id") or "primary"}, **_ts(tok))],
                   conversation_id=conversation_id)


@_safe
def calendar_updated(res: dict, *, conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("ok") and res.get("id")) or res.get("unchanged"):
        return None
    before = res.get("undo_before") or {}
    changed = [k for k in EVENT_FIELDS if k in (res.get("set") or {})]
    fields = {k: before.get(k, "") for k in changed if k in before}
    tok = seal_title(before.get("summary") or res.get("title"))
    # an all-day event's times are dates, which a change of times cannot set back
    timed = all("T" in str(fields.get(k) or "") for k in ("start", "end") if k in fields)
    undoable = bool(fields) and len(fields) == len(changed) and timed
    seal = _sealed(fields) if undoable else None
    rows = ([dict({"op": "restore_fields", "id": res["id"],
                   "account": res.get("account_id") or "", "calendar": res.get("calendar_id") or "primary"},
                  **seal, **_ts(tok))] if seal else [])
    return _record("update_calendar_event", "update", "calendar",
                   items=[dict({"id": res["id"], "changed": changed}, **_ts(tok))],
                   summary="Changed the %s of an event%s." % (
                       changed_words(changed),
                       "" if rows else NOT_SEALED if undoable else
                       " (it cannot be undone: an all-day event's dates cannot be set "
                       "back by Undo)" if fields and not timed else
                       " (it cannot be undone: the event could not be read first)"),
                   said="Changed an event.", undo_rows=rows, conversation_id=conversation_id)


@_safe
def calendar_deleted(res: dict, *, conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("ok") and res.get("id")) or res.get("already_gone"):
        return None
    b = res.get("undo_before") or res.get("before") or {}
    t = _title(b.get("summary") or b.get("title"), "an event")
    tok = seal_title(b.get("summary") or b.get("title"))
    can = (bool(b.get("start")) and "T" in str(b.get("start")) and "T" in str(b.get("end") or "")
           and not b.get("recurring"))
    guests = int((res.get("before") or {}).get("guests") or 0)
    seal = _sealed({k: b.get(k, "") for k in EVENT_FIELDS if k != "summary"}
                   | {"summary": str(b.get("summary") or b.get("title") or t)}) if can else None
    rows = ([dict({"op": "recreate", "id": res["id"],
                   "guests": guests, "account": res.get("account_id") or "",
                   "calendar": res.get("calendar_id") or "primary"}, **seal, **_ts(tok))] if seal else [])
    note = (NOT_SEALED if can and not seal else "" if can else " (a repeating event cannot be made again by Undo)" if b.get("recurring")
            else " (an all-day event cannot be made again by Undo)") + (
        " Undo makes it again without its %d guest(s)." % guests if can and guests else "")
    return _record("delete_calendar_event", "delete", "calendar",
                   items=[dict({"id": res["id"]}, **_ts(tok))],
                   summary="Deleted an event from the calendar.%s" % note,
                   said="Deleted an event.", undo_rows=rows, conversation_id=conversation_id)


def undo_calendar_row(cw, r: dict) -> bool:
    """Put back one calendar change. True when Google confirmed it; its sealed data then goes."""
    ok = _undo_calendar_row(cw, r)
    if ok and r.get("sealed"):
        from agent_friday.services import undo_store
        undo_store.drop(r["sealed"])
    return ok


def _undo_calendar_row(cw, r: dict) -> bool:
    op = r.get("op") or "move"
    acct, cal = r.get("account") or None, r.get("calendar") or "primary"
    if op == "move":
        res = cw.update_event(r["id"], start=r["start"], end=r["end"], account_id=acct, calendar_id=cal)
        return not res.get("error")
    if op == "delete_created":
        now = cw.read_event(r["id"], calendar_id=cal, account_id=acct, for_write=True)
        if now.get("gone") or now.get("not_found"):
            return True                                   # already deleted by the owner: put back
        if not now.get("ok"):
            return False
        had = int(r.get("guests") or 0)
        if int((now.get("before") or {}).get("guests") or 0) > had:
            # guests were added since Friday made it: deleting it would cancel on them
            return False
        # guests Friday invited with it are told it is cancelled, as the card says
        res = cw.delete_event(r["id"], calendar_id=cal, account_id=acct, notify_guests=bool(had))
        return bool(res.get("ok"))
    if op == "restore_fields":
        f = _fields(r)
        if f is None:
            return False                  # gone, expired or cannot be opened: nothing to put back
        res = cw.update_event(r["id"], title=f.get("summary"), location=f.get("location"),
                              description=f.get("description"), start=f.get("start") or None,
                              end=f.get("end") or None, allow_clearing=True,
                              account_id=acct, calendar_id=cal)
        return bool(res.get("ok"))
    if op == "recreate":
        f = _fields(r)
        if f is None:
            return False
        res = cw.create_event(title=f.get("summary") or r.get("title") or "(no title)",
                              start=f.get("start") or "", end=f.get("end") or "",
                              location=f.get("location") or "", description=f.get("description") or "",
                              account_id=acct, calendar_id=cal)
        return bool(res.get("ok"))
    return False


# ── Google Tasks ────────────────────────────────────────────────────────────

@_safe
def task_created(res: dict, *, tasklist_id: str, conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("id") and not res.get("error")):
        return None
    tok = seal_title(res.get("title"))
    return _record("create_task", "create", "tasks",
                   items=[dict({"id": res["id"]}, **_ts(tok))],
                   summary="Added a task.", said="Added a task.",
                   undo_rows=[dict({"op": "delete_created", "id": res["id"],
                                    "account": res.get("account_id") or "", "tasklist": tasklist_id},
                                   **_ts(tok))],
                   conversation_id=conversation_id)


@_safe
def task_changed(res: dict, before: Optional[dict], *, tool: str, changed: List[str], tasklist_id: str,
                 conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("id") and not res.get("error")):
        return None
    tok = seal_title((before or {}).get("title") or res.get("title"))
    fields = {k: (before or {}).get(k, "") for k in changed} if before else {}
    if "status" in fields and fields["status"] not in ("needsAction", "completed"):
        fields["status"] = "needsAction"
    seal = _sealed(fields) if fields else None
    rows = ([dict({"op": "restore", "id": res["id"],
                   "account": res.get("account_id") or "", "tasklist": tasklist_id}, **seal, **_ts(tok))]
            if seal else [])
    what = "Marked a task done" if tool == "complete_task" else "Changed the %s of a task" % changed_words(changed)
    return _record(tool, "complete" if tool == "complete_task" else "update", "tasks",
                   items=[dict({"id": res["id"], "changed": changed}, **_ts(tok))],
                   summary=what + ("." if rows else (NOT_SEALED + ".") if fields
                                   else " (it cannot be undone: the task could not be read first)."),
                   said="Changed a task.", undo_rows=rows, conversation_id=conversation_id)


@_safe
def task_deleted(res: dict, before: Optional[dict], *, tasklist_id: str,
                 conversation_id: Optional[str] = None) -> Optional[dict]:
    if not (isinstance(res, dict) and res.get("deleted")) or res.get("already_gone"):
        return None
    tok = seal_title((before or {}).get("title"))
    seal = _sealed({k: (before or {}).get(k, "") for k in TASK_FIELDS}) if before else None
    rows = ([dict({"op": "recreate", "id": res.get("id"),
                   "account": res.get("account_id") or "", "tasklist": tasklist_id}, **seal, **_ts(tok))]
            if seal else [])
    return _record("delete_task", "delete", "tasks", items=[dict({"id": res.get("id")}, **_ts(tok))],
                   summary="Deleted a task%s." % (
                       "" if rows else NOT_SEALED if before
                       else " (it cannot be undone: the task could not be read first)"),
                   said="Deleted a task.", undo_rows=rows, conversation_id=conversation_id)


def undo_task_row(ga, r: dict) -> bool:
    """Put back one Google Tasks change. True when Google confirmed it; its sealed data then goes."""
    ok = _undo_task_row(ga, r)
    if ok and r.get("sealed"):
        from agent_friday.services import undo_store
        undo_store.drop(r["sealed"])
    return ok


def _undo_task_row(ga, r: dict) -> bool:
    op, acct, tl = r.get("op"), r.get("account") or "", r.get("tasklist") or "@default"
    f = {} if op == "delete_created" else _fields(r)
    if f is None:
        return False                      # gone, expired or cannot be opened: nothing to put back
    if op == "delete_created":
        res = ga.delete_task(account_id=acct, tasklist_id=tl, task_id=r["id"])
        return bool(res.get("deleted") or res.get("not_found"))
    if op == "restore":
        res = ga.update_task(account_id=acct, tasklist_id=tl, task_id=r["id"],
                             title=f.get("title") if "title" in f else None,
                             notes=f.get("notes") if "notes" in f else None,
                             due=(f.get("due") or None) if "due" in f else None,
                             clear_due="due" in f and not f.get("due"),
                             status=f.get("status") if "status" in f else None)
        return bool(res.get("id")) and not res.get("error")
    if op == "recreate":
        res = ga.create_task(account_id=acct, title=f.get("title") or r.get("title") or "(untitled)",
                             tasklist_id=tl, notes=f.get("notes") or "", due=f.get("due") or "")
        if res.get("error") or not res.get("id"):
            return False
        if f.get("status") == "completed":
            # The task exists again whatever happens next: never made a second time by a later Undo.
            done = ga.complete_task(account_id=acct, tasklist_id=tl, task_id=res["id"])
            if done.get("error"):
                _log.info("task made again but not marked done: %s", done.get("error"))
        return True
    return False


def undo_lines(receipt: dict, domain: str, titled: bool = True) -> List[str]:
    """What Undo will do, one line per change, for its card: a delete is named as a delete.
    `titled=False` for text that is kept (the card's action description): no titles."""
    out = []
    for r in undo_rows(receipt, domain):
        # opened for the card on screen; never kept readable in the receipt
        fallback = "the event" if domain == "calendar" else "the task"
        t = title_of(r, fallback) if titled else fallback
        op = r.get("op") or "move"
        if op == "delete_created":
            g = int(r.get("guests") or 0)
            out.append("Delete \u201c%s\u201d, which Friday created%s." % (
                t, "" if domain != "calendar" else
                ("; its %d guest(s) are told it is cancelled (not if more were added since)" % g) if g
                else " (not if guests were added since)"))
        elif op == "recreate":
            g = int(r.get("guests") or 0)
            out.append("Make \u201c%s\u201d again%s." % (t, (" without its %d guest(s)" % g) if g else ""))
        elif op in ("restore_fields", "restore"):
            out.append("Set %s of \u201c%s\u201d back as it was." % (
                ", ".join(r.get("field_names") or sorted(r.get("fields") or {})), t))
        else:
            out.append("Move \u201c%s\u201d back to %s." % (t, r.get("start") or "its old time"))
    return out


def why_not(receipt: dict, domain: str, done) -> str:
    """Why a row could not be put back, when the reason is Undo's own: what it was is no longer
    kept, or cannot be opened (the vault is locked)."""
    missing = [r for i, r in enumerate(undo_rows(receipt, domain))
               if i not in done and r.get("sealed") and _fields(r) is None]
    if not missing:
        return ""
    return (" What %d of them were is no longer kept or can't be opened right now (if your vault is "
            "locked, unlock it and try again)." % len(missing))


def undo_rows(receipt: dict, domain: str) -> List[dict]:
    return list(((receipt or {}).get("undo") or {}).get(domain) or [])
