"""Mailbox changes Friday proposes on its own initiative.

The owner's own click in the Messages workspace acts at once, with undo: it is
the owner acting through Friday's page, which the governance checkpoint leaves
alone on purpose (docs/decisions/2026-09-24-injection-provenance-gate.md, "the
owner's own REST actions").

When Friday itself decides to delete, archive, mute, report spam or
unsubscribe, that is a different thing, and it goes through the checkpoint
like any other outward action (governance/action_gate.authorize_external): the
cLaws are verified, an approval card says in words what will happen, and a
signed receipt records the decision. The change runs only when the owner has
approved that exact card, and running it passes the checkpoint again, which
uses the card up, so one approval buys exactly one change. The approved change
runs the same code the owner's click runs (routes.messages.run_action, or
mail_unsubscribe.unsubscribe), so an approval can do nothing a click could not.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("friday.mail_proposals")

#: The checkpoint's own card kind for actions that are not tool calls.
APPROVAL_KIND = "governed_action"
HANDLER = "mail_proposal"

VERBS = {
    "trash": "move to Trash",
    "spam": "report as spam",
    "archive": "archive",
    "mute": "mute",
    "unsubscribe": "unsubscribe from",
}

#: The card's title, with {n} as "3 conversations".
TITLES = {
    "trash": "move {n} to Trash",
    "spam": "report {n} as spam",
    "archive": "archive {n}",
    "mute": "mute {n}",
}

#: The card's first line, before the list of messages.
BODIES = {
    "trash": "move these to Trash",
    "spam": "report these as spam",
    "archive": "archive these",
    "mute": "mute these",
    "unsubscribe": "unsubscribe you from",
}


def _clean_items(items):
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        out.append({k: str(it.get(k) or "")[:300] for k in ("id", "account_id", "thread_id", "subject", "sender")})
    return out[:200]


def _action_name(action: str) -> str:
    return "mail: " + action


def _detail(action, ids, items, extra) -> dict:
    return {"handler": HANDLER, "action": action,
            "ids": sorted({str(i) for i in ids or [] if str(i)}),
            "gmail": _clean_items(items), "extra": extra or {}}


def _words(detail: dict, reason: str = "") -> tuple:
    action, ids, items = detail["action"], detail["ids"], detail["gmail"]
    n = len(ids) or 1
    if action == "unsubscribe":
        title = "Friday wants to unsubscribe you from %s" % (detail["extra"].get("sender") or "a mailing list")
    else:
        title = "Friday wants to " + TITLES[action].format(
            n="%d conversation%s" % (n, "" if n == 1 else "s"))
    listing = "\n".join("- %s%s" % (it.get("subject") or it.get("id") or "(message)",
                                     (" — " + it["sender"]) if it.get("sender") else "")
                        for it in items[:25])
    body = "Friday proposes to %s:\n%s%s" % (
        BODIES[action], listing or "(the messages it named)",
        ("\n\nWhy: " + reason.strip()) if reason and reason.strip() else "")
    return title, body


def _run(detail: dict) -> dict:
    """The change itself: the same code the owner's click runs."""
    action = detail["action"]
    if action == "unsubscribe":
        from agent_friday.services import mail_unsubscribe as mu
        ex = detail.get("extra") or {}
        return mu.unsubscribe(ex.get("account_id"), ex.get("message_id"))
    from agent_friday.routes.messages import run_action
    return run_action(action, detail.get("ids") or [], detail.get("gmail") or [], {})


def _succeeded(detail: dict, res: dict) -> bool:
    if detail["action"] == "unsubscribe":
        return res.get("status") in ("done", "next")
    return res.get("status") == "ok"


def propose(action: str, ids, gmail_items, *, requested_by: str, reason: str = "",
            extra: dict | None = None) -> dict:
    """Put a mailbox change Friday wants to make to the governance checkpoint.

    Normally that raises an approval card and nothing happens yet. If the
    owner already approved this exact change and the card is unused, it runs
    now (and uses the card); if the checkpoint holds it, it does not run.
    """
    from agent_friday.governance import action_gate
    from agent_friday.services import approvals as ap
    if action not in VERBS:
        raise ValueError("Friday cannot propose %r" % action)
    detail = _detail(action, ids, gmail_items, extra)
    title, body = _words(detail, reason)
    v = action_gate.authorize_external(
        _action_name(action), detail, requested_by=requested_by or "friday",
        title=title, action_description=body,
        description="Friday asked for this on its own. Nothing happens unless you approve it.")
    if v.action == "allow":
        res = _run(detail)
        return {"status": "done" if _succeeded(detail, res) else "error", "result": res,
                "message": "You had already approved exactly this, so it was done."}
    if v.action == "deny":
        return {"status": "refused", "message": "Friday's governance check held this: %s" % v.reason}
    card = None
    try:
        import hashlib
        import json
        fp = hashlib.sha256(json.dumps({"a": _action_name(action), "d": detail}, sort_keys=True,
                                       default=str).encode()).hexdigest()[:16]
        prefix = "%s:%s" % (_action_name(action), fp)
        # the newest pending card for exactly this change (a card raised again
        # after an earlier one was used carries a suffix on its subject)
        mine = [r for r in ap.list_approvals(status="pending")
                if r.get("kind") == APPROVAL_KIND and str(r.get("subject_id") or "").startswith(prefix)]
        mine.sort(key=lambda r: r.get("created_at") or 0)
        card = mine[-1] if mine else None
    except Exception:
        card = None
    return {"status": "pending_approval",
            "approval_id": (card or {}).get("approval_id"),
            "approval_status": (card or {}).get("status"),
            "message": "Friday asked first: this is waiting for your approval."}


def _notify(title: str, body: str, kind: str = "info") -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body, priority="medium", kind=kind, source="mail")
    except Exception as e:
        _log.warning("could not notify (%s): %s", title, e)


def _on_decision(record: dict) -> None:
    detail = record.get("payload") or {}
    if detail.get("handler") != HANDLER:
        return                      # another governed action (e.g. a compute job)
    if (record.get("status") or "").lower() != "approved":
        return
    action = detail.get("action")
    try:
        from agent_friday.governance import action_gate
        v = action_gate.authorize_external(_action_name(action), detail,
                                           requested_by="owner approval",
                                           approval_id=record.get("approval_id"))
        if v.action != "allow":
            _notify("Mail: approved change NOT made",
                    "Friday's governance check held it: %s" % v.reason, "warning")
            return
        res = _run(detail)
        ok = _succeeded(detail, res)
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
