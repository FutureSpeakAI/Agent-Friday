"""device_pairing — the paired-device store and device sessions (TV gateway).

Design: ftv/program/specs/tv_desktop_pairing_SLICE_PLAN_2026-11-02.md (D1, D2).

A paired device (today: the Friday TV app) is a CALLER of this desktop, never
the owner. What it may do is decided here and in tv_gateway, structurally:

  * The store is ``<friday home>/security/devices.json``, HMAC-signed with the
    governance key. A file whose signature does not verify reads as "no
    devices, gateway off": a restored or hand-edited store grants nothing.
  * A device is known by the SPKI of a P-256 key that never left it (the
    TV's Android Keystore). There is no shared secret to steal from this PC.
  * A session is opened by signing a single-use challenge with that key. The
    session token is returned once; only its SHA-256 lives here, in memory.
    A restart ends every session; the device signs again.
  * Removing a device ends its sessions at once: every check re-reads the
    device, so a token outliving its device is refused on the next call.

Nothing here can produce ``owner:ui`` or decide a card; the principal of a
device request is ``device:<id>`` (see principal()).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

SESSION_PREFIX = "fdev_"
SESSION_TTL_S = 12 * 3600
CHALLENGE_TTL_S = 60
RECONFIRM_DAYS = 30
# Named rights. A device holds a set of them; "chat_read" always, the others only by a
# press on this PC, one card per right, nothing pre-selected (tv_gateway/pairing).
#   chat_read  chat with Friday and read what she shares (memory, knowledge graph, models)
#   tv_brain   Friday is the TV's brain: TV-catalogue actions come back to the TV's own gate,
#              desktop-side writes stay on this PC
#   studio     a press on the TV may start Studio jobs that spend on this PC's providers
#              (Higgsfield through the desktop connector, kie.ai, Gemini, ...). By the
#              owner's ruling the PC's daily spend stop does not apply to TV-started Studio
#              jobs; each still shows its cost and is receipted.
#   tv_channels  the TV may send a copy of its channels to this PC and fetch the ones kept here
#              (tv_gateway/channels). A channel is data the PC stores and never runs.
RIGHTS = ("chat_read", "tv_brain", "studio", "tv_channels")
BASE_RIGHT = "chat_read"
SESSION_CONTEXT = b"friday-tv-session-v1|"

_LOCK = threading.RLock()
_SESSIONS: dict = {}                         # sha256(token) -> (device_id, expires_at)
_CHALLENGES: dict = {}                       # nonce hex -> expires_at
_RESTORED = False                            # this process has read the persisted sessions once


def _sessions_path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "security" / "device_sessions.json"


def _persist_sessions() -> None:
    """Write the live sessions (hashes only, never a token) so a restart or a deploy does not log a
    paired device out: the phone holds a session for up to SESSION_TTL_S and must not be told it is
    unpaired because this PC restarted. Signed like the device store; best effort."""
    try:
        now = time.time()
        body = {"sessions": {h: [did, exp] for h, (did, exp) in _SESSIONS.items() if exp > now}}
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(_key(), b"friday-device-sessions-v1|" + raw, hashlib.sha256).hexdigest()
        p = _sessions_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"body": body, "sig": sig}, sort_keys=True), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def _restore_sessions() -> None:
    """Once per process: read the persisted sessions back (a bad signature or file restores nothing).
    A device removed since has no record, so check_session still refuses its session."""
    global _RESTORED
    if _RESTORED:
        return
    _RESTORED = True
    try:
        doc = json.loads(_sessions_path().read_text(encoding="utf-8"))
        body, sig = doc["body"], str(doc["sig"])
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        want = hmac.new(_key(), b"friday-device-sessions-v1|" + raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig):
            return
        now = time.time()
        for h, (did, exp) in (body.get("sessions") or {}).items():
            if exp > now:
                _SESSIONS.setdefault(h, (did, exp))
    except Exception:
        pass


def _path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "security" / "devices.json"


def _key() -> bytes:
    from agent_friday.governance.proof_of_integrity import get_governance_key
    return get_governance_key()


def _sign(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_key(), b"friday-devices-v1|" + raw, hashlib.sha256).hexdigest()


def _empty() -> dict:
    return {"gateway": {"enabled": False, "port": 0}, "devices": {}}


def _load() -> dict:
    p = _path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        body, sig = doc["body"], doc["sig"]
        if not isinstance(body, dict) or not hmac.compare_digest(_sign(body), str(sig)):
            return _empty()
        body.setdefault("gateway", {"enabled": False, "port": 0})
        body.setdefault("devices", {})
        return body
    except Exception:
        return _empty()


def _save(body: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"body": body, "sig": _sign(body)}, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


# ── gateway switch (lives here, not in the settings schema) ─────────────────

def gateway_config() -> dict:
    with _LOCK:
        return dict(_load()["gateway"])


def set_gateway(enabled: bool, port: int = 0) -> dict:
    with _LOCK:
        body = _load()
        body["gateway"] = {"enabled": bool(enabled), "port": int(port or body["gateway"].get("port") or 0)}
        _save(body)
        if not enabled:
            _SESSIONS.clear()
            _persist_sessions()
        return dict(body["gateway"])


# ── devices ─────────────────────────────────────────────────────────────────

def spki_id(spki_der: bytes) -> str:
    """A device's id: the first 16 hex of the SHA-256 of its public key."""
    return hashlib.sha256(spki_der).hexdigest()[:16]


