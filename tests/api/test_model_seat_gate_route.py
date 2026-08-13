"""API tests for the FR-1 seat-change gate wired into POST /api/settings
(routes/core_routes.py::_check_local_model_seat_gate).

CAUTION: /api/settings in this test suite writes to the real
~/.friday/settings.json (same as the rest of tests/api/test_wiki_settings_routes.py
::TestSettings) — there is no per-test FRIDAY_DIR isolation. Every test here
that touches model_routing.local_model snapshots the pre-test value and
restores it in a finally block so a test run never leaves the machine's real
local-model seat changed. The conformance gate itself is always monkeypatched
so no test makes a live Ollama call.
"""
from __future__ import annotations

import pytest

from agent_friday.services import honesty_battery, model_seat_gate

HONESTY_GREEN = {"axis": "honesty", "passed": True, "score": "12/12"}
HONESTY_RED = {"axis": "honesty", "passed": False, "score": "8/12"}


@pytest.fixture
def preserve_local_model(client):
    before = client.get("/api/settings").get_json()["settings"]["model_routing"]["local_model"]
    try:
        yield before
    finally:
        client.post("/api/settings", json={"settings": {"model_routing": {"local_model": before}}})


class TestSeatGateRoute:
    def test_red_model_rejected_and_not_persisted(self, client, monkeypatch, preserve_local_model):
        monkeypatch.setattr(model_seat_gate, "get_cached_status", lambda *a, **k: None)
        monkeypatch.setattr(
            model_seat_gate, "run_conformance_gate",
            lambda model, **k: {"model": model, "provider": "local", "passed": False,
                                 "score": "0/10", "prose_leaks": [], "results": []},
        )
        resp = client.post("/api/settings",
                            json={"settings": {"model_routing": {"local_model": "some-red-model"}}})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["status"] == "error"
        assert "conformance gate" in body["message"]
        assert "some-red-model" not in body["message"] or "0/10" in body["message"]
        # Must not have been persisted.
        current = client.get("/api/settings").get_json()["settings"]["model_routing"]["local_model"]
        assert current == preserve_local_model

    def test_dual_green_model_accepted_and_persisted(self, client, monkeypatch, preserve_local_model):
        # A5: seating now needs BOTH axes green — structural conformance
        # AND the honesty battery.
        monkeypatch.setattr(model_seat_gate, "get_cached_status", lambda *a, **k: None)
        monkeypatch.setattr(
            model_seat_gate, "run_conformance_gate",
            lambda model, **k: {"model": model, "provider": "local", "passed": True,
                                 "score": "10/10", "prose_leaks": [], "results": []},
        )
        monkeypatch.setattr(honesty_battery, "get_honesty_status",
                            lambda *a, **k: dict(HONESTY_GREEN))
        resp = client.post("/api/settings",
                            json={"settings": {"model_routing": {"local_model": "some-green-model"}}})
        assert resp.status_code == 200
        current = client.get("/api/settings").get_json()["settings"]["model_routing"]["local_model"]
        assert current == "some-green-model"

    def test_structural_green_without_honesty_record_fails_closed(self, client, monkeypatch, preserve_local_model):
        monkeypatch.setattr(model_seat_gate, "get_cached_status",
                             lambda *a, **k: {"passed": True, "score": "10/10"})
        monkeypatch.setattr(honesty_battery, "get_honesty_status",
                            lambda *a, **k: None)
        resp = client.post("/api/settings",
                            json={"settings": {"model_routing": {"local_model": "structural-only-model"}}})
        assert resp.status_code == 400
        assert "honesty" in resp.get_json()["message"].lower()
        current = client.get("/api/settings").get_json()["settings"]["model_routing"]["local_model"]
        assert current == preserve_local_model

    def test_honesty_red_model_rejected_with_reason(self, client, monkeypatch, preserve_local_model):
        monkeypatch.setattr(model_seat_gate, "get_cached_status",
                             lambda *a, **k: {"passed": True, "score": "10/10"})
        monkeypatch.setattr(honesty_battery, "get_honesty_status",
                            lambda *a, **k: dict(HONESTY_RED))
        resp = client.post("/api/settings",
                            json={"settings": {"model_routing": {"local_model": "liar-model"}}})
        assert resp.status_code == 400
        body = resp.get_json()
        assert "honesty battery" in body["message"]
        assert body["honesty"]["score"] == "8/12"

    def test_cached_green_status_skips_a_new_gate_run(self, client, monkeypatch, preserve_local_model):
        monkeypatch.setattr(model_seat_gate, "get_cached_status",
                             lambda *a, **k: {"passed": True, "score": "10/10"})
        monkeypatch.setattr(honesty_battery, "get_honesty_status",
                            lambda *a, **k: dict(HONESTY_GREEN))
        calls = []
        monkeypatch.setattr(model_seat_gate, "run_conformance_gate",
                             lambda model, **k: calls.append(model) or {"passed": True})
        resp = client.post("/api/settings",
                            json={"settings": {"model_routing": {"local_model": "already-gated-model"}}})
        assert resp.status_code == 200
        assert calls == []

    def test_reassigning_the_same_model_never_calls_the_gate(self, client, monkeypatch, preserve_local_model):
        calls = []
        monkeypatch.setattr(model_seat_gate, "run_conformance_gate",
                             lambda model, **k: calls.append(model) or {"passed": False})
        resp = client.post(
            "/api/settings",
            json={"settings": {"model_routing": {"local_model": preserve_local_model}}},
        )
        assert resp.status_code == 200
        assert calls == []

    def test_unrelated_settings_never_touch_the_gate(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(model_seat_gate, "run_conformance_gate",
                             lambda model, **k: calls.append(model) or {"passed": False})
        resp = client.post("/api/settings", json={"settings": {"communication_style": "casual"}})
        assert resp.status_code == 200
        assert calls == []

    def test_gate_error_surfaces_as_400_not_500(self, client, monkeypatch, preserve_local_model):
        monkeypatch.setattr(model_seat_gate, "get_cached_status", lambda *a, **k: None)

        def _boom(model, **k):
            raise ConnectionError("Ollama unreachable")
        monkeypatch.setattr(model_seat_gate, "run_conformance_gate", _boom)
        resp = client.post("/api/settings",
                            json={"settings": {"model_routing": {"local_model": "unreachable-model"}}})
        assert resp.status_code == 400
        assert "not applied" in resp.get_json()["message"]
