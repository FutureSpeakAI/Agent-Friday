"""The owner's list of remote add-on server hosts over the API (BD-07, M1): listed without
paths or keys; changed only by a press on Friday's own page."""
from __future__ import annotations

import json

import pytest

REMOTE = "https://mcp.example.com/mcp?api_key=zq-not-a-real-key"  # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday.governance import mcp_hosts
    monkeypatch.setattr(mcp_hosts, "_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_hosts, "_key", lambda: b"test-governance-key-not-real")
    monkeypatch.setattr(mcp_hosts, "_configured_remote_urls", lambda: [])
    return tmp_path


def test_removing_a_host_takes_a_press_on_screen(home, client):
    from agent_friday.governance import mcp_hosts
    mcp_hosts.approve(REMOTE, made_by="owner:ui")
    r = client.post("/api/mcp/hosts/remove", json={"host": "mcp.example.com:443"})
    assert r.status_code == 403 and "Nothing was changed" in r.get_json()["message"]
    assert mcp_hosts.allowed(REMOTE)
    listed = client.get("/api/mcp/hosts").get_json()["hosts"]
    assert [h["host"] for h in listed] == ["mcp.example.com"]
    assert "zq-not-a-real-key" not in json.dumps(listed)


def test_the_owners_press_removes_a_host(home, client):
    from agent_friday import core
    from agent_friday.governance import mcp_hosts
    mcp_hosts.approve(REMOTE, made_by="owner:ui")
    press = {"X-Friday-Token": core._current_api_token(), "Sec-Fetch-Site": "same-origin",
             "Origin": "http://localhost"}
    r = client.post("/api/mcp/hosts/remove", json={"host": "mcp.example.com:443"}, headers=press)
    assert r.status_code == 200 and r.get_json()["removed"] is True
    assert r.get_json()["message"] == "Friday will not reach mcp.example.com any more."
    assert not mcp_hosts.allowed(REMOTE)
