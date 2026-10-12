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

#: What a batch that must be exactly undoable (a card Laya raised) says for a conversation it could not
#: read first. It is left alone: a change that cannot be put back exactly is not made.
NOT_EXACT_REFUSED = ("Friday could not read this conversation first, so it left it alone "
                     "(it could not have been undone exactly)")


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


def snapshot_messages(account_id: str, thread_ids) -> dict:
    """{thread id: [{"id", "from", "ts"}]} for these conversations right now, in order: each message's id,
    its From header and its arrival time (ms since the epoch, or None). Raises when any conversation
    cannot be read: a caller that binds a card to messages cannot guess."""
    svc = _svc(account_id)
    out = {}
    for tid in dict.fromkeys(t for t in thread_ids if t):
        t = gmail_api.execute(svc.users().threads().get(userId="me", id=tid, format="metadata",
                                                         metadataHeaders=["From"]))
        msgs = []
        for m in t.get("messages") or []:
            if not m.get("id"):
                raise ValueError("a message came back without an id")
            hdr = {h.get("name", "").lower(): h.get("value", "")
                   for h in (m.get("payload") or {}).get("headers") or []}
            try:
                ts = int(m.get("internalDate"))
            except (TypeError, ValueError):
                ts = None
            msgs.append({"id": m["id"], "from": hdr.get("from", ""), "ts": ts})
        out[tid] = msgs
    return out


def modify_threads(account_id: str, thread_ids, add=(), remove=(), _allow=(),
                   only=None, exact_only=False) -> dict:
    """Apply label changes to conversations, message by message. Returns
    {"changed": {thread_id: {"added": [...], "removed": [...], "messages": {message_id:
    {"added": [...], "removed": [...]}}}}, "failed": {thread_id: error}}.
    `messages` holds only the labels each message really gained or lost, which is what undo
    reverses; a message that arrives later is not in it. If a conversation's state could not
    be read first, the change still runs and the entry says `inexact`, so undo says so too.

    `only` ({thread id: message ids}) restricts the change to the messages a card listed: a reply
    that arrived since is not touched, and a thread with no entry is left alone. `exact_only`
    refuses a conversation whose state could not be read first instead of running it inexactly."""
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
        if only is not None and tid not in only:
            failed[tid] = "this conversation was not in the batch that was approved"
            continue
        try:
            before = _thread_messages(svc, tid)
            if only is not None:
                want = set(only[tid])
                before = {mid: labels for mid, labels in before.items() if mid in want}
        except Exception as first:
            if exact_only or only is not None:      # never run a change that undo could not reverse exactly
                failed[tid] = NOT_EXACT_REFUSED
                continue
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


def apply_action(account_id: str, thread_ids, action: str, only=None, exact_only=False) -> dict:
    if action in TRASH_ACTIONS:
        if only is not None or exact_only:
            raise UserFacingValueError("trash is not a per-message change")
        return trash_threads(account_id, thread_ids, restore=(action == "untrash"))
    if action not in ACTIONS:
        raise UserFacingValueError("unknown mailbox action %r" % action)
    add, remove = ACTIONS[action]
    return modify_threads(account_id, thread_ids, add, remove,
                          _allow=_SPAM_ONLY if action in ("spam", "notspam") else (),
                          only=only, exact_only=exact_only)


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


def delete_label_if_empty(account_id: str, label_id: str) -> bool:
    """Delete a USER label that one of our changes created, but only when nothing is in it. True when it was
    deleted. Never touches a system label, never deletes mail."""
    svc = _svc(account_id)
    lab = gmail_api.execute(svc.users().labels().get(userId="me", id=label_id))
    if lab.get("type") != "user" or int(lab.get("messagesTotal") or 0) or int(lab.get("threadsTotal") or 0):
        return False
    gmail_api.execute(svc.users().labels().delete(userId="me", id=label_id))
    return True


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
