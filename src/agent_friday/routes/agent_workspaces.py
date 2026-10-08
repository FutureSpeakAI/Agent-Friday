"""Local owner controls for independently driven browser workspaces."""
from __future__ import annotations

import json
import re

from flask import Blueprint, jsonify, request

import agent_friday.core as core
from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import agent_workspace_permissions as permissions
from agent_friday.services import browser_authority, browser_session, off_record, screen_click
from agent_friday.user_errors import UserFacingPermissionError, UserFacingValueError

agent_workspaces_bp = Blueprint("agent_workspaces", __name__)
_MAX_BODY = 32_768


def _admit(*, write=False):
    if not core._is_local_request():
        raise UserFacingPermissionError("Manage agent workspaces from Friday on this computer.", status=403)
    if screen_click.is_cross_site(request):
        raise UserFacingPermissionError("Open the workspace control in Friday's own page.", status=403)
    if write and not screen_click.screen_session(request):
        raise UserFacingPermissionError("This control requires Friday's own page on this computer.", status=403)
    # Capture before any request body read or permission-store wait.
    generation = off_record.generation()
    # Keep only immutable authority values: validators run on browser workers,
    # where Flask's request proxy is unavailable.
    token = request.headers.get("X-Friday-Token", "") if write else None
    origin = (off_record.active(), generation, token)
    _require_origin(origin, public=False)
    return origin


def _require_origin(origin, *, public=True):
    _require_screen(origin)
    private, generation, _ = origin
    if (off_record.generation() != generation or off_record.active() != private
            or off_record.generation() != generation or (public and private)):
        raise UserFacingPermissionError("Workspace access changed with privacy mode. Open a fresh on-record workspace.", status=403)


def _require_screen(origin):
    token = origin[2]
    if token is not None and not core._api_token_valid(token):
        raise UserFacingPermissionError("This Friday page's control session expired. Refresh it before continuing.", status=403)


def _body(keys):
    if not request.is_json:
        raise UserFacingValueError("JSON body required.", status=415)
    if request.content_length is not None and request.content_length > _MAX_BODY:
        raise UserFacingValueError("This workspace request is too large.", status=413)
    raw = request.stream.read(_MAX_BODY + 1)
    if len(raw) > _MAX_BODY:
        raise UserFacingValueError("This workspace request is too large.", status=413)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise UserFacingValueError("The workspace request is not valid JSON.", status=400) from exc
    if not isinstance(data, dict) or set(data) - set(keys):
        raise UserFacingValueError("The workspace request contains unsupported fields.", status=400)
    return data


def _generation(value):
    if type(value) is not int or not 1 <= value <= 2 ** 53 - 1:
        raise UserFacingValueError("Refresh the workspace before using this control.", status=400)
    return value


def _validator(origin):
    def validate(owner, *, purpose="act"):
        _require_origin(origin)
        result = browser_authority.validate_owner(owner, purpose=purpose)
        _require_origin(origin)
        return result
    return validate


def _authorize_surface(state, validator):
    current = browser_session.owned_status(state["surface_id"], validator)
    if any(current.get(key) != state.get(key) for key in ("generation", "page_generation")):
        raise browser_session.BrowserRefused("The workspace changed before its view could be published.")


def _reply(origin, *, public=True, authorize=None, **data):
    _require_origin(origin, public=public)
    response = jsonify(ok=True, **data)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Serializing a frame can wait; never publish a response from an old origin.
    if authorize is not None:
        authorize()
    _require_origin(origin, public=public)
    return response


def _error(exc):
    if isinstance(exc, browser_session.BrowserRefused):
        return api_error(exc, "This workspace changed or is unavailable. Refresh it before continuing", 409, shape="ok")
    return api_error(exc, "Couldn't access this agent workspace", shape="ok")


@agent_workspaces_bp.route("/api/browser/workspaces/permission", methods=["GET", "PUT"])
@login_required
def workspace_permission():
    try:
        origin = _admit(write=request.method == "PUT")
        previous = permissions.snapshot()
        if request.method == "GET":
            return _reply(origin, public=False, permission=previous)
        data = _body({"enabled", "generation"})
        if type(data.get("enabled")) is not bool:
            raise UserFacingValueError("Workspace permission must be on or off.", status=400)
        expected = _generation(data.get("generation"))
        _require_origin(origin, public=data["enabled"])
        try:
            state = permissions.set_enabled(
                data["enabled"], expected_generation=expected,
                authorize=lambda: _require_origin(origin, public=data["enabled"]))
        finally:
            # Even a failed disable must immediately cancel this process's work.
            if not data["enabled"]:
                browser_session.stop_all_owned_sessions()
        return _reply(origin, public=data["enabled"], permission=state)
    except Exception as exc:
        return _error(exc)


