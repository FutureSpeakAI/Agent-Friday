"""Authenticated conversation membership and idempotent Crew dispatch."""
from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import crew_runtime as runtime

crew_rooms_bp = Blueprint("crew_rooms", __name__)


def _error(exc):
    if isinstance(exc, runtime.CrewRoomError):
        return jsonify(status="error", message=str(exc)), (
            409 if isinstance(exc, runtime.CrewConflict) else 400)
    return api_error(exc, "Couldn't update this Crew conversation")


@crew_rooms_bp.route("/api/crew/rooms/<conversation_id>", methods=["GET", "PUT"])
@login_required
def crew_room(conversation_id):
    try:
        room = (runtime.update_room(conversation_id, request.get_json(silent=True))
                if request.method == "PUT" else runtime.get_room(conversation_id))
        return jsonify(status="ok", room=room)
    except Exception as exc:
        return _error(exc)


@crew_rooms_bp.route("/api/crew/rooms/<conversation_id>/turns", methods=["GET", "POST"])
@login_required
def crew_turns(conversation_id):
    try:
        if request.method == "POST":
            return jsonify(status="ok", **runtime.dispatch(
                conversation_id, request.get_json(silent=True))), 202
        return jsonify(status="ok", **runtime.turns(conversation_id))
    except Exception as exc:
        return _error(exc)
