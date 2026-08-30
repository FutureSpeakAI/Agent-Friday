"""Where the vault passphrase lives, and who can find it.

THE DEFECTS THESE PIN
---------------------
The passphrase that decrypts ~/.friday/vault has, until now, had exactly one
automatic home: ``<install>/app/start.bat``, in the clear, on the line after the
API keys. That directory is the one the installer deletes and rebuilds. 5.6.6
taught the installer to carry the file across that delete; the credential still
lived in the condemned building, and it still sat in plaintext.

Three separate things were wrong, and they fail independently:

  F1  ``services/agent.py::_get_vault_key`` documents "tries the OS keychain
      before the environment variable so the passphrase never needs to appear
      in a shell script" and then reads the environment first. Since
      ``core._bootstrap_env_from_launch_scripts`` loads start.bat INTO the
      environment at import, start.bat wins over the keychain -- the exact
      opposite of the documented intent.

  F2  ``services/credential_store.py`` never consults the keychain at all. So
      ``friday vault-setup``, which writes ONLY to the keychain, gives you a
      working Sovereign Vault and a credential store that silently drops to
      DPAPI. Two modules, one secret, two different answers.

  F3  There is no durable home for the passphrase outside the app directory.

These are red-first: each test asserts the CORRECT behaviour and fails against
the code as it stood at 09b8114.
"""
from __future__ import annotations

import os
import sys
import types

import pytest


# -- The DPAPI file home is Windows-only -------------------------------------
# With `keyring` absent, the ONLY durable home is the DPAPI-wrapped file, and
# CryptProtectData exists only on Windows: vault_passphrase.dpapi() returns
# None when os.name != "nt", so store() writes nothing at all on Linux. The
# two tests below are about what that file does, so off Windows they have no
# subject and fail describing an empty directory rather than the behaviour.
#
# This guard states a real and previously unwritten platform assumption: on a
# non-Windows host the passphrase has NO durable home. That costs users
# nothing today -- Friday is a Windows desktop app (tray, DPAPI, PowerShell,
# packaging/windows is the only packaging path) -- but it is an assumption,
# not a law, and it is now recorded where someone porting this will hit it.
requires_dpapi = pytest.mark.skipif(
    sys.platform != "win32",
    reason="the durable file home is DPAPI-wrapped; CryptProtectData is Windows-only",
)


# -- A fake OS keychain ------------------------------------------------------
# `keyring` is an optional dependency (pyproject.toml:86). The packaged
# installer ships it (packaging/windows/requirements/core.txt:38); a source
# checkout does not, and this repo's dev venv does not have it. So the tests
# inject one. That absence is itself the reason the design cannot make the
# keychain a SOLE store -- see test_dpapi_file_is_a_durable_home_without_keyring.

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
def fake_keyring(monkeypatch):
    fk = _FakeKeyring()
    mod = types.ModuleType("keyring")
    mod.get_password = fk.get_password
    mod.set_password = fk.set_password
    mod.delete_password = fk.delete_password
    monkeypatch.setitem(sys.modules, "keyring", mod)
    return fk


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def friday_home(tmp_path, monkeypatch):
    """An isolated ~/.friday for the passphrase store to write into."""
    import agent_friday.core as core

    from agent_friday.services import vault_passphrase as vp

    home = tmp_path / ".friday"
    home.mkdir(parents=True)
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setattr(vp, "_SECURITY_DIR", home / "security")
    vp.reset_cache()
    yield home
    vp.reset_cache()


# -- F1 ----------------------------------------------------------------------

def test_keychain_beats_a_passphrase_that_came_from_start_bat(
    fake_keyring, clean_env, friday_home, monkeypatch
):
    """The docstring's promise, enforced.

    ``agent.py:4855`` promises the keychain is consulted first "so the
    passphrase never needs to appear in a shell script". The environment IS the
    shell script -- ``core._bootstrap_env_from_launch_scripts`` copies
    start.bat's SET lines into os.environ at package import, and publishes which
    names it took from there in ``core.ENV_FROM_LAUNCH_SCRIPTS``.

    So "env vs keychain" is the wrong question. The right one is "did a HUMAN
    set this variable, or did the file we are trying to retire set it?" A
    deliberate operator export must still win -- that is what an env var is for.
    A value that arrived from start.bat must not.
    """
    import agent_friday.core as core

    from agent_friday.services import vault_passphrase as vp

    fake_keyring.set_password("agent-friday", "vault-passphrase", "from-the-keychain")
    monkeypatch.setenv("FRIDAY_PASSWORD", "from-start-bat")
    monkeypatch.setattr(core, "ENV_FROM_LAUNCH_SCRIPTS", {"FRIDAY_PASSWORD"}, raising=False)

    secret, source = vp.resolve()
    assert secret == "from-the-keychain", (
        "start.bat's value won over the OS keychain -- the precise inversion "
        "agent.py's own docstring forbids (got source=%r)" % (source,)
    )


