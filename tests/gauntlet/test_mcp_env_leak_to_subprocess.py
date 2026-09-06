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
            "SANDBOXED_ENV_ALLOWLIST exists specifically to prevent this and "
            "was never wired into MCPServerProcess._spawn()"
        )
        assert "FRIDAY_VAULT_KEY" not in env
        # CORRECTION (F67, 2026-09-04): this assertion used to REQUIRE the
        # opposite -- that an arbitrary, unrelated env var DID pass through
        # ("the filter must be a denylist of named secrets, not a broad
        # allowlist"). That was the defect, not a property to protect: a
        # denylist has to name every secret that will ever exist, and 6 more
        # real provider keys were found missing from it after this test was
        # written. Stephen's ruling inverted the design -- a sandboxed
        # subprocess environment is now BUILT FROM an allowlist of names
        # subprocesses functionally need, so an arbitrary unrelated variable
        # (which is exactly what an unnamed-and-therefore-never-blocklisted
        # secret would also look like) must NOT pass through either.
        assert "SOME_UNRELATED_VAR" not in env, (
            "an arbitrary env var neither on SANDBOXED_ENV_ALLOWLIST nor a "
            "known secret reached a sandboxed subprocess -- the allowlist "
            "is supposed to restrict to named-safe variables only, not let "
            "through anything that merely isn't a recognized secret name "
            "(that was the exact shape of F32/F44/F67's repeated failure)"
        )

    def test_the_real_vault_passphrase_env_var_is_blocked(self, monkeypatch):
        """Gauntlet finding: ENV_BLOCKLIST named FRIDAY_VAULT_KEY/
        FRIDAY_HMAC_SECRET -- neither is a real env var anywhere in this
        codebase (vault_passphrase.py's own _ENV_VARS is
        ("FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD")). The one that
        actually carries the Sovereign Vault decryption passphrase when a
        user sets it via environment variable, FRIDAY_VAULT_PASSPHRASE,
        was never in the blocklist at all -- F32's fix would not have
        stripped it before spawning a sandboxed community MCP connector."""
        monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", "correct-horse-battery-staple")
        sp = MCPServerProcess(name="community-pkg", command="npx",
                              args=["some-mcp-server"], trust_level="sandboxed")
        env = _spawn_and_capture(monkeypatch, sp)
        assert "FRIDAY_VAULT_PASSPHRASE" not in env, (
            "the real vault-passphrase environment variable name reached a "
            "sandboxed MCP server's subprocess environment -- under the "
            "current allowlist design (F67) this can now only happen if "
            "FRIDAY_VAULT_PASSPHRASE was mistakenly added to "
            "SANDBOXED_ENV_ALLOWLIST, since nothing not explicitly named "
            "there is ever copied over at all"
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


class TestF67AllowlistInversion:
    """F67 (2026-09-04): Stephen's direct ruling on a defect that recurred
    three times under a denylist design (F32 built it, F44 mis-fixed it,
    an external review found 6 more real provider keys missing from it) --
    invert to an allowlist, so a name nobody has thought to add yet cannot
    leak by omission the way a name nobody thought to BLOCK could."""

    def test_a_provider_on_no_list_at_all_does_not_arrive(self, monkeypatch):
        """The exact proof Stephen asked for: plant a fake key for a
        provider that appears on NEITHER the old ENV_BLOCKLIST (which no
        longer exists) NOR any list anywhere in this codebase -- a
        hypothetical 8th, 9th, 10th provider nobody has added yet -- spawn
        a sandboxed connector, and confirm it doesn't arrive. Under the old
        denylist design this would have passed straight through, exactly
        as MISTRAL_API_KEY/DEEPSEEK_API_KEY/XAI_API_KEY/FIREWORKS_API_KEY/
        PERPLEXITY_API_KEY/COHERE_API_KEY did before F67 found them."""
        monkeypatch.setenv("BRAND_NEW_PROVIDER_NOBODY_HAS_HEARD_OF_API_KEY",
                           "sk-future-REAL-SECRET")
        sp = MCPServerProcess(name="community-pkg", command="npx",
                              args=["some-mcp-server"], trust_level="sandboxed")
        env = _spawn_and_capture(monkeypatch, sp)
        assert "BRAND_NEW_PROVIDER_NOBODY_HAS_HEARD_OF_API_KEY" not in env, (
            "a provider key that is on NO list anywhere -- named for no "
            "other reason than that it doesn't exist yet -- reached a "
            "sandboxed subprocess. The whole point of inverting to an "
            "allowlist was that this class of variable structurally "
            "cannot pass through, whether or not anyone remembered to "
            "name it as dangerous"
        )

    def test_untrusted_level_gets_the_same_allowlist_as_sandboxed(self, monkeypatch):
        monkeypatch.setenv("SOME_FUTURE_SECRET", "sk-REAL-SECRET")
        sp = MCPServerProcess(name="stranger-danger", command="npx",
                              args=["pkg"], trust_level="untrusted")
        env = _spawn_and_capture(monkeypatch, sp)
        assert "SOME_FUTURE_SECRET" not in env

    def test_functionally_necessary_vars_still_reach_a_sandboxed_connector(self, monkeypatch):
        """Non-regression check: the inversion must not silently break real
        connectors. PATH is needed on every platform to resolve further
        executables; SYSTEMROOT is required on Windows for crypto/socket
        APIs to initialize at all (a connector that spawns without it can
        fail network calls, not just look slightly different)."""
        import sys as _sys
        monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))
        if _sys.platform == "win32":
            monkeypatch.setenv("SYSTEMROOT", os.environ.get("SYSTEMROOT", r"C:\Windows"))
        sp = MCPServerProcess(name="real-connector", command="npx",
                              args=["some-real-mcp-server"], trust_level="sandboxed")
        env = _spawn_and_capture(monkeypatch, sp)
        assert "PATH" in env, (
            "PATH itself was filtered out of a sandboxed connector's "
            "environment -- the allowlist must not be so narrow it breaks "
            "the ability to resolve any executable at all"
        )
        if _sys.platform == "win32":
            assert "SYSTEMROOT" in env, (
                "SYSTEMROOT was filtered out on Windows -- Node/npm-based "
                "MCP servers (the common case) can fail crypto/socket "
                "calls outright without it, not just behave oddly"
            )

    def test_allowlist_matching_is_case_insensitive(self, monkeypatch):
        """Verified directly against a live Windows os.environ before
        relying on it: os.environ.copy() (the real call site's input)
        degrades to a plain, case-SENSITIVE dict even though Windows'
        os.environ itself is case-insensitive -- a variable this process
        sees as "SystemRoot" could be stored as "SYSTEMROOT" by the time a
        caller hands sanitize_env_for_mcp() a plain copy. A naive exact-
        case set lookup would silently drop it, breaking every sandboxed
        Windows connector while looking, on paper, like nothing changed."""
        from agent_friday.services import extension_security as extsec
        raw_env = {"SystemRoot": r"C:\Windows", "path": "/usr/bin",
                  "SOME_RANDOM_SECRET": "leak-me-not"}
        result = extsec.sanitize_env_for_mcp(raw_env, trust_level="sandboxed")
        assert result.get("SystemRoot") == r"C:\Windows" or result.get("SYSTEMROOT") == r"C:\Windows", (
            f"a differently-cased allowlisted variable was dropped entirely "
            f"instead of matched case-insensitively -- got {result!r}"
        )
        assert "path" in result or "PATH" in result
        assert "SOME_RANDOM_SECRET" not in result and "some_random_secret" not in result
