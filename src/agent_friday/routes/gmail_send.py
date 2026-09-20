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
