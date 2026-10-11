"""A card Laya raised is never offered by text and a YES code for it is refused (F3-MJ-1).

The real phone service handlers run over the fake Twilio: nothing here reaches a network."""
import time

from agent_friday.phone import config, service
from agent_friday.services import approvals
from agent_friday.services import item_actions as ia
from tests.unit.phone_fakes import fake_twilio, phone_home, quiet, verify_owner  # noqa: F401


def _laya_card():
    return approvals.create_approval(
        kind=ia.APPROVAL_KIND, subject_type="email", subject_id="laya:%s" % time.time(),
        title="Friday wants to archive 3 conversations", action_description="archive 3 conversations",
        payload={"handler": ia.HANDLER, "origin": "laya_safe_write"}, force_gate=True)


def _model_card():
    return approvals.create_approval(
        kind="external_message", subject_type="email", subject_id="m:%s" % time.time(),
        title="Send email to someone", action_description="Send email to someone", force_gate=True)


def test_a_laya_card_is_never_offered_by_text(phone_home, fake_twilio, monkeypatch, quiet):  # noqa: F811
    verify_owner(monkeypatch)
    config.update({"sms_approvals": True})
    monkeypatch.setattr(service, "_gate_text", lambda t: t)
    _laya_card()
    assert service.offer_pending_approvals() == 0 and fake_twilio.sent_bodies() == []
    _model_card()
    assert service.offer_pending_approvals() == 1, "other cards are offered as before"


def test_a_yes_code_for_a_laya_card_is_refused_through_the_real_reply_handler(phone_home, fake_twilio,  # noqa: F811
                                                                              monkeypatch, quiet):
    """Fails on da66aaa40: the reply handler approved it with decided_by owner:sms, and the owner never saw the rows."""
    verify_owner(monkeypatch)
    config.update({"sms_approvals": True})
    card = _laya_card()
    salt = "abcd1234abcd1234"
    service._save_state("sms_approvals.json", {"codes": {card["approval_id"]: {
        "salt": salt, "hash": service._hash_code("123456", salt), "expires": time.time() + 1000,
        "title": "Friday wants to archive 3 conversations"}}, "failures": []})
    said = service.handle_approval_reply("YES 123456")
    assert said.startswith("A card Laya raised is approved only on screen")
    assert approvals.get_approval(card["approval_id"])["status"] == "pending"
    assert service.handle_approval_reply("NO 123456").startswith("Denied"), "a NO still declines it"
    assert approvals.get_approval(card["approval_id"])["status"] == "denied"


def test_the_central_rule_holds_even_if_the_handler_were_bypassed(phone_home):  # noqa: F811
    card = _laya_card()
    rec = approvals.decide(card["approval_id"], "approve", decided_by="owner:sms")
    assert rec["status"] == "pending"
