"""
Agent Friday — Memory card API (services/core_memory)

  GET   /api/core-memory              both files, Friday's recent changes, the review queue
  POST  /api/core-memory/add          {target, text}            the owner adds a line
  POST  /api/core-memory/edit         {target, index, text}     the owner rewrites a line
  POST  /api/core-memory/delete       {target, index}           the owner deletes a line
  POST  /api/core-memory/undo         {change_id}               undo one of Friday's changes
  POST  /api/core-memory/review       {id, keep: bool}          keep or discard a queued suggestion
  GET   /api/memory/corrections       the owner's corrections, newest first (owner only, bounded)
  POST  /api/memory/corrections/<id>/test  {on, question?, expected?}  keep a correction as a test

Every write here is the owner's own, so it reaches every open conversation's
next turn at once (services/prompt_freeze).
"""
import re

from flask import Blueprint, g, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import public_result
from agent_friday.services import core_memory, corrections

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
                                                       str(d.get("text") or ""),
                                                       reason=str(d.get("reason") or "")[:300]),
                                 "Couldn't change that line"))


@core_memory_bp.route("/api/core-memory/delete", methods=["POST"])
@login_required
def delete_line():
    d = _body()
    return jsonify(public_result(core_memory.owner_delete(str(d.get("target") or ""), d.get("index"),
                                                          wrong=bool(d.get("wrong")),
                                                          reason=str(d.get("reason") or "")[:300]),
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


# ── the owner's corrections (services/corrections) ───────────────────────────
_CORR_ID = re.compile(r"^corr-[0-9a-f]{12}$")
_SHORT = 200


def _owner_only():
    """The corrections log holds the owner's old claims: the local owner
    principal only (an observer or a crew principal gets 403)."""
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"ok": False, "error": "owner_only"}), 403
    except Exception:
        return jsonify({"ok": False, "error": "owner_only"}), 403
    return None


def _short(s) -> str:
    s = str(s or "")
    return s if len(s) <= _SHORT else s[:_SHORT - 1] + "…"


def _public_event(e):
    msgs = [a[4:] for a in (e.get("affected") or []) if str(a).startswith("msg:")]
    return {"id": e.get("id"), "at": e.get("at"), "store": e.get("store"), "action": e.get("action"),
            "claim": _short(e.get("claim")), "correction": _short(e.get("correction")),
            "reason": e.get("reason") or "", "sources": e.get("sources") or [],
            "affected": e.get("affected") or [], "repair": e.get("repair") or {},
            "restores": e.get("restores") or [], "restored_by": e.get("restored_by") or [],
            "regression_test": bool(e.get("regression_test")), "golden": e.get("golden"),
            # Undo exists where the card can put messages back.
            "undo_ids": msgs if (e.get("store") == "card" and e.get("action") in ("corrected", "marked_wrong")
                                 and msgs and not e.get("restored_by")) else []}


@core_memory_bp.route("/api/memory/corrections", methods=["GET"])
@login_required
def list_corrections():
    bad = _owner_only()
    if bad:
        return bad
    try:
        n = int(request.args.get("n") or 50)
    except (TypeError, ValueError):
        n = 50
    n = max(1, min(n, corrections.LIST_MAX))
    return jsonify({"ok": True, "corrections": [_public_event(e) for e in corrections.events(n)],
                    "max": corrections.LIST_MAX})


@core_memory_bp.route("/api/memory/corrections/<cid>/test", methods=["POST"])
@login_required
def correction_test_flag(cid):
    """Tick or untick "keep this as a test": {on: bool, question?, expected?}.
    The golden is the owner's own; nothing is generated."""
    bad = _owner_only()
    if bad:
        return bad
    if not _CORR_ID.match(str(cid or "")):
        return jsonify({"ok": False, "error": "which correction?"}), 400
    d = _body()
    q, x = d.get("question") or "", d.get("expected") or ""
    if not isinstance(q, str) or not isinstance(x, str) or len(q) > corrections.QUESTION_MAX \
            or len(x) > corrections.EXPECTED_MAX:
        return jsonify({"ok": False, "error": "the question or the answer is too long"}), 400
    res = corrections.set_test(cid, bool(d.get("on")), q, x)
    return jsonify(res), (200 if res.get("ok") else 404)
