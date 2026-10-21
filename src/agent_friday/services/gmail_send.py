"""Sending mail, which Friday cannot do alone.

THE POSITION, and why it is not the obvious one. Friday has deliberately never
requested a Gmail send scope (`services/agent.py` refuses it in the system
prompt, and `google_accounts.GOOGLE_MULTI_SCOPES` is read-only for mail). The
requirement is the ability to send Gmail, but only with explicit
authorization — which is a different thing from "add a send tool".

The difference matters because of what else is true of this machine: a nightly
self-improvement loop runs unattended and spawns background tasks. Of every
capability available here, "sends mail as the owner while they are asleep"
has the largest blast radius, and the failure is not technical — it is a real
message to a real person they know. So the gate is not a setting. It is structural:

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
  * The one exception to "a card per message" is the owner's own standing
    pre-approval (governance/send_grants.py, made in Settings): named
    recipients, optionally one scheduled job or skill. The action checkpoint
    finds it, this module re-checks it for the exact recipients, and the
    approval record is created already approved BY THAT GRANT (never
    `auto_approved`). Everything else here still applies, and every use is
    logged. Recipients the grant does not name still get a card.

SENDING NEEDS gmail.send, ALWAYS. The owner may also grant gmail.modify
("Reconnect with sending"), which Google would let send mail too; this module
never sends on the strength of modify. Modify is used for one thing here:
saving a draft into the owner's own Gmail Drafts — when they press "Save
draft", or when they ask Friday to draft a message (the draft_email tool,
whose default is to save, not send). A draft reaches nobody: it is a write to
their own mailbox, receipted, with no card. Sending it is a separate,
explicit request (draft_email with send=true), which goes through
request_send and its card like any other send.

AFTER APPROVAL, A SHORT HOLD. An approved message waits UNDO_SECONDS (or
until the time it was scheduled for) in a held queue on disk, and the owner
can cancel it there; cancelling burns the approval, so that message can never
go. If Friday was not running when a held message fell due and it is more
than LATE_LIMIT_S late, it is not sent at all: the owner is told and asked
again, rather than a stranger receiving it hours after it made sense.

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
from email import policy as _email_policy
from email.message import EmailMessage
from agent_friday.user_errors import UserFacingError, log_failure

_log = logging.getLogger("friday.gmail_send")

#: The one scope. See the module docstring on why not `gmail.compose`.
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"

#: Approval classification. `external_message` is gated in the default policy
#: table (services/approvals.py:POLICY_TABLE_DEFAULTS).
APPROVAL_KIND = "external_message"
SUBJECT_TYPE = "email"

# The domain needs a dot with at least one character on each side; matching up
# to the FIRST such dot keeps the check linear in the address length.
_ADDR = re.compile(r"^[^@\s,;]+@[^@\s,;][^@\s,;.]*\.[^@\s,;]+$")

#: After approval, how long a message waits for "Undo" before it goes, when the
#: owner approved it in person. A send nobody is watching (a scheduled job or a
#: background task under a pre-approval) waits the owner's own window instead:
#: `unattended_undo_seconds()` (Settings, 2 minutes by default, 10 s to 10 min).
UNDO_SECONDS = 10
#: A held message this late (Friday was off) is not sent; the owner is asked again.
LATE_LIMIT_S = 15 * 60
#: How far ahead "send later" may be scheduled.
MAX_SCHEDULE_S = 30 * 86400


class SendRefused(UserFacingError, RuntimeError):
    """A send that did not happen, and says why. The message is written for
    the user; where a provider's own text explains the failure it rides in
    `detail` (what str() returns, for the model and the log)."""


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
                        cc=None, bcc=None, extras: dict | None = None) -> str:
    """What the owner approved, exactly.

    The approval card is a promise about a specific message. Hashing the
    recipients, subject and body means a send whose content drifted after the
    decision is refused instead of delivered — approving a draft is not
    approving whatever the drafter later felt like sending.
    """
    doc = {
        "to": _addresses(to), "cc": _addresses(cc or []),
        "bcc": _addresses(bcc or []),
        "subject": str(subject or ""), "body": str(body or ""),
    }
    # Formatting, threading and attachments are part of the message too.
    # A plain message hashes exactly as it always has, so cards already in
    # the queue stay valid.
    if extras:
        doc["x"] = extras
    payload = json.dumps(doc, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Attachments ───────────────────────────────────────────────────────────
# Files to send are stored once, by content hash, under Friday's home. The
# approval card names them and the fingerprint covers their hashes, so the
# bytes that go out are the bytes that were approved.

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024        # Gmail's limit is 25 MB per message


def _att_dir():
    from agent_friday.core import FRIDAY_DIR
    d = FRIDAY_DIR / "mail_outbox" / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_attachment(data: bytes, filename: str, mime: str) -> dict:
    if not data:
        raise SendRefused("that attachment is empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise SendRefused("that attachment is larger than %d MB" % (MAX_ATTACHMENT_BYTES // 1048576))
    sha = hashlib.sha256(data).hexdigest()
    safe = re.sub(r'[\\/:*?"<>|\r\n]', '_', filename or 'attachment')[:180] or 'attachment'
    (_att_dir() / sha).write_bytes(data)
    return {"sha256": sha, "filename": safe, "mime": mime or "application/octet-stream", "size": len(data)}


def _load_attachment(meta: dict) -> bytes:
    sha = str(meta.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SendRefused("an attachment reference is malformed. Nothing was sent.")
    path = _att_dir() / sha
    if not path.exists():
        raise SendRefused("the attachment %s is no longer on this machine. Nothing was sent."
                          % meta.get("filename"))
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise SendRefused("the attachment %s changed after you approved it. Nothing was sent."
                          % meta.get("filename"))
    return data


def _send_at_iso(send_at):
    """Normalise a requested send time to UTC ISO seconds, or None. Refuses
    the past and anything beyond MAX_SCHEDULE_S."""
    if not send_at:
        return None
    from datetime import datetime, timezone
    import time as _t
    try:
        dt = datetime.fromisoformat(str(send_at).replace("Z", "+00:00"))
    except ValueError:
        raise SendRefused("that send time is not a date and time I can read")
    if dt.tzinfo is None:
        dt = dt.astimezone()                     # a local time, as the owner typed it
    ts = dt.timestamp()
    if ts < _t.time() + 60:
        raise SendRefused("that send time is in the past (or under a minute away); send it now instead")
    if ts > _t.time() + MAX_SCHEDULE_S:
        raise SendRefused("that send time is more than 30 days away")
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _extras(html=None, thread_id=None, in_reply_to=None, references=None, attachments=None,
            send_at=None) -> dict:
    x = {}
    if send_at:
        x["send_at"] = send_at
    if html:
        x["html"] = str(html)
    if thread_id:
        x["thread_id"] = str(thread_id)
    if in_reply_to:
        x["in_reply_to"] = str(in_reply_to)
    if references:
        x["references"] = str(references)
    if attachments:
        x["attachments"] = [{"sha256": a["sha256"], "filename": a["filename"],
                             "mime": a.get("mime") or "application/octet-stream",
                             "size": int(a.get("size") or 0)} for a in attachments]
    return x


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


def _tool_context() -> dict:
    """The running tool call's session context (task, schedule), if any."""
    try:
        from agent_friday.services import agent as _agent
        return dict(_agent._CURRENT_TOOL_CONTEXT.get() or {})
    except Exception:
        return {}


