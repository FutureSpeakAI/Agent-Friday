"""Owner-authenticated Crew profile configuration; no model-facing writer."""
from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import crew_profiles as profiles
from agent_friday.user_errors import UserFacingValueError

crew_bp = Blueprint("crew", __name__)


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise UserFacingValueError("Send an agent settings object.", status=400)
    return data


def _error(exc, what):
    if isinstance(exc, profiles.ProfileConflict):
        return jsonify({"status": "error", "message": exc.user_message,
                        "current": exc.current}), 409
    return api_error(exc, what)


@crew_bp.route("/api/crew/capabilities", methods=["GET"])
@login_required
def crew_capabilities():
    try:
        return jsonify({"status": "ok", **profiles.capabilities()})
    except Exception as exc:
        return _error(exc, "Couldn't load Crew choices")


@crew_bp.route("/api/crew/agents", methods=["GET"])
@login_required
def crew_agents():
    try:
        return jsonify({"status": "ok", "agents": profiles.list_profiles(
            include_retired=request.args.get("include_retired") == "true")})
    except Exception as exc:
        return _error(exc, "Couldn't load Crew agents")


@crew_bp.route("/api/crew/tasks", methods=["GET"])
@login_required
def crew_tasks():
    try:
        from agent_friday.services import crew_runtime
        return jsonify({"status": "ok", "tasks": crew_runtime.hub_tasks()})
    except Exception as exc:
        return _error(exc, "Couldn't load Crew work")


@crew_bp.route("/api/crew/agents", methods=["POST"])
@login_required
def crew_create():
    try:
        return jsonify({"status": "ok", "agent": profiles.create_profile(_body())}), 201
    except Exception as exc:
        return _error(exc, "Couldn't create the Crew agent")


@crew_bp.route("/api/crew/agents/<profile_id>", methods=["GET"])
@login_required
def crew_get(profile_id):
    try:
        return jsonify({"status": "ok", "agent": profiles.get_profile(profile_id)})
    except Exception as exc:
        return _error(exc, "Couldn't load the Crew agent")


@crew_bp.route("/api/crew/agents/<profile_id>", methods=["PATCH"])
@login_required
def crew_update(profile_id):
    try:
        data = _body()
        revision = data.pop("revision", None)
        return jsonify({"status": "ok", "agent": profiles.update_profile(profile_id, data, revision)})
    except Exception as exc:
        return _error(exc, "Couldn't save the Crew agent")


@crew_bp.route("/api/crew/agents/<profile_id>/retire", methods=["POST"])
@login_required
def crew_retire(profile_id):
    try:
        data = _body()
        if set(data) != {"revision"}:
            raise UserFacingValueError("Retiring an agent requires its current revision.", status=400)
        return jsonify({"status": "ok", "agent": profiles.retire_profile(profile_id, data["revision"])})
    except Exception as exc:
        return _error(exc, "Couldn't retire the Crew agent")
