"""Guest keys through the routes: added, listed without the secret, removed with a word."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import credential_store as cs

SECRET = "sk-ant-guest-test-222222222222"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_resident_brain", lambda: None)
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", tmp_path / "keys")
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 0.0)
    monkeypatch.setattr(cm, "codebase_costs", lambda cid, rng="all": {"total_usd": 0.0, "by_key_profile": {}, "calls": 0})
    yield


def test_guest_keys_through_the_routes(client):
    d = client.post("/api/codebases", json={"title": "Rent tracker", "template": "static"}).get_json()
    cid = d["codebase"]["id"]
    base = f"/api/codebases/{cid}"
    r = client.post(base + "/keys", json={"label": "Alex", "provider": "anthropic", "key": SECRET, "cap_usd": 5})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert SECRET not in body and "Alex" in body
    listed = client.get(base + "/keys").get_json()
    assert [k["label"] for k in listed["keys"]] == ["Alex"] and SECRET not in json.dumps(listed)
    assert client.post(base + "/keys", json={"label": "Alex", "provider": "anthropic", "key": SECRET}).status_code == 400
    assert client.post(base + "/keys", json={"label": "Bo", "provider": "nope", "key": SECRET}).status_code == 400
    # Now the key profile can be chosen, and the header names it.
    r = client.post(base + "/key", json={"profile": "Alex"})
    assert r.status_code == 200 and "Alex's key" in r.get_json()["header"]["text"]
    # Remove: deleted, said, and back to the owner's key.
    r = client.delete(base + "/keys/Alex")
    assert r.status_code == 200 and r.get_json()["deleted"] is True
    assert client.get(base + "/keys").get_json()["keys"] == []
    assert client.get(base + "/header").get_json()["key"] == "mine"
    assert client.delete(base + "/keys/Alex").status_code == 404
    # The codebase record itself never carries the secret.
    assert SECRET not in json.dumps(client.get(base).get_json())
