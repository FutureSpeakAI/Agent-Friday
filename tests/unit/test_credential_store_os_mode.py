"""PR-5 (credentials, OS-mode sequence) — fail-closed credential storage.

WHAT THIS PINS
--------------
Before this PR, `credential_store.protect()` had exactly one fallthrough when
neither a vault key (FRIDAY_PASSWORD) nor Windows DPAPI was available: print a
one-time stderr warning and write the credential as PLAINTEXT. That is a
reasonable last resort on a desktop Windows install with an operator who might
see the warning. It is not reasonable on the sealed Friday Linux kiosk image
(FRIDAY_OS_MODE=1): DPAPI never applies there (Windows-only), there is no
interactive operator to read a stderr warning, and "plaintext credential
written to a sealed appliance's disk, silently" is exactly the failure mode
encryption-at-rest exists to prevent.

Under FRIDAY_OS_MODE, `protect()` now raises instead of falling through to
plaintext. Windows-default behavior (OS mode off) is asserted UNCHANGED —
same warning, same plaintext fallthrough as before this PR.

Each test verifies filesystem state literally (not just that an exception was
raised), per the PR-5 acceptance criteria: a raise that still leaves a
plaintext file on disk would defeat the entire point.

Only synthetic, fake test values are used — never a real secret.
"""
from __future__ import annotations

import sys

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.privacy import vault_crypto as vc


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Isolated on-disk locations, a clean env, and reset module caches.

    Redirects the module-level path constants that were computed once at
    import time from `core.FRIDAY_DIR` — mutating `core.FRIDAY_DIR` after
    import would not reach them, matching the pattern in
    tests/unit/test_vault_passphrase_migration.py.
    """
    home = tmp_path / ".friday"
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE", home / "vault" / ".vault_config.json")
    monkeypatch.setattr(cs, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", home / "providers" / "keys")

    for var in ("FRIDAY_OS_MODE", "FRIDAY_PASSWORD", "FRIDAY_VAULT_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)

    # A real start.bat elsewhere on this machine (or a real OS keychain
    # entry) must never leak a real passphrase into these tests.
    from agent_friday.services import vault_passphrase as vp
    monkeypatch.setattr(vp, "_from_start_bat", lambda: "")
    monkeypatch.setitem(sys.modules, "keyring", None)
    vp.reset_cache()

    cs._VAULT_KEY = None
    cs._VAULT_KEY_READY = False
    cs._WARNED_PLAINTEXT = False
    yield home
    vp.reset_cache()
    cs._VAULT_KEY = None
    cs._VAULT_KEY_READY = False
    cs._WARNED_PLAINTEXT = False


def _no_dpapi(monkeypatch):
    """Force the 'no DPAPI available' branch regardless of host OS, so the
    fail-closed / plaintext-fallthrough paths are exercised deterministically
    on any machine running the suite (including this Windows dev box)."""
    monkeypatch.setattr(cs, "_dpapi_available", lambda: False)
    monkeypatch.setattr(cs, "_dpapi", lambda data, encrypt: None)


# ── Acceptance criterion 1: OS mode + no vault key + no DPAPI -> raise, ─────
#    and NOTHING is written to disk. ────────────────────────────────────────

class TestFailClosedUnderOsMode:
    def test_protect_raises_when_no_password_and_no_dpapi(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        _no_dpapi(monkeypatch)
        with pytest.raises(RuntimeError, match="FRIDAY_OS_MODE"):
            cs.protect(b"a-fake-test-secret-not-real")

    def test_write_secret_raises_and_writes_nothing_to_disk(self, monkeypatch, tmp_path):
        """The literal filesystem check the acceptance criteria demand: not
        just 'it raised', but 'nothing landed on disk' -- no target file, no
        .tmp file, not even an empty parent directory."""
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        _no_dpapi(monkeypatch)

        target = tmp_path / "creds" / "google_token.json"
        with pytest.raises(RuntimeError):
            cs.write_secret(target, b"a-fake-oauth-token-not-real")

        assert not target.exists(), "the secret file was written despite the raise"
        assert not target.with_name(target.name + ".tmp").exists(), (
            "a temp file with the (unencrypted) blob was left behind"
        )
        assert not target.parent.exists(), (
            "the parent directory was created even though nothing could be "
            "written safely -- protect() must run before any disk touch"
        )

    def test_set_provider_key_raises_and_key_file_absent(self, monkeypatch):
        """The real call site a caller actually uses (onboarding storing a
        provider API key), not just the low-level primitive."""
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        _no_dpapi(monkeypatch)

        with pytest.raises(RuntimeError):
            cs.set_provider_key("openai", "sk-fake-not-a-real-key-0000000000")  # pragma: allowlist secret

        assert cs.provider_key_status("openai") == "missing"
        assert not cs._provider_key_path("openai").exists()


# ── Acceptance criterion 2: FRIDAY_PASSWORD set -> vault magic present ──────

class TestVaultPathStillWorksUnderOsMode:
    def test_protect_encrypts_with_vault_key_when_password_set(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        monkeypatch.setenv("FRIDAY_PASSWORD", "correct-horse-battery-staple-TEST")
        _no_dpapi(monkeypatch)  # prove the vault path wins even with no DPAPI

        blob, method = cs.protect(b"a-fake-test-secret-not-real")

        assert method == "vault"
        assert blob.startswith(vc.MAGIC), "vault magic (FRIDAYVAULT) missing from blob"
        assert cs.looks_protected(blob) == "vault"

    def test_write_secret_succeeds_and_file_carries_vault_magic(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        monkeypatch.setenv("FRIDAY_PASSWORD", "correct-horse-battery-staple-TEST")
        _no_dpapi(monkeypatch)

        target = tmp_path / "creds" / "google_token.json"
        method = cs.write_secret(target, b'{"fake": "not-a-real-token"}')

        assert method == "vault"
        on_disk = target.read_bytes()
        assert on_disk.startswith(vc.MAGIC), "the file on disk does not carry the vault magic"
        assert b"not-a-real-token" not in on_disk, "the plaintext leaked into the blob"

        # Round-trips back to the original plaintext.
        assert cs.read_secret(target) == b'{"fake": "not-a-real-token"}'


# ── Acceptance criterion 3 (Windows-default regression guard) ──────────────

class TestWindowsDefaultBehaviorUnchanged:
    """OS mode OFF: this PR must not change anything. Same warning, same
    plaintext fallthrough as before -- verified, not assumed."""

    def test_plaintext_fallthrough_with_warning_when_os_mode_off(self, monkeypatch, capsys):
        monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
        _no_dpapi(monkeypatch)

        blob, method = cs.protect(b"a-fake-test-secret-not-real")

        assert method == "plaintext"
        assert blob == b"a-fake-test-secret-not-real"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "no FRIDAY_PASSWORD and no DPAPI" in captured.err

    def test_write_secret_writes_plaintext_to_disk_when_os_mode_off(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
        _no_dpapi(monkeypatch)

        target = tmp_path / "creds" / "google_token.json"
        method = cs.write_secret(target, b'{"fake": "not-a-real-token"}')

        assert method == "plaintext"
        assert target.read_bytes() == b'{"fake": "not-a-real-token"}'

    def test_warning_is_only_printed_once(self, monkeypatch, capsys):
        """Regression guard: _WARNED_PLAINTEXT must still dedupe exactly as
        before -- this PR only adds a branch ABOVE the warning, it must not
        change the warning's own behavior."""
        monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
        _no_dpapi(monkeypatch)

        cs.protect(b"secret-one")
        cs.protect(b"secret-two")
        captured = capsys.readouterr()
        assert captured.err.count("WARNING") == 1
