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
                                                   -> {"won": bool, "already_decided": bool}
  GET    /api/approvals/events                    SSE: pending list, then pending/resolved

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
from agent_friday.routes._errors import api_error, public_result

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
        return api_error(e, "Couldn't create the goal", 400, shape="ok")
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
        return api_error(e, "Couldn't update the goal", 409, shape="ok")
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
        return jsonify(public_result(result, "Couldn't run the milestone")), 404 if "not found" in (result.get("error") or "") else 409
    return jsonify(public_result(result, "Couldn't run the milestone")), 200


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
    return jsonify(public_result({"ok": True, "review": review}, "Couldn't load the review"))


@goals_bp.route("/api/goals/review/run", methods=["POST"])
@login_required
def run_review_route():
    result = _goals.run_weekly_review()
    return jsonify({"ok": True, **result})


# ═══════════════════════════════════════════════════════════════════════════
#  General approval queue (services/approvals.py)
# ═══════════════════════════════════════════════════════════════════════════

# AN APPROVAL CARD IS NOT AN ORDINARY READ.
#
# The module header's "reads are open" split is right for goals and routines -
# a status list is not sensitive. An approval is different: it is the full
# description of an action Friday is waiting to take, including whatever
# argument it carries, and the queue is a map of what this machine is about to
# do next. `/decide` was already gated; reading the card was not, so the
# contents were available to any non-loopback caller while the decision was
# protected. Loopback - the desktop app - is unaffected either way.
@goals_bp.route("/api/approvals", methods=["GET"])
@login_required
def list_approvals_route():
    status = request.args.get("status")
    subject_type = request.args.get("subject_type")
    kind = request.args.get("kind")
    if status and status not in _approvals.STATUSES:
        return jsonify({"ok": False, "error": f"status must be one of {sorted(_approvals.STATUSES)}"}), 400
    return jsonify({"ok": True,
                    "approvals": _approvals.list_approvals(status=status, subject_type=subject_type, kind=kind)})


@goals_bp.route("/api/approvals/<approval_id>", methods=["GET"])
@login_required
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
    rec, won = _approvals.decide_with_outcome(
        approval_id, decision, decided_by=data.get("decided_by", "owner"),
        note=data.get("note", ""))
    if not rec:
        return jsonify({"ok": False, "error": "not found"}), 404
    # `won` is False when the card was already decided (in another tab, by
    # voice, by a text reply) or had expired: this request changed nothing,
    # and the page says so instead of implying its click did.
    return jsonify({"ok": True, "approval": rec, "won": won, "already_decided": not won})


#: Seconds between heartbeats on a quiet approvals stream.
BEAT_S = 20


