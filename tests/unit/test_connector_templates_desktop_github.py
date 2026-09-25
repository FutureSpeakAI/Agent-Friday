"""Connector templates: GitHub's own server, and Windows desktop read-only first.

The GitHub connector used the archived `@modelcontextprotocol/server-github`
package; it now names GitHub's official server (github/github-mcp-server).
The Windows desktop connector (Windows-MCP) ships with only its observing
tools enabled: an allowlist, so every action tool -- and any tool a later
version adds -- is off until the owner names it.
"""
from __future__ import annotations

import json

from agent_friday.services import connectors, desktop_grants as dg
from agent_friday.services import extension_security as xs


def test_github_uses_the_official_server_and_the_archived_one_is_gone():
    tmpl = connectors.CONNECTOR_DEFS["github"]["mcp_template"]
    assert "ghcr.io/github/github-mcp-server" in tmpl["args"]
    # The token is passed through the (encrypted) env, never on the command line.
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in tmpl["args"]
    assert not any("=" in str(a) for a in tmpl["args"])
    assert "server-github" not in json.dumps(connectors.CONNECTOR_DEFS)
    assert xs.assess_server("github", tmpl)["verdict"] != "block"


def test_windows_desktop_template_enables_only_observing_tools():
    d = connectors.CONNECTOR_DEFS["windows_desktop"]
    tmpl = d["mcp_template"]
    assert tmpl["command"] == "uvx" and tmpl["args"] == ["windows-mcp"]
    assert d["mcp_server"] == dg.DESKTOP_SERVER
    enabled = tmpl["enabled_tools"]
    assert enabled, "read-only first needs an allowlist, not an absent one"
    for name in enabled:
        kind = dg._kind(f"mcp_{dg.DESKTOP_SERVER}_{name}")
        assert kind in ("observe", "wait"), f"{name} is not a read tool ({kind})"
    for action in ("Click-Tool", "Type-Tool", "Powershell-Tool", "Launch-Tool",
                   "Clipboard-Tool", "Shortcut-Tool", "Scrape-Tool"):
        assert action not in enabled
    assert xs.assess_server("windows_desktop", tmpl)["verdict"] == "allow"


class _FakeAgent:
    def __init__(self, cfg):
        self.cfg = cfg

    def _load_mcp_servers(self):
        return json.loads(json.dumps(self.cfg))

    def _save_mcp_servers(self, cfg):
        self.cfg = cfg
        return cfg

    def _mcp_reload(self):
        return {"ok": True}


def test_connecting_writes_the_allowlist_and_keeps_an_owner_edited_one(monkeypatch):
    fake = _FakeAgent({"servers": {}})
    monkeypatch.setattr(connectors, "_mcp_bridge", lambda: fake)
    assert connectors.connect_connector("windows_desktop", {})["ok"]
    spec = fake.cfg["servers"]["windows_desktop"]
    assert spec["enabled_tools"] == connectors.CONNECTOR_DEFS["windows_desktop"][
        "mcp_template"]["enabled_tools"]

    spec["enabled_tools"] = ["State-Tool", "Click-Tool"]      # the owner turned one on
    connectors.connect_connector("windows_desktop", {})
    assert fake.cfg["servers"]["windows_desktop"]["enabled_tools"] == ["State-Tool",
                                                                      "Click-Tool"]