def _pre_approval(recipients):
    """The owner's standing pre-approval behind THIS call, re-checked for
    these exact recipients, or None. Only the action checkpoint can name it:
    it is the owner's decision (`action_gate.owner_decision`) the checkpoint
    set from a send grant, never anything a caller passes in."""
    try:
        from agent_friday.governance import action_gate as _gate
        from agent_friday.governance import send_grants as _sg
        decided = str(_gate.owner_decision() or "")
        if not decided.startswith("sgr_"):
            return None
        return _sg.verify(decided, "send_email", recipients)
    except Exception as e:
        _log.warning("pre-approval check failed, so the send asks: %s", e)
        return None


#: The line on a send card for a message a cloud model wrote that has private details in it.
CLOUD_SENSITIVE_NOTE = ("Written on a cloud model, and it has private details in it: this is exactly "
                        "what would be sent. Send, or Don't send.")


def ccs_for_note(cc, bcc) -> list:
    return _addresses(cc or []) + _addresses(bcc or [])


def _pre_approval_names(recipients) -> bool:
    """Did the checkpoint find a pre-approval for these recipients (used or not)?"""
    try:
        return _pre_approval(recipients) is not None
    except Exception:
        return False


def _withheld_reason() -> str:
    """Why the checkpoint did not use a pre-approval that names these
    recipients (the call drew on outside content), or ""."""
    try:
        from agent_friday.governance import send_grants as _sg
        return str(_sg.WITHHELD.get() or "")
    except Exception:
        return ""


