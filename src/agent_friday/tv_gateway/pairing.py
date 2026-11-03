"""Pairing a TV (D2): commit, reveal, six digits on both screens, a press on this PC.

The TV connects over TLS and records the certificate it met. Then:

  1. TV -> PC   its device public key (P-256 SPKI, made in its Keystore)
     PC -> TV   commit = H(pin || device key || Nd)      Nd stays secret
  2. TV -> PC   Nt
     PC -> TV   Nd                                       TV checks the commitment
  3. both       SAS = H(pin || device key || Nt || Nd) mod 10^6

"pin" is the SHA-256 of the gateway certificate's SPKI: the PC uses its own,
the TV the one it met. A machine in the middle meets each side with its own
key, so the two screens show different digits; because the PC committed to Nd
before seeing Nt, the middle gets one guess at matching them, not a million.

The PC then raises a ``device_pair`` card naming the six digits. It is a
screen-only kind: only a verified press on this PC's own screen approves it
(approvals.SCREEN_ONLY_KINDS). Voice, chat, the gateway and the TV can decline
it at most. The device is recorded only from the card's decision hook, with
the least rights ("chat_read"); widening is a separate press (D5).

Limits: a pairing lives five minutes, each step happens once, one pairing at
a time, at most three started per hour.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from typing import Optional

from agent_friday.services import device_pairing

KIND = "device_pair"
PAIR_TTL_S = 300
MAX_STARTS_PER_HOUR = 3
SAS_CONTEXT = b"friday-tv-pair-v1|"
COMMIT_CONTEXT = b"friday-tv-pair-commit-v1|"
FIRST_RIGHTS = "chat_read"

_LOCK = threading.RLock()
_PENDING: dict = {}
_STARTS: deque = deque()


class PairingRefused(Exception):
    """A pairing step was refused; the message is safe to show the TV."""


def commitment(pin_hex: str, device_spki: bytes, nd: bytes) -> str:
    return hashlib.sha256(COMMIT_CONTEXT + bytes.fromhex(pin_hex) + device_spki + nd).hexdigest()


def sas(pin_hex: str, device_spki: bytes, nt: bytes, nd: bytes) -> str:
    h = hashlib.sha256(SAS_CONTEXT + bytes.fromhex(pin_hex) + device_spki + nt + nd).digest()
    return "%06d" % (int.from_bytes(h[:8], "big") % 1_000_000)


def spoken(code: str) -> str:
    return code[:3] + " " + code[3:]


def _pin() -> str:
    from agent_friday.tv_gateway import tls
    return tls.spki_sha256()


def _expire(now: float) -> None:
    for pid, p in list(_PENDING.items()):
        if p["state"] in ("committed", "awaiting_press") and p["expires"] < now:
            p["state"] = "expired"
        if p["expires"] + 600 < now:
            _PENDING.pop(pid, None)


def start(device_spki: bytes, name: str) -> dict:
    now = time.time()
    try:
        device_pairing._load_pub(device_spki)
    except Exception:
        raise PairingRefused("the TV's key must be P-256")
    if device_pairing.get_device(device_pairing.spki_id(device_spki)) is not None:
        raise PairingRefused("this TV is already paired; remove it on the PC to pair again")
    with _LOCK:
        _expire(now)
        while _STARTS and now - _STARTS[0] > 3600:
            _STARTS.popleft()
        if len(_STARTS) >= MAX_STARTS_PER_HOUR:
            raise PairingRefused("too many pairing attempts this hour; try again later")
        if any(p["state"] in ("committed", "awaiting_press") for p in _PENDING.values()):
            raise PairingRefused("another device is pairing right now")
        _STARTS.append(now)
        pid = secrets.token_urlsafe(16)
        nd = secrets.token_bytes(32)
        pin = _pin()
        _PENDING[pid] = {"state": "committed", "spki": device_spki, "name": str(name or "TV")[:60],
                         "nd": nd, "pin": pin, "expires": now + PAIR_TTL_S, "card_id": None,
                         "device_id": device_pairing.spki_id(device_spki)}
        return {"pairing_id": pid, "commit": commitment(pin, device_spki, nd), "ttl_s": PAIR_TTL_S}


def reveal(pairing_id: str, nt: bytes) -> bytes:
    if not isinstance(nt, (bytes, bytearray)) or len(nt) != 32:
        raise PairingRefused("Nt must be 32 bytes")
    with _LOCK:
        _expire(time.time())
        p = _PENDING.get(str(pairing_id))
        if p is None or p["state"] != "committed":
            raise PairingRefused("no pairing waiting for this step")
        p["state"] = "awaiting_press"
        code = sas(p["pin"], p["spki"], bytes(nt), p["nd"])
        nd = p["nd"]
    from agent_friday.services import approvals
    card = approvals.create_approval(
        kind=KIND, subject_type="device", subject_id=str(pairing_id),
        title="Pair a TV: is it showing %s?" % spoken(code),
        description=("Press Approve only if the TV in front of you shows %s. "
                     "Paired, it may chat with Friday and read what she shares; "
                     "what else it may do is your choice afterwards, under Paired devices." % spoken(code)),
        action_description="Pair a TV with Friday",
        payload={"pairing_id": str(pairing_id), "device_id": p["device_id"], "sas": code,
                 "origin": "tv_gateway"},
        requested_by="tv_gateway", force_gate=True)
    with _LOCK:
        p["card_id"] = card.get("approval_id")
    return nd


def status(pairing_id: str) -> dict:
    with _LOCK:
        _expire(time.time())
        p = _PENDING.get(str(pairing_id))
        if p is None:
            return {"state": "unknown"}
        out = {"state": p["state"]}
        if p["state"] == "paired":
            out["device_id"] = p["device_id"]
        return out


def _on_decision(rec: dict) -> None:
    payload = rec.get("payload") or {}
    with _LOCK:
        p = _PENDING.get(str(payload.get("pairing_id")))
        if p is None or p["card_id"] != rec.get("approval_id") or p["state"] != "awaiting_press":
            return
        if time.time() > p["expires"]:
            p["state"] = "expired"
            return
        from agent_friday.services.approvals import SCREEN_LABEL
        if rec.get("status") != "approved" or rec.get("decided_by") != SCREEN_LABEL:
            p["state"] = "refused"
            return
        dev = device_pairing.add_device(p["spki"], name=p["name"], rights=FIRST_RIGHTS)
        p["state"] = "paired"
    device_pairing._receipt("device_paired", device_pairing.get_device(dev["id"]) or dev,
                            approval_id=rec.get("approval_id"), rights=FIRST_RIGHTS)


def _register() -> None:
    from agent_friday.services import approvals
    approvals.register_decision_hook(KIND, _on_decision)


_register()


def _reset_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()
        _STARTS.clear()


def pending_card(pairing_id: str) -> Optional[str]:
    with _LOCK:
        p = _PENDING.get(str(pairing_id))
        return p["card_id"] if p else None
