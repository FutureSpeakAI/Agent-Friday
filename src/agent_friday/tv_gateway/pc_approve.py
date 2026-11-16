"""Approving cards from the owner's phone or tablet (Mobile v3, Addendum B; brief BURNDOWN_BUILD_BRIEF_approvals_from_phone).

A device registers its approval key here. The key stays staged until the owner presses the ``pc_approve`` card on this PC's own screen; that press, in the
card's decision hook (pairing._on_right_decision), activates the key and then grants the right; a right without an active key never approves
(device_pairing.can_approve).
"""
from __future__ import annotations

import base64
import binascii
import time
from typing import Dict, List

from flask import Flask, g, request

from agent_friday.services import device_pairing
from agent_friday.tv_gateway import pairing, tailnet
from agent_friday.tv_gateway.pc_common import allowed, refuse

MAX_SPKI_B64 = 400
MAX_KEYS_PER_HOUR = 3
_STAGED_AT: Dict[str, List[float]] = {}
ASKED = {True: "The device asked for the fingerprint on every approval.",
         False: "The device asked for NO fingerprint: any use while it is unlocked approves."}


def _within_key_budget(device_id: str) -> bool:
    """At most MAX_KEYS_PER_HOUR registrations per device an hour. Guarded by test_a_device_may_register_three_keys_an_hour."""
    now = time.time()
    recent = [t for t in _STAGED_AT.get(device_id, []) if now - t < 3600]
    if len(recent) >= MAX_KEYS_PER_HOUR:
        return False
    _STAGED_AT[device_id] = recent + [now]
    return True


def register(app: Flask) -> None:
    @app.post("/tv/v1/approvals/key")
    def pc_approval_key():
        dev = getattr(g, "device", None)
        if not allowed(dev):
            return refuse(403, "this device cannot approve for the PC", code="pc_approve_not_for_this_device")
        body = request.get_json(silent=True) or {}
        biometric = body.get("biometric")
        try:
            spki_b64 = str(body.get("spki") or "")
            spki = base64.b64decode(spki_b64, validate=True) if 0 < len(spki_b64) <= MAX_SPKI_B64 else b""
        except (binascii.Error, ValueError):
            spki = b""
        if not spki or not isinstance(biometric, bool):
            return refuse(400, "send {spki: base64 P-256 key, biometric: true or false}", code="bad_request")
        if not _within_key_budget(dev["id"]):
            return refuse(429, "this device has registered too many keys this hour; try later", code="too_many_keys")
        try:
            stage, key_id = device_pairing.stage_approval_key(dev["id"], spki, biometric)
        except device_pairing.ApprovalKeyRefused as e:
            return refuse(422, "the PC will not take that key", code=e.code)
        if stage is device_pairing.KeyStage.ALREADY_ACTIVE:
            device_pairing.expire_pending_key_cards(dev["id"], "the active key was registered again")
            return {"ok": True, "status": stage.value, "key_id": key_id, "biometric": biometric, "attested": False}
        pairing.offer_right(dev["id"], device_pairing.APPROVAL_RIGHT, key_id=key_id, biometric=biometric,
                           detail=ASKED[biometric])
        device_pairing._receipt("device_approval_key_staged", dev, key_id=key_id, biometric=biometric,
                                via=tailnet.via())
        return {"ok": True, "status": stage.value, "key_id": key_id, "biometric": biometric, "attested": False,
                "next": "press the card on the PC"}
