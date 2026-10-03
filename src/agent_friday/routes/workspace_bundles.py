"""Bundle workspaces and "Improve this workspace" (salon spec §4.9.1, Phase 2b).

  GET   /api/workspaces/bundles                 the installed bundle workspaces (registry-shaped, group "mine")
  GET   /api/workspaces/<ws>/bundle             the current version's page, for the sandboxed frame
  GET   /api/workspaces/<ws>/versions           {current, versions, history}
  POST  /api/workspaces/<ws>/improve            open the codebase chat that improves it
                                                (409 with blocker needs_phase_7 for a native workspace)
  POST  /api/workspaces/swap    {codebase_id}   ONE approval card to swap the codebase in (409 when refused)
  POST  /api/workspaces/<ws>/rollback {sha256}  one click: an earlier version is current again
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import workspace_bundles as wb

workspace_bundles_bp = Blueprint("workspace_bundles", __name__)


def _bad(msg, code=400, **extra):
    body = {"status": "error", "error": str(msg)}
    body.update(extra)
    return jsonify(body), code


@workspace_bundles_bp.errorhandler(ValueError)
def _bad_value(e):
    return _bad(e)


@workspace_bundles_bp.errorhandler(KeyError)
def _missing(e):
    return _bad("no such workspace or version", 404)


@workspace_bundles_bp.route("/api/workspaces/bundles", methods=["GET"])
@login_required
def bundles():
    return jsonify({"status": "ok", "workspaces": wb.list_installed()})


@workspace_bundles_bp.route("/api/workspaces/<ws>/bundle", methods=["GET"])
@login_required
def bundle_page(ws):
    """The page itself, as HTML: the app's isolation headers add the sandbox
    CSP (never allow-same-origin), so even opened directly it holds no origin."""
    if wb.get(ws) is None:
        return _bad("no such workspace", 404)
    resp = Response(wb.html(ws), content_type="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@workspace_bundles_bp.route("/api/workspaces/<ws>/versions", methods=["GET"])
@login_required
def versions(ws):
    rec = wb.get(ws)
    if rec is None:
        return _bad("no such workspace", 404)
    return jsonify({"status": "ok", "current": rec["current"], "versions": rec["versions"], "history": rec.get("history", [])})


@workspace_bundles_bp.route("/api/workspaces/<ws>/improve", methods=["POST"])
@login_required
def improve(ws):
    try:
        out = wb.improve(ws)
    except wb.NativeWorkspace as e:
        return _bad(e, 409, status="refused", blocker=e.blocker)
    return jsonify({"status": "ok", **out})


@workspace_bundles_bp.route("/api/workspaces/swap", methods=["POST"])
@login_required
def swap():
    body = request.get_json(silent=True) or {}
    cid = str(body.get("codebase_id") or "")
    if not cid:
        return _bad("codebase_id is required")
    wb.register()
    try:
        card = wb.request_swap(cid, requested_by=str(body.get("requested_by") or "you"))
    except wb.SmokeFailed as e:
        return _bad(e, 409, status="refused", blocker=e.blocker)
    except (wb.BrandRefused, wb.ManifestRefused) as e:
        return _bad(e, 409, status="refused")
    except ValueError as e:
        return _bad(e, 409, status="refused")
    return jsonify({"status": "ok", "approval": card})


@workspace_bundles_bp.route("/api/workspaces/<ws>/rollback", methods=["POST"])
@login_required
def rollback(ws):
    body = request.get_json(silent=True) or {}
    rec = wb.rollback(ws, str(body.get("sha256") or ""), by="you")
    return jsonify({"status": "ok", "workspace": rec})
