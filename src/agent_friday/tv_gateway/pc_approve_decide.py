"""The approvals routes for the owner's phone and tablet: list, feed, challenge and decide (brief 3.2-3.5).

* list and feed need ``pc_read``; a card that repeats the action's details (approvals.PRIVATE_DETAIL_KINDS) shows only
  its generic title unless the device also holds ``pc_approve`` with an active key.
* challenge and an approve need ``pc_approve`` and an active key, and a signature from that key (approval_verify).
* a deny needs only an owner phone or tablet: narrowing never needs a signature.
Every outcome writes a receipt naming the device, ``via`` and the card. Each rule is held by a test in
tests/unit/test_tv_pc_approve_decide.py, named at the rule.
"""
from __future__ import annotations

import dataclasses
import json
import queue
import time
from typing import Optional

from flask import Flask, Response, g, jsonify, request, stream_with_context

from agent_friday.services import approval_feed, approvals, card_facts, device_pairing
from agent_friday.tv_gateway import approval_verify as av
from agent_friday.tv_gateway import pc_read, tailnet
from agent_friday.tv_gateway.approval_verify import Refusal, Rejected
from agent_friday.tv_gateway.pc_common import allowed, refuse

BEAT_S = 20


def _fresh(dev: dict) -> Optional[dict]:
    return device_pairing.get_device(dev["id"])


def card_item(rec: dict, dev: dict) -> dict:
    """One card as this device may see it. Private-detail kinds are cut down to their generic title unless the
    device can approve. Guarded by test_a_private_card_shows_only_its_generic_title_without_pc_approve."""
    may_see_detail = device_pairing.can_approve(dev) or rec.get("kind") not in approvals.PRIVATE_DETAIL_KINDS
    why_not = _why_not_here(rec, dev)
    item = {"approval_id": rec.get("approval_id"), "kind": rec.get("kind"), "status": rec.get("status"),
            "created_at": rec.get("created_at"), "can_approve_here": why_not is None,
            "approve_blocked_because": why_not}
    if may_see_detail:
        item.update(title=rec.get("title"), description=rec.get("description"),
                    card_facts=card_facts.safe_card_facts(rec))
    else:
        item.update(title=approvals.notice_title(rec), description="", waiting_notice_title=approvals.notice_title(rec))
    return item


def _why_not_here(rec: dict, dev: dict) -> Optional[str]:
    if not device_pairing.can_approve(dev):
        return "This device has not been allowed to approve for the PC. Approve this one on your PC."
    return av.device_may_not_decide(rec, dev["id"])


def _receipt(kind: str, dev: dict, rec: dict, **extra) -> None:
    key = device_pairing.approval_key(dev)
    device_pairing._receipt(kind, dev, approval_id=rec.get("approval_id"), card_kind=rec.get("kind"),
                            device_name=dev.get("name"), key_id=key.key_id if key else None,
                            biometric=bool(key and key.biometric), attested=False, via=tailnet.via(), **extra)


def _owner_device() -> Optional[Response]:
    dev = getattr(g, "device", None)
    if not allowed(dev):
        return refuse(403, "this device cannot approve for the PC", code=Refusal.NOT_FOR_DEVICE.value)
    return None


def _refusal_response(rejected: Rejected) -> Response:
    extra = {"why": rejected.why} if rejected.why else {}
    return refuse(av.HTTP_STATUS[rejected.refusal], rejected.refusal.value.replace("_", " "),
                  code=rejected.refusal.value, **extra)


def _card(approval_id: str) -> dict:
    rec = approvals.get_approval(approval_id)
    if rec is None:
        raise Rejected(Refusal.UNKNOWN_CARD)
    return rec


def _approving_device(dev: dict) -> dict:
    current = _fresh(dev) or {}
    if not allowed(current):
        raise Rejected(Refusal.NOT_FOR_DEVICE)
    if not device_pairing.can_approve(current):
        raise Rejected(Refusal.NEEDS_RIGHT)
    return current


def _signed_approval(approval_id: str, body: dict) -> av.SignedApproval:
    nonce, signature, signed_time = body.get("nonce"), body.get("sig"), body.get("time")
    if not (isinstance(nonce, str) and isinstance(signature, str) and isinstance(signed_time, int)
            and not isinstance(signed_time, bool)):
        raise Rejected(Refusal.BAD_REQUEST, "send {decision, nonce, time, sig}")
    return av.SignedApproval(approval_id, "", nonce, signed_time, signature)


