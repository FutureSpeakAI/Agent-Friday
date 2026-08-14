"""Global activity ledger API (B4 — Seats & Transparency).

GET /api/activity — recent activity events (newest first), filterable by
kind / model / task_id / since / until. Events are metadata-only by
construction (see services/activity_ledger.py) so this endpoint never leaks
prompt text or tool payloads.
"""

from flask import Blueprint, jsonify, request

from agent_friday.services import activity_ledger

activity_bp = Blueprint('activity', __name__)


def _float_arg(name):
    raw = request.args.get(name)
    if raw in (None, ''):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@activity_bp.route('/api/activity')
def api_activity():
    try:
        limit = int(request.args.get('limit', 200))
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 1000))
    events = activity_ledger.read(
        limit=limit,
        kind=request.args.get('kind') or None,
        model=request.args.get('model') or None,
        task_id=request.args.get('task_id') or None,
        since=_float_arg('since'),
        until=_float_arg('until'),
    )
    return jsonify({"status": "ok", "events": events, "count": len(events)})
