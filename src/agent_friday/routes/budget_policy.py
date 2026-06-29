"""
Budget policy routes.

GET  /api/budget/status/<workspace>    — remaining budget for a workspace
GET  /api/budget/status                — all workspaces
POST /api/budget/policy                — set/update a budget policy
GET  /api/budget/policies              — list all policies
POST /api/budget/hard-stop/<worker_id> — emergency kill overbudget worker
"""
import traceback

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import budget_enforcer as be

budget_bp = Blueprint("budget_policy", __name__)


@budget_bp.route("/api/budget/status", methods=["GET"])
@login_required
def budget_status_all():
    try:
        policies = be.get_all_policies()
        summaries = [be.budget_status(p["workspace"]) for p in policies]
        if not summaries:
            summaries = [be.budget_status("default")]
        return jsonify({"ok": True, "budgets": summaries})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@budget_bp.route("/api/budget/status/<workspace>", methods=["GET"])
@login_required
def budget_status_workspace(workspace):
    try:
        return jsonify({"ok": True, "budget": be.budget_status(workspace)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@budget_bp.route("/api/budget/policies", methods=["GET"])
@login_required
def list_policies():
    try:
        return jsonify({"ok": True, "policies": be.get_all_policies()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@budget_bp.route("/api/budget/policy", methods=["POST"])
@login_required
def set_policy():
    data = request.get_json(silent=True) or {}
    workspace = data.get("workspace", "default")
    try:
        policy = be.set_policy(
            workspace=workspace,
            monthly_cap_mψ=int(data.get("monthly_cap_mψ", 1_000_000)),
            per_task_cap_mψ=int(data.get("per_task_cap_mψ", 100_000)),
            warn_pct=int(data.get("warn_pct", 80)),
        )
        return jsonify({"ok": True, "policy": policy})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@budget_bp.route("/api/budget/hard-stop/<worker_id>", methods=["POST"])
@login_required
def hard_stop(worker_id):
    try:
        ok = be.enforce_hard_stop(worker_id)
        return jsonify({"ok": ok})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
