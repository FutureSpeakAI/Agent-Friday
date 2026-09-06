"""Gauntlet finding: MCPManager.restart() bypasses the security-disable gate
that start_all() and authorize() both respect.

services/extension_security.gate_mcp_config() sets a server's config
`enabled: False` (on an in-memory copy) when its launch command trips the
destructive/download-and-execute scanner. MCPManager.load_config() turns
that into `sp.status = "disabled"` (mcp_client.py:742-744), and
start_all() (line 750-752) and authorize() (line 794-795) both check for
that status and refuse to run the server. restart() (line 770-781) does
not -- it unconditionally calls `sp.stop()` then `sp.start()`.

Consequence: a connector blocked at boot for a real security reason shows
up in /api/connectors as a generic "error: MCP server failed to start" (a
separate, queued documentation/status-message gap -- see progress.md), and
the single most natural thing an operator does in response -- click
Restart -- actually starts the blocked server for real, with no re-check
of the scanner's decision at all.

This probe must be RED before the fix (restart() calls sp.start() on a
disabled server) and GREEN after (it refuses, matching start_all()'s
existing guard).
"""
from __future__ import annotations

from agent_friday.mcp_client import MCPManager, MCPServerProcess


def _disabled_server(name="blocked-server") -> MCPServerProcess:
    sp = MCPServerProcess(name=name, command="npx", args=["some-blocked-pkg"])
    sp.status = "disabled"
    return sp


class TestMCPRestartRespectsDisabled:
    def test_restart_does_not_start_a_disabled_server(self, monkeypatch):
        mgr = MCPManager()
        sp = _disabled_server()
        mgr.servers = {sp.name: sp}

        started = []
        monkeypatch.setattr(sp, "start", lambda *a, **k: started.append(True) or True)
        monkeypatch.setattr(sp, "stop", lambda *a, **k: None)

        result = mgr.restart(sp.name)

        assert not started, (
            "MCPManager.restart() called sp.start() on a server whose status "
            "is 'disabled' -- the same guard start_all() already has "
            "(mcp_client.py:751-752) is missing here, so a server blocked by "
            "extension_security.gate_mcp_config() at boot can be started for "
            "real just by clicking Restart in the UI"
        )
        assert result is False

    def test_restart_still_works_for_a_normal_server(self, monkeypatch):
        """No-op check for the fix itself: restart() must still restart an
        ordinary (non-disabled) server -- the guard must be specific to
        'disabled', not a blanket refusal."""
        mgr = MCPManager()
        sp = MCPServerProcess(name="normal-server", command="npx", args=["ok-pkg"])
        sp.status = "stopped"
        mgr.servers = {sp.name: sp}

        started = []
        monkeypatch.setattr(sp, "start", lambda *a, **k: started.append(True) or True)
        monkeypatch.setattr(sp, "stop", lambda *a, **k: None)

        result = mgr.restart(sp.name)

        assert started, "restart() must still start a server that isn't disabled"
        assert result is True
