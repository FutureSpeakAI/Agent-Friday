"""The artifact panel's routes (docs/design/active/vibe-coding-salon.md §4.2).

  GET   /api/artifacts?conversation_id=          the conversation's artifacts (metadata)
  POST  /api/artifacts/<cid>                      make one from the panel (authored by "you")
  GET   /api/artifacts/<cid>/<aid>[?version=N][&include=versions]
                                                  one version, with content (and the timeline)
  GET   /api/artifacts/<cid>/<aid>/versions       the timeline (metadata)
  POST  /api/artifacts/<cid>/<aid>                a hand edit: a new version by "you"
  POST  /api/artifacts/<cid>/<aid>/restore        {version}: a new version with old content

Every write goes through services/artifacts.put, which asks off_record first.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import artifacts as art

artifacts_bp = Blueprint("artifacts", __name__)


def _bad(msg: str, code=400):
    return jsonify({"status": "error", "error": msg}), code


def _fail(exc, code=400):
    """A refused request. A `UserFacingError` shows its message; any other
    exception is logged and answered with a short error id."""
    return api_error(exc, "Couldn't complete that artifact request", code, key="error")


@artifacts_bp.route("/api/artifacts", methods=["GET"])
@login_required
def list_artifacts():
    cid = request.args.get("conversation_id") or ""
    try:
        return jsonify({"status": "ok", "artifacts": art.list_for(cid)})
    except ValueError as e:
        return _fail(e)


@artifacts_bp.route("/api/artifacts/<cid>", methods=["POST"])
@login_required
def create_artifact(cid):
    body = request.get_json(silent=True) or {}
    try:
        rec = art.put(cid, str(body.get("kind") or ""), str(body.get("title") or ""),
                      body.get("content"), meta=body.get("meta") if isinstance(body.get("meta"), dict) else None,
                      author="you", note=str(body.get("note") or "made in the panel"))
    except ValueError as e:
        return _fail(e)
    return jsonify({"status": "ok", "artifact": rec})


@artifacts_bp.route("/api/artifacts/<cid>/<aid>", methods=["GET"])
@login_required
def read_artifact(cid, aid):
    version = request.args.get("version")
    try:
        rec = art.get(cid, aid, version=int(version) if version else None)
    except ValueError as e:
        return _fail(e)
    if rec is None:
        return _bad("no such artifact or version", 404)
    out = {"status": "ok", "artifact": rec}
    if request.args.get("include") == "versions":
        # One request for the panel: the record and its timeline together,
        # because the page's connections to Friday are few and shared.
        out["versions"] = art.versions(cid, aid)
    return jsonify(out)


@artifacts_bp.route("/api/artifacts/<cid>/<aid>/versions", methods=["GET"])
@login_required
def artifact_versions(cid, aid):
    try:
        vs = art.versions(cid, aid)
    except ValueError as e:
        return _fail(e)
    if not vs:
        return _bad("no such artifact", 404)
    return jsonify({"status": "ok", "versions": vs})


@artifacts_bp.route("/api/artifacts/<cid>/<aid>", methods=["POST"])
@login_required
def edit_artifact(cid, aid):
    body = request.get_json(silent=True) or {}
    if "content" not in body:
        return _bad("content is required")
    try:
        rec = art.edit(cid, aid, body.get("content"), title=body.get("title") or None,
                       note=body.get("note") or None)
    except KeyError:
        return _bad("no such artifact", 404)
    except ValueError as e:
        return _fail(e)
    return jsonify({"status": "ok", "artifact": rec})


@artifacts_bp.route("/api/artifacts/<cid>/<aid>/plan/approve", methods=["POST"])
@login_required
def approve_plan(cid, aid):
    """The panel's "Build this plan": the user's approval, recorded as a new
    version, told to the conversation, and built on Friday's next turn."""
    from agent_friday.services import plans as _plans
    try:
        if art.get(cid, aid) is None:
            return _bad("no such artifact", 404)
        rec = _plans.approve(cid, aid, by="you")
    except ValueError as e:
        return _fail(e)
    try:
        from agent_friday.services import conversations as _convs
        _convs.append(cid, {"role": "system", "text": "\u2705 Plan approved: \"%s\". Friday builds it from the next turn, one milestone at a time." % rec["title"],
                            "meta": {"kind": "plan_approved", "artifact_id": aid}})
    except Exception:
        pass
    return jsonify({"status": "ok", "artifact": rec})


@artifacts_bp.route("/api/artifacts/<cid>/<aid>/restore", methods=["POST"])
@login_required
def restore_artifact(cid, aid):
    body = request.get_json(silent=True) or {}
    try:
        version = int(body.get("version"))
    except (TypeError, ValueError):
        return _bad("version is required")
    try:
        rec = art.restore(cid, aid, version)
    except KeyError:
        return _bad("no such version", 404)
    except ValueError as e:
        return _fail(e)
    return jsonify({"status": "ok", "artifact": rec})
