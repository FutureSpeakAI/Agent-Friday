"""A short list of real items for the owner to label, one card at a time (services/laya_triage_labels).

  GET  /laya/labels                the page (static/laya_labels.html), on this PC only
  GET  /api/decisions/triage_queue the items still waiting, and how far along he is
  POST /api/decisions/triage_labels {subject, answer: yes|no|skip}: one answer, written
                                   to laya_labels.jsonl with a receipt that chains to the last
  GET  /api/decisions/triage_score his answers set against what each head and the incumbent said

Not on the observer's read-only list (services/observer_access): the items are the owner's own
notifications and news.
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import laya_triage_labels as triage

laya_labels_bp = Blueprint("laya_labels_page", __name__)

_PAGE = Path(__file__).resolve().parents[3] / "static" / "laya_labels.html"
_ON_THIS_PC = "These questions open on this PC only."


def _page_path() -> Path:
    # the same folder /static/ is served from (core_routes.serve_static_asset)
    for p in (Path(os.path.abspath("static")) / _PAGE.name, _PAGE):
        if p.is_file():
            return p
    return _PAGE


@laya_labels_bp.route("/laya/labels", methods=["GET"])
@login_required
def labels_page():
    from agent_friday import core
    if not core._is_local_request():
        return _ON_THIS_PC, 403
    try:
        html = _page_path().read_text(encoding="utf-8")
    except OSError:
        return "The label page was not found", 404
    tok = ('<script>window.__FRIDAY_API_TOKEN="%s";</script>'  # pragma: allowlist secret
           % core._current_api_token())
    resp = Response(html.replace("<head>", "<head>\n" + tok, 1), content_type="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@laya_labels_bp.route("/api/decisions/triage_queue", methods=["GET"])
@login_required
def triage_queue():
    from agent_friday import core
    if not core._is_local_request():
        return jsonify({"status": "error", "message": _ON_THIS_PC}), 403
    try:
        return jsonify({"status": "ok", **triage.queue(), "valid_chain": triage.chain_ok()})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "Couldn't read the questions")


@laya_labels_bp.route("/api/decisions/triage_labels", methods=["POST"])
@login_required
def triage_label():
    from agent_friday import core
    if not core._is_local_request():
        return jsonify({"status": "error", "message": _ON_THIS_PC}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "Send the item and your answer."}), 400
    try:
        row = triage.answer(str(data.get("subject") or ""), str(data.get("answer") or ""))
    except ValueError as e:
        return api_error(e, "Couldn't save that answer", 400)
    except Exception as e:  # noqa: BLE001
        return api_error(e, "Couldn't save that answer")
    return jsonify({"status": "ok", "receipt": row["receipt"], "answer": row["answer"]})


@laya_labels_bp.route("/api/decisions/triage_score", methods=["GET"])
@login_required
def triage_score():
    from agent_friday import core
    if not core._is_local_request():
        return jsonify({"status": "error", "message": _ON_THIS_PC}), 403
    try:
        return jsonify({"status": "ok", "score": triage.score(), "valid_chain": triage.chain_ok()})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "Couldn't read the score")
