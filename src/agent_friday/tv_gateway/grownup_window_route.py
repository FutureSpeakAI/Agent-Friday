"""/tv/v1/grownup: the grown-up window this PC issues and checks (services/tv_grownup_window).

  * GET  /tv/v1/grownup         any bound device: is a PIN set, is this session's window open (X-Friday-Window).
  * POST /tv/v1/grownup/open    the household TV, {"pin": "...."}: a grant on a match, bound to this session.
  * POST /tv/v1/grownup/close   the household TV: ends its windows (screen off, kids or family mode, Lock now).
  * POST /tv/v1/grownup/pin     an owner-bound phone, tablet or watch only, {"pin": "...."}: set the verifier.
    A TV or family device gets 403; the TV never sets the PIN it is checked against.

Tests: tests/unit/test_tv_grownup_window_route.py.
"""
from __future__ import annotations

from flask import g, jsonify, request


def register(app, refuse):
    from agent_friday.services import tv_grownup_window as win
    from agent_friday.tv_gateway import owner_turn

    def _grant():
        return request.headers.get("X-Friday-Window", "")

    @app.get("/tv/v1/grownup")
    def grownup_status():
        return jsonify({"ok": True, **win.status(g.device, _grant())})

    @app.post("/tv/v1/grownup/open")
    def grownup_open():
        body = request.get_json(silent=True) or {}
        try:
            grant = win.open_with_pin(g.device, g.session_key, body.get("pin"))
        except win.WindowRefused as e:
            return refuse(e.status, str(e), code=e.code)
        return jsonify({"ok": True, "window": grant, "ttl_s": win.TTL_S, "idle_s": win.IDLE_S})

    @app.post("/tv/v1/grownup/close")
    def grownup_close():
        win.close(g.device)
        return jsonify({"ok": True})

    @app.post("/tv/v1/grownup/pin")
    def grownup_pin():
        if not owner_turn.is_owner_device(g.device):
            return refuse(403, "the grown-up PIN is set on the owner's phone, tablet or PC", code="owner_only")
        body = request.get_json(silent=True) or {}
        try:
            win.set_pin(body.get("pin"))
        except ValueError as e:
            return refuse(400, str(e), code="bad_pin")
        return jsonify({"ok": True, "has_pin": True})
