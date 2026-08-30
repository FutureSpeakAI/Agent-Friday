"""Moving an existing user's passphrase out of start.bat without stranding them.

WHAT MIGRATION HAS TO SURVIVE
-----------------------------
Every existing install has its passphrase in exactly one place: a plaintext
``SET FRIDAY_PASSWORD=`` line in ``<install>/app/start.bat``. Moving it is a
one-way operation on the only copy of an unrecoverable credential, so the
ordering is the entire safety argument:

    copy -> verify the copy by reading it back -> remove the original

Each test below kills the migration at one of those seams and asserts the
passphrase is still findable afterwards. That is the property that matters --
not "migration succeeded", but "no interruption loses the vault".

The last two tests cover the case the ordering cannot fix: two stored
passphrases that disagree. Only one of them opens the vault, and guessing
destroys the data, so migration must refuse rather than resolve.
"""
from __future__ import annotations

import os
import sys
import types

import pytest


class _FakeKeyring:
    def __init__(self):
        self._store: dict = {}

    def get_password(self, service, account):
        return self._store.get((service, account))

    def set_password(self, service, account, value):
        self._store[(service, account)] = value

    def delete_password(self, service, account):
        self._store.pop((service, account), None)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated home, an isolated start.bat, and a fake keychain."""
    import agent_friday.core as core

    from agent_friday.services import vault_passphrase as vp

    home = tmp_path / ".friday"
    home.mkdir(parents=True)
    app = tmp_path / "app"
    app.mkdir()

    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setattr(vp, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(core, "ENV_FROM_LAUNCH_SCRIPTS", set(), raising=False)
    for var in ("FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    fk = _FakeKeyring()
    mod = types.ModuleType("keyring")
    mod.get_password = fk.get_password
    mod.set_password = fk.set_password
    mod.delete_password = fk.delete_password
    monkeypatch.setitem(sys.modules, "keyring", mod)

    # Redirect start.bat resolution at the two functions that compute it.
    start_bat = app / "start.bat"

    def _read():
        if not start_bat.exists():
            return ""
        import re
        m = re.search(r"(?im)^\s*SET\s+FRIDAY_PASSWORD=(.*)$",
                      start_bat.read_text(encoding="utf-8", errors="ignore"))
        return m.group(1).strip() if m else ""

    def _strip():
        if not start_bat.exists():
            return False
        import re
        text = start_bat.read_text(encoding="utf-8")
        out = re.sub(r"(?im)^\s*SET\s+FRIDAY_PASSWORD=.*\r?\n?", "", text)  # pragma: allowlist secret
        if out == text:
            return False
        start_bat.write_text(out, encoding="utf-8")
        return True

    monkeypatch.setattr(vp, "_from_start_bat", _read)
    monkeypatch.setattr(vp, "_strip_start_bat_passphrase", _strip)
    vp.reset_cache()

    class Env:
        pass

    e = Env()
    e.home = home
    e.start_bat = start_bat
    e.keyring = fk
    e.vp = vp
    yield e
    vp.reset_cache()


def _write_start_bat(path, passphrase):
    path.write_text(
        "@echo off\r\n"
        "title Agent Friday\r\n"
        "SET ANTHROPIC_API_KEY=placeholder-not-a-key\r\n"
        "SET FRIDAY_PASSWORD=%s\r\n"
        "python server.py\r\n" % passphrase,
        encoding="utf-8",
    )


# -- The happy path -----------------------------------------------------------

def test_migration_moves_it_and_removes_the_plaintext_line(env):
    _write_start_bat(env.start_bat, "the-original-passphrase")
    assert env.vp.resolve(use_cache=False)[0] == "the-original-passphrase"

    result = env.vp.migrate()

    assert result["action"] == "migrated", result
    assert result["wrote"], "nothing was written to a durable home"
    assert result["stripped"] is True, "the plaintext line was left in start.bat"

    text = env.start_bat.read_text(encoding="utf-8")
    assert "FRIDAY_PASSWORD" not in text, "the passphrase is still in start.bat"
    assert "ANTHROPIC_API_KEY" in text, (
        "migration removed more than the passphrase line -- the API keys have a "
        "separate argument and are not this function's business"
    )

    env.vp.reset_cache()
    secret, source = env.vp.resolve(use_cache=False)
    assert secret == "the-original-passphrase", "the passphrase did not survive the move"
    assert "start.bat" not in source, "still being resolved from start.bat (source=%r)" % source


def test_migration_is_idempotent(env):
    _write_start_bat(env.start_bat, "pw")
    env.vp.migrate()
    again = env.vp.migrate()
    assert again["action"] == "already-migrated", again
    assert env.vp.resolve(use_cache=False)[0] == "pw"


# -- The interruption seams ---------------------------------------------------

def test_interrupted_before_the_write_changes_nothing(env, monkeypatch):
    """Killed during store(). start.bat must be untouched."""
    _write_start_bat(env.start_bat, "pw")

    def boom(_):
        raise KeyboardInterrupt("killed mid-write")

    monkeypatch.setattr(env.vp, "store", boom)
    with pytest.raises(KeyboardInterrupt):
        env.vp.migrate()

    assert "SET FRIDAY_PASSWORD=pw" in env.start_bat.read_text(encoding="utf-8")
    env.vp.reset_cache()
    assert env.vp.resolve(use_cache=False)[0] == "pw"


def test_a_write_that_did_not_take_does_not_remove_the_original(env, monkeypatch):
    """store() reported success but nothing reads back. Refuse to strip."""
    _write_start_bat(env.start_bat, "pw")
    monkeypatch.setattr(env.vp, "store", lambda _: ["keychain"])
    monkeypatch.setattr(env.vp, "durable_value", lambda: ("", ""))

    result = env.vp.migrate()

    assert result["action"] == "blocked", result
    assert result["stripped"] is False
    assert "SET FRIDAY_PASSWORD=pw" in env.start_bat.read_text(encoding="utf-8")


def test_no_durable_home_available_leaves_start_bat_alone(env, monkeypatch):
    """No keychain and no DPAPI. Better a plaintext passphrase than none."""
    _write_start_bat(env.start_bat, "pw")
    monkeypatch.setitem(sys.modules, "keyring", None)
    monkeypatch.setattr(env.vp, "dpapi_available", lambda: False)

    result = env.vp.migrate()

    assert result["action"] == "blocked", result
    assert "SET FRIDAY_PASSWORD=pw" in env.start_bat.read_text(encoding="utf-8")
    env.vp.reset_cache()
    assert env.vp.resolve(use_cache=False)[0] == "pw", "the user lost their passphrase"


def test_interrupted_between_verify_and_strip_is_finished_next_boot(env):
    """Both copies exist and start.bat still has the line. Resume, do not redo."""
    _write_start_bat(env.start_bat, "pw")
    env.vp.store("pw")            # the copy happened
    env.vp.reset_cache()          # ... and then the process died

    result = env.vp.migrate()

    assert result["action"] == "finish", result
    assert result["stripped"] is True
    assert "FRIDAY_PASSWORD" not in env.start_bat.read_text(encoding="utf-8")
    env.vp.reset_cache()
    assert env.vp.resolve(use_cache=False)[0] == "pw"


# -- Disagreement (Q4) --------------------------------------------------------

def test_two_different_passphrases_are_never_silently_resolved(env):
    """One of them does not open the vault. Guessing destroys the data."""
    _write_start_bat(env.start_bat, "start-bat-value")
    env.keyring.set_password("agent-friday", "vault-passphrase", "keychain-value")
    env.vp.reset_cache()

    result = env.vp.migrate()

    assert result["action"] == "conflict", result
    assert result["stripped"] is False, "migration destroyed one of two candidate keys"
    assert "SET FRIDAY_PASSWORD=start-bat-value" in env.start_bat.read_text(encoding="utf-8")  # pragma: allowlist secret
    assert "two different passphrases" in result["detail"]


def test_a_conflict_is_decided_by_which_one_opens_the_vault(env):
    """The one question with a factual answer: ask the ciphertext."""
    import json

    from agent_friday.privacy import vault_crypto as vc

    vault = env.home / "vault"
    vault.mkdir(parents=True)
    salt = os.urandom(16)
    (vault / ".vault_config.json").write_text(
        json.dumps({"salt_hex": salt.hex(), "kdf": "argon2id", "cipher": "aes-256-gcm"}),
        encoding="utf-8")
    # The vault was encrypted under the value that is in start.bat.
    (vault / "note.enc").write_bytes(
        vc.encrypt(b"a real note", vc.derive_key("start-bat-value", salt)))

    _write_start_bat(env.start_bat, "start-bat-value")
    env.keyring.set_password("agent-friday", "vault-passphrase", "stale-keychain-value")
    env.vp.reset_cache()

    result = env.vp.migrate()

    assert result["action"] == "conflict", result
    assert "the start.bat copy opens the vault" in result["detail"], result["detail"]
    assert result["stripped"] is False
