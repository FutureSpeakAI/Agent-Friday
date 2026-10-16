"""PR-5 (credentials, OS-mode sequence) — fail-closed vault-passphrase storage,
and the Linux keyring/DPAPI gap this surfaces.

WHAT THIS PINS
--------------
`vault_passphrase.store()` writes the passphrase to every durable home it can
(OS keychain via `keyring`, and a DPAPI-wrapped file on Windows) and returns
the names of whichever ones actually took. Before this PR, if BOTH were
unavailable -- the exact situation on a Linux host with no `keyring` package
installed, which is Friday Linux's situation today (see KNOWN_ISSUES.md
Section 7) -- `store()` silently returned `[]`. Every caller already treats
an empty list as failure and tells the user (cli.py, setup_wizard.py,
routes/core_routes.py, routes/insights.py), so on a normal interactive
Windows install this was visible. On the sealed Friday Linux kiosk image
(FRIDAY_OS_MODE=1) there is no interactive operator to see that message, so
the same `[]` was effectively "nothing was persisted, and nobody was told in
a way that stops anything."

Under FRIDAY_OS_MODE, `store()` now raises RuntimeError instead of returning
`[]` when nothing durable could be written. Windows-default behavior (OS mode
off) is unchanged -- still returns `[]`, still lets the existing callers
report it their own way.

Only synthetic, fake test values are used -- never a real passphrase.
"""
from __future__ import annotations

import sys
import types

import pytest

from agent_friday.services import vault_passphrase as vp


class _FakeKeyring:
    """Same shape as tests/unit/test_vault_passphrase_migration.py's fake --
    an in-memory dict standing in for the OS keychain so nothing here ever
    touches a real Credential Manager / Secret Service."""

    def __init__(self, *, working: bool = True):
        self._store: dict = {}
        self._working = working

    def get_password(self, service, account):
        return self._store.get((service, account))

    def set_password(self, service, account, value):
        if not self._working:
            raise RuntimeError("no Secret Service backend available (fake)")
        self._store[(service, account)] = value


def _install_fake_keyring(monkeypatch, *, working: bool):
    fk = _FakeKeyring(working=working)
    mod = types.ModuleType("keyring")
    mod.get_password = fk.get_password
    mod.set_password = fk.set_password
    monkeypatch.setitem(sys.modules, "keyring", mod)
    return fk


