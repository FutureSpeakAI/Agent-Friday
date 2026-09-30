import traceback

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services.workspace_studio import (
    all_customizations,
    check_ws_id,
    clear_chat,
    load_ws_doc,
    reset_customization,
    revert_customization,
    workspace_chat_turn,
)
from agent_friday.services.model_router import (
    _gated_vault_control,
    _get_friday_system_prompt,
    _predict_route_provider,
)
from agent_friday.routes._errors import api_error

ws_studio_bp = Blueprint('ws_studio', __name__)


# Defence in depth for routes that will soon carry executable bundles: every
# one requires login on its own (the global before_request is not relied on),
# every answer carries a CSP, and a workspace id that is not a plain name is a
# 400, never a file under another name (services/workspace_studio.check_ws_id).
@ws_studio_bp.after_request
def _ws_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    return resp


@ws_studio_bp.errorhandler(ValueError)
def _ws_bad_id(e):
    return jsonify({"status": "error", "message": str(e) or "invalid request"}), 400


# ═══ WORKSPACE STUDIO — Friday as per-workspace customization agent ═══

@ws_studio_bp.route('/api/workspace/customizations')
@login_required
def ws_customizations():
    """Every workspace's current customization, so the UI can apply them all on
    first paint (one call instead of one-per-window)."""
    try:
        return jsonify({"status": "ok", "customizations": all_customizations()})
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't load the workspace customizations")


@ws_studio_bp.route('/api/workspace/<ws_id>/chat', methods=['GET'])
@login_required
def ws_chat_get(ws_id):
    check_ws_id(ws_id)
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
        return api_error(e, "Couldn't load the workspace chat")


@ws_studio_bp.route('/api/workspace/<ws_id>/chat', methods=['POST'])
@login_required
def ws_chat_post(ws_id):
    check_ws_id(ws_id)
    """Send a message to the workspace-scoped chat. Friday may reply with a live
    customization, which is applied + versioned server-side."""
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"status": "error", "message": "message required"}), 400
    label = (data.get('label') or ws_id).strip()
    try:
        system = _get_friday_system_prompt(
            keywords=message, workspace=ws_id,
            provider=_predict_route_provider(keywords=message, workspace=ws_id),
            vault_control=_gated_vault_control())
    except Exception:
        system = None
    try:
        result = workspace_chat_turn(ws_id, label, message, system=system)
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't send the workspace message")


@ws_studio_bp.route('/api/workspace/<ws_id>/chat/clear', methods=['POST'])
@login_required
def ws_chat_clear(ws_id):
    check_ws_id(ws_id)
    try:
        doc = clear_chat(ws_id)
        return jsonify({"status": "ok", "chat": doc.get("chat", [])})
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't clear the workspace chat")


@ws_studio_bp.route('/api/workspace/<ws_id>/revert', methods=['POST'])
@login_required
def ws_revert(ws_id):
    check_ws_id(ws_id)
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
        return api_error(e, "Couldn't revert the workspace")


@ws_studio_bp.route('/api/workspace/<ws_id>/reset', methods=['POST'])
@login_required
def ws_reset(ws_id):
    check_ws_id(ws_id)
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
        return api_error(e, "Couldn't reset the workspace")