def verify(dev: dict, approval_id: str, body: dict, now: Optional[float] = None) -> av.SignedApproval:
    """The brief's order (3.4). Raises Rejected; returns what was signed. Each step is held by a test:

    1. the device may approve (owner phone or tablet, the right, an active key):
       test_a_device_without_the_right_cannot_get_a_challenge_or_approve
    2. the card kind may be approved from a device:
       test_a_device_right_card_is_never_approved_from_a_device
    3. the nonce is live for this device and card, and is spent BEFORE the signature is looked at:
       test_a_replayed_nonce_is_refused, test_a_bad_signature_still_spends_the_nonce
    4. the signed time is within the window: test_a_time_outside_the_window_is_refused
    5. the signature verifies with the approval key (never the session key):
       test_the_session_key_cannot_sign_an_approval
    6. the card as it is NOW is pending, sealed, and has the fingerprint that was signed:
       test_a_card_resealed_between_challenge_and_decide_is_refused_by_the_signed_fingerprint
    7. the card kind is still one a device may approve:
       test_the_cards_the_pc_keeps_to_their_own_button_are_not_approved_from_a_device
    """
    now = time.time() if now is None else now
    dev = _approving_device(dev)
    rec = _card(approval_id)
    why = av.device_may_not_decide(rec, dev["id"])
    if why:
        raise Rejected(Refusal.CARD_NOT_FOR_DEVICES, why)
    signed = _signed_approval(approval_id, body)
    challenge = av.consume(dev["id"], approval_id, signed.nonce, now)
    if abs(now - signed.signed_time) > av.TIME_WINDOW_S:
        raise Rejected(Refusal.BAD_TIME)
    key = device_pairing.approval_key(dev)
    message = av.signing_message(approval_id, challenge.action_fp, challenge.nonce, dev["id"], signed.signed_time)
    if not av.signature_holds(key.spki, message, signed.signature_b64):
        count = device_pairing.note_bad_signature(dev["id"])
        _receipt("device_approval_refused", dev, rec, reason="bad_signature", count=count)
        raise Rejected(Refusal.BAD_SIGNATURE)
    rec = _card(approval_id)
    if rec.get("status") != "pending":
        raise Rejected(Refusal.ALREADY_DECIDED)
    if not (approvals.card_seal_holds(rec) and approvals.fingerprint_holds(rec)
            and approvals._action_fp(rec) == challenge.action_fp):
        raise Rejected(Refusal.CARD_CHANGED)
    why = av.device_may_not_decide(rec, dev["id"])
    if why:
        raise Rejected(Refusal.CARD_NOT_FOR_DEVICES, why)
    return av.SignedApproval(approval_id, challenge.action_fp, challenge.nonce, signed.signed_time,
                             signed.signature_b64)


def _decision_answer(rec: Optional[dict], won: bool):
    status = (rec or {}).get("status")
    return jsonify({"ok": True, "won": won, "already_decided": not won, "status": status})


def _approve(dev: dict, approval_id: str, body: dict):
    try:
        signed = verify(dev, approval_id, body)
    except Rejected as rejected:
        if rejected.refusal is Refusal.ALREADY_DECIDED:
            return _decision_answer(approvals.get_approval(approval_id), False)
        card = approvals.get_approval(approval_id) or {"approval_id": approval_id}
        if rejected.refusal is not Refusal.BAD_SIGNATURE:
            _receipt("device_approval_refused", dev, card, reason=rejected.refusal.value)
        return _refusal_response(rejected)
    device_pairing.clear_bad_signatures(dev["id"])
    rec, won = approvals.decide_with_outcome(approval_id, "approve", decided_by=av.device_label(dev["id"]),
                                             note="approved on the device", source="device_signed",
                                             expect_fp=signed.action_fp,
                                             proof={**dataclasses.asdict(signed),
                                                    "key_id": device_pairing.approval_key(dev).key_id})
    if won:
        _receipt("device_approved", dev, rec, action_fp=signed.action_fp, nonce=signed.nonce,
                 signed_time=signed.signed_time, signature=signed.signature_b64, outcome=rec.get("status"))
    elif rec is None:
        return _refusal_response(Rejected(Refusal.UNKNOWN_CARD))
    elif (rec or {}).get("status") == "pending":
        _receipt("device_approval_refused", dev, rec, reason=Refusal.NOT_APPROVED.value)
        return _refusal_response(Rejected(Refusal.NOT_APPROVED, "the PC did not approve it; try again or use the PC"))
    return _decision_answer(rec, won)


