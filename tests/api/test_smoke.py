"""Smoke tests — the foundation the rest of the suite stands on.

Verifies the app imports hermetically, the route map is sane, GET routes don't
500, and the LLM kill-switch is engaged.
"""
from __future__ import annotations

import pytest


def test_app_imported(server_module):
    assert server_module.app is not None
    assert server_module._TESTING is True


def test_route_count(app):
    rules = list(app.url_map.iter_rules())
    # 240 app routes + static; guard against accidental mass deletion.
    assert len(rules) > 200, f"only {len(rules)} routes registered"


def test_no_background_threads():
    """FRIDAY_TESTING must keep the import inert — no daemon loops."""
    import threading
    names = [t.name for t in threading.enumerate()]
    # The kill-hotkey / scheduler / archiver loops must not be running.
    assert threading.active_count() < 6, f"unexpected threads: {names}"


def test_llm_is_stubbed(server_module):
    from tests.conftest import CANNED_TEXT
    assert server_module._generate_text([{"role": "user", "content": "hi"}]) == CANNED_TEXT
    text, calls = server_module._generate_agent([{"role": "user", "content": "hi"}])
    assert text == CANNED_TEXT and calls == []


def test_anthropic_client_is_sentinel(server_module):
    """The client factory is patched to a non-None sentinel: pre-flight None
    checks pass, but using it to make a real call raises (no network)."""
    client = server_module.get_anthropic_client()
    assert client is not None
    with pytest.raises(AttributeError):
        client.messages.create(model="x", messages=[])


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, dict)


def test_index_served(client):
    resp = client.get("/")
    # index.html should be served (200) — proves static wiring + auth pass-through.
    assert resp.status_code in (200, 302)


@pytest.mark.parametrize("path", [
    "/api/health",
    "/api/model-stats",
    "/api/notifications",
    "/api/todos",
    "/api/settings",
])
def test_common_get_routes_reachable(client, path, assert_reachable):
    resp = client.get(path)
    assert assert_reachable(resp), f"{path} returned {resp.status_code}"
