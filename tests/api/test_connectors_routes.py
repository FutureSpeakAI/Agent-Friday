"""One-click connector registry API — /api/connectors/*.

The suite is fully offline: the Google one-click flow's subprocess launch and the
MCP hot-reload (which would spawn real `npx` servers) are stubbed so connect/
disconnect only exercise config + status logic, never a real process or network.
"""
from __future__ import annotations

import json

import pytest

import services.agent as agent_svc
import services.connectors as conn


@pytest.fixture(autouse=True)
def _no_subprocess(monkeypatch):
    """Never spawn the Google consent subprocess or real MCP servers in tests."""
    monkeypatch.setattr(
        conn, "_launch_google_connect",
        lambda: {"ok": True, "status": "connecting", "message": "stubbed consent"},
    )
    monkeypatch.setattr(conn, "_google_web_auth_url", lambda host: None)
    # _mcp_reload() would call _mcp_boot() → start_all() → spawn npx. No-op it.
    monkeypatch.setattr(agent_svc, "_mcp_reload", lambda: {"ok": True})


def test_list_connectors(client):
    resp = client.get("/api/connectors")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    keys = {c["key"] for c in data["connectors"]}
    assert {"google", "slack", "github", "linear", "notion", "discord"} <= keys
    # Health summary travels alongside the list.
    assert "summary" in data["health"]
    assert data["health"]["total"] == len(data["connectors"])
    # Every connector advertises which workspaces it powers (connector-aware UI).
    for c in data["connectors"]:
        assert isinstance(c["workspaces"], list)
        assert c["color"].startswith("#")


def test_health_endpoint(client):
    resp = client.get("/api/connectors/health")
    assert resp.status_code == 200
    h = resp.get_json()["health"]
    assert h["connected"] >= 0
    assert h["total"] >= 6
    assert isinstance(h["degraded"], list)


def test_intelligence_endpoint(client):
    resp = client.get("/api/connectors/intelligence")
    assert resp.status_code == 200
    intel = resp.get_json()["intelligence"]
    # Shape contract — empty when nothing is connected, never an error.
    assert "sections" in intel and "signals" in intel and "markdown" in intel


def test_single_connector_and_404(client):
    resp = client.get("/api/connectors/github")
    assert resp.status_code == 200
    assert resp.get_json()["connector"]["name"] == "GitHub"
    # GitHub needs a PAT field.
    fields = resp.get_json()["connector"]["fields"]
    assert any(f["key"] == "GITHUB_PERSONAL_ACCESS_TOKEN" for f in fields)

    missing = client.get("/api/connectors/nope")
    assert missing.status_code == 404


def test_mcp_connect_requires_token(client):
    """Connecting an MCP connector with no token reports needs_setup, not success."""
    resp = client.post("/api/connectors/slack/connect", json={})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "error"
    assert "Missing" in data["message"]


def test_mcp_connect_then_disconnect(client, server_module):
    """A token write enables the server in mcp_servers.json; disconnect disables it."""
    resp = client.post(
        "/api/connectors/github/connect",
        json={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_testtoken"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    # Persisted: enabled with the token in env (home is a temp dir under test).
    cfg = json.loads(agent_svc.MCP_SERVERS_FILE.read_text(encoding="utf-8"))
    gh = cfg["servers"]["github"]
    assert gh["enabled"] is True
    assert gh["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_testtoken"

    # The status endpoint must NOT echo the secret value back, only that it's set.
    status = client.get("/api/connectors/github").get_json()["connector"]
    pat_field = next(f for f in status["fields"] if f["key"] == "GITHUB_PERSONAL_ACCESS_TOKEN")
    assert pat_field["set"] is True
    assert "ghp_testtoken" not in json.dumps(status)

    # Disconnect keeps the config but flips enabled off.
    d = client.post("/api/connectors/github/disconnect")
    assert d.status_code == 200
    cfg2 = json.loads(agent_svc.MCP_SERVERS_FILE.read_text(encoding="utf-8"))
    assert cfg2["servers"]["github"]["enabled"] is False
    # Token is retained so reconnecting is one click.
    assert cfg2["servers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_testtoken"


def test_oauth_connect_is_one_click(client):
    """Google connect (no fields) drives the consent flow immediately."""
    resp = client.post("/api/connectors/google/connect", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["connector"]["kind"] == "oauth"
