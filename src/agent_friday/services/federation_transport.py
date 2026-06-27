"""
Agent Friday — Federation Transport (Layer 3)
FutureSpeak.AI · Asimov's Mind

Encrypted inter-agent messaging using X25519 ECDH + ChaCha20-Poly1305 AEAD.
Implements a Noise-XX-inspired handshake with forward secrecy.

Message envelope (JSON, outer layer):
  {
    "type": "federation_message",
    "version": "1.0",
    "sender_pubkey": "<ed25519 hex>",
    "recipient_pubkey": "<ed25519 hex>",
    "session_key_hint": "<x25519 ephem pubkey hex>",
    "timestamp": "...",
    "nonce": "<32 hex bytes>",
    "encrypted_payload": "<hex>",
    "signature": "<ed25519 sig over all above except signature>"
  }

Message types (inner payload):
  HANDSHAKE, CONTENT_OFFER, CONTENT_REQUEST, CONTENT_TRANSFER,
  LICENSE_QUERY, TRUST_ATTESTATION, HEARTBEAT

Rate limiting: per-peer sliding window (default 100 msg/min)
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import agent_friday.core as core

# ── optional crypto deps ──────────────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

MSG_TYPES: Dict[str, str] = {
    "HANDSHAKE":          "handshake",
    "CONTENT_OFFER":      "content_offer",
    "CONTENT_REQUEST":    "content_request",
    "CONTENT_TRANSFER":   "content_transfer",
    "LICENSE_QUERY":      "license_query",
    "TRUST_ATTESTATION":  "trust_attestation",
    "HEARTBEAT":          "heartbeat",
}

RATE_LIMIT_PER_MIN: int = 100

# ─────────────────────────────────────────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────────────────────────────────────────

_sessions: Dict[str, Dict[str, Any]] = {}
_rate_limiters: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.RLock()


# ─────────────────────────────────────────────────────────────────────────────
#  INTEGRITY ENGINE (lazy)
# ─────────────────────────────────────────────────────────────────────────────

def _engine():
    try:
        from agent_friday.governance.proof_of_integrity import get_integrity_engine
        return get_integrity_engine()
    except Exception:
        return None


def _our_pubkey_hex() -> str:
    eng = _engine()
    if eng:
        return eng.get_public_key_hex() or ""
    return ""


def _sign_bytes(data: bytes) -> str:
    eng = _engine()
    if eng:
        return eng.sign_payload(data) or ""
    return ""


def _ed25519_seed_bytes() -> Optional[bytes]:
    """Return raw 32-byte Ed25519 seed, or None."""
    try:
        eng = _engine()
        if eng and eng._signing_key is not None:
            return bytes(eng._signing_key)[:32]
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  KEY CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def _ed25519_to_x25519_bytes(ed25519_seed_bytes: bytes) -> bytes:
    """Convert Ed25519 seed to X25519 scalar per RFC 7748 / IETF procedure.

    SHA-512 of seed, clamp bits 0-2 of byte 0 and bits 6-7 of byte 31.
    Returns raw 32-byte X25519 private scalar.
    """
    import hashlib
    h = hashlib.sha512(ed25519_seed_bytes).digest()
    scalar = bytearray(h[:32])
    scalar[0] &= 248   # clear bits 0,1,2
    scalar[31] &= 127  # clear bit 255
    scalar[31] |= 64   # set bit 254
    return bytes(scalar)


# ─────────────────────────────────────────────────────────────────────────────
#  HKDF HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _hkdf_derive(ikm: bytes, info: bytes = b"friday-federation-v1", length: int = 32) -> bytes:
    """HKDF-SHA256 key derivation."""
    hkdf = HKDF(algorithm=SHA256(), length=length, salt=None, info=info)
    return hkdf.derive(ikm)


# ─────────────────────────────────────────────────────────────────────────────
#  SESSION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def create_session(peer_pubkey_hex: str) -> Dict[str, Any]:
    """Generate an ephemeral X25519 key and store initial session state."""
    if not _HAS_CRYPTO:
        return {"ephem_pubkey_hex": "", "session_id": "no-crypto"}
    try:
        ephem_key = X25519PrivateKey.generate()
        ephem_pub_bytes = ephem_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ephem_pub_hex = ephem_pub_bytes.hex()
        import uuid
        session_id = str(uuid.uuid4())
        with _LOCK:
            _sessions[peer_pubkey_hex] = {
                "ephem_private_key": ephem_key,
                "ephem_pubkey_hex": ephem_pub_hex,
                "shared_secret": None,
                "their_ephem_pubkey": None,
                "message_count": 0,
                "window_start": time.time(),
                "nonce_counter": 0,
                "session_id": session_id,
            }
        return {"ephem_pubkey_hex": ephem_pub_hex, "session_id": session_id}
    except Exception as e:
        print(f"  [fed-transport] create_session failed: {e}")
        return {"ephem_pubkey_hex": "", "session_id": ""}


def complete_handshake(peer_pubkey_hex: str, their_ephem_pubkey_hex: str) -> str:
    """ECDH with their ephemeral pubkey; derive shared_secret via HKDF-SHA256."""
    if not _HAS_CRYPTO:
        return ""
    try:
        with _LOCK:
            sess = _sessions.get(peer_pubkey_hex)
        if not sess or sess.get("ephem_private_key") is None:
            # Auto-create session if none exists
            create_session(peer_pubkey_hex)
            with _LOCK:
                sess = _sessions[peer_pubkey_hex]

        their_pub_bytes = bytes.fromhex(their_ephem_pubkey_hex)
        their_pub = X25519PublicKey.from_public_bytes(their_pub_bytes)
        shared_raw = sess["ephem_private_key"].exchange(their_pub)
        shared_secret = _hkdf_derive(shared_raw)  # pragma: allowlist secret

        with _LOCK:
            _sessions[peer_pubkey_hex]["shared_secret"] = shared_secret
            _sessions[peer_pubkey_hex]["their_ephem_pubkey"] = their_ephem_pubkey_hex

        return sess["ephem_pubkey_hex"]
    except Exception as e:
        print(f"  [fed-transport] complete_handshake failed: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  KEY DERIVATION (no session)
# ─────────────────────────────────────────────────────────────────────────────

def _derive_key_from_static(recipient_pubkey_hex: str) -> Optional[bytes]:
    """One-shot key from HKDF(sha256(our_ed25519_seed + their_pubkey)).

    No forward secrecy but still encrypted. Used when no session is established.
    """
    if not _HAS_CRYPTO:
        return None
    try:
        import hashlib
        seed = _ed25519_seed_bytes()
        if not seed:
            return None
        their_bytes = bytes.fromhex(recipient_pubkey_hex)
        ikm = hashlib.sha256(seed + their_bytes).digest()
        return _hkdf_derive(ikm, info=b"friday-federation-static-v1")
    except Exception as e:
        print(f"  [fed-transport] _derive_key_from_static failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  ENCRYPT / DECRYPT
# ─────────────────────────────────────────────────────────────────────────────

def encrypt_message(
    msg_type: str,
    payload_dict: Dict[str, Any],
    recipient_pubkey_hex: str,
    session_key: Optional[bytes] = None,
) -> Dict[str, Any]:
    """Build an encrypted, signed federation envelope."""
    sender_pub = _our_pubkey_hex()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce_bytes = os.urandom(12)
    nonce_hex = os.urandom(16).hex()  # 32 hex chars for envelope nonce field

    # Determine encryption key and session_key_hint
    ephem_pub_hex = ""
    with _LOCK:
        sess = _sessions.get(recipient_pubkey_hex)

    if session_key is not None:
        key = session_key
    elif sess and sess.get("shared_secret"):
        key = sess["shared_secret"]
        ephem_pub_hex = sess.get("ephem_pubkey_hex", "")
    else:
        key = _derive_key_from_static(recipient_pubkey_hex)
        if key is None:
            # Plaintext fallback with warning
            print("  [fed-transport] WARNING: no crypto available, sending plaintext")
            import base64
            payload_bytes = json.dumps({"msg_type": msg_type, "payload": payload_dict}).encode()
            envelope = {
                "type": "federation_message",
                "version": "1.0",
                "sender_pubkey": sender_pub,
                "recipient_pubkey": recipient_pubkey_hex,
                "session_key_hint": "",
                "timestamp": ts,
                "nonce": nonce_hex,
                "encrypted_payload": base64.b64encode(payload_bytes).decode(),
                "plaintext_fallback": True,
            }
            envelope["signature"] = ""
            return envelope

    try:
        inner = json.dumps({"msg_type": msg_type, "payload": payload_dict}, sort_keys=True).encode()
        aad_obj = {
            "sender_pubkey": sender_pub,
            "recipient_pubkey": recipient_pubkey_hex,
            "timestamp": ts,
            "nonce": nonce_hex,
        }
        aad = json.dumps(aad_obj, sort_keys=True).encode()
        chacha = ChaCha20Poly1305(key)
        ct = chacha.encrypt(nonce_bytes, inner, aad)
        # Prepend nonce to ciphertext so recipient can decrypt
        payload_hex = (nonce_bytes + ct).hex()

        envelope = {
            "type": "federation_message",
            "version": "1.0",
            "sender_pubkey": sender_pub,
            "recipient_pubkey": recipient_pubkey_hex,
            "session_key_hint": ephem_pub_hex,
            "timestamp": ts,
            "nonce": nonce_hex,
            "encrypted_payload": payload_hex,
        }

        # Sign everything except the signature field
        sig_data = json.dumps(envelope, sort_keys=True).encode()
        envelope["signature"] = _sign_bytes(sig_data)
        return envelope
    except Exception as e:
        print(f"  [fed-transport] encrypt_message failed: {e}")
        return {}


def decrypt_message(envelope_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Verify signature, decrypt payload. Returns {ok, msg_type, payload, sender_pubkey}."""
    try:
        env = dict(envelope_dict)
        sig = env.pop("signature", "")
        sender_pub = env.get("sender_pubkey", "")

        # Reject messages from defederated agents at the transport layer
        if sender_pub:
            try:
                from agent_friday.services.defederation import is_defederated
                if is_defederated(sender_pub):
                    return {
                        "ok": False,
                        "error": "sender_defederated",
                        "sender_pubkey": sender_pub,
                    }
            except Exception:
                pass

        # Signature verification
        if sig and sender_pub:
            sig_data = json.dumps(env, sort_keys=True).encode()
            from agent_friday.governance.proof_of_integrity import IntegrityEngine
            if not IntegrityEngine.verify_payload(sig_data, sig, sender_pub):
                return {"ok": False, "error": "signature_invalid", "sender_pubkey": sender_pub}

        if env.get("plaintext_fallback"):
            import base64
            inner = json.loads(base64.b64decode(env["encrypted_payload"]))
            return {
                "ok": True,
                "msg_type": inner.get("msg_type"),
                "payload": inner.get("payload"),
                "sender_pubkey": sender_pub,
            }

        if not _HAS_CRYPTO:
            return {"ok": False, "error": "crypto_unavailable", "sender_pubkey": sender_pub}

        # Determine decryption key
        recipient_pub = env.get("recipient_pubkey", "")
        session_hint = env.get("session_key_hint", "")
        key = None
        with _LOCK:
            sess = _sessions.get(sender_pub)
        if sess and sess.get("shared_secret"):
            key = sess["shared_secret"]
        elif session_hint:
            # Complete handshake using their hint
            hint_key = complete_handshake(sender_pub, session_hint)
            with _LOCK:
                sess = _sessions.get(sender_pub)
            if sess and sess.get("shared_secret"):
                key = sess["shared_secret"]
        if key is None:
            key = _derive_key_from_static(sender_pub)
        if key is None:
            return {"ok": False, "error": "no_decryption_key", "sender_pubkey": sender_pub}

        ct_with_nonce = bytes.fromhex(env["encrypted_payload"])
        nonce_bytes = ct_with_nonce[:12]
        ct = ct_with_nonce[12:]

        aad_obj = {
            "sender_pubkey": sender_pub,
            "recipient_pubkey": recipient_pub,
            "timestamp": env.get("timestamp", ""),
            "nonce": env.get("nonce", ""),
        }
        aad = json.dumps(aad_obj, sort_keys=True).encode()
        chacha = ChaCha20Poly1305(key)
        inner_bytes = chacha.decrypt(nonce_bytes, ct, aad)
        inner = json.loads(inner_bytes)
        return {
            "ok": True,
            "msg_type": inner.get("msg_type"),
            "payload": inner.get("payload"),
            "sender_pubkey": sender_pub,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "sender_pubkey": envelope_dict.get("sender_pubkey", "")}


