"""Migrating credentials onto Friday's keystore must be unable to lose one.

This is the operation with the worst downside in the codebase. A bad run means
re-authorising every Google account, every MCP server, every platform and every
provider key - and the plaintext exists nowhere else to check against.

The rescue path is tested too, because it is not hypothetical: credentials
can ALREADY be unopenable, encrypted under a passphrase the resolver has
stopped preferring. Migration is what recovers them; otherwise "Firecrawl
needs an API key" really means "Friday has your Firecrawl key and cannot read
it".
"""
from __future__ import annotations

import json

import pytest

import agent_friday.privacy.vault_crypto as vc
from agent_friday.services import credential_store as cs
from agent_friday.services import keystore as ks


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(cs, "_SECURITY_DIR", tmp_path / "security")
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", tmp_path / "providers" / "keys")
    monkeypatch.setattr(cs, "_CRED_AUDIT_LOG",
                        tmp_path / "security" / "credential_audit.jsonl")
    monkeypatch.setattr(ks, "KEYSTORE_PATH", tmp_path / "security" / "keystore.json")
    monkeypatch.setattr(ks, "_CACHED_KEY", None)
    monkeypatch.setattr(ks, "_passphrase", lambda: "")
    return tmp_path


def _legacy_blob(data: bytes, passphrase: str, home):
    """A credential as the OLD scheme wrote it: vault magic, key from a
    passphrase and the shared vault salt."""
    cfg = home / "vault" / ".vault_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if cfg.exists():
        salt = bytes.fromhex(json.loads(cfg.read_text())["salt_hex"])
    else:
        salt = b"\x11" * 16
        cfg.write_text(json.dumps({"salt_hex": salt.hex()}))
    return vc.encrypt(data, vc.derive_key(passphrase, salt))


def _put(home, rel, blob):
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(blob)
    return p


def test_a_readable_credential_is_rewritten_and_still_opens(home, monkeypatch):
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE",
                        home / "vault" / ".vault_config.json")
    monkeypatch.setattr(cs, "_legacy_keys", lambda: [])
    p = _put(home, "providers/keys/openrouter.key", ks.encrypt(b"sk-live-xyz"))
    rep = cs.migrate_to_keystore()
    assert rep["already"] == 1 and rep["migrated"] == 0
    assert cs.read_secret(p) == b"sk-live-xyz"


def test_a_credential_under_an_old_passphrase_is_rescued(home, monkeypatch):
    """THE CRUX. Most credentials on an affected install are in this state."""
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE",
                        home / "vault" / ".vault_config.json")
    blob = _legacy_blob(b"fc-secret", "the-old-one", home)
    p = _put(home, "providers/keys/firecrawl.key", blob)

    salt = bytes.fromhex(json.loads(
        (home / "vault" / ".vault_config.json").read_text())["salt_hex"])
    monkeypatch.setattr(cs, "_legacy_keys",
                        lambda: [("launcher", vc.derive_key("the-old-one", salt))])
    # The CURRENT key is a different passphrase, exactly as on the real machine.
    monkeypatch.setattr(cs, "_vault_key",
                        lambda: vc.derive_key("the-new-one", salt))

    rep = cs.migrate_to_keystore()
    assert rep["migrated"] == 1
    assert [r["via"] for r in rep.get("recovered", [])] == ["launcher"]
    assert cs.read_secret(p) == b"fc-secret"


def test_a_credential_nothing_can_open_is_left_exactly_where_it_is(home, monkeypatch):
    """A blob this process cannot read is not necessarily a dead one - the
    process that wrote it may still open it. Deleting it would destroy the
    only copy on the strength of a guess."""
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE",
                        home / "vault" / ".vault_config.json")
    blob = _legacy_blob(b"lost", "nobody-knows-this", home)
    p = _put(home, "providers/keys/mystery.key", blob)
    before = p.read_bytes()
    monkeypatch.setattr(cs, "_legacy_keys", lambda: [])
    monkeypatch.setattr(cs, "_vault_key", lambda: None)

    rep = cs.migrate_to_keystore()
    assert rep["migrated"] == 0
    assert [x["path"] for x in rep["unreadable"]] == [str(p)]
    assert p.read_bytes() == before, "an unreadable credential was modified"
    assert p.exists()


