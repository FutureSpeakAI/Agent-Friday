"""The header line, the seats and the key profile through the routes (salon spec §4.7)."""
from __future__ import annotations

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_resident_brain", lambda: ("bonsai2:27b", "Bonsai2"))
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 0.0)
    monkeypatch.setattr(cm, "codebase_costs", lambda cid, rng="all": {"total_usd": 0.0, "by_key_profile": {}, "calls": 0})
    yield


def test_header_seats_key_and_costs_through_the_routes(client):
    d = client.post("/api/codebases", json={"title": "Rent tracker", "template": "static"}).get_json()
    cid, conv = d["codebase"]["id"], d["conversation"]["id"]
    base = f"/api/codebases/{cid}"
    h = client.get(base + "/header").get_json()
    assert h["text"].startswith("Rent tracker · Bonsai2 (this PC) for small edits") and h["spoken"]
    r = client.post(base + "/seats", json={"which": "heavy", "model": "claude-opus-5-5"})
    assert r.status_code == 200 and r.get_json()["header"]["text"].count("Opus 5.5") == 1
    assert client.get(base).get_json()["codebase"]["seats"]["heavy_seat"] == "claude-opus-5-5"
    assert any(m.get("kind") == "seat_change" for m in convs.messages(conv))
    assert client.post(base + "/seats", json={"which": "huge", "model": "x"}).status_code == 400
    assert client.post(base + "/key", json={"profile": "alex"}).status_code == 400
    assert client.post(base + "/key", json={"profile": "mine"}).status_code == 200
    c = client.get(base + "/costs").get_json()
    assert c["total_usd"] == 0.0 and "by_key_profile" in c
    assert client.get("/api/codebases/nope/header").status_code == 404
