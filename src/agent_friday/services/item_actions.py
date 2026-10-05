"""Friday organizing the owner's things: mail, files and wiki pages.

The three kinds of item share one module so they keep the same rules:

* Every change writes a receipt (services/action_journal) that names each
  item, what happened to it, and what undo needs to put it back.
* Mail lives in Gmail, which reaches every device, so a change there is
  outward. Archive, trash, spam, restore and move wait for the owner's
  approval, on ONE card for the whole batch that lists what it will touch.
  The list is the change: approving it changes exactly the conversations found
  when the card was raised. A search is never run again later. Mark read or
  unread, star and label are the exception (EMAIL_NO_CARD): the owner ruled
  that changes which take nothing out of the inbox and undo at once run
  without a card, with a receipt. A batch made from the owner's ticked rows
  keeps those rows' refs, so the page can show them held, done or declined.
* Files and wiki pages are local and can be undone. One item changes at once.
  Two or more are a batch, and a batch waits for one card, like mail. A file
  change that reaches further than this PC waits for a card even when it is
  one file: moving into a folder a cloud client syncs, or touching the code
  projects.
* Nothing is deleted. Trash moves a file or page into Friday's own trash
  (action_journal.home_trash), and undo moves it back. Mail goes to Gmail's
  Trash, which keeps it for 30 days.
* Nothing is overwritten. A move or rename onto a name that is already taken
  is refused for that item, and the rest of the batch goes on.
* A card can be answered in words, typed or spoken (answer_card). The decision
  counts only if the owner's own words said yes or no, and only if they came
  after the card was raised: typed words by the chat gate's rule, spoken ones
  through the voice path's own (local_context.decide_by_voice).
* A result for the cloud voice model (QUIET) names counts and kinds, never a
  subject, sender, account, file or page Friday found
  (docs/reference/voice-tool-contract.md §5). The card lists them on screen.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from contextvars import ContextVar
from pathlib import Path, PurePosixPath

from agent_friday.services import action_journal as journal
from agent_friday.user_errors import UserFacingError, exception_text

_log = logging.getLogger("friday.item_actions")

#: The `handler` every organize card's payload carries; the decision hook acts
#: only on these.
HANDLER = "item_batch"
#: The checkpoint's own card kind for actions that are not a tool call.
APPROVAL_KIND = "governed_action"
#: A call naming this many items or more is a batch: one card for all of it.
BULK_FROM = 2
#: The most items one call may change. A larger request is asked to narrow.
MAX_ITEMS = 500
#: How many items a card, a result or a spoken read-back names.
PREVIEW = 25
#: How long answer_card waits for an approved batch to finish before it says
#: the batch is still running. Voice has a hard limit per tool call.
WAIT_VOICE_S = 12.0
WAIT_CHAT_S = 45.0
#: How long the numbered choices an ambiguous page name gave can be picked
#: from by number (#2).
CHOICE_TTL_S = 900.0

#: True while a call's result goes to the cloud voice model. Its text then
#: names counts and kinds, never a subject, sender, account, file or page
#: Friday found (docs/reference/voice-tool-contract.md §5). The tool handlers
#: set it (agent._organize_call).
QUIET: ContextVar = ContextVar("item_actions_quiet", default=False)


def _quiet() -> bool:
    return bool(QUIET.get())


class Refused(UserFacingError):
    """Nothing was changed, for a reason that can be said to the owner."""


# ── Shared ──────────────────────────────────────────────────────────────────

def _plural(n: int, one: str, many: str | None = None) -> str:
    return "%d %s" % (n, one if n == 1 else (many or one + "s"))


def words_hash(text: str) -> str:
    """A short hash of the owner's words, kept on a card so an answer can be
    told apart from the words that raised it (no text is kept)."""
    return hashlib.sha256(re.sub(r"\s+", " ", str(text or "")).strip().lower()
                          .encode("utf-8")).hexdigest()[:12]


def _notify(title: str, body: str, kind: str = "info", priority: str = "") -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body,
                priority=priority or ("low" if kind == "info" else "medium"),
                kind=kind, source="actions")
    except Exception as e:
        _log.info("could not notify (%s): %s", title, e)


def _tell_pages(domain: str, receipt_id: str) -> None:
    """News for the desktop: a list it shows may have changed."""
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.broadcast({"type": "items_changed", "domain": domain,
                               "receipt_id": receipt_id}, kind="desktop")
    except Exception:
        pass


def _post_back(conversation_id: str | None, text: str, approval_id: str | None) -> None:
    """Put an approved batch's outcome in the conversation that asked for it."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    try:
        from agent_friday.services import conversations as _convs
        _convs.append(cid, {"role": "friday", "text": text, "ts": time.time(),
                            "meta": {"kind": "approval_result", "approval_id": approval_id}})
    except Exception as e:
        _log.warning("could not post the outcome into %s: %s", cid, e)
        return
    try:
        from agent_friday.services import desktop_bus as _bus
        _bus.broadcast({"type": "approval_result", "conversation_id": cid,
                        "approval_id": approval_id}, kind="chat")
    except Exception:
        pass


def _fingerprint(name: str, detail: dict) -> str:
    """The checkpoint's own fingerprint of an action (governance/action_gate)."""
    return hashlib.sha256(json.dumps({"a": name, "d": detail}, sort_keys=True,
                                     default=str).encode()).hexdigest()[:16]


def _action_name(domain: str, action: str) -> str:
    return "organize %s: %s" % (domain, action)


def _find_card(name: str, detail: dict) -> dict | None:
    """The newest card raised for exactly this change, whatever its state."""
    from agent_friday.services import approvals as ap
    prefix = "%s:%s" % (name, _fingerprint(name, detail))
    mine = [r for r in ap.list_approvals(kind=APPROVAL_KIND)
            if str(r.get("subject_id") or "") == prefix
            or str(r.get("subject_id") or "").startswith(prefix + ":")]
    mine.sort(key=lambda r: r.get("created_at") or 0)
    return mine[-1] if mine else None


#: What a card's own text may hold of its list: the store keeps 2000
#: characters of description. The page lists every item from the payload.
DESCRIPTION_BUDGET = 1700


def _listed(lines: list[str], budget: int = DESCRIPTION_BUDGET) -> str:
    out, used = [], 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > budget:
            out.append("\u2026and %d more" % (len(lines) - i))
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _raise_card(domain: str, action: str, detail: dict, *, title: str, body: str,
                requested_by: str = "friday", replaces: str = "") -> dict:
    """Put a batch to the governance checkpoint. Normally that raises one card
    and nothing happens yet; the change runs when the owner approves it.

    `body` says what the action is in one short line (the harm check and the
    policy classifier read it); the items themselves ride in the payload as
    `lines`, which the card lists, and in the description for any surface
    that shows only text."""
    from agent_friday.governance import action_gate
    name = _action_name(domain, action)
    v = action_gate.authorize_external(
        name, detail, requested_by=requested_by or "friday", title=title[:200],
        description=("Nothing changes until you approve. Approving changes exactly "
                     "what this card lists, and the receipt keeps what undo needs.\n\n"
                     + _listed(detail.get("lines") or [])),
        action_description=body[:1000])
    if v.action == "deny":
        raise Refused("Friday's governance check held this: %s" % v.reason)
    card = _find_card(name, detail) or {}
    if v.action == "allow":
        # An approved, unused card for exactly this change already existed and
        # the check just used it: run it now.
        _start(detail, card.get("approval_id"))
        return {"status": "running", "approval_id": card.get("approval_id")}
    status = card.get("status") or "pending"
    out = {"status": "pending_approval" if status == "pending" else status,
           "approval_id": card.get("approval_id")}
    if replaces and status == "pending" and card.get("approval_id"):
        note = withdraw(replaces, card["approval_id"], detail.get("conversation_id"))
        if note:
            out["notes"] = [note]
    return out


def withdraw(card_id: str, by_card: str, conversation_id: str | None) -> str:
    """Withdraw one of Friday's pending organize cards that a changed card
    replaces, so only the owner's latest version can be approved. Nothing on
    the withdrawn card happens. Returns a note for the model ("" if there was
    nothing to withdraw)."""
    from agent_friday.services import approvals as ap
    cid = str(card_id or "").strip()
    if not cid or cid == by_card:
        return ""
    rec = ap.get_approval(cid)
    if not rec or (rec.get("payload") or {}).get("handler") != HANDLER:
        return "there was no organize card %s to withdraw" % cid
    asked_in = (rec.get("payload") or {}).get("conversation_id") or ""
    if conversation_id and asked_in and asked_in != conversation_id:
        return "card %s belongs to another conversation and was left alone" % cid
    if rec.get("status") != "pending":
        return "card %s was already %s" % (cid, rec.get("status"))
    _out, won = ap.decide_with_outcome(cid, "deny", decided_by="friday:withdrawn",
                                       note="replaced by %s, the changed request" % by_card)
    return ("the earlier card %s is withdrawn" % cid) if won else ""


# ── Mail ────────────────────────────────────────────────────────────────────

