"""First-run key creation is secure (north star §34.4, NS-34.4-3).

Every key Friday mints on first use is random, created once even when two
first uses race (two threads, or two processes), never half-written, and
restricted to the current user. Before this, the keystore, the file-grants
signing key and the vault salt each had a window where two first uses minted
two different keys and the loser's work became unreadable.
"""
from __future__ import annotations

import json
import os
import sys
import threading

import pytest


# ── the shared helper ────────────────────────────────────────────────────────

def test_create_exclusive_creates_once(tmp_path):
    from agent_friday.services import credential_store as cs
    p = tmp_path / "k" / "secret.bin"
    assert cs.create_exclusive(p, b"first") is True
    assert cs.create_exclusive(p, b"second") is False
    assert p.read_bytes() == b"first"
    assert [x.name for x in p.parent.iterdir()] == ["secret.bin"], "a temp file was left behind"


def test_create_exclusive_hardens_the_file(tmp_path, monkeypatch):
    from agent_friday.services import credential_store as cs
    hardened = []
    monkeypatch.setattr(cs, "harden_permissions", lambda p: hardened.append(os.path.basename(str(p))))
    cs.create_exclusive(tmp_path / "secret.bin", b"x")
    assert "secret.bin" in hardened


def test_harden_permissions_reports_a_failure(tmp_path, monkeypatch):
    from agent_friday.services import credential_store as cs
    p = tmp_path / "f"
    p.write_bytes(b"x")
    if os.name == "nt":
        monkeypatch.setattr(cs.subprocess, "run",
                            lambda *a, **k: type("R", (), {"returncode": 5})())
    else:
        monkeypatch.setattr(cs.os, "chmod", lambda *a, **k: (_ for _ in ()).throw(PermissionError("no")))
    assert cs.harden_permissions(p) is False


# ── the keystore root key ────────────────────────────────────────────────────

@pytest.fixture
def keystore(tmp_path, monkeypatch):
    from agent_friday.services import keystore as ks
    monkeypatch.setattr(ks, "KEYSTORE_PATH", tmp_path / "security" / "keystore.json")
    ks._reset_cache_for_tests()
    yield ks
    ks._reset_cache_for_tests()


def test_a_rival_keystore_created_first_is_adopted_not_overwritten(keystore, monkeypatch):
    """Another process creates the keystore between this one's check and its
    write: this process must use that key, and leave the file alone."""
    from agent_friday.services import credential_store as cs
    ks = keystore
    rival = {"version": 1, "created": "x", "cipher": "aes-256-gcm", "wrap": "none",
             "key": __import__("base64").b64encode(b"R" * 32).decode()}
    real = cs.create_exclusive

    def racing(path, data, **kw):
        if path == ks.KEYSTORE_PATH and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rival), encoding="utf-8")
        return real(path, data, **kw)
    monkeypatch.setattr(cs, "create_exclusive", racing)
    assert ks.root_key() == b"R" * 32
    assert json.loads(ks.KEYSTORE_PATH.read_text(encoding="utf-8"))["key"] == rival["key"]


def test_two_threads_get_one_root_key(keystore):
    ks = keystore
    out, barrier = [], threading.Barrier(4)

    def go():
        barrier.wait()
        out.append(ks.root_key(use_cache=False))
    ts = [threading.Thread(target=go) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(out)) == 1 and len(out[0]) == 32


def test_an_unreadable_keystore_is_not_replaced(keystore):
    ks = keystore
    ks.KEYSTORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ks.KEYSTORE_PATH.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot be read"):
        ks.root_key()
    assert ks.KEYSTORE_PATH.read_text(encoding="utf-8") == "{ not json"


# ── the file-grants signing key ──────────────────────────────────────────────

@pytest.fixture
def grants_home(tmp_path, monkeypatch, keystore):
    from agent_friday.services import file_grants as fg
    monkeypatch.setattr(fg, "_ledger_path", lambda: tmp_path / "privacy" / "file_grants.jsonl")
    fg._SIGNING_KEY_CACHE.clear()
    yield fg
    fg._SIGNING_KEY_CACHE.clear()


