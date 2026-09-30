"""Codebases (docs/design/active/vibe-coding-salon.md §4.8, §5; Phase 2).

  POST  /api/codebases                       {title, template|path, conversation_id?}
                                              -> the codebase and the chat bound to it
  GET   /api/codebases                        every codebase
  GET   /api/codebases/<id>                   one
  GET   /api/codebases/<id>/files             the tree (no .git, no .friday)
  GET   /api/codebases/<id>/file?path=        one file
  POST  /api/codebases/<id>/file              {path, content}: a hand edit, a step by "you"
  GET   /api/codebases/<id>/steps             newest first, with receipts
  POST  /api/codebases/<id>/undo              revert the newest step not yet undone
  GET   /api/codebases/<id>/diff/<sha>        one step's unified diff
  GET   /api/codebases/<id>/preview           {html}: the one-document preview for the frame
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import codebases as cb

codebases_bp = Blueprint("codebases", __name__)


def _bad(msg, code=400):
    return jsonify({"status": "error", "error": str(msg)}), code


@codebases_bp.errorhandler(ValueError)
def _bad_value(e):
    return _bad(e)


@codebases_bp.errorhandler(KeyError)
def _missing(e):
    return _bad("no such codebase", 404)


@codebases_bp.route("/api/codebases", methods=["GET"])
@login_required
def list_codebases():
    return jsonify({"status": "ok", "codebases": cb.list_all()})


@codebases_bp.route("/api/codebases", methods=["POST"])
@login_required
def create_codebase():
    body = request.get_json(silent=True) or {}
    title = str(body.get("title") or "").strip() or "Untitled"
    cid = body.get("conversation_id") or None
    from agent_friday.services import conversations as convs
    conv = None
    if not cid:
        conv = convs.create(title)
        cid = conv["id"]
    rec = cb.create(title, template=str(body.get("template") or "static"),
                    conversation_id=cid, existing_path=body.get("path") or None)
    conv = conv or convs.load(cid)
    return jsonify({"status": "ok", "codebase": rec, "conversation": conv})


@codebases_bp.route("/api/codebases/<cid>", methods=["GET"])
@login_required
def get_codebase(cid):
    rec = cb.load(cid)
    if rec is None:
        return _bad("no such codebase", 404)
    return jsonify({"status": "ok", "codebase": rec})


@codebases_bp.route("/api/codebases/<cid>/files", methods=["GET"])
@login_required
def codebase_files(cid):
    return jsonify({"status": "ok", "files": cb.files(cid)})


@codebases_bp.route("/api/codebases/<cid>/file", methods=["GET"])
@login_required
def codebase_file(cid):
    rel = request.args.get("path") or ""
    content = cb.read(cid, rel)
    if content is None:
        return _bad("no such file", 404)
    return jsonify({"status": "ok", "path": rel, "content": content})


@codebases_bp.route("/api/codebases/<cid>/file", methods=["POST"])
@login_required
def codebase_write(cid):
    body = request.get_json(silent=True) or {}
    rel = str(body.get("path") or "")
    if "content" not in body:
        return _bad("content is required")
    st = cb.write(cid, rel, str(body.get("content") or ""))
    return jsonify({"status": "ok", "step": st})


@codebases_bp.route("/api/codebases/<cid>/steps", methods=["GET"])
@login_required
def codebase_steps(cid):
    return jsonify({"status": "ok", "steps": cb.steps(cid)})


@codebases_bp.route("/api/codebases/<cid>/undo", methods=["POST"])
@login_required
def codebase_undo(cid):
    try:
        st = cb.undo(cid)
    except cb.NothingToUndo as e:
        return _bad(e, 409)
    except RuntimeError as e:
        return _bad(e, 409)
    return jsonify({"status": "ok", "step": st})


@codebases_bp.route("/api/codebases/<cid>/diff/<sha>", methods=["GET"])
@login_required
def codebase_diff(cid, sha):
    return jsonify({"status": "ok", "sha": sha, "diff": cb.diff(cid, sha)})


@codebases_bp.route("/api/codebases/<cid>/export", methods=["GET"])
@login_required
def codebase_export(cid):
    """One click, one zip: the plain project, nothing of Friday's inside."""
    name, data = cb.export_zip(cid)
    resp = Response(data, content_type="application/zip")
    resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % name
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@codebases_bp.route("/api/codebases/<cid>/preview", methods=["GET"])
@login_required
def codebase_preview(cid):
    return jsonify({"status": "ok", "html": cb.preview(cid)})