def _load_pub(spki_der: bytes):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    pub = load_der_public_key(spki_der)
    if not isinstance(pub, ec.EllipticCurvePublicKey) or pub.curve.name != "secp256r1":
        raise ValueError("device key must be P-256")
    return pub


def _rights(rights) -> list:
    names = [rights] if isinstance(rights, str) else list(rights or ())
    if any(r not in RIGHTS for r in names):
        raise ValueError("unknown right")
    return sorted(set(names) | {BASE_RIGHT})


def add_device(spki_der: bytes, *, name: str, rights=BASE_RIGHT, kind: str = "tv") -> dict:
    """Record a device. Only the pairing flow calls this, after a desk press (D2)."""
    rights = _rights(rights)
    _load_pub(spki_der)
    now = time.time()
    dev = {
        "id": spki_id(spki_der),
        "kind": kind,
        "name": str(name)[:60],
        "spki": base64.b64encode(spki_der).decode("ascii"),
        "rights": rights,
        "paired_at": now,
        "last_seen": None,
        "reconfirm_by": now + RECONFIRM_DAYS * 86400,
    }
    with _LOCK:
        body = _load()
        body["devices"][dev["id"]] = dev
        _save(body)
    return public_view(dev)


def public_view(dev: dict) -> dict:
    return {k: dev.get(k) for k in ("id", "kind", "name", "rights", "paired_at", "last_seen", "reconfirm_by")}


def has_right(dev: Optional[dict], right: str) -> bool:
    return bool(dev) and right in (dev.get("rights") or ())


def set_right(device_id: str, right: str, granted: bool) -> Optional[dict]:
    """Grant or withdraw one named right. Granting is only ever called from a
    screen-only card's decision hook (tv_gateway/pairing); withdrawing may come
    from anywhere the owner asks, because narrowing never needs a press."""
    if right not in RIGHTS or right == BASE_RIGHT:
        raise ValueError("not a grantable right")
    with _LOCK:
        body = _load()
        dev = body["devices"].get(str(device_id))
        if dev is None:
            return None
        have = set(dev.get("rights") or ()) | {BASE_RIGHT}
        (have.add if granted else have.discard)(right)
        dev["rights"] = sorted(have)
        _save(body)
    _receipt("device_right_granted" if granted else "device_right_withdrawn", dev, right=right)
    return public_view(dev)