def test_the_originals_are_backed_up_before_anything_is_replaced(home, monkeypatch):
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE",
                        home / "vault" / ".vault_config.json")
    blob = _legacy_blob(b"v", "old", home)
    p = _put(home, "providers/keys/a.key", blob)
    salt = bytes.fromhex(json.loads(
        (home / "vault" / ".vault_config.json").read_text())["salt_hex"])
    monkeypatch.setattr(cs, "_legacy_keys",
                        lambda: [("old", vc.derive_key("old", salt))])
    cs.migrate_to_keystore()
    backup = cs._migration_backup_dir() / "a.key.bak"
    assert backup.exists() and backup.read_bytes() == blob


def test_running_it_twice_is_free(home, monkeypatch):
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE",
                        home / "vault" / ".vault_config.json")
    monkeypatch.setattr(cs, "_legacy_keys", lambda: [])
    _put(home, "providers/keys/a.key", ks.encrypt(b"v"))
    first = cs.migrate_to_keystore()
    second = cs.migrate_to_keystore()
    assert second["migrated"] == 0
    assert second["already"] == first["already"] + first["migrated"]


def test_a_dry_run_writes_nothing(home, monkeypatch):
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE",
                        home / "vault" / ".vault_config.json")
    blob = _legacy_blob(b"v", "old", home)
    p = _put(home, "providers/keys/a.key", blob)
    salt = bytes.fromhex(json.loads(
        (home / "vault" / ".vault_config.json").read_text())["salt_hex"])
    monkeypatch.setattr(cs, "_legacy_keys",
                        lambda: [("old", vc.derive_key("old", salt))])
    rep = cs.migrate_to_keystore(dry_run=True)
    assert rep["migrated"] == 1 and rep["dry_run"] is True
    assert p.read_bytes() == blob, "a dry run modified a credential"
    assert not cs._migration_backup_dir().exists()


def test_old_blobs_still_open_before_any_migration_runs(home, monkeypatch):
    """"Friday has its own credential store now" must not mean "reconnect
    everything". Reads have to keep working for blobs written last month."""
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE",
                        home / "vault" / ".vault_config.json")
    blob = _legacy_blob(b"still-good", "current", home)
    p = _put(home, "providers/keys/a.key", blob)
    salt = bytes.fromhex(json.loads(
        (home / "vault" / ".vault_config.json").read_text())["salt_hex"])
    monkeypatch.setattr(cs, "_vault_key", lambda: vc.derive_key("current", salt))
    assert cs.read_secret(p) == b"still-good"


def test_new_writes_use_the_keystore(home, monkeypatch):
    monkeypatch.setattr(cs, "_VAULT_CONFIG_FILE",
                        home / "vault" / ".vault_config.json")
    p = home / "providers" / "keys" / "new.key"
    method = cs.write_secret(p, b"brand-new")
    assert method == "keystore"
    assert cs.looks_protected(p.read_bytes()) == "keystore"
    assert cs.read_secret(p) == b"brand-new"


def test_a_locked_keystore_refuses_to_write_rather_than_downgrade(home, monkeypatch):
    """Falling through to a weaker scheme would write the credential under a
    key the unlocked process cannot read - the original bug, sign flipped."""
    ks.root_key()
    ks.set_wrap("passphrase", passphrase="right")
    ks._reset_cache_for_tests()
    monkeypatch.setattr(ks, "_passphrase", lambda: "wrong")
    with pytest.raises(ks.KeystoreLocked):
        cs.write_secret(home / "providers" / "keys" / "x.key", b"v")
