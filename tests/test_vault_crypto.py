"""Tests for vault_crypto — run with: python tests/test_vault_crypto.py

Uses FAST_PROFILE so the Argon2id KDF doesn't take 256MB/4-passes per call.
All tests operate on synthetic data — never on real vault files.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_friday.privacy.vault_crypto as vc  # noqa: E402

FAST = vc.FAST_PROFILE
SALT = bytes(range(16, 48))  # 32 deterministic bytes


def _key(passphrase="correct horse battery staple"):
    return vc.derive_key(passphrase, SALT, profile=FAST)


def test_derive_key_deterministic():
    assert _key() == _key()
    assert len(_key()) == vc.KEY_LEN


def test_derive_key_passphrase_sensitive():
    assert _key("a") != _key("b")


def test_derive_key_rejects_empty():
    try:
        vc.derive_key("", SALT, profile=FAST)
        assert False, "empty passphrase should raise"
    except vc.VaultCryptoError:
        pass


def test_roundtrip():
    key = _key()
    pt = b"Custody case D-1-FM-XX-XXXXXX \xe2\x80\x94 highly sensitive."
    blob = vc.encrypt(pt, key)
    assert vc.is_encrypted(blob)
    assert blob != pt
    assert vc.decrypt(blob, key) == pt


def test_nonce_is_random():
    key = _key()
    pt = b"same plaintext"
    assert vc.encrypt(pt, key) != vc.encrypt(pt, key)  # different nonces


def test_wrong_key_fails():
    blob = vc.encrypt(b"secret", _key("right"))
    try:
        vc.decrypt(blob, _key("wrong"))
        assert False, "wrong key should fail"
    except vc.IntegrityError:
        pass


def test_tamper_detected():
    key = _key()
    blob = bytearray(vc.encrypt(b"secret payload here", key))
    blob[-1] ^= 0x01  # flip a ciphertext/tag bit
    try:
        vc.decrypt(bytes(blob), key)
        assert False, "tampered blob should fail"
    except vc.IntegrityError:
        pass


def test_version_downgrade_detected():
    key = _key()
    blob = bytearray(vc.encrypt(b"secret", key))
    blob[len(vc.MAGIC) - 1] ^= 0x01  # flip the version byte (it's AAD)
    try:
        vc.decrypt(bytes(blob), key)
        assert False, "version tamper should fail"
    except (vc.IntegrityError, vc.VaultCryptoError):
        pass


def test_decrypt_plaintext_rejected():
    try:
        vc.decrypt(b"this is just plaintext, no magic", _key())
        assert False, "non-blob should raise"
    except vc.VaultCryptoError:
        pass


def test_file_roundtrip_and_helper():
    key = _key()
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "legal.md"
        enc = Path(d) / "legal.md.enc"
        dec = Path(d) / "legal.out.md"
        src.write_bytes(b"# Court order inventory\nsensitive details\n")
        assert vc.roundtrip_ok(src, key)
        vc.encrypt_file(src, enc, key)
        assert vc.is_encrypted(enc.read_bytes())
        vc.decrypt_file(enc, dec, key)
        assert dec.read_bytes() == src.read_bytes()


def test_hmac_sign_verify():
    key = os.urandom(32)
    entry = {"tool": "read_wiki", "ring": 0, "decision": "allow"}
    signed = vc.sign_entry(entry, key)
    assert "hmac" in signed
    assert vc.verify_entry(signed, key)


def test_hmac_tamper_detected():
    key = os.urandom(32)
    signed = vc.sign_entry({"tool": "x", "decision": "allow"}, key)
    signed["decision"] = "deny"  # tamper after signing
    assert not vc.verify_entry(signed, key)


def test_hmac_wrong_key():
    signed = vc.sign_entry({"a": 1}, os.urandom(32))
    assert not vc.verify_entry(signed, os.urandom(32))


def test_hmac_matches_server_scheme():
    # Replicate server.py exactly: hmac over json.dumps(entry, sort_keys=True)
    # with the hmac field excluded.
    import hashlib
    import hmac as _hmac
    import json as _json
    key = os.urandom(32)
    entry = {"b": 2, "a": 1, "decision": "allow"}
    canonical = _json.dumps(entry, sort_keys=True).encode("utf-8")
    expected = _hmac.new(key, canonical, hashlib.sha256).hexdigest()
    signed = vc.sign_entry(entry, key)
    assert signed["hmac"] == expected, "must match server.py decision-BOM scheme"


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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
