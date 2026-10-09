"""A caller that gets the "still computing" health answer (the first request
after a cold start, when the full computation is slower than the wait) still
learns which release it is talking to. The version is a small file read, so the
fallback carries it: an installed Friday never reports a blank version."""
from __future__ import annotations

import threading

from flask import Flask


def test_the_computing_fallback_names_the_release(monkeypatch):
    from agent_friday.routes import core_routes as cr
    from agent_friday.services import app_version

    cr._reset_health_cache_for_tests()
    release = threading.Event()
    monkeypatch.setattr(cr, "_HEALTH_WAIT_S", 0.2, raising=False)
    monkeypatch.setattr(cr, "_health_payload", lambda: (release.wait(30.0), {"status": "ok"})[1])
    app = Flask(__name__)
    app.register_blueprint(cr.core_bp)
    try:
        body = app.test_client().get("/api/health").get_json()
    finally:
        release.set()
        cr._reset_health_cache_for_tests()
    assert body.get("computing") is True, "the first request got the fallback, not the full payload"
    assert body["version"] == app_version.running_version() and body["version"]
    assert body["release_name"] == app_version.display_version()
