"""Gauntlet finding: MCPServerProcess._spawn() handed every stdio MCP server
the ENTIRE parent process environment, including Friday's own live decrypted
cloud-provider API keys and other secrets, with no filtering at all.

services/extension_security.py has a complete, named mechanism built
specifically to prevent this -- ENV_BLOCKLIST ("Env vars MCP servers must
NEVER see"), TRUST_LEVELS (sandboxed/untrusted -> env_filter: True), and
sanitize_env_for_mcp() -- plus a live API surface
(routes/ext_security.py's /api/security/env-blocklist and
/api/security/trust-levels) that reads as though the control is active.
sanitize_env_for_mcp() had zero callers anywhere in the codebase.

Meanwhile credential_store.bootstrap_provider_env() decrypts every stored
provider key straight into os.environ at server boot, well before any MCP
server is spawned (server.py's own daemon-cascade ordering). _spawn()'s
`full_env = os.environ.copy()` inherited all of that, unfiltered, into
every stdio connector's subprocess -- a random npx/pip/uvx-launched
community MCP package (which defaults to "sandboxed" trust under this
codebase's own model) could read Friday's live ANTHROPIC_API_KEY /
OPENAI_API_KEY / GEMINI_API_KEY / vault key / etc. straight out of
os.environ, no exploit required.

This probe must be RED before the fix (a blocklisted key survives into the
subprocess env for the default/sandboxed trust level) and GREEN after.
"""
from __future__ import annotations

import os

import agent_friday.mcp_client as mc
from agent_friday.mcp_client import MCPManager, MCPServerProcess
from agent_friday.services import extension_security as extsec


class _FakePopen:
    """Captures the env= kwarg instead of actually spawning a process."""

    def __init__(self, *args, **kwargs):
        self.captured_env = kwargs.get("env")
        self.args = args
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def poll(self):
        return None


def _spawn_and_capture(monkeypatch, sp: MCPServerProcess) -> dict:
    monkeypatch.setattr(mc.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(mc.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())
    from agent_friday.services import connector_secrets as cse
    monkeypatch.setattr(cse, "decrypt_env", lambda env: env)
    sp._spawn()
    return sp.proc.captured_env


class TestMcpEnvLeakToSubprocess:
    def test_sandboxed_server_never_sees_friday_secrets(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REAL-SECRET")
        monkeypatch.setenv("FRIDAY_VAULT_KEY", "vault-REAL-SECRET")
        monkeypatch.setenv("SOME_UNRELATED_VAR", "harmless")

        sp = MCPServerProcess(name="community-pkg", command="npx",
                              args=["some-mcp-server"], trust_level="sandboxed")
        env = _spawn_and_capture(monkeypatch, sp)

        assert "ANTHROPIC_API_KEY" not in env, (
            "a sandboxed MCP server's subprocess environment still contains "
            "Friday's own live ANTHROPIC_API_KEY -- extension_security."
            "ENV_BLOCKLIST exists specifically to prevent this and was never "
            "wired into MCPServerProcess._spawn()"
        )
        assert "FRIDAY_VAULT_KEY" not in env
        assert env.get("SOME_UNRELATED_VAR") == "harmless", (
            "the filter must be a denylist of named secrets, not a broad "
            "allowlist -- ordinary env vars a subprocess needs (PATH, HOME, "
            "an unrelated var) must still pass through"
        )

    def test_default_trust_level_is_sandboxed_not_wide_open(self, monkeypatch):
        """No trust_level configured at all (every server today) must default
        to filtered, not to trusting-by-omission."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-REAL-SECRET")
        sp = MCPServerProcess(name="unconfigured", command="npx", args=["pkg"])
        env = _spawn_and_capture(monkeypatch, sp)
        assert "OPENAI_API_KEY" not in env

    def test_trusted_server_keeps_the_no_op_bypass(self, monkeypatch):
        """No-op-shaped sanity check: a server explicitly marked 'trusted'
        (extension_security.TRUST_LEVELS['trusted']['env_filter'] is False)
        must still receive the normal inherited environment -- proves the fix
        is trust-level-aware, not a blanket strip that would also break a
        server a user has explicitly vouched for."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-REAL-SECRET")
        sp = MCPServerProcess(name="my-own-server", command="node",
                              args=["server.js"], trust_level="trusted")
        env = _spawn_and_capture(monkeypatch, sp)
        assert env.get("ANTHROPIC_API_KEY") == "sk-ant-REAL-SECRET"

    def test_connectors_own_configured_env_still_passes_through(self, monkeypatch):
        """No-op-shaped sanity check: a connector's own (legitimately
        configured, separately-encrypted) env vars must still reach it even
        when the parent-env blocklist filter is active."""
        sp = MCPServerProcess(name="needs-its-own-token", command="npx",
                              args=["pkg"], env={"MY_CONNECTOR_TOKEN": "tok-123"},
                              trust_level="sandboxed")
        env = _spawn_and_capture(monkeypatch, sp)
        assert env.get("MY_CONNECTOR_TOKEN") == "tok-123"

    def test_load_config_resolves_trust_level_from_server_spec(self, monkeypatch):
        """No-op-shaped sanity check on the wiring, not just the primitive:
        MCPManager.load_config() must actually read trust_level out of each
        server's own config (extension_security.get_trust_level) rather than
        every server silently defaulting regardless of what's configured."""
        mgr = MCPManager()
        mgr.load_config({"servers": {
            "trusted-one": {"command": "node", "args": ["a.js"],
                            "trust_level": "trusted"},
            "sandboxed-one": {"command": "node", "args": ["b.js"]},
        }})
        assert mgr.servers["trusted-one"].trust_level == "trusted"
        assert mgr.servers["sandboxed-one"].trust_level == "sandboxed"