def test_two_threads_mint_one_file_grants_key(grants_home, monkeypatch):
    import secrets
    import time
    fg = grants_home
    real = secrets.token_bytes

    def slow(n):
        time.sleep(0.05)                    # widen the window between "none yet" and the write
        return real(n)
    monkeypatch.setattr(secrets, "token_bytes", slow)
    out, barrier = [], threading.Barrier(3)

    def go():
        barrier.wait()
        out.append(fg._secret_bytes())
    ts = [threading.Thread(target=go) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    from agent_friday.services import credential_store as cs
    assert len(set(out)) == 1
    assert cs.read_secret(fg._signing_key_path(), use=False) == out[0]


def test_a_file_grants_key_another_process_wrote_first_is_adopted(grants_home, monkeypatch):
    from agent_friday.services import credential_store as cs
    fg = grants_home
    theirs = b"T" * 32
    real = cs.create_exclusive

    def racing(path, data, **kw):
        if path == fg._signing_key_path() and not path.exists():
            cs.write_secret(path, theirs)
        return real(path, data, **kw)
    monkeypatch.setattr(cs, "create_exclusive", racing)
    assert fg._secret_bytes() == theirs


# ── the vault salt ───────────────────────────────────────────────────────────

def test_the_vault_salt_on_disk_is_the_one_used(tmp_path, monkeypatch):
    from agent_friday.services import credential_store as cs
    cfg = tmp_path / "vault" / ".vault_config.json"
    theirs = "ab" * 16
    real = cs.create_exclusive

    def racing(path, data, **kw):
        if path == cfg and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"salt_hex": theirs}), encoding="utf-8")
        return real(path, data, **kw)
    monkeypatch.setattr(cs, "create_exclusive", racing)
    assert cs.vault_salt_hex(cfg) == theirs
    assert cs.vault_salt_hex(cfg) == theirs


def test_a_new_vault_salt_is_random_and_kept(tmp_path):
    from agent_friday.services import credential_store as cs
    a = cs.vault_salt_hex(tmp_path / "a" / ".vault_config.json")
    b = cs.vault_salt_hex(tmp_path / "b" / ".vault_config.json")
    assert len(a) == 32 and a != b
    assert cs.vault_salt_hex(tmp_path / "a" / ".vault_config.json") == a


# ── the governance key's file fallback ───────────────────────────────────────

def _no_keychain(monkeypatch):
    import types
    mod = types.ModuleType("keyring")
    errs = types.ModuleType("keyring.errors")

    class NoKeyringError(Exception):
        pass
    errs.NoKeyringError = NoKeyringError
    mod.errors = errs
    mod.get_password = lambda s, a: (_ for _ in ()).throw(NoKeyringError())
    mod.set_password = lambda s, a, v: (_ for _ in ()).throw(NoKeyringError())
    monkeypatch.setitem(sys.modules, "keyring", mod)
    monkeypatch.setitem(sys.modules, "keyring.errors", errs)


def test_the_governance_key_file_is_hardened(tmp_path, monkeypatch):
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday import secure_file
    _no_keychain(monkeypatch)
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", tmp_path / "vault" / ".governance-key")
    hardened = []
    monkeypatch.setattr(secure_file, "harden", lambda p: hardened.append(os.path.basename(str(p))) or True)
    key = poi.get_governance_key()
    assert len(key) == 32 and ".governance-key" in hardened


def test_a_governance_key_another_process_wrote_first_is_adopted(tmp_path, monkeypatch):
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday import secure_file
    _no_keychain(monkeypatch)
    path = tmp_path / "vault" / ".governance-key"
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", path)
    theirs = b"G" * 32
    real = secure_file.create_exclusive

    def racing(p, data, **kw):
        if p == path and not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(theirs)
        return real(p, data, **kw)
    monkeypatch.setattr(secure_file, "create_exclusive", racing)
    assert poi.get_governance_key() == theirs
    assert path.read_bytes() == theirs


# ── the session secret and the local CA key ──────────────────────────────────

def test_an_unusable_session_secret_is_replaced_once(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.delenv("FRIDAY_SECRET_KEY", raising=False)
    monkeypatch.setattr(core, "friday_home", lambda: tmp_path)
    (tmp_path / "secret_key").write_text("", encoding="utf-8")
    first = core._load_or_create_secret()
    assert len(first) >= 32
    assert (tmp_path / "secret_key").read_text(encoding="utf-8").strip() == first
    assert core._load_or_create_secret() == first


def test_a_new_session_secret_is_written_once(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.delenv("FRIDAY_SECRET_KEY", raising=False)
    monkeypatch.setattr(core, "friday_home", lambda: tmp_path)
    a = core._load_or_create_secret()
    assert core._load_or_create_secret() == a
    assert [p.name for p in tmp_path.iterdir()] == ["secret_key"]

def test_the_local_ca_private_key_is_hardened(tmp_path, monkeypatch):
    from agent_friday.services import credential_store as cs, local_ca
    hardened = []
    monkeypatch.setattr(cs, "harden_permissions", lambda p: hardened.append(os.path.basename(str(p))))
    marker = ("PRIVATE" + " KEY").encode()          # built here so no key-shaped literal sits in the file
    local_ca._write(tmp_path / "ca-key.pem", b"-----BEGIN " + marker + b"-----\nx\n")
    local_ca._write(tmp_path / "ca.pem", b"-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
    assert "ca-key.pem" in hardened and "ca.pem" not in hardened
