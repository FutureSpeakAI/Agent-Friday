"""The setup wizard must not mint a new vault passphrase over an existing vault.

THE DEFECT THIS PINS (5.6.6)
----------------------------
`step_vault_password` opened with "Generate a random passphrase for me?"
defaulting to YES, and never looked at whether a vault already existed. The
Windows installer runs the wizard at step 12 on EVERY run, including every
upgrade. So an existing user pressing Enter through an upgrade minted a fresh
passphrase over a vault encrypted under the old one — AES-256-GCM over an
Argon2id key derived from (passphrase, salt). The ciphertext stays, the key
changes, the data is gone. It reported success.

The installer half of this (app.copy deleting app\\start.bat, the passphrase's
only automatic home) is proven separately by a published-asset upgrade
rehearsal; it cannot be reached from pytest. This file covers the wizard half.

The tests drive the DANGEROUS input on purpose: every prompt answered by
pressing Enter, i.e. `Prompt.ask`/`Confirm.ask` returning their own defaults.
That is the path a real upgrading user takes, and it is the path that used to
destroy the vault. A test that typed careful answers would prove nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_friday import setup_wizard as w


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path, monkeypatch):
    """An isolated ~/.friday/vault with a real salt, plus a real ciphertext."""
    vdir = tmp_path / ".friday" / "vault"
    vdir.mkdir(parents=True)
    monkeypatch.setattr(w, "VAULT_DIR", vdir)
    monkeypatch.setattr(w, "VAULT_CONFIG", vdir / ".vault_config.json")
    monkeypatch.setattr(w, "PROJ_ROOT", tmp_path / "app")
    (tmp_path / "app").mkdir()
    for var in ("FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    return vdir


def _encrypt_real(vdir: Path, passphrase: str) -> None:
    """Write a genuinely encrypted artifact, the way the product writes one."""
    from agent_friday.privacy import vault_crypto as vc
    salt = os.urandom(32)
    (vdir / ".vault_config.json").write_text(
        json.dumps({"salt_hex": salt.hex()}), encoding="utf-8")
    key = vc.derive_key(passphrase, salt)      # DEFAULT profile — as production
    (vdir / "note.enc").write_bytes(vc.encrypt(b"private note", key))


@pytest.fixture
def enter_only(monkeypatch):
    """Every prompt answered by pressing Enter — the dangerous path."""
    seen = []

    def ask(prompt, *a, **kw):
        seen.append(str(prompt))
        return kw.get("default", "")

    def confirm(prompt, *a, **kw):
        seen.append(str(prompt))
        return kw.get("default", False)

    monkeypatch.setattr(w.Prompt, "ask", staticmethod(ask))
    monkeypatch.setattr(w.Confirm, "ask", staticmethod(confirm))
    return seen


# ── The regression ──────────────────────────────────────────────────────────

def test_existing_vault_keeps_passphrase_from_start_bat(vault, enter_only, monkeypatch):
    """The upgrade case. Passphrase in start.bat, vault present, user hits Enter."""
    pw = "the-original-passphrase"
    _encrypt_real(vault, pw)
    (w.PROJ_ROOT / "start.bat").write_text(
        "@echo off\r\nSET FRIDAY_PASSWORD=%s\r\n" % pw, encoding="utf-8")

    got = w.step_vault_password(12, "")

    assert got == pw, (
        "the wizard replaced an existing vault passphrase; every byte in "
        "~/.friday/vault is now unreadable"
    )


def test_existing_vault_keeps_passphrase_from_environment(vault, enter_only, monkeypatch):
    """Same, sourced from the environment the running product would see."""
    pw = "from-the-environment"
    _encrypt_real(vault, pw)
    monkeypatch.setenv("FRIDAY_PASSWORD", pw)

    assert w.step_vault_password(12, "") == pw


def test_existing_vault_keeps_passphrase_from_keyring(vault, enter_only, monkeypatch):
    """The copy `friday vault-setup` makes — the only one that survives app.copy."""
    pw = "from-the-keychain"
    _encrypt_real(vault, pw)

    class _KR:
        @staticmethod
        def get_password(service, name):
            assert (service, name) == ("agent-friday", "vault-passphrase")
            return pw

    monkeypatch.setitem(__import__("sys").modules, "keyring", _KR)
    assert w.step_vault_password(12, "") == pw


def test_lost_passphrase_does_not_silently_mint_a_new_one(vault, enter_only):
    """Vault present, passphrase gone. Pressing Enter must NOT generate one.

    This is the case Stephen singled out: "that is a situation to stop and
    explain, not to paper over by generating a new one." The default answer is
    'leave it unset', which keeps the old ciphertext recoverable if the
    passphrase turns up later.
    """
    _encrypt_real(vault, "a-passphrase-nobody-now-has")

    got = w.step_vault_password(12, "")

    assert got == "", (
        "the wizard generated a new passphrase over a vault it could not open"
    )


def test_lost_passphrase_abandon_requires_typing_the_word(vault, monkeypatch):
    """Starting a new vault is possible, but never by pressing Enter."""
    _encrypt_real(vault, "gone")
    answers = iter(["3", ""])          # choose 'start a new vault', then Enter

    monkeypatch.setattr(w.Prompt, "ask",
                        staticmethod(lambda *a, **kw: next(answers, kw.get("default", ""))))
    monkeypatch.setattr(w.Confirm, "ask",
                        staticmethod(lambda *a, **kw: kw.get("default", False)))

    assert w.step_vault_password(12, "") == "", (
        "abandoning an existing vault must require typing 'abandon'"
    )


def test_wrong_passphrase_is_not_accepted(vault, enter_only, monkeypatch):
    """A passphrase that does not open the vault is treated as a lost one."""
    _encrypt_real(vault, "the-real-one")
    (w.PROJ_ROOT / "start.bat").write_text(
        "@echo off\r\nSET FRIDAY_PASSWORD=a-stale-different-one\r\n",  # pragma: allowlist secret
        encoding="utf-8")

    got = w.step_vault_password(12, "")

    assert got != "a-stale-different-one", (
        "a passphrase that fails to decrypt the vault was accepted anyway"
    )
    assert got == ""


# ── The fresh-install path ──────────────────────────────────────────────────

def test_fresh_install_pressing_enter_skips_rather_than_generating(vault, enter_only):
    """CHANGED DELIBERATELY in 5.7.0. This test used to assert the opposite.

    It read: "No vault yet: the default-Yes generate path is correct and must
    remain." That path opened with "Generate a random passphrase for me?"
    defaulting to YES, printed the passphrase to the terminal, and wrote it to
    start.bat -- the file inside the app directory the installer deletes, on
    the line after the API keys. It was the second of the two routes that
    destroyed vaults, and it recommended defeating the threat model the same
    screen had just finished describing.

    Enter-through now SKIPS. Nothing is generated and nothing is written to any
    file. The safe direction is unencrypted-and-recoverable
    (_migrate_vault_plaintext encrypts whatever exists on the next start)
    rather than encrypted-under-a-key-in-a-doomed-file.
    """
    assert not w._vault_exists()

    got = w.step_vault_password(12, "")

    assert got == "", "the wizard minted a passphrase the user never chose"
    sb = w.PROJ_ROOT / "start.bat"
    assert (not sb.exists()) or "FRIDAY_PASSWORD" not in sb.read_text(encoding="utf-8"), \
        "a passphrase was written into start.bat"


def test_holding_enter_through_the_vault_screen_terminates(vault, enter_only):
    """A hang here is a hung install.

    The wizard runs in the FOREGROUND at installer step 12. The first draft of
    the rewritten screen re-entered itself on every empty answer, so a user
    pressing Enter -- no passphrase, then declining the skip -- looped forever.
    pytest caught it as a RecursionError; a real user would have seen the
    installer stop with no explanation.
    """
    got = w.step_vault_password(12, "")     # every prompt answered with Enter
    assert got == ""


# ── The helper, directly ────────────────────────────────────────────────────

def test_existing_vault_password_prefers_env_over_start_bat(vault, monkeypatch):
    (w.PROJ_ROOT / "start.bat").write_text(
        "SET FRIDAY_PASSWORD=from-bat\r\n",  # pragma: allowlist secret
        encoding="utf-8")
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", "from-env")

    pw, source = w._existing_vault_password()
    assert pw == "from-env"
    assert "environment" in source


def test_existing_vault_password_empty_when_nothing_anywhere(vault, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    pw, source = w._existing_vault_password()
    assert pw == ""
    assert source == ""
