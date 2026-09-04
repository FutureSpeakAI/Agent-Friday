"""
Memory proposal routes — the manual door into services/memory_proposals.py.

Gauntlet finding F53 (2026-09-04): the service module was fully built --
propose()/pending()/approve()/reject()/state() -- but nothing in the running
app could ever call it. Its own docstring is explicit that this is meant to
be manual-first: "propose() is something the user RUNS, and its output is
shown to him before any of it becomes durable." These routes are that door.
No UI consumes them yet -- that is a separate, real design decision (where
review lives in the app) left for later. This makes the feature reachable,
which it was not before.

  POST /api/memory/proposals/propose    — read a day, stage candidate facts
  GET  /api/memory/proposals/pending    — facts awaiting review
  POST /api/memory/proposals/approve    — promote reviewed facts to durable memory
  POST /api/memory/proposals/reject     — discard proposals
  GET  /api/memory/proposals/state      — counts + whether the seat is usable
"""
import traceback

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import memory_proposals as mp

memory_proposals_bp = Blueprint("memory_proposals", __name__)


@memory_proposals_bp.route("/api/memory/proposals/propose", methods=["POST"])
@login_required
def api_memory_proposals_propose():
    data = request.get_json(silent=True) or {}
    try:
        result = mp.propose(day=data.get("day"))
        return jsonify(result)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "reason": str(e)}), 500


@memory_proposals_bp.route("/api/memory/proposals/pending", methods=["GET"])
@login_required
def api_memory_proposals_pending():
    try:
        day = request.args.get("day")
        limit = request.args.get("limit", default=200, type=int)
        return jsonify({"ok": True, "facts": mp.pending(day=day, limit=limit)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "reason": str(e)}), 500


@memory_proposals_bp.route("/api/memory/proposals/approve", methods=["POST"])
@login_required
def api_memory_proposals_approve():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(mp.approve(data.get("fact_ids") or []))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "reason": str(e)}), 500


@memory_proposals_bp.route("/api/memory/proposals/reject", methods=["POST"])
@login_required
def api_memory_proposals_reject():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(mp.reject(data.get("fact_ids") or []))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "reason": str(e)}), 500


@memory_proposals_bp.route("/api/memory/proposals/state", methods=["GET"])
@login_required
def api_memory_proposals_state():
    try:
        return jsonify({"ok": True, **mp.state()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "reason": str(e)}), 500
