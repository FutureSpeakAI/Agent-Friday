"""routes/privacy_map.py -- the privacy map (north star §26.22, BD-15 slice P1).

    GET /api/privacy/map                 what Friday holds, where, how long; what left and to whom
    GET /api/privacy/map/left?to=<id>    the egress log's own rows for one destination (its receipts)

Read-only, and only for the person at this PC: the same gate as the Locks
page (the local owner principal, a local request). Nothing here deletes,
exports or sends; the map links to the controls that do.
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from agent_friday.services import privacy_map

privacy_map_bp = Blueprint("privacy_map", __name__)


@privacy_map_bp.before_request
def _gate():
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "the privacy map is only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "the privacy map can only be read on Friday's own computer"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    from agent_friday.services import screen_click
    if screen_click.is_cross_site(request):
        return jsonify({"error": "cross-site request refused"}), 403
    return None


@privacy_map_bp.route("/api/privacy/map", methods=["GET"])
def privacy_map_view():
    return jsonify({"ok": True, **privacy_map.view()})


@privacy_map_bp.route("/api/privacy/map/left", methods=["GET"])
def privacy_map_left():
    to = str(request.args.get("to") or "")[:80]
    if not to:
        return jsonify({"ok": False, "error": "say which destination: ?to=<name>"}), 400
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    return jsonify({"ok": True, **privacy_map.left_rows(to, limit)})


@privacy_map_bp.route("/api/privacy/map/sent/<sha>", methods=["GET"])
def privacy_map_sent(sha):
    """The exact text of one send, when the owner chose to keep it. Shown only
    to a click in Friday's own window: the words are the most sensitive thing
    the map can open."""
    from agent_friday.services import screen_click
    if not screen_click.screen_session(request):
        return jsonify({"ok": False, "message": "This opens only from the privacy map in Friday's window."}), 403
    data = privacy_map.sent_text(str(sha or "")[:64])
    if not data:
        return jsonify({"ok": False, "message": "That text was not kept (or its time is up)."}), 404
    return jsonify({"ok": True, "sha": sha, **data})


@privacy_map_bp.route("/api/privacy/sent-text", methods=["POST"])
def privacy_sent_text_choice():
    """Keep the exact text of sends, or stop (which deletes what was kept).
    The owner's click in Friday's own window only."""
    from agent_friday.services import screen_click, sent_text_store
    if not request.is_json:
        return jsonify({"ok": False, "message": "JSON body required"}), 415
    if not screen_click.screen_session(request):
        return jsonify({"ok": False, "message": "This changes only from the privacy map in Friday's window."}), 403
    body = request.get_json(silent=True) or {}
    keep = body.get("keep")
    if not isinstance(keep, bool):
        return jsonify({"ok": False, "message": "say keep: true or false"}), 400
    days = body.get("days")
    if days is not None and (not isinstance(days, int) or isinstance(days, bool)):
        return jsonify({"ok": False, "message": "days must be a whole number"}), 400
    return jsonify({"ok": True, **sent_text_store.set_keep(keep, days)})
