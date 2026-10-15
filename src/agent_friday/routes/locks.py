"""routes/locks.py -- the Locks page (first-run spec C13).

    GET  /api/locks                    every line of the page, read from the stores that decide it
    POST /api/locks/open-windows       {page: device_encryption | other_users}: open that Windows page
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from agent_friday.services import locks

locks_bp = Blueprint("locks", __name__)


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
