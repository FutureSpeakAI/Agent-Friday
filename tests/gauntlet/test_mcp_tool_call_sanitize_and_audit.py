"""Gauntlet finding F36: extension_security.py's validate_tool_input,
validate_tool_output, and audit_tool_call were never called from either MCP
transport's real tool-call path -- the same shape as F32's env leak, on the
tool-call path instead of the spawn path.

TRUST_LEVELS (extension_security.py) declares "audit": True for every
level and "unicode_sanitize": True for sandboxed/untrusted -- a real,
named control the live GET /api/security/mcp-audit and /trust-levels
routes present as active. Before this fix:

  * A sandboxed/untrusted MCP server's tool OUTPUT reached the agent's
    context with invisible/control Unicode intact -- the exact
    steganographic injection vector sanitize_unicode exists to close.
  * GET /api/security/mcp-audit would return an empty log forever, no
    matter how many tool calls happened -- a live API presenting a
    non-existent audit trail.

This probe must be RED before the fix (invisible Unicode survives in the
tool result; audit_tool_call is never invoked) and GREEN after, for both
the stdio (MCPServerProcess) and Streamable HTTP (MCPServerHTTP)
transports.
"""
from __future__ import annotations

from agent_friday.mcp_client import MCPServerHTTP, MCPServerProcess
from agent_friday.services import extension_security as extsec

_INVISIBLE = "​"  # zero-width space, category Cf -- sanitize_unicode strips it
_PAYLOAD = "hello" + _INVISIBLE + "world"


def _fake_result(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


class TestStdioToolCallSanitizesAndAudits:
    def test_output_is_scrubbed_and_call_is_audited(self, monkeypatch):
        sp = MCPServerProcess(name="untrusted-pkg", command="npx",
                              args=["some-pkg"], trust_level="sandboxed")
        monkeypatch.setattr(sp, "_alive", lambda: True)
        monkeypatch.setattr(sp, "_request", lambda *a, **k: _fake_result(_PAYLOAD))
        audit_calls = []
        monkeypatch.setattr(extsec, "audit_tool_call",
                            lambda *a, **k: audit_calls.append((a, k)))

        out = sp.call_tool("some_tool", {"q": _PAYLOAD})

        assert _INVISIBLE not in out, (
            "a sandboxed MCP server's tool output still contains invisible "
            "Unicode -- validate_tool_output/sanitize_unicode was never "
            "called on the real call_tool() path"
        )
        assert out == "helloworld"
        assert audit_calls, (
            "call_tool() never called audit_tool_call() -- "
            "GET /api/security/mcp-audit is structurally empty regardless "
            "of how many tool calls happen"
        )

    def test_trusted_server_output_is_not_mangled(self, monkeypatch):
        """No-op-shaped sanity check: a 'trusted' server's normal (non-
        invisible-Unicode) output must pass through unchanged -- this fix
        must not corrupt ordinary tool results."""
        sp = MCPServerProcess(name="my-own-server", command="node",
                              args=["server.js"], trust_level="trusted")
        monkeypatch.setattr(sp, "_alive", lambda: True)
        monkeypatch.setattr(sp, "_request", lambda *a, **k: _fake_result("plain result"))
        out = sp.call_tool("some_tool", {"q": "plain arg"})
        assert out == "plain result"


class TestHttpToolCallSanitizesAndAudits:
    def test_output_is_scrubbed_and_call_is_audited(self, monkeypatch):
        hp = MCPServerHTTP(name="remote-untrusted", url="https://example.invalid/mcp",
                           trust_level="sandboxed")
        hp.status = "ready"
        monkeypatch.setattr(hp, "_request", lambda *a, **k: _fake_result(_PAYLOAD))
        audit_calls = []
        monkeypatch.setattr(extsec, "audit_tool_call",
                            lambda *a, **k: audit_calls.append((a, k)))

        out = hp.call_tool("some_tool", {"q": _PAYLOAD})

        assert _INVISIBLE not in out
        assert out == "helloworld"
        assert audit_calls, "HTTP transport's call_tool() never called audit_tool_call()"

    def test_load_config_resolves_trust_level_for_http_servers_too(self):
        """No-op-shaped sanity check on the wiring: MCPManager.load_config()
        must resolve trust_level for url-based (HTTP) servers the same way
        it does for stdio servers, not just default silently."""
        from agent_friday.mcp_client import MCPManager
        mgr = MCPManager()
        mgr.load_config({"servers": {
            "remote-trusted": {"url": "https://example.invalid/a",
                               "trust_level": "trusted"},
            "remote-sandboxed": {"url": "https://example.invalid/b"},
        }})
        assert mgr.servers["remote-trusted"].trust_level == "trusted"
        assert mgr.servers["remote-sandboxed"].trust_level == "sandboxed"
