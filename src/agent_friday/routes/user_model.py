"""
Agent Friday — User Model API
FutureSpeak.AI · Asimov's Mind

  GET   /api/user-model            full profile (traits, facts, workflow)
  POST  /api/user-model/forget     reset all, or a single {category}
  POST  /api/user-model/fact       add a durable fact  {category, text, confidence?}
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import prompt_freeze, user_model
from agent_friday.routes._errors import public_result

user_model_bp = Blueprint("user_model", __name__)


@user_model_bp.route("/api/user-model", methods=["GET"])
@login_required
def get_user_model():
    return jsonify(public_result({"ok": True, "profile": user_model.profile(),
                    "prompt": user_model.render_user_model_prompt()}, "Couldn't load what Friday knows about you"))


def _owner_changed(result):
    """An owner's own edit reaches every conversation's next turn at once;
    Friday's own saves wait for the next conversation (services/prompt_freeze)."""
    if (result or {}).get("ok"):
        prompt_freeze.owner_changed("user model edited by the owner")
    return result


@user_model_bp.route("/api/user-model/forget", methods=["POST"])
@login_required
def forget_user_model():
    data = request.get_json(silent=True) or {}
    return jsonify(public_result(_owner_changed(user_model.forget(category=data.get("category"))),
                                 "Couldn't forget that"))


@user_model_bp.route("/api/user-model/fact", methods=["POST"])
@login_required
def add_fact():
    data = request.get_json(silent=True) or {}
    if not data.get("text"):
        return jsonify({"ok": False, "error": "text required"}), 400
    return jsonify(public_result(_owner_changed(user_model.note_fact(
        data.get("category", "preference"), data["text"],
        confidence=float(data.get("confidence", 0.7)), source="manual")), "Couldn't save that fact"))
