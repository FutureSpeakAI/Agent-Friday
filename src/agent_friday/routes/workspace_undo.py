"""Undo for liquid-UI / workspace customizations.

  GET  /api/workspace/<ws>/history        the audit trail
  POST /api/workspace/<ws>/undo           undo the most recent change
  POST /api/workspace/<ws>/revert         {version_id} restore a specific version
  POST /api/workspace/<ws>/restore-as-of  {when} restore state at a moment
  POST /api/workspace/<ws>/reset          back to baseline

Stephen, 2026-08-17: "if I decide to talk to Friday and modify one of my
workspaces, that needs to be easily rolled back either by telling Friday to roll
it back or through a UI element."

The snapshot machinery for this already existed in services/workspace_studio.py
and NOTHING could reach it — `revert_customization` had no route, no tool and no
button anywhere in the tree. Built, never wired, which is the day's recurring
shape. These routes are the button's half; `revert_workspace` is the spoken half;
both call the same functions.
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


@workspace_undo_bp.route("/api/workspace/<ws_id>/revert", methods=["POST"])
def ws_revert(ws_id):
    vid = (request.get_json(silent=True) or {}).get("version_id")
    if not vid:
        return jsonify({"ok": False, "error": "version_id is required"}), 400
    doc = _ws().revert_customization(ws_id, vid)
    if doc is None:
        return jsonify({"ok": False, "error": "no such version"}), 404
    return jsonify({"ok": True,
                    "customization": doc.get("customization") or {}})


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


@workspace_undo_bp.route("/api/workspace/<ws_id>/reset", methods=["POST"])
def ws_reset(ws_id):
    doc = _ws().reset_customization(ws_id)
    return jsonify({"ok": True,
                    "customization": doc.get("customization") or {}})
