"""
Agent Friday — Channel Integration API
FutureSpeak.AI · Asimov's Mind

  GET   /api/channels                     configured channels + status
  POST  /api/channels/enable              master switch  {enabled}
  POST  /api/channels/<name>/configure    set token + options
  POST  /api/channels/<name>/start
  POST  /api/channels/<name>/stop
  POST  /api/channels/<name>/test         send a test message  {chat_id?, text?}
  POST  /api/channels/<name>/revoke       turn it off and delete its token
  GET   /api/channels/overview            every channel: what it can do, who can reach it

Every change is the owner's act at this PC (as the phone settings are): a local request,
same-origin JSON, and a press on Friday's own screen (services/desk_press). Before, these
answered any signed-in session, and a test went to whatever chat a request named.
"""
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request
from agent_friday.core import _is_local_request, login_required
from agent_friday.services.channels import manager
from agent_friday.routes._errors import public_result

channels_bp = Blueprint("channels", __name__)


def _guard():
    if not _is_local_request():
        return jsonify({"ok": False, "error": "channels can only be changed on Friday's own computer"}), 403
    if not request.is_json:
        return jsonify({"ok": False, "error": "JSON body required"}), 415
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return jsonify({"ok": False, "error": "cross-origin request refused"}), 403
    from agent_friday.services import screen_click
    return screen_click.press_refusal(request, "Change this with a press in Settings, on the computer "
                                               "Friday runs on.")


@channels_bp.route("/api/channels/overview", methods=["GET"])
@login_required
def channels_overview():
    """Every message channel: what it can do, who can reach it, on or off, how to test it and
    how to remove it (NS-19.7-3). No token, number or chat id."""
    return jsonify(public_result(manager.overview(), "Couldn't load the channels"))


@channels_bp.route("/api/channels", methods=["GET"])
@login_required
def list_channels():
    return jsonify(public_result({"ok": True, **manager.status()}, "Couldn't load the channels"))


@channels_bp.route("/api/channels/enable", methods=["POST"])
@login_required
def enable_channels():
    bad = _guard()
    if bad:
        return bad
    data = request.get_json(silent=True) or {}
    cfg = manager.load_config()
    cfg["enabled"] = bool(data.get("enabled", False))
    return jsonify(public_result(manager.save_config(cfg), "Couldn't save the channel settings"))


@channels_bp.route("/api/channels/<name>/configure", methods=["POST"])
@login_required
def configure_channel(name):
    bad = _guard()
    if bad:
        return bad
    data = request.get_json(silent=True) or {}
    opts = {k: data[k] for k in ("enabled", "allowlist", "poll_interval") if k in data}
    return jsonify(public_result(manager.configure_channel(name, opts, token=data.get("token")), "Couldn't configure the channel"))  # pragma: allowlist secret


@channels_bp.route("/api/channels/<name>/start", methods=["POST"])
@login_required
def start_channel(name):
    bad = _guard()
    if bad:
        return bad
    return jsonify(public_result(manager.start_channel(name), "Couldn't start the channel"))


@channels_bp.route("/api/channels/<name>/stop", methods=["POST"])
@login_required
def stop_channel(name):
    bad = _guard()
    if bad:
        return bad
    return jsonify(public_result(manager.stop_channel(name), "Couldn't stop the channel"))


@channels_bp.route("/api/channels/<name>/test", methods=["POST"])
@login_required
def test_channel(name):
    bad = _guard()
    if bad:
        return bad
    data = request.get_json(silent=True) or {}
    # Only to a chat on the channel's allowlist (the first one when none is named).
    return jsonify(public_result(manager.test_channel(name, data.get("chat_id") or None,
                                        data.get("text", "Friday here — channel test ✅")), "Couldn't test the channel"))


@channels_bp.route("/api/channels/<name>/revoke", methods=["POST"])
@login_required
def revoke_channel(name):
    bad = _guard()
    if bad:
        return bad
    return jsonify(public_result(manager.revoke_channel(name), "Couldn't remove the channel"))
