"""Publish to web (docs/design/active/vibe-coding-salon.md §4.10.1).

  POST  /api/publish/request                 {conversation_id, artifact_id, adapter?, version?}
                                              -> the one approval card, or a plain refusal
  GET   /api/publish/preview/<staging>/<path> the staged bundle, sandboxed, for the card
  GET   /api/publish/list                     what is up, per slug
  POST  /api/publish/<slug>/unpublish         take a page down
  GET   /api/publish/status                   the default adapter and each adapter's state

Nothing here publishes. The card does, once approved (services/publish_web).
"""
from __future__ import annotations

import mimetypes

from flask import Blueprint, Response, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error, log_failure
from agent_friday.services import publish_web as pw

publish_bp = Blueprint("publish", __name__)


def _bad(msg: str, code=400):
    return jsonify({"status": "error", "error": msg}), code


def _fail(exc, code=400):
    """A refused request. A `UserFacingError` shows its message; any other
    exception is logged and answered with a short error id."""
    return api_error(exc, "Couldn't complete that publishing request", code, key="error")


@publish_bp.route("/api/publish/request", methods=["POST"])
@login_required
def publish_request():
    body = request.get_json(silent=True) or {}
    cid = str(body.get("conversation_id") or "")
    aid = str(body.get("artifact_id") or "")
    version = body.get("version")
    try:
        out = pw.request_publish(cid, aid, adapter=body.get("adapter") or None,
                                 version=int(version) if version else None,
                                 requested_by=str(body.get("requested_by") or "panel"))
    except KeyError:
        return _bad("no such artifact", 404)
    except ValueError as e:
        return _fail(e)
    if out.get("refused"):
        return jsonify({"status": "refused", "refused": out["refused"], "scan": out.get("scan")})
    return jsonify({"status": "ok", "approval": out["approval"], "scan": out.get("scan")})


@publish_bp.route("/api/publish/preview/<staging>/<path:rel>", methods=["GET"])
@login_required
def publish_preview(staging, rel):
    p = pw.staged_file(staging, rel)
    if p is None:
        return _bad("not found", 404)
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
        ctype += "; charset=utf-8"
    resp = Response(p.read_bytes(), content_type=ctype)
    for k, v in pw.STRICT_HEADERS.items():
        resp.headers[k] = v
    return resp


@publish_bp.route("/api/publish/list", methods=["GET"])
@login_required
def publish_list():
    return jsonify({"status": "ok", "published": pw.list_published()})


@publish_bp.route("/api/publish/<slug>/unpublish", methods=["POST"])
@login_required
def publish_unpublish(slug):
    try:
        ok = pw.unpublish(slug)
    except ValueError as e:
        return _fail(e)
    if not ok:
        return _bad("nothing is published under that name", 404)
    return jsonify({"status": "ok", "slug": slug})


def _this_pc(refresh: bool = False) -> dict:
    try:
        from agent_friday.services import publish_hosting as _ph
        st = _ph.status(refresh=refresh)
        st["line"] = _ph.status_line()
        return st
    except Exception as e:
        # The cause goes to the local log; the panel gets the error id.
        error_id = log_failure(e, "Couldn't read the hosting status")
        return {"enabled": False, "serving": False, "url": None, "reachable": None,
                "line": "Published pages: hosting is unavailable (error %s)." % error_id,
                "error": "Hosting is unavailable (error %s)" % error_id}


@publish_bp.route("/api/publish/connect", methods=["POST"])
@login_required
def publish_connect():
    """Connect a hosted adapter with the user's own token. The token is stored
    encrypted in the credential store and is never echoed back."""
    body = request.get_json(silent=True) or {}
    adapter = str(body.get("adapter") or "")
    token = str(body.get("token") or "").strip()
    if adapter not in ("cloudflare_pages", "github_pages"):
        return _bad("adapter must be cloudflare_pages or github_pages")
    if not token:
        return _bad("a token is required")
    from agent_friday.services import publish_hosting as _ph
    try:
        _ph.connect_adapter(adapter, token, account_id=str(body.get("account_id") or ""),
                            project=str(body.get("project") or ""), repo=str(body.get("repo") or ""),
                            branch=str(body.get("branch") or ""))
    except ValueError as e:
        return _fail(e)
    return jsonify({"status": "ok", "adapter": adapter, "connected": _ph.adapter_connected(adapter)})


@publish_bp.route("/api/publish/disconnect", methods=["POST"])
@login_required
def publish_disconnect():
    body = request.get_json(silent=True) or {}
    adapter = str(body.get("adapter") or "")
    if adapter not in ("cloudflare_pages", "github_pages"):
        return _bad("adapter must be cloudflare_pages or github_pages")
    from agent_friday.services import publish_hosting as _ph
    _ph.disconnect_adapter(adapter)
    return jsonify({"status": "ok", "adapter": adapter, "connected": False})


@publish_bp.route("/api/publish/status", methods=["GET"])
@login_required
def publish_status():
    adapters = {"cloudflare_pages": {"connected": pw.adapter_connected("cloudflare_pages")},
                "github_pages": {"connected": pw.adapter_connected("github_pages")},
                "this_pc": _this_pc(refresh=request.args.get("refresh") in ("1", "true"))}
    return jsonify({"status": "ok", "default_adapter": pw.default_adapter(), "adapters": adapters,
                    "published": len(pw.list_published())})


@publish_bp.route("/api/publish/this-pc/<action>", methods=["POST"])
@login_required
def publish_this_pc_switch(action):
    """The owner's switch: `disable` takes every published page offline at
    once; `enable` brings the server and its tunnel back."""
    if action not in ("enable", "disable"):
        return _bad("action must be enable or disable", 404)
    from agent_friday.services import publish_hosting as _ph
    _ph.set_enabled(action == "enable")
    return jsonify({"status": "ok", "this_pc": _this_pc()})
