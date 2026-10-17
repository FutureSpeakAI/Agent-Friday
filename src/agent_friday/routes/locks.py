"""routes/locks.py -- the Locks page (first-run spec C13).

    GET  /api/locks                    every line of the page, read from the stores that decide it
    POST /api/locks/open-windows       {page: device_encryption | other_users}: open that Windows page
    POST /api/locks/raw-holds/ask      {path}: raise the file-access card for one held raw record
    POST /api/locks/raw-holds/reset    raise the card that starts the held list fresh (approved on screen only)
    POST /api/locks/never-send/remove  {entry}: take one entry off the never-send list (a click on this page only)
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from agent_friday.services import locks

locks_bp = Blueprint("locks", __name__)
try:
    locks.ensure_hook()
except Exception:  # noqa: BLE001 - the route raises it again when used
    pass


@locks_bp.before_request
def _gate():
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"error": "the Locks page is only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"error": "the Locks page can only be read on Friday's own computer"}), 403
    except Exception:
        return jsonify({"error": "could not establish that this request is local"}), 403
    if request.method == "POST":
        if not request.is_json:
            return jsonify({"error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"error": "cross-origin request refused"}), 403
    return None


@locks_bp.route("/api/locks", methods=["GET"])
def locks_view():
    return jsonify({"ok": True, **locks.view()})


@locks_bp.route("/api/locks/open-windows", methods=["POST"])
def locks_open_windows():
    out = locks.open_windows_page(str((request.get_json(silent=True) or {}).get("page") or ""))
    return jsonify(out), (200 if out.get("ok") else 400)


@locks_bp.route("/api/locks/raw-holds/ask", methods=["POST"])
def locks_raw_ask():
    out = locks.ask_for_held(str((request.get_json(silent=True) or {}).get("path") or ""))
    return jsonify(out), (200 if out.get("ok") else 400)


@locks_bp.route("/api/locks/never-send/remove", methods=["POST"])
def locks_never_send_remove():
    # Loosening the floor is the owner's click on this page, never a program that names itself owner.
    from agent_friday.services import screen_click
    if not screen_click.screen_session(request):
        return jsonify({"ok": False, "message": "This changes only from the Locks page."}), 403
    out = locks.remove_never_send(str((request.get_json(silent=True) or {}).get("entry") or ""))
    return jsonify(out), (200 if out.get("ok") else 400)


@locks_bp.route("/api/locks/raw-holds/reset", methods=["POST"])
def locks_raw_reset():
    return jsonify(locks.request_reset(requested_by="owner:locks")), 202
