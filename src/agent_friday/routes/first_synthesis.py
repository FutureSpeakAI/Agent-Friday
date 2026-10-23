"""routes/first_synthesis.py -- "What I see so far" (services/first_synthesis).

    GET  /api/first-synthesis          the briefing: at most five items, seven fields each
    POST /api/first-synthesis/mark     {id, mark}: the owner's mark on one item

Only the local owner on this PC, from Friday's own page. A mark is the owner's own press in
Friday's window (screen_click.screen_session), through the action gate as 'synthesis_mark':
"remember this" writes the memory card, so nothing else may make it.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import first_synthesis

first_synthesis_bp = Blueprint("first_synthesis", __name__)


@first_synthesis_bp.before_request
def _gate():
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"ok": False, "error": "this is only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"ok": False, "error": "this is only shown on Friday's own computer"}), 403
    except Exception:
        return jsonify({"ok": False, "error": "could not establish that this request is local"}), 403
    if request.method == "POST":
        if not request.is_json:
            return jsonify({"ok": False, "error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"ok": False, "error": "cross-origin request refused"}), 403
    return None


@first_synthesis_bp.route("/api/first-synthesis", methods=["GET"])
@login_required
def first_synthesis_get():
    return jsonify(first_synthesis.build())


@first_synthesis_bp.route("/api/first-synthesis/mark", methods=["POST"])
@login_required
def first_synthesis_mark():
    from agent_friday.services import screen_click
    if not screen_click.screen_session(request):
        return jsonify({"ok": False, "code": "desk_proof_required",
                        "error": "A mark is made only by your own press in Friday's window on this PC."}), 403
    b = request.get_json(silent=True) or {}
    answer = str(b.get("mark") or "")[:16]
    item = str(b.get("id") or "")[:40]
    from agent_friday.governance import action_gate
    v = action_gate.authorize("synthesis_mark", {"mark": answer, "id": item},
                              {"screen_click": True, "surface": "first-synthesis"})
    if v.action != "allow":
        return jsonify({"ok": False, "error": "held: %s" % v.reason}), 403
    out = first_synthesis.mark(item, answer, by="owner:ui")
    return jsonify(out), (200 if out.get("ok") else 400)
