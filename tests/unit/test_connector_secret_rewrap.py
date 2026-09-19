"""MCP connector secrets, re-sealed under Friday's keystore.

THE GAP THIS CLOSES. `credential_store.migrate_to_keystore` walks FILES. These
secrets are base64 inside `mcp_servers.json`, so `_credential_files()` cannot
see them and the first migration missed them entirely. The cost was exact: five
credentials came back on 2026-09-19 while the GitHub MCP server kept failing
every spawn with "GCM auth tag mismatch", because its token was still sealed
under the passphrase the resolver had stopped preferring. Two more were
stranded here - the GitHub PAT and a Google OAuth credential - and both
recovered.

The rules are migration's rules, because this is somebody's only copy of a
token: decrypt with any key Friday knows, round-trip before replacing, leave
anything unreadable exactly as it is, and make a second run free.
"""
from __future__ import annotations

import base64

import pytest

import agent_friday.privacy.vault_crypto as vc
from agent_friday.services import connector_secrets as cs
from agent_friday.services import credential_store as cstore
from agent_friday.services import keystore as ks


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(cstore, "_SECURITY_DIR", tmp_path / "security")
    monkeypatch.setattr(cstore, "_CRED_AUDIT_LOG",
                        tmp_path / "security" / "audit.jsonl")
    monkeypatch.setattr(ks, "KEYSTORE_PATH", tmp_path / "security" / "keystore.json")
    monkeypatch.setattr(ks, "_CACHED_KEY", None)
    monkeypatch.setattr(ks, "_passphrase", lambda: "")
    monkeypatch.setattr(cs, "_MIGRATED", set(), raising=False)
    return tmp_path


def _old_envelope(secret: str, passphrase: str, salt: bytes) -> str:
    """A value as the pre-keystore scheme wrote it."""
    blob = vc.encrypt(secret.encode("utf-8"), vc.derive_key(passphrase, salt))
    return cs.SECRET_MARKER + "vault:" + base64.b64encode(blob).decode("ascii")


def _cfg(value: str, key: str = "GITHUB_PERSONAL_ACCESS_TOKEN") -> dict:
    return {"servers": {"github": {"command": "npx", "env": {key: value}}}}


def test_a_stranded_secret_is_recovered_and_resealed(home, monkeypatch):
    """THE CRUX, and exactly what happened to the GitHub token."""
    salt = b"\x22" * 16
    monkeypatch.setattr(cstore, "_legacy_keys",
                        lambda: [("launcher", vc.derive_key("old-one", salt))])
    monkeypatch.setattr(cstore, "_vault_key",
                        lambda: vc.derive_key("the-new-one", salt))

    cfg = _cfg(_old_envelope("ghp_realtoken", "old-one", salt))
    new, rep = cs.rewrap_config_onto_keystore(cfg)

    assert rep["rewrapped"] == 1 and rep["unreadable"] == []
    assert [r["via"] for r in rep["recovered"]] == ["launcher"]
    value = new["servers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert cs.decrypt_value(value) == "ghp_realtoken"


def test_a_value_nothing_can_open_is_left_exactly_as_it_was(home, monkeypatch):
    """A token this process cannot read may still be readable elsewhere.
    Replacing it with an empty string would destroy the only copy."""
    salt = b"\x22" * 16
    monkeypatch.setattr(cstore, "_legacy_keys", lambda: [])
    monkeypatch.setattr(cstore, "_vault_key", lambda: None)

    original = _old_envelope("lost", "nobody-knows", salt)
    new, rep = cs.rewrap_config_onto_keystore(_cfg(original))

    assert rep["rewrapped"] == 0 and len(rep["unreadable"]) == 1
    assert new["servers"]["github"]["env"][
        "GITHUB_PERSONAL_ACCESS_TOKEN"] == original


def test_running_it_twice_is_free(home, monkeypatch):
    monkeypatch.setattr(cstore, "_legacy_keys", lambda: [])
    cfg = _cfg(cs.encrypt_value("ghp_x"))
    once, r1 = cs.rewrap_config_onto_keystore(cfg)
    twice, r2 = cs.rewrap_config_onto_keystore(once)
    assert r1["already"] + r1["rewrapped"] >= 1
    assert r2["rewrapped"] == 0
    assert cs.decrypt_value(
        twice["servers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]) == "ghp_x"


def test_non_secret_values_are_untouched(home, monkeypatch):
    monkeypatch.setattr(cstore, "_legacy_keys", lambda: [])
    cfg = {"servers": {"x": {"command": "npx",
                             "env": {"LOG_LEVEL": "debug", "PORT": "8080"}}}}
    new, rep = cs.rewrap_config_onto_keystore(cfg)
    assert rep["examined"] == 0
    assert new["servers"]["x"]["env"] == {"LOG_LEVEL": "debug", "PORT": "8080"}


def test_a_server_with_no_env_survives(home, monkeypatch):
    monkeypatch.setattr(cstore, "_legacy_keys", lambda: [])
    cfg = {"servers": {"a": {"command": "npx"}, "b": "not-a-dict"}}
    new, rep = cs.rewrap_config_onto_keystore(cfg)
    assert new["servers"]["a"] == {"command": "npx"}
    assert new["servers"]["b"] == "not-a-dict"
    assert rep["examined"] == 0


def test_a_malformed_config_is_returned_unchanged(home):
    for bad in (None, [], "nope", {}, {"servers": "no"}):
        out, rep = cs.rewrap_config_onto_keystore(bad)
        assert out is bad or out == bad
        assert rep["rewrapped"] == 0


def test_the_round_trip_is_checked_before_anything_is_replaced(home, monkeypatch):
    """If re-encryption produced something that will not decrypt back, the
    original must survive - the new value is only better if it opens."""
    salt = b"\x22" * 16
    monkeypatch.setattr(cstore, "_legacy_keys",
                        lambda: [("launcher", vc.derive_key("old", salt))])
    monkeypatch.setattr(cstore, "_vault_key", lambda: None)
    monkeypatch.setattr(cs, "decrypt_value", lambda v: "something-else")

    original = _old_envelope("ghp_real", "old", salt)
    new, rep = cs.rewrap_config_onto_keystore(_cfg(original))

    assert rep["rewrapped"] == 0
    assert len(rep["unreadable"]) == 1
    assert new["servers"]["github"]["env"][
        "GITHUB_PERSONAL_ACCESS_TOKEN"] == original
