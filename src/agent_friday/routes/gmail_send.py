"""routes/gmail_send.py — the HTTP surface for sending mail.

Endpoints (all under /api/mail):
    GET  /can-send      which connected accounts may send, if any
    GET  /outbox        recent send attempts — successes AND failures
    POST /request       queue a message for the owner's approval (sends nothing)
    POST /send          send a message the owner already approved

WHY THIS MODULE EXISTS AT ALL, beyond the three endpoints.

services/gmail_send.py registers the approval-decision hook at import. A
service nobody imports registers nothing, and the failure would be silent in
the worst possible way: the owner presses approve on a card that says "Send
email to X", the card flips to approved, and no message is ever sent. Route
modules are the one thing server.py imports unconditionally at startup
(_discover_and_register_blueprints walks the whole package), so importing the
service here is what makes the hook exist on every boot.

There is no DELETE/unsend. Gmail has no such thing, and offering a button that
cannot do what it says is worse than not offering it.
"""
from flask import Blueprint, jsonify, request

# Imported for its side effect as much as its functions — see the docstring.
from agent_friday.services import gmail_send as _gs
# The same reason: its approval hook carries out mail changes Friday proposed
# and the owner approved.
from agent_friday.services import mail_proposals as _mp  # noqa: F401

gmail_send_bp = Blueprint("gmail_send", __name__)


@gmail_send_bp.route("/api/mail/can-send", methods=["GET"])
def mail_can_send():
    """What the UI needs to decide whether to offer sending at all.

    Reports the scopes Google actually granted, not the ones Friday asked
    for — a connect where the send permission was declined must read here as
    "cannot send".
    """
    accounts = _gs.sendable_accounts()
    return jsonify({
        "status": "ok",
        "can_send": bool(accounts),
        "accounts": accounts,
        "how": ("Settings → Connectors → Google → Add account, with "
                "\"allow sending\" ticked. Each message still waits for your "
                "approval."),
    })


@gmail_send_bp.route("/api/mail/outbox", methods=["GET"])
def mail_outbox():
    """Every send attempt, including the ones that failed.

    The approval queue records the decision; it cannot tell you whether the
    message left. For mail that gap is the whole problem, so the outbox is a
    first-class read rather than a log file.
    """
    try:
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    return jsonify({"status": "ok", "sent": _gs.outbox(limit=limit)})


def _attachments_for(body):
    """Uploaded files (by the refs /api/mail/attachment returned) plus, for a
    forward, the original message's attachments, fetched read-only now so
    the card and the fingerprint cover the exact bytes."""
    out = [a for a in (body.get("attachments") or []) if isinstance(a, dict)]
    for f in body.get("forward_attachments") or []:
        from agent_friday.services import gmail_read
        data = gmail_read.get_attachment(f.get("account_id") or "", f.get("message_id") or "",
                                         f.get("attachment_id") or "")
        out.append(_gs.store_attachment(data, f.get("filename") or "attachment", f.get("mime") or ""))
    return out or None


_CONTACTS = {"at": 0.0, "list": [], "errors": []}


@gmail_send_bp.route("/api/mail/contacts")
def mail_contacts():
    """Address autocomplete for compose: Google contacts (read once and kept
    for 10 minutes, so typing does not spend People API quota) plus the
    people in recently loaded mail. Read-only."""
    import time
    q = (request.args.get("q") or "").strip().lower()
    if time.time() - _CONTACTS["at"] > 600:
        try:
            from agent_friday.services import google_accounts as ga
            res = ga.search_contacts("", max_results=1000)
            _CONTACTS.update(at=time.time(), list=res.get("contacts") or [], errors=res.get("errors") or [])
        except Exception as e:
            _CONTACTS.update(at=time.time(), errors=[{"error": str(e)}])
    seen, out = set(), []

    def add(name, addr, source):
        addr = (addr or "").strip().lower()
        if not addr or addr in seen:
            return
        if q and q not in addr and q not in (name or "").lower():
            return
        seen.add(addr)
        out.append({"name": name or "", "email": addr, "source": source})
    for c in _CONTACTS["list"]:
        for e in c.get("emails") or ([c.get("email")] if c.get("email") else []):
            add(c.get("name"), e if isinstance(e, str) else (e or {}).get("value"), "contacts")
    try:
        from agent_friday.services import message_triage as mt
        for slot in list(mt._collect_cache.values()):
            for m in (slot.get("result") or {}).get("messages") or []:
                add(m.get("sender"), m.get("sender_email"), "recent mail")
    except Exception:
        pass
    return jsonify({"status": "ok", "contacts": out[:20],
                    "errors": _CONTACTS["errors"]})


