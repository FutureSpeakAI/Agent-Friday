"""Undo for liquid-UI / workspace customizations.

  GET  /api/workspace/<ws>/history        the audit trail
  POST /api/workspace/<ws>/undo           undo the most recent change
  POST /api/workspace/<ws>/restore-as-of  {when} restore state at a moment

Stephen, 2026-08-17: "if I decide to talk to Friday and modify one of my
workspaces, that needs to be easily rolled back either by telling Friday to roll
it back or through a UI element."

NOTE (2026-08-18): this module originally also registered /revert and /reset,
believing the snapshot machinery had no routes. It did — workspace_studio.py has
registered both since before the public release, and because 'workspace_studio'
sorts before 'workspace_undo' in blueprint registration, Flask served the studio
handlers and the duplicates here were dead code that LOOKED live (the app-level
test suite proved this at runtime: the two handlers return different JSON shapes,
and only the studio shape ever came back). The UI in ui_parts/app.html reads the
studio shape ({status, customization, versions}), so studio keeps /revert and
/reset; this module keeps the routes only it provides. Do not re-add the
duplicates — Flask will not warn you, it will just ignore them.
"""
from flask import Blueprint, jsonify, request

workspace_undo_bp = Blueprint("workspace_undo", __name__)


def _ws():
    from agent_friday.services import workspace_studio as ws
    return ws


@workspace_undo_bp.route("/api/workspace/<ws_id>/history", methods=["GET"])
def ws_history(ws_id):
    return jsonify(_ws().history(ws_id))


@workspace_undo_bp.route("/api/workspace/<ws_id>/undo", methods=["POST"])
def ws_undo(ws_id):
    doc, err = _ws().undo_last(ws_id)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True, "customization": doc.get("customization") or {},
                    "note": "undone — this undo is itself snapshotted, so it "
                            "can be undone too"})


@workspace_undo_bp.route("/api/workspace/<ws_id>/restore-as-of", methods=["POST"])
def ws_restore_as_of(ws_id):
    when = (request.get_json(silent=True) or {}).get("when")
    if not when:
        return jsonify({"ok": False,
                        "error": "when is required (ISO timestamp)"}), 400
    doc, err = _ws().restore_as_of(ws_id, when)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True,
                    "customization": doc.get("customization") or {}})


