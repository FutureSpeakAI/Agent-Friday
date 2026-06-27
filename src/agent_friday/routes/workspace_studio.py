import traceback

from flask import Blueprint, jsonify, request

from agent_friday.services.workspace_studio import (
    all_customizations,
    clear_chat,
    load_ws_doc,
    reset_customization,
    revert_customization,
    workspace_chat_turn,
)
from agent_friday.services.model_router import _get_friday_system_prompt

ws_studio_bp = Blueprint('ws_studio', __name__)


# ═══ WORKSPACE STUDIO — Friday as per-workspace customization agent ═══

@ws_studio_bp.route('/api/workspace/customizations')
def ws_customizations():
    """Every workspace's current customization, so the UI can apply them all on
    first paint (one call instead of one-per-window)."""
    try:
        return jsonify({"status": "ok", "customizations": all_customizations()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@ws_studio_bp.route('/api/workspace/<ws_id>/chat', methods=['GET'])
def ws_chat_get(ws_id):
    """Per-workspace chat history + current customization + version stack."""
    try:
        doc = load_ws_doc(ws_id)
        return jsonify({
            "status": "ok",
            "workspace": ws_id,
            "chat": doc.get("chat", []),
            "customization": doc.get("customization", {}),
            "versions": doc.get("versions", []),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@ws_studio_bp.route('/api/workspace/<ws_id>/chat', methods=['POST'])
def ws_chat_post(ws_id):
    """Send a message to the workspace-scoped chat. Friday may reply with a live
    customization, which is applied + versioned server-side."""
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"status": "error", "message": "message required"}), 400
    label = (data.get('label') or ws_id).strip()
    try:
        system = _get_friday_system_prompt(keywords=message, workspace=ws_id)
    except Exception:
        system = None
    try:
        result = workspace_chat_turn(ws_id, label, message, system=system)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@ws_studio_bp.route('/api/workspace/<ws_id>/chat/clear', methods=['POST'])
def ws_chat_clear(ws_id):
    try:
        doc = clear_chat(ws_id)
        return jsonify({"status": "ok", "chat": doc.get("chat", [])})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@ws_studio_bp.route('/api/workspace/<ws_id>/revert', methods=['POST'])
def ws_revert(ws_id):
    """Roll the workspace back to a snapshot version."""
    data = request.get_json(silent=True) or {}
    version_id = (data.get('version_id') or '').strip()
    if not version_id:
        return jsonify({"status": "error", "message": "version_id required"}), 400
    try:
        doc = revert_customization(ws_id, version_id)
        if doc is None:
            return jsonify({"status": "error", "message": "version not found"}), 404
        return jsonify({
            "status": "ok",
            "customization": doc.get("customization", {}),
            "versions": doc.get("versions", []),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@ws_studio_bp.route('/api/workspace/<ws_id>/reset', methods=['POST'])
def ws_reset(ws_id):
    """Clear all customization (snapshotted first, so it's undoable)."""
    try:
        doc = reset_customization(ws_id)
        return jsonify({
            "status": "ok",
            "customization": doc.get("customization", {}),
            "versions": doc.get("versions", []),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
