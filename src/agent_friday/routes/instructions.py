"""
Friday's core instructions, for the owner to inspect and undo.

  GET   /api/instructions              every section: who changed it, paused or
                                       ignored overrides and why (health rows)
  GET   /api/instructions/<id>         one section: shipped text, text in use,
                                       a stored override, the difference, history
  POST  /api/instructions/<id>/undo    put back the text before the last change.
                                       Only the owner's click on the page counts
                                       (services/screen_click); anything else 403.

Locked sections (the safety rules) are shown read-only and can never be undone
or edited here: they change only in a software update.
"""
from flask import Blueprint, jsonify, request

from agent_friday.core import login_required

instructions_bp = Blueprint("instructions", __name__)


def _sid_ok(sid: str) -> bool:
    from agent_friday.services import instruction_files as inf
    return sid in inf.section_ids() or sid in {p["id"] for p in inf.policies()}


@instructions_bp.route("/api/instructions", methods=["GET"])
@login_required
def instructions_list():
    from agent_friday.services import instruction_files as inf
    rows = inf.status()
    return jsonify({"ok": True, "sections": rows, "problems": inf.problems()})


@instructions_bp.route("/api/instructions/<path:sid>", methods=["GET"])
@login_required
def instructions_detail(sid):
    from agent_friday.services import instruction_files as inf
    if not _sid_ok(sid):
        return jsonify({"ok": False, "error": "no such section"}), 404
    return jsonify({"ok": True, "section": inf.detail(sid)})


@instructions_bp.route("/api/instructions/<path:sid>/undo", methods=["POST"])
@login_required
def instructions_undo(sid):
    from agent_friday.services import instruction_files as inf
    from agent_friday.services import screen_click
    if screen_click.decided_by(request, "owner:ui") != "owner:ui":
        return jsonify({"ok": False, "error": "Undo is a click on the Settings page."}), 403
    if not _sid_ok(sid):
        return jsonify({"ok": False, "error": "no such section"}), 404
    if inf.is_locked(sid):
        return jsonify({"ok": False, "error": "This is part of Friday's safety rules and changes only in a software update."}), 400
    try:
        row = inf.undo(sid, actor="owner:ui")
    except inf.InstructionError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    try:
        from agent_friday.services import prompt_freeze
        prompt_freeze.owner_changed("instruction section undone by the owner")
    except Exception:
        pass
    return jsonify({"ok": True, "change": row, "section": inf.detail(sid)})