def request_send(*, to, subject: str, body: str, cc=None, bcc=None,
                 account_id: str | None = None,
                 requested_by: str = "friday", html: str | None = None,
                 thread_id: str | None = None, in_reply_to: str | None = None,
                 references: str | None = None, attachments=None,
                 send_at: str | None = None) -> dict:
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
    # theirs. Guessing between two connected accounts is not a small wrong guess.
    sendable = sendable_accounts()
    if not sendable:
        raise SendRefused(
            "no connected Google account has granted Friday permission to "
            "send. Settings → Connections → Google → Add account, with "
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

    for a in attachments or []:
        _load_attachment(a)                      # present and intact before anyone is asked
    extras = _extras(html, thread_id, in_reply_to, references, attachments,
                     _send_at_iso(send_at))
    fp = message_fingerprint(tos, subject, body, cc, bcc, extras or None)
    live = _live_card(fp)
    if live is not None:
        return {"status": live.get("status"), "approval": live,
                "approval_id": live.get("approval_id")}

    ccs = _addresses(cc or [])
    notes = []
    if extras.get("send_at"):
        from datetime import datetime
        local = datetime.fromisoformat(extras["send_at"]).astimezone()
        notes.append("Scheduled: sent at %s (your time), if Friday is running then."
                     % local.strftime("%a %d %b %H:%M"))
    if extras.get("thread_id"):
        notes.append("This is a reply in an existing conversation.")
    if extras.get("html"):
        notes.append("Formatted text (the plain version is shown below).")
    for a in extras.get("attachments") or []:
        notes.append("Attachment: %s (%s, %d KB)" % (a["filename"], a["mime"], max(1, a["size"] // 1024)))
    try:
        from agent_friday.services import screen_stage as _ss
        notes += _ss.fill_note(body)
    except Exception:
        pass
    withheld = _withheld_reason()
    # Written on a cloud model with private details (privacy/vault_access: cloud_send_card_<tier>):
    # the owner sees exactly what would go and decides on this card, whatever was pre-approved.
    sensitive = bool(_tool_context().get("sensitive_cloud_send"))
    if sensitive:
        notes.append(CLOUD_SENSITIVE_NOTE)
        if not withheld and _pre_approval_names(tos + ccs_for_note(cc, bcc)):
            withheld = ("this message has private details and was written on a cloud model, "
                        "so you see it first")
    if withheld:
        # A reason that names its own subject ("This chat read an outside email, so your
        # pre-approval doesn't cover this send") stands as it is.
        notes.append(withheld + "." if withheld.startswith("This ")
                     else "You pre-approved these recipients, but %s." % withheld)
    note_text = ("\n" + "\n".join(notes)) if notes else ""
    card = dict(
        kind=APPROVAL_KIND,
        subject_type=SUBJECT_TYPE,
        subject_id="%s:%s" % (fp, uuid.uuid4().hex[:8]),
        title="Send email to %s" % ", ".join(
            tos[:3] + (["…"] if len(tos) > 3 else [])),
        action_description=(
            "Send mail as you.\n\nFrom: %s\nTo: %s\nCc: %s\nSubject: %s%s\n\n%s"
            % (account.get("email") or account_id, ", ".join(tos),
               ", ".join(ccs) or "—",
               subject or "(no subject)", note_text, str(body or ""))),
        description="This leaves your machine and arrives as a message from "
                    "you. It cannot be unsent.",
        payload={# The decision hook fires for EVERY external_message card,
                 # including ones other subsystems create. This marker is how
                 # it knows a card is one of ours; without it, approving an
                 # unrelated external message would reach send().
                 "handler": "gmail_send",
                 "fingerprint": fp, "to": tos,
                 "cc": ccs, "bcc": _addresses(bcc or []),
                 "subject": str(subject or ""), "body": str(body or ""),
                 "account_id": account_id,
                 "from_email": account.get("email"),
                 **({"extras": extras} if extras else {})},
        requested_by=requested_by,
    )
    # PRE-APPROVED: the owner's standing yes for these recipients (Settings),
    # which the action checkpoint used for this very call. Re-verified here
    # for the exact message, so a grant revoked a moment ago is not used. The
    # record is created approved by that grant: no card, but the same undo
    # window, burn-once send, outbox and a logged use.
    grant = None if sensitive else _pre_approval(tos + ccs + _addresses(bcc or []))
    if grant is not None:
        appr = _ap.record_pre_approved(grant_id=grant["grant_id"], **card)
        if appr.get("status") == "approved" and not appr.get("consumed"):
            from agent_friday.governance import send_grants as _sg
            ctx = _tool_context()
            _sg.record_use(grant, "send_email", tos + ccs + _addresses(bcc or []),
                           approval_id=appr.get("approval_id"), ctx=ctx)
            unattended = bool(ctx.get("schedule_id") or ctx.get("is_background_task"))
            # Nobody is watching an unattended send: it waits the owner's own Undo window
            # (Settings: unattended_send_undo_s, 2 minutes unless they chose otherwise).
            row = hold(appr, undo_seconds=unattended_undo_seconds() if unattended else None)
            # EVERY covered send tells the owner at once, from chat, voice or
            # a background run alike: no card was raised, so this notice,
            # with its Undo, is how they see it while it can be taken back.
            _notify_pre_approved_hold(appr, row, grant["grant_id"], ctx)
        # The grant id and recipients are in the grant log (send_grants); this
        # line says only that a pre-approval was used.
        _log.info("send to %d recipient(s) went ahead under a pre-approval", len(tos))
        return {"status": appr.get("status"), "approval": appr,
                "approval_id": appr.get("approval_id"), "pre_approved": grant["grant_id"]}
    appr = _ap.create_approval(
        # ALWAYS gate, whatever the policy table has been set to. The default
        # classifies external_message as gated; force_gate means a local
        # override cannot turn mail into an auto-approve.
        force_gate=True, **card)
    _log.info("send requested to %d recipient(s); approval %s (%s)%s",
              len(tos), appr.get("approval_id"), appr.get("status"),
              "; pre-approval not used: " + withheld if withheld else "")
    return {"status": appr.get("status"), "approval": appr,
            "approval_id": appr.get("approval_id"),
            **({"pre_approval_withheld": withheld} if withheld else {})}


def send(approval_id: str) -> dict:
    """Deliver a message the owner approved. The ONLY function that sends.

    Everything it checks, it checks by re-reading the queue — a caller saying
    "this was approved" is not evidence.
    """
    from agent_friday.services import approvals as _ap
    from agent_friday.services import google_accounts as G

    import agent_friday.core as _core_gs
    if _core_gs.GLOBAL_STOP.is_set():
        # Stop everything (services/global_stop): nothing is sent while stopped.
        raise SendRefused("everything is stopped, so nothing was sent. Press Resume first.")
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
    # A message approved by the owner's standing pre-approval, not by a card:
    # the pre-approval must still hold NOW, for every recipient. It may have
    # been revoked during the undo window or before a scheduled time days
    # away; then this message does not go, and its approval is burned.
    if appr.get("pre_approved"):
        from agent_friday.governance import send_grants as _sg
        rcpts = [a for k in ("to", "cc", "bcc") for a in (payload.get(k) or [])]
        if _sg.verify(appr["pre_approved"], "send_email", rcpts) is None:
            _burn(approval_id, "gmail_send_grant_revoked", {"reason": _sg.REVOKED_WHY})
            try:
                _sg._log("refused", grant_id=appr["pre_approved"], by="friday",
                         approval_id=approval_id, reason=_sg.REVOKED_WHY)
            except Exception:
                pass
            _log.info("refused %s: its pre-approval %s no longer holds",
                      approval_id, appr["pre_approved"])
            raise SendRefused(_sg.REVOKED_WHY[:1].upper() + _sg.REVOKED_WHY[1:] + ".")

    expected = payload.get("fingerprint")
    extras = payload.get("extras") or {}
    actual = message_fingerprint(payload.get("to"), payload.get("subject"),
                                 payload.get("body"), payload.get("cc"),
                                 payload.get("bcc"), extras or None)
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

    raw = _mime_raw(payload, extras)
    send_body = {"raw": raw}
    if extras.get("thread_id"):
        send_body["threadId"] = extras["thread_id"]

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
    # The checkpoint's integrity check and signed receipt, as for any tool
    # call. Before the approval is burned: a held send leaves the card usable
    # once whatever held it is fixed.
    from agent_friday.governance import action_gate as _gate
    try:
        _gate.record_external("gmail:send", surface="mail", approval_id=approval_id,
                              target=_fingerprint_target(payload))
    except _gate.Held as e:
        raise SendRefused("held by the action checkpoint (error %s). Nothing was sent."
                          % log_failure(e, "Mail send held by the action checkpoint"),
                          detail="held: %s. Nothing was sent." % e)

    _burned, was_first = _ap._consume(appr)
    if not was_first:
        raise SendRefused("that approval is already being used to send. "
                          "Nothing was sent twice.")

    try:
        from googleapiclient.discovery import build
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        sent = svc.users().messages().send(userId="me", body=send_body).execute()
    except Exception as e:
        _log.warning("send failed: %s", e)
        _outbox_record(appr, ok=False, error=str(e)[:500])
        raise SendRefused("Gmail refused the message (error %s)." % log_failure(e, "Gmail send failed"),
                          detail="Gmail refused the message: %s" % str(e)[:300])

    _ap.mark_used(approval_id, "gmail_send", {"message_id": sent.get("id"),
                                              "thread_id": sent.get("threadId")})
    _outbox_record(appr, ok=True, message_id=sent.get("id"))
    _log.info("sent message %s via approval %s", sent.get("id"), approval_id)
    return {"ok": True, "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": payload.get("to"), "approval_id": approval_id}


def _fingerprint_target(payload: dict) -> str:
    """Who a message goes to, for the receipt, without the addresses."""
    rcpts = [str(a).lower() for k in ("to", "cc", "bcc") for a in (payload.get(k) or [])]
    return "%d recipient(s) %s" % (
        len(rcpts), hashlib.sha256("|".join(sorted(rcpts)).encode()).hexdigest()[:12])


def _mime_raw(payload: dict, extras: dict) -> str:
    """The message as Gmail wants it (base64url RFC 822), for send and drafts."""
    msg = EmailMessage()
    msg["To"] = ", ".join(payload.get("to") or [])
    if payload.get("cc"):
        msg["Cc"] = ", ".join(payload["cc"])
    if payload.get("bcc"):
        msg["Bcc"] = ", ".join(payload["bcc"])
    msg["Subject"] = payload.get("subject") or ""
    if extras.get("in_reply_to"):
        msg["In-Reply-To"] = extras["in_reply_to"]
        msg["References"] = (extras.get("references") or "") + (" " if extras.get("references") else "") + extras["in_reply_to"]
    msg.set_content(payload.get("body") or "")
    if extras.get("html"):
        msg.add_alternative(extras["html"], subtype="html")
    for a in extras.get("attachments") or []:
        data = _load_attachment(a)
        maintype, _, subtype = (a.get("mime") or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype or "application",
                           subtype=subtype or "octet-stream", filename=a.get("filename"))
    # CRLF line endings (RFC 5322, which Gmail's `raw` field asks for); a
    # non-ASCII subject is RFC 2047 encoded and the body declares UTF-8.
    return base64.urlsafe_b64encode(msg.as_bytes(policy=_email_policy.SMTP)).decode("ascii")


# ═══════════════════════════════════════════════════════════════════════════
#  DRAFTS — saved into the owner's own Gmail Drafts; nothing is sent
# ═══════════════════════════════════════════════════════════════════════════

class DraftScopeMissing(SendRefused):
    """The account has not granted gmail.modify, so Gmail will not take a
    draft from Friday. Nothing was saved. `account` is the account's public
    record (id, email, label)."""

    def __init__(self, account: dict, *, google_said: bool = False):
        who = account.get("email") or account.get("label") or account.get("id") or "That account"
        super().__init__(
            "%s has not given Friday permission to save drafts in its Gmail%s, so nothing "
            "was saved and nothing was sent. To allow it: Settings > Connections > Google, "
            "press \"Reconnect with sending\" on %s." % (
                who, " (Google refused the draft for that reason)" if google_said else "", who))
        self.account = account


class DraftNeedsAccount(SendRefused):
    """More than one account and nothing says which: the owner is asked."""

    def __init__(self, message: str, accounts: list):
        super().__init__(message)
        self.accounts = accounts


#: Opens one draft in Gmail on the web. `compose=` takes the draft's MESSAGE id
#: (the hex id Gmail returns in draft.message.id), not the draft id ("r-..."),
#: and /u/<address>/ picks the signed-in account by its address, so the link
#: opens the right mailbox when several are signed in.
DRAFT_LINK = "https://mail.google.com/mail/u/%s/#drafts?compose=%s"
DRAFTS_FOLDER_LINK = "https://mail.google.com/mail/u/%s/#drafts"


def draft_link(account_email: str, message_id: str | None) -> str:
    from urllib.parse import quote
    who = quote(str(account_email or "0"), safe="@")
    if message_id and re.fullmatch(r"[0-9A-Za-z_-]+", str(message_id)):
        return DRAFT_LINK % (who, message_id)
    return DRAFTS_FOLDER_LINK % who


def _google_refused_scope(e) -> bool:
    msg = str(getattr(e, "message", "") or e).lower()
    return getattr(e, "kind", "") == "auth" and ("insufficient" in msg or "scope" in msg
                                                 or "permission" in msg)


def save_draft(*, account_id: str, to=None, subject: str = "", body: str = "", cc=None,
               bcc=None, html: str | None = None, thread_id: str | None = None,
               in_reply_to: str | None = None, references: str | None = None,
               attachments=None) -> dict:
    """Write a draft into the account's Gmail Drafts. Sends nothing, asks
    nothing: the draft sits in the owner's own mailbox, and sending it from Friday
    still goes through request_send and an approval card.

    The account's recorded scopes are checked first (they are what Google
    returned at connect time); if Google itself refuses for lack of a scope,
    that is reported the same way. Either way: DraftScopeMissing, nothing saved."""
    from agent_friday.services import google_accounts as G
    rec = G.get_account(account_id) if account_id else None
    if not rec:
        raise SendRefused("which account should the draft go to?")
    if G.GMAIL_MODIFY not in (rec.get("scopes") or []):
        raise DraftScopeMissing(rec)
    tos = [a for a in _addresses(to or [])] if to else []
    extras = _extras(html, thread_id, in_reply_to, references, attachments)
    raw = _mime_raw({"to": tos, "cc": _addresses(cc or []), "bcc": _addresses(bcc or []),
                     "subject": subject, "body": body}, extras)
    msg = {"raw": raw}
    if extras.get("thread_id"):
        msg["threadId"] = extras["thread_id"]
    creds = G.credentials_for(account_id)
    if not creds:
        raise SendRefused("that account's credentials could not be read, so nothing was saved")
    from googleapiclient.discovery import build
    from agent_friday.services import gmail_api
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    try:
        d = gmail_api.execute(svc.users().drafts().create(userId="me", body={"message": msg}))
    except gmail_api.GmailError as e:
        if _google_refused_scope(e):
            raise DraftScopeMissing(rec, google_said=True) from e
        raise SendRefused("Gmail did not save the draft (error %s). Nothing was saved."
                          % log_failure(e, "Gmail draft failed"),
                          detail="Gmail did not save the draft: %s. Nothing was saved." % e)
    m = (d or {}).get("message") or {}
    return {"ok": True, "draft_id": d.get("id"), "account_id": account_id,
            "message_id": m.get("id"), "thread_id": m.get("threadId") or thread_id,
            "account_email": rec.get("email"), "account_label": rec.get("label"),
            "link": draft_link(rec.get("email"), m.get("id"))}


# ── Drafts the model asks for (the draft_email tool) ───────────────────────────

def wants_send(args) -> bool:
    """Did this draft_email call ask to SEND? Only an explicit yes counts:
    anything else saves a draft into the owner's own Gmail and sends nothing."""
    v = (args or {}).get("send") if isinstance(args, dict) else None
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("true", "yes", "1")


def _norm(x) -> str:
    return str(x or "").strip().lower()


def _account_public(r: dict) -> dict:
    from agent_friday.services import google_accounts as G
    return {"id": r.get("id"), "email": r.get("email"), "label": r.get("label"),
            "can_save_drafts": G.GMAIL_MODIFY in (r.get("scopes") or [])}


def mail_accounts() -> list:
    """Connected accounts with Gmail switched on, as public records."""
    from agent_friday.services import google_accounts as G
    try:
        recs = G.list_accounts()
    except Exception:
        recs = []
    return [r for r in recs if (r.get("services") or {}).get("gmail", True)]


def match_account(want, accounts: list) -> dict | None:
    """An account by its id, address or label (case-insensitive), or None."""
    w = _norm(want)
    if not w:
        return None
    for key in ("id", "email", "label"):
        hit = [a for a in accounts if _norm(a.get(key)) == w]
        if len(hit) == 1:
            return hit[0]
    return None


def default_account_id() -> str:
    """The owner's default account for mail, if they set one (settings
    `mail_default_account`: an id, address or label); "" when unset."""
    try:
        from agent_friday.services import owner_choices as _oc
        return _oc.mail_default_account()
    except Exception:
        return ""


def _who(a: dict) -> str:
    em, lab = a.get("email") or a.get("id"), a.get("label")
    return "%s (%s)" % (em, lab) if lab and _norm(lab) != _norm(em) else str(em)


def choose_account(want=None, thread_account: str | None = None) -> tuple:
    """(account record, how it was chosen) for a draft. Order: the account
    named > the account the replied-to conversation is in > the owner's
    default > the only account. Several accounts and none of those: raises
    DraftNeedsAccount listing them; never a guess."""
    accounts = mail_accounts()
    if not accounts:
        raise SendRefused("no Google account with Gmail is connected, so no draft could be saved. "
                          "Connect one in Settings > Connections > Google. Nothing was saved.")
    if _norm(want):
        hit = match_account(want, accounts)
        if hit is None:
            raise DraftNeedsAccount(
                "No connected account matches %r. Connected: %s. Ask which one; nothing was saved."
                % (str(want), "; ".join(_who(a) for a in accounts)), [_account_public(a) for a in accounts])
        return hit, "the account you named"
    if thread_account:
        hit = match_account(thread_account, accounts)
        if hit is not None:
            return hit, "the account the conversation is in"
    dflt = default_account_id()
    if dflt:
        hit = match_account(dflt, accounts)
        if hit is not None:
            return hit, "your default account"
    if len(accounts) == 1:
        return accounts[0], "your only connected account"
    raise DraftNeedsAccount(
        "Which account should the draft go to: %s? Nothing was saved yet."
        % " or ".join(_who(a) for a in accounts), [_account_public(a) for a in accounts])


def _bare(header_value) -> list:
    """Addresses only, from a header value like 'Pat <pat@x.org>, b@y.org'."""
    from email.utils import getaddresses
    out = []
    for _name, addr in getaddresses([str(header_value or "")]):
        addr = addr.strip()
        if addr and _ADDR.match(addr) and _norm(addr) not in {_norm(a) for a in out}:
            out.append(addr)
    return out


def reply_context(thread, account_hint: str | None = None, reply_all: bool = False,
                  own_addresses=()) -> dict:
    """What a reply to a conversation needs, from its LAST message: the
    thread id, the account it is in, In-Reply-To and References, a "Re:"
    subject, and the recipients (the sender, or Reply-To; with reply_all,
    everyone else on To and Cc too; the owner's own addresses never). If the
    last message is the owner's own, the reply goes to whom that one went to.
    `thread`: a search_email ref ("mail:<account>:<thread>") or a thread id.
    Quotes nothing."""
    from agent_friday.services import gmail_read
    from agent_friday.services.screen_stage import split_mail_ref
    t = str(thread or "").strip()
    try:
        from agent_friday.services import mail_thread as _mt
        if t.lower() in _mt.DEICTIC:
            ref, how = _mt.resolve_deictic()
            if not ref:
                raise SendRefused(how + " Nothing was saved.")
            t = ref
    except SendRefused:
        raise
    except Exception:
        pass
    pair = split_mail_ref(t)
    account, tid = pair if pair else (account_hint, t)
    if not tid:
        raise SendRefused("that is not a conversation reference. Nothing was saved.")
    res = gmail_read.get_thread(tid, account or None)
    if res.get("status") != "ok" or not res.get("messages"):
        raise SendRefused("the conversation to reply to could not be read (%s). Nothing was saved."
                          % (res.get("error") or res.get("kind") or "empty"))
    last = res["messages"][-1]
    mine = {_norm(a) for a in own_addresses if a}
    try:
        from agent_friday.services import google_accounts as G
        rec = G.get_account(res.get("account_id")) or {}
        if rec.get("email"):
            mine.add(_norm(rec["email"]))
    except Exception:
        pass
    sender = _bare(last.get("sender"))
    if sender and _norm(sender[0]) in mine:
        to = [a for a in _bare(last.get("to")) if _norm(a) not in mine]
        cc = [a for a in _bare(last.get("cc")) if _norm(a) not in mine] if reply_all else []
    else:
        to = _bare(last.get("reply_to")) or sender
        cc = []
        if reply_all:
            seen = {_norm(a) for a in to} | mine
            for a in _bare(last.get("to")) + _bare(last.get("cc")):
                if _norm(a) not in seen:
                    seen.add(_norm(a))
                    cc.append(a)
    subj = str(last.get("subject") or "").strip()
    if subj == "(no subject)":
        subj = ""
    if not re.match(r"(?i)^re\s*:", subj):
        subj = ("Re: " + subj).strip()
    mid = str(last.get("message_id_header") or "").strip()
    return {"account_id": res.get("account_id"), "thread_id": tid, "to": to, "cc": cc,
            "subject": subj, "in_reply_to": mid or None,
            "references": str(last.get("references") or "").strip() or None}


# ═══════════════════════════════════════════════════════════════════════════
#  HELD — approved, waiting out the undo window or the scheduled time
# ═══════════════════════════════════════════════════════════════════════════
import threading as _threading
import time as _time

_HELD_LOCK = _threading.RLock()
_WAKE = _threading.Event()


def _held_path():
    from agent_friday.core import FRIDAY_DIR
    d = FRIDAY_DIR / "mail_outbox"
    d.mkdir(parents=True, exist_ok=True)
    return d / "held.json"


def _load_held() -> list:
    try:
        return json.loads(_held_path().read_text(encoding="utf-8")) or []
    except FileNotFoundError:
        return []
    except Exception as e:
        _log.warning("held queue unreadable: %s", e)
        return []


def _save_held(rows: list) -> None:
    p = _held_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    tmp.replace(p)


def _ts(iso):
    if not iso:
        return 0.0
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


def unattended_undo_seconds() -> int:
    """The Undo window for a send nobody is watching (a scheduled job or background task under a
    pre-approval): the owner's setting, 10 s to 10 min (owner_choices.unattended_undo_seconds)."""
    try:
        from agent_friday.services import owner_choices as _oc
        return _oc.unattended_undo_seconds()
    except Exception:
        return 120


def _words_for_seconds(s: int) -> str:
    s = int(s)
    if s < 60:
        return "%d seconds" % s
    m, r = divmod(s, 60)
    out = "%d minute%s" % (m, "" if m == 1 else "s")
    return out + (" %d seconds" % r if r else "")


def hold(record: dict, now: float | None = None, undo_seconds: int | None = None) -> dict:
    """Queue an approved message: it goes after the undo window (UNDO_SECONDS,
    or `undo_seconds` for an unattended send), or at its scheduled time,
    whichever is later."""
    now = _time.time() if now is None else now
    payload = record.get("payload") or {}
    extras = payload.get("extras") or {}
    window = UNDO_SECONDS if undo_seconds is None else int(undo_seconds)
    due = max(now + window, _ts(extras.get("send_at")))
    row = {"approval_id": record.get("approval_id"), "due": due, "held_at": now,
           "undo_seconds": window,
           "scheduled": bool(extras.get("send_at")),
           "to": payload.get("to"), "subject": payload.get("subject"),
           "from": payload.get("from_email"),
           # Which standing pre-approval let it go without a card, if any:
           # revoking that grant takes this message back (cancel_for_grant).
           "pre_approved": record.get("pre_approved") or None}
    with _HELD_LOCK:
        rows = [r for r in _load_held() if r.get("approval_id") != row["approval_id"]]
        rows.append(row)
        _save_held(rows)
    _WAKE.set()
    return row


def held() -> list:
    """What is waiting to go, soonest first, with seconds left."""
    now = _time.time()
    with _HELD_LOCK:
        rows = sorted(_load_held(), key=lambda r: r.get("due") or 0)
    return [dict(r, seconds_left=max(0, round(r["due"] - now))) for r in rows]


def _burn(approval_id: str, why: str, detail: dict) -> None:
    from agent_friday.services import approvals as _ap
    appr = _ap.get_approval(approval_id)
    if not appr:
        return
    _ap._consume(appr)
    _ap.mark_used(approval_id, why, detail)
    _outbox_record(appr, ok=False, error=detail.get("reason") or why)


def cancel(approval_id: str) -> dict:
    """Undo send: take a held message back before it goes. The approval is
    burned, so this message can never be sent by that decision."""
    with _HELD_LOCK:
        rows = _load_held()
        mine = [r for r in rows if r.get("approval_id") == approval_id]
        if not mine:
            raise SendRefused("that message is not waiting to be sent (it has already gone, "
                              "or was never approved)")
        _save_held([r for r in rows if r.get("approval_id") != approval_id])
    _burn(approval_id, "gmail_send_cancelled", {"reason": "cancelled by you before it was sent"})
    return {"ok": True, "approval_id": approval_id, "sent": False}


def _pre_approved_who(ctx) -> str:
    sc = ctx or {}
    if sc.get("schedule_id"):
        return "A scheduled job"
    if sc.get("is_background_task") or sc.get("task_id"):
        return "A background task"
    if sc.get("is_voice") or str(sc.get("surface") or "").startswith("voice"):
        return "Friday, from your voice conversation,"
    return "Friday, from your chat,"


def _notify_pre_approved_hold(appr: dict, row: dict, grant_id: str, ctx=None) -> None:
    """A message was queued under the owner's pre-approval (from any surface):
    no card was raised, so tell them now, while it can still be taken back.
    The Undo opens the Approvals card, where the waiting message has its Undo
    (POST /api/mail/held/<id>/cancel)."""
    from datetime import datetime
    payload = appr.get("payload") or {}
    aid = appr.get("approval_id")
    due = row.get("due") or _time.time()
    when = datetime.fromtimestamp(due).strftime("%a %H:%M:%S")
    left = max(0, int(round(due - _time.time())))
    _notify("Email going out under your pre-approval",
            "%s is sending your message to %s (\"%s\") at %s: you have %s to undo it. No card was "
            "needed: you pre-approved these recipients (%s). Press Undo here or on the Approvals "
            "card before then, or revoke the pre-approval in Settings. (The undo window for "
            "unattended sends is yours to set: Settings > Privacy & Data > Pre-approved sends.)"
            % (_pre_approved_who(ctx),
               ", ".join(payload.get("to") or []), payload.get("subject") or "", when,
               _words_for_seconds(left), grant_id),
            priority="medium", kind="info", key="preapproved:%s" % aid,
            target={"workspace": "system", "tab": "approvals"},
            actions=[{"label": "Undo", "workspace": "system", "tab": "approvals",
                      "method": "POST", "url": "/api/mail/held/%s/cancel" % aid}],
            meta={"undo_until": due, "undo_seconds": row.get("undo_seconds")})


def cancel_for_grant(grant_id: str) -> list:
    """The owner revoked a pre-approval: take back every message still waiting
    under it. Each one's approval is burned and recorded in the outbox as not
    sent, and the owner is told. Returns the approval ids taken back."""
    from agent_friday.services import approvals as _ap
    from agent_friday.governance import send_grants as _sg
    gid = str(grant_id or "")
    if not gid:
        return []
    with _HELD_LOCK:
        rows = _load_held()
        mine = [r for r in rows
                if (r.get("pre_approved") or
                    (_ap.get_approval(r.get("approval_id") or "") or {}).get("pre_approved")) == gid]
        if not mine:
            return []
        ids = {r.get("approval_id") for r in mine}
        _save_held([r for r in rows if r.get("approval_id") not in ids])
    for r in mine:
        _burn(r["approval_id"], "gmail_send_grant_revoked", {"reason": _sg.REVOKED_WHY})
        _notify("Email NOT sent",
                "Your message to %s (\"%s\") was taken back: %s."
                % (", ".join(r.get("to") or []), r.get("subject") or "", _sg.REVOKED_WHY),
                priority="high", kind="warning")
    return [r["approval_id"] for r in mine]


def _deliver(approval_id: str, to) -> None:
    try:
        result = send(approval_id)
        from agent_friday.services import approvals as _ap
        grant = ((_ap.get_approval(approval_id) or {}).get("pre_approved") or "")
        _notify("Email sent" + (" under your pre-approval" if grant else ""),
                "Your message to %s is on its way.%s" % (
                    ", ".join(to or []),
                    (" No card was needed: you pre-approved these recipients (%s). "
                     "Revoke it in Settings or on the Approvals card." % grant) if grant else ""),
                priority="low", kind="info")
        _log.info("held message sent %s", result.get("message_id"))
    except SendRefused as e:
        # The owner pressed approve and reasonably believes the message went.
        _notify("Email NOT sent", str(e), priority="high", kind="warning")
    except Exception as e:
        _log.warning("held send crashed: %s", e)
        _notify("Email NOT sent", "Something went wrong sending it: %s" % str(e)[:200],
                priority="high", kind="warning")


def _tick(now: float | None = None) -> list:
    """Send what is due. A message due more than LATE_LIMIT_S ago (Friday was
    off) is not sent: its approval is burned and the owner is told."""
    now = _time.time() if now is None else now
    try:
        # Stop everything (services/global_stop): nothing is sent while stopped; the
        # rows stay held and are judged again after Resume.
        import agent_friday.core as _core_gs
        if _core_gs.GLOBAL_STOP.is_set():
            return []
    except Exception:
        pass
    with _HELD_LOCK:
        rows = _load_held()
        due = [r for r in rows if (r.get("due") or 0) <= now]
        if not due:
            return []
        _save_held([r for r in rows if (r.get("due") or 0) > now])
    done = []
    for r in due:
        if now - r["due"] > LATE_LIMIT_S:
            _burn(r["approval_id"], "gmail_send_missed",
                  {"reason": "not sent: Friday was not running when it was due"})
            _notify("Email NOT sent",
                    "Your %s message to %s (\"%s\") was due while Friday was off, so it was not "
                    "sent late. Ask again if you still want it to go."
                    % ("scheduled" if r.get("scheduled") else "approved",
                       ", ".join(r.get("to") or []), r.get("subject") or ""),
                    priority="high", kind="warning")
            done.append((r["approval_id"], "missed"))
            continue
        _deliver(r["approval_id"], r.get("to"))
        done.append((r["approval_id"], "sent"))
    return done


def _scheduler_loop():
    while True:
        try:
            _tick()
            with _HELD_LOCK:
                nxt = min([r.get("due") or 0 for r in _load_held()] or [_time.time() + 30])
        except Exception as e:
            _log.warning("held-mail scheduler: %s", e)
            nxt = _time.time() + 5
        _WAKE.wait(timeout=max(0.2, min(30.0, nxt - _time.time())))
        _WAKE.clear()


def start_scheduler() -> None:
    """Idempotent. Not started under tests (FRIDAY_TESTING), which call _tick."""
    import os
    global _SCHED_STARTED
    if _SCHED_STARTED or os.environ.get("FRIDAY_TESTING"):
        return
    _SCHED_STARTED = True
    _threading.Thread(target=_scheduler_loop, name="gmail-held", daemon=True).start()


_SCHED_STARTED = False


# ═══════════════════════════════════════════════════════════════════════════
#  OUTBOX — every attempt, in one append-only place the owner can read
# ═══════════════════════════════════════════════════════════════════════════
#
# The approval queue records the DECISION. It does not record the outcome,
# and the two come apart in the only way that matters: an approved card whose
# send failed looks, in the queue, exactly like an approved card whose send
# worked. For anything else that would be a reporting nicety. For mail it is
# the difference between "the owner thinks it went" and "it went".

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
    # Approval starts the undo window (or waits for the scheduled time);
    # the held-mail scheduler sends it when that is over. See HELD above.
    try:
        hold(record)
    except Exception as e:
        _log.warning("could not hold the approved message: %s", e)
        _notify("Email NOT sent", "Friday could not queue it: %s" % str(e)[:200],
                priority="high", kind="warning")


def _notify(title: str, body: str, *, priority: str = "medium",
            kind: str = "info", actions=None, target=None, key: str = "", meta=None) -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body, priority=priority, kind=kind,
                source="gmail_send", emitter="E43", actions=actions, target=target,
                dedupe_key="gmail_send:%s" % key if key else "gmail_send:%s:%s" % (kind, priority),
                **({"meta": meta} if meta else {}))
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
        start_scheduler()
    except Exception as e:
        _log.warning("could not register the send hook: %s", e)


_HOOKS_REGISTERED = False
register_hooks()
