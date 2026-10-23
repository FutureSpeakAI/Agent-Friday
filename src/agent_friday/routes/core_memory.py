"""
Agent Friday — Memory card API (services/core_memory)

  GET   /api/core-memory              both files, Friday's recent changes, the review queue
  POST  /api/core-memory/add          {target, text}            the owner adds a line
  POST  /api/core-memory/edit         {target, index, text}     the owner rewrites a line
  POST  /api/core-memory/delete       {target, index}           the owner deletes a line
  POST  /api/core-memory/undo         {change_id}               undo one of Friday's changes
  POST  /api/core-memory/review       {id, keep: bool}          keep or discard a queued suggestion

Every write here is the owner's own, so it reaches every open conversation's
next turn at once (services/prompt_freeze).
"""
from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import public_result
from agent_friday.services import core_memory

core_memory_bp = Blueprint("core_memory", __name__)


def _body():
    return request.get_json(silent=True) or {}


@core_memory_bp.route("/api/core-memory", methods=["GET"])
@login_required
def get_core_memory():
    return jsonify(public_result({"ok": True, "card": core_memory.snapshot(),
                                  "changes": core_memory.recent_changes(20),
                                  "pending": core_memory.pending()},
                                 "Couldn't load the memory card"))


@core_memory_bp.route("/api/core-memory/add", methods=["POST"])
@login_required
def add_line():
    d = _body()
    return jsonify(public_result(core_memory.add(str(d.get("target") or ""), str(d.get("text") or ""),
                                                 actor="owner"), "Couldn't add that line"))


@core_memory_bp.route("/api/core-memory/edit", methods=["POST"])
@login_required
def edit_line():
    d = _body()
    return jsonify(public_result(core_memory.owner_set(str(d.get("target") or ""), d.get("index"),
                                                       str(d.get("text") or "")), "Couldn't change that line"))


@core_memory_bp.route("/api/core-memory/delete", methods=["POST"])
@login_required
def delete_line():
    d = _body()
    return jsonify(public_result(core_memory.owner_delete(str(d.get("target") or ""), d.get("index"),
                                                          wrong=bool(d.get("wrong"))),
                                 "Couldn't delete that line"))


@core_memory_bp.route("/api/core-memory/restore-messages", methods=["POST"])
@login_required
def restore_messages():
    """Put back into recall the messages a card correction retired (the ids
    the edit or delete returned)."""
    d = _body()
    ids = d.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 200 or not all(isinstance(i, str) and 0 < len(i) <= 80
                                                                       for i in ids):
        return jsonify({"ok": False, "error": "which messages?"}), 400
    return jsonify({"ok": True, "restored": core_memory.restore_superseded(ids)})


@core_memory_bp.route("/api/core-memory/undo", methods=["POST"])
@login_required
def undo_change():
    d = _body()
    return jsonify(public_result(core_memory.undo(str(d.get("change_id") or "")), "Couldn't undo that"))


@core_memory_bp.route("/api/core-memory/review", methods=["POST"])
@login_required
def review_suggestion():
    d = _body()
    return jsonify(public_result(core_memory.decide(str(d.get("id") or ""), bool(d.get("keep"))),
                                 "Couldn't record that"))
