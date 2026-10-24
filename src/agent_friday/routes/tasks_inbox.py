"""routes/tasks_inbox.py -- the phone list in Google Tasks (services/tasks_inbox), Part 1.

    GET  /api/tasks-inbox            what the owner sees: on or off, account, text level, what a tick does
    POST /api/tasks-inbox/enable     {account_id, text_level}: turn it on (a press on this PC's screen only)
    POST /api/tasks-inbox/level      {text_level}: more text to Google is a press; less is open
    POST /api/tasks-inbox/disable    {remove_list}: turning it off is open; removing the list is a press
    POST /api/tasks-inbox/sync       run one sync now

Only the local owner on this PC reaches these routes. Widening (turning it on, sending more text to
Google, deleting the list) needs a press in Friday's own window on this PC (services/screen_click),
never voice, a typed reply, a text message, another device or a script. Narrowing is open.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from agent_friday.services import tasks_inbox

tasks_inbox_bp = Blueprint("tasks_inbox", __name__)

_PRESS = ("This changes only with a press in %s on this PC, because it decides what goes to Google.")


def _pressed() -> bool:
    try:
        from agent_friday.services import screen_click
        return bool(screen_click.screen_session(request))
    except Exception:
        return False


def _press_refusal():
    return jsonify({"ok": False, "code": "desk_proof_required",
                    "message": _PRESS % tasks_inbox._assistant()}), 403


@tasks_inbox_bp.before_request
def _gate():
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "the phone list settings are only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "the phone list settings can only be changed on Friday's own computer"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    if request.method == "POST":
        if not request.is_json:
            return jsonify({"error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"error": "cross-origin request refused"}), 403
    return None


@tasks_inbox_bp.route("/api/tasks-inbox", methods=["GET"])
def tasks_inbox_view():
    return jsonify({"ok": True, **tasks_inbox.view()})


@tasks_inbox_bp.route("/api/tasks-inbox/enable", methods=["POST"])
def tasks_inbox_enable():
    if not _pressed():
        return _press_refusal()
    body = request.get_json(silent=True) or {}
    out = tasks_inbox.enable(str(body.get("account_id") or ""), str(body.get("text_level") or ""))
    return jsonify(out), (200 if out.get("ok") else 400)


@tasks_inbox_bp.route("/api/tasks-inbox/level", methods=["POST"])
def tasks_inbox_level():
    level = str((request.get_json(silent=True) or {}).get("text_level") or "")
    if level not in tasks_inbox.TEXT_LEVELS:
        return jsonify({"ok": False, "message": "Unknown text level."}), 400
    if tasks_inbox.is_raise(level) and not _pressed():
        return _press_refusal()
    out = tasks_inbox.set_level(level)
    return jsonify(out), (200 if out.get("ok") else 400)


@tasks_inbox_bp.route("/api/tasks-inbox/disable", methods=["POST"])
def tasks_inbox_disable():
    remove = bool((request.get_json(silent=True) or {}).get("remove_list"))
    if remove and not _pressed():
        return _press_refusal()
    return jsonify(tasks_inbox.disable(remove_list=remove))


@tasks_inbox_bp.route("/api/tasks-inbox/sync", methods=["POST"])
def tasks_inbox_sync():
    out = tasks_inbox.tick(now=None, force=True)
    return jsonify(out), 200
