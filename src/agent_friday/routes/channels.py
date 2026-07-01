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

channels_bp = Blueprint("channels", __name__)


@channels_bp.route("/api/channels", methods=["GET"])
@login_required
def list_channels():
    return jsonify({"ok": True, **manager.status()})


@channels_bp.route("/api/channels/enable", methods=["POST"])
@login_required
def enable_channels():
    data = request.get_json(silent=True) or {}
    cfg = manager.load_config()
    cfg["enabled"] = bool(data.get("enabled", False))
    return jsonify(manager.save_config(cfg))


@channels_bp.route("/api/channels/<name>/configure", methods=["POST"])
@login_required
def configure_channel(name):
    data = request.get_json(silent=True) or {}
    opts = {k: data[k] for k in ("enabled", "allowlist", "poll_interval") if k in data}
    return jsonify(manager.configure_channel(name, opts, token=data.get("token")))  # pragma: allowlist secret


@channels_bp.route("/api/channels/<name>/start", methods=["POST"])
@login_required
def start_channel(name):
    return jsonify(manager.start_channel(name))


@channels_bp.route("/api/channels/<name>/stop", methods=["POST"])
@login_required
def stop_channel(name):
    return jsonify(manager.stop_channel(name))


@channels_bp.route("/api/channels/<name>/test", methods=["POST"])
@login_required
def test_channel(name):
    data = request.get_json(silent=True) or {}
    if not data.get("chat_id"):
        return jsonify({"ok": False, "error": "chat_id required"}), 400
    return jsonify(manager.test_channel(name, data["chat_id"],
                                        data.get("text", "Friday here — channel test ✅")))
