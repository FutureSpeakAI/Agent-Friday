"""A secret is never stored in plain text, on any host, with or without OS mode.

`protect()` used to end in a plaintext fallthrough (a stderr warning nobody reads, then the
secret written as-is) when neither Friday's keystore, a vault passphrase nor DPAPI could
encrypt it. It now fails closed with an error written for the person, naming the fix, and
nothing is written. Reading an old plaintext blob still works so the migration can move it.
Only synthetic values are used.
"""
from __future__ import annotations

import pytest

from agent_friday.services import credential_store as cs
from agent_friday.services import keystore as ks
from agent_friday.user_errors import UserFacingError

FAKE = b'{"fake": "not-a-real-token"}'


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE", home / "vault" / ".vault_config.json")
    monkeypatch.setattr(cs, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", home / "providers" / "keys")
    for var in ("FRIDAY_OS_MODE", "FRIDAY_PASSWORD", "FRIDAY_VAULT_PASSPHRASE"):
        monkeypatch.delenv(var, raising=False)
    import sys
    from agent_friday.services import vault_passphrase as vp
    monkeypatch.setattr(vp, "_from_start_bat", lambda: "")
    monkeypatch.setattr(vp, "_from_dpapi_file", lambda: "")
    monkeypatch.setitem(sys.modules, "keyring", None)
    vp.reset_cache()
    monkeypatch.setattr(ks, "KEYSTORE_PATH", home / "security" / "keystore.json")
    ks._reset_cache_for_tests()
    cs._VAULT_KEY = None
    cs._VAULT_KEY_READY = False
    yield home
    ks._reset_cache_for_tests()
    vp.reset_cache()
    cs._VAULT_KEY = None
    cs._VAULT_KEY_READY = False


def _nothing_can_encrypt(monkeypatch):
    def _unavailable(data):
        raise RuntimeError("keystore unavailable in this test")
    monkeypatch.setattr(ks, "encrypt", _unavailable)
    monkeypatch.setattr(cs, "_dpapi_available", lambda: False)
    monkeypatch.setattr(cs, "_dpapi", lambda data, encrypt: None)
    monkeypatch.setattr(cs, "_vault_key", lambda: None)


def test_protect_refuses_instead_of_returning_the_secret_as_it_is(monkeypatch):
    _nothing_can_encrypt(monkeypatch)
    with pytest.raises(cs.CredentialProtectionUnavailable) as ei:
        cs.protect(FAKE)
    e = ei.value
    assert isinstance(e, RuntimeError) and isinstance(e, UserFacingError)
    words = e.user_message
    assert "did not save" in words and "Nothing was written" in words
    assert "vault passphrase" in words and "friday vault-setup" in words, words    # the fix is offered
    assert "not-a-real-token" not in words


def test_write_secret_writes_nothing_when_it_cannot_encrypt(monkeypatch, tmp_path):
    _nothing_can_encrypt(monkeypatch)
    target = tmp_path / "creds" / "google_token.json"
    with pytest.raises(cs.CredentialProtectionUnavailable):
        cs.write_secret(target, FAKE)
    assert not target.exists() and not target.parent.exists() and not list(tmp_path.rglob("*.tmp"))


def test_os_mode_keeps_its_own_refusal_and_it_is_the_same_kind_of_error(monkeypatch):
    _nothing_can_encrypt(monkeypatch)
    monkeypatch.setenv("FRIDAY_OS_MODE", "1")
    with pytest.raises(cs.CredentialProtectionUnavailable) as ei:
        cs.protect(FAKE)
    assert "OS_MODE" in str(ei.value) and "PLAINTEXT" in str(ei.value)


def test_a_locked_keystore_is_still_its_own_refusal(monkeypatch):
    def _locked(data):
        raise ks.KeystoreLocked("locked")
    monkeypatch.setattr(ks, "encrypt", _locked)
    with pytest.raises(ks.KeystoreLocked):
        cs.protect(FAKE)


def test_the_encrypting_tiers_still_work(monkeypatch):
    # Friday's own keystore on a healthy host
    blob, method = cs.protect(FAKE)
    assert method == "keystore" and FAKE not in blob and cs.unprotect(blob) == FAKE
    # a vault key when the keystore is unavailable
    def _unavailable(data):
        raise RuntimeError("keystore unavailable in this test")
    monkeypatch.setattr(ks, "encrypt", _unavailable)
    from agent_friday.privacy import vault_crypto as vc
    key = bytes(range(32))
    monkeypatch.setattr(cs, "_vault_key", lambda: key)
    blob, method = cs.protect(FAKE)
    assert method == "vault" and blob.startswith(vc.MAGIC) and FAKE not in blob


def test_an_old_plaintext_blob_can_still_be_read_so_the_migration_can_move_it():
    assert cs.unprotect(FAKE) == FAKE


def test_the_status_report_says_when_nothing_can_encrypt(monkeypatch):
    _nothing_can_encrypt(monkeypatch)
    monkeypatch.setattr(ks, "root_key", lambda: (_ for _ in ()).throw(RuntimeError("no keystore")))
    assert cs.protection_method() == "plaintext"      # the label the status screens already show
