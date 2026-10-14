"""Attach a meeting's summary to its calendar event, behind the owner's card.

Writing to a calendar is an outward action, and the summary is meeting content,
so this is never done on a click alone and never by the notes run. The path is
the gated one: ``annotate_calendar_events`` is an outward tool, and this module
puts exactly its action to the governance checkpoint (``authorize_external``),
which raises an approval card that says what will be added and to which event.
Nothing is written until the owner approves that card; approving runs it once.

What is offered is the summary only (decisions and action items, from the notes
already written), never the transcript. The event is found by its id, not only
by its title, so the other events that share a name are not touched.
"""
from __future__ import annotations

import logging

log = logging.getLogger("friday.meetings.calendar")

ACTION = "annotate_calendar_events"
HANDLER = "meeting_calendar"
APPROVAL_KIND = "governed_action"


def _detail(mid: str, event_id: str, query: str, note: str) -> dict:
    return {"handler": HANDLER, "meeting_id": mid, "event_id": event_id, "query": query, "note": note}


def _write(**kw):
    from agent_friday.services import calendar_write
    return calendar_write.annotate_events(**kw)


def _run(detail: dict) -> dict:
    return _write(query=detail["query"], note=detail["note"], event_id=detail["event_id"], apply_to_series=False)


def request(meeting: dict, *, requested_by: str = "meetings") -> dict:
    """Raise the card for attaching this meeting's summary to its calendar event.

    {"status": "pending_approval", "approval_id"} normally; "nothing" when there is no summary or the
    event already has it; "unlinked" when the meeting was not started from a calendar event;
    "error" with a plain reason when the calendar cannot be reached."""
    from agent_friday.services import meeting_workspace as mw
    note = mw.calendar_note(meeting)
    if not note:
        return {"status": "nothing", "message": "There is no summary to add yet. Write the notes first."}
    event_id = str(meeting.get("event_id") or "")
    query = str(meeting.get("title") or "").strip()
    if not event_id or not query:
        return {"status": "unlinked",
                "message": "This meeting was not started from a calendar event, so there is no event to add it to."}
    try:
        pre = _write(query=query, note=note, event_id=event_id, apply_to_series=False, dry_run=True)
    except Exception as e:  # noqa: BLE001 - reported in plain words
        log.info("calendar preview failed: %s", e)
        return {"status": "error", "message": "I couldn't reach your calendar just now."}
    if pre.get("error"):
        return {"status": "error", "message": str(pre["error"])[:240]}
    if not pre.get("changed"):
        return {"status": "nothing", "message": "That event already has this summary."}
    detail = _detail(str(meeting.get("id") or ""), event_id, query, note)
    ev = pre["changed"][0]
    from agent_friday.governance import action_gate
    v = action_gate.authorize_external(
        ACTION, detail, requested_by=requested_by,
        title="Add the summary of \"%s\" to your calendar event" % query[:80],
        description=("This adds the text below to the event's description in your calendar, which is kept by your "
                     "calendar account, not only on this PC. Nothing is added unless you approve.\n\n" + note)[:1900],
        action_description="Add a meeting summary note to one calendar event")
    if v.action == "allow":
        res = _run(detail)
        return {"status": "done" if res.get("ok") else "error", "message": res.get("error") or "Added to the event.",
                "event": ev.get("title")}
    if v.action == "deny":
        return {"status": "refused", "message": "Friday's governance check held this: %s" % v.reason}
    return {"status": "pending_approval", "approval_id": _card_id(detail), "event": ev.get("title"),
            "message": "Waiting for your approval. Nothing has been added to your calendar."}


def _card_id(detail: dict):
    try:
        from agent_friday.governance import action_gate
        from agent_friday.services import approvals as ap
        rec = ap.find_for_subject("external_action", action_gate.external_subject(ACTION, detail), APPROVAL_KIND)
        return (rec or {}).get("approval_id")
    except Exception:  # noqa: BLE001
        return None


def _notify(title: str, body: str, kind: str = "info") -> None:
    # Two registered emitters (services/notice_registry): MC1 the summary was
    # added, MC2 it was not (the owner has something to look at).
    try:
        import agent_friday.notifications_engine as ne
        if kind == "info":
            ne.push(title=title, body=body, priority="medium", kind="info", source="meetings",
                    emitter="MC1", dedupe_key="meeting-calendar:info")
        else:
            ne.push(title=title, body=body, priority="medium", kind="warning", source="meetings",
                    emitter="MC2", dedupe_key="meeting-calendar:warning")
    except Exception as e:  # noqa: BLE001
        log.warning("could not notify (%s): %s", title, e)


def _on_decision(record: dict) -> None:
    detail = record.get("payload") or {}
    if detail.get("handler") != HANDLER or (record.get("status") or "").lower() != "approved":
        return
    try:
        from agent_friday.governance import action_gate
        v = action_gate.authorize_external(ACTION, detail, requested_by="owner approval",
                                           approval_id=record.get("approval_id"))
        if v.action != "allow":
            _notify("Meetings: summary NOT added", "Friday's governance check held it: %s" % v.reason, "warning")
            return
        res = _run(detail)
        ok = bool(res.get("ok"))
        _notify("Meetings: summary %s" % ("added to your calendar event" if ok else "NOT added"),
                "Done." if ok else str(res.get("error") or "The calendar did not take it.")[:200],
                "info" if ok else "warning")
    except Exception as e:  # noqa: BLE001
        log.warning("approved calendar summary failed: %s", e)
        _notify("Meetings: summary NOT added", str(e)[:200], "warning")


_HOOKS_REGISTERED = False


def register_hooks() -> None:
    """Idempotent."""
    global _HOOKS_REGISTERED
    if _HOOKS_REGISTERED:
        return
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook(APPROVAL_KIND, _on_decision)
        _HOOKS_REGISTERED = True
    except Exception as e:  # noqa: BLE001
        log.warning("could not register the meeting calendar hook: %s", e)


register_hooks()
