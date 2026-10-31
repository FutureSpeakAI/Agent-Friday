"""GET /api/doctor/diagnostic (north star §33.5-6): the newest diagnostic
start report, read-only, on this PC only."""
from __future__ import annotations

import importlib.util


def _route_exists():
    assert importlib.util.find_spec("agent_friday.services.diagnostic_start") is not None, \
        "there is no diagnostic start mode"


def test_the_route_answers_on_this_pc_and_not_from_elsewhere(client):
    _route_exists()
    r = client.get("/api/doctor/diagnostic")
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d["status"] == "ok" and "active" in d and "report" in d
    assert r.headers.get("Cache-Control") == "no-store"
    r = client.get("/api/doctor/diagnostic", environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)


def test_choosing_a_diagnostic_start_needs_a_press_on_this_pc(client):
    _route_exists()
    r = client.post("/api/doctor/diagnostic", json={"next_start": True})
    assert r.status_code == 403 and r.get_json()["code"] == "press_required"
    from agent_friday.services import diagnostic_start
    assert not diagnostic_start.next_marker().exists()


def test_the_route_returns_the_newest_report(client, monkeypatch):
    _route_exists()
    from agent_friday.services import diagnostic_start, doctor, safe_mode
    monkeypatch.setenv("FRIDAY_DIAGNOSTIC_START", "1")
    monkeypatch.setattr(doctor, "build", lambda: {"report": "agent-friday-doctor", "checks": []})
    monkeypatch.setattr(diagnostic_start, "_STATE", {})
    safe_mode.reset_for_tests()
    try:
        rep = diagnostic_start.run_report()
    finally:
        safe_mode.reset_for_tests()
    try:
        d = client.get("/api/doctor/diagnostic").get_json()
        assert d["report"]["id"] == rep["id"] and "Start timeline" in d["text"]
    finally:
        for suffix in (".json", ".txt"):
            (diagnostic_start._dir() / (rep["id"] + suffix)).unlink(missing_ok=True)
