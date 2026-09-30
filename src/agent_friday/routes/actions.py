"""
Agent Friday — receipts for the changes Friday makes to the owner's things.

  GET   /api/actions/receipts               the newest receipts (?limit=, max 100)
  GET   /api/actions/receipts/<id>          one receipt
  POST  /api/actions/receipts/<id>/undo     the owner's own Undo: puts it back now

The Undo here is the owner acting through Friday's page, like the Messages
workspace's own buttons, so it runs at once, mail included. When Friday itself
wants to put mail back, its undo_action tool raises a card instead
(services/item_actions).
"""
import re

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import action_journal

actions_bp = Blueprint("actions", __name__)

_RID = re.compile(r"^rcpt_[0-9a-f]{8,40}$")


@actions_bp.route("/api/actions/receipts", methods=["GET"])
@login_required
def receipts_list():
    try:
        limit = min(100, max(1, int(request.args.get("limit") or 20)))
    except ValueError:
        limit = 20
    return jsonify({"status": "ok",
                    "receipts": [action_journal.public_view(r) for r in action_journal.recent(limit)]})


@actions_bp.route("/api/actions/receipts/<rid>", methods=["GET"])
@login_required
def receipt_read(rid):
    if not _RID.match(rid or ""):
        return jsonify({"status": "error", "error": "not a receipt id"}), 400
    rec = action_journal.get(rid)
    if rec is None:
        return jsonify({"status": "error", "error": "no such receipt"}), 404
    return jsonify({"status": "ok", "receipt": action_journal.public_view(rec)})


@actions_bp.route("/api/actions/receipts/<rid>/undo", methods=["POST"])
@login_required
def receipt_undo(rid):
    if not _RID.match(rid or ""):
        return jsonify({"status": "error", "error": "not a receipt id"}), 400
    from agent_friday.services import item_actions
    try:
        out = item_actions.undo(rid, by_owner=True, requested_by="owner:ui")
    except item_actions.Refused as e:
        return jsonify({"status": "error", "error": e.user_message}), 409
    return jsonify({"status": "ok", "result": out})
