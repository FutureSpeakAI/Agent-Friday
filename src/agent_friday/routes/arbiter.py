"""HTTP surface for the resource arbiter of record (AE-0).

Thin: every decision lives in `services/arbiter.py`. These handlers translate
and nothing more, so there is exactly one place where availability is computed
and exactly one place a refusal is worded.

Envelope follows `routes/residency.py`, the closest sibling: {"status": "ok"|"error"}.
"""
import traceback

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import arbiter as _arb

arbiter_bp = Blueprint('arbiter', __name__)


def _err(e):
    traceback.print_exc()
    return jsonify({"status": "error", "message": str(e)}), 500


@arbiter_bp.route('/api/arbiter/status')
def arbiter_status():
    """What the resource strip draws. Measured every call (cached ~2 s)."""
    try:
        fresh = request.args.get('fresh') in ('1', 'true', 'yes')
        return jsonify({"status": "ok", "arbiter": _arb.status(fresh=fresh),
                        "presets": [{"name": k, "label": v["label"]}
                                    for k, v in _arb.PRESETS.items()]})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/leases')
def arbiter_leases():
    try:
        return jsonify({"status": "ok",
                        "leases": _arb.held(request.args.get('resource'))})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/events')
def arbiter_events():
    try:
        limit = int(request.args.get('limit') or 50)
        return jsonify({"status": "ok", "events": _arb.events(limit)})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/acquire', methods=['POST'])
@login_required
def arbiter_acquire():
    """Claim a resource. A refusal is a 200 with granted=false and options --
    this is a decision, not an error, and the caller must be able to render it."""
    body = request.get_json(silent=True) or {}
    try:
        d = _arb.acquire(
            body.get('resource'), body.get('amount'),
            body.get('holder') or 'friday',
            purpose=body.get('purpose'), ttl_s=body.get('ttl_s'),
            evictable=bool(body.get('evictable')),
            evict_cost_s=body.get('evict_cost_s'),
            restore_cost_s=body.get('restore_cost_s'),
            restore_token=body.get('restore_token'),
            allow_evict=bool(body.get('allow_evict')))
        return jsonify({"status": "ok", "decision": d})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/release', methods=['POST'])
@login_required
def arbiter_release():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok",
                        "result": _arb.release(body.get('lease_id'))})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/foreign-hold', methods=['POST'])
@login_required
def arbiter_foreign_hold():
    """Declare a claim by something Friday did not start. Never evictable."""
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok", "result": _arb.declare_foreign_hold(
            body.get('resource'), body.get('amount'),
            body.get('holder') or 'foreign process',
            purpose=body.get('purpose'), ttl_s=body.get('ttl_s'))})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/foreign-hold/preset', methods=['POST'])
@login_required
def arbiter_foreign_preset():
    """The one-click declaration: "I'm training - hands off the GPU and one core"."""
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok", "result": _arb.declare_preset(
            body.get('preset') or 'training', body.get('holder'),
            purpose=body.get('purpose'), ttl_s=body.get('ttl_s'))})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/eviction-plan', methods=['POST'])
@login_required
def arbiter_eviction_plan():
    """What it would cost to make room -- the offer behind a refusal."""
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok", "plan": _arb.plan_eviction(
            body.get('resource'), int(body.get('need') or 0),
            requester=body.get('requester'))})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/evict', methods=['POST'])
@login_required
def arbiter_evict():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok", "result": _arb.evict(
            body.get('lease_id'), reason=body.get('reason'))})
    except Exception as e:
        return _err(e)


@arbiter_bp.route('/api/arbiter/restore', methods=['POST'])
@login_required
def arbiter_restore():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"status": "ok",
                        "decision": _arb.restore(body.get('lease_id'))})
    except Exception as e:
        return _err(e)
