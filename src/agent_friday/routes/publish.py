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
from agent_friday.services import publish_web as pw

publish_bp = Blueprint("publish", __name__)


def _bad(msg, code=400):
    return jsonify({"status": "error", "error": str(msg)}), code


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
        return _bad(e)
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
        return _bad(e)
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
        return {"enabled": False, "serving": False, "url": None, "reachable": None,
                "line": "Published pages: hosting is unavailable (%s)." % e, "error": str(e)}


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