def list_devices() -> list:
    with _LOCK:
        return [public_view(d) for d in _load()["devices"].values()]


def get_device(device_id: str) -> Optional[dict]:
    with _LOCK:
        return _load()["devices"].get(str(device_id))


def remove_device(device_id: str) -> bool:
    with _LOCK:
        body = _load()
        dev = body["devices"].pop(str(device_id), None)
        gone = dev is not None
        if gone:
            _save(body)
            _receipt("device_removed", dev)
        for h, (did, _exp) in list(_SESSIONS.items()):
            if did == device_id:
                _SESSIONS.pop(h, None)
        _persist_sessions()
        return gone


def stop_all() -> int:
    with _LOCK:
        body = _load()
        n = len(body["devices"])
        body["devices"] = {}
        _save(body)
        _SESSIONS.clear()
        _persist_sessions()
        return n


# ── sessions ────────────────────────────────────────────────────────────────

def new_challenge() -> str:
    nonce = secrets.token_hex(32)
    now = time.time()
    with _LOCK:
        for n, exp in list(_CHALLENGES.items()):
            if exp < now:
                _CHALLENGES.pop(n, None)
        if len(_CHALLENGES) > 256:          # a flood never grows memory without bound
            _CHALLENGES.clear()
        _CHALLENGES[nonce] = now + CHALLENGE_TTL_S
    return nonce


def session_message(nonce: str, device_id: str) -> bytes:
    return SESSION_CONTEXT + nonce.encode("ascii") + b"|" + device_id.encode("ascii")


def open_session(device_id: str, nonce: str, signature_der: bytes) -> Optional[str]:
    """Return a session token if the device signed this live challenge, else None."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    with _LOCK:
        exp = _CHALLENGES.pop(str(nonce), None)    # single use, whatever happens next
    if exp is None or exp < time.time():
        return None
    dev = get_device(device_id)
    if dev is None:
        return None
    try:
        pub = _load_pub(base64.b64decode(dev["spki"]))
        pub.verify(signature_der, session_message(nonce, dev["id"]), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return None
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    with _LOCK:
        _restore_sessions()
        _SESSIONS[hashlib.sha256(token.encode()).hexdigest()] = (dev["id"], time.time() + SESSION_TTL_S)
        _persist_sessions()
    _touch(dev["id"])
    _receipt("device_session_opened", dev)
    return token


def _receipt(kind: str, dev: dict, **extra) -> None:
    """A best-effort action-ledger row; the principal is the device, never the owner."""
    try:
        from agent_friday.services import action_ledger
        action_ledger.append(kind, {"principal": principal(dev), "device_kind": dev.get("kind"),
                                    "surface": "tv_gateway", **extra})
    except Exception:
        pass


def check_session(token: str) -> Optional[dict]:
    """The device behind a live session token, or None. Re-reads the store every time."""
    if not token or not token.startswith(SESSION_PREFIX):
        return None
    h = hashlib.sha256(token.encode()).hexdigest()
    with _LOCK:
        _restore_sessions()
        hit = _SESSIONS.get(h)
        if hit is None:
            return None
        did, exp = hit
        if exp < time.time():
            _SESSIONS.pop(h, None)
            return None
        dev = _load()["devices"].get(did)
        if dev is None:                      # removed: the session dies with it
            _SESSIONS.pop(h, None)
            return None
        return dev


def principal(dev: dict) -> str:
    return "device:" + dev["id"]


def _touch(device_id: str) -> None:
    with _LOCK:
        body = _load()
        dev = body["devices"].get(device_id)
        if dev is not None:
            dev["last_seen"] = time.time()
            _save(body)


def _reset_for_tests() -> None:
    global _RESTORED
    with _LOCK:
        _SESSIONS.clear()
        _CHALLENGES.clear()
        _RESTORED = False
        try:
            _sessions_path().unlink(missing_ok=True)
        except Exception:
            pass
