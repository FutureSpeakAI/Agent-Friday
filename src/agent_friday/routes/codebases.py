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
  GET   /api/codebases/<id>/export            the plain project as a zip (nothing of Friday's inside)
  POST  /api/codebases/<id>/pick              {selector, tag, text, snippet, rect}: what the user pointed at
  POST  /api/codebases/<id>/pick/clear        forget it
  POST  /api/codebases/<id>/quick-style       {selector, action} or {selector, prop, value}: one CSS rule,
                                              a step by "you", never a model call
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


@codebases_bp.route("/api/codebases/<cid>/pick", methods=["POST"])
@login_required
def codebase_pick(cid):
    body = request.get_json(silent=True) or {}
    return jsonify({"status": "ok", "pick": cb.set_pick(cid, body)})


@codebases_bp.route("/api/codebases/<cid>/pick/clear", methods=["POST"])
@login_required
def codebase_pick_clear(cid):
    return jsonify({"status": "ok", "cleared": cb.clear_pick(cid)})


@codebases_bp.route("/api/codebases/<cid>/quick-style", methods=["POST"])
@login_required
def codebase_quick_style(cid):
    """A simple property edit through the non-model patcher: one rule, one step by "you"."""
    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "")
    if action:
        if action not in cb.QUICK_ACTIONS:
            return _bad("unknown quick action %r" % action)
        rec = cb.load(cid)
        pick = (rec or {}).get("pick") or {}
        known = pick.get("font_px") if pick.get("selector") == str(body.get("selector") or "") else None
        prop, value = cb.quick_value(action, body.get("font_px") or known)
    else:
        prop, value = str(body.get("prop") or ""), str(body.get("value") or "")
    try:
        st = cb.quick_style(cid, str(body.get("selector") or ""), prop, value)
    except RuntimeError as e:
        return _bad(e, 409)
    return jsonify({"status": "ok", "step": st})


@codebases_bp.route("/api/codebases/<cid>/header", methods=["GET"])
@login_required
def codebase_header(cid):
    """The one line above the panel: seats, key, cost; with its spoken form."""
    return jsonify({"status": "ok", **cb.header(cid)})


@codebases_bp.route("/api/codebases/<cid>/seats", methods=["GET", "POST"])
@login_required
def codebase_seats(cid):
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        rec = cb.set_seat(cid, str(body.get("which") or ""), str(body.get("model") or ""), by="you")
    else:
        rec = cb.load(cid)
        if rec is None:
            return _bad("no such codebase", 404)
    return jsonify({"status": "ok", "seats": rec["seats"], "key_profile": rec.get("key_profile", "mine"), "header": cb.header(cid)})


@codebases_bp.route("/api/codebases/<cid>/key", methods=["POST"])
@login_required
def codebase_key(cid):
    body = request.get_json(silent=True) or {}
    rec = cb.set_key_profile(cid, str(body.get("profile") or ""), by="you")
    return jsonify({"status": "ok", "key_profile": rec["key_profile"], "header": cb.header(cid)})


@codebases_bp.route("/api/codebases/<cid>/costs", methods=["GET"])
@login_required
def codebase_costs(cid):
    if cb.load(cid) is None:
        return _bad("no such codebase", 404)
    from agent_friday.services import cost_meter as _cm
    return jsonify({"status": "ok", **_cm.codebase_costs(cid, request.args.get("range") or "all")})


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
