"""The check a signed approval from an owner's phone or tablet must pass (approvals-from-the-phone brief, 3.3-3.4).

Pure rules and in-memory nonces; the routes are in pc_approve.py. Each rule is held by a test in
tests/unit/test_tv_pc_approve_decide.py, named at the rule.

The order is the brief's, and it matters: the nonce is spent BEFORE the signature is looked at, so a replayed or
guessed message never gets a second try; the card is re-read AFTER the signature, so a card edited between the
challenge and the decision is refused as changed (not counted against the device) rather than approved.
"""
from __future__ import annotations

import base64
import enum
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

SIGN_PREFIX = b"friday-pc-approval-v1|"
NONCE_TTL_S = 60
MAX_LIVE_NONCES = 5
TIME_WINDOW_S = 120


class Refusal(enum.Enum):
    NOT_APPROVED = "not_approved"
    NOT_FOR_DEVICE = "pc_approve_not_for_this_device"
    NEEDS_RIGHT = "needs_right"
    BAD_REQUEST = "bad_request"
    UNKNOWN_CARD = "unknown_card"
    ALREADY_DECIDED = "already_decided"
    CARD_NOT_FOR_DEVICES = "card_not_for_devices"
    CARD_CHANGED = "card_changed"
    BAD_NONCE = "bad_nonce"
    BAD_TIME = "bad_time"
    BAD_SIGNATURE = "bad_signature"
    TOO_MANY_CHALLENGES = "too_many_challenges"


HTTP_STATUS = {Refusal.NOT_FOR_DEVICE: 403, Refusal.NEEDS_RIGHT: 403, Refusal.BAD_REQUEST: 400,
               Refusal.UNKNOWN_CARD: 404, Refusal.ALREADY_DECIDED: 200, Refusal.CARD_NOT_FOR_DEVICES: 422,
               Refusal.CARD_CHANGED: 409, Refusal.NOT_APPROVED: 409, Refusal.BAD_NONCE: 403, Refusal.BAD_TIME: 403,
               Refusal.BAD_SIGNATURE: 403, Refusal.TOO_MANY_CHALLENGES: 429}


class Rejected(Exception):
    def __init__(self, refusal: Refusal, why: str = ""):
        super().__init__(refusal.value)
        self.refusal = refusal
        self.why = why


@dataclass(frozen=True)
class Challenge:
    nonce: str
    device_id: str
    approval_id: str
    action_fp: str
    expires_at: float


@dataclass(frozen=True)
class SignedApproval:
    approval_id: str
    action_fp: str
    nonce: str
    signed_time: int
    signature_b64: str


_LIVE: Dict[str, List[Challenge]] = {}
_LOCK = threading.Lock()


def reset_for_tests() -> None:
    with _LOCK:
        _LIVE.clear()


def signing_message(approval_id: str, action_fp: str, nonce: str, device_id: str, signed_time: int) -> bytes:
    fields = [approval_id, "approve", action_fp, nonce, device_id, str(signed_time)]
    return SIGN_PREFIX + "|".join(fields).encode("utf-8")


def mint(device_id: str, approval_id: str, action_fp: str, now: Optional[float] = None) -> Challenge:
    now = time.time() if now is None else now
    with _LOCK:
        live = [c for c in _LIVE.get(device_id, []) if c.expires_at > now]
        if len(live) >= MAX_LIVE_NONCES:
            _LIVE[device_id] = live
            raise Rejected(Refusal.TOO_MANY_CHALLENGES)
        challenge = Challenge(secrets.token_hex(32), device_id, approval_id, action_fp, now + NONCE_TTL_S)
        _LIVE[device_id] = live + [challenge]
        return challenge


def consume(device_id: str, approval_id: str, nonce: str, now: Optional[float] = None) -> Challenge:
    """Spend the nonce for this device and this card. Single use: gone whether or not the rest passes."""
    now = time.time() if now is None else now
    with _LOCK:
        live = _LIVE.get(device_id, [])
        found = next((c for c in live if c.nonce == nonce and c.approval_id == approval_id), None)
        _LIVE[device_id] = [c for c in live if c is not found and c.expires_at > now]
    if found is None or found.expires_at <= now:
        raise Rejected(Refusal.BAD_NONCE)
    return found


def signature_holds(approval_spki_b64: str, message: bytes, signature_b64: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from agent_friday.services import device_pairing
    try:
        public_key = device_pairing._load_pub(base64.b64decode(approval_spki_b64))
        public_key.verify(base64.b64decode(signature_b64, validate=True), message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def device_label(device_id: str) -> str:
    """The decided_by of a decision made on this device (approvals.DEVICE_LABEL + its id)."""
    from agent_friday.services import approvals
    return approvals.DEVICE_LABEL + str(device_id)


def device_may_not_decide(rec: dict, device_id: str) -> Optional[str]:
    """Why this card is never approved from a device, in words for the phone; None when it may be. A device
    never grants a right (device_right and pairing cards: test_a_device_right_card_is_never_approved_from_a_device,
    test_a_pairing_card_is_never_approved_from_a_device); the rest follows approvals.refusal_for and the
    screen-only kinds."""
    from agent_friday.services import approvals
    from agent_friday.tv_gateway import pairing
    if rec.get("kind") in (pairing.RIGHT_KIND, pairing.KIND):
        return "A device cannot give rights to a device. Approve this one on your PC."
    label = device_label(device_id)
    screen_only = approvals.SCREEN_ONLY_KINDS.get(rec.get("kind"))
    if screen_only is not None and label not in screen_only and rec.get("kind") not in approvals.DEVICE_SIGNED_KINDS:
        return "This card is approved only with a press on your PC's screen."
    return approvals.refusal_for(str(rec.get("approval_id")), "approve", label) or None
