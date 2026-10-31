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

  GET   /api/memory/search  ?q=&kinds=a,b&since=&until=&superseded=1&limit=
                                      the owner's search over everything Friday keeps
  GET   /api/memory/why     ?kind=&id=  where Friday knows one result from

Every write here is the owner's own, so it reaches every open conversation's
next turn at once (services/prompt_freeze). The two reads are the owner's
only (principal "user"; an observer or any other is refused), the parameters are bounded, and
neither the query nor a result is logged (services/memory_search).
"""
from flask import Blueprint, g, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import public_result
from agent_friday.services import core_memory

core_memory_bp = Blueprint("core_memory", __name__)


def _body():
    return request.get_json(silent=True) or {}


def _not_owner():
    """Only the owner's own principal reads the owner's memory: an observer, or any other principal
    login_required lets through, is refused (as routes/lanes, routes/traces and the others check it)."""
    try:
        return (getattr(g, "friday_principal", "user") or "user") != "user"
    except Exception:
        return True


def _flag(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@core_memory_bp.route("/api/memory/search", methods=["GET"])
@login_required
def memory_search_route():
    if _not_owner():
        return jsonify({"ok": False, "error": "Only the owner can search Friday's memory."}), 403
    from agent_friday.services import memory_search as ms
    a = request.args
    try:
        q, kinds, since, until, limit = ms.check_params(a.get("q"), a.get("kinds"), a.get("since"),
                                                        a.get("until"), a.get("limit"))
    except ms.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(public_result(ms.search(q, kinds=kinds, since=since, until=until,
                                           include_superseded=_flag(a.get("superseded")), limit=limit),
                                 "Couldn't search memory"))


@core_memory_bp.route("/api/memory/why", methods=["GET"])
@login_required
def memory_why_route():
    if _not_owner():
        return jsonify({"ok": False, "error": "Only the owner can read where Friday knows something from."}), 403
    from agent_friday.services import memory_search as ms
    try:
        kind, item_id = ms.check_why(request.args.get("kind"), request.args.get("id"))
    except ms.BadRequest as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(public_result(ms.why(kind, item_id), "Couldn't look that up"))


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
