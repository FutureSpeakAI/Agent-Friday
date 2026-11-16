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
import enum
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

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
# The owner's phone/tablet control rights (Mobile v3, Addendum B). They are NOT part of the pairing bundle above:
# each is its own screen-only press at the PC, never preselected.
PC_RIGHTS = ("pc_read", "pc_approve", "pc_act")
ALL_RIGHTS = RIGHTS + PC_RIGHTS
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
    if any(r not in ALL_RIGHTS for r in names):
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
    out = {k: dev.get(k) for k in ("id", "kind", "name", "rights", "paired_at", "last_seen", "reconfirm_by")}
    out["approval_biometric"] = dev.get("approval_biometric")
    out["approval_attested"] = dev.get("approval_attested")
    return out


def has_right(dev: Optional[dict], right: str) -> bool:
    return bool(dev) and right in (dev.get("rights") or ())


def set_right(device_id: str, right: str, granted: bool) -> Optional[dict]:
    """Grant or withdraw one named right. Granting is only ever called from a
    screen-only card's decision hook (tv_gateway/pairing); withdrawing may come
    from anywhere the owner asks, because narrowing never needs a press."""
    if right not in ALL_RIGHTS or right == BASE_RIGHT:
        raise ValueError("not a grantable right")
    with _LOCK:
        body = _load()
        dev = body["devices"].get(str(device_id))
        if dev is None:
            return None
        have = set(dev.get("rights") or ()) | {BASE_RIGHT}
        (have.add if granted else have.discard)(right)
        dev["rights"] = sorted(have)
        if right == APPROVAL_RIGHT and not granted:
            _clear_approval_key(dev)
        _save(body)
    if right == APPROVAL_RIGHT and not granted:
        expire_pending_key_cards(device_id, "the right was withdrawn")
    _receipt("device_right_granted" if granted else "device_right_withdrawn", dev, right=right)
    return public_view(dev)


# ── the approval key (pc_approve) ───────────────────────────────────────────

APPROVAL_RIGHT = "pc_approve"
RIGHT_CARD_KIND = "device_right"      # the kind of the screen-only card that grants a wider right (tv_gateway/pairing)
APPROVAL_FIELDS = ("approval_spki", "approval_biometric", "approval_attested", "approval_fail_count")


class KeyRefusal(enum.Enum):
    BAD_KEY = "bad_key"
    UNKNOWN_DEVICE = "unknown_device"
    SESSION_KEY_REUSED = "session_key_reused"
    TABLET_NEEDS_BIOMETRIC = "tablet_needs_biometric"


class ApprovalKeyRefused(ValueError):
    """The PC will not take this approval key; ``code`` is the plain reason the route sends back."""

    def __init__(self, refusal: KeyRefusal):
        super().__init__(refusal.value)
        self.code = refusal.value


class KeyStage(enum.Enum):
    STAGED = "staged"                  # waiting for the owner's press at the PC
    ALREADY_ACTIVE = "already_active"


@dataclass(frozen=True)
class ApprovalKey:
    """A device's active approval key: P-256 SPKI (base64) and whether the owner's fingerprint is requested on
    every use. ``attested`` stays False until the hardware proof is checked, so no screen may say "enforced"."""
    spki: str
    biometric: bool
    attested: bool

    @property
    def key_id(self) -> str:
        return spki_id(base64.b64decode(self.spki))