def _mailbox_call(fn):
    from agent_friday.services import gmail_mailbox as gm
    try:
        return jsonify({"status": "ok", **fn(gm)})
    except gm.NotPermitted as e:
        return jsonify({"status": "not_permitted", "message": str(e) + ". Use Reconnect with sending in Settings."}), 403
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        from agent_friday.services import gmail_api
        d = gmail_api.describe(e)
        return jsonify({"status": "error", "kind": d.get("kind"), "message": d.get("message") or str(e)}), 502


@gmail_send_bp.route("/api/mail/labels", methods=["GET", "POST"])
def mail_labels():
    """GET ?account= : the account's own Gmail labels. POST {account_id, name}: create one."""
    if request.method == "GET":
        aid = (request.args.get("account") or "").strip()
        return _mailbox_call(lambda gm: {"labels": gm.list_labels(aid)})
    body = request.get_json(silent=True) or {}
    return _mailbox_call(lambda gm: {"label": gm.create_label((body.get("account_id") or "").strip(), body.get("name") or "")})


@gmail_send_bp.route("/api/mail/modify", methods=["POST"])
def mail_modify():
    """Add/remove labels on conversations in Gmail. {account_id, thread_ids, add, remove}.
    Returns `changed` (exactly what was different), which /api/mail/modify/undo reverses."""
    b = request.get_json(silent=True) or {}
    return _mailbox_call(lambda gm: gm.modify_threads((b.get("account_id") or "").strip(),
                                                        b.get("thread_ids") or [], b.get("add") or [], b.get("remove") or []))


@gmail_send_bp.route("/api/mail/modify/undo", methods=["POST"])
def mail_modify_undo():
    b = request.get_json(silent=True) or {}
    return _mailbox_call(lambda gm: gm.undo((b.get("account_id") or "").strip(), b.get("changed") or {}))


@gmail_send_bp.route("/api/mail/signature")
def mail_signature():
    """?account= : the account's own Gmail signature (read-only permission is
    enough), so a message written in Friday ends the way one written in Gmail does."""
    aid = (request.args.get("account") or "").strip()

    def read(gm):
        from agent_friday.services import gmail_api
        svc = gm._read_svc(aid)
        d = gmail_api.execute(svc.users().settings().sendAs().list(userId="me"))
        rows = d.get("sendAs") or []
        pick = next((r for r in rows if r.get("isDefault")), None) or next((r for r in rows if r.get("isPrimary")), None) or {}
        return {"signature": pick.get("signature") or "", "email": pick.get("sendAsEmail") or "",
                "name": pick.get("displayName") or ""}
    return _mailbox_call(read)


@gmail_send_bp.route("/api/mail/original")
def mail_original():
    """?account=&message= : the message exactly as Gmail holds it (headers and
    MIME source), for "Show original" and printing headers. ?dl=1 downloads it
    as a .eml file."""
    from flask import Response
    from agent_friday.services import gmail_read
    aid = (request.args.get("account") or "").strip()
    mid = (request.args.get("message") or "").strip()
    if not aid or not mid:
        return jsonify({"status": "error", "message": "account and message are required"}), 400
    try:
        raw = gmail_read.get_raw(aid, mid)
    except Exception as e:
        from agent_friday.services import gmail_api
        d = gmail_api.describe(e)
        return jsonify({"status": "error", "kind": d.get("kind"), "message": d.get("message") or str(e)}), 502
    if request.args.get("dl"):
        resp = Response(raw, mimetype="message/rfc822")
        resp.headers["Content-Disposition"] = 'attachment; filename="message-%s.eml"' % "".join(c for c in mid if c.isalnum())[:40]
        return resp
    from email.parser import BytesHeaderParser
    hdr = BytesHeaderParser().parsebytes(raw)
    cap = 2 * 1024 * 1024
    return jsonify({"status": "ok", "size": len(raw), "truncated": len(raw) > cap,
                    "headers": [[k, str(v)] for k, v in hdr.items()],
                    "source": raw[:cap].decode("utf-8", "replace")})


