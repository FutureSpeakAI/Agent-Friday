"""
Agent Friday — Voice-First Onboarding API
FutureSpeak.AI · Asimov's Mind

  GET   /api/onboarding/state     current step + the line Friday should speak
  POST  /api/onboarding/step      advance  {answer?, key_provider?, key_value?}
  POST  /api/onboarding/complete  finalize (marker + identity + SOUL.md)
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import onboarding

onboarding_bp = Blueprint("onboarding", __name__)


@onboarding_bp.route("/api/onboarding/state", methods=["GET"])
@login_required
def onboarding_state():
    return jsonify({"ok": True, **onboarding.get_state()})


@onboarding_bp.route("/api/onboarding/step", methods=["POST"])
@login_required
def onboarding_step():
    d = request.get_json(silent=True) or {}
    return jsonify(onboarding.advance(
        answer=d.get("answer", ""),
        key_provider=d.get("key_provider", ""),
        key_value=d.get("key_value", "")))


@onboarding_bp.route("/api/onboarding/complete", methods=["POST"])
@login_required
def onboarding_complete():
    return jsonify(onboarding.complete())