@goals_bp.route("/api/approvals/events", methods=["GET"])
@login_required
def approval_events_route():
    """SSE: every open Friday page's feed of approval cards
    (services/approval_feed.py). The first frame is the full pending list, so
    a page that opens or reconnects later has every waiting card; after that,
    `pending` and `resolved` frames as cards are created and decided, and a
    `beat` frame when nothing has happened for BEAT_S seconds.

    The pages of one address share one stream between them (the browser
    allows about six open connections per address, and a stream per tab would
    use them up): one tab holds it and relays to the others, see
    fridayApprovalFeed in index.html."""
    import json as _json
    import queue as _queue
    from flask import Response, stream_with_context
    from agent_friday.services import approval_feed

    def stream():
        # Subscribe BEFORE reading the list: a card created in between then
        # arrives twice (the page keys cards by id) rather than not at all.
        q = approval_feed.subscribe()
        try:
            pending = _approvals.list_approvals(status="pending")
            yield "data: " + _json.dumps({"type": "snapshot", "pending": pending},
                                         default=str) + "\n\n"
            while True:
                try:
                    evt = q.get(timeout=BEAT_S)
                except _queue.Empty:
                    # A data frame, not an SSE comment: the tab holding the
                    # stream passes it on, and the other tabs of that address
                    # take the stream over when they stop hearing it.
                    yield 'data: {"type": "beat"}\n\n'
                    continue
                if evt.get("type") == "resync":
                    evt = {"type": "snapshot",
                           "pending": _approvals.list_approvals(status="pending")}
                yield "data: " + _json.dumps(evt, default=str) + "\n\n"
        finally:
            approval_feed.unsubscribe(q)

    return Response(stream_with_context(stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Governance grants: outward powers for work nobody is watching ──────────
# A scheduled or background job may take an outward action (send, post,
# change a calendar, run a non-read command) only with a grant the owner made
# here: named tools, one job's scope, an expiry and a use count. Everything
# else it attempts waits on an approval card. See governance/action_gate.py.

@goals_bp.route("/api/governance/grants", methods=["GET"])
@login_required
def list_governance_grants():
    from agent_friday.governance import action_gate
    return jsonify({"grants": action_gate.list_grants()})


@goals_bp.route("/api/governance/grants", methods=["POST"])
@login_required
def create_governance_grant():
    from agent_friday.governance import action_gate
    body = request.get_json(silent=True) or {}
    try:
        g = action_gate.create_grant(
            tools=list(body.get("tools") or []), scope=str(body.get("scope") or ""),
            expires_in_seconds=float(body.get("expires_in_seconds") or 0),
            max_uses=int(body.get("max_uses", 1)), created_by="owner",
            note=str(body.get("note") or ""))
    except (TypeError, ValueError) as e:
        return api_error(e, "Couldn't create the grant", 400, shape="bare")
    return jsonify({"grant": g}), 201


@goals_bp.route("/api/governance/receipts", methods=["GET"])
@login_required
def governance_receipts():
    """What Friday did: every checkpoint decision in a window, each with its
    signature checked, plus the background tasks that finished in it.
    Read-only. `since` / `until` are epoch seconds; `since` defaults to 24
    hours ago. `date=YYYY-MM-DD` with `tz_offset_min` (the browser's
    getTimezoneOffset) selects that local day instead."""
    import calendar as _cal
    import time as _t
    from datetime import datetime as _dt
    from agent_friday.services import morning_receipt
    now = _t.time()
    try:
        day = (request.args.get("date") or "").strip()
        if day:
            # Local midnight in UTC = UTC midnight of that date plus the
            # browser's offset (getTimezoneOffset is UTC minus local).
            off = int(request.args.get("tz_offset_min") or 0)
            start = _cal.timegm(_dt.strptime(day, "%Y-%m-%d").timetuple()) + off * 60
            since, until = float(start), float(start + 86400)
        else:
            since = float(request.args.get("since") or (now - 86400))
            until = float(request.args["until"]) if request.args.get("until") else now
    except (TypeError, ValueError):
        return jsonify({"error": "since/until must be epoch seconds; date must be YYYY-MM-DD"}), 400
    if until < since:
        return jsonify({"error": "until is before since"}), 400
    return jsonify(morning_receipt.build(since, until))


@goals_bp.route("/api/governance/grants/<grant_id>", methods=["DELETE"])
@login_required
def revoke_governance_grant(grant_id):
    from agent_friday.governance import action_gate
    return jsonify({"revoked": action_gate.revoke_grant(grant_id)})


@goals_bp.route("/api/governance/outward-tools", methods=["GET"])
@login_required
def list_outward_tools():
    """The actions a grant can cover: every registered tool the governance
    checkpoint classifies as outward, in its own words. Read from the live
    registry and the checkpoint's classifier, so the Grants screen offers
    exactly what the gate would otherwise hold."""
    from agent_friday.governance import action_gate
    from agent_friday.services import agent as _agent
    from agent_friday.services import desktop_grants as _dg
    out = []
    for name in sorted(_agent.CLAUDE_TOOL_HANDLERS):
        if _dg.is_desktop_tool(name):
            # Desktop control is granted per app (Computer Control), not per
            # job, and classifying it here would probe the live desktop.
            continue
        probe = {"publish_at": "x"} if name == "content_create_post" else \
                {"command": "Remove-Item x"} if name == "run_command" else {}
        try:
            klass, why = action_gate.classify(name, probe)
        except Exception:
            klass, why = action_gate.OUTWARD, "unclassified"
        if klass == action_gate.OUTWARD and name not in action_gate.SELF_GATED:
            out.append({"name": name, "why": why, "label": _GRANT_LABELS.get(name)
                        or _connector_label(name)})
    return jsonify({"tools": out})


#: Plain words for the Grants screen. A tool missing here still appears, under
#: a label built from its name, so a new outward tool is never hidden.
_GRANT_LABELS = {
    "create_calendar_event": "Create calendar events and send invites",
    "update_calendar_event": "Change calendar events",
    "annotate_calendar_events": "Add notes to calendar events",
    "content_create_post": "Schedule a new social post",
    "content_schedule_post": "Schedule an existing social post",
    "delete_task": "Delete tasks",
    "install_package": "Install software",
    "run_command": "Run commands that change things",
    "run_sandboxed": "Run Python code in the sandbox",
    "spawn_interactive_session": "Start a terminal session",
    "send_to_session": "Type into an open terminal session",
}


def _connector_label(name):
    words = name[4:].split("_") if name.startswith("mcp_") else name.split("_")
    if name.startswith("mcp_") and len(words) > 1:
        return "%s: %s" % (words[0].capitalize(), " ".join(words[1:]))
    return " ".join(words).capitalize()
