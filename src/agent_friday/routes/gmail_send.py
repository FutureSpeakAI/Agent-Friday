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
        "how": ("Settings → Accounts & Keys → Google → Add account, with "
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
