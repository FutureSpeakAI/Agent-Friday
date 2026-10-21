import traceback

from flask import Blueprint, jsonify, request
from agent_friday.services.pasted_secrets import withholds_request_body

from agent_friday.core import login_required
from agent_friday.services.workspace_studio import (
    all_customizations,
    apply_proposal,
    check_ws_id,
    clear_chat,
    decline_proposal,
    load_ws_doc,
    reset_customization,
    revert_customization,
    workspace_chat_turn,
    read_presentation,
    review_presentation,
    WorkspaceConflictError,
    _admit_write,
    _presentation_origin,
    _proposal_view,
)
from agent_friday.services.model_router import (
    _gated_vault_control,
    _get_friday_system_prompt,
    _predict_route_provider,
)
from agent_friday.routes._errors import api_error, error_text

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
    return api_error(e, "That request was not valid", 400)


def owner_change(ws_id, op):
    """None when this request may change workspace `ws_id`'s saved state, else the 403 to answer.

    Two checks, in this order, before anything is read or written:
      * the owner's own press in Friday's page on this PC (screen_click.screen_session); a program
        that can reach the API, or a model's command, is not a press;
      * the action checkpoint (governance/action_gate.record_external): the cLaws are intact and a
        signed receipt names this exact change.
    Each refusal says what did not happen and where the change can be made. Off the record, the
    change is refused first (as the store would refuse it), so no receipt names a change that the
    privacy rule stops."""
    from agent_friday.services import owner_press
    try:
        _admit_write()
    except PermissionError as exc:
        return api_error(exc, 'That change was refused', 403)
    refused = owner_press.refusal(request, "Nothing changed: workspace changes are made with your press in "
                                           "this workspace's Studio on this PC, or by asking Friday in chat.")
    if refused is not None:
        return refused
    from agent_friday.governance import action_gate as _gate
    try:
        _gate.record_external("workspace:" + op, surface="workspace_studio", target=ws_id)
    except _gate.Held:
        return jsonify(status='error', code='held',
                       message="Nothing changed: the action checkpoint held this workspace change "
                               "(its integrity check or signed receipt failed)."), 403
    return None


# ═══ WORKSPACE STUDIO — Friday as per-workspace customization agent ═══

@ws_studio_bp.route('/api/workspace/<ws_id>/appearance', methods=['GET', 'POST'])
@login_required
def ws_appearance(ws_id):
    """A scoped appearance preview and a revision-checked application."""
    try:
        if request.method == 'GET':
            result = read_presentation(ws_id)
        else:
            origin = _presentation_origin()
            if request.content_length is None or request.content_length > 32 * 1024:
                return jsonify(status='error', message='The appearance request is too large or has no declared size.'), 413
            data = request.get_json(silent=True)
            if (not isinstance(data, dict) or set(data) - {'patch', 'expected_revision', 'apply'}
                    or type(data.get('apply', False)) is not bool):
                return jsonify(status='error', message='Provide an appearance patch and its reviewed revision.'), 400
            if data.get('apply'):
                # A preview is read-only and open to every caller; applying it is the owner's press.
                refused = owner_change(check_ws_id(ws_id), 'appearance')
                if refused:
                    return refused
            result = review_presentation(ws_id, data.get('patch'), data.get('expected_revision'),
                                         apply=data.get('apply', False), origin=origin)
        return jsonify(status='ok', **result)
    except WorkspaceConflictError as exc:
        return jsonify(status='conflict', message=error_text(exc, 'The workspace changed')), 409
    except PermissionError as exc:
        return api_error(exc, 'That change was refused', 403)
    except ValueError as exc:
        return api_error(exc, 'That appearance change was not valid', 400)
    except OSError:
        return jsonify(status='error', message='Workspace appearance could not be read or saved. Your draft is kept.'), 503

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
@withholds_request_body
def ws_chat_post(ws_id):
    check_ws_id(ws_id)
    """Send a message to the workspace-scoped chat. Friday may reply with a live
    customization, which is applied + versioned server-side."""
    try:
        origin = _admit_write()
        data = request.get_json(silent=True) or {}
        message = (data.get('message') or '').strip()
        if not message:
            return jsonify({"status": "error", "message": "message required"}), 400
        label = (data.get('label') or ws_id).strip()
        _admit_write(origin)
    except PermissionError as exc:
        return api_error(exc, 'That change was refused', 403)
    try:
        system = _get_friday_system_prompt(
            keywords=message, workspace=ws_id,
            provider=_predict_route_provider(keywords=message, workspace=ws_id),
            vault_control=_gated_vault_control())
    except Exception:
        system = None
    try:
        result = workspace_chat_turn(ws_id, label, message, system=system, origin=origin)
        return jsonify(result)
    except PermissionError as exc:
        return api_error(exc, 'That change was refused', 403)
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't send the workspace message")


