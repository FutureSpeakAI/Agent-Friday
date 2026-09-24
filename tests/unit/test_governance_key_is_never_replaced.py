"""The governance key is minted once and never replaced by accident.

It signs the governance checkpoint's receipts, the decision BOM, dissent
events and the integrity manifest, and the cLaws pin is an HMAC under it. A
new key orphans all of that. `get_governance_key` used to mint one whenever
the keychain read raised and no file existed -- and write it over the
keychain entry -- and to fall back to a key held only in memory when saving
failed. Both are per-boot keys. Neither happens now: an unreadable existing
key raises, as the reasoning-trace and file-grants keys already do.
"""
from __future__ import annotations

import sys
import types

import pytest

from agent_friday.governance import proof_of_integrity as poi


class _NoKeyringError(Exception):
    pass


def _fake_keyring(monkeypatch, *, get=None, get_raises=None, set_raises=None):
    writes = []
    mod = types.ModuleType("keyring")
    errs = types.ModuleType("keyring.errors")
    errs.NoKeyringError = _NoKeyringError
    mod.errors = errs

    def get_password(service, account):
        if get_raises:
            raise get_raises
        return get

    def set_password(service, account, value):
        if set_raises:
            raise set_raises
        writes.append(value)

    mod.get_password, mod.set_password = get_password, set_password
    monkeypatch.setitem(sys.modules, "keyring", mod)
    monkeypatch.setitem(sys.modules, "keyring.errors", errs)
    return writes


@pytest.fixture
def keyfile(tmp_path, monkeypatch):
    p = tmp_path / "vault" / ".governance-key"
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", p)
    return p


def test_a_keychain_that_did_not_answer_is_not_overwritten(monkeypatch, keyfile):
    writes = _fake_keyring(monkeypatch, get_raises=OSError("credential manager busy"))
    with pytest.raises(RuntimeError, match="keychain did not answer"):
        poi.get_governance_key()
    assert writes == [] and not keyfile.exists()


def test_an_unreadable_key_file_is_not_replaced(monkeypatch, keyfile):
    _fake_keyring(monkeypatch, get=None)
    keyfile.parent.mkdir(parents=True)
    keyfile.write_bytes(b"not hex and not 32 bytes")
    with pytest.raises(RuntimeError, match="not minting"):
        poi.get_governance_key()
    assert keyfile.read_bytes() == b"not hex and not 32 bytes"


def test_no_keychain_at_all_mints_once_into_a_file(monkeypatch, keyfile):
    _fake_keyring(monkeypatch, get_raises=_NoKeyringError(), set_raises=_NoKeyringError())
    k1 = poi.get_governance_key()
    assert keyfile.exists() and len(k1) == 32
    assert poi.get_governance_key() == k1


def test_a_key_that_cannot_be_saved_is_not_used(monkeypatch, keyfile):
    _fake_keyring(monkeypatch, get=None, set_raises=OSError("no keychain write"))
    monkeypatch.setattr(type(keyfile), "write_bytes",
                        lambda self, data: (_ for _ in ()).throw(OSError("read-only")))
    with pytest.raises(RuntimeError, match="could not be saved"):
        poi.get_governance_key()


def test_the_agent_wrapper_has_no_per_boot_fallback(monkeypatch):
    import agent_friday.services.agent as agent
    monkeypatch.setattr(agent, "_GOVERNANCE_KEY", None)
    monkeypatch.setattr(poi, "get_governance_key",
                        lambda: (_ for _ in ()).throw(RuntimeError("unavailable")))
    with pytest.raises(RuntimeError):
        agent._get_governance_key()
    assert agent._GOVERNANCE_KEY is None