# ─────────────────────────────────────────────────────────────────────────────
#  RATE LIMITING
# ─────────────────────────────────────────────────────────────────────────────

def check_rate_limit(peer_pubkey_hex: str, limit: int = RATE_LIMIT_PER_MIN) -> bool:
    """Sliding-window rate check. Returns True if allowed."""
    now = time.time()
    with _LOCK:
        rl = _rate_limiters.setdefault(peer_pubkey_hex, {"count": 0, "window_start": now})
        if now - rl["window_start"] >= 60:
            rl["count"] = 0
            rl["window_start"] = now
        if rl["count"] >= limit:
            return False
        rl["count"] += 1
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC SEND / BUILD
# ─────────────────────────────────────────────────────────────────────────────

def build_message(
    msg_type: str,
    payload_dict: Dict[str, Any],
    recipient_pubkey_hex: str,
) -> Dict[str, Any]:
    """Encrypt and sign a message for a recipient. Alias for encrypt_message."""
    return encrypt_message(msg_type, payload_dict, recipient_pubkey_hex)


def send_to_peer(
    peer_endpoint: str,
    envelope_dict: Dict[str, Any],
    timeout: int = 15,
) -> Dict[str, Any]:
    """HTTP POST the envelope to {peer_endpoint}/api/federation/inbox."""
    try:
        url = peer_endpoint.rstrip("/") + "/api/federation/inbox"
        body = json.dumps(envelope_dict).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"http_{e.code}", "status": e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)}
