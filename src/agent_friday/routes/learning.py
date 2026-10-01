"""
Agent Friday — Learning Loop API
FutureSpeak.AI · Asimov's Mind

  GET   /api/learning/state     counts, top skills, observation total
  GET   /api/learning/skills    active learned heuristics
  POST  /api/learning/epoch     run one mine→promote cycle now
  POST  /api/learning/observe   record a task outcome (used by the agent loop)
  GET   /api/learning/proposals  pending heuristic changes awaiting the owner
  POST  /api/learning/proposals/<skill_id>  {decision: accept|edit|reject|defer,
                                             pattern?: the owner's wording for edit}
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import learning_loop
from agent_friday.routes._errors import public_result

learning_bp = Blueprint("learning", __name__)


@learning_bp.route("/api/learning/state", methods=["GET"])
@login_required
def learning_state():
    return jsonify(public_result({"ok": True, "state": learning_loop.state()}, "Couldn't read the learning state"))


@learning_bp.route("/api/learning/skills", methods=["GET"])
@login_required
def learning_skills():
    task_type = request.args.get("task_type")
    return jsonify({"ok": True, "skills": learning_loop.active_skills(task_type)})


@learning_bp.route("/api/learning/epoch", methods=["POST"])
@login_required
def learning_epoch():
    return jsonify(public_result(learning_loop.run_epoch(), "Couldn't run the learning epoch"))


@learning_bp.route("/api/learning/observe", methods=["POST"])
@login_required
def learning_observe():
    d = request.get_json(silent=True) or {}
    if not d.get("task_type") or "success" not in d:
        return jsonify({"ok": False, "error": "task_type and success required"}), 400
    return jsonify(public_result(learning_loop.observe(
        d["task_type"], d.get("prompt", ""), approach=d.get("approach", "default"),
        success=bool(d["success"]), satisfaction=d.get("satisfaction"),
        revisions=int(d.get("revisions", 0)), workspace=d.get("workspace", "")), "Couldn't record the observation"))


@learning_bp.route("/api/learning/proposals", methods=["GET"])
@login_required
def learning_proposals():
    return jsonify({"ok": True, "proposals": learning_loop.pending_proposals()})


@learning_bp.route("/api/learning/proposals/<skill_id>", methods=["POST"])
@login_required
def learning_decide(skill_id):
    d = request.get_json(silent=True) or {}
    res = learning_loop.decide_proposal(skill_id, str(d.get("decision") or ""),
                                        pattern=d.get("pattern"))
    return jsonify(public_result(res, "Couldn't record that decision")), (200 if res.get("ok") else 400)
