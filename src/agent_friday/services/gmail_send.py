"""Sending mail, which Friday could not do until 2026-09-20 and still cannot
do alone.

THE POSITION, and why it is not the obvious one. Friday has deliberately never
requested a Gmail send scope (`services/agent.py` refuses it in the system
prompt, and `google_accounts.GOOGLE_MULTI_SCOPES` is read-only for mail). The
maintainer's instruction on 2026-09-20 was "I definitely want the ability to
send Gmail, but only with explicit authorization" — which is a different thing
from "add a send tool".

The difference matters because of what else is true of this machine: a nightly
self-improvement loop runs unattended, spawns background tasks, and until
today did so with no declared scope at all. Of every capability available
here, "sends mail as Stephen while he is asleep" has the largest blast radius,
and the failure is not technical — it is a real message to a real person he
knows. So the gate is not a setting. It is structural:

  * `send()` REQUIRES an approval id and re-reads that approval from the queue
    itself. It does not accept a caller's word that approval happened.
  * The approval must be `approved` — not `auto_approved`. `gate_action`
    classifies `external_message` as gated by default, but the policy table is
    user-overridable, and an override that made mail auto-approve must not
    silently become a send. This function refuses anything it did not watch a
    human decide.
  * The approval must match THIS message. The card carries a hash of
    recipients + subject + body; if any of it changed after the decision, the
    send is refused. Approving a draft is not approving whatever the drafter
    later felt like sending.
  * A used approval is burned. One decision, one message.

NARROWEST SCOPE THAT DOES THE JOB. `gmail.send` only — not `gmail.compose`.
Compose would let Friday create, read, alter and delete drafts in the real
mailbox; send lets it do exactly the thing that was authorised and nothing
else. The review copy lives on the approval card, where the person already is,
rather than in a Gmail draft nobody asked to have written.

ADDING THIS SCOPE REQUIRES RE-CONSENT on every connected account. Nothing here
works until the owner reconnects and grants it, and that is stated rather than
discovered.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from email.message import EmailMessage

_log = logging.getLogger("friday.gmail_send")

#: The one scope. See the module docstring on why not `gmail.compose`.
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"

#: Approval classification. `external_message` is gated in the default policy
#: table (services/approvals.py:POLICY_TABLE_DEFAULTS).
APPROVAL_KIND = "external_message"
SUBJECT_TYPE = "email"

_ADDR = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class SendRefused(RuntimeError):
    """A send that did not happen, and says why."""


def scope_granted(account_id: str | None = None) -> bool:
    """Has the owner actually granted send on the account that would be used?

    Reads the scopes Google returned, not the scopes Friday requested. Google
    lets a person decline individual permissions, so those are different
    things — and an `include_send` connect where the box got unticked must
    read as "cannot send", not as "asked, therefore may".
    """
    try:
        from agent_friday.services import google_accounts as G
        recs = [r for r in G.list_accounts()
                if not account_id or r.get("id") == account_id]
        if account_id and not recs:
            return False
        return any(GMAIL_SEND in (r.get("scopes") or []) for r in recs)
    except Exception:
        return False


def sendable_accounts() -> list:
    """Accounts that can actually send, for the settings UI and the agent."""
    try:
        from agent_friday.services import google_accounts as G
        return [{"id": r.get("id"), "email": r.get("email"),
                 "label": r.get("label")}
                for r in G.list_accounts()
                if GMAIL_SEND in (r.get("scopes") or [])]
    except Exception:
        return []


def _addresses(raw) -> list:
    """Normalise and validate recipients.

    Rejects rather than repairs. A malformed address in a `to:` list is the
    kind of thing that silently sends to the wrong person when a parser is
    generous.
    """
    if isinstance(raw, str):
        parts = [p.strip() for p in re.split(r"[,;]", raw)]
    else:
        parts = [str(p).strip() for p in (raw or [])]
    out = [p for p in parts if p]
    bad = [p for p in out if not _ADDR.match(p)]
    if bad:
        raise SendRefused("these are not valid email addresses: %s"
                          % ", ".join(bad))
    return out


def message_fingerprint(to, subject: str, body: str,
                        cc=None, bcc=None) -> str:
    """What the owner approved, exactly.

    The approval card is a promise about a specific message. Hashing the
    recipients, subject and body means a send whose content drifted after the
    decision is refused instead of delivered — approving a draft is not
    approving whatever the drafter later felt like sending.
    """
    payload = json.dumps({
        "to": _addresses(to), "cc": _addresses(cc or []),
        "bcc": _addresses(bcc or []),
        "subject": str(subject or ""), "body": str(body or ""),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _live_card(fingerprint: str) -> dict | None:
    """The undecided-or-unspent card for this exact message, if one exists.

    Two things have to be true at once, and a naive idempotency key gives you
    only one of them. Retrying a request must NOT pile duplicate cards into
    the owner's queue; but re-sending a message that was already sent must
    still ask again, because one decision buys one message. So a card counts
    as live only while it is pending, or approved-and-not-yet-spent. Once it
    is spent, denied or expired, the next request mints a fresh generation
    and the owner gets asked again — which is the correct outcome, not a
    duplicate.
    """
    from agent_friday.services import approvals as _ap
    for rec in _ap.list_approvals(kind=APPROVAL_KIND,
                                  subject_type=SUBJECT_TYPE):  # newest first
        if (rec.get("payload") or {}).get("fingerprint") != fingerprint:
            continue
        status = (rec.get("status") or "").lower()
        if status == "pending":
            return rec
        if status == "approved" and not rec.get("consumed"):
            return rec
    return None


def request_send(*, to, subject: str, body: str, cc=None, bcc=None,
                 account_id: str | None = None,
                 requested_by: str = "friday") -> dict:
    """Ask to send. Returns the approval card; sends nothing.

    This is the only way to start a send, and it never delivers.

    It deliberately does NOT go through `approvals.gate_action`. That helper
    consumes a card the moment it observes one that is already approved —
    correct for callers where asking and acting are the same step, wrong
    here, where a second request for an identical message would silently burn
    the approval that a later `send()` needs. `create_approval` with
    force_gate leaves the burning to `send()`, which is the only thing that
    knows whether a message actually went out.
    """
    from agent_friday.services import approvals as _ap
    import uuid

    tos = _addresses(to)
    if not tos:
        raise SendRefused("a message needs at least one recipient")
    if not str(body or "").strip():
        raise SendRefused("refusing to send an empty message")

    # Resolve WHICH identity this goes out as, now, and put it on the card.
    # "From" is the part of a message that cannot be corrected afterwards, and
    # an owner approving a send is approving it from a specific address of
    # his. Guessing between two connected accounts is not a small wrong guess.
    sendable = sendable_accounts()
    if not sendable:
        raise SendRefused(
            "no connected Google account has granted Friday permission to "
            "send. Settings → Connectors → Google → Add account, with "
            "\"allow sending\" ticked. Nothing was queued.")
    if account_id:
        match = [a for a in sendable if a["id"] == account_id]
        if not match:
            raise SendRefused(
                "that account cannot send mail (it was connected read-only). "
                "Nothing was queued.")
        account = match[0]
    elif len(sendable) == 1:
        account = sendable[0]
    else:
        raise SendRefused(
            "more than one account can send (%s) — say which one. Nothing "
            "was queued." % ", ".join(a.get("email") or a["id"]
                                      for a in sendable))
    account_id = account["id"]

    fp = message_fingerprint(tos, subject, body, cc, bcc)
    live = _live_card(fp)
    if live is not None:
        return {"status": live.get("status"), "approval": live,
                "approval_id": live.get("approval_id")}

    ccs = _addresses(cc or [])
    appr = _ap.create_approval(
        kind=APPROVAL_KIND,
        subject_type=SUBJECT_TYPE,
        subject_id="%s:%s" % (fp, uuid.uuid4().hex[:8]),
        title="Send email to %s" % ", ".join(
            tos[:3] + (["…"] if len(tos) > 3 else [])),
        action_description=(
            "Send mail as you.\n\nFrom: %s\nTo: %s\nCc: %s\nSubject: %s\n\n%s"
            % (account.get("email") or account_id, ", ".join(tos),
               ", ".join(ccs) or "—",
               subject or "(no subject)", str(body or ""))),
        description="This leaves your machine and arrives as a message from "
                    "you. It cannot be unsent.",
        # ALWAYS gate, whatever the policy table has been set to. The default
        # classifies external_message as gated; force_gate means a local
        # override cannot turn mail into an auto-approve.
        force_gate=True,
        payload={# The decision hook fires for EVERY external_message card,
                 # including ones other subsystems create. This marker is how
                 # it knows a card is one of ours; without it, approving an
                 # unrelated external message would reach send().
                 "handler": "gmail_send",
                 "fingerprint": fp, "to": tos,
                 "cc": ccs, "bcc": _addresses(bcc or []),
                 "subject": str(subject or ""), "body": str(body or ""),
                 "account_id": account_id,
                 "from_email": account.get("email")},
        requested_by=requested_by,
    )
    _log.info("send requested to %d recipient(s); approval %s (%s)",
              len(tos), appr.get("approval_id"), appr.get("status"))
    return {"status": appr.get("status"), "approval": appr,
            "approval_id": appr.get("approval_id")}


def send(approval_id: str) -> dict:
    """Deliver a message the owner approved. The ONLY function that sends.

    Everything it checks, it checks by re-reading the queue — a caller saying
    "this was approved" is not evidence.
    """
    from agent_friday.services import approvals as _ap
    from agent_friday.services import google_accounts as G

    appr = _ap.get_approval(approval_id)
    if not appr:
        raise SendRefused("no such approval: %s" % approval_id)

    status = (appr.get("status") or "").lower()
    if status != "approved":
        # `auto_approved` is refused on purpose. See the module docstring.
        raise SendRefused(
            "this message is %s, not approved by you. Nothing was sent."
            % (status or "in an unknown state"))
    if appr.get("consumed"):
        raise SendRefused("that approval was already used. One decision, one "
                          "message — ask again if you want another sent.")
    if (appr.get("kind") or "") != APPROVAL_KIND:
        raise SendRefused("that approval is for %r, not for sending mail."
                          % appr.get("kind"))

    payload = appr.get("payload") or {}
    expected = payload.get("fingerprint")
    actual = message_fingerprint(payload.get("to"), payload.get("subject"),
                                 payload.get("body"), payload.get("cc"),
                                 payload.get("bcc"))
    if not expected or expected != actual:
        raise SendRefused(
            "the message changed after you approved it. Nothing was sent — "
            "ask again with the new text.")

    # And separately: does the payload still match the card's own identity?
    #
    # The check above only proves the payload agrees with a hash stored in the
    # same payload, which one edit to both would preserve. `subject_id` is
    # different: request_send writes the fingerprint into it at creation, it
    # is the queue's idempotency key, and nothing downstream rewrites it. So
    # it is the copy of the fingerprint that a payload edit cannot reach.
    stamped = (appr.get("subject_id") or "").split(":")[0]
    if stamped != actual:
        raise SendRefused(
            "this message does not match the card you approved. Nothing was "
            "sent.")
    # A second, weaker check against the text the owner literally read.
    # `action_description` is truncated to 1000 chars at creation, so this can
    # only ever be a bonus — it is skipped outright when the text was cut,
    # rather than refusing a legitimate long message. The subject_id check
    # above is the guarantee; this one just catches a short-message swap
    # early, with a clearer error.
    shown = appr.get("action_description") or ""
    if len(shown) < 1000:
        for label, value in (("recipients", ", ".join(payload.get("to") or [])),
                             ("subject", payload.get("subject") or "")):
            if value and value[:120] not in shown:
                raise SendRefused(
                    "the %s do not match what you approved. Nothing was sent."
                    % label)

    # The account was pinned at request time, so the message goes out as the
    # address the owner saw on the card — not as whichever account happens to
    # be primary when send() runs. Re-checked here because a grant can be
    # revoked between the decision and the send.
    account_id = payload.get("account_id")
    if not account_id:
        raise SendRefused("that approval does not say which account to send "
                          "from. Nothing was sent.")
    if not scope_granted(account_id):
        raise SendRefused(
            "%s has not granted Friday permission to send mail (or the grant "
            "was withdrawn). Reconnect it in Settings with sending allowed; "
            "nothing was sent." % (payload.get("from_email") or account_id))
    creds = G.credentials_for(account_id)
    if not creds:
        raise SendRefused("that account's credentials could not be read, so "
                          "nothing was sent.")

    msg = EmailMessage()
    msg["To"] = ", ".join(payload.get("to") or [])
    if payload.get("cc"):
        msg["Cc"] = ", ".join(payload["cc"])
    if payload.get("bcc"):
        msg["Bcc"] = ", ".join(payload["bcc"])
    msg["Subject"] = payload.get("subject") or ""
    msg.set_content(payload.get("body") or "")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    # BURN THE APPROVAL BEFORE THE NETWORK CALL, not after.
    #
    # Two callers can reach the `consumed` check above at the same time — the
    # decision hook and a tool call, say — and both pass, and the message goes
    # twice. `_consume` is the check-and-set that happens under the queue's
    # own lock and tells the loser it was not first. It is private to
    # approvals.py, and used here deliberately: this is exactly the case it
    # exists for, and a public wrapper would not make the race any smaller.
    #
    # The cost of burning first is that a crash between here and Gmail leaves
    # an approval spent with no message sent. That is the right direction to
    # fail: the owner is asked again, rather than a stranger receiving the
    # same message twice.
    _burned, was_first = _ap._consume(appr)
    if not was_first:
        raise SendRefused("that approval is already being used to send. "
                          "Nothing was sent twice.")

    try:
        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        sent = svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        _log.warning("send failed: %s", e)
        _outbox_record(appr, ok=False, error=str(e)[:500])
        raise SendRefused("Gmail refused the message: %s" % str(e)[:300])

    _ap.mark_used(approval_id, "gmail_send", {"message_id": sent.get("id"),
                                              "thread_id": sent.get("threadId")})
    _outbox_record(appr, ok=True, message_id=sent.get("id"))
    _log.info("sent message %s via approval %s", sent.get("id"), approval_id)
    return {"ok": True, "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": payload.get("to"), "approval_id": approval_id}


# ═══════════════════════════════════════════════════════════════════════════
#  OUTBOX — every attempt, in one append-only place the owner can read
# ═══════════════════════════════════════════════════════════════════════════
#
# The approval queue records the DECISION. It does not record the outcome,
# and the two come apart in the only way that matters: an approved card whose
# send failed looks, in the queue, exactly like an approved card whose send
# worked. For anything else that would be a reporting nicety. For mail it is
# the difference between "he thinks it went" and "it went".

def _outbox_path():
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "sent_mail.jsonl"


def _outbox_record(appr: dict, *, ok: bool, message_id: str | None = None,
                   error: str | None = None) -> None:
    import time
    payload = appr.get("payload") or {}
    row = {"at": time.time(), "ok": bool(ok),
           "approval_id": appr.get("approval_id"),
           "from": payload.get("from_email"),
           "to": payload.get("to"), "cc": payload.get("cc"),
           "subject": payload.get("subject"),
           "message_id": message_id, "error": error}
    try:
        path = _outbox_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:  # never let bookkeeping break or fake a send
        _log.warning("could not write the outbox record: %s", e)


def outbox(limit: int = 50) -> list:
    """Recent send attempts, newest first — successes and failures alike."""
    try:
        path = _outbox_path()
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8",
                                   errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        return rows[::-1][:max(1, int(limit or 50))]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════
#  DECISION HOOK — approving the card is what sends the message
# ═══════════════════════════════════════════════════════════════════════════
#
# The card says "Send email to X". If clicking approve did not send it, the
# card would be lying about what the button does, and the owner would have to
# issue a second instruction that adds no information. So approval sends.
#
# It runs off the decide() thread because decide() is called from an HTTP
# handler and a hung Gmail call must not hang the request — and because
# approvals._fire_hook swallows hook exceptions, which is fine for a resumable
# goal and not fine here. The outbox above is what makes the outcome visible
# either way.

def _on_decision(record: dict) -> None:
    import threading
    payload = record.get("payload") or {}
    if payload.get("handler") != "gmail_send":
        return                      # some other external_message card
    if (record.get("status") or "").lower() != "approved":
        return                      # denied or expired: nothing to do
    approval_id = record.get("approval_id")

    def _run():
        try:
            result = send(approval_id)
            _notify("Email sent",
                    "Your message to %s is on its way."
                    % ", ".join(payload.get("to") or []),
                    priority="low", kind="info")
            _log.info("hook sent %s", result.get("message_id"))
        except SendRefused as e:
            # A refusal here is the important one: the owner pressed approve
            # and reasonably believes the message went. Say otherwise loudly.
            _notify("Email NOT sent", str(e), priority="high", kind="warning")
        except Exception as e:
            _log.warning("hook send crashed: %s", e)
            _notify("Email NOT sent",
                    "Something went wrong sending it: %s" % str(e)[:200],
                    priority="high", kind="warning")

    threading.Thread(target=_run, name="gmail-send", daemon=True).start()


def _notify(title: str, body: str, *, priority: str = "medium",
            kind: str = "info") -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body, priority=priority, kind=kind,
                source="gmail_send")
    except Exception as e:
        _log.warning("could not notify (%s): %s", title, e)


def register_hooks() -> None:
    """Idempotent; called at import and safe to call again."""
    global _HOOKS_REGISTERED
    if _HOOKS_REGISTERED:
        return
    try:
        from agent_friday.services import approvals as _ap
        _ap.register_decision_hook(APPROVAL_KIND, _on_decision)
        _HOOKS_REGISTERED = True
    except Exception as e:
        _log.warning("could not register the send hook: %s", e)


_HOOKS_REGISTERED = False
register_hooks()
