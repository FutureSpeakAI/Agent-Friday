"""The pick and quick-style routes behind point-and-say."""
from __future__ import annotations

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    yield


def test_pick_quick_style_and_clear(client):
    rec = client.post("/api/codebases", json={"title": "T", "template": "static"}).get_json()["codebase"]
    base = f"/api/codebases/{rec['id']}"
    r = client.post(base + "/pick", json={"selector": "main > h1", "tag": "h1", "text": "T", "snippet": "<h1>T</h1>"})
    assert r.status_code == 200 and r.get_json()["pick"]["selector"] == "main > h1"
    assert client.get(base).get_json()["codebase"]["pick"]["tag"] == "h1"
    assert client.post(base + "/pick", json={"selector": "h1 {"}).status_code == 400
    r = client.post(base + "/quick-style", json={"selector": "main > h1", "action": "bigger"})
    assert r.status_code == 200 and r.get_json()["step"]["author"] == "you"
    # With the picked element's size on record, bigger is an absolute size.
    client.post(base + "/pick", json={"selector": "main > h1", "tag": "h1", "font_px": 22})
    r = client.post(base + "/quick-style", json={"selector": "main > h1", "action": "bigger"})
    assert r.status_code == 200 and "28px" in r.get_json()["step"]["summary"]
    assert client.post(base + "/quick-style", json={"selector": "main > h1", "action": "explode"}).status_code == 400
    r = client.post(base + "/quick-style", json={"selector": "main > h1", "prop": "color", "value": "#fff"})
    assert r.status_code == 200
    assert client.post(base + "/pick/clear").status_code == 200
    assert client.get(base).get_json()["codebase"].get("pick") is None