def canonical_spki(spki_der: bytes) -> bytes:
    """The one DER form of a P-256 public key (uncompressed point), whatever encoding it arrived in, so the same
    key always has the same bytes and the same id. Raises ValueError for anything that is not a P-256 key."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return _load_pub(spki_der).public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def approval_key(dev: Optional[dict]) -> Optional[ApprovalKey]:
    """The active approval key, or None while the owner has not pressed its card."""
    if not dev or not dev.get("approval_spki"):
        return None
    return ApprovalKey(dev["approval_spki"], bool(dev.get("approval_biometric")), bool(dev.get("approval_attested")))


def _clear_approval_key(dev: dict) -> None:
    for field in APPROVAL_FIELDS + ("approval_pending",):
        dev.pop(field, None)


def stage_approval_key(device_id: str, spki_der: bytes, biometric: bool) -> Tuple[KeyStage, str]:
    """Hold a device's approval key until the owner presses its card at the PC (``activate_approval_key``).
    Returns what happened and the key's id. Refused: a key that is not P-256, the session key itself in any
    encoding (a signature from it must never count as an approval), and a tablet key that does not ask for the
    fingerprint. Guarded by tests/unit/test_tv_pc_approve_key.py."""
    try:
        canonical = canonical_spki(spki_der)
    except Exception:
        raise ApprovalKeyRefused(KeyRefusal.BAD_KEY)
    with _LOCK:
        body = _load()
        dev = body["devices"].get(str(device_id))
        if dev is None:
            raise ApprovalKeyRefused(KeyRefusal.UNKNOWN_DEVICE)
        if canonical_spki(base64.b64decode(dev["spki"])) == canonical:
            raise ApprovalKeyRefused(KeyRefusal.SESSION_KEY_REUSED)
        if dev.get("kind") == "tablet" and not biometric:
            raise ApprovalKeyRefused(KeyRefusal.TABLET_NEEDS_BIOMETRIC)
        key_id = spki_id(canonical)
        active = approval_key(dev)
        if active and active.key_id == key_id and active.biometric == bool(biometric):
            dev.pop("approval_pending", None)
            _save(body)
            return KeyStage.ALREADY_ACTIVE, key_id
        dev["approval_pending"] = {"spki": base64.b64encode(canonical).decode("ascii"), "biometric": bool(biometric)}
        _save(body)
    return KeyStage.STAGED, key_id


def can_approve(dev: Optional[dict]) -> bool:
    """A device approves only with the right AND an active key: a right without a key never approves.
    Guarded by test_a_right_without_a_key_cannot_approve."""
    return bool(dev) and has_right(dev, APPROVAL_RIGHT) and approval_key(dev) is not None


def clear_pending_key(device_id: str) -> None:
    """Drop a staged key that has no card to press. Guarded by test_a_registered_key_always_has_a_card_on_the_screen."""
    with _LOCK:
        body = _load()
        dev = body["devices"].get(str(device_id))
        if dev is not None and dev.pop("approval_pending", None) is not None:
            _save(body)


def expire_pending_key_cards(device_id: str, note: str) -> int:
    """Take the device's unpressed pc_approve key cards off the screen (never approves one). Guarded by
    test_a_new_key_replaces_the_pending_card, test_the_active_key_registered_again_takes_a_pending_card_away and
    test_withdrawing_the_right_takes_its_pending_card_away."""
    from agent_friday.services import approvals

    def key_card(rec: dict) -> bool:
        payload = rec.get("payload") or {}
        return (rec.get("kind") == RIGHT_CARD_KIND and payload.get("device_id") == str(device_id)
                and payload.get("right") == APPROVAL_RIGHT and "key_id" in payload)
    return approvals.expire_matching(key_card, "tv_gateway", note)


def pending_approval_key(device_id: str) -> Optional[ApprovalKey]:
    dev = get_device(device_id) or {}
    pend = dev.get("approval_pending")
    return ApprovalKey(pend["spki"], bool(pend["biometric"]), False) if pend else None


def activate_approval_key(device_id: str, key_id: str, biometric: bool) -> bool:
    """Make the staged key active, only if it is still the key AND the fingerprint choice the owner's card named.
    The caller is the card's decision hook (pairing._on_right_decision); guarded by
    tests/unit/test_tv_pc_approve_key.py, which also pins who calls it."""
    with _LOCK:
        body = _load()
        dev = body["devices"].get(str(device_id))
        pend = (dev or {}).get("approval_pending")
        if (not pend or spki_id(base64.b64decode(pend["spki"])) != key_id
                or bool(pend["biometric"]) != bool(biometric)):
            return False
        dev.update(approval_spki=pend["spki"], approval_biometric=pend["biometric"], approval_attested=False,
                   approval_fail_count=0)
        dev.pop("approval_pending")
        _save(body)
    return True


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
    if gone:
        expire_pending_key_cards(device_id, "the device was removed")
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