#: organize_email's actions: (gmail_mailbox's name for it, what the card asks
#: to do, what the receipt says was done).
EMAIL_ACTIONS = {
    "archive": ("archive", "archive", "archived"),
    "inbox": ("unarchive", "move back to the inbox", "moved back to the inbox"),
    "read": ("read", "mark as read", "marked read"),
    "unread": ("unread", "mark as unread", "marked unread"),
    "star": ("flag", "star", "starred"),
    "unstar": ("unflag", "unstar", "unstarred"),
    "label": ("label", "label", "labelled"),
    "unlabel": ("unlabel", "take the label off", "unlabelled"),
    "move": ("move", "move", "moved"),
    "trash": ("trash", "move to Trash", "moved to Trash"),
    "restore": ("untrash", "take out of Trash", "taken out of Trash"),
    "spam": ("spam", "report as spam", "reported as spam"),
    "not_spam": ("notspam", "mark as not spam", "marked not spam"),
}
_LABEL_ACTIONS = ("label", "unlabel", "move")
#: The owner's rule (2026-10): a change that is instantly undoable and takes nothing out of
#: the inbox runs at once, with a receipt and an Undo. Everything else about the mailbox
#: (archive, trash, spam, restore, move, back to the inbox) is one card per batch.
EMAIL_NO_CARD = ("read", "unread", "star", "unstar", "label", "unlabel")


def _mail_accounts(account: str = "") -> list[dict]:
    """The connected accounts with mail switched on, or the one `account` names
    (its id, label or address)."""
    from agent_friday.services import google_accounts as G
    recs = [r for r in G.list_accounts()
            if (r.get("services") or {}).get("gmail", True) and (r.get("mail") or {}).get("read")
            and (r.get("health") or {}).get("healthy", True)]
    want = str(account or "").strip().lower()
    if not want:
        return recs
    hit = [r for r in recs if want in (str(r.get("id") or "").lower(),
                                       str(r.get("label") or "").lower(),
                                       str(r.get("email") or "").lower())]
    if not hit:
        hit = [r for r in recs if want in str(r.get("email") or "").lower()
               or want in str(r.get("label") or "").lower()]
    if len(hit) > 1:
        if _quiet():
            raise Refused("%d accounts match %r; ask which one the user means" % (len(hit), account))
        raise Refused("more than one account matches %r: %s" % (
            account, ", ".join(r.get("label") or r.get("email") or r["id"] for r in hit)))
    return hit


def _account_name(rec: dict) -> str:
    return str(rec.get("label") or rec.get("email") or rec.get("id") or "an account")


def _search_threads(account_id: str, query: str, limit: int) -> tuple[list[str], bool]:
    """Conversation ids Gmail's own search matches, newest first, and whether
    there were more than `limit`."""
    from agent_friday.services import gmail_api
    from agent_friday.services import gmail_mailbox as gm
    svc = gm._read_svc(account_id)
    out, token = [], None
    while True:
        kw = {"userId": "me", "q": query, "maxResults": min(500, max(1, limit - len(out) + 1))}
        if token:
            kw["pageToken"] = token
        resp = gmail_api.execute(svc.users().threads().list(**kw))
        for t in resp.get("threads") or []:
            if t.get("id") and t["id"] not in out:
                out.append(t["id"])
        token = resp.get("nextPageToken")
        if len(out) > limit:
            return out[:limit], True
        if not token:
            return out, False


def _thread_heads(account_id: str, tids: list[str]) -> dict:
    """{thread id: {"subject", "sender", "date"}} for a few conversations."""
    from agent_friday.services import gmail_api
    from agent_friday.services import gmail_mailbox as gm
    if not tids:
        return {}
    svc = gm._read_svc(account_id)
    got, _failed = gmail_api.batch_get(svc, tids, kind="threads", fmt="metadata",
                                       headers=["From", "Subject", "Date"])
    out = {}
    for tid, t in got.items():
        msgs = t.get("messages") or []
        first = msgs[0] if msgs else {}
        h = {x.get("name", "").lower(): x.get("value", "")
             for x in (first.get("payload") or {}).get("headers") or []}
        out[tid] = {"subject": (h.get("subject") or "(no subject)")[:140],
                    "sender": re.sub(r"\s*<[^>]*>\s*$", "", h.get("from") or "")[:80] or "someone",
                    "date": (h.get("date") or "")[:40], "messages": len(msgs)}
    return out


def _split_ids(thread_ids, accounts: list[dict]) -> dict:
    """{account id: [thread ids]} from ids that may carry their account
    ("account:thread"). An id without one belongs to the only account, or to
    whichever account has it."""
    from agent_friday.services import gmail_api
    from agent_friday.services import gmail_mailbox as gm
    by_key = {}
    for r in accounts:
        for k in (r.get("id"), r.get("label"), r.get("email")):
            if k:
                by_key[str(k).lower()] = r["id"]
    out: dict[str, list[str]] = {}
    loose = []
    for raw in thread_ids or []:
        s = str(raw or "").strip()
        if not s:
            continue
        head, sep, tail = s.rpartition(":")
        if sep and head.lower() in by_key and tail:
            out.setdefault(by_key[head.lower()], []).append(tail)
        else:
            loose.append(s)
    if loose and len(accounts) == 1:
        out.setdefault(accounts[0]["id"], []).extend(loose)
    elif loose:
        for tid in loose[:50]:
            for r in accounts:
                try:
                    svc = gm._read_svc(r["id"])
                    gmail_api.execute(svc.users().threads().get(userId="me", id=tid, format="minimal"))
                    out.setdefault(r["id"], []).append(tid)
                    break
                except Exception:
                    continue
    return {a: list(dict.fromkeys(t)) for a, t in out.items()}


def select_email(query: str = "", thread_ids=None, account: str = "",
                 limit: int = MAX_ITEMS) -> dict:
    """The conversations a request names, per account, with the first few
    described. Accounts that cannot be changed are named, not included."""
    query = str(query or "").strip()
    if not query and not thread_ids:
        raise Refused("say which mail: a Gmail search (from:, older_than:, label:) or conversation ids")
    accts = _mail_accounts(account)
    if not accts:
        raise Refused("no Gmail account is connected" if not account
                      else "no connected Gmail account matches %r" % account)
    from agent_friday.services import gmail_mailbox as gm
    per: dict[str, list[str]] = {}
    blocked, errors, truncated = [], [], False
    if thread_ids:
        per = _split_ids(thread_ids, accts)
    else:
        room = limit
        for r in accts:
            if room <= 0:
                truncated = True
                break
            try:
                tids, more = _search_threads(r["id"], query, room)
            except Exception as e:
                errors.append("%s: %s" % (_account_name(r), exception_text(e)))
                continue
            truncated = truncated or more
            if tids:
                per[r["id"]] = tids
                room -= len(tids)
    names = {r["id"]: _account_name(r) for r in accts}
    for acct in list(per):
        if not gm.can_modify(acct):
            blocked.append({"account": names.get(acct, acct), "count": len(per.pop(acct))})
    total = sum(len(t) for t in per.values())
    preview = []
    for acct, tids in per.items():
        if len(preview) >= PREVIEW:
            break
        want = tids[:PREVIEW - len(preview)]
        try:
            heads = _thread_heads(acct, want)
        except Exception:
            heads = {}
        for tid in want:
            h = heads.get(tid) or {}
            preview.append({"id": tid, "account_id": acct, "account": names.get(acct, acct),
                            "subject": h.get("subject") or "(conversation %s)" % tid,
                            "sender": h.get("sender") or "", "date": h.get("date") or ""})
    return {"accounts": per, "count": total, "preview": preview, "blocked": blocked,
            "errors": errors, "truncated": truncated, "query": query}


