"""Routes for Friday's internal task scheduler (Part A).

CRUD over the schedule registry plus run-now and run-history. Reads are open
(the Settings panel polls them); mutations require an authenticated session.
"""
import traceback
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import scheduler as _sched

scheduler_bp = Blueprint('scheduler', __name__)


@scheduler_bp.route('/api/schedules')
def list_schedules():
    """All scheduled tasks with computed next-run + running flags."""
    try:
        return jsonify({"status": "ok", "schedules": _sched.list_schedules(),
                        "builtin_tasks": sorted(_sched.BUILTIN_TASKS.keys())})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduler_bp.route('/api/schedules', methods=['POST'])
@login_required
def create_schedule():
    """Create a user-defined schedule. Body: a schedule record (see spec A.2)."""
    data = request.get_json(silent=True) or {}
    if not (data.get("task") or {}).get("kind"):
        return jsonify({"status": "error", "message": "task.kind is required"}), 400
    try:
        rec = _sched.register_schedule(data)
        return jsonify({"status": "ok", "schedule": rec})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@scheduler_bp.route('/api/schedules/<sid>', methods=['GET'])
def get_schedule(sid):
    rec = _sched.get_schedule(sid)
    if not rec:
        return jsonify({"status": "error", "message": "not found"}), 404
    return jsonify({"status": "ok", "schedule": rec})


@scheduler_bp.route('/api/schedules/<sid>', methods=['POST', 'PATCH'])
@login_required
def update_schedule(sid):
    """Patch a schedule (name/trigger/spec/task/enabled/notify/retry/timeout)."""
    patch = request.get_json(silent=True) or {}
    rec = _sched.update_schedule(sid, patch)
    if not rec:
        return jsonify({"status": "error", "message": "not found"}), 404
    return jsonify({"status": "ok", "schedule": rec})


@scheduler_bp.route('/api/schedules/<sid>', methods=['DELETE'])
@login_required
def delete_schedule(sid):
    ok = _sched.delete_schedule(sid)
    if not ok:
        return jsonify({"status": "error",
                        "message": "not found or built-in (built-ins can be "
                                   "disabled but not deleted)"}), 400
    return jsonify({"status": "ok", "deleted": sid})


@scheduler_bp.route('/api/schedules/<sid>/run-now', methods=['POST'])
@scheduler_bp.route('/api/schedules/<sid>/run', methods=['POST'])
@login_required
def run_now(sid):
    """Dispatch a schedule immediately so the user can test it."""
    run_id = _sched.run_now(sid)
    if not run_id:
        return jsonify({"status": "error", "message": "not found"}), 404
    return jsonify({"status": "ok", "run_id": run_id})


@scheduler_bp.route('/api/schedules/<sid>/history')
def history(sid):
    """Recent run history for a schedule (newest first)."""
    limit = request.args.get('limit', default=10, type=int)
    try:
        return jsonify({"status": "ok",
                        "history": _sched.run_history(sid, limit=limit)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
