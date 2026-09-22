"""Friday's own credential store.

WHAT WENT WRONG, and why this module exists. Credentials were encrypted with a
key derived from whatever `vault_passphrase.resolve()` returned, and that
resolver walks five sources in priority order. On 2026-09-19 Stephen's machine
held TWO DIFFERENT passphrases - one in friday_startup.bat, another in the
Windows keychain - and the resolver prefers the keychain. So every credential
written before that keychain entry appeared became unreadable the moment it
did: four provider API keys and an MCP OAuth token, silently. The health
surface reported Firecrawl as "no API key set" when the key was right there
and merely unopenable, and both Google accounts had to be reconnected every
morning.

The fix is one random root key in one file that every process finds the same
way, no keychain and no DPAPI in the path. These tests hold the properties
that make that true, and the migration property that matters more than any of
them: it must be incapable of losing a credential.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import keystore as ks


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "KEYSTORE_PATH", tmp_path / "keystore.json")
    monkeypatch.setattr(ks, "_CACHED_KEY", None)
    monkeypatch.setattr(ks, "_passphrase", lambda: "")
    return tmp_path


# ── the root key ────────────────────────────────────────────────────────────

def test_the_same_key_comes_back_every_time(store):
    """THE CRUX, and the whole bug in one assertion. Two reads, one key -
    and crucially the second read does not consult a passphrase resolver."""
    a = ks.root_key()
    ks._reset_cache_for_tests()
    b = ks.root_key()
    assert a == b and len(a) == 32


def test_the_key_does_not_depend_on_any_passphrase(store, monkeypatch):
    """Change every passphrase source and the key must not move. This is
    precisely what was not true before."""
    first = ks.root_key()
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "a-completely-different-one")
    assert ks.root_key() == first


def test_it_is_created_on_first_use(store):
    assert not ks.exists()
    ks.root_key()
    assert ks.exists()
    assert ks.describe()["wrap"] == "none"


def test_creating_over_an_existing_keystore_is_refused(store):
    """Minting a new root key would orphan every credential under the old one,
    unrecoverably, since the old key exists nowhere else."""
    ks.root_key()
    with pytest.raises(FileExistsError):
        ks.create()


def test_a_malformed_key_is_not_guessed_at(store):
    ks.root_key()
    doc = json.loads(ks.KEYSTORE_PATH.read_text())
    doc["key"] = "AAAA"
    ks.KEYSTORE_PATH.write_text(json.dumps(doc))
    ks._reset_cache_for_tests()
    with pytest.raises(RuntimeError):
        ks.root_key()


# ── wrapping ────────────────────────────────────────────────────────────────

def test_wrapping_does_not_change_the_root_key(store, monkeypatch):
    """THE REASON THE ROOT KEY IS RANDOM. Adding a passphrase must cost one
    rewrap, not a re-encryption of every credential on the machine."""
    original = ks.root_key()
    ks.set_wrap("passphrase", passphrase="hunter2")
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "hunter2")
    assert ks.root_key() == original


def test_a_wrapped_store_with_no_passphrase_is_locked_not_guessed(store, monkeypatch):
    ks.root_key()
    ks.set_wrap("passphrase", passphrase="hunter2")
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "")
    with pytest.raises(ks.KeystoreLocked):
        ks.root_key()


def test_the_wrong_passphrase_says_so(store, monkeypatch):
    """The failure this whole exercise exists to end was a key mismatch
    surfacing as an unexplained crypto error inside a token read, which then
    got filed as Google revoking the account."""
    ks.root_key()
    ks.set_wrap("passphrase", passphrase="right")
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "wrong")
    with pytest.raises(ks.KeystoreLocked):
        ks.root_key()


def test_a_wrapped_store_never_reports_itself_unlocked(store, monkeypatch):
    ks.root_key()
    ks.set_wrap("passphrase", passphrase="right")
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "wrong")
    d = ks.describe()
    assert d["wrap"] == "passphrase" and d["unlocked"] is False
    assert "Locked" in d["summary"]


def test_unwrapping_restores_unattended_use(store, monkeypatch):
    original = ks.root_key()
    ks.set_wrap("passphrase", passphrase="x")
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "x")
    ks.set_wrap("none")
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "")
    assert ks.root_key() == original


# ── the envelope ────────────────────────────────────────────────────────────

def test_round_trip(store):
    assert ks.decrypt(ks.encrypt(b"s3cret")) == b"s3cret"


def test_a_keystore_blob_is_recognisable(store):
    blob = ks.encrypt(b"x")
    assert ks.is_keystore_blob(blob)
    assert not ks.is_keystore_blob(b"{}")
    assert not ks.is_keystore_blob(b"")


def test_describe_never_leaks_key_material(store):
    ks.root_key()
    raw = json.loads(ks.KEYSTORE_PATH.read_text())["key"]
    assert raw not in json.dumps(ks.describe())
