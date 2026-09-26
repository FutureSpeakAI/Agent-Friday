"""
Agent Friday — Channel Integration API
FutureSpeak.AI · Asimov's Mind

  GET   /api/channels                     configured channels + status
  POST  /api/channels/enable              master switch  {enabled}
  POST  /api/channels/<name>/configure    set token + options
  POST  /api/channels/<name>/start
  POST  /api/channels/<name>/stop
  POST  /api/channels/<name>/test         send a test message  {chat_id, text?}
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services.channels import manager
from agent_friday.routes._errors import public_result

channels_bp = Blueprint("channels", __name__)


@channels_bp.route("/api/channels", methods=["GET"])
@login_required
def list_channels():
    return jsonify(public_result({"ok": True, **manager.status()}, "Couldn't load the channels"))


@channels_bp.route("/api/channels/enable", methods=["POST"])
@login_required
def enable_channels():
    data = request.get_json(silent=True) or {}
    cfg = manager.load_config()
    cfg["enabled"] = bool(data.get("enabled", False))
    return jsonify(public_result(manager.save_config(cfg), "Couldn't save the channel settings"))


@channels_bp.route("/api/channels/<name>/configure", methods=["POST"])
@login_required
def configure_channel(name):
    data = request.get_json(silent=True) or {}
    opts = {k: data[k] for k in ("enabled", "allowlist", "poll_interval") if k in data}
    return jsonify(public_result(manager.configure_channel(name, opts, token=data.get("token")), "Couldn't configure the channel"))  # pragma: allowlist secret


@channels_bp.route("/api/channels/<name>/start", methods=["POST"])
@login_required
def start_channel(name):
    return jsonify(public_result(manager.start_channel(name), "Couldn't start the channel"))


@channels_bp.route("/api/channels/<name>/stop", methods=["POST"])
@login_required
def stop_channel(name):
    return jsonify(public_result(manager.stop_channel(name), "Couldn't stop the channel"))


@channels_bp.route("/api/channels/<name>/test", methods=["POST"])
@login_required
def test_channel(name):
    data = request.get_json(silent=True) or {}
    if not data.get("chat_id"):
        return jsonify({"ok": False, "error": "chat_id required"}), 400
    return jsonify(public_result(manager.test_channel(name, data["chat_id"],
                                        data.get("text", "Friday here — channel test ✅")), "Couldn't test the channel"))
