"""A search card (payload.public_query) is never offered by text and a YES code for it is refused.

SENSITIVE (privacy/public_query, "ASK WITH PREVIEW"). A search card asks the owner whether to send
the exact text it shows; a text message carries only the title, so the owner would approve text
they never saw. Only the Send button on screen approves one (approvals.PUBLIC_QUERY_CARD_APPROVERS).

The real phone service handlers run over the fake Twilio: nothing here reaches a network.
All fixtures are synthetic."""
import time

from agent_friday.phone import config, service
from agent_friday.services import approvals
from tests.unit.phone_fakes import fake_twilio, phone_home, quiet, verify_owner  # noqa: F401

QUERY = "biopsy results from the clinic came back meaning"


def _search_card():
    return approvals.create_approval(
        kind="tool_confirm", subject_type="tool_action", subject_id="public_query:sms:%s" % time.time(),
        title="Send this web search?", action_description=QUERY,
        description="This search repeats words from this conversation.", force_gate=True,
        requested_by="public_query",
        payload={"tool": "search_web", "input": {"query": QUERY},
                 "public_query": {"provider": "anthropic", "model": "claude-x",
                                  "engine": "DuckDuckGo", "texts": [QUERY]}})


def _model_card():
    return approvals.create_approval(
        kind="external_message", subject_type="email", subject_id="m:%s" % time.time(),
        title="Send email to someone", action_description="Send email to someone", force_gate=True)


def test_a_search_card_is_never_offered_by_text(phone_home, fake_twilio, monkeypatch, quiet):  # noqa: F811
    """Red on 55b31e73a: the search card was texted with a YES code."""
    verify_owner(monkeypatch)
    config.update({"sms_approvals": True})
    monkeypatch.setattr(service, "_gate_text", lambda t: t)
    _search_card()
    assert service.offer_pending_approvals() == 0 and fake_twilio.sent_bodies() == []
    _model_card()
    assert service.offer_pending_approvals() == 1, "other cards are offered as before"


def test_a_yes_code_for_a_search_card_is_refused(phone_home, fake_twilio, monkeypatch, quiet):  # noqa: F811
    """Red on 55b31e73a: the reply handler approved it with decided_by owner:sms."""
    verify_owner(monkeypatch)
    config.update({"sms_approvals": True})
    card = _search_card()
    salt = "abcd1234abcd1234"
    service._save_state("sms_approvals.json", {"codes": {card["approval_id"]: {
        "salt": salt, "hash": service._hash_code("123456", salt), "expires": time.time() + 1000,
        "title": "Send this web search?"}}, "failures": []})
    said = service.handle_approval_reply("YES 123456")
    assert said.startswith("A search card is sent only on screen")
    assert approvals.get_approval(card["approval_id"])["status"] == "pending"
    assert service.handle_approval_reply("NO 123456").startswith("Denied"), "a NO still declines it"
    assert approvals.get_approval(card["approval_id"])["status"] == "denied"


def test_the_central_rule_holds_even_if_the_handler_were_bypassed(phone_home):  # noqa: F811
    card = _search_card()
    assert approvals.decide(card["approval_id"], "approve", decided_by="owner:sms")["status"] == "pending"
