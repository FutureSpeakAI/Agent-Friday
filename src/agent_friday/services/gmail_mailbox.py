"""Changes to the owner's own mailbox in Gmail itself: archive, read/unread,
star, important, labels, spam and Trash, one conversation at a time.

Every change records exactly what it did per message, so undo reverses that
change and nothing else: archiving a conversation that was already out of the
inbox records nothing to put back, your own sent replies never gain a label
they did not lose, a message that arrives afterwards is not touched, and a
conversation moved to Trash is taken back out with Gmail's own untrash. If the
per-message record could not be taken, the change still runs and undo says it
cannot be exact.

It needs gmail.modify on the account ("Reconnect with sending"); without it
nothing is attempted and the caller is told so.

Delete means Trash: Gmail keeps it for 30 days and it can be restored.
Permanent deletion is not offered here at all (threads.delete is never
called), and neither is emptying the Trash.
"""
from __future__ import annotations

import logging

from agent_friday.services import gmail_api
from agent_friday.user_errors import UserFacingError, UserFacingValueError, exception_text

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


class NotPermitted(UserFacingError, RuntimeError):
    """The account has not granted gmail.modify. Its message is written for the user."""


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


#: Gmail's batchModify takes at most this many message ids.
_BATCH = 1000

#: What undo says for a conversation whose exact per-message state was never recorded.
UNDO_INEXACT = "Friday could not record this conversation's exact state, so it can't undo exactly"


def _why(e: Exception) -> str:
    if isinstance(e, gmail_api.GmailError):
        return exception_text(e)
    return gmail_api.describe(e).get("message") or exception_text(e)


def _thread_messages(svc, thread_id: str) -> dict:
    """{message id: set of its labels} for every message in the conversation right now."""
    t = gmail_api.execute(svc.users().threads().get(userId="me", id=thread_id, format="minimal"))
    out = {}
    for m in t.get("messages") or []:
        if not m.get("id"):
            raise ValueError("a message came back without an id")
        out[m["id"]] = set(m.get("labelIds") or [])
    return out


def _apply_groups(svc, groups: dict) -> dict:
    """Run {(add, remove): [message ids]} as Gmail batchModify calls. Returns {message id: why}
    for the messages that could not be changed: a refused group is retried one message at a
    time, so one vanished message never blocks the others."""
    errors = {}
    for (add, remove), mids in groups.items():
        body = {"addLabelIds": list(add), "removeLabelIds": list(remove)}
        for i in range(0, len(mids), _BATCH):
            chunk = mids[i:i + _BATCH]
            try:
                gmail_api.execute(svc.users().messages().batchModify(
                    userId="me", body=dict(body, ids=chunk)))
                continue
            except Exception as e:
                if len(chunk) == 1:
                    errors[chunk[0]] = _why(e)
                    continue
            for mid in chunk:
                try:
                    gmail_api.execute(svc.users().messages().modify(userId="me", id=mid, body=dict(body)))
                except Exception as e:
                    errors[mid] = _why(e)
    return errors