def _deny(dev: dict, approval_id: str):
    """Narrowing needs no signature (brief 3.2). Guarded by test_deny_needs_only_an_owner_phone_with_no_extra_right."""
    rec = approvals.get_approval(approval_id)
    if rec is None:
        return _refusal_response(Rejected(Refusal.UNKNOWN_CARD))
    rec, won = approvals.decide_with_outcome(approval_id, "deny", decided_by=av.device_label(dev["id"]),
                                             note="declined on the device", source="device")
    if won:
        _receipt("device_denied", dev, rec, outcome=rec.get("status"))
    return _decision_answer(rec, won)


def register(app: Flask) -> None:
    @app.get("/tv/v1/approvals")
    def pc_approvals_list():
        denied = pc_read._guard("approvals")
        if denied is not None:
            return denied
        if request.args.get("status", "pending") != "pending":
            return refuse(400, "only pending cards are listed", code=Refusal.BAD_REQUEST.value)
        cards = [card_item(rec, g.device) for rec in approvals.list_approvals(status="pending")]
        return jsonify({"ok": True, "as_of": int(time.time()), "cards": cards, "count": len(cards)})

    @app.get("/tv/v1/approvals/events")
    def pc_approvals_events():
        denied = pc_read._guard("approvals")
        if denied is not None:
            return denied
        device_id = g.device["id"]

        def frames():
            feed = approval_feed.subscribe()
            try:
                dev = _fresh(g.device) or {}
                pending = [card_item(r, dev) for r in approvals.list_approvals(status="pending")]
                yield "data: " + json.dumps({"type": "snapshot", "cards": pending}, default=str) + "\n\n"
                while True:
                    try:
                        event = feed.get(timeout=BEAT_S)
                    except queue.Empty:
                        event = {"type": "beat"}
                    dev = device_pairing.get_device(device_id) or {}
                    if not (allowed(dev) and device_pairing.has_right(dev, pc_read.RIGHT)):
                        yield 'data: {"type": "revoked"}\n\n'
                        return
                    yield "data: " + json.dumps(_frame(event, dev), default=str) + "\n\n"
            finally:
                approval_feed.unsubscribe(feed)
        return Response(stream_with_context(frames()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/tv/v1/approvals/<approval_id>/challenge")
    def pc_approval_challenge(approval_id):
        denied = _owner_device()
        if denied is not None:
            return denied
        try:
            dev = _approving_device(g.device)
            rec = _card(approval_id)
            why = av.device_may_not_decide(rec, dev["id"])
            if why:
                raise Rejected(Refusal.CARD_NOT_FOR_DEVICES, why)
            if rec.get("status") != "pending":
                raise Rejected(Refusal.ALREADY_DECIDED)
            if not (approvals.card_seal_holds(rec) and approvals.fingerprint_holds(rec)):
                raise Rejected(Refusal.CARD_CHANGED)
            challenge = av.mint(dev["id"], approval_id, approvals._action_fp(rec))
        except Rejected as rejected:
            if rejected.refusal is Refusal.ALREADY_DECIDED:
                return _decision_answer(approvals.get_approval(approval_id), False)
            return _refusal_response(rejected)
        key = device_pairing.approval_key(dev)
        return jsonify({"ok": True, "approval_id": approval_id, "kind": rec.get("kind"), "title": rec.get("title"),
                        "description": rec.get("description"), "card_facts": card_facts.safe_card_facts(rec),
                        "action_fp": challenge.action_fp, "nonce": challenge.nonce, "ttl_s": av.NONCE_TTL_S,
                        "biometric_required": bool(key and key.biometric)})

    @app.post("/tv/v1/approvals/<approval_id>/decide")
    def pc_approval_decide(approval_id):
        denied = _owner_device()
        if denied is not None:
            return denied
        body = request.get_json(silent=True) or {}
        decision = body.get("decision")
        if decision == "deny":
            return _deny(g.device, approval_id)
        if decision == "approve":
            return _approve(g.device, approval_id, body)
        return refuse(400, "decision must be approve or deny", code=Refusal.BAD_REQUEST.value)


def _frame(event: dict, dev: dict) -> dict:
    """A feed event as this device may see it: a pending card is cut down the same way the list is."""
    if event.get("type") == "pending" and isinstance(event.get("approval"), dict):
        return {"type": "pending", "card": card_item(event["approval"], dev)}
    return event
