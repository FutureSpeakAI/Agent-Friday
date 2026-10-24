"""routes/import_staging.py -- the owner's answers to imported items (services/import_staging).

    GET  /api/import-staging            ?source=&status=open|proposed|deferred|accepted|rejected&kind=
    POST /api/import-staging/review     {id, answer: accept|edit|reject|defer, edits?}
                                        or {source, answer: accept|reject|defer, all: true, kind?}

Only the local owner on this PC reaches these routes, from Friday's own page. An answer is what
makes an imported item durable, so a POST counts only as a press in Friday's window on this PC
(services/screen_click), and goes through the action gate as the owner's own act, with a receipt.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import import_staging

import_staging_bp = Blueprint("import_staging", __name__)


@import_staging_bp.before_request
def _gate():
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"ok": False, "error": "imports are only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"ok": False, "error": "imports can only be reviewed on Friday's own computer"}), 403
    except Exception:
        return jsonify({"ok": False, "error": "could not establish that this request is local"}), 403
    if request.method == "POST":
        if not request.is_json:
            return jsonify({"ok": False, "error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"ok": False, "error": "cross-origin request refused"}), 403
    return None


@import_staging_bp.route("/api/import-staging", methods=["GET"])
@login_required
def import_staging_list():
    source = (request.args.get("source") or "").strip() or None
    status = (request.args.get("status") or "open").strip()
    kind = (request.args.get("kind") or "").strip() or None
    items = import_staging.list_items(source=source, status=status, kind=kind)
    return jsonify({"ok": True, "counts": import_staging.counts(source),
                    "items": [import_staging.public(it) for it in items[:500]],
                    "more": max(0, len(items) - 500)})


@import_staging_bp.route("/api/import-staging/review", methods=["POST"])
@login_required
def import_staging_review():
    from agent_friday.services import screen_click
    if not screen_click.screen_session(request):
        return jsonify({"ok": False, "code": "desk_proof_required",
                        "error": "An imported item is saved only by your own press in Friday's window on this PC."}), 403
    b = request.get_json(silent=True) or {}
    answer = str(b.get("answer") or "")[:12]
    from agent_friday.governance import action_gate
    v = action_gate.authorize("import_review", {"answer": answer, "id": str(b.get("id") or "")[:80],
                                                "source": str(b.get("source") or "")[:24],
                                                "all": bool(b.get("all"))},
                              {"screen_click": True, "surface": "import-staging"})
    if v.action != "allow":
        return jsonify({"ok": False, "error": "held: %s" % v.reason}), 403
    if b.get("all"):
        out = import_staging.review_class(str(b.get("source") or ""), answer,
                                          kind=(str(b.get("kind") or "") or None), by="owner:ui")
    else:
        edits = b.get("edits") if isinstance(b.get("edits"), dict) else None
        out = import_staging.review(str(b.get("id") or ""), answer, edits=edits, by="owner:ui")
    return jsonify(out), (200 if out.get("ok") else 400)