def modify_threads(account_id: str, thread_ids, add=(), remove=(), _allow=()) -> dict:
    """Apply label changes to conversations, message by message. Returns
    {"changed": {thread_id: {"added": [...], "removed": [...], "messages": {message_id:
    {"added": [...], "removed": [...]}}}}, "failed": {thread_id: error}}.
    `messages` holds only the labels each message really gained or lost, which is what undo
    reverses; a message that arrives later is not in it. If a conversation's state could not
    be read first, the change still runs and the entry says `inexact`, so undo says so too."""
    add, remove = [l for l in add if l], [l for l in remove if l]
    touched = set(add) | set(remove)
    if _FORBIDDEN & touched:
        raise UserFacingValueError("Trash and Gmail's own labels are not changed as labels")
    if (_SPAM_ONLY & touched) - set(_allow):
        raise UserFacingValueError("spam is reported with the spam action, not as a label")
    svc = _svc(account_id)
    changed, failed = {}, {}
    plan, groups = {}, {}                  # tid -> {mid: (add, remove)}; (add, remove) -> [mid]
    for tid in dict.fromkeys(t for t in thread_ids if t):
        try:
            before = _thread_messages(svc, tid)
        except Exception as first:
            try:                           # no record to undo from: run it, and say so
                gmail_api.execute(svc.users().threads().modify(
                    userId="me", id=tid, body={"addLabelIds": add, "removeLabelIds": remove}))
                changed[tid] = {"added": [], "removed": [], "messages": {}, "inexact": _why(first)}
            except Exception as e:         # one bad conversation never stops the rest
                failed[tid] = _why(e)
            continue
        plan[tid] = {}
        for mid, labels in before.items():
            m_add = tuple(l for l in add if l not in labels)
            m_rem = tuple(l for l in remove if l in labels)
            if m_add or m_rem:
                plan[tid][mid] = (m_add, m_rem)
                groups.setdefault((m_add, m_rem), []).append(mid)
    errors = _apply_groups(svc, groups) if groups else {}
    for tid, per_msg in plan.items():
        done = {mid: {"added": list(a), "removed": list(r)}
                for mid, (a, r) in per_msg.items() if mid not in errors}
        bad = [errors[mid] for mid in per_msg if mid in errors]
        if bad:
            failed[tid] = bad[0]
        if done or not bad:
            changed[tid] = {"added": sorted({l for d in done.values() for l in d["added"]}),
                            "removed": sorted({l for d in done.values() for l in d["removed"]}),
                            "messages": done}
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
            failed[tid] = exception_text(e)
        except Exception as e:                   # one bad conversation never stops the rest
            failed[tid] = gmail_api.describe(e).get("message") or exception_text(e)
    return {"changed": changed, "failed": failed}


def apply_action(account_id: str, thread_ids, action: str) -> dict:
    if action in TRASH_ACTIONS:
        return trash_threads(account_id, thread_ids, restore=(action == "untrash"))
    if action not in ACTIONS:
        raise UserFacingValueError("unknown mailbox action %r" % action)
    add, remove = ACTIONS[action]
    return modify_threads(account_id, thread_ids, add, remove,
                          _allow=_SPAM_ONLY if action in ("spam", "notspam") else ())


def undo(account_id: str, changed: dict) -> dict:
    """Reverse exactly what modify_threads or trash_threads reported: each message gets back
    only the labels it gained or lost, and a conversation that was not recorded per message is
    reported as failed rather than guessed at."""
    svc = _svc(account_id)
    failed, groups, owner = {}, {}, {}
    for tid, ch in (changed or {}).items():
        if ch.get("trashed") or ch.get("untrashed"):
            try:
                call = svc.users().threads().untrash if ch.get("trashed") else svc.users().threads().trash
                gmail_api.execute(call(userId="me", id=tid))
            except Exception as e:
                failed[tid] = exception_text(e)
            continue
        if ch.get("inexact") or ("messages" not in ch and (ch.get("added") or ch.get("removed"))):
            failed[tid] = UNDO_INEXACT
            continue
        per_msg = {}
        for mid, d in (ch.get("messages") or {}).items():
            back_add, back_rem = list((d or {}).get("removed") or []), list((d or {}).get("added") or [])
            if _FORBIDDEN & (set(back_add) | set(back_rem)):
                failed[tid] = "refused"
                break
            if back_add or back_rem:
                per_msg[mid] = (tuple(back_add), tuple(back_rem))
        if tid in failed:
            continue
        for mid, key in per_msg.items():
            groups.setdefault(key, []).append(mid)
            owner[mid] = tid
    for mid, why in (_apply_groups(svc, groups) if groups else {}).items():
        failed.setdefault(owner[mid], why)
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
        raise UserFacingValueError("a label needs a name")
    svc = _svc(account_id)
    lab = gmail_api.execute(svc.users().labels().create(userId="me", body={
        "name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}))
    return {"id": lab.get("id"), "name": lab.get("name") or name}
