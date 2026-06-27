"""Routes for the tool lifecycle-hook system (Part B).

Read-only listing of the active PreToolUse/PostToolUse hooks plus a toggle for
the non-critical built-ins (critical hooks can't be disabled). Authoring new
hooks stays at the code/skill level — this surface is view + enable/disable.
"""
import traceback
from flask import Blueprint, jsonify, request
import agent_friday.core as core
from agent_friday.core import login_required, _load_settings_raw, _save_settings
from agent_friday.services import tool_hooks as _hooks

hooks_bp = Blueprint('hooks', __name__)


@hooks_bp.route('/api/hooks')
def list_hooks():
    """All registered tool hooks (name, phase, scope, source, enabled)."""
    try:
        return jsonify({"status": "ok", "hooks": _hooks.list_hooks()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@hooks_bp.route('/api/hooks/<name>', methods=['POST'])
@login_required
def toggle_hook(name):
    """Enable/disable a non-critical built-in hook. Body: {"enabled": bool}."""
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", True))
    # Refuse to disable a critical hook (governance/vault) — they're fail-closed
    # security gates and must always run.
    rows = {h["name"]: h for h in _hooks.list_hooks()}
    row = rows.get(name)
    if not row:
        return jsonify({"status": "error", "message": f"unknown hook {name!r}"}), 404
    if row.get("critical") and not enabled:
        return jsonify({"status": "error",
                        "message": f"hook {name!r} is critical and cannot be disabled"}), 400
    try:
        settings = _load_settings_raw()
        th = dict(settings.get("tool_hooks") or {})
        entry = dict(th.get(name) or {})
        entry["enabled"] = enabled
        th[name] = entry
        _save_settings({"tool_hooks": th})
        return jsonify({"status": "ok", "name": name, "enabled": enabled})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