def test_a_deliberate_operator_export_still_wins(
    fake_keyring, clean_env, friday_home, monkeypatch
):
    """The other half of F1: do not overcorrect.

    Someone who exports FRIDAY_VAULT_PASSPHRASE in their own shell is making a
    deliberate statement about this process. A stale keychain entry must not
    override it, or we have swapped one silent-wrong-value bug for another.
    """
    import agent_friday.core as core

    from agent_friday.services import vault_passphrase as vp

    fake_keyring.set_password("agent-friday", "vault-passphrase", "stale-keychain")
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", "deliberate-export")
    monkeypatch.setattr(core, "ENV_FROM_LAUNCH_SCRIPTS", set(), raising=False)

    secret, source = vp.resolve()
    assert secret == "deliberate-export", "operator export was ignored (source=%r)" % (source,)


# -- F2 ----------------------------------------------------------------------

def test_credential_store_sees_a_keychain_only_passphrase(
    fake_keyring, clean_env, friday_home, monkeypatch
):
    """``friday vault-setup`` alone must fully restore an install.

    It writes ONLY to the keychain (cli.py:1091). Before this change,
    ``credential_store._friday_password()`` read FRIDAY_PASSWORD from the
    environment and nothing else, so the Sovereign Vault came back and the
    credential store silently dropped a tier to DPAPI -- invisible until someone
    inspected which cipher a blob had been written with.
    """
    from agent_friday.services import credential_store as cs

    fake_keyring.set_password("agent-friday", "vault-passphrase", "keychain-only-secret")
    monkeypatch.setattr(cs, "_VAULT_KEY", None, raising=False)
    monkeypatch.setattr(cs, "_VAULT_KEY_READY", False, raising=False)

    assert cs._friday_password() == "keychain-only-secret", (
        "credential_store could not see a passphrase that the vault can see; "
        "the two resolvers still disagree"
    )


def test_both_resolvers_return_the_same_secret(
    fake_keyring, clean_env, friday_home, monkeypatch
):
    """One secret, one answer. The unification, stated directly."""
    from agent_friday.services import credential_store as cs
    from agent_friday.services import vault_passphrase as vp

    fake_keyring.set_password("agent-friday", "vault-passphrase", "the-one-secret")
    monkeypatch.setattr(cs, "_VAULT_KEY", None, raising=False)
    monkeypatch.setattr(cs, "_VAULT_KEY_READY", False, raising=False)

    assert cs._friday_password() == vp.resolve()[0] == "the-one-secret"


# -- F3 ----------------------------------------------------------------------

@requires_dpapi
def test_dpapi_file_is_a_durable_home_without_keyring(clean_env, friday_home, monkeypatch):
    """``keyring`` is optional and this venv does not have it.

    A store that only works when an optional dependency imports is not a store.
    With the keychain unavailable, ``store()`` must still leave the passphrase
    somewhere ``resolve()`` can find it on the next process.
    """
    from agent_friday.services import vault_passphrase as vp

    monkeypatch.setitem(sys.modules, "keyring", None)  # import keyring -> ImportError

    written = vp.store("survives-without-keyring")
    assert written, "store() wrote the passphrase nowhere at all"

    vp.reset_cache()
    secret, source = vp.resolve()
    assert secret == "survives-without-keyring", "lost it (source=%r)" % (source,)


def test_the_durable_home_is_not_in_the_app_directory(friday_home):
    """T1, the static containment proof.

    ``start.bat`` was not carelessly placed -- it was placed somewhere everyone
    believed was safe, and nobody checked it against what app.copy actually
    does. A second convention would fail the same way, so this asserts the
    property rather than trusting it: no durable passphrase home may sit under
    the package root, which for a packaged install IS <InstallRoot>\\app, the
    directory install.ps1:545 deletes recursively.
    """
    from pathlib import Path

    from agent_friday.services import vault_passphrase as vp

    proj_root = Path(vp.__file__).resolve().parents[3]
    homes = list(vp.file_homes())
    assert homes, "there is no durable file home at all"
    for path in homes:
        assert proj_root not in Path(path).resolve().parents, (
            "%s lives under the app directory the installer deletes" % (path,)
        )


@requires_dpapi
def test_a_torn_write_is_not_mistaken_for_a_passphrase(clean_env, friday_home, monkeypatch):
    """A half-written credential is worse than an absent one: it looks present.

    The DPAPI file must be written atomically, and a corrupt blob must resolve
    to "nothing found" rather than to garbage that then derives a wrong key and
    reports the vault as broken.
    """
    from agent_friday.services import vault_passphrase as vp

    monkeypatch.setitem(sys.modules, "keyring", None)
    vp.store("intact")

    homes = [p for p in vp.file_homes() if os.path.exists(p)]
    assert homes, "nothing was written to disk to corrupt"
    with open(homes[0], "wb") as fh:
        fh.write(b"FRIDAYDPAPI\x01truncated-garbage")

    vp.reset_cache()
    secret, source = vp.resolve()
    assert secret == "", "a corrupt blob resolved to %r from %r" % (secret, source)
