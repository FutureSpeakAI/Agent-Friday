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
from agent_friday.routes._errors import public_result
from agent_friday.services import first_action, first_synthesis

first_synthesis_bp = Blueprint("first_synthesis", __name__)

# The first governed action's card runs from its own decision hook (services/first_action, A27):
# registered when the server loads its routes, so a press after a restart still runs it.
first_action.ensure_hook()


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
    from agent_friday.services import screen_click
    if screen_click.is_cross_site(request):
        return jsonify({"ok": False, "error": "cross-site request refused"}), 403
    return jsonify(first_synthesis.build())


@first_synthesis_bp.route("/api/onboarding/status", methods=["GET"])
@login_required
def onboarding_status_get():
    """Section 9.6: onboarding is done only when all six checks are met. `setup_complete` (the
    setup steps ran) is reported beside it and is not the same thing."""
    from agent_friday.services import onboarding_status
    return jsonify(onboarding_status.status())


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
    out = first_synthesis.mark(item, answer, shown_text=str(b.get("observation") or "")[:400], by="owner:ui")
    return jsonify(public_result(out, "Couldn't record that")), (200 if out.get("ok") else 400)


#: Section 9.6 answers only the owner gives (services/onboarding_status).
_ONBOARDING_ANSWERS = ("saw_where_it_ran", "skip_goal", "skip_first_action")


@first_synthesis_bp.route("/api/onboarding/first-action", methods=["GET"])
@login_required
def first_action_get():
    """The first governed action (A27): none / nothing_to_propose / proposed / expired / done /
    skipped / failed, its card and its receipt's ledger number."""
    return jsonify({"ok": True, **first_action.state()})


@first_synthesis_bp.route("/api/onboarding/first-action/propose", methods=["POST"])
@login_required
def first_action_propose():
    """Ask Friday for the one proposal (it is also made at the end of setup). This only raises a
    card that waits for the owner's press; nothing runs here."""
    from agent_friday.services import screen_click
    if screen_click.is_cross_site(request):
        return jsonify({"ok": False, "error": "cross-site request refused"}), 403
    out = first_action.propose(requested_by="owner:home")
    return jsonify(out), (200 if out.get("ok") else 400)


@first_synthesis_bp.route("/api/onboarding/answer", methods=["POST"])
@login_required
def onboarding_answer():
    """{answer: saw_where_it_ran, seq} -- the owner saw where that receipt's action ran;
    {answer: skip_goal} -- the owner chose not to set a goal now. Each is the owner's press here."""
    from agent_friday.services import screen_click
    if not screen_click.screen_session(request):
        return jsonify({"ok": False, "code": "desk_proof_required",
                        "error": "This is answered only by your own press in Friday's window on this PC."}), 403
    b = request.get_json(silent=True) or {}
    answer = str(b.get("answer") or "")
    if answer not in _ONBOARDING_ANSWERS:
        return jsonify({"ok": False, "error": "unknown answer"}), 400
    from agent_friday.services import onboarding_status
    out = onboarding_status.answer(answer, seq=b.get("seq"))
    return jsonify(out), (200 if out.get("ok") else 400)
