"""Adversarial vault crypto tests — AES-256-GCM round-trip, key derivation
determinism, tamper detection, downgrade resistance, HMAC integrity, and
passphrase-change re-encryption.

Uses FAST_PROFILE so Argon2id runs quickly. NEVER use FAST_PROFILE for real data.
"""
from __future__ import annotations

import os

import pytest

from agent_friday.privacy import vault_crypto as vc


SALT = b"0123456789abcdef0123456789abcdef"  # 32 bytes


def _key(passphrase="correct horse battery staple"):
    return vc.derive_key(passphrase, SALT, profile=vc.FAST_PROFILE)


# ── Key derivation ────────────────────────────────────────────────────────────

class TestKeyDerivation:
    def test_derives_32_byte_key(self):
        assert len(_key()) == vc.KEY_LEN == 32

    def test_deterministic_for_same_inputs(self):
        assert _key("pass") == _key("pass")

    def test_different_passphrase_different_key(self):
        assert _key("pass-a") != _key("pass-b")

    def test_different_salt_different_key(self):
        k1 = vc.derive_key("pass", SALT, profile=vc.FAST_PROFILE)
        k2 = vc.derive_key("pass", b"f" * 32, profile=vc.FAST_PROFILE)
        assert k1 != k2

    def test_empty_passphrase_rejected(self):
        with pytest.raises(vc.VaultCryptoError):
            vc.derive_key("", SALT, profile=vc.FAST_PROFILE)

    def test_short_salt_rejected(self):
        with pytest.raises(vc.VaultCryptoError):
            vc.derive_key("pass", b"tooshort", profile=vc.FAST_PROFILE)

    def test_bytes_passphrase_accepted(self):
        assert len(vc.derive_key(b"raw-bytes-pass", SALT, profile=vc.FAST_PROFILE)) == 32


# ── Encrypt / decrypt round-trip ──────────────────────────────────────────────

class TestRoundTrip:
    # Explicit short ids: a raw 100 KB param value would blow past the Windows
    # 32767-char cap on PYTEST_CURRENT_TEST (the env var pytest sets per test).
    @pytest.mark.parametrize("plaintext", [
        b"",
        b"hello",
        b"a" * 100_000,
        "unicode: café 日本語 🔒".encode("utf-8"),
        os.urandom(4096),
    ], ids=["empty", "short", "large-100k", "unicode", "random-4k"])
    def test_round_trip(self, plaintext):
        key = _key()
        assert vc.decrypt(vc.encrypt(plaintext, key), key) == plaintext

    def test_blob_starts_with_magic(self):
        blob = vc.encrypt(b"data", _key())
        assert blob.startswith(vc.MAGIC)
        assert vc.is_encrypted(blob)

    def test_nonces_are_unique(self):
        key = _key()
        b1 = vc.encrypt(b"same", key)
        b2 = vc.encrypt(b"same", key)
        # Same plaintext + key but fresh random nonce → different ciphertext.
        assert b1 != b2

    def test_wrong_key_size_rejected(self):
        with pytest.raises(vc.VaultCryptoError):
            vc.encrypt(b"data", b"tooshort")
        with pytest.raises(vc.VaultCryptoError):
            vc.decrypt(vc.encrypt(b"data", _key()), b"tooshort")


# ── Tamper / downgrade detection ──────────────────────────────────────────────

class TestTamperDetection:
    def test_wrong_key_fails_integrity(self):
        blob = vc.encrypt(b"secret", _key("right"))
        with pytest.raises(vc.IntegrityError):
            vc.decrypt(blob, _key("wrong"))

    def test_flipped_ciphertext_byte_fails(self):
        key = _key()
        blob = bytearray(vc.encrypt(b"secret payload", key))
        blob[-1] ^= 0xFF  # corrupt the auth tag
        with pytest.raises(vc.IntegrityError):
            vc.decrypt(bytes(blob), key)

    def test_stripped_magic_rejected(self):
        key = _key()
        blob = vc.encrypt(b"secret", key)
        stripped = blob[len(vc.MAGIC):]  # remove magic header
        with pytest.raises(vc.VaultCryptoError):
            vc.decrypt(stripped, key)

    def test_version_downgrade_fails_tag(self):
        key = _key()
        blob = bytearray(vc.encrypt(b"secret", key))
        # Flip the version byte inside MAGIC — it's AAD, so the tag must fail.
        blob[len(vc.MAGIC) - 1] ^= 0x01
        with pytest.raises(vc.VaultCryptoError):
            vc.decrypt(bytes(blob), key)

    def test_roundtrip_ok_helper(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_bytes(b"prove recoverable before deleting plaintext")
        assert vc.roundtrip_ok(p, _key()) is True


# ── Passphrase change → re-encryption works with new key ──────────────────────

class TestPassphraseChange:
    def test_reencrypt_under_new_passphrase(self):
        old_key = _key("old-pass")
        new_key = _key("new-pass")
        blob = vc.encrypt(b"my data", old_key)
        # Simulate a passphrase change: decrypt with old, re-encrypt with new.
        plain = vc.decrypt(blob, old_key)
        new_blob = vc.encrypt(plain, new_key)
        assert vc.decrypt(new_blob, new_key) == b"my data"
        # Old key can no longer read the re-encrypted blob.
        with pytest.raises(vc.IntegrityError):
            vc.decrypt(new_blob, old_key)


# ── HMAC integrity for decision-BOM entries ───────────────────────────────────

class TestHMACIntegrity:
    def test_sign_and_verify_round_trip(self):
        key = os.urandom(32)
        entry = {"action": "delete", "target": "x", "ts": 123}
        signed = vc.sign_entry(entry, key)
        assert vc.verify_entry(signed, key) is True

    def test_tampered_entry_fails_verification(self):
        key = os.urandom(32)
        signed = vc.sign_entry({"action": "delete", "target": "x"}, key)
        signed["target"] = "y"  # tamper after signing
        assert vc.verify_entry(signed, key) is False

    def test_wrong_key_fails_verification(self):
        signed = vc.sign_entry({"a": 1}, os.urandom(32))
        assert vc.verify_entry(signed, os.urandom(32)) is False

    def test_missing_hmac_field_fails(self):
        assert vc.verify_entry({"a": 1}, os.urandom(32)) is False

    def test_hmac_excludes_own_field(self):
        # Signing is stable regardless of a pre-existing hmac field.
        key = os.urandom(32)
        e1 = vc.sign_entry({"a": 1}, key)
        e2 = vc.sign_entry(dict(e1), key)  # re-sign the already-signed entry
        assert e1["hmac"] == e2["hmac"]

    def test_verify_bom_file(self, tmp_path):
        import json
        key = os.urandom(32)
        good = vc.sign_entry({"a": 1}, key)
        bad = vc.sign_entry({"a": 2}, key)
        bad["a"] = 3  # invalidate
        unsigned = {"a": 4}
        p = tmp_path / "bom.jsonl"
        p.write_text("\n".join(json.dumps(x) for x in (good, bad, unsigned)) + "\n"
                     + "{not json}\n", encoding="utf-8")
        result = vc.verify_bom_file(p, key)
        assert result["valid"] == 1
        assert result["invalid"] == 1
        assert result["unsigned"] == 1
        assert result["bad_lines"] == 1
