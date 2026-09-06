"""Tests for `credential_store.reencrypt_stale_provider_keys` — the
stale-vault-key recovery path built 2026-09-03 after a rehearsal run
overwrote the shared, machine-wide OS keychain entry and orphaned three real
provider keys mid-session. See the module docstring in credential_store.py
for what this can and cannot do: it can only save what the CURRENT process
can still decrypt, re-encrypted under whatever a FRESH process derives now.

Every test here redirects BOTH the current process's `core.FRIDAY_DIR`
(`monkeypatch.setattr`, for this test's own reads/writes) AND the real
`USERPROFILE` environment variable (`monkeypatch.setenv`, for the genuinely
separate verification SUBPROCESS this function spawns, which does its own
fresh `os.path.expanduser("~")` and cannot see a monkeypatched Python
attribute in a process that no longer exists by the time it runs) -- so
nothing here ever touches the real ~/.friday.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import credential_store as cs
from agent_friday.services import vault_passphrase as vp


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """A redirected ~/.friday, isolated for BOTH this process and any
    subprocess reencrypt_stale_provider_keys spawns to verify its work."""
    home = tmp_path / "home"
    friday_dir = home / ".friday"
    friday_dir.mkdir(parents=True)
    monkeypatch.setattr(core, "FRIDAY_DIR", friday_dir)
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE", friday_dir / "vault" / ".vault_config.json")
    monkeypatch.setattr(cs, "_SECURITY_DIR", friday_dir / "security")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", friday_dir / "providers" / "keys")
    monkeypatch.setattr(cs, "_VAULT_KEY", None, raising=False)
    monkeypatch.setattr(cs, "_VAULT_KEY_READY", False, raising=False)
    # The subprocess is a genuinely separate process: it inherits the OS
    # environment, not this process's monkeypatched Python objects, so it
    # needs USERPROFILE redirected for its own os.path.expanduser("~").
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    for var in ("FRIDAY_PASSWORD", "FRIDAY_VAULT_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(sys.modules, "keyring", None)  # no real OS keychain touched
    vp.reset_cache()
    yield friday_dir
    vp.reset_cache()


def _seed_salt(friday_dir: Path) -> str:
    vault_dir = friday_dir / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)
    salt_hex = "ab" * 16
    (vault_dir / ".vault_config.json").write_text(
        json.dumps({"salt_hex": salt_hex, "kdf": "argon2id", "cipher": "aes-256-gcm"}),
        encoding="utf-8")
    return salt_hex


def _write_key_under(friday_dir: Path, provider: str, plaintext: str,
                     passphrase: str, salt_hex: str) -> None:
    """Simulate a provider key that was encrypted under some PAST passphrase
    -- exactly what the live server's stale cache would have written before
    the keychain entry underneath it changed."""
    key = cs._vc.derive_key(passphrase, bytes.fromhex(salt_hex))
    blob = cs._vc.encrypt(plaintext.encode("utf-8"), key)
    path = cs._provider_key_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


class TestRecoversWhatThisProcessCanStillDecrypt:
    def test_stale_key_is_recovered_and_verified_by_a_real_subprocess(
            self, isolated_home, monkeypatch):
        salt_hex = _seed_salt(isolated_home)
        _write_key_under(isolated_home, "testprov", "sk-the-real-secret",
                         "old-passphrase", salt_hex)

        # This process's cache holds the OLD key -- standing in for a live
        # server that resolved it before the keychain changed underneath it.
        old_key = cs._vc.derive_key("old-passphrase", bytes.fromhex(salt_hex))
        monkeypatch.setattr(cs, "_VAULT_KEY", old_key, raising=False)
        monkeypatch.setattr(cs, "_VAULT_KEY_READY", True, raising=False)
        # A fresh process resolves a DIFFERENT passphrase now. Set via the
        # real env var (not a monkeypatch of vp.resolve): the verification
        # step below spawns a genuinely separate subprocess, which inherits
        # this process's environment but not its patched Python objects --
        # so the fresh passphrase has to be something a REAL, unmocked
        # resolve() in that child process will also find.
        monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", "new-passphrase")

        result = cs.reencrypt_stale_provider_keys(names=["testprov"])

        assert result["recovered"] == ["testprov"]
        assert result["failed"] == []
        assert result["backup_dir"] and Path(result["backup_dir"]).exists()
        # Backup holds the pre-reencryption ciphertext, not the plaintext.
        backed_up = Path(result["backup_dir"]) / "providers" / "keys" / "testprov.key"
        assert backed_up.exists()

        # The proof that matters: a GENUINELY FRESH process -- this
        # process's own cache reset, mimicking a restart -- can now read it
        # back using only what a fresh process derives.
        monkeypatch.setattr(cs, "_VAULT_KEY", None, raising=False)
        monkeypatch.setattr(cs, "_VAULT_KEY_READY", False, raising=False)
        assert cs.get_provider_key("testprov") == "sk-the-real-secret"

    def test_already_matching_key_is_reported_unchanged_not_rewritten(
            self, isolated_home, monkeypatch):
        salt_hex = _seed_salt(isolated_home)
        _write_key_under(isolated_home, "testprov", "sk-already-fresh",
                         "same-passphrase", salt_hex)
        same_key = cs._vc.derive_key("same-passphrase", bytes.fromhex(salt_hex))
        monkeypatch.setattr(cs, "_VAULT_KEY", same_key, raising=False)
        monkeypatch.setattr(cs, "_VAULT_KEY_READY", True, raising=False)
        monkeypatch.setattr(vp, "resolve",
                            lambda use_cache=True: ("same-passphrase", "test"))

        before = cs._provider_key_path("testprov").read_bytes()
        result = cs.reencrypt_stale_provider_keys(names=["testprov"])

        assert result["unchanged"] == ["testprov"]
        assert result["recovered"] == []
        assert cs._provider_key_path("testprov").read_bytes() == before, \
            "an already-fresh key must not be rewritten"

    def test_a_key_this_process_also_cannot_decrypt_is_reported_not_touched(
            self, isolated_home, monkeypatch):
        salt_hex = _seed_salt(isolated_home)
        _write_key_under(isolated_home, "testprov", "sk-unreachable",
                         "some-other-passphrase", salt_hex)
        # This process's cache does NOT hold the key that encrypted it --
        # the same-shape loss as a key nobody currently running can reach.
        monkeypatch.setattr(cs, "_VAULT_KEY", None, raising=False)
        monkeypatch.setattr(cs, "_VAULT_KEY_READY", False, raising=False)
        monkeypatch.setattr(vp, "resolve",
                            lambda use_cache=True: ("yet-another-passphrase", "test"))
        before = cs._provider_key_path("testprov").read_bytes()

        result = cs.reencrypt_stale_provider_keys(names=["testprov"])

        assert result["recovered"] == [] and result["unchanged"] == []
        assert len(result["failed"]) == 1
        assert result["failed"][0]["name"] == "testprov"
        assert cs._provider_key_path("testprov").read_bytes() == before

    def test_no_resolvable_fresh_passphrase_aborts_before_touching_anything(
            self, isolated_home, monkeypatch):
        salt_hex = _seed_salt(isolated_home)
        _write_key_under(isolated_home, "testprov", "sk-whatever",
                         "old-passphrase", salt_hex)
        old_key = cs._vc.derive_key("old-passphrase", bytes.fromhex(salt_hex))
        monkeypatch.setattr(cs, "_VAULT_KEY", old_key, raising=False)
        monkeypatch.setattr(cs, "_VAULT_KEY_READY", True, raising=False)
        # A fresh process finds nothing at all -- e.g. the keychain entry
        # was deleted outright, not merely changed.
        monkeypatch.setattr(vp, "resolve", lambda use_cache=True: ("", ""))
        before = cs._provider_key_path("testprov").read_bytes()

        result = cs.reencrypt_stale_provider_keys(names=["testprov"])

        assert result["recovered"] == [] and result["unchanged"] == []
        assert result["failed"] and result["failed"][0]["name"] == "*"
        assert result["backup_dir"] is None, "must not back up when it aborts this early"
        assert cs._provider_key_path("testprov").read_bytes() == before

    def test_multiple_keys_are_independent_one_failure_does_not_block_others(
            self, isolated_home, monkeypatch):
        salt_hex = _seed_salt(isolated_home)
        _write_key_under(isolated_home, "recoverable", "sk-good",
                         "old-passphrase", salt_hex)
        _write_key_under(isolated_home, "unreachable", "sk-bad",
                         "some-other-passphrase", salt_hex)
        old_key = cs._vc.derive_key("old-passphrase", bytes.fromhex(salt_hex))
        monkeypatch.setattr(cs, "_VAULT_KEY", old_key, raising=False)
        monkeypatch.setattr(cs, "_VAULT_KEY_READY", True, raising=False)
        monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", "new-passphrase")

        result = cs.reencrypt_stale_provider_keys(
            names=["recoverable", "unreachable"])

        assert result["recovered"] == ["recoverable"]
        assert [f["name"] for f in result["failed"]] == ["unreachable"]
