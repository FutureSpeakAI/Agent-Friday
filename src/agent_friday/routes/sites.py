"""Authenticated Sites panels and previews; credentials enter only dedicated UI routes."""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.user_errors import UserFacingPermissionError
import agent_friday.core as core
from agent_friday.services import sites_operations as sites
from agent_friday.services import sites_privacy

sites_bp = Blueprint("sites", __name__)


@sites_bp.after_request
def preview_response_headers(response):
    if request.path == "/api/sites/preview" or request.path.startswith(("/api/sites/preview/", "/api/sites/preview-frame/")):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _bad(message: str, code=400):
    return jsonify({"status": "error", "error": message}), code


def _fail(exc, code=400):
    """A refused request. A `UserFacingError` shows its message; any other
    exception is logged and answered with a short error id."""
    return api_error(exc, "Couldn't complete that Sites request", code, key="error")


def _object():
    if request.content_length and request.content_length > 250_000:
        raise ValueError("This Sites request is too large.")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object.")
    return body


def _local():
    if not core._is_local_request() or not core._api_token_valid(request.headers.get("X-Friday-Token")):
        raise UserFacingPermissionError("Use Sites on this PC for hosting account actions.")


@sites_bp.route("/api/sites", methods=["GET"])
@login_required
def sites_overview():
    """The authenticated owner view can navigate sites across their own chats."""
    try:
        generation = sites_privacy.capture().generation
    except ValueError as exc:
        return _fail(exc)
    rows = []
    for path in sites._root().glob("site-*/site.json"):
        try:
            site = sites._read(path)
            sites.validate_site_owner(site)
            rows.append(sites._overview(site))
        except ValueError:
            continue
    try:
        sites_privacy.require_generation(generation)
        return jsonify({"status": "ok", "sites": rows})
    except ValueError as exc:
        return _fail(exc)


@sites_bp.route("/api/sites/action", methods=["POST"])
@login_required
def site_action():
    try:
        _local()
        origin = sites_privacy.capture()
        body = _object()
        if set(body) - {"action", "args", "conversation_id"}:
            raise ValueError("Provide a Sites action and its arguments.")
        args = body.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("Sites arguments must be an object.")
        cid = body.get("conversation_id")
        if not cid and args.get("site_id"):
            site = sites.get_site(args["site_id"])
            cid = (site or {}).get("conversation_id")
        result = sites.execute(body.get("action"), args, {"conversation_id": cid, "_sites_origin": origin})
        return jsonify(result)
    except (ValueError, KeyError) as exc:
        return _fail(exc)
    except PermissionError as exc:
        return _fail(exc, 403)
    except Exception:
        return _bad("The Sites action could not finish. Check the operation before trying again.", 503)


@sites_bp.route("/api/sites/preview/<site_id>/<build_id>/<path:rel>", methods=["GET"])
@login_required
def site_preview(site_id, build_id, rel):
    return _bad("Open a fresh frozen preview from Sites.", 410)


@sites_bp.route("/api/sites/preview", methods=["POST"])
@login_required
def site_preview_session():
    from agent_friday.services import site_previews
    result = None
    try:
        _local()
        mode = request.headers.get("X-Friday-Preview-Mode")
        if mode not in {"manual", "navigation"}:
            raise ValueError("Choose an explicit preview request mode.")
        origin = sites_privacy.capture() if mode == "manual" else None
        body = _object()
        fields = {"mode", "site_id", "site_revision", "build_id"}
        if mode == "manual" and body.get("mode") == mode and set(body) == fields:
            result = site_previews.issue(body["site_id"], body["site_revision"], body["build_id"],
                                         origin=origin, parent_origin=request.host_url)
        elif mode == "navigation" and body.get("mode") == mode and set(body) == fields | {"request_id"}:
            result = site_previews.issue_navigation(body["site_id"], body["site_revision"], body["build_id"],
                                                    body["request_id"], parent_origin=request.host_url)
        else:
            raise ValueError("Choose a saved site, its current revision and a successful build.")
        site_previews.check_response(result)
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response
    except PermissionError as exc:
        return _fail(exc, 403)
    except (ValueError, KeyError):
        if result is not None:
            site_previews.close(result["preview_url"].rsplit("/", 1)[-1])
        return _bad("The selected preview is no longer available. Open the current build again.", 409)
    except Exception:
        if result is not None:
            site_previews.close(result["preview_url"].rsplit("/", 1)[-1])
        return _bad("The frozen preview could not be opened.", 503)


@sites_bp.route("/api/sites/preview-frame/<handle>", methods=["GET", "DELETE"])
@login_required
def site_preview_frame(handle):
    from agent_friday.services import site_previews
    try:
        if request.method == "DELETE":
            _local()
            site_previews.close(handle)
            return jsonify({"status": "ok"})
        if not core._is_local_request():
            raise UserFacingPermissionError("Open the preview in Friday on this PC.")
        body, headers = site_previews.wrapper(handle, parent_origin=request.host_url)
    except PermissionError as exc:
        return _fail(exc, 403)
    except (ValueError, OSError, KeyError):
        return _bad("That frozen preview expired. Open it again from Sites.", 410)
    response = Response(body, content_type="text/html; charset=utf-8")
    for key, value in headers.items():
        response.headers[key] = value
    return response


@sites_bp.route("/api/sites/hosting", methods=["GET", "POST"])
@login_required
def sites_hosting():
    from agent_friday.services import site_hosting
    try:
        generation = sites_privacy.capture().generation
        if request.method == "GET":
            connections = site_hosting.list_connections()
            sites_privacy.require_generation(generation)
            return jsonify({"status": "ok", "connections": connections})
        _local()
        sites._durable()
        connection = site_hosting.connect(_object(), generation=generation)
        sites_privacy.require_generation(generation)
        return jsonify({"status": "ok", "connection": connection})
    except ValueError as exc:
        return _fail(exc)
    except PermissionError as exc:
        return _fail(exc, 403)
    except Exception:
        return _bad("The hosting connection could not be saved securely.", 503)


@sites_bp.route("/api/sites/hosting/<connection_id>/disconnect", methods=["POST"])
@login_required
def sites_disconnect(connection_id):
    from agent_friday.services import site_hosting
    try:
        _local()
        generation = sites_privacy.capture().generation
        sites._durable()
        connection = site_hosting.disconnect(connection_id, _object().get("revision"), generation=generation)
        sites_privacy.require_generation(generation)
        return jsonify({"status": "ok", "connection": connection})
    except ValueError as exc:
        return _fail(exc)
    except PermissionError as exc:
        return _fail(exc, 403)
    except Exception:
        return _bad("The hosting connection could not be disconnected.", 503)
