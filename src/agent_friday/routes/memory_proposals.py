"""
Memory proposal routes — the manual door into services/memory_proposals.py.

The service module -- propose()/pending()/approve()/reject()/state() --
needs a caller in the running app, and its own docstring is explicit that
this is meant to be manual-first: "propose() is something the user RUNS, and
its output is shown to them before any of it becomes durable." These routes
are that door. The Meetings view (static/meetings_ws.js) reviews a meeting's
suggestions through them: Keep, Edit and keep, Not now, and "Don't suggest
things like this".

  POST /api/memory/proposals/propose    — read a day, stage candidate facts
  GET  /api/memory/proposals/pending    — facts awaiting review
  POST /api/memory/proposals/approve    — promote reviewed facts to durable memory
                                          ({fact_ids: [id], text}: keep the owner's edit of one)
  POST /api/memory/proposals/reject     — discard proposals
  POST /api/memory/proposals/suppress   — {fact_id}: discard it and don't suggest things like it
                                          (rules are listed and removed under /api/core-memory/suppressed)
  GET  /api/memory/proposals/state      — counts + whether the seat is usable
  GET  /api/memory/discarded            — discarded suggestions (owner only)
  POST /api/memory/discarded/<id>/undo  — allow one again (owner only)
"""
import traceback

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import memory_proposals as mp
from agent_friday.services import screen_click
from agent_friday.routes._errors import api_error, public_result

memory_proposals_bp = Blueprint("memory_proposals", __name__)


@memory_proposals_bp.route("/api/memory/proposals/propose", methods=["POST"])
@login_required
def api_memory_proposals_propose():
    data = request.get_json(silent=True) or {}
    try:
        result = mp.propose(day=data.get("day"))
        return jsonify(public_result(result, "Couldn't propose the memory"))
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't propose the memory", shape="ok", key="reason")


@memory_proposals_bp.route("/api/memory/proposals/pending", methods=["GET"])
@login_required
def api_memory_proposals_pending():
    try:
        day = request.args.get("day")
        limit = request.args.get("limit", default=200, type=int)
        return jsonify({"ok": True, "facts": mp.pending(day=day, limit=limit)})
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't load the pending memories", shape="ok", key="reason")


@memory_proposals_bp.route("/api/memory/proposals/approve", methods=["POST"])
@login_required
def api_memory_proposals_approve():
    data = request.get_json(silent=True) or {}
    if mp.any_from_meeting(data.get("fact_ids") or []) and not screen_click.screen_session(request):
        # What a meeting suggested becomes part of what Friday knows about the owner, and so of what a cloud
        # model may later be told: only a click on the screen keeps it.
        return jsonify({"ok": False, "reason": "A suggestion from a meeting is kept only from the Meetings view in the "
                                              "Friday window."}), 403
    ids = data.get("fact_ids") or []
    text = data.get("text")
    edits = None
    if text is not None:
        # "Edit and keep": the owner's own wording of exactly one suggestion.
        if not isinstance(text, str) or len(ids) != 1:
            return jsonify({"ok": False, "reason": "An edit keeps one suggestion: send one fact id and the text."})
        edits = {str(ids[0]): text}
    try:
        return jsonify(mp.approve(ids, edits=edits) if edits else mp.approve(ids))
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't approve the memory", shape="ok", key="reason")


@memory_proposals_bp.route("/api/memory/proposals/suppress", methods=["POST"])
@login_required
def api_memory_proposals_suppress():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(public_result(mp.suppress(str(data.get("fact_id") or "")), "Couldn't record that"))
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't record that", shape="ok", key="reason")


@memory_proposals_bp.route("/api/memory/proposals/reject", methods=["POST"])
@login_required
def api_memory_proposals_reject():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(mp.reject(data.get("fact_ids") or []))
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't reject the memory", shape="ok", key="reason")


def _owner_only():
    """The discarded-suggestion list opens sealed labels: the local owner's
    principal ("user") only, as the other owner-only routes check it."""
    try:
        from flask import g
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"ok": False, "reason": "Discarded suggestions are only shown to the owner."}), 403
    except Exception:
        return jsonify({"ok": False, "reason": "Could not establish who is asking."}), 403
    return None


@memory_proposals_bp.route("/api/memory/discarded", methods=["GET"])
@login_required
def api_memory_discarded():
    """Suggestions the owner discarded (a card suggestion's Discard, a proposal's
    reject), newest first, each with its label opened for the owner."""
    refused = _owner_only()
    if refused:
        return refused
    try:
        from agent_friday.services import tombstones
        limit = request.args.get("limit", default=100, type=int) or 100
        limit = max(1, min(limit, 200))
        return jsonify({"ok": True, "discarded": tombstones.list_discarded(limit=limit),
                        "note": "Allow again lets Friday suggest it again later; it does not put it "
                                "back in the review queue."})
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't load the discarded suggestions", shape="ok", key="reason")


@memory_proposals_bp.route("/api/memory/discarded/<discard_id>/undo", methods=["POST"])
@login_required
def api_memory_discarded_undo(discard_id):
    """Allow a discarded suggestion again: its tombstone is removed. Nothing is
    restored to the queue; a later pass may suggest it again."""
    refused = _owner_only()
    if refused:
        return refused
    if len(discard_id or "") > 32:
        return jsonify({"ok": False, "reason": "No such discarded suggestion."}), 404
    try:
        from agent_friday.services import tombstones
        r = tombstones.undo_discard(discard_id)
        if not r.get("ok"):
            return jsonify({"ok": False, "reason": r.get("error") or "That did not work."}), \
                (404 if "no such" in str(r.get("error") or "") else 500)
        out = {"ok": True, "removed": int(r.get("removed") or 0),
               "note": "Friday may suggest it again later. It was not put back in the queue."}
        if r.get("warning"):
            out["warning"] = r["warning"]
        return jsonify(out)
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't allow the suggestion again", shape="ok", key="reason")


@memory_proposals_bp.route("/api/memory/proposals/state", methods=["GET"])
@login_required
def api_memory_proposals_state():
    try:
        return jsonify(public_result({"ok": True, **mp.state()}, "Couldn't load the memory state"))
    except Exception as e:
        traceback.print_exc()
        return api_error(e, "Couldn't load the memory state", shape="ok", key="reason")
