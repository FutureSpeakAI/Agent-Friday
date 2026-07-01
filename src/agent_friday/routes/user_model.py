"""
Agent Friday — User Model API
FutureSpeak.AI · Asimov's Mind

  GET   /api/user-model            full profile (traits, facts, workflow)
  POST  /api/user-model/forget     reset all, or a single {category}
  POST  /api/user-model/fact       add a durable fact  {category, text, confidence?}
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import user_model

user_model_bp = Blueprint("user_model", __name__)


@user_model_bp.route("/api/user-model", methods=["GET"])
@login_required
def get_user_model():
    return jsonify({"ok": True, "profile": user_model.profile(),
                    "prompt": user_model.render_user_model_prompt()})


@user_model_bp.route("/api/user-model/forget", methods=["POST"])
@login_required
def forget_user_model():
    data = request.get_json(silent=True) or {}
    return jsonify(user_model.forget(category=data.get("category")))


@user_model_bp.route("/api/user-model/fact", methods=["POST"])
@login_required
def add_fact():
    data = request.get_json(silent=True) or {}
    if not data.get("text"):
        return jsonify({"ok": False, "error": "text required"}), 400
    return jsonify(user_model.note_fact(
        data.get("category", "preference"), data["text"],
        confidence=float(data.get("confidence", 0.7)), source="manual"))
