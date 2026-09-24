"""Changes to the owner's own mailbox in Gmail itself: archive, read/unread,
star, important, labels, spam and Trash, one conversation at a time.

Every change records exactly what it did per conversation, so undo reverses
that change and nothing else: archiving a conversation that was already out of
the inbox records nothing to put back, and a conversation moved to Trash is
taken back out with Gmail's own untrash.

It needs gmail.modify on the account ("Reconnect with sending"); without it
nothing is attempted and the caller is told so.

Delete means Trash: Gmail keeps it for 30 days and it can be restored.
Permanent deletion is not offered here at all (threads.delete is never
called), and neither is emptying the Trash.
"""
from __future__ import annotations

import logging

from agent_friday.services import gmail_api

_log = logging.getLogger("friday.gmail_mailbox")

#: Friday's local actions, as Gmail label changes: (add, remove).
ACTIONS = {
    "archive": ((), ("INBOX",)),
    "unarchive": (("INBOX",), ()),
    "read": ((), ("UNREAD",)),
    "unread": (("UNREAD",), ()),
    "flag": (("STARRED",), ()),
    "unflag": ((), ("STARRED",)),
    "important": (("IMPORTANT",), ()),
    "unimportant": ((), ("IMPORTANT",)),
    # Report spam / not spam: Gmail's own buttons make exactly this label move.
    "spam": (("SPAM",), ("INBOX",)),
    "notspam": (("INBOX",), ("SPAM",)),
}

#: Trash is not a label change: Gmail's trash/untrash calls move whole
#: conversations and keep their other labels for the way back.
TRASH_ACTIONS = ("trash", "untrash")

# Labels no label change may touch: Trash goes through trash/untrash, and
# SENT/DRAFT/CHAT are Gmail's own bookkeeping.
_FORBIDDEN = {"TRASH", "SENT", "DRAFT", "CHAT"}
# SPAM moves only as the explicit spam / not-spam action (and its undo).
_SPAM_ONLY = {"SPAM"}


class NotPermitted(RuntimeError):
    """The account has not granted gmail.modify."""


def can_modify(account_id: str) -> bool:
    from agent_friday.services import google_accounts as G
    rec = G.get_account(account_id) if account_id else None
    return bool(rec) and G.GMAIL_MODIFY in (rec.get("scopes") or [])


def _svc(account_id: str):
    from agent_friday.services import google_accounts as G
    if not can_modify(account_id):
        raise NotPermitted("this account has not allowed Friday to change its mailbox")
    creds = G.credentials_for(account_id)
    if not creds:
        raise NotPermitted("this account's credentials could not be read")
    from agent_friday.services.gmail_read import _service
    return _service(creds)


def _thread_labels(svc, thread_id: str) -> set:
    t = gmail_api.execute(svc.users().threads().get(userId="me", id=thread_id, format="minimal"))
    out = set()
    for m in t.get("messages") or []:
        out.update(m.get("labelIds") or [])
    return out


def modify_threads(account_id: str, thread_ids, add=(), remove=(), _allow=()) -> dict:
    """Apply label changes to whole conversations. Returns
    {"changed": {thread_id: {"added": [...], "removed": [...]}}, "failed": {thread_id: error}}.
    `changed` holds only what was actually different before, which is what undo reverses."""
    add, remove = [l for l in add if l], [l for l in remove if l]
    touched = set(add) | set(remove)
    if _FORBIDDEN & touched:
        raise ValueError("Trash and Gmail's own labels are not changed as labels")
    if (_SPAM_ONLY & touched) - set(_allow):
        raise ValueError("spam is reported with the spam action, not as a label")
    svc = _svc(account_id)
    changed, failed = {}, {}
    for tid in dict.fromkeys(t for t in thread_ids if t):
        try:
            before = _thread_labels(svc, tid)
            # UNREAD is per message: removing it from a conversation clears it
            # everywhere; adding it marks the conversation unread.
            to_add = [l for l in add if l not in before or l == "UNREAD"]
            to_remove = [l for l in remove if l in before]
            if to_add or to_remove:
                gmail_api.execute(svc.users().threads().modify(
                    userId="me", id=tid, body={"addLabelIds": to_add, "removeLabelIds": to_remove}))
            changed[tid] = {"added": [l for l in to_add if l not in before], "removed": to_remove}
        except gmail_api.GmailError as e:
            failed[tid] = str(e)
        except Exception as e:                   # one bad conversation never stops the rest
            failed[tid] = gmail_api.describe(e).get("message") or str(e)
    return {"changed": changed, "failed": failed}