def _remove_keyring(monkeypatch):
    """Simulate the real Friday Linux situation: `keyring` is not installed
    at all (it is an optional extra not in the planned venv), so `import
    keyring` inside store() raises ModuleNotFoundError."""
    monkeypatch.setitem(sys.modules, "keyring", None)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    monkeypatch.setattr(vp, "_SECURITY_DIR", home / "security")
    monkeypatch.setattr(vp, "_from_start_bat", lambda: "")
    for var in ("FRIDAY_OS_MODE", "FRIDAY_VAULT_PASSPHRASE", "FRIDAY_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    vp.reset_cache()
    yield home
    vp.reset_cache()


# ── Acceptance: OS mode + no keyring + no DPAPI -> raise, nothing on disk ──

class TestFailClosedUnderOsMode:
    def test_store_raises_when_no_keyring_and_no_dpapi(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        _remove_keyring(monkeypatch)
        monkeypatch.setattr(vp, "dpapi_available", lambda: False)

        with pytest.raises(RuntimeError, match="FRIDAY_OS_MODE"):
            vp.store("a-fake-test-passphrase-not-real")

    def test_store_raises_and_writes_nothing_to_disk(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        _remove_keyring(monkeypatch)
        monkeypatch.setattr(vp, "dpapi_available", lambda: False)

        with pytest.raises(RuntimeError):
            vp.store("a-fake-test-passphrase-not-real")

        for path in vp.file_homes():
            assert not path.exists(), f"a durable-home file was written despite the raise: {path}"

    def test_store_raises_when_keyring_present_but_backend_broken(self, monkeypatch):
        """A `keyring` import that succeeds but whose backend cannot actually
        store anything (no Secret Service provider in the image) must be
        treated the same as `keyring` not being installed at all -- store()
        already swallows the exception into `pass`; this just confirms the
        os-mode raise still fires afterward."""
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        _install_fake_keyring(monkeypatch, working=False)
        monkeypatch.setattr(vp, "dpapi_available", lambda: False)

        with pytest.raises(RuntimeError, match="FRIDAY_OS_MODE"):
            vp.store("a-fake-test-passphrase-not-real")


# ── Acceptance: a valid path available -> success, correct content ─────────

class TestSuccessPathsUnderOsMode:
    def test_store_succeeds_via_working_keyring(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        fk = _install_fake_keyring(monkeypatch, working=True)
        monkeypatch.setattr(vp, "dpapi_available", lambda: False)

        written = vp.store("a-fake-test-passphrase-not-real")

        assert written == ["keychain"]
        assert fk.get_password(vp.KEYRING_SERVICE, vp.KEYRING_ACCOUNT) == (
            "a-fake-test-passphrase-not-real"
        )

    def test_store_succeeds_via_dpapi_when_no_keyring(self, monkeypatch, tmp_path):
        """DPAPI-only success path -- exercises the real ctypes CryptProtectData
        call on this Windows dev/CI box, which is the whole reason
        vault_passphrase carries a DPAPI fallback at all."""
        if sys.platform != "win32":
            pytest.skip("DPAPI is Windows-only")
        monkeypatch.setenv("FRIDAY_OS_MODE", "1")
        _remove_keyring(monkeypatch)

        written = vp.store("a-fake-test-passphrase-not-real")

        assert written == ["protected file"]
        path = vp.file_homes()[0]
        assert path.exists()
        assert path.read_bytes().startswith(vp.DPAPI_MAGIC)

        # And it reads back correctly through the real resolver.
        vp.reset_cache()
        secret, source = vp.resolve(use_cache=False)
        assert secret == "a-fake-test-passphrase-not-real"
        assert source == "Friday's protected credential file"

    def test_store_via_real_keyring_package_null_backend(self, monkeypatch):
        """Acceptance criterion 3, literally: exercise the REAL installed
        `keyring` package (not a fake stand-in module), routed to an
        in-memory test backend via `keyring.set_keyring()` so nothing ever
        touches this machine's actual OS keychain / Credential Manager.
        Skips cleanly wherever `keyring` isn't installed (this repo's own
        dev venv and CI do not install it by default -- see
        KNOWN_ISSUES.md Section 7) rather than failing the suite.
        """
        keyring = pytest.importorskip("keyring")
        import keyring.backend as keyring_backend
        import keyring.credentials as keyring_credentials

        class _InMemoryBackend(keyring_backend.KeyringBackend):
            priority = 1  # highest priority so set_keyring() sticks

            def __init__(self):
                self._store = {}

            def get_password(self, service, username):
                return self._store.get((service, username))

            def set_password(self, service, username, password):
                self._store[(service, username)] = password

            def delete_password(self, service, username):
                self._store.pop((service, username), None)

            def get_credential(self, service, username):
                pw = self.get_password(service, username)
                if pw is None:
                    return None
                return keyring_credentials.SimpleCredential(username or "", pw)

        original_backend = keyring.get_keyring()
        # sys.modules must genuinely resolve to the real package for store()'s
        # own `import keyring as _keyring` to reach this same configured
        # instance.
        monkeypatch.setitem(sys.modules, "keyring", keyring)
        keyring.set_keyring(_InMemoryBackend())
        try:
            written = vp.store("a-fake-test-passphrase-not-real")
            assert "keychain" in written
            assert keyring.get_password(
                vp.KEYRING_SERVICE, vp.KEYRING_ACCOUNT
            ) == "a-fake-test-passphrase-not-real"
        finally:
            keyring.set_keyring(original_backend)


# ── Windows-default regression guard: OS mode OFF is unchanged ─────────────

class TestWindowsDefaultBehaviorUnchanged:
    def test_store_returns_empty_list_without_raising_when_os_mode_off(self, monkeypatch):
        monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
        _remove_keyring(monkeypatch)
        monkeypatch.setattr(vp, "dpapi_available", lambda: False)

        written = vp.store("a-fake-test-passphrase-not-real")  # must not raise

        assert written == []

    def test_store_still_writes_both_homes_when_available_and_os_mode_off(self, monkeypatch):
        monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
        fk = _install_fake_keyring(monkeypatch, working=True)

        if sys.platform == "win32":
            written = vp.store("a-fake-test-passphrase-not-real")
            assert set(written) == {"keychain", "protected file"}
        else:
            monkeypatch.setattr(vp, "dpapi_available", lambda: False)
            written = vp.store("a-fake-test-passphrase-not-real")
            assert written == ["keychain"]
        assert fk.get_password(vp.KEYRING_SERVICE, vp.KEYRING_ACCOUNT) == (
            "a-fake-test-passphrase-not-real"
        )