def _senders(preview: list[dict], n: int = 3) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for p in preview:
        s = (p.get("sender") or "").strip()
        if s:
            counts[s] = counts.get(s, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _email_words(action: str, label: str, count: int) -> tuple[str, str]:
    """(what to do, as a card title fragment) for an email action."""
    verb = EMAIL_ACTIONS[action][1]
    what = _plural(count, "conversation")
    if action == "label":
        return verb, "label %s \u201c%s\u201d" % (what, label)
    if action == "unlabel":
        return verb, "take the label \u201c%s\u201d off %s" % (label, what)
    if action == "move":
        return verb, "move %s to \u201c%s\u201d" % (what, label)
    return verb, "%s %s" % (verb, what)


def email_readback(sel: dict, action: str, label: str = "", room_mode: bool = False) -> str:
    """One sentence Friday can say about the batch before the owner decides.
    With several people in the room it names no sender and no subject: the
    list is on the owner's screen, and the room hears only the shape of it."""
    n = sel["count"]
    _verb, doing = _email_words(action, label, n)
    if room_mode or _quiet():
        return "I found %s; the list is on your screen. Shall I %s?" % (_plural(n, "conversation"), doing)
    who = _senders(sel["preview"])
    lead = ""
    if who:
        lead = ", mostly from " + " and ".join(s for s, _c in who[:2])
    subj = [p["subject"] for p in sel["preview"][:2] if p.get("subject")]
    like = (" \u2014 like \u201c%s\u201d" % "\u201d and \u201c".join(subj)) if subj else ""
    return "I found %s%s%s. Shall I %s?" % (_plural(n, "conversation"), lead, like, doing)


def _tell_held(detail: dict, state: str, receipt_id: str = "", card_id: str = "") -> None:
    """Tell the page which rows a screen-bound batch holds, and how it ended: `held` (a card
    waits for the owner), `done` (it ran; the receipt feeds the Undo) or `declined` (the
    ticks stay). Only batches made from the owner's on-screen selection carry refs. Never
    raises, never waits: the change has already happened or been refused."""
    refs = list(detail.get("refs") or [])
    if not refs:
        return
    try:
        from agent_friday.services import desktop_bus, screen_stage
        desktop_bus.push([{"type": "held", "workspace": "messages", "card_id": card_id,
                           "refs": refs, "state": state, "action": detail.get("action") or "",
                           "receipt_id": receipt_id, "selection_id": detail.get("selection_id") or ""}])
        screen_stage.log_counts("held_" + state, "messages", len(refs))
    except Exception:
        pass


def propose_email(action: str, *, query: str = "", thread_ids=None, account: str = "",
                  label: str = "", why: str = "", conversation_id: str | None = None,
                  owner_words: str = "", requested_by: str = "friday", room_mode: bool = False,
                  replaces: str = "", refs=None, selection_id: str = "",
                  stage_rev: int = 0) -> dict:
    """Find the mail a request names and raise ONE card to change all of it.

    `refs` (with `selection_id` and `stage_rev`) bind the batch to what the owner saw ticked:
    `thread_ids` are exactly those conversations, nothing is searched, and the card keeps the
    refs so the page can hold those rows until the owner answers. A mark-read, star or label
    change (EMAIL_NO_CARD) runs at once and returns its receipt."""
    action = str(action or "").strip().lower().replace(" ", "_").replace("-", "_")
    if action not in EMAIL_ACTIONS:
        raise Refused("organize_email can: " + ", ".join(EMAIL_ACTIONS))
    label = str(label or "").strip()[:225]
    if action in _LABEL_ACTIONS and not label:
        raise Refused("say which label")
    sel = select_email(query, thread_ids, account)
    n = sel["count"]
    notes = []
    for b in sel["blocked"]:
        notes.append("%s in %s left alone: that account has not allowed Friday to change "
                     "its mailbox (Settings \u2192 Accounts: reconnect with sending)"
                     % (_plural(b["count"], "conversation"),
                        "one account" if _quiet() else b["account"]))
    if _quiet() and sel["errors"]:
        notes.append("%s could not be searched" % _plural(len(sel["errors"]), "account"))
    elif sel["errors"]:
        notes += ["%s could not be searched" % e for e in sel["errors"]]
    if n == 0:
        return {"status": "nothing", "count": 0, "notes": notes,
                "text": "Nothing to change: no conversation matches%s." % (
                    (" " + sel["query"]) if sel["query"] else "")
                + ("" if not notes else " " + "; ".join(notes) + ".")}
    lines = ["%s%s" % (p["subject"], (" \u2014 " + p["sender"]) if p.get("sender") else "")
             for p in sel["preview"][:PREVIEW]]
    if n > len(lines):
        lines.append("\u2026and %d more" % (n - len(lines)))
    detail = {"handler": HANDLER, "domain": "email", "action": action, "label": label,
              "query": sel["query"], "accounts": sel["accounts"], "count": n,
              "preview": sel["preview"], "lines": lines, "conversation_id": conversation_id or "",
              "asked_with": words_hash(owner_words), "why": str(why or "").strip()[:300]}
    if refs:
        detail.update(refs=[str(r) for r in refs][:MAX_ITEMS], selection_id=str(selection_id or "")[:40],
                      stage_rev=int(stage_rev or 0))
    _verb, doing = _email_words(action, label, n)
    if action in EMAIL_NO_CARD:
        rec = run_email(detail)
        _tell_held(detail, "done", receipt_id=rec.get("receipt_id") or "")
        _tell_pages("email", rec.get("receipt_id") or "")
        return {"status": rec.get("status") or "complete", "count": n, "receipt_id": rec.get("receipt_id"),
                "text": rec.get("said") or rec.get("summary") or "", "notes": notes,
                "readback": rec.get("said") or "", "truncated": sel["truncated"]}
    title = "Friday wants to " + doing
    said = [("Found with the Gmail search: " + sel["query"][:300]) if sel["query"]
            else "The conversations Friday named."]
    if sel["truncated"]:
        said.append("The search matched more than %d; only these %d are in this batch." % (MAX_ITEMS, n))
    if detail["why"]:
        said.append("Why (Friday's words): " + detail["why"])
    card = _raise_card("email", action, detail, title=title, body=" ".join(said),
                       requested_by=requested_by, replaces=replaces)
    if card.get("status") == "pending_approval":
        _tell_held(detail, "held", card_id=card.get("approval_id") or "")
    out = {**card, "count": n, "readback": email_readback(sel, action, label, room_mode),
           "preview": [{"subject": p["subject"], "sender": p["sender"]} for p in sel["preview"][:5]],
           "notes": notes + (card.get("notes") or []), "truncated": sel["truncated"]}
    return out


def _label_id(account_id: str, name: str, create: bool) -> tuple[str | None, bool]:
    """(label id, whether it was created now) for a label by name."""
    from agent_friday.services import gmail_mailbox as gm
    low = name.strip().lower()
    for lab in gm.list_labels(account_id):
        if str(lab.get("name") or "").strip().lower() == low:
            return lab["id"], False
    if not create:
        return None, False
    return gm.create_label(account_id, name)["id"], True


def run_email(detail: dict, approval_id: str | None = None) -> dict:
    """Carry out an approved mail batch: exactly the conversations it lists."""
    from agent_friday.services import gmail_mailbox as gm
    action = detail["action"]
    gm_action, _ask, past = EMAIL_ACTIONS[action]
    label = detail.get("label") or ""
    preview = {(p.get("account_id"), p.get("id")): p for p in detail.get("preview") or []}
    items, changed_all, created = [], {}, []
    for acct, tids in (detail.get("accounts") or {}).items():
        try:
            if action in _LABEL_ACTIONS:
                lid, made = _label_id(acct, label, create=(action != "unlabel"))
                if made:
                    created.append(label)
                if lid is None:
                    raise Refused("there is no label called \u201c%s\u201d" % label)
                if action == "unlabel":
                    res = gm.modify_threads(acct, tids, (), (lid,))
                else:
                    res = gm.modify_threads(acct, tids, (lid,), ("INBOX",) if action == "move" else ())
            else:
                res = gm.apply_action(acct, tids, gm_action)
        except Exception as e:
            why = getattr(e, "user_message", None) or exception_text(e)
            res = {"changed": {}, "failed": {t: why for t in tids}}
        changed = res.get("changed") or {}
        if changed:
            changed_all[acct] = changed
        for t in tids:
            p = preview.get((acct, t)) or {}
            items.append({"id": t, "account_id": acct, "subject": p.get("subject"),
                          "sender": p.get("sender"), "ok": t in changed,
                          "error": (res.get("failed") or {}).get(t)})
    n = len(items)
    ok = sum(1 for i in items if i["ok"])
    status = "complete" if ok == n else ("partial" if ok else "failed")
    summary = "%s %s" % (past[0].upper() + past[1:], _plural(ok, "conversation"))
    if action in _LABEL_ACTIONS:
        summary += " (\u201c%s\u201d)" % label
    if ok < n:
        summary += "; %d could not be changed" % (n - ok)
    said = "%s %s%s." % (past[0].upper() + past[1:], _plural(ok, "conversation"),
                         ("; %d could not be changed" % (n - ok)) if ok < n else "")
    if created:
        summary += "; made the label \u201c%s\u201d" % created[0]
    return journal.record("organize_email", action, "email", items=items, summary=summary + ".",
                          status=status, undo={"email": changed_all} if changed_all else {},
                          approval_id=approval_id, conversation_id=detail.get("conversation_id"),
                          extra={"query": detail.get("query") or "", "said": said})


def undo_email(receipt: dict, approval_id: str | None = None) -> dict:
    """Reverse exactly what a mail receipt changed."""
    from agent_friday.services import gmail_mailbox as gm
    changed = (receipt.get("undo") or {}).get("email") or {}
    failed_n, total = 0, 0
    for acct, ch in changed.items():
        total += len(ch)
        try:
            failed_n += len((gm.undo(acct, ch) or {}).get("failed") or {})
        except Exception:
            failed_n += len(ch)
    back = total - failed_n
    journal.update(receipt["receipt_id"], undone=failed_n == 0, undone_at=time.time(),
                   undo_failed=failed_n)
    return journal.record("undo_action", "undo", "email",
                          items=[{"receipt_id": receipt["receipt_id"]}],
                          summary="Put back %s%s." % (_plural(back, "conversation"),
                                                      "; %d could not be" % failed_n if failed_n else ""),
                          status="complete" if not failed_n else ("partial" if back else "failed"),
                          approval_id=approval_id,
                          conversation_id=(receipt.get("run") or {}).get("conversation_id"),
                          extra={"said": "Put back %s%s." % (
                              _plural(back, "conversation"),
                              "; %d could not be" % failed_n if failed_n else "")})


# ── Files ───────────────────────────────────────────────────────────────────

FILE_ACTIONS = ("move", "rename", "trash", "new_folder")

#: Folder names a cloud client keeps in sync with its servers.
_SYNCED = re.compile(r"(?i)^(onedrive( - .+)?|dropbox|google drive|my drive|icloud ?drive|box)$")


def _sf():
    from agent_friday.services import studio_files
    return studio_files


def _roots_words() -> str:
    return "Documents, Downloads, Desktop, Creations or Projects"


def file_ref(text: str) -> tuple[str, str]:
    """(root id, path inside it) for 'Documents/Taxes/w2.pdf',
    'documents:Taxes/w2.pdf' or a full path inside one of the folders."""
    sf = _sf()
    s = str(text or "").strip().strip('"').strip("'").strip()
    if not s:
        raise Refused("a file needs a name")
    rmap = sf.roots()
    p = Path(os.path.expanduser(s))
    if p.is_absolute():
        best = None
        for rid, root in rmap.items():
            try:
                rel = p.resolve(strict=False).relative_to(Path(root).resolve())
            except (ValueError, OSError):
                continue
            if best is None or len(rel.parts) < len(best[1].parts):
                best = (rid, rel)
        if best is None:
            raise Refused("%s is not in %s" % (p.name or s, _roots_words()))
        return best[0], PurePosixPath(*best[1].parts).as_posix() if best[1].parts else ""
    s = s.replace("\\", "/").strip("/")
    head, _sep, rest = s.partition("/")
    if ":" in head:
        head, _c, first = head.partition(":")
        rest = "/".join(x for x in (first, rest) if x)
    key = head.strip().lower()
    for rid in rmap:
        if key in (rid, sf.ROOT_LABELS.get(rid, rid).lower()):
            return rid, rest.strip("/")
    raise Refused("start the path with the folder it is in (%s): %s" % (_roots_words(), s))


def _root_synced(root_id: str) -> bool:
    try:
        root = Path(_sf().roots()[root_id]).resolve()
    except Exception:
        return False
    return any(_SYNCED.match(part) for part in root.parts)


def _dest_folder(dest: str) -> tuple[str, str, list[str]]:
    """(root, folder path, the folder names that do not exist yet) for a
    destination. Its nearest existing folder must be one Friday may show."""
    sf = _sf()
    rid, rel = file_ref(dest)
    parts = [x for x in rel.split("/") if x]
    missing: list[str] = []
    while True:
        try:
            got = sf.resolve(rid, "/".join(parts))
            if not got.is_dir():
                raise Refused("%s is a file, not a folder" % got.name)
            break
        except sf.Denied as e:
            if str(getattr(e, "user_message", "") or e) != "no such file" or not parts:
                raise Refused(str(getattr(e, "user_message", "") or e))
            missing.insert(0, parts.pop())
    for name in missing:
        _valid_name(name, is_dir=True)
    return rid, "/".join(parts + missing), missing


def _valid_name(name: str, *, is_dir: bool = False, old: Path | None = None) -> str:
    sf = _sf()
    try:
        name = sf._valid_new_name(name)
    except sf.Denied as e:
        raise Refused(str(getattr(e, "user_message", "") or e))
    if is_dir:
        from agent_friday.services import open_safety
        if open_safety._SHELL_JUNCTION.search(name):
            raise Refused("that folder name would make it a shell object")
        return name
    if old is not None and old.is_file():
        new_ext, old_ext = Path(name).suffix.lower(), old.suffix.lower()
        if not new_ext and old_ext:
            return name + old.suffix
        if new_ext != old_ext:
            from agent_friday.services import open_safety
            if new_ext not in open_safety.SAFE_OPEN_EXTENSIONS:
                raise Refused("renaming %s to %s would change what kind of file it is; "
                              "keep %s" % (old.name, name, old.suffix or "no extension"))
    return name


def _parse_moves(moves) -> list[tuple[str, str]]:
    out = []
    for m in moves or []:
        src, sep, dest = str(m or "").partition("=>")
        if not sep or not src.strip() or not dest.strip():
            raise Refused('each move reads "item => folder": %s' % m)
        out.append((src.strip(), dest.strip()))
    return out


def plan_files(action: str, items=None, to: str = "", new_name: str = "", moves=None) -> list[dict]:
    """The exact changes a request means, checked against the folders Friday
    may show. Raises Refused for anything it cannot plan."""
    sf = _sf()
    action = str(action or "").strip().lower()
    if action not in FILE_ACTIONS:
        raise Refused("organize_files can: " + ", ".join(FILE_ACTIONS))
    if action == "new_folder":
        if not to:
            raise Refused("say where the new folder goes, e.g. Documents/Taxes 2025")
        rid, rel, missing = _dest_folder(to)
        if not missing:
            raise Refused("that folder already exists")
        return [{"op": "folder", "root": rid, "path": rel, "name": missing[-1],
                 "make": missing}]
    pairs = _parse_moves(moves) if moves else [(i, to) for i in (items or [])]
    if not pairs:
        raise Refused("say which files")
    if len(pairs) > MAX_ITEMS:
        raise Refused("that is %d files; do at most %d at a time" % (len(pairs), MAX_ITEMS))
    if action == "rename" and len(pairs) != 1:
        raise Refused("rename one file at a time")
    ops, seen, landing = [], set(), set()
    for src, dest in pairs:
        rid, rel = file_ref(src)
        if not rel:
            raise Refused("a whole top-level folder cannot be changed")
        try:
            path = sf.resolve(rid, rel)
        except sf.Denied as e:
            raise Refused("%s: %s" % (src, getattr(e, "user_message", "") or e))
        key = (rid, rel.lower())
        if key in seen:
            continue
        seen.add(key)
        op = {"op": action, "root": rid, "path": rel, "name": path.name, "dir": path.is_dir()}
        if action == "move":
            if not dest:
                raise Refused("say which folder to move it to")
            drid, drel, missing = _dest_folder(dest)
            target_rel = "/".join(x for x in (drel, path.name) if x)
            if (drid, target_rel.lower()) == key:
                raise Refused("%s is already there" % path.name)
            if drid == rid and (drel.lower() + "/").startswith(rel.lower() + "/"):
                raise Refused("a folder cannot go inside itself")
            op.update(to_root=drid, to_path=target_rel, make=missing)
        elif action == "rename":
            name = _valid_name(new_name, is_dir=path.is_dir(), old=path)
            parent = str(PurePosixPath(rel).parent)
            op.update(to_root=rid, to_path=name if parent in ("", ".") else parent + "/" + name,
                      make=[])
        if action in ("move", "rename"):
            spot = (op["to_root"], op["to_path"].lower())
            if spot in landing:
                raise Refused("two items called %s would land in the same place"
                              % PurePosixPath(op["to_path"]).name)
            landing.add(spot)
            if not op.get("make") and _abs(op["to_root"], op["to_path"]).exists():
                folder = str(PurePosixPath(op["to_path"]).parent).strip(".") or \
                    sf.ROOT_LABELS.get(op["to_root"], op["to_root"])
                raise Refused("%s already has something called %s; nothing is overwritten"
                              % (folder, PurePosixPath(op["to_path"]).name))
        ops.append(op)
    return ops


def classify_files(args: dict) -> tuple[str, str]:
    """(INTERNAL|OUTWARD, why) for an organize_files call. One local change
    Friday can undo is internal; a batch, anything in the code projects, and
    a move into a folder a cloud client syncs wait for a card."""
    a = args or {}
    try:
        n = len(_parse_moves(a.get("moves"))) if a.get("moves") else len(a.get("items") or [])
        if str(a.get("action") or "").lower() == "new_folder":
            n = 1
        if n >= BULK_FROM:
            return "outward", "a batch of %d waits for one card" % n
        places = []
        for s in list(a.get("items") or []) + [m.split("=>")[0] for m in a.get("moves") or []]:
            places.append(file_ref(s))
        dests = [file_ref(a["to"])] if a.get("to") else []
        dests += [file_ref(m.split("=>", 1)[1]) for m in a.get("moves") or [] if "=>" in m]
        roots = {r for r, _p in places} | {r for r, _p in dests}
        if "projects" in roots:
            return "outward", "it changes files in the code projects"
        if any(_root_synced(r) for r, _p in dests) and not all(_root_synced(r) for r, _p in places):
            return "outward", "it moves a file into a folder a cloud service syncs"
        return "internal", "one local change Friday can undo"
    except Exception as e:
        return "outward", "the change could not be classified (%s)" % e


def _file_words(ops: list[dict]) -> tuple[str, list[str]]:
    sf = _sf()
    lab = lambda r: sf.ROOT_LABELS.get(r, r)  # noqa: E731
    lines = []
    for o in ops:
        if o["op"] == "move":
            lines.append("- %s \u2192 %s/%s" % (o["name"], lab(o["to_root"]),
                                               str(PurePosixPath(o["to_path"]).parent).strip(".")))
        elif o["op"] == "rename":
            lines.append("- %s \u2192 %s" % (o["name"], PurePosixPath(o["to_path"]).name))
        elif o["op"] == "trash":
            lines.append("- %s (%s)" % (o["name"], lab(o["root"])))
        else:
            lines.append("- new folder %s/%s" % (lab(o["root"]), o["path"]))
    kinds = {o["op"] for o in ops}
    n = len(ops)
    if kinds == {"move"}:
        doing = "move %s" % _plural(n, "item")
    elif kinds == {"trash"}:
        doing = "move %s to Friday's trash" % _plural(n, "item")
    elif kinds == {"rename"}:
        doing = "rename %s" % ops[0]["name"]
    else:
        doing = "make a folder"
    return doing, lines


def _abs(root_id: str, rel: str) -> Path:
    root = Path(_sf().roots()[root_id]).resolve()
    return root.joinpath(*[x for x in rel.split("/") if x])


def run_files(ops: list[dict], *, approval_id: str | None = None,
              conversation_id: str | None = None, receipt_id: str | None = None) -> dict:
    """Carry out planned file changes, re-checking each path now: an approval
    covers a change to a file that is still one Friday may show."""
    sf = _sf()
    rid_receipt = receipt_id or journal.new_id()
    items = []
    for o in ops:
        it = {"op": o["op"], "root": o["root"], "path": o["path"], "name": o.get("name"), "ok": False}
        try:
            if o["op"] == "folder":
                parent = "/".join(o["path"].split("/")[:-len(o["make"])])
                base = sf.resolve(o["root"], parent)
                made = []
                cur = base
                for name in o["make"]:
                    cur = cur / _valid_name(name, is_dir=True)
                    cur.mkdir()
                    made.append(cur)
                it.update(ok=True, made=[str(PurePosixPath(parent, *o["make"][:i + 1]))
                                         for i in range(len(made))])
            else:
                src = sf.resolve(o["root"], o["path"])
                if o["op"] == "trash":
                    dst = journal.home_trash(rid_receipt) / "files" / o["root"] / o["path"]
                    journal.safe_move(src, dst)
                    it.update(ok=True, trash=dst.relative_to(journal.home_trash(rid_receipt)).as_posix())
                else:
                    folder = [x for x in o["to_path"].split("/")[:-1] if x]
                    made = []
                    if o.get("make"):
                        have = folder[:len(folder) - len(o["make"])]
                        cur = sf.resolve(o["to_root"], "/".join(have))
                        for name in o["make"]:
                            cur = cur / _valid_name(name, is_dir=True)
                            have = have + [name]
                            if not cur.exists():
                                cur.mkdir()
                                made.append("/".join(have))
                    dest_parent = sf.resolve(o["to_root"], "/".join(folder))
                    target = dest_parent / o["to_path"].split("/")[-1]
                    if target.exists():
                        raise Refused("%s already has something called %s" % (
                            dest_parent.name or o["to_root"], target.name))
                    if src.is_dir() and os.stat(src).st_dev != os.stat(dest_parent).st_dev:
                        raise Refused("a folder cannot move to another drive here; move its files")
                    journal.safe_move(src, target)
                    it.update(ok=True, to_root=o["to_root"], to_path=o["to_path"], made=made)
        except Exception as e:
            it["error"] = str(getattr(e, "user_message", "") or exception_text(e))[:200]
        items.append(it)
    ok = sum(1 for i in items if i["ok"])
    n = len(items)
    doing, _lines = _file_words(ops)
    summary = ("Done: %s." % doing) if ok == n else (
        "%d of %d done (%s); %s" % (ok, n, doing, next((i["error"] for i in items if not i["ok"]), "")))
    status = "complete" if ok == n else ("partial" if ok else "failed")
    op0 = ops[0]["op"] if ops else ""
    said = {"move": "Moved %s", "rename": "Renamed %s", "trash": "Moved %s to Friday's trash",
            "folder": "Made %s"}.get(op0, "Changed %s") % _plural(ok, "folder" if op0 == "folder" else "item")
    if ok < n:
        said += "; %d could not be changed (the reason is in System, Changes Friday made)" % (n - ok)
    said += "."
    return journal.record("organize_files", ops[0]["op"] if ops else "", "files", items=items,
                          summary=summary, status=status,
                          undo={"files": [i for i in items if i["ok"]]} if ok else {},
                          approval_id=approval_id, conversation_id=conversation_id,
                          receipt_id=rid_receipt, extra={"said": said})


def undo_files(receipt: dict) -> dict:
    """Put back what a file receipt did, newest change first. A place that has
    been taken since is left alone and named."""
    rid = receipt["receipt_id"]
    back, failed = 0, []
    for it in reversed((receipt.get("undo") or {}).get("files") or []):
        try:
            if it["op"] == "folder":
                for rel in reversed(it.get("made") or []):
                    p = _abs(it["root"], rel)
                    if p.is_dir() and not any(p.iterdir()):
                        p.rmdir()
                back += 1
                continue
            original = _abs(it["root"], it["path"])
            if it["op"] == "trash":
                current = journal.home_trash(rid) / it["trash"]
            else:
                current = _abs(it["to_root"], it["to_path"])
            if not current.exists():
                raise Refused("%s is no longer where Friday put it" % (it.get("name") or current.name))
            _sf().resolve(it["root"], "/".join(it["path"].split("/")[:-1]))
            journal.safe_move(current, original)
            for rel in reversed(it.get("made") or []):
                p = _abs(it.get("to_root") or it["root"], rel)
                if p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            back += 1
        except Exception as e:
            failed.append("%s: %s" % (it.get("name") or it.get("path"),
                                      str(getattr(e, "user_message", "") or exception_text(e))[:120]))
    journal.update(rid, undone=not failed, undone_at=time.time(), undo_failed=len(failed))
    return journal.record("undo_action", "undo", "files", items=[{"receipt_id": rid}],
                          summary="Put back %s.%s" % (_plural(back, "item"),
                                                      (" Not put back: " + "; ".join(failed[:3])) if failed else ""),
                          status="complete" if not failed else ("partial" if back else "failed"),
                          conversation_id=(receipt.get("run") or {}).get("conversation_id"),
                          extra={"said": "Put back %s%s." % (
                              _plural(back, "item"),
                              "; %d could not be" % len(failed) if failed else "")})


# ── Wiki ────────────────────────────────────────────────────────────────────

WIKI_ACTIONS = ("move", "rename", "tag", "untag", "archive", "trash")
ARCHIVE_DIR = "_archived"

_WIKILINK = re.compile(r"\[\[([^\]|#]+?)((?:[|#][^\]]*?)?)\]\]")
# The wiki graph's own link grammar (knowledge_graph/wiki_graph): a page name
# may hold spaces, "[x](../people/Dana Smith.md)".
_MDLINK = re.compile(r"(\[[^\]]*?\]\()([^)\n]+?\.md)((?:#[^)\n]*)?\))")
_FRONT = re.compile(r"\A---(\r?\n)(.*?)\r?\n---(?:\r?\n|\Z)", re.S)
_TAG_OK = re.compile(r"^[\w][\w\-/.]{0,48}$", re.U)


def _we():
    from agent_friday.services import wiki_engine
    return wiki_engine


def _wiki_root() -> Path:
    return Path(_we().WIKI_DIR).resolve()


def _slug(s: str) -> str:
    return str(s or "").strip().lower().replace(" ", "-").replace("_", "-")


def _pages() -> list[Path]:
    from agent_friday.services.knowledge_graph.wiki_graph import list_wiki_pages
    return list_wiki_pages(_wiki_root())


def _rel(p: Path) -> str:
    return p.resolve().relative_to(_wiki_root()).as_posix()


#: The pages an ambiguous name fitted, per conversation: (when, paths), so a
#: later "#2" names the second.
_CHOICES: dict[str, tuple[float, list[str]]] = {}


def _choice_key() -> str:
    try:
        from agent_friday.services import agent as _ag
        return str(_ag._CURRENT_CONVERSATION.get() or "")
    except Exception:
        return ""


def wiki_ref(text: str) -> str:
    """The page a name or path means, as its path in the wiki. A title that
    fits more than one page is refused with numbered choices, to ask about;
    "#2" then names the second of them."""
    s = str(text or "").strip().strip('"').replace("\\", "/").strip("/")
    if not s:
        raise Refused("say which page")
    pick = re.fullmatch(r"#\s*(\d{1,2})", s)
    if pick:
        when, paths = _CHOICES.get(_choice_key()) or (0.0, [])
        if not paths or time.time() - when > CHOICE_TTL_S:
            raise Refused("there are no numbered pages to choose from; say the page's name")
        n = int(pick.group(1))
        if not 1 <= n <= len(paths):
            raise Refused("choose a number from 1 to %d" % len(paths))
        return paths[n - 1]
    we = _we()
    direct = we._safe_wiki_path(s)
    if direct is not None and direct.is_file():
        return _rel(direct)
    want = _slug(PurePosixPath(s).stem if s.lower().endswith(".md") else s)
    hits = [p for p in _pages() if _slug(p.stem) == want]
    if not hits:
        hits = [p for p in _pages() if want and want in _slug(p.stem)]
    if len(hits) == 1:
        return _rel(hits[0])
    if not hits:
        raise Refused("no wiki page is called %r" % s)
    paths = [_rel(p) for p in hits[:9]]
    _CHOICES[_choice_key()] = (time.time(), paths)
    while len(_CHOICES) > 50:
        _CHOICES.pop(next(iter(_CHOICES)))
    numbered = ["%d. %s" % (i + 1, p) for i, p in enumerate(paths)]
    if _quiet():
        _notify("Which page did you mean?", "\n".join(numbered), priority="medium")
        raise Refused("%d pages fit %r. They are numbered on the user's screen: ask which "
                      "number, then give that page as #1, #2 and so on" % (len(paths), s))
    raise Refused("more than one page is called %r: %s. Which one? (Give it by path, or "
                  "as #1, #2 and so on.)" % (s, "; ".join(numbered)))


def _section(rel: str) -> str:
    return rel.split("/")[0].lower() if "/" in rel else ""


def _sensitive(rel: str) -> bool:
    return _section(rel) in _we()._wiki_encrypted_sections()


def _mirror_root() -> Path | None:
    try:
        return _we()._wiki_mirror_dir()
    except Exception:
        return None


def _mirror_sync(old_rel: str | None, new_rel: str | None) -> None:
    """Keep the owner's mirror folder in step: the old copy goes, the new one
    is the page's bytes (ciphertext for an encrypted section)."""
    root = _mirror_root()
    if root is None:
        return
    from agent_friday.paths import contained
    try:
        if old_rel:
            m = contained(root, old_rel)
            if m.is_file():
                m.unlink()
        if new_rel:
            src = _wiki_root() / new_rel
            m = contained(root, new_rel)
            m.parent.mkdir(parents=True, exist_ok=True)
            m.write_bytes(src.read_bytes())
    except Exception as e:
        _log.warning("wiki mirror not updated (%s -> %s): %s", old_rel, new_rel, e)


def _dirty(why: str) -> None:
    try:
        from agent_friday.services.knowledge_graph import mark_wiki_dirty
        mark_wiki_dirty(why)
    except Exception:
        pass


def _read_page(rel: str) -> str:
    we = _we()
    text = we.wiki_read_text(_wiki_root() / rel)
    if text == we.VAULT_LOCKED_PLACEHOLDER:
        raise Refused("%s is encrypted and the vault is locked" % rel)
    return text


def _write_page(rel: str, text: str) -> None:
    _we().wiki_write_text(_wiki_root() / rel, text)
    _mirror_sync(None, rel)


def _tags_edit(text: str, add=(), remove=()) -> tuple[str, list[str]]:
    """The page with its frontmatter tags changed, and which tags actually
    changed. Keeps the page's own newline and list style."""
    m = _FRONT.match(text)
    nl = m.group(1) if m else ("\r\n" if "\r\n" in text else "\n")
    fm = m.group(2) if m else ""
    lines = fm.split(nl) if fm else []
    tags, style, at, end = [], None, None, None
    for i, line in enumerate(lines):
        flow = re.match(r"^tags:\s*\[(.*)\]\s*$", line)
        if flow:
            tags = [t.strip().strip("'\"") for t in flow.group(1).split(",") if t.strip()]
            style, at, end = "flow", i, i + 1
            break
        if re.match(r"^tags:\s*$", line):
            j = i + 1
            while j < len(lines) and re.match(r"^\s+-\s+", lines[j]):
                tags.append(re.sub(r"^\s+-\s+", "", lines[j]).strip().strip("'\""))
                j += 1
            style, at, end = "block", i, j
            break
        single = re.match(r"^tags:\s*(\S.*)$", line)
        if single:
            tags = [t.strip().strip("'\"") for t in single.group(1).split(",") if t.strip()]
            style, at, end = "flow", i, i + 1
            break
    low = {t.lower() for t in tags}
    changed = []
    for t in add:
        if t.lower() not in low:
            tags.append(t)
            low.add(t.lower())
            changed.append(t)
    for t in remove:
        if t.lower() in low:
            tags = [x for x in tags if x.lower() != t.lower()]
            low.discard(t.lower())
            changed.append(t)
    if not changed:
        return text, []
    if style == "block":
        new = ["tags:"] + ["  - %s" % t for t in tags]
    else:
        new = ["tags: [%s]" % ", ".join(tags)]
    if at is None:
        lines = lines + new
    else:
        lines = lines[:at] + new + lines[end:]
    head = "---" + nl + nl.join(lines) + nl + "---" + nl
    if m:
        return head + text[m.end():], changed
    return head + nl + text, changed


def _rewrite_links(old_rel: str, new_rel: str, *, rename: bool) -> list[dict]:
    """Point every link at the page's new name or place. Returns, per page
    changed, the exact replacements, so undo can reverse just those."""
    old_stem, new_stem = PurePosixPath(old_rel).stem, PurePosixPath(new_rel).stem
    stems = [p for p in _pages() if _slug(p.stem) == _slug(old_stem) and _rel(p) != new_rel]
    wikilinks = rename and not stems        # another page answers to the old name
    done = []
    for p in _pages():
        rel = _rel(p)
        try:
            text = _read_page(rel)
        except Exception:
            continue
        reps: list[tuple[str, str]] = []

        def wl(m):
            if _slug(m.group(1)) != _slug(old_stem):
                return m.group(0)
            new = "[[%s%s]]" % (new_stem, m.group(2))
            reps.append((m.group(0), new))
            return new

        def md(m):
            here = PurePosixPath(rel).parent
            target = os.path.normpath(os.path.join(here.as_posix(), m.group(2))).replace("\\", "/")
            if target.lower() != old_rel.lower():
                return m.group(0)
            if "(" in new_rel or ")" in new_rel:
                # A bracket cannot sit inside a markdown link's address, so the
                # link becomes the wiki's own kind, which finds a page by name.
                anchor = m.group(3)[:-1]
                new = "[[%s%s|%s]]" % (new_stem, anchor, m.group(1)[1:-2])
            else:
                to = os.path.relpath(new_rel, here.as_posix() or ".").replace("\\", "/")
                new = m.group(1) + to + m.group(3)
            reps.append((m.group(0), new))
            return new

        out = _WIKILINK.sub(wl, text) if wikilinks else text
        out = _MDLINK.sub(md, out)
        if out != text and rel != old_rel:
            _write_page(rel, out)
            done.append({"page": rel, "reps": reps})
    return done


def _undo_links(changes: list[dict]) -> int:
    n = 0
    for ch in changes or []:
        rel = ch["page"]
        try:
            text = _read_page(rel)
        except Exception:
            continue
        out = text
        for old, new in ch.get("reps") or []:
            out = out.replace(new, old, 1)
        if out != text:
            _write_page(rel, out)
            n += 1
    return n


def plan_wiki(action: str, pages=None, to: str = "", new_name: str = "", tags=None,
              moves=None) -> list[dict]:
    action = str(action or "").strip().lower()
    if action not in WIKI_ACTIONS:
        raise Refused("organize_wiki can: " + ", ".join(WIKI_ACTIONS))
    pairs = _parse_moves(moves) if moves else [(p, to) for p in (pages or [])]
    if not pairs:
        raise Refused("say which pages")
    if len(pairs) > MAX_ITEMS:
        raise Refused("that is %d pages; do at most %d at a time" % (len(pairs), MAX_ITEMS))
    if action == "rename" and len(pairs) != 1:
        raise Refused("rename one page at a time")
    tag_list = [str(t).strip().lstrip("#") for t in (tags or []) if str(t).strip()]
    if action in ("tag", "untag"):
        if not tag_list:
            raise Refused("say which tags")
        bad = [t for t in tag_list if not _TAG_OK.match(t)]
        if bad:
            raise Refused("a tag is one word (letters, digits, - / .): %s" % ", ".join(bad))
    we = _we()
    ops, seen = [], set()
    for src, dest in pairs:
        rel = wiki_ref(src)
        if rel.lower() in seen:
            continue
        seen.add(rel.lower())
        op = {"op": action, "path": rel, "name": PurePosixPath(rel).stem}
        target = None
        if action == "move":
            folder = str(dest or "").strip().replace("\\", "/").strip("/")
            if not folder:
                raise Refused("say which folder to move it to")
            target = folder + "/" + PurePosixPath(rel).name
        elif action == "rename":
            name = str(new_name or "").strip()
            if not name or any(c in name for c in '\\/:*?"<>|[]#^') or name.startswith((".", "_")):
                raise Refused("that is not a page name Friday can use")
            if not name.lower().endswith(".md"):
                name += ".md"
            parent = str(PurePosixPath(rel).parent)
            target = name if parent in ("", ".") else parent + "/" + name
            taken = [p for p in _pages() if _slug(p.stem) == _slug(PurePosixPath(name).stem)
                     and _rel(p).lower() != rel.lower()]
            if taken:
                if _quiet():
                    raise Refused("a page called %s already exists" % PurePosixPath(name).stem)
                raise Refused("a page called %s already exists (%s)" % (
                    PurePosixPath(name).stem, _rel(taken[0])))
        elif action == "archive":
            target = ARCHIVE_DIR + "/" + rel
        elif action in ("tag", "untag"):
            op["tags"] = tag_list
        if target is not None:
            p = we._safe_wiki_path(target)
            if p is None:
                raise Refused("%s is not a place in the wiki" % target)
            target = _rel(p) if p.exists() else p.relative_to(_wiki_root()).as_posix()
            if target.lower() == rel.lower():
                raise Refused("%s is already there" % op["name"])
            if (_wiki_root() / target).exists():
                raise Refused("%s already has a page called %s" % (
                    str(PurePosixPath(target).parent), PurePosixPath(target).name))
            if _sensitive(rel) and not _sensitive(target) and action == "move" and _quiet():
                raise Refused("%s is in an encrypted section; moving it there would store it "
                              "unencrypted, so it stays where it is" % op["name"])
            if _sensitive(rel) and not _sensitive(target) and action == "move":
                raise Refused("%s is in an encrypted section (%s); moving it to %s would store it "
                              "unencrypted. Encrypt that section first, or keep it in %s."
                              % (op["name"], _section(rel), _section(target) or "the top level",
                                 _section(rel)))
            op["to"] = target
        ops.append(op)
    return ops


def classify_wiki(args: dict) -> tuple[str, str]:
    a = args or {}
    n = len(_parse_moves(a.get("moves"))) if a.get("moves") else len(a.get("pages") or [])
    if n >= BULK_FROM:
        return "outward", "a batch of %d waits for one card" % n
    return "internal", "one change to the wiki that Friday can undo"


def _encrypted_bytes(p: Path) -> bool:
    try:
        import agent_friday.privacy.vault_crypto as _vc
        return bool(_vc.is_encrypted(p.read_bytes()))
    except Exception:
        return False


def _move_page(rel: str, target: str) -> None:
    """Move a page. Its bytes move as they are (an encrypted page stays
    encrypted wherever it goes, and still reads); a plain page moving into an
    encrypted section is encrypted on the way in."""
    root = _wiki_root()
    src, dst = root / rel, root / target
    if _sensitive(target) and not _encrypted_bytes(src):
        text = _read_page(rel)
        if dst.exists():
            raise FileExistsError(str(dst))
        _we().wiki_write_text(dst, text)
        os.unlink(src)
    else:
        journal.safe_move(src, dst)
    _mirror_sync(rel, target)


def _rebase_own_links(old_rel: str, new_rel: str) -> list[dict]:
    """A page that moved to another folder: its own relative links to other
    pages are rewritten to point at the same pages from its new place."""
    old_dir, new_dir = PurePosixPath(old_rel).parent, PurePosixPath(new_rel).parent
    if old_dir == new_dir:
        return []
    try:
        text = _read_page(new_rel)
    except Exception:
        return []
    reps: list[tuple[str, str]] = []

    def md(m):
        link = m.group(2)
        if "://" in link or link.startswith("/"):
            return m.group(0)
        target = os.path.normpath(os.path.join(old_dir.as_posix(), link)).replace("\\", "/")
        to = os.path.relpath(target, new_dir.as_posix() or ".").replace("\\", "/")
        if to == link:
            return m.group(0)
        new = m.group(1) + to + m.group(3)
        reps.append((m.group(0), new))
        return new

    out = _MDLINK.sub(md, text)
    if out == text:
        return []
    _write_page(new_rel, out)
    return [{"page": new_rel, "reps": reps}]


def run_wiki(ops: list[dict], *, approval_id: str | None = None,
             conversation_id: str | None = None, receipt_id: str | None = None) -> dict:
    rid = receipt_id or journal.new_id()
    items = []
    for o in ops:
        it = {"op": o["op"], "path": o["path"], "name": o["name"], "ok": False}
        try:
            if not (_wiki_root() / o["path"]).is_file():
                raise Refused("%s is no longer there" % o["name"])
            if o["op"] in ("tag", "untag"):
                text = _read_page(o["path"])
                new, changed = _tags_edit(text, add=o["tags"] if o["op"] == "tag" else (),
                                          remove=o["tags"] if o["op"] == "untag" else ())
                if changed:
                    _write_page(o["path"], new)
                it.update(ok=True, changed=changed)
            elif o["op"] == "trash":
                dst = journal.home_trash(rid) / "wiki" / o["path"]
                journal.safe_move(_wiki_root() / o["path"], dst)
                _mirror_sync(o["path"], None)
                it.update(ok=True, trash="wiki/" + o["path"])
            else:
                if (_wiki_root() / o["to"]).exists():
                    raise Refused("%s is taken" % o["to"])
                _move_page(o["path"], o["to"])
                own = _rebase_own_links(o["path"], o["to"])
                links = [] if o["op"] == "archive" else _rewrite_links(
                    o["path"], o["to"], rename=o["op"] == "rename")
                it.update(ok=True, to=o["to"], links=links, own_links=own)
        except Exception as e:
            it["error"] = str(getattr(e, "user_message", "") or exception_text(e))[:200]
        items.append(it)
    _dirty("organize:%d" % len(items))
    ok = sum(1 for i in items if i["ok"])
    n = len(items)
    verb = {"move": "moved", "rename": "renamed", "tag": "tagged", "untag": "untagged",
            "archive": "archived", "trash": "moved to Friday's trash"}[ops[0]["op"]] if ops else "changed"
    relinked = sum(len(i.get("links") or []) for i in items)
    summary = "%s %s%s" % (verb[0].upper() + verb[1:], _plural(ok, "page"),
                           (", and updated links in %s" % _plural(relinked, "page")) if relinked else "")
    said = "%s %s%s." % (verb[0].upper() + verb[1:], _plural(ok, "page"),
                         ("; %d not changed" % (n - ok)) if ok < n else "")
    if ok < n:
        summary += "; %d not changed (%s)" % (n - ok, next((i["error"] for i in items if not i["ok"]), ""))
    return journal.record("organize_wiki", ops[0]["op"] if ops else "", "wiki", items=items,
                          summary=summary + ".",
                          status="complete" if ok == n else ("partial" if ok else "failed"),
                          undo={"wiki": [i for i in items if i["ok"]]} if ok else {},
                          approval_id=approval_id, conversation_id=conversation_id, receipt_id=rid,
                          extra={"said": said})


def undo_wiki(receipt: dict) -> dict:
    rid = receipt["receipt_id"]
    back, failed = 0, []
    for it in reversed((receipt.get("undo") or {}).get("wiki") or []):
        try:
            if it["op"] in ("tag", "untag"):
                if it.get("changed"):
                    text = _read_page(it["path"])
                    new, _c = _tags_edit(text, add=it["changed"] if it["op"] == "untag" else (),
                                         remove=it["changed"] if it["op"] == "tag" else ())
                    if new != text:
                        _write_page(it["path"], new)
            elif it["op"] == "trash":
                src = journal.home_trash(rid) / it["trash"]
                if (_wiki_root() / it["path"]).exists():
                    raise Refused("a page is at %s again" % it["path"])
                journal.safe_move(src, _wiki_root() / it["path"])
                _mirror_sync(None, it["path"])
            else:
                if not (_wiki_root() / it["to"]).is_file():
                    raise Refused("%s is no longer at %s" % (it["name"], it["to"]))
                if (_wiki_root() / it["path"]).exists():
                    raise Refused("a page is at %s again" % it["path"])
                _undo_links(it.get("links"))
                _undo_links(it.get("own_links"))
                _move_page(it["to"], it["path"])
            back += 1
        except Exception as e:
            failed.append("%s: %s" % (it.get("name"), str(getattr(e, "user_message", "") or e)[:120]))
    _dirty("organize-undo")
    journal.update(rid, undone=not failed, undone_at=time.time(), undo_failed=len(failed))
    return journal.record("undo_action", "undo", "wiki", items=[{"receipt_id": rid}],
                          summary="Put back %s.%s" % (_plural(back, "page"),
                                                      (" Not put back: " + "; ".join(failed[:3])) if failed else ""),
                          status="complete" if not failed else ("partial" if back else "failed"),
                          conversation_id=(receipt.get("run") or {}).get("conversation_id"),
                          extra={"said": "Put back %s%s." % (
                              _plural(back, "page"),
                              "; %d could not be" % len(failed) if failed else "")})


# ── The local tools: one change now, a batch on a card ─────────────────────

def _said(rec: dict) -> str:
    """What a receipt says happened: by name, or in counts when QUIET."""
    return (rec.get("said") or "It finished (%s)." % rec.get("status")) if _quiet() \
        else (rec.get("summary") or "")


def _local(domain: str, action: str, ops: list[dict], *, bulk: bool, why: str,
           conversation_id: str | None, owner_words: str, requested_by: str,
           replaces: str = "") -> dict:
    run = run_files if domain == "files" else run_wiki
    if not bulk:
        rec = run(ops, conversation_id=conversation_id)
        _tell_pages(domain, rec["receipt_id"])
        return {"status": rec["status"], "receipt_id": rec["receipt_id"], "text": _said(rec)}
    if domain == "files":
        doing, lines = _file_words(ops)
    else:
        doing = "%s %s" % ({"move": "move", "rename": "rename", "tag": "tag", "untag": "untag",
                            "archive": "archive", "trash": "trash"}[action], _plural(len(ops), "page"))
        lines = ["%s%s" % (o["path"], (" \u2192 " + o["to"]) if o.get("to") else
                           (" (%s)" % ", ".join(o["tags"])) if o.get("tags") else "") for o in ops]
    lines = [ln[2:] if ln.startswith("- ") else ln for ln in lines]
    detail = {"handler": HANDLER, "domain": domain, "action": action, "ops": ops,
              "count": len(ops), "lines": lines, "conversation_id": conversation_id or "",
              "asked_with": words_hash(owner_words), "why": str(why or "").strip()[:300]}
    body = "Friday proposes to %s.%s" % (
        doing, (" Why (Friday's words): " + detail["why"]) if detail["why"] else "")
    card = _raise_card(domain, action, detail, title="Friday wants to " + doing, body=body,
                       requested_by=requested_by, replaces=replaces)
    return {**card, "count": len(ops),
            "readback": "That is %s. Shall I %s?" % (_plural(len(ops), "item" if domain == "files" else "page"), doing)}


def organize_files(action: str, *, items=None, to: str = "", new_name: str = "", moves=None,
                   why: str = "", conversation_id: str | None = None, owner_words: str = "",
                   requested_by: str = "friday", replaces: str = "") -> dict:
    ops = plan_files(action, items, to, new_name, moves)
    klass, _why = classify_files({"action": action, "items": items, "to": to, "moves": moves})
    return _local("files", str(action).lower(), ops, bulk=klass != "internal", why=why,
                  conversation_id=conversation_id, owner_words=owner_words,
                  requested_by=requested_by, replaces=replaces)


def organize_wiki(action: str, *, pages=None, to: str = "", new_name: str = "", tags=None,
                  moves=None, why: str = "", conversation_id: str | None = None,
                  owner_words: str = "", requested_by: str = "friday", replaces: str = "") -> dict:
    ops = plan_wiki(action, pages, to, new_name, tags, moves)
    return _local("wiki", str(action).lower(), ops, bulk=len(ops) >= BULK_FROM, why=why,
                  conversation_id=conversation_id, owner_words=owner_words,
                  requested_by=requested_by, replaces=replaces)


# ── Undo ────────────────────────────────────────────────────────────────────

def classify_undo(args: dict, conversation_id: str | None = None) -> tuple[str, str]:
    rec = _undo_target((args or {}).get("receipt_id"), conversation_id, strict=False)
    if rec and rec.get("domain") == "email":
        return "outward", "putting mail back changes Gmail"
    return "internal", "it puts back Friday's own local change"


def _undo_target(receipt_id, conversation_id, strict=True) -> dict | None:
    rid = str(receipt_id or "").strip()
    rec = journal.get(rid) if rid else journal.latest(conversation_id)
    if rec is None and rid and strict:
        raise Refused("there is no receipt %s" % rid)
    if rec is None and strict:
        raise Refused("there is nothing of Friday's to undo in this conversation")
    return rec


def undo(receipt_id: str = "", *, conversation_id: str | None = None, owner_words: str = "",
         by_owner: bool = False, requested_by: str = "friday") -> dict:
    """Undo one receipt. Files and pages go back at once; mail goes back on a
    card, unless the owner asked from their own page (by_owner)."""
    rec = _undo_target(receipt_id, conversation_id)
    if rec.get("undone"):
        return {"status": "nothing", "text": "That was already undone."}
    if not rec.get("undo"):
        return {"status": "nothing", "text": "That change has nothing to put back."}
    dom = rec.get("domain")
    if dom == "email" and not by_owner:
        n = sum(len(v) for v in ((rec.get("undo") or {}).get("email") or {}).values())
        detail = {"handler": HANDLER, "domain": "email_undo", "action": "undo",
                  "receipt_id": rec["receipt_id"], "count": n,
                  "conversation_id": conversation_id or "", "asked_with": words_hash(owner_words)}
        body = "Friday proposes to undo: %s\nThat puts %s back as they were." % (
            rec.get("summary") or "", _plural(n, "conversation"))
        card = _raise_card("email_undo", "undo", detail, requested_by=requested_by,
                           title="Friday wants to put back %s" % _plural(n, "conversation"), body=body)
        return {**card, "count": n, "readback": "Shall I put back the %s I %s?" % (
            _plural(n, "conversation"), EMAIL_ACTIONS.get(rec.get("action"), ("", "", "changed"))[2])}
    out = (undo_email(rec) if dom == "email" else undo_files(rec) if dom == "files"
           else undo_wiki(rec) if dom == "wiki" else None)
    if out is None:
        raise Refused("Friday cannot undo that kind of change")
    _tell_pages(dom, rec["receipt_id"])
    return {"status": out["status"], "receipt_id": out["receipt_id"], "text": _said(out)}


# ── Approved batches ────────────────────────────────────────────────────────

_RUNS: dict[str, dict] = {}      # approval id -> {"event", "receipt"}
_RUNS_LOCK = threading.Lock()


def _execute(detail: dict, approval_id: str | None) -> dict:
    dom = detail.get("domain")
    if dom == "email":
        return run_email(detail, approval_id=approval_id)
    if dom == "email_undo":
        rec = journal.get(detail.get("receipt_id") or "")
        if not rec:
            raise Refused("the receipt to undo is gone")
        return undo_email(rec, approval_id=approval_id)
    if dom == "files":
        return run_files(detail.get("ops") or [], approval_id=approval_id,
                         conversation_id=detail.get("conversation_id"))
    if dom == "wiki":
        return run_wiki(detail.get("ops") or [], approval_id=approval_id,
                        conversation_id=detail.get("conversation_id"))
    raise Refused("unknown batch")


def _start(detail: dict, approval_id: str | None) -> threading.Event:
    ev = threading.Event()
    with _RUNS_LOCK:
        _RUNS[approval_id or ""] = {"event": ev, "receipt": None}
        for old in list(_RUNS)[:-100]:
            _RUNS.pop(old, None)

    def work():
        from agent_friday.services import approvals as ap
        rec = None
        try:
            rec = _execute(detail, approval_id)
            text = rec["summary"] + (" Say \u201cundo that\u201d to put it back."
                                     if rec.get("undo") else "")
            ok = rec["status"] in ("complete", "partial")
        except Exception as e:
            _log.warning("approved batch failed: %s", e)
            text = "The approved change did not happen: %s" % (
                getattr(e, "user_message", "") or exception_text(e))
            ok = False
        if approval_id:
            try:
                ap.mark_used(approval_id, "item_actions",
                             {"ok": ok, "receipt_id": (rec or {}).get("receipt_id"),
                              "summary": text[:300]})
            except Exception:
                pass
        _post_back(detail.get("conversation_id"), text, approval_id)
        _tell_call(detail.get("conversation_id"), rec, ok)
        _notify("Done" if ok else "Not done", text, "info" if ok else "warning")
        if rec:
            _tell_pages(detail.get("domain", "").replace("_undo", ""), rec["receipt_id"])
        _tell_held(detail, "done" if ok else "declined", receipt_id=(rec or {}).get("receipt_id") or "",
                   card_id=approval_id or "")
        with _RUNS_LOCK:
            run = _RUNS.get(approval_id or "")
            if run is not None:
                run["receipt"] = rec or {"status": "failed", "summary": text}
        ev.set()

    threading.Thread(target=work, name="item-batch", daemon=True).start()
    return ev


def _tell_call(conversation_id: str | None, rec: dict | None, ok: bool) -> None:
    """A live voice call on the conversation hears how an approved batch
    ended, in counts: that call is the cloud voice model, which is never
    handed a name (voice-tool-contract.md §5)."""
    cid = str(conversation_id or "").strip()
    if not cid:
        return
    try:
        from agent_friday.services import voice_live_channel as vlc
        if not vlc.is_live(cid):
            return
        if ok and rec:
            line = "The approved change finished: %s" % (rec.get("said") or "done.")
            if rec.get("undo"):
                line += " They can say \u201cundo that\u201d to put it back."
        else:
            line = "The approved change did not happen; the reason is on their screen."
        vlc.deliver(cid, line, kind="result")
    except Exception as e:
        _log.info("could not tell the live call: %s", e)


def _on_decision(record: dict) -> None:
    detail = record.get("payload") or {}
    if detail.get("handler") != HANDLER:
        return
    if (record.get("status") or "").lower() != "approved":
        if (record.get("status") or "").lower() in ("denied", "blocked", "expired"):
            _tell_held(detail, "declined", card_id=record.get("approval_id") or "")
        return
    from agent_friday.governance import action_gate
    from agent_friday.services import approvals as ap
    aid = record.get("approval_id")
    if not ap.claim_for_execution(aid):
        return
    name = _action_name(detail.get("domain", ""), detail.get("action", ""))
    v = action_gate.authorize_external(name, detail, requested_by="owner approval", approval_id=aid)
    if v.action != "allow":
        ap.mark_used(aid, "item_actions", {"ok": False, "error": v.reason})
        _post_back(detail.get("conversation_id"),
                   "You approved it, but Friday's governance check held it: %s. Nothing changed." % v.reason,
                   aid)
        return
    _start(detail, aid)


def wait_for(approval_id: str, timeout: float) -> dict | None:
    """The receipt of an approved batch, once it has finished (None if it is
    still running after `timeout`)."""
    with _RUNS_LOCK:
        run = _RUNS.get(approval_id)
    if run is None:
        return None
    if run["event"].wait(max(0.0, timeout)):
        return run["receipt"]
    return None


# ── Answering a card in words ──────────────────────────────────────────────

def typed_decision(words: str) -> str | None:
    """'approve', 'deny' or None from the owner's typed words, by the chat
    confirmation gate's rule (agent._is_affirmative): the whole reply must
    approve, so "yes, but not those" approves nothing, and a reply that reads
    both ways is neither. Spoken words go through the voice path instead
    (local_context.decide_by_voice); nothing here adds a way to say yes."""
    from agent_friday.services import agent as _ag
    w = str(words or "")
    if _ag._is_negative(w):
        return "deny"
    if _ag._is_affirmative(w):
        return "approve"
    return None


def _not_recorded(want: str, room_mode: bool) -> dict:
    return {"ok": False, "text": "NOT RECORDED: the user's own words did not %s it%s. Ask them directly." % (
        "approve" if want == "approve" else "decline",
        " (with several people in the room, a spoken OK must name Friday)" if room_mode else "")}


def answer_card(card_id: str, claimed: str, owner_words: str, *, room_mode: bool = False,
                surface: str = "chat") -> dict:
    """Decide one of Friday's organize cards from the owner's own words.

    Spoken words decide through local_context.decide_by_voice, the one way a
    card is decided by voice (a no wins; in a room a yes names Friday); typed
    words by the chat gate's whole-reply rule. Either way the words must come
    after the card was raised."""
    from agent_friday.services import approvals as ap
    rec = ap.get_approval(str(card_id or "").strip())
    if not rec or (rec.get("payload") or {}).get("handler") != HANDLER:
        return {"ok": False, "text": "NOT RECORDED: there is no pending organize card with that id."}
    if rec.get("status") != "pending":
        return {"ok": False, "text": "NOT RECORDED: that card is already %s." % rec.get("status")}
    if not str(owner_words or "").strip():
        return {"ok": False, "text": "NOT RECORDED: there are no words from the user to go by."}
    if words_hash(owner_words) == (rec.get("payload") or {}).get("asked_with"):
        return {"ok": False, "text": "NOT RECORDED: the user has not answered since the card was raised. "
                                     "Read it back and ask."}
    want = {"approve": "approve", "yes": "approve", "decline": "deny", "deny": "deny",
            "no": "deny"}.get(str(claimed or "").strip().lower())
    if want is None:
        return {"ok": False, "text": "NOT RECORDED: the decision is approve or decline."}
    spoken = str(surface or "").startswith("voice")
    if spoken:
        from agent_friday.services import local_context as _lc
        got = _lc.decide_by_voice(rec["approval_id"], owner_words, room_mode, want)
        if "error" in got:
            return _not_recorded(want, room_mode)
        won, status = bool(got.get("won")), got.get("status")
    else:
        if typed_decision(owner_words) != want:
            return _not_recorded(want, room_mode)
        out, won = ap.decide_with_outcome(rec["approval_id"], want,
                                          decided_by="owner:%s" % (surface or "chat"))
        status = (out or {}).get("status")
    if not won:
        return {"ok": False, "text": "That card was already decided (%s)." % status}
    if want == "deny":
        return {"ok": True, "text": "Recorded: declined. Nothing was changed."}
    done = wait_for(rec["approval_id"], WAIT_VOICE_S if spoken else WAIT_CHAT_S)
    if done is None:
        # The finished batch reports to the conversation that raised the card.
        cid = (rec.get("payload") or {}).get("conversation_id")
        told = False
        try:
            from agent_friday.services import voice_live_channel as vlc
            told = spoken and vlc.is_live(cid)
        except Exception:
            pass
        return {"ok": True, "text": "RUNNING: approved, and still working. " + (
            "The outcome is told to you when it finishes." if told else
            "The result will be posted in the conversation when it finishes.")}
    return {"ok": True, "receipt_id": done.get("receipt_id"),
            "text": "Approved and done: %s" % _said(done)}


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
        _log.warning("could not register the organize hook: %s", e)


register_hooks()
