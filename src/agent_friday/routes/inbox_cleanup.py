"""routes/inbox_cleanup.py -- the inbox clean-up board (first-run spec, B3).

    GET  /api/inbox-cleanup               the board: scan state, the batch card, the found ledger
    POST /api/inbox-cleanup/start         read headers from {source: {kind: gmail, account_id} | {kind: mbox, path}}
    POST /api/inbox-cleanup/unsubscribe   {senders: [...], requested_by}

The owner's own tick in Friday's page (requested_by "ui:<surface>") is the
approval and acts at once, one request per sender, each with a receipt.
Anything else, including a request that does not say who made it, is Friday
asking, and becomes an approval card that lists every sender; nothing is
sent until that card is approved.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from agent_friday.routes._errors import api_error
from agent_friday.services import inbox_cleanup as ic

inbox_cleanup_bp = Blueprint("inbox_cleanup", __name__)

# The approval hook must exist on every boot: route modules are what the
# server imports unconditionally.
ic.register_hooks()


@inbox_cleanup_bp.before_request
def _gate():
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "the inbox clean-up is only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "the inbox clean-up can only be used on Friday's own computer"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    if request.method == "POST":
        if not request.is_json:
            return jsonify({"error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"error": "cross-origin request refused"}), 403
    return None


def _is_owner(actor) -> bool:
    from agent_friday.routes.messages import _is_owner as owner
    return owner(actor)


@inbox_cleanup_bp.route("/api/inbox-cleanup", methods=["GET"])
def inbox_cleanup_view():
    return jsonify({"ok": True, **ic.view()})


@inbox_cleanup_bp.route("/api/inbox-cleanup/start", methods=["POST"])
def inbox_cleanup_start():
    b = request.get_json(silent=True) or {}
    src = b.get("source") if isinstance(b.get("source"), dict) else {}
    if not _is_owner(b.get("requested_by")):
        return jsonify({"ok": False, "error": "owner_only",
                        "message": "Reading your mail for the clean-up starts from your own click."}), 403
    try:
        return jsonify({"ok": True, **ic.start({k: str(v)[:1000] for k, v in src.items()})})
    except ValueError as e:
        return api_error(e, "Couldn't start the inbox clean-up", 400, shape="ok", key="message",
                         error="bad_source")


@inbox_cleanup_bp.route("/api/inbox-cleanup/unsubscribe", methods=["POST"])
def inbox_cleanup_unsubscribe():
    b = request.get_json(silent=True) or {}
    senders = [str(s)[:120] for s in (b.get("senders") or []) if str(s).strip()][:200]
    if not senders:
        return jsonify({"ok": False, "error": "nothing_ticked", "message": "Tick at least one sender."}), 400
    if not _is_owner(b.get("requested_by")):
        out = ic.propose(senders, requested_by=str(b.get("requested_by") or "friday"),
                         reason=str(b.get("reason") or ""))
        return jsonify({"ok": out.get("status") != "error", **out}), 202
    return jsonify({"ok": True, **ic.unsubscribe(senders, decided_by="owner click"),
                    "board": ic.view()})
