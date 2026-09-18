"""The HTTP surface for conversations.

THE UI HAS BEEN CALLING THESE ENDPOINTS ALL ALONG. `index.html` has a
conversation switcher, a per-chat model picker and a transcript loader, and
every one of them fetches `/api/conversations...` — six call sites. None of
those routes existed. The service layer underneath
(`services/conversations.py`) is complete: per-conversation directories, an
atomic message log, cost totals and a per-conversation `seat`. Only the doorway
was missing, so the switcher silently did nothing and every turn fell back to
Main.

That is why "multiple chat windows" looked like a big frontend project. Most of
it is already built on both sides; this is the join.

CONTRACT, taken from what the UI actually sends rather than from a design doc:

    GET    /api/conversations              -> {status, conversations, main_id}
    POST   /api/conversations              -> {status, conversation}
    GET    /api/conversations/<cid>        -> {status, conversation}
    PATCH  /api/conversations/<cid>        -> {status, conversation, note?}
    GET    /api/conversations/<cid>/messages -> {status, messages}

The PATCH response always carries the conversation as it now IS, never as it
was asked to be. The UI compares the two and tells the user when a binding did
not stick — a refusal that reports success is the failure this whole codebase
keeps having to fix.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from agent_friday.services import conversations as _conv

conversations_bp = Blueprint("conversations", __name__)

_log = logging.getLogger("friday.conversations")

#: Fields a client may set. Mirrors what `conversations.patch` accepts; listed
#: again here so an HTTP caller cannot reach a field the UI has no business
#: writing just because the service happens to allow it.
_PATCHABLE = ("title", "status", "seat", "pinned")


def _summary(conv: dict) -> dict:
    """What the switcher needs, without the whole transcript behind it."""
    return {
        "id": conv.get("id"),
        "title": conv.get("title") or "New chat",
        "status": conv.get("status") or "active",
        "pinned": bool(conv.get("pinned")),
        "seat": conv.get("seat"),
        "created_at": conv.get("created_at"),
        "last_active_at": conv.get("last_active_at"),
        "totals": conv.get("totals") or {},
    }


@conversations_bp.route("/api/conversations", methods=["GET"])
def list_conversations():
    _conv.ensure_main()
    convs = [_summary(c) for c in _conv.list_all(include_archived=False)]
    return jsonify({"status": "ok", "conversations": convs,
                    "main_id": _conv.MAIN_ID})


@conversations_bp.route("/api/conversations", methods=["POST"])
def create_conversation():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "New chat").strip()[:120] or "New chat"
    seat = data.get("seat") if isinstance(data.get("seat"), dict) else None
    conv = _conv.create(title=title, seat=seat)
    _log.info("created conversation %s (%r)", conv.get("id"), title)
    return jsonify({"status": "ok", "conversation": _summary(conv)}), 201


@conversations_bp.route("/api/conversations/<cid>", methods=["GET"])
def get_conversation(cid):
    conv = _conv.load(cid)
    if conv is None:
        return jsonify({"status": "error",
                        "error": "no such conversation: %s" % cid}), 404
    return jsonify({"status": "ok", "conversation": _summary(conv)})


@conversations_bp.route("/api/conversations/<cid>", methods=["PATCH"])
def patch_conversation(cid):
    data = request.get_json(silent=True) or {}
    fields = {k: data[k] for k in _PATCHABLE if k in data}
    if not fields:
        conv = _conv.load(cid)
        if conv is None:
            return jsonify({"status": "error",
                            "error": "no such conversation: %s" % cid}), 404
        return jsonify({"status": "ok", "conversation": _summary(conv)})

    note = None
    if "seat" in fields:
        seat = fields["seat"]
        note = _why_this_seat_cannot_be_bound(seat)
        if note:
            # Refuse the binding and say so, rather than accepting it and
            # letting the next turn discover the seat is not there.
            fields.pop("seat")
            _log.info("refused seat binding on %s: %s", cid, note)

    conv = _conv.patch(cid, **fields) if fields else _conv.load(cid)
    if conv is None:
        return jsonify({"status": "error",
                        "error": "no such conversation: %s" % cid}), 404
    out = {"status": "ok", "conversation": _summary(conv)}
    if note:
        out["note"] = note
    return jsonify(out)


@conversations_bp.route("/api/conversations/<cid>/messages", methods=["GET"])
def conversation_messages(cid):
    if _conv.load(cid) is None:
        return jsonify({"status": "error",
                        "error": "no such conversation: %s" % cid}), 404
    try:
        limit = int(request.args.get("limit") or 0) or None
    except Exception:
        limit = None
    return jsonify({"status": "ok", "messages": _conv.messages(cid, limit)})


def _why_this_seat_cannot_be_bound(seat) -> str | None:
    """A reason this seat cannot be given to a conversation, or None.

    ONE LOCAL MODEL AT A TIME ON THIS HARDWARE. Two 27B seats do not fit in
    12 GB — measured on 2026-09-18, two bonsai2:27b servers held 11,605 MiB of
    12,282 between them and every turn sent into that state hung. Letting a
    second conversation quietly bind a different local model is offering the
    user something the machine cannot do, and they find out as a hang rather
    than as a sentence.

    CONSERVATIVE ON PURPOSE. Only a POSITIVE reading of a different local
    model actually serving is grounds for refusal; every failure to look
    returns None and the binding is allowed. "I could not check" is not "it is
    occupied", and a picker that refuses on a failed probe is worse than one
    that occasionally over-promises — the same rule applied to seat presence
    everywhere else in this codebase.
    """
    if not isinstance(seat, dict):
        return None
    model = (seat.get("model") or "").strip()
    if not model:
        return None                       # unbinding is always allowed
    try:
        from agent_friday.services import local_seats
        if not local_seats._is_local_name(model):
            return None                   # cloud seats do not contend
    except Exception:
        return None
    try:
        from agent_friday.services.residency_arbiter import survey_live_seats
        live = survey_live_seats() or {}
    except Exception:
        return None
    others = [m for m in live if m and m != model]
    if not others:
        return None
    return ("%s is already serving on this machine and there is only room for "
            "one local model at a time. Free it first, or give this chat a "
            "cloud model." % others[0])
