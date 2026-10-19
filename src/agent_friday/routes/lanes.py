"""The four places (Needs you, Activity, Updates, Receipts & traces), as the panel reads them.

GET  /api/lanes/summary               one small poll: counts for the top-bar chip and the badge
GET  /api/lanes/needs-you             approval batches and singles, and the other blocking items
GET  /api/lanes/activity              every task (model, status, trace link, queue position, Stop)
GET  /api/lanes/updates[?today=1]     the digest of results and FYIs
GET  /api/lanes/receipts[?task=<id>]  recent reasoning traces (pulled; content is /api/traces/<id>)
GET  /api/lanes/why/<notice_id>       why a notice reached you the way it did
POST /api/lanes/quiet                 {"until_morning": true|false}: "quiet until morning" on or off
GET  /api/approvals/batches           pending approvals grouped into batch cards
POST /api/approvals/batches/<id>/decide   {"decision", "approval_ids": [...]}: decides exactly those

Every read here is a view over state that already exists (services/activity_lane). A batch decision
runs each listed approval through the same gate as a single card, with the same screen-click rule.
Traces and receipts are never pushed: these routes are pulled by the page when you open the item.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import activity_lane as lane
from agent_friday.services import approval_batches as batches
from agent_friday.services import interruption_policy as ip
from agent_friday.services import screen_click as _screen_click

lanes_bp = Blueprint("lanes", __name__)


def _local_only():
    """None for the local user; otherwise a refusal (traces never leave this PC)."""
    try:
        from flask import g
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "traces and receipts are only served to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "traces and receipts are only served on this machine"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    return None


@lanes_bp.route("/api/lanes/summary", methods=["GET"])
@login_required
def summary():
    n, a = lane.needs_you(), lane.activity()
    u = lane.updates(today_only=True)
    try:
        from agent_friday.services import gmail_send as _gs
        held = len(_gs.held())          # sends waiting out their undo window (the top bar keeps Activity in sight)
    except Exception:
        held = 0
    return jsonify({"needs_you": n["count"], "approvals": n["approvals"], "summary": n["summary"],
                    "running": a["running"], "queued": a["queued"], "activity": a["count"],
                    "updates": u["count"], "held_sends": held, "quiet": ip.quiet_state()})


@lanes_bp.route("/api/lanes/needs-you", methods=["GET"])
@login_required
def needs_you():
    return jsonify(lane.needs_you())


@lanes_bp.route("/api/lanes/activity", methods=["GET"])
@login_required
def activity():
    return jsonify(lane.activity())


@lanes_bp.route("/api/lanes/updates", methods=["GET"])
@login_required
def updates():
    return jsonify(lane.updates(today_only=request.args.get("today") in ("1", "true")))


@lanes_bp.route("/api/lanes/receipts", methods=["GET"])
@login_required
def receipts():
    refused = _local_only()
    if refused is not None:
        return refused
    try:
        limit = int(request.args.get("limit", 30))
    except (TypeError, ValueError):
        limit = 30
    return jsonify(lane.receipts(limit=limit, task_id=(request.args.get("task") or "").strip()))


@lanes_bp.route("/api/lanes/why/<notice_id>", methods=["GET"])
@login_required
def why(notice_id):
    refused = _local_only()
    if refused is not None:
        return refused
    out = lane.why(notice_id)
    return jsonify(out), (200 if out.get("ok") else 404)


@lanes_bp.route("/api/lanes/quiet", methods=["GET", "POST"])
@login_required
def quiet():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        from agent_friday.core import _save_settings
        if data.get("until_morning"):
            ip.turn_on_quiet("page")          # a click on the page: the notice and the ledger line still happen
        else:
            _save_settings({"quiet_until_ts": 0})
    return jsonify(ip.status())


@lanes_bp.route("/api/approvals/batches", methods=["GET"])
@login_required
def list_batches():
    return jsonify({"ok": True, **batches.pending_plan_view()})


@lanes_bp.route("/api/approvals/batches/<batch_id>/decide", methods=["POST"])
@login_required
def decide_batch(batch_id):
    data = request.get_json(silent=True) or {}
    decision = (data.get("decision") or "").strip().lower()
    if decision not in ("approve", "deny"):
        return jsonify({"ok": False, "error": "decision must be 'approve' or 'deny'"}), 400
    ids = data.get("approval_ids")
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "name the approvals this batch lists (approval_ids)"}), 400
    if decision == "approve":
        # A batch is approved only by a press on its card in Friday's page on this PC
        # (routes/goals._press_required has the same rule for one card). Declining is open.
        refused = _screen_click.press_refusal(
            request, "Approve this with a press on the card, on the computer Friday runs on.")
        if refused is not None:
            return refused
    out = batches.decide_batch(
        batch_id, ids, decision,
        decided_by=_screen_click.decided_by(request, data.get("decided_by", "owner"), for_ids=ids),
        note=str(data.get("note") or ""))
    return jsonify(out), (200 if out.get("ok") else 409)
