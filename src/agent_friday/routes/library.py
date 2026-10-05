"""Routes for the Library workspace (services/library/).

Same-origin, token-checked, and closed to the observer principal. The
principal comes from the request context, never from an argument. Reads are
GETs; the POSTs that change the Library (add, remove, forget, shelf, reindex)
need a JSON body and refuse a cross-origin Origin, and each is the owner's own
act in the workspace (a folder picked, a button pressed after a confirmation
that names the document).
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, Response, jsonify, request, send_file, stream_with_context

import agent_friday.core as core
from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import screen_click, studio_files as sf
from agent_friday.services.library import (answer, api, forget, grants, pages, principal as pr, procrun,
                                           runtime, search)
from agent_friday.services.library.store import store_for
from agent_friday.user_errors import UserFacingValueError

library_bp = Blueprint("library", __name__)

RAW_CSP = "sandbox; default-src 'none'"


def _principal():
    p = pr.current()
    if p is None:
        return None, (jsonify({"status": "denied", "error": "the Library is not available to this account"}), 403)
    runtime.start_background()
    return p, None


@library_bp.before_request
def _not_another_site():
    """Nothing in the Library answers a request another site's page caused: an image,
    audio or fetch aimed here would otherwise leak which documents exist."""
    if screen_click.is_cross_site(request):
        return jsonify({"status": "denied", "error": "cross-site request refused"}), 403
    if not core._api_token_valid(request.headers.get("X-Friday-Token")):
        return jsonify({"status": "denied", "error": "the Library answers only Friday's own page"}), 403
    return None


#: What a change names, so the receipt of its decision names it too (never any document text).
_TARGET_KEYS = ("doc_id", "target_id", "scope_id", "path", "shelf", "recursive", "glob")


def _gated(op: str, args: dict | None = None):
    """A change to the Library goes through the action gate like every other state change:
    the owner's verified click on the screen is the decision, so it is classed internal and
    receipted (with the target it names); anything else is held."""
    from agent_friday.governance import action_gate
    v = action_gate.authorize(op, args or {}, {"screen_click": True, "surface": "library-workspace"})
    if v.action != "allow":
        return jsonify({"status": "denied", "error": f"held: {v.reason}"}), 403
    return None


def _same_origin_json(op: str = ""):
    """A change to the Library is the owner's own act in the workspace: a JSON POST from a
    browser page of this server, never a bare call."""
    if not request.is_json:
        return jsonify({"status": "error", "error": "JSON body required"}), 415
    if not screen_click.screen_session(request):
        return jsonify({"status": "denied", "error": "this change must come from the Library page"}), 403
    if not op:
        return None
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    return _gated(op, {k: body[k] for k in _TARGET_KEYS if k in body and isinstance(body[k], (str, int, bool, type(None)))})


@library_bp.route("/api/library/status")
@login_required
def lib_status():
    p, bad = _principal()
    if bad:
        return bad
    st = runtime.prepare(p)
    return jsonify({"status": "ok", **api.status(st, p, runtime.indexer_for(p).pending())})


@library_bp.route("/api/library/tree")
@login_required
def lib_tree():
    p, bad = _principal()
    if bad:
        return bad
    st = runtime.prepare(p)
    return jsonify({"status": "ok", **api.nodes(st, p, request.args.get("node", ""), request.args.get("depth", 1, type=int))})


@library_bp.route("/api/library/document/<int:doc_id>")
@login_required
def lib_document(doc_id):
    p, bad = _principal()
    if bad:
        return bad
    d = api.document_detail(runtime.prepare(p), p, doc_id)
    if not d:
        return jsonify({"status": "denied", "error": "no such document in your Library"}), 404
    return jsonify({"status": "ok", "document": d})


@library_bp.route("/api/library/section/<int:section_id>")
@login_required
def lib_section(section_id):
    p, bad = _principal()
    if bad:
        return bad
    d = api.section_passages(runtime.prepare(p), p, section_id)
    if not d:
        return jsonify({"status": "denied", "error": "source no longer in your Library"}), 404
    return jsonify({"status": "ok", "section": d})


@library_bp.route("/api/library/search")
@login_required
def lib_search():
    p, bad = _principal()
    if bad:
        return bad
    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "").strip() or None
    want_answer = request.args.get("answer") == "1"
    floor = bool(_settings().get("library_floor_tier", False))
    runtime.prepare(p)

    def stream():
        done = None
        try:
            for ev in search.search(q, principal=p, scope=scope, floor_tier=floor):
                if ev["event"] == "done":
                    done = ev["result"]
                    continue
                yield "data: " + json.dumps(ev) + "\n\n"
            if done is not None and want_answer and done.get("evidence"):
                out = answer.write(q, done["evidence"], stamp=done.get("stamp"), receipt=done.get("receipt"))
                yield "data: " + json.dumps({"event": "answer" if out["ok"] else "answer_unavailable",
                                              "text": out.get("text"), "reason": out.get("reason")}) + "\n\n"
        except Exception as e:  # noqa: BLE001 - a plain cause, never a traceback
            yield "data: " + json.dumps({"event": "failed", "error": "The search could not finish (%s)." % type(e).__name__}) + "\n\n"
        yield "data: " + json.dumps({"event": "done", "receipt": (done or {}).get("receipt"),
                                      "stamp": (done or {}).get("stamp")}) + "\n\n"

    return Response(stream_with_context(stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


@library_bp.route("/api/library/block/<int:block_id>")
@login_required
def lib_block(block_id):
    p, bad = _principal()
    if bad:
        return bad
    b = api.block(runtime.prepare(p), p, block_id)
    if not b:
        return jsonify({"status": "denied", "error": "source no longer in your Library"}), 404
    return jsonify({"status": "ok", "block": b})


def _doc_path(p: str, doc_id: int):
    st = runtime.prepare(p)
    row = api.tree.TreeBuilder(st, p).visible_docs().get(doc_id)
    if not row:
        return None, None
    path = Path(row["path"])
    return (path, row) if path.is_file() else (None, row)


@library_bp.route("/api/library/page/<int:doc_id>/<int:n>.webp")
@login_required
def lib_page(doc_id, n):
    p, bad = _principal()
    if bad:
        return bad
    path, row = _doc_path(p, doc_id)
    if path is None or row["kind"] != "pdf":
        return jsonify({"status": "denied", "error": "no page image for this document"}), 404
    try:
        got = pages.render_pdf_page(path, n, request.args.get("w", 900, type=int) or 900,
                                    cache=row["shelf"] != "vault")
    except procrun.TaskFailed as e:
        return jsonify({"status": "error", "error": "Couldn't read this page: " + e.reason}), 422
    resp = Response(got["data"], mimetype=got["mime"])
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store" if row["shelf"] == "vault" else "private, max-age=3600"
    resp.headers["Content-Security-Policy"] = RAW_CSP
    resp.headers["X-Page-Points"] = "%s,%s" % tuple(got["page_pt"])
    resp.headers["X-Pdf-Pages"] = str(got["pages"])
    return resp


@library_bp.route("/api/library/raw/<int:doc_id>")
@login_required
def lib_raw(doc_id):
    p, bad = _principal()
    if bad:
        return bad
    path, row = _doc_path(p, doc_id)
    if path is None:
        return jsonify({"status": "denied", "error": "the file is not in your Library any more"}), 404
    mimetype, _inline = sf.serve_type(path)
    resp = send_file(str(path), mimetype=mimetype, conditional=True, as_attachment=False, download_name=path.name)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = RAW_CSP
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Cache-Control"] = "no-store" if row["shelf"] == "vault" else "private, no-cache"
    return resp


@library_bp.route("/api/library/receipt/<search_id>")
@login_required
def lib_receipt(search_id):
    p, bad = _principal()
    if bad:
        return bad
    r = api.receipt(runtime.prepare(p), search_id)
    return (jsonify({"status": "ok", "receipt": r}) if r
            else (jsonify({"status": "denied", "error": "no record of that search"}), 404))


@library_bp.route("/api/library/add", methods=["POST"])
@login_required
def lib_add():
    bad = _same_origin_json("library_add")
    if bad:
        return bad
    p, bad = _principal()
    if bad:
        return bad
    b = request.get_json(silent=True) or {}
    if b.get("root"):                       # a folder Friday already lists (Documents, Downloads, ...)
        try:
            b["path"] = str(sf.roots()[str(b["root"])])
        except (KeyError, Exception):
            return jsonify({"status": "denied", "error": "that is not one of your folders"}), 400
    try:
        ev = grants.add_scope(p, b.get("path", ""), recursive=bool(b.get("recursive", True)),
                              glob=b.get("glob") or None, source="you")
    except UserFacingValueError as e:
        return jsonify({"status": "denied", "error": e.user_message}), 400
    runtime.index_scope(p, ev)
    return jsonify({"status": "ok", "scope": api._scope_row(store_for(p), ev)})


@library_bp.route("/api/library/tracked", methods=["GET", "POST"])
@login_required
def lib_tracked():
    """The "everything Friday already tracks" switch. POST {on: true} records the
    one bulk consent (the owner's own press) and starts reading; {on: false}
    removes that consent, and what only it covered leaves the Library."""
    p, bad = _principal()
    if bad:
        return bad
    if request.method == "POST":
        bad = _same_origin_json("library_tracked")
        if bad:
            return bad
        b = request.get_json(silent=True) or {}
        if b.get("on") is True:
            ev = grants.add_tracked(p, source="you")
            runtime.index_scope(p, ev)
        elif b.get("on") is False:
            a = grants.tracked_consent(p)
            if a is not None:
                grants.remove_scope(p, a["id"])
                runtime.purge_uncovered(p)
        else:
            return jsonify({"status": "error", "error": "say on: true or on: false"}), 400
    return jsonify({"status": "ok", "tracked": api.tracked_state(store_for(p), p,
                                                                 runtime.indexer_for(p).pending())})


@library_bp.route("/api/library/remove", methods=["POST"])
@login_required
def lib_remove():
    bad = _same_origin_json("library_remove")
    if bad:
        return bad
    p, bad = _principal()
    if bad:
        return bad
    b = request.get_json(silent=True) or {}
    if b.get("scope_id"):
        grants.remove_scope(p, str(b["scope_id"]))
        n = runtime.purge_uncovered(p)
        return jsonify({"status": "ok", "removed_documents": n})
    if b.get("doc_id"):
        return jsonify({"status": "ok", **forget.remove_document(p, int(b["doc_id"]))})
    return jsonify({"status": "error", "error": "say what to remove"}), 400


@library_bp.route("/api/library/forget", methods=["POST"])
@login_required
def lib_forget():
    bad = _same_origin_json("library_forget")
    if bad:
        return bad
    p, bad = _principal()
    if bad:
        return bad
    b = request.get_json(silent=True) or {}
    if not b.get("doc_id") or b.get("confirm") is not True:
        return jsonify({"status": "error", "error": "forgetting needs the owner's confirmation"}), 400
    return jsonify({"status": "ok", **forget.forget_document(p, int(b["doc_id"]))})


@library_bp.route("/api/library/shelf", methods=["POST"])
@login_required
def lib_shelf():
    bad = _same_origin_json("library_shelf")
    if bad:
        return bad
    p, bad = _principal()
    if bad:
        return bad
    b = request.get_json(silent=True) or {}
    st = runtime.prepare(p)
    row = st.get_document(int(b.get("doc_id") or 0))
    if not row or b.get("shelf") not in ("open", "vault"):
        return jsonify({"status": "error", "error": "unknown document or shelf"}), 400
    if row["shelf"] == "vault" and b["shelf"] == "open" and b.get("confirm") is not True:
        return jsonify({"status": "error", "error": "moving a document off the vault shelf needs your confirmation"}), 400
    grants.set_shelf(p, row["path"], b["shelf"])
    if b["shelf"] == "vault" and row["shelf"] != "vault":
        forget.left_the_open_shelf(p, row["id"])
    runtime.indexer_for(p).enqueue(row["path"], recursive=False, force=True)
    return jsonify({"status": "ok", "shelf": b["shelf"]})


@library_bp.route("/api/library/reindex", methods=["POST"])
@login_required
def lib_reindex():
    bad = _same_origin_json("library_reindex")
    if bad:
        return bad
    p, bad = _principal()
    if bad:
        return bad
    return jsonify({"status": "ok", "scopes": runtime.resweep(p)})
