"""routes/browser.py — the owner's controls for Friday's own browser.

  GET  /api/browser/status          is it open, which page, how big the profile is
  POST /api/browser/close           close the window
  POST /api/browser/profile/clear   close it and delete Friday's browser profile
                                    (every cookie and sign-in in it)

The profile is Friday's alone (services/browser_session.py); these never touch
the owner's normal browser. Every route is LOCAL ONLY on top of the login gate,
and the two that change something need a same-origin JSON body. These are the
owner's own clicks, so they do not pass the action checkpoint.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from agent_friday.core import _is_local_request, login_required
from agent_friday.services import browser_session

browser_bp = Blueprint("browser", __name__)


def _guard(write: bool = True):
    if not _is_local_request():
        return jsonify({"ok": False, "error": "Friday's browser can only be managed "
                                              "on Friday's own computer"}), 403
    if write:
        if not request.is_json:
            return jsonify({"ok": False, "error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"ok": False, "error": "cross-origin request refused"}), 403
    return None


@browser_bp.route("/api/browser/status", methods=["GET"])
@login_required
def browser_status():
    bad = _guard(write=False)
    if bad:
        return bad
    return jsonify({"ok": True, **browser_session.profile_status()})


@browser_bp.route("/api/browser/close", methods=["POST"])
@login_required
def browser_close():
    bad = _guard()
    if bad:
        return bad
    try:
        closed = browser_session.close_session()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "closed": closed, **browser_session.profile_status()})


@browser_bp.route("/api/browser/profile/clear", methods=["POST"])
@login_required
def browser_profile_clear():
    bad = _guard()
    if bad:
        return bad
    try:
        res = browser_session.clear_profile()
    except browser_session.BrowserRefused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, **res, **browser_session.profile_status()})
