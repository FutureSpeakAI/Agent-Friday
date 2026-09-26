"""
Agent Friday — SOUL.md Personality API
FutureSpeak.AI · Asimov's Mind

  GET   /api/soul            current SOUL.md text + state
  POST  /api/soul            save new SOUL.md text  {text}
  POST  /api/soul/reset      restore the shipped default
  GET   /api/soul/history    version snapshots
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import soul
from agent_friday.routes._errors import public_result

soul_bp = Blueprint("soul", __name__)


@soul_bp.route("/api/soul", methods=["GET"])
@login_required
def get_soul():
    return jsonify({"ok": True, "text": soul.load_soul(),
                    "default": soul.default_soul(), "state": soul.state()})


@soul_bp.route("/api/soul", methods=["POST"])
@login_required
def save_soul():
    data = request.get_json(silent=True) or {}
    res = soul.save_soul(data.get("text", ""))
    code = 200 if res.get("ok") else 400
    return jsonify(public_result(res, "Couldn't save the soul")), code


@soul_bp.route("/api/soul/reset", methods=["POST"])
@login_required
def reset_soul():
    return jsonify(public_result(soul.reset_soul(), "Couldn't reset the soul"))


@soul_bp.route("/api/soul/history", methods=["GET"])
@login_required
def soul_history():
    return jsonify({"ok": True, "versions": soul.history()})
