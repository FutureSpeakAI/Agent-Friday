"""Authenticated conversation membership and idempotent Crew dispatch."""
from flask import Blueprint, jsonify, request

from agent_friday.core import login_required, _is_local_request
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
            origin = runtime.capture_host_origin()
            return jsonify(status="ok", **runtime.dispatch(
                conversation_id, request.get_json(silent=True), host_origin=origin)), 202
        return jsonify(status="ok", **runtime.turns(conversation_id))
    except Exception as exc:
        return _error(exc)


def _instruction_body():
    if not _is_local_request() or not request.is_json:
        raise runtime.CrewRoomError("Use Friday's local page and send a JSON instruction.")
    origin = runtime.capture_host_origin()
    runtime.require_public_host_origin(origin)
    data = request.get_json(silent=True)
    runtime.require_public_host_origin(origin)
    return origin, data


@crew_rooms_bp.route("/api/crew/rooms/<conversation_id>/tasks/<task_id>/steer", methods=["GET", "POST"])
@login_required
def crew_steer(conversation_id, task_id):
    try:
        if request.method == "POST":
            origin, data = _instruction_body()
            return jsonify(**runtime.steer(conversation_id, task_id, data, host_origin=origin)), 202
        return jsonify(**runtime.steering_status(conversation_id, task_id))
    except Exception as exc:
        return _error(exc)


@crew_rooms_bp.route("/api/crew/rooms/<conversation_id>/tasks/<task_id>/talk", methods=["GET", "POST"])
@login_required
def crew_talk(conversation_id, task_id):
    try:
        if request.method == "POST":
            origin, data = _instruction_body()
            return jsonify(**runtime.talk(conversation_id, task_id, data, host_origin=origin)), 202
        return jsonify(**runtime.talk_status(conversation_id, task_id))
    except Exception as exc:
        return _error(exc)
