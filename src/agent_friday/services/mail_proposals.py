"""Mailbox changes Friday proposes on its own initiative.

The owner's own click in the Messages workspace acts at once, with undo.
When Friday itself decides to delete, archive, mute, report spam or
unsubscribe, that is a different thing: it becomes an approval card, and the
change happens only when the owner approves it. The approved change runs the
same code the owner's click runs (routes.messages.run_action, or
mail_unsubscribe.unsubscribe), so an approval can do nothing a click could not.
"""
from __future__ import annotations

import hashlib
import json
import logging

_log = logging.getLogger("friday.mail_proposals")

APPROVAL_KIND = "outward"
SUBJECT_TYPE = "mail_action"
HANDLER = "mail_proposal"

VERBS = {
    "trash": "move to Trash",
    "spam": "report as spam",
    "archive": "archive",
    "mute": "mute",
    "unsubscribe": "unsubscribe from",
}


def _clean_items(items):
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({k: str(it.get(k) or "")[:300] for k in ("id", "account_id", "thread_id", "subject", "sender")})
    return out[:200]


def propose(action: str, ids, gmail_items, *, requested_by: str, reason: str = "",
            extra: dict | None = None) -> dict:
    """File an approval card for a mailbox change Friday wants to make."""
    from agent_friday.services import approvals as ap
    if action not in VERBS:
        raise ValueError("Friday cannot propose %r" % action)
    ids = sorted({str(i) for i in ids or [] if str(i)})
    items = _clean_items(gmail_items)
    key = json.dumps([action, ids, extra or {}], sort_keys=True)
    sid = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    n = len(ids) or 1
    listing = "\n".join("- %s%s" % (it.get("subject") or it.get("id") or "(message)",
                                     (" — " + it["sender"]) if it.get("sender") else "")
                        for it in items[:25])
    what = VERBS[action]
    if action == "unsubscribe":
        title = "Friday wants to unsubscribe you from %s" % ((extra or {}).get("sender") or "a mailing list")
    else:
        title = "Friday wants to %s %d conversation%s" % (what, n, "" if n == 1 else "s")
    appr = ap.create_approval(
        kind=APPROVAL_KIND,
        subject_type=SUBJECT_TYPE,
        subject_id=sid,
        title=title,
        action_description=("Friday proposes to %s:\n%s%s" % (
            what, listing or "(the messages it named)",
            ("\n\nWhy: " + reason.strip()) if reason and reason.strip() else "")),
        description="Friday asked for this on its own. Nothing happens unless you approve it.",
        force_gate=True,
        payload={"handler": HANDLER, "action": action, "ids": ids, "gmail": items,
                 "extra": extra or {}},
        requested_by=requested_by or "friday",
    )
    return {"status": "pending_approval", "approval_id": appr.get("approval_id"),
            "approval_status": appr.get("status"),
            "message": "Friday asked first: this is waiting for your approval."}


def _notify(title: str, body: str, kind: str = "info") -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body, priority="medium", kind=kind, source="mail")
    except Exception as e:
        _log.warning("could not notify (%s): %s", title, e)


def _on_decision(record: dict) -> None:
    payload = record.get("payload") or {}
    if payload.get("handler") != HANDLER:
        return                      # some other outward card
    if (record.get("status") or "").lower() != "approved":
        return
    action = payload.get("action")
    try:
        if action == "unsubscribe":
            from agent_friday.services import mail_unsubscribe as mu
            ex = payload.get("extra") or {}
            res = mu.unsubscribe(ex.get("account_id"), ex.get("message_id"))
            ok = res.get("status") in ("done", "next")
        else:
            from agent_friday.routes.messages import run_action
            res = run_action(action, payload.get("ids") or [], payload.get("gmail") or [], {})
            ok = res.get("status") == "ok"
        _notify("Mail: approved change %s" % ("made" if ok else "NOT made"),
                res.get("message") or ("Done: %s." % VERBS.get(action, action)),
                "info" if ok else "warning")
    except Exception as e:
        _log.warning("approved mail change failed: %s", e)
        _notify("Mail: approved change NOT made", str(e)[:200], "warning")


_HOOKS_REGISTERED = False


def register_hooks() -> None:
    """Idempotent; called at import."""
    global _HOOKS_REGISTERED
    if _HOOKS_REGISTERED:
        return
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook(APPROVAL_KIND, _on_decision)
        _HOOKS_REGISTERED = True
    except Exception as e:
        _log.warning("could not register the mail proposal hook: %s", e)


register_hooks()
