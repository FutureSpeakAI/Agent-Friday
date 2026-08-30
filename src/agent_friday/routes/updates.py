"""Routes for the weekly update check.

Three verbs, and deliberately no fourth:

  GET  /api/updates/status   what we already know (reads state, never the network)
  POST /api/updates/check    check GitHub NOW, because the user asked
  POST /api/updates/enabled  turn the weekly check on or off

There is no download endpoint and there will not be one. This feature notifies;
the person decides. Updating Friday means re-running the installer, which is
the one update path — see cli.cmd_update and packaging/windows/install.ps1.

The toggle writes through to the SCHEDULE record (`sch_update_check.enabled`),
not to a separate settings key. One switch, one truth: a settings flag beside a
schedule flag is two things to keep in sync and one of them will be wrong.
"""
import traceback

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import scheduler as _sched
from agent_friday.services import update_check as _uc

updates_bp = Blueprint('updates', __name__)


@updates_bp.route('/api/updates/status')
def updates_status():
    """Cached state + version truth. Never touches the network, so opening
    Settings is not a GitHub request."""
    try:
        return jsonify({"status": "ok", **_uc.status()})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@updates_bp.route('/api/updates/check', methods=['POST'])
@login_required
def updates_check_now():
    """A user-initiated check. `force` bypasses the once-a-week floor — the
    floor exists so the SCHEDULER is polite, not to stop someone who clicked."""
    try:
        result = _uc.run_update_check(force=True)
        return jsonify({"status": "ok", "result": result, **_uc.status()})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@updates_bp.route('/api/updates/enabled', methods=['POST'])
@login_required
def updates_set_enabled():
    """Body: {"enabled": bool}. Patches the schedule record."""
    body = request.get_json(silent=True) or {}
    if "enabled" not in body:
        return jsonify({"status": "error", "message": "enabled is required"}), 400
    want = bool(body["enabled"])
    try:
        rec = _sched.update_schedule(_uc.SCHEDULE_ID, {"enabled": want})
        if not rec:
            return jsonify({"status": "error",
                            "message": "update check schedule not found"}), 404
        return jsonify({"status": "ok", "enabled": bool(rec.get("enabled", True)),
                        **_uc.status()})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
