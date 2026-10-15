"""Verifying minisign signatures (Ed25519), the format Friday's signed files use.

Release manifests and the public lists FutureSpeak maintains are signed with
the standard ``minisign`` tool on an offline machine, so the private key never
sits on a server or in CI. This module only verifies; it never signs.

The format (https://jedisct1.github.io/minisign/):

* public key: an "untrusted comment" line, then base64 of
  ``"Ed"`` (2 bytes) + key id (8 bytes) + Ed25519 public key (32 bytes);
* signature: an "untrusted comment" line; base64 of
  algorithm (``"Ed"`` signs the file itself, ``"ED"`` signs its BLAKE2b-512
  hash) + key id (8) + signature (64); a ``trusted comment: ...`` line; and
  base64 of a second signature over the first signature followed by the
  trusted comment, which is what makes the comment trustworthy.

Friday puts its metadata in the trusted comment as ``key=value`` pairs
(``name=``, ``version=``, ``expires=``), so a signed file cannot be replayed
under another name, rolled back to an older version, or kept alive past its
expiry without the signature failing.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re

UNTRUSTED = "untrusted comment:"
TRUSTED = "trusted comment:"


class SignatureError(ValueError):
    """The signature does not verify, or the files are not in minisign format.
    The message is plain words for the person."""


def _b64(text: str, what: str) -> bytes:
    try:
        return base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        raise SignatureError("the %s is not readable" % what) from e


def parse_public_key(text: str) -> tuple[bytes, bytes]:
    """(key id, 32-byte public key) from a minisign public key, with or without
    its comment line."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    body = next((ln for ln in lines if not ln.lower().startswith(UNTRUSTED)), "")
    raw = _b64(body, "public key")
    if len(raw) != 42 or raw[:2] != b"Ed":
        raise SignatureError("the public key is not an Ed25519 minisign key")
    return raw[2:10], raw[10:]


def key_id_hex(public_key_text: str) -> str:
    kid, _pk = parse_public_key(public_key_text)
    return kid[::-1].hex().upper()


def parse_signature(text: str) -> dict:
    lines = [ln.rstrip("\r") for ln in str(text or "").splitlines()]
    lines = [ln for ln in lines if ln.strip()]
    if len(lines) < 4 or not lines[0].lower().startswith(UNTRUSTED) \
            or not lines[2].lower().startswith(TRUSTED):
        raise SignatureError("the signature file is not in minisign format")
    sig = _b64(lines[1], "signature")
    if len(sig) != 74 or sig[:2] not in (b"Ed", b"ED"):
        raise SignatureError("the signature is not an Ed25519 minisign signature")
    comment = lines[2][len(TRUSTED):].lstrip(" ")
    gsig = _b64(lines[3], "signature")
    if len(gsig) != 64:
        raise SignatureError("the signature's trusted comment is not signed")
    return {"alg": sig[:2], "key_id": sig[2:10], "signature": sig[10:],
            "trusted_comment": comment, "global_signature": gsig}


def _ed25519_verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except InvalidSignature:
        return False


def verify(data: bytes, signature_text: str, public_keys) -> dict:
    """Verify `data` against a minisign signature made by one of `public_keys`
    (minisign public key texts). Returns {"key_id", "trusted_comment",
    "meta"} on success; raises SignatureError otherwise.

    An empty `public_keys` refuses: a file is never accepted because nobody
    was asked whose signature it should carry.
    """
    keys = {}
    for k in public_keys or ():
        kid, pk = parse_public_key(k)
        keys[kid] = pk
    if not keys:
        raise SignatureError("no trusted signing key is configured yet")
    sig = parse_signature(signature_text)
    pk = keys.get(sig["key_id"])
    if pk is None:
        raise SignatureError("it was signed with a key Friday does not trust")
    message = hashlib.blake2b(data, digest_size=64).digest() if sig["alg"] == b"ED" else data
    if not _ed25519_verify(pk, sig["signature"], message):
        raise SignatureError("the signature does not match the file")
    if not _ed25519_verify(pk, sig["global_signature"],
                           sig["signature"] + sig["trusted_comment"].encode("utf-8")):
        raise SignatureError("the signature's trusted comment was changed")
    return {"key_id": sig["key_id"][::-1].hex().upper(),
            "trusted_comment": sig["trusted_comment"],
            "meta": comment_meta(sig["trusted_comment"])}


_PAIR = re.compile(r"([a-z_]+)=(\S+)")


def comment_meta(comment: str) -> dict:
    """``name=x version=3 expires=2026-12-31`` -> {"name": "x", ...}."""
    return {k: v for k, v in _PAIR.findall(str(comment or ""))}
