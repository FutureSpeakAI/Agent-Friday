"""Routes for cost metering + budgets (Part D).

Read endpoints back the Settings → Cost & Usage dashboard; the budget POST is
authenticated. Spend itself is recorded at the model-call sites via
services.cost_meter.
"""
import traceback
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import cost_meter as _cm

costs_bp = Blueprint('costs', __name__)


@costs_bp.route('/api/costs/summary')
def costs_summary():
    """Totals + by-provider/workspace/model/kind for a range."""
    rng = request.args.get('range', 'today')
    frm = request.args.get('from', type=float)
    to = request.args.get('to', type=float)
    try:
        return jsonify({"status": "ok", "summary": _cm.summary(rng, frm, to)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@costs_bp.route('/api/costs/timeseries')
def costs_timeseries():
    """Bucketed spend over time (drives the dashboard sparkline)."""
    rng = request.args.get('range', 'month')
    bucket = request.args.get('bucket', 'day')
    try:
        return jsonify({"status": "ok", "series": _cm.timeseries(rng, bucket)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@costs_bp.route('/api/costs/scheduled')
def costs_scheduled():
    """Per-schedule cost — 'what does job-intel cost me?'"""
    rng = request.args.get('range', 'month')
    try:
        return jsonify({"status": "ok", "scheduled": _cm.by_schedule(rng)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@costs_bp.route('/api/costs/budget', methods=['GET'])
def get_budget():
    return jsonify({"status": "ok", "budget": _cm.get_budget()})


@costs_bp.route('/api/costs/budget', methods=['POST'])
@login_required
def set_budget():
    patch = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok", "budget": _cm.set_budget(patch)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500
