"""vault_crypto — real encryption-at-rest primitives for the Sovereign Vault.

This is the Python-side counterpart to the (currently unused) JS SovereignVault
in asimovs-mind/mcp/friday-core. The live app stores its private data under
~/.friday/vault and ~/wiki as PLAINTEXT; this module provides the crypto needed
to actually protect it at rest, matching the documented design:

    AES-256-GCM  +  Argon2id key derivation  +  HMAC-SHA256 integrity

Design notes
------------
* Master-key model: one 32-byte key is derived from a passphrase + the vault's
  master salt (stored in ~/.friday/vault/.vault_config.json). The same key
  encrypts every file; per-file confidentiality comes from a fresh random
  96-bit nonce stored in each blob. This mirrors typical password-vault design
  and the JS implementation's deriveMasterKey/AES-GCM split.
* Self-describing container: every ciphertext blob starts with a magic+version
  header which is also fed to AES-GCM as additional authenticated data (AAD),
  so the version cannot be stripped or downgraded without failing the tag.
* HMAC helpers replicate the EXACT scheme already used for the decision-BOM in
  server.py (hmac-sha256 over json.dumps(entry, sort_keys=True) with the 'hmac'
  field excluded), so verify_bom_line can validate existing audit entries.

Only depends on `cryptography` (>=44 for native Argon2id) and the stdlib.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

# ── Container format ────────────────────────────────────────────────────────
# 13-byte magic/version header. Bumping the trailing byte invalidates old blobs
# only if you also change the AAD, which is intentional: version is integrity-
# protected because it is fed to AES-GCM as AAD.
MAGIC = b"FRIDAYVAULT\x01"        # 12 bytes + 1 version byte
NONCE_LEN = 12                     # 96-bit nonce, the AES-GCM standard
KEY_LEN = 32                       # AES-256


# ── Argon2id profiles ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class Argon2Profile:
    """Argon2id cost parameters. memory_cost is in KiB."""
    time_cost: int      # iterations
    memory_cost: int    # KiB
    lanes: int

    def __post_init__(self):
        # cryptography requires memory_cost >= 8 * lanes
        if self.memory_cost < 8 * self.lanes:
            raise ValueError("memory_cost must be >= 8 * lanes")


# Strong default ~= the JS vault's libsodium settings (opslimit=4, memlimit=256MB).
# 256 MiB / 4 passes is deliberately heavy — this runs once per unlock, not per
# request. Raise, never lower, in production.
DEFAULT_PROFILE = Argon2Profile(time_cost=4, memory_cost=262_144, lanes=4)

# Fast profile for unit tests ONLY — never use to protect real data.
FAST_PROFILE = Argon2Profile(time_cost=1, memory_cost=64, lanes=1)


class VaultCryptoError(Exception):
    """Base error for vault crypto operations."""


class IntegrityError(VaultCryptoError):
    """Raised when authentication/HMAC verification fails (tamper or wrong key)."""


# ── Key derivation ──────────────────────────────────────────────────────────
def derive_key(passphrase: str | bytes, salt: bytes,
               profile: Argon2Profile = DEFAULT_PROFILE) -> bytes:
    """Derive a 32-byte AES-256 key from a passphrase + salt via Argon2id.

    Deterministic for a given (passphrase, salt, profile) — that is what lets
    the same key decrypt previously-written blobs. Losing the passphrase means
    losing the data: there is no recovery path by design.
    """
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")
    if not passphrase:
        raise VaultCryptoError("refusing to derive a key from an empty passphrase")
    if len(salt) < 16:
        raise VaultCryptoError("salt must be at least 16 bytes")
    kdf = Argon2id(
        salt=salt,
        length=KEY_LEN,
        iterations=profile.time_cost,
        lanes=profile.lanes,
        memory_cost=profile.memory_cost,
    )
    return kdf.derive(passphrase)


def load_salt(vault_config_path: Optional[Path] = None) -> bytes:
    """Load the vault master salt from .vault_config.json (salt_hex)."""
    if vault_config_path is None:
        vault_config_path = Path.home() / ".friday" / "vault" / ".vault_config.json"
    cfg = json.loads(Path(vault_config_path).read_text(encoding="utf-8"))
    salt_hex = cfg.get("salt_hex")
    if not salt_hex:
        raise VaultCryptoError(f"no salt_hex in {vault_config_path}")
    return bytes.fromhex(salt_hex)


# ── AES-256-GCM encrypt / decrypt ───────────────────────────────────────────
def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt plaintext -> self-describing blob: MAGIC || nonce || ct+tag.

    The MAGIC header is authenticated as AAD so the version/format cannot be
    altered without invalidating the GCM tag.
    """
    if len(key) != KEY_LEN:
        raise VaultCryptoError(f"key must be {KEY_LEN} bytes")
    nonce = os.urandom(NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    return MAGIC + nonce + ct


def decrypt(blob: bytes, key: bytes) -> bytes:
    """Inverse of encrypt(). Raises IntegrityError on tamper or wrong key."""
    if len(key) != KEY_LEN:
        raise VaultCryptoError(f"key must be {KEY_LEN} bytes")
    if not blob.startswith(MAGIC):
        raise VaultCryptoError("not a FRIDAYVAULT blob (bad magic) — already plaintext?")
    nonce = blob[len(MAGIC):len(MAGIC) + NONCE_LEN]
    ct = blob[len(MAGIC) + NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, MAGIC)
    except InvalidTag as e:
        raise IntegrityError("GCM auth tag mismatch — tampered ciphertext or wrong key") from e


def is_encrypted(blob: bytes) -> bool:
    """True if the bytes look like a vault blob (magic prefix present)."""
    return blob[:len(MAGIC)] == MAGIC


# ── File helpers ────────────────────────────────────────────────────────────
def encrypt_file(src: Path, dst: Path, key: bytes) -> None:
    dst.write_bytes(encrypt(Path(src).read_bytes(), key))


def decrypt_file(src: Path, dst: Path, key: bytes) -> None:
    dst.write_bytes(decrypt(Path(src).read_bytes(), key))


def roundtrip_ok(src: Path, key: bytes) -> bool:
    """Encrypt src in-memory then decrypt and confirm it equals the original.

    Used by the migration tool to PROVE a file can be recovered before any
    plaintext is ever removed.
    """
    original = Path(src).read_bytes()
    return decrypt(encrypt(original, key), key) == original


# ── HMAC integrity (matches server.py decision-BOM scheme exactly) ──────────
def _canonical_hmac(entry: dict, key: bytes) -> str:
    """Compute the hex HMAC-SHA256 over an entry with its 'hmac' field excluded.

    Mirrors server.py: json.dumps(entry, sort_keys=True) of the entry WITHOUT
    the hmac field, keyed by the 32-byte governance key.
    """
    payload = {k: v for k, v in entry.items() if k != "hmac"}
    canonical = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def sign_entry(entry: dict, key: bytes) -> dict:
    """Return a copy of entry with an 'hmac' field added."""
    out = dict(entry)
    out.pop("hmac", None)
    out["hmac"] = _canonical_hmac(out, key)
    return out


def verify_entry(entry: dict, key: bytes) -> bool:
    """Constant-time verification of an entry's 'hmac' field."""
    claimed = entry.get("hmac")
    if not isinstance(claimed, str):
        return False
    expected = _canonical_hmac(entry, key)
    return hmac.compare_digest(claimed, expected)


def load_governance_key(vault_dir: Optional[Path] = None) -> bytes:
    """Load the 32-byte governance/HMAC key from ~/.friday/vault/.governance-key."""
    if vault_dir is None:
        vault_dir = Path.home() / ".friday" / "vault"
    return (Path(vault_dir) / ".governance-key").read_bytes()


def verify_bom_file(path: Path, key: bytes) -> dict[str, Any]:
    """Verify every HMAC in a decision-BOM JSONL file.

    Returns {total, valid, invalid, unsigned, bad_lines}. Read-only.
    """
    total = valid = invalid = unsigned = bad_lines = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if "hmac" not in entry:
            unsigned += 1
        elif verify_entry(entry, key):
            valid += 1
        else:
            invalid += 1
    return {"total": total, "valid": valid, "invalid": invalid,
            "unsigned": unsigned, "bad_lines": bad_lines}