@agent_workspaces_bp.route("/api/browser/workspaces", methods=["GET"])
@login_required
def workspace_list():
    try:
        origin = _admit()
        _require_origin(origin)
        if set(request.args) - {"conversation_id"}:
            raise UserFacingValueError("Choose a supported workspace filter.", status=400)
        cid = request.args.get("conversation_id")
        if cid is not None and (len(request.args.getlist("conversation_id")) != 1
                                or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", cid)):
            raise UserFacingValueError("Choose an existing conversation.", status=400)
        validator = _validator(origin)
        rows = browser_session.list_owned_sessions(validator)
        if cid is not None:
            rows = [row for row in rows if row.get("conversation_id") == cid]
        return _reply(origin, authorize=lambda: [_authorize_surface(row, validator) for row in rows], workspaces=rows)
    except Exception as exc:
        return _error(exc)


@agent_workspaces_bp.route("/api/browser/workspaces/<surface_id>/control", methods=["POST"])
@login_required
def workspace_control(surface_id):
    try:
        origin = _admit(write=True)
        data = _body({"operation", "generation"})
        if not isinstance(data.get("operation"), str) or data["operation"] not in {"pause", "resume", "takeover", "revoke", "close"}:
            raise UserFacingValueError("Choose a supported workspace control.", status=400)
        stopping = data["operation"] in {"close", "revoke"}
        validator = _validator(origin)
        _require_screen(origin)
        if not stopping:
            _require_origin(origin)
        state = browser_session.control_owned_session(
            surface_id, data["operation"], _generation(data.get("generation")), validator)
        if stopping:
            # Closing remains available after grants expire; return no page data.
            response = jsonify(ok=True, workspace={key: state[key] for key in
                               ("surface_id", "generation", "mode") if key in state})
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            _require_screen(origin)
            return response
        return _reply(origin, authorize=lambda: _authorize_surface(state, validator), workspace=state)
    except Exception as exc:
        return _error(exc)


@agent_workspaces_bp.route("/api/browser/workspaces/<surface_id>/frame", methods=["GET"])
@login_required
def workspace_frame(surface_id):
    try:
        origin = _admit()
        _require_origin(origin)
        raw = request.args.get("generation", "")
        if set(request.args) != {"generation"} or len(request.args.getlist("generation")) != 1 or not re.fullmatch(r"[1-9][0-9]{0,15}", raw):
            raise UserFacingValueError("Refresh the workspace before requesting its view.", status=400)
        generation = _generation(int(raw))
        validator = _validator(origin)
        frame = browser_session.owned_frame(surface_id, generation, validator)
        # Capture can block: recheck stored owner authority before publishing pixels.
        browser_session.owned_status(surface_id, validator)
        return _reply(origin, authorize=lambda: _authorize_surface(frame, validator), frame=frame)
    except Exception as exc:
        return _error(exc)


@agent_workspaces_bp.route("/api/browser/workspaces/<surface_id>/input", methods=["POST"])
@login_required
def workspace_input(surface_id):
    try:
        origin = _admit(write=True)
        _require_origin(origin)
        data = _body({"generation", "event"})
        if not isinstance(data.get("event"), dict):
            raise UserFacingValueError("Choose an input action in the workspace.", status=400)
        validator = _validator(origin)
        _require_origin(origin)
        state = browser_session.owned_human_input(
            surface_id, _generation(data.get("generation")), data["event"], validator)
        return _reply(origin, authorize=lambda: _authorize_surface(state, validator), workspace=state)
    except Exception as exc:
        return _error(exc)


@agent_workspaces_bp.route("/api/browser/workspaces/stop-all", methods=["POST"])
@login_required
def workspace_stop_all():
    try:
        origin = _admit(write=True)
        _body(set())
        _require_screen(origin)
        browser_session.stop_all_owned_sessions()
        response = jsonify(ok=True, stopped=True)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        _require_screen(origin)
        return response
    except Exception as exc:
        return _error(exc)
