"""
Agent Friday — Learning Loop API
FutureSpeak.AI · Asimov's Mind

  GET   /api/learning/state     counts, top skills, observation total
  GET   /api/learning/skills    active learned heuristics
  POST  /api/learning/epoch     run one mine→promote cycle now
  POST  /api/learning/observe   record a task outcome (used by the agent loop)
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import learning_loop

learning_bp = Blueprint("learning", __name__)


@learning_bp.route("/api/learning/state", methods=["GET"])
@login_required
def learning_state():
    return jsonify({"ok": True, "state": learning_loop.state()})


@learning_bp.route("/api/learning/skills", methods=["GET"])
@login_required
def learning_skills():
    task_type = request.args.get("task_type")
    return jsonify({"ok": True, "skills": learning_loop.active_skills(task_type)})


@learning_bp.route("/api/learning/epoch", methods=["POST"])
@login_required
def learning_epoch():
    return jsonify(learning_loop.run_epoch())


@learning_bp.route("/api/learning/observe", methods=["POST"])
@login_required
def learning_observe():
    d = request.get_json(silent=True) or {}
    if not d.get("task_type") or "success" not in d:
        return jsonify({"ok": False, "error": "task_type and success required"}), 400
    return jsonify(learning_loop.observe(
        d["task_type"], d.get("prompt", ""), approach=d.get("approach", "default"),
        success=bool(d["success"]), satisfaction=d.get("satisfaction"),
        revisions=int(d.get("revisions", 0)), workspace=d.get("workspace", "")))