def trash_threads(account_id: str, thread_ids, restore: bool = False) -> dict:
    """Move whole conversations to Gmail's Trash (restore=False) or back out
    of it. Gmail keeps trashed mail for 30 days. Same result shape as
    modify_threads; each changed conversation records {"trashed": True} or
    {"untrashed": True} so undo can do the opposite."""
    svc = _svc(account_id)
    changed, failed = {}, {}
    for tid in dict.fromkeys(t for t in thread_ids if t):
        try:
            call = svc.users().threads().untrash if restore else svc.users().threads().trash
            gmail_api.execute(call(userId="me", id=tid))
            changed[tid] = {"untrashed": True} if restore else {"trashed": True}
        except gmail_api.GmailError as e:
            failed[tid] = str(e)
        except Exception as e:                   # one bad conversation never stops the rest
            failed[tid] = gmail_api.describe(e).get("message") or str(e)
    return {"changed": changed, "failed": failed}


def apply_action(account_id: str, thread_ids, action: str) -> dict:
    if action in TRASH_ACTIONS:
        return trash_threads(account_id, thread_ids, restore=(action == "untrash"))
    if action not in ACTIONS:
        raise ValueError("unknown mailbox action %r" % action)
    add, remove = ACTIONS[action]
    return modify_threads(account_id, thread_ids, add, remove,
                          _allow=_SPAM_ONLY if action in ("spam", "notspam") else ())


def undo(account_id: str, changed: dict) -> dict:
    """Reverse exactly what modify_threads or trash_threads reported."""
    svc = _svc(account_id)
    failed = {}
    for tid, ch in (changed or {}).items():
        if ch.get("trashed") or ch.get("untrashed"):
            try:
                call = svc.users().threads().untrash if ch.get("trashed") else svc.users().threads().trash
                gmail_api.execute(call(userId="me", id=tid))
            except Exception as e:
                failed[tid] = str(e)
            continue
        add, remove = list(ch.get("removed") or []), list(ch.get("added") or [])
        if _FORBIDDEN & (set(add) | set(remove)):
            failed[tid] = "refused"
            continue
        if not (add or remove):
            continue
        try:
            gmail_api.execute(svc.users().threads().modify(
                userId="me", id=tid, body={"addLabelIds": add, "removeLabelIds": remove}))
        except Exception as e:
            failed[tid] = str(e)
    return {"failed": failed}


def _read_svc(account_id: str):
    """Reading needs only gmail.readonly: browsing by label works on an
    account that has not allowed changes."""
    from agent_friday.services import google_accounts as G
    creds = G.credentials_for(account_id)
    if not creds:
        raise NotPermitted("this account's credentials could not be read")
    from agent_friday.services.gmail_read import _service
    return _service(creds)


def list_labels(account_id: str) -> list:
    """The account's own labels (not Gmail's system ones), by name."""
    svc = _read_svc(account_id)
    d = gmail_api.execute(svc.users().labels().list(userId="me"))
    return sorted(({"id": l["id"], "name": l.get("name") or l["id"]}
                   for l in d.get("labels") or [] if l.get("type") == "user"),
                  key=lambda l: l["name"].lower())


def create_label(account_id: str, name: str) -> dict:
    name = (name or "").strip()[:225]
    if not name:
        raise ValueError("a label needs a name")
    svc = _svc(account_id)
    lab = gmail_api.execute(svc.users().labels().create(userId="me", body={
        "name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}))
    return {"id": lab.get("id"), "name": lab.get("name") or name}
