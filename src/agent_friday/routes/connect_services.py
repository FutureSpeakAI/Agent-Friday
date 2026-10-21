"""Connect your services: the actions behind the screen.

    POST   /api/setup/connections/<id>/test           live test {state, text, fix}
    POST   /api/setup/connections/local-only          {on}: "I'll just use local AI"
    POST   /api/setup/connections/ollama              {base_url}
    POST   /api/setup/connections/endpoint            {kind: lmstudio|custom, label, base_url}
    POST   /api/setup/connections/mcp                 {name, url | command}
    DELETE /api/setup/connections/mcp/<name>
    POST   /api/setup/connections/mcp/<name>/sign-in  -> {auth_url}

The list itself is ``GET /api/setup/connections`` (routes/setup_chat.py). Same
gate as setup: the local user, on Friday's own computer, same-origin JSON. No
route here accepts or returns a key; keys go through
``POST /api/providers/<name>/key``.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from agent_friday.services import connections_setup as cx

_log = logging.getLogger("friday.connect_services")

connect_services_bp = Blueprint("connect_services", __name__)


@connect_services_bp.before_request
def _gate():
    from agent_friday.routes.setup_chat import _local_user_only
    return _local_user_only()


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _refused(e: Exception):
    """422 with the screen's fixed sentence for the refusal's code; nothing of
    the exception's own text."""
    message = cx.refusal_text(getattr(e, "code", ""), getattr(e, "connector", ""))
    return jsonify({"ok": False, "error": "refused", "message": message}), 422


@connect_services_bp.route("/api/setup/connections/<path:item_id>/test", methods=["POST"])
def connection_test(item_id):
    return jsonify({"ok": True, "id": item_id, **cx.test(item_id)})


@connect_services_bp.route("/api/setup/connections/local-only", methods=["POST"])
def connection_local_only():
    on = bool(_body().get("on", True))
    return jsonify({"ok": True, "local_only": cx.set_local_only(on)})


@connect_services_bp.route("/api/setup/connections/ollama", methods=["POST"])
def connection_ollama():
    try:
        return jsonify({"ok": True, **cx.save_ollama(str(_body().get("base_url") or ""))})
    except cx.Refused as e:
        return _refused(e)


@connect_services_bp.route("/api/setup/connections/endpoint", methods=["POST"])
def connection_endpoint():
    b = _body()
    try:
        return jsonify({"ok": True, **cx.save_endpoint(
            str(b.get("kind") or ""), str(b.get("label") or ""),
            str(b.get("base_url") or ""))})
    except cx.Refused as e:
        return _refused(e)
    except ValueError as e:      # the registry's own descriptor validation
        _log.debug("endpoint descriptor refused: %s", type(e).__name__)
        return _refused(cx.Refused("endpoint_not_accepted"))


@connect_services_bp.route("/api/setup/connections/mcp", methods=["POST"])
def connection_mcp_add():
    # An MCP server is a command or an address Friday will run or call as the owner: a press
    # on this PC's own screen (services/desk_press), not only a request that looks local.
    from agent_friday.services import screen_click
    refused = screen_click.press_refusal(
        request, "Add a connector with a press in Settings, on the computer Friday runs on.")
    if refused is not None:
        return refused
    b = _body()
    try:
        return jsonify({"ok": True, **cx.add_mcp(str(b.get("name") or ""),
                                                 url=str(b.get("url") or "").strip(),
                                                 command=str(b.get("command") or "").strip())})
    except cx.Refused as e:
        return _refused(e)


@connect_services_bp.route("/api/setup/connections/mcp/<name>", methods=["DELETE"])
def connection_mcp_remove(name):
    try:
        return jsonify({"ok": True, **cx.remove_mcp(name)})
    except cx.Refused as e:
        return _refused(e)


@connect_services_bp.route("/api/setup/connections/mcp/<name>/sign-in", methods=["POST"])
def connection_mcp_sign_in(name):
    try:
        return jsonify({"ok": True, **cx.sign_in_mcp(name)})
    except cx.Refused as e:
        return _refused(e)
