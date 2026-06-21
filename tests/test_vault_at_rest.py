"""Tests for server.py's transparent encryption-at-rest layer.

Exercises the REAL _vault_read_text / _vault_write_text helpers wired into
server.py. A fast Argon2id-equivalent key is injected directly so the test
never pays the 256MB/4-pass production KDF cost and never touches real data.

Run with: python tests/test_vault_at_rest.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import the server module (heavy, but gives us the actually-wired helpers).
import server  # noqa: E402
import services.agent as _agent  # noqa: E402
import vault_crypto as vc  # noqa: E402

# Inject a deterministic key, bypassing the production KDF + FRIDAY_PASSWORD.
_TEST_KEY = vc.derive_key("test passphrase", bytes(range(16, 32)), profile=vc.FAST_PROFILE)

# _vault_read_text/_vault_write_text are DEFINED in services/agent.py and read
# _VAULT_KEY from that module's globals. Setting server._VAULT_KEY alone is a
# no-op for them (server merely re-exports the functions), and with a real
# FRIDAY_PASSWORD in the environment the derived key would stay live — so the
# toggles must hit services.agent. server is kept in the loop for any legacy
# alias.
def _set_key(key):
    for _mod in (_agent, server):
        _mod._VAULT_KEY = key
        _mod._VAULT_KEY_READY = True


def _enable_key():
    _set_key(_TEST_KEY)


def _disable_key():
    _set_key(None)


def test_write_encrypts_at_rest():
    _enable_key()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "portfolio.json"
        server._vault_write_text(p, '{"positions": ["SECRET"]}')
        raw = p.read_bytes()
        assert vc.is_encrypted(raw), "file on disk must carry the FRIDAYVAULT magic"
        assert b"SECRET" not in raw, "plaintext must not appear on disk"


def test_read_decrypts_roundtrip():
    _enable_key()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "meds.json"
        payload = '{"medications": ["highly sensitive"]}'
        server._vault_write_text(p, payload)
        assert server._vault_read_text(p) == payload


def test_read_plaintext_passthrough():
    # A pre-migration plaintext file must still read cleanly when a key is set.
    _enable_key()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "legacy.json"
        p.write_text('{"legacy": true}', encoding="utf-8")
        assert server._vault_read_text(p) == '{"legacy": true}'


def test_keyless_is_plaintext():
    _disable_key()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "noenc.json"
        server._vault_write_text(p, '{"x": 1}')
        assert p.read_bytes() == b'{"x": 1}', "no key -> plaintext on disk"
        assert server._vault_read_text(p) == '{"x": 1}'


def test_encrypted_without_key_raises():
    _enable_key()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "enc.json"
        server._vault_write_text(p, '{"secret": 1}')
        _disable_key()  # simulate password later removed
        try:
            server._vault_read_text(p)
            assert False, "encrypted blob with no key should raise"
        except Exception:
            pass


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    # server import starts background (non-daemon) threads; force a clean exit.
    # os._exit() skips buffer flushing, so flush explicitly first.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if failed else 0)


if __name__ == "__main__":
    main()
