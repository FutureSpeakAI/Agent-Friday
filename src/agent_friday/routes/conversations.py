"""Conversation CRUD — docs/design/conversations-and-concurrency.md §3.1.

Everything here is scoped to one conversation. That is the whole point: the
endpoint this replaces, `/api/chat/clear`, was global, so "+ New Chat" deleted
the thread he was trying to come back to.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.services import conversations as conv_store

conversations_bp = Blueprint("conversations", __name__)


def _running_for(cid: str) -> int:
    """How much live work reports into this conversation.

    Owner stamping lands in a later step of the build order; until then a
    conversation reports 0 rather than guessing, because a wrong count here
    would be read as "nothing is running" and that is the failure this whole
    effort exists to remove.
    """
    try:
        from agent_friday.core import PROCESSES, PROCESSES_LOCK
        with PROCESSES_LOCK:
            return sum(1 for p in PROCESSES.values()
                       if p.get("conversation_id") == cid
                       and p.get("status") == "running")
    except Exception:
        return 0


def _view(c: dict) -> dict:
    return {
        "id": c.get("id"),
        "title": c.get("title") or "New chat",
        "created_at": c.get("created_at"),
        "last_active_at": c.get("last_active_at"),
        "seat": c.get("seat"),
        "status": c.get("status") or "active",
        "totals": c.get("totals") or {},
        "running": _running_for(c.get("id")),
    }


@conversations_bp.route("/api/conversations", methods=["GET", "POST"])
def conversations_index():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        conv = conv_store.create(
            title=(body.get("title") or "New chat"),
            seat=body.get("seat") or None)
        return jsonify({"status": "ok", "conversation": _view(conv)})

    conv_store.ensure_main()      # first call also performs the legacy import
    # Archived means HIDDEN. The DELETE branch below archives rather than
    # destroys precisely so the address running work reports into stays alive,
    # and its own comment says "the list view hides it" — but this defaulted to
    # including them, so archiving a chat left it sitting in the switcher and
    # the only visible effect of "delete" was nothing at all. Pass
    # ?archived=1 to see them.
    include_archived = str(request.args.get("archived", "0")).lower() not in ("0", "false")
    return jsonify({
        "status": "ok",
        "main_id": conv_store.MAIN_ID,
        "conversations": [_view(c) for c in conv_store.list_all(include_archived)],
    })


@conversations_bp.route("/api/conversations/<cid>", methods=["GET", "PATCH", "DELETE"])
def conversation_detail(cid):
    conv = conv_store.load(cid)
    if conv is None:
        return jsonify({"status": "error", "message": "no such conversation"}), 404

    if request.method == "GET":
        return jsonify({"status": "ok", "conversation": _view(conv)})

    if request.method == "PATCH":
        body = request.get_json(silent=True) or {}
        fields = {}
        if "title" in body:
            fields["title"] = str(body["title"])[:200]
        if "status" in body and body["status"] in ("active", "archived"):
            fields["status"] = body["status"]
        if "seat" in body:
            # null clears the binding and returns this conversation to the
            # global default, resolved per turn.
            seat = body["seat"]
            fields["seat"] = None if not seat else {
                "model": str((seat or {}).get("model") or ""),
                "provider": str((seat or {}).get("provider") or "") or None,
            }
            if fields["seat"] and not fields["seat"]["model"]:
                return jsonify({"status": "error",
                                "message": "seat needs a model"}), 400
        return jsonify({"status": "ok",
                        "conversation": _view(conv_store.patch(cid, **fields))})

    # DELETE — archive, never destroy.
    #
    # §3.5: a conversation is the address that running work reports back to, so
    # deleting one with work in flight orphans that work. Archiving keeps the
    # address alive; the list view hides it.
    running = _running_for(cid)
    if running:
        return jsonify({
            "status": "error", "running": running,
            "message": (f"{running} job(s) still report into this conversation. "
                        f"Archive it instead, or cancel the work first."),
        }), 409
    conv_store.patch(cid, status="archived")
    return jsonify({"status": "ok", "archived": True})


@conversations_bp.route("/api/conversations/<cid>/messages", methods=["GET"])
def conversation_messages(cid):
    if conv_store.load(cid) is None:
        return jsonify({"status": "error", "message": "no such conversation"}), 404
    try:
        limit = int(request.args.get("limit", 0)) or None
    except (TypeError, ValueError):
        limit = None
    msgs = conv_store.messages(cid, limit)
    return jsonify({"status": "ok", "count": len(msgs), "messages": msgs})


@conversations_bp.route("/api/conversations/<cid>/clear", methods=["POST"])
def conversation_clear(cid):
    """Scoped clear. The global /api/chat/clear is what erased his old chats."""
    if conv_store.load(cid) is None:
        return jsonify({"status": "error", "message": "no such conversation"}), 404
    body = request.get_json(silent=True) or {}
    removed = conv_store.clear(cid, include_pinned=bool(body.get("include_pinned")))
    return jsonify({"status": "ok", "removed": removed})
