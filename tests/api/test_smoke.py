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


#: The loops server.py starts outside FRIDAY_TESTING, by the name a thread
#: carries: an explicit name, or Python's default "Thread-N (<target>)".
_BOOT_DAEMONS = ("boot-reconcile", "_start_kill_hotkey",
                 "_notification_trigger_loop", "warm-embedder",
                 "_news_archiver_loop", "_network_monitor_loop",
                 "_prewarm_predicted_boot", "_predictive_prewarm_loop",
                 "connector_health_monitor_loop", "sweep_loop",
                 "_residency_boot")


def test_no_background_threads(server_module):
    """FRIDAY_TESTING must keep the import inert — no daemon loops.

    Asserted by NAME, not by counting every thread in the process: this
    process also runs whatever other test files share the worker, and their
    workers and caches legitimately start threads of their own."""
    import threading
    names = [t.name for t in threading.enumerate()]
    running = [n for n in names if any(d in n for d in _BOOT_DAEMONS)]
    assert not running, f"boot daemons running under FRIDAY_TESTING: {running}"


def test_llm_reaches_canned_text_through_the_real_body(server_module):
    """Post-D9 inversion: the provider FUNCTIONS are real; only the wire is fake.

    `_generate_text` runs its actual attempt-ladder and `_call_claude` builds and
    parses a real payload — the canned text arrives from the transport double,
    not from a lambda standing in for the function.
    """
    from tests.conftest import CANNED_TEXT
    assert server_module._generate_text([{"role": "user", "content": "hi"}]) == CANNED_TEXT
    text, calls = server_module._generate_agent([{"role": "user", "content": "hi"}])
    assert text == CANNED_TEXT and calls == []


def test_anthropic_client_is_an_offline_double(server_module):
    """The client factory yields a usable fake, not a tripwire sentinel.

    It must be usable, because the real `_call_claude` body now calls it — but
    it must never reach the network. Both properties are asserted here.
    """
    from tests.fake_backends import FakeAnthropicClient

    client = server_module.get_anthropic_client()
    assert isinstance(client, FakeAnthropicClient)

    resp = client.messages.create(model="x", messages=[])
    assert resp.stop_reason == "end_turn"
    assert client.calls[-1]["model"] == "x"


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
