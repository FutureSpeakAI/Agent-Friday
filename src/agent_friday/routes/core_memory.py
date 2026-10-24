"""
Agent Friday — Memory card API (services/core_memory)

  GET   /api/core-memory              both files, Friday's recent changes, the review queue
  POST  /api/core-memory/add          {target, text}            the owner adds a line
  POST  /api/core-memory/edit         {target, index, text}     the owner rewrites a line
  POST  /api/core-memory/delete       {target, index}           the owner deletes a line
  POST  /api/core-memory/undo         {change_id}               undo one of Friday's changes
  POST  /api/core-memory/review       {id, keep: bool, text?}   keep or discard a queued suggestion;
                                                                with text, keep the owner's edit of it
                                                                (/api/core-memory/decide is the same)
  POST  /api/core-memory/suppress     {id}                      "Don't suggest things like this"
  GET   /api/core-memory/suppressed                             the suppression rules and drop counts
  POST  /api/core-memory/suppressed/remove {id}                 remove one suppression rule

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
    return jsonify(public_result(core_memory.owner_delete(str(d.get("target") or ""), d.get("index")),
                                 "Couldn't delete that line"))


@core_memory_bp.route("/api/core-memory/undo", methods=["POST"])
@login_required
def undo_change():
    d = _body()
    return jsonify(public_result(core_memory.undo(str(d.get("change_id") or "")), "Couldn't undo that"))


@core_memory_bp.route("/api/core-memory/review", methods=["POST"])
@core_memory_bp.route("/api/core-memory/decide", methods=["POST"])
@login_required
def review_suggestion():
    d = _body()
    text = d.get("text")
    if text is not None and not isinstance(text, str):
        return jsonify({"ok": False, "error": "text must be a string"})
    return jsonify(public_result(core_memory.decide(str(d.get("id") or ""), bool(d.get("keep")), text),
                                 "Couldn't record that"))


@core_memory_bp.route("/api/core-memory/suppress", methods=["POST"])
@login_required
def suppress_suggestion():
    d = _body()
    return jsonify(public_result(core_memory.suppress(str(d.get("id") or "")), "Couldn't record that"))


@core_memory_bp.route("/api/core-memory/suppressed", methods=["GET"])
@login_required
def suppression_rules():
    return jsonify(public_result(core_memory.suppression_rules(), "Couldn't load the suppressed list"))


@core_memory_bp.route("/api/core-memory/suppressed/remove", methods=["POST"])
@login_required
def remove_suppression_rule():
    d = _body()
    return jsonify(public_result(core_memory.remove_suppression(str(d.get("id") or "")),
                                 "Couldn't remove that rule"))
