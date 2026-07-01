"""
Agent Friday — Memory Dreaming API
FutureSpeak.AI · Asimov's Mind

  GET   /api/memory/dreams       recent consolidation runs
  GET   /api/memory/dream/state  dreaming subsystem status
  POST  /api/memory/dream        run a consolidation pass now  {day?}
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import memory_dreaming

dreaming_bp = Blueprint("dreaming", __name__)


@dreaming_bp.route("/api/memory/dreams", methods=["GET"])
@login_required
def list_dreams():
    n = int(request.args.get("n", 7))
    return jsonify({"ok": True, "dreams": memory_dreaming.recent_dreams(n)})


@dreaming_bp.route("/api/memory/dream/state", methods=["GET"])
@login_required
def dream_state():
    return jsonify({"ok": True, "state": memory_dreaming.state()})


@dreaming_bp.route("/api/memory/dream", methods=["POST"])
@login_required
def run_dream():
    data = request.get_json(silent=True) or {}
    return jsonify(memory_dreaming.dream(day=data.get("day")))
