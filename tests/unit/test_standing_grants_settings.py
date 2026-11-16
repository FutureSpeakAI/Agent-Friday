"""RED twin of K5 PR C: on main Settings has no list of standing grants and no way to stop an offer."""
from __future__ import annotations

import os

PAGE = {"X-Friday-Token": "the-page-itself"}


def _client(monkeypatch):
    from flask import Flask

    import agent_friday.core as core
    from agent_friday.routes import goals as route
    monkeypatch.setattr(core, "_is_local_request", lambda: True)
    monkeypatch.setattr(core, "_api_token_valid", lambda t: t == PAGE["X-Friday-Token"])
    app = Flask(__name__)
    app.secret_key = os.urandom(16)
    app.register_blueprint(route.goals_bp)
    return app.test_client()


def test_settings_has_a_list_of_standing_grants_for_the_owner_on_the_page(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/api/governance/standing-grants", headers=PAGE).status_code == 200
    assert client.get("/api/governance/standing-grants").status_code == 403


def test_an_offer_can_be_stopped_for_good(monkeypatch):
    client = _client(monkeypatch)
    reply = client.post("/api/governance/standing-grants/stop-offering", headers=PAGE, json={"pattern": "x"})
    assert reply.status_code == 200 and reply.get_json() == {"stopped": False}
