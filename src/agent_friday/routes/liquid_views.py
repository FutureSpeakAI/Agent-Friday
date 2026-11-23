"""The desktop's side of A2UI workspaces: it draws the SAME document the phone draws (contract draft 3, section 4).

    GET /api/liquid/views/<ws>/rendered  -> {ok, ws, rev, as_of, tree, look}
    GET /liquid/<ws>                     -> the page that draws it with static/friday_a2ui.js (this PC only)

The tree is the reference renderer's bind (services/a2ui_render) of the stored-or-builtin document with the broker's
data (services/a2ui_broker), and his customization applied by the shared shell rule (apply_customization). The
browser only draws; it never validates or fetches data itself. tests/api/test_liquid_views_route.py holds the rules.
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, Response, jsonify

from agent_friday.core import login_required
from agent_friday.services import a2ui_broker, a2ui_render, a2ui_store, a2ui_workspaces

_log = logging.getLogger(__name__)

liquid_views_bp = Blueprint("liquid_views", __name__)

_PAGE = Path(__file__).resolve().parents[3] / "static" / "liquid_view.html"


def _served_block(ws: str):
    """The device block when ws has a view whose sections the block declares; None otherwise. Raises when the
    manifest cannot be read (the route answers 503, never a guessed sensitivity)."""
    from agent_friday.tv_gateway import ws_content
    if not a2ui_store._VIEW_ID.fullmatch(ws or ""):
        return None
    return ws_content.view_block(ws)


@liquid_views_bp.route("/api/liquid/views/<ws>/rendered", methods=["GET"])
@login_required
def liquid_view_rendered(ws):
    try:
        block = _served_block(ws)
    except Exception as e:
        _log.warning("liquid view %s: device block unreadable: %s", ws, type(e).__name__)
        return jsonify({"ok": False, "error": "Friday could not read this view right now."}), 503
    if block is None:
        return jsonify({"ok": False, "error": "This workspace has no view yet."}), 404
    try:
        view = a2ui_workspaces.current(ws)
        got = a2ui_broker.resolve(view["doc"], sensitivity=block["sensitivity"])
    except a2ui_broker.BrokerRefused:
        return jsonify({"ok": False, "error": "This view asks for data Friday does not give it."}), 422
    except Exception as e:
        _log.warning("liquid view %s failed: %s", ws, type(e).__name__)
        return jsonify({"ok": False, "error": "Friday could not read this view right now."}), 503
    rendered = a2ui_render.render(view["doc"], a2ui_workspaces.ctx(ws), data=got["data"])
    shown = a2ui_render.apply_customization(rendered.tree, view["customization"])
    return jsonify({"ok": rendered.ok, "ws": ws, "label": view["label"], "rev": view["rev"], "as_of": got["as_of"],
                    "tree": shown["tree"], "look": shown["look"], "refused": rendered.receipt})


def _page_path() -> Path:
    import os
    for p in (Path(os.path.abspath("static")) / "liquid_view.html", _PAGE):
        if p.is_file():
            return p
    return _PAGE


@liquid_views_bp.route("/liquid/<ws>", methods=["GET"])
@login_required
def liquid_view_page(ws):
    from agent_friday import core
    if not core._is_local_request():
        return "This view opens on this PC only.", 403
    try:
        block = _served_block(ws)
    except Exception as e:
        _log.warning("liquid page %s: device block unreadable: %s", ws, type(e).__name__)
        return "Friday could not read this view right now.", 503
    if block is None:
        return "This workspace has no view yet.", 404
    try:
        html = _page_path().read_text(encoding="utf-8")
    except OSError:
        return "View page not found", 404
    tok = ('<script>window.__FRIDAY_API_TOKEN="%s";</script>'  # pragma: allowlist secret
           % core._current_api_token())
    html = html.replace("<head>", "<head>\n" + tok, 1)
    resp = Response(html, content_type="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp
