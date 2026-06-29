"""
Work log routes — audit trail for all orchestrated work.

GET  /api/work-log                 — paginated log
GET  /api/work-log/<work_id>       — single entry
POST /api/work-log/prune           — delete entries older than N days
"""
from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import work_log as wl

work_log_bp = Blueprint("work_log", __name__)


@work_log_bp.route("/api/work-log", methods=["GET"])
@login_required
def get_log():
    limit  = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    workspace   = request.args.get("workspace")
    worker_type = request.args.get("worker_type")
    since = request.args.get("since")
    until = request.args.get("until")
    try:
        entries = wl.get_log(limit, offset, workspace, worker_type, since, until)
        return jsonify({"ok": True, "entries": entries, "count": len(entries)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@work_log_bp.route("/api/work-log/<work_id>", methods=["GET"])
@login_required
def get_entry(work_id):
    entry = wl.get_entry(work_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "entry": entry})


@work_log_bp.route("/api/work-log/prune", methods=["POST"])
@login_required
def prune():
    data = request.get_json(silent=True) or {}
    days = int(data.get("days", 90))
    try:
        count = wl.delete_old_entries(days)
        return jsonify({"ok": True, "deleted": count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