@gmail_send_bp.route("/api/mail/unsubscribe", methods=["GET", "POST"])
def mail_unsubscribe():
    """GET ?account=&message= : how this list says to leave it (for the
    confirm dialog). POST {account_id, message_id, confirmed}: leave it.
    The owner's own confirmed click acts; a request from Friday itself
    becomes an approval card."""
    from agent_friday.services import mail_unsubscribe as mu
    if request.method == "GET":
        aid = (request.args.get("account") or "").strip()
        mid = (request.args.get("message") or "").strip()
        return _mailbox_call(lambda gm: mu.options(aid, mid))
    b = request.get_json(silent=True) or {}
    aid, mid = (b.get("account_id") or "").strip(), (b.get("message_id") or "").strip()
    if not aid or not mid:
        return jsonify({"status": "error", "message": "account_id and message_id are required"}), 400
    from agent_friday.routes.messages import _is_owner
    if not _is_owner(b.get("requested_by")):
        return jsonify(_mp.propose("unsubscribe", [mid], [], requested_by=str(b.get("requested_by")),
                                   reason=str(b.get("reason") or ""),
                                   extra={"account_id": aid, "message_id": mid, "sender": str(b.get("sender") or "")})), 202
    if not b.get("confirmed"):
        return jsonify({"status": "error", "message": "Unsubscribing tells the sender this address is read; confirm it first."}), 400
    return _mailbox_call(lambda gm: mu.unsubscribe(aid, mid))


@gmail_send_bp.route("/api/mail/held")
def mail_held():
    """Approved messages waiting out the undo window or their scheduled time."""
    return jsonify({"status": "ok", "held": _gs.held(), "undo_seconds": _gs.UNDO_SECONDS})


@gmail_send_bp.route("/api/mail/held/<approval_id>/cancel", methods=["POST"])
def mail_held_cancel(approval_id):
    """Undo send. The message does not go, and its approval cannot be reused."""
    try:
        return jsonify({"status": "ok", **_gs.cancel(approval_id)})
    except _gs.SendRefused as e:
        return jsonify({"status": "refused", "message": str(e)}), 409


@gmail_send_bp.route("/api/mail/draft", methods=["POST"])
def mail_draft():
    """Save a draft into the account's Gmail Drafts. Sends nothing."""
    body = request.get_json(silent=True) or {}
    try:
        out = _gs.save_draft(
            account_id=(body.get("account_id") or "").strip(),
            to=body.get("to") or None, subject=body.get("subject") or "",
            body=body.get("body") or "", cc=body.get("cc") or None, bcc=body.get("bcc") or None,
            html=body.get("html") or None, thread_id=body.get("thread_id") or None,
            in_reply_to=body.get("in_reply_to") or None, references=body.get("references") or None,
            attachments=_attachments_for(body))
        return jsonify({"status": "ok", **out})
    except _gs.SendRefused as e:
        return jsonify({"status": "refused", "message": str(e)}), 400


@gmail_send_bp.route("/api/mail/attachment", methods=["POST"])
def mail_attachment_upload():
    """Store a file to attach to a message that is about to be requested.
    Sends nothing and asks nothing; the approval card comes later."""
    f = request.files.get("file")
    if not f:
        return jsonify({"status": "error", "message": "no file"}), 400
    try:
        meta = _gs.store_attachment(f.read(), f.filename or "attachment", f.mimetype or "")
    except _gs.SendRefused as e:
        return jsonify({"status": "refused", "message": str(e)}), 400
    return jsonify({"status": "ok", **meta})


@gmail_send_bp.route("/api/mail/request", methods=["POST"])
def mail_request():
    """Queue a message for approval. NEVER sends, whatever the body says."""
    body = request.get_json(silent=True) or {}
    try:
        result = _gs.request_send(
            to=body.get("to"),
            subject=body.get("subject") or "",
            body=body.get("body") or "",
            cc=body.get("cc"), bcc=body.get("bcc"),
            account_id=(body.get("account_id") or "").strip() or None,
            requested_by=(body.get("requested_by") or "ui"),
            html=body.get("html") or None,
            thread_id=body.get("thread_id") or None,
            in_reply_to=body.get("in_reply_to") or None,
            references=body.get("references") or None,
            attachments=_attachments_for(body),
            send_at=body.get("send_at") or None,
        )
    except _gs.SendRefused as e:
        return jsonify({"status": "refused", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok", "approval_id": result.get("approval_id"),
                    "approval_status": result.get("status"),
                    "message": "Waiting for your approval. Nothing has been "
                               "sent yet."})


@gmail_send_bp.route("/api/mail/send", methods=["POST"])
def mail_send():
    """Send a message the owner approved.

    Normally redundant — approving the card fires the send hook. This exists
    for the case where the hook could not run (the server restarted between
    the decision and the send), and it is safe to call anyway: send() re-reads
    the approval and refuses a spent one, so a duplicate press cannot produce
    a duplicate message.
    """
    body = request.get_json(silent=True) or {}
    approval_id = (body.get("approval_id") or "").strip()
    if not approval_id:
        return jsonify({"status": "error",
                        "message": "approval_id is required"}), 400
    try:
        result = _gs.send(approval_id)
    except _gs.SendRefused as e:
        return jsonify({"status": "refused", "message": str(e)}), 409
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok", **result})
