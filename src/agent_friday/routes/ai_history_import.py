"""routes/ai_history_import.py -- bring a ChatGPT or Claude export in (services/ai_history_import).

    POST /api/import/ai-history     multipart: file (conversations.json or the export .zip),
                                    preview=1 to count only. Answers: the preview counts, or what
                                    was staged for review (services/import_staging).

Only the local owner on this PC, from Friday's own page. Staging makes nothing durable: each
conversation waits for the owner's answer, which is a press on this PC (routes/import_staging).
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, g, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import ai_history_import as aih

ai_history_import_bp = Blueprint("ai_history_import", __name__)


@ai_history_import_bp.before_request
def _gate():
    try:
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"ok": False, "error": "imports are only available to the local user"}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"ok": False, "error": "imports can only be made on Friday's own computer"}), 403
        from agent_friday.services import screen_click
        if screen_click.is_cross_site(request):
            return jsonify({"ok": False, "error": "cross-site request refused"}), 403
    except Exception:
        return jsonify({"ok": False, "error": "could not establish that this request is local"}), 403
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return jsonify({"ok": False, "error": "cross-origin request refused"}), 403
    # An export can be larger than the app-wide request cap; this route reads up to its own limit.
    try:
        request.max_content_length = aih.MAX_FILE_BYTES
    except Exception:
        pass
    return None


@ai_history_import_bp.route("/api/import/ai-history", methods=["POST"])
@login_required
def ai_history_import():
    f = request.files.get("file") if request.files else None
    if f is None:
        return jsonify({"ok": False, "error": "Choose the conversations.json (or the export .zip) to import."}), 400
    data = f.read(aih.MAX_FILE_BYTES + 1)
    preview = str(request.form.get("preview") or "").lower() in ("1", "true", "yes")
    out = aih.preview(data) if preview else aih.stage(data)
    return jsonify(out), (200 if out.get("ok") else 400)