@ws_studio_bp.route('/api/workspace/<ws_id>/chat/clear', methods=['POST'])
@login_required
def ws_chat_clear(ws_id):
    check_ws_id(ws_id)
    refused = owner_change(ws_id, 'clear_history')
    if refused:
        return refused
    try:
        doc = clear_chat(ws_id)
        return jsonify({"status": "ok", "chat": doc.get("chat", [])})
    except PermissionError as exc:
        return api_error(exc, 'That change was refused', 403)
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't clear the workspace chat")


@ws_studio_bp.route('/api/workspace/<ws_id>/revert', methods=['POST'])
@login_required
def ws_revert(ws_id):
    check_ws_id(ws_id)
    """Roll the workspace back to a snapshot version."""
    try:
        origin = _admit_write()
        refused = owner_change(ws_id, 'revert')
        if refused:
            return refused
        data = request.get_json(silent=True) or {}
        version_id = (data.get('version_id') or '').strip()
        if not version_id:
            return jsonify({"status": "error", "message": "version_id required"}), 400
        doc = revert_customization(ws_id, version_id, origin=origin)
        if doc is None:
            return jsonify({"status": "error", "message": "version not found"}), 404
        return jsonify({
            "status": "ok",
            "customization": doc.get("customization", {}),
            "versions": doc.get("versions", []),
        })
    except PermissionError as exc:
        return api_error(exc, 'That change was refused', 403)
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't revert the workspace")


@ws_studio_bp.route('/api/workspace/<ws_id>/reset', methods=['POST'])
@login_required
def ws_reset(ws_id):
    check_ws_id(ws_id)
    """Clear all customization (snapshotted first, so it's undoable)."""
    refused = owner_change(ws_id, 'reset')
    if refused:
        return refused
    try:
        doc = reset_customization(ws_id)
        return jsonify({
            "status": "ok",
            "customization": doc.get("customization", {}),
            "versions": doc.get("versions", []),
        })
    except PermissionError as exc:
        return api_error(exc, 'That change was refused', 403)
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't reset the workspace")


def _proposal_answer(ws_id, proposal_id, apply):
    try:
        doc, prop = (apply_proposal if apply else decline_proposal)(ws_id, proposal_id)
    except WorkspaceConflictError as exc:
        return jsonify(status='conflict', message=error_text(exc, 'The workspace changed')), 409
    except PermissionError as exc:
        return api_error(exc, 'That change was refused', 403)
    except ValueError as exc:
        return api_error(exc, 'That proposal could not be found', 404)
    except OSError:
        return jsonify(status='error', message='The workspace could not be read or saved. Nothing changed.'), 503
    return jsonify({
        "status": "ok",
        "proposal": _proposal_view(prop),
        "applied": prop.get("status") == "applied",
        "revert_to": prop.get("revert_to"),
        "customization": doc.get("customization", {}),
        "versions": doc.get("versions", []),
    })


@ws_studio_bp.route('/api/workspace/<ws_id>/proposals/<proposal_id>/apply', methods=['POST'])
@login_required
def ws_proposal_apply(ws_id, proposal_id):
    """The owner's Apply on a change Friday proposed in the Studio: exactly the
    lines the proposal showed, after the owner's press and the checkpoint."""
    check_ws_id(ws_id)
    refused = owner_change(ws_id, 'apply_proposal')
    if refused:
        return refused
    return _proposal_answer(ws_id, proposal_id, True)


@ws_studio_bp.route('/api/workspace/<ws_id>/proposals/<proposal_id>/decline', methods=['POST'])
@login_required
def ws_proposal_decline(ws_id, proposal_id):
    """"Not now": the proposal stays in the history, marked declined. Declining
    changes nothing about the workspace, so any of the owner's channels may."""
    check_ws_id(ws_id)
    return _proposal_answer(ws_id, proposal_id, False)
