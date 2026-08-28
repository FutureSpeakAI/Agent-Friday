"""Connector credentials must not sit in plaintext in ~/.friday/mcp_servers.json.

Friday's whole claim is that your data stays on your machine and under your
control. "On your machine, in a world-readable JSON file, next to the tool that
uses it" is not that claim honoured; it is that claim assumed. An Airtable
token and a Gmail app password were being written exactly that way, and the
product already had the machinery not to: credential_store picks the strongest
protection the host offers (vault key -> Windows DPAPI -> plaintext with a loud
warning) and the OAuth tokens already use it.

Two exposures close together here. The obvious one is the file. The quieter one
is GET /api/mcp/servers, which returns the raw config -- so every connector
token was also being handed to the browser on request.

Migration matters as much as the fix: anyone who connected a service before
this shipped has a plaintext secret on disk already, and a fix that only
protects new writes leaves them exactly where they were.
"""

import json

import pytest

from agent_friday.services import connector_secrets as cs
from agent_friday.services import credential_store


def _cfg(token="pat_live_abcdef123456", team="T0123"):
    return {"servers": {"airtable": {
        "command": "npx",
        "args": ["-y", "airtable-mcp-server"],
        "env": {"AIRTABLE_API_KEY": token, "AIRTABLE_TEAM_ID": team},
        "enabled": True,
    }}}


class TestWhichValuesAreSecret:
    @pytest.mark.parametrize("key", [
        "AIRTABLE_API_KEY", "SLACK_BOT_TOKEN", "GITHUB_PERSONAL_ACCESS_TOKEN",
        "GMAIL_APP_PASSWORD", "DISCORD_TOKEN", "NOTION_SECRET",
        "some_api_key", "OPENAI_APIKEY",
    ])
    def test_credential_shaped_names_are_secret(self, key):
        assert cs.looks_secret(key) is True

    @pytest.mark.parametrize("key", [
        "SLACK_TEAM_ID", "ALLOWED_DIRS", "PATH", "NODE_ENV",
        "AIRTABLE_BASE_ID", "LOG_LEVEL",
    ])
    def test_ordinary_configuration_is_left_readable(self, key):
        """Encrypting everything would make the file impossible to hand-fix.

        It would also break extension_security.assess_config, which reasons
        about what a server is allowed to reach.
        """
        assert cs.looks_secret(key) is False


class TestEncryptDecrypt:
    def test_round_trip(self):
        env = {"AIRTABLE_API_KEY": "pat_live_secret", "AIRTABLE_TEAM_ID": "T1"}
        enc = cs.encrypt_env(env)
        assert cs.decrypt_env(enc) == env

    def test_only_the_secret_is_transformed(self):
        enc = cs.encrypt_env({"AIRTABLE_API_KEY": "pat_live_secret",
                              "AIRTABLE_TEAM_ID": "T1"})
        assert enc["AIRTABLE_TEAM_ID"] == "T1"
        if credential_store.protection_method() != "plaintext":
            assert enc["AIRTABLE_API_KEY"].startswith(cs.SECRET_MARKER)
            assert "pat_live_secret" not in enc["AIRTABLE_API_KEY"]

    def test_encrypting_twice_does_not_double_wrap(self):
        """The raw-config route round-trips this object through the browser.

        A GET returns ciphertext and a POST sends it straight back, so encrypt
        has to be a no-op on something already encrypted or the value would be
        wrapped again and decrypt once would return ciphertext.
        """
        once = cs.encrypt_env({"AIRTABLE_API_KEY": "pat_live_secret"})
        twice = cs.encrypt_env(once)
        assert twice == once
        assert cs.decrypt_env(twice)["AIRTABLE_API_KEY"] == "pat_live_secret"

    def test_an_empty_value_is_left_alone(self):
        """A blank field means "not configured", not "encrypt the empty string"."""
        assert cs.encrypt_env({"AIRTABLE_API_KEY": ""})["AIRTABLE_API_KEY"] == ""

    def test_undecryptable_value_raises_rather_than_returning_ciphertext(self):
        """DPAPI blobs do not survive being copied to another machine.

        Handing the ciphertext to the MCP server as if it were the token would
        produce a baffling auth failure inside someone else's software. Fail
        here, where the cause is legible.
        """
        with pytest.raises(Exception):
            cs.decrypt_value(cs.SECRET_MARKER + "bm90LWEtcmVhbC1ibG9i")


class TestOnDiskAndInFlight:
    def test_saving_a_connector_encrypts_the_token_on_disk(self, friday_dir):
        from agent_friday.services import agent as agent_svc
        agent_svc._save_mcp_servers(_cfg())
        raw = agent_svc.MCP_SERVERS_FILE.read_text(encoding="utf-8")
        if credential_store.protection_method() == "plaintext":
            pytest.skip("host offers no encryption; nothing to assert")
        assert "pat_live_abcdef123456" not in raw, (
            "the connector token is sitting in mcp_servers.json in plaintext")
        assert "T0123" in raw, "non-secret config should stay readable"

    def test_loading_does_not_hand_back_the_plaintext(self, friday_dir):
        """GET /api/mcp/servers returns this object verbatim to the browser."""
        from agent_friday.services import agent as agent_svc
        agent_svc._save_mcp_servers(_cfg())
        loaded = agent_svc._load_mcp_servers()
        val = loaded["servers"]["airtable"]["env"]["AIRTABLE_API_KEY"]
        if credential_store.protection_method() != "plaintext":
            assert val != "pat_live_abcdef123456"
            assert val.startswith(cs.SECRET_MARKER)

    def test_the_spawned_process_still_gets_the_real_token(self, friday_dir):
        """Encryption at rest is worthless if it breaks the connector."""
        from agent_friday.services import agent as agent_svc
        agent_svc._save_mcp_servers(_cfg())
        spec = agent_svc._load_mcp_servers()["servers"]["airtable"]
        assert cs.decrypt_env(spec["env"]) == {
            "AIRTABLE_API_KEY": "pat_live_abcdef123456",
            "AIRTABLE_TEAM_ID": "T0123"}


class TestMigrationOfExistingPlaintext:
    def test_a_plaintext_file_is_upgraded_in_place_on_load(self, friday_dir):
        """Everyone who connected a service before this shipped."""
        from agent_friday.services import agent as agent_svc
        agent_svc.MCP_SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        agent_svc.MCP_SERVERS_FILE.write_text(json.dumps(_cfg()),
                                              encoding="utf-8")
        cs.reset_migration_state_for_tests()

        loaded = agent_svc._load_mcp_servers()
        assert cs.decrypt_env(loaded["servers"]["airtable"]["env"])[
            "AIRTABLE_API_KEY"] == "pat_live_abcdef123456"

        if credential_store.protection_method() == "plaintext":
            pytest.skip("host offers no encryption; nothing to assert")
        raw = agent_svc.MCP_SERVERS_FILE.read_text(encoding="utf-8")
        assert "pat_live_abcdef123456" not in raw, (
            "an existing plaintext secret was read but never upgraded")

    def test_migration_preserves_everything_else(self, friday_dir):
        from agent_friday.services import agent as agent_svc
        agent_svc.MCP_SERVERS_FILE.write_text(json.dumps(_cfg()),
                                              encoding="utf-8")
        cs.reset_migration_state_for_tests()
        loaded = agent_svc._load_mcp_servers()
        srv = loaded["servers"]["airtable"]
        assert srv["command"] == "npx"
        assert srv["args"] == ["-y", "airtable-mcp-server"]
        assert srv["enabled"] is True
        assert srv["env"]["AIRTABLE_TEAM_ID"] == "T0123"
