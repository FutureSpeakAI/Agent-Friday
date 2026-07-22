"""
Agent Friday — Durable Goals & Approvals API
FutureSpeak.AI · Asimov's Mind

V6_WHOLENESS_SPEC.md §4 Phase 5 / AUTONOMY_SPEC.md §8 (A3). API-complete;
GoalsWS (the UI panel) is deferred to A8 per AUTONOMY_SPEC §2 — this build is
backend/API-first.

  GET    /api/goals                              list (?status=)
  POST   /api/goals                               create
  GET    /api/goals/<goal_id>                     detail
  PATCH  /api/goals/<goal_id>                     update mutable fields
  POST   /api/goals/<goal_id>/transition          {"status": "..."} — state-machine move
  POST   /api/goals/<goal_id>/milestones          add a milestone
  POST   /api/goals/<goal_id>/milestones/<mid>/run   advance one attempt-cycle now
  GET    /api/goals/<goal_id>/receipts            signed proof-of-work receipts
  GET    /api/goals/review                        latest weekly review doc
  POST   /api/goals/review/run                    force-run the weekly review job now

General approval queue (services/approvals.py) — not goal-specific, but
exposed from this Blueprint per AUTONOMY_SPEC A3's "routes/goals.py — CRUD +
approve + receipts + review":

  GET    /api/approvals                           list (?status=, ?subject_type=, ?kind=)
  GET    /api/approvals/<approval_id>              detail
  POST   /api/approvals/<approval_id>/decide       {"decision": "approve"|"deny", "note"?}

Reads are open (mirrors routes/scheduler.py's own read/mutate split);
mutations require an authenticated session (@login_required — loopback
callers, i.e. the desktop app itself, are always trusted).

Note on run_milestone_route: this executes SYNCHRONOUSLY within the request,
matching orchestrator.delegate()'s own "spawn, block until done, return"
philosophy (V6 §2 substrate table lists orchestrator.delegate as exactly this
shape). The PRIMARY autonomous execution path for real goal progress is the
scheduler-driven services.goals.run_due_milestones tick (registered as the
'goal_milestones_tick' builtin), not this endpoint — this route exists as an
on-demand "run it now" utility (the same relationship
POST /api/schedules/<id>/run-now has to the scheduler's own tick loop, except
that one dispatches to a background thread and returns immediately; this one
is intentionally synchronous so a caller gets the actual outcome — done /
escalated / pending_approval / budget_exceeded — in the response body without
a second poll).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import goals as _goals
from agent_friday.services import approvals as _approvals

goals_bp = Blueprint("goals", __name__)


# ═══════════════════════════════════════════════════════════════════════════
#  Goals CRUD
# ═══════════════════════════════════════════════════════════════════════════

@goals_bp.route("/api/goals", methods=["GET"])
def list_goals_route():
    status = request.args.get("status")
    if status and status not in _goals.STATUSES:
        return jsonify({"ok": False, "error": f"status must be one of {sorted(_goals.STATUSES)}"}), 400
    return jsonify({"ok": True, "goals": _goals.list_goals(status=status)})


@goals_bp.route("/api/goals", methods=["POST"])
@login_required
def create_goal_route():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "title is required"}), 400
    verification_mode = data.get("verification_mode", "auto")
    if verification_mode not in ("auto", "manual"):
        return jsonify({"ok": False, "error": "verification_mode must be 'auto' or 'manual'"}), 400
    status = data.get("status", "proposed")
    if status not in _goals.STATUSES:
        return jsonify({"ok": False, "error": f"status must be one of {sorted(_goals.STATUSES)}"}), 400
    try:
        goal = _goals.create_goal(
            title=title, description=data.get("description", ""),
            owner=data.get("owner", "owner"), deadline=data.get("deadline"),
            milestones=data.get("milestones"), success_criteria=data.get("success_criteria", ""),
            linked_schedules=data.get("linked_schedules"), linked_chains=data.get("linked_chains"),
            budget_cap_mψ=data.get("budget_cap_mψ", 0),
            approval_required=bool(data.get("approval_required", False)),
            verification_mode=verification_mode, status=status,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "goal": goal}), 201


@goals_bp.route("/api/goals/<goal_id>", methods=["GET"])
def get_goal_route(goal_id):
    goal = _goals.get_goal(goal_id)
    if not goal:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "goal": goal})


@goals_bp.route("/api/goals/<goal_id>", methods=["PATCH", "POST"])
@login_required
def update_goal_route(goal_id):
    patch = request.get_json(silent=True) or {}
    goal = _goals.update_goal(goal_id, patch)
    if not goal:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "goal": goal})


@goals_bp.route("/api/goals/<goal_id>/transition", methods=["POST"])
@login_required
def transition_goal_route(goal_id):
    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip()
    if new_status not in _goals.STATUSES:
        return jsonify({"ok": False, "error": f"status must be one of {sorted(_goals.STATUSES)}"}), 400
    try:
        goal = _goals.transition_goal(goal_id, new_status, reason=data.get("reason", ""))
    except KeyError:
        return jsonify({"ok": False, "error": "not found"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "goal": goal})


@goals_bp.route("/api/goals/<goal_id>/milestones", methods=["POST"])
@login_required
def add_milestone_route(goal_id):
    data = request.get_json(silent=True) or {}
    if not (data.get("name") or "").strip():
        return jsonify({"ok": False, "error": "milestone name is required"}), 400
    goal = _goals.add_milestone(goal_id, data)
    if not goal:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "goal": goal}), 201


@goals_bp.route("/api/goals/<goal_id>/milestones/<milestone_id>/run", methods=["POST"])
@login_required
def run_milestone_route(goal_id, milestone_id):
    result = _goals.run_milestone(goal_id, milestone_id)
    if "error" in result and result.get("status") is None:
        # goal/milestone not found, or goal not active — a request-shape problem
        return jsonify(result), 404 if "not found" in (result.get("error") or "") else 409
    return jsonify(result), 200


@goals_bp.route("/api/goals/<goal_id>/receipts", methods=["GET"])
def get_receipts_route(goal_id):
    receipts = _goals.get_receipts(goal_id)
    if receipts is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "receipts": receipts, "count": len(receipts)})


# ═══════════════════════════════════════════════════════════════════════════
#  Weekly review
# ═══════════════════════════════════════════════════════════════════════════

@goals_bp.route("/api/goals/review", methods=["GET"])
def get_review_route():
    review = _goals.latest_review()
    if not review:
        return jsonify({"ok": True, "review": None})
    return jsonify({"ok": True, "review": review})


@goals_bp.route("/api/goals/review/run", methods=["POST"])
@login_required
def run_review_route():
    result = _goals.run_weekly_review()
    return jsonify({"ok": True, **result})


# ═══════════════════════════════════════════════════════════════════════════
#  General approval queue (services/approvals.py)
# ═══════════════════════════════════════════════════════════════════════════

@goals_bp.route("/api/approvals", methods=["GET"])
def list_approvals_route():
    status = request.args.get("status")
    subject_type = request.args.get("subject_type")
    kind = request.args.get("kind")
    if status and status not in _approvals.STATUSES:
        return jsonify({"ok": False, "error": f"status must be one of {sorted(_approvals.STATUSES)}"}), 400
    return jsonify({"ok": True,
                    "approvals": _approvals.list_approvals(status=status, subject_type=subject_type, kind=kind)})


@goals_bp.route("/api/approvals/<approval_id>", methods=["GET"])
def get_approval_route(approval_id):
    appr = _approvals.get_approval(approval_id)
    if not appr:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "approval": appr})


@goals_bp.route("/api/approvals/<approval_id>/decide", methods=["POST"])
@login_required
def decide_approval_route(approval_id):
    data = request.get_json(silent=True) or {}
    decision = (data.get("decision") or "").strip().lower()
    if decision not in ("approve", "deny"):
        return jsonify({"ok": False, "error": "decision must be 'approve' or 'deny'"}), 400
    rec = _approvals.decide(approval_id, decision, decided_by=data.get("decided_by", "owner"),
                            note=data.get("note", ""))
    if not rec:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "approval": rec})
