"""A turn that came in by phone may only read, and the phone tools act on the
owner's own words, never on a number the model picked up elsewhere."""
import json

import pytest

import agent_friday.services.agent as agent
from agent_friday.phone import config
from agent_friday.services import approvals
from tests.unit.phone_fakes import (OWNER, STRANGER, fake_twilio, phone_home, quiet,  # noqa: F401
                                    verify_owner)


@pytest.fixture(autouse=True)
def _no_bom(monkeypatch):
    # _governance_check signs an audit entry; keep it out of the real home.
    import agent_friday.privacy.vault_crypto as vc
    monkeypatch.setattr(vc, "sign_entry", lambda e, *a, **k: e, raising=False)


@pytest.mark.parametrize("tool", ["write_file", "text_by_phone", "call_by_phone",
                                  "draft_email", "spawn_task", "unknown_tool"])
def test_a_phone_turn_cannot_use_anything_above_ring_0(tool):
    for ctx in ({"origin": "phone"}, {"origin": "phone", "authenticated": True},
                {"origin": "phone", "is_background_task": True}):
        allowed, reason = agent._governance_check(tool, {}, ctx)
        assert not allowed and "by phone" in reason, (tool, ctx)


def test_a_phone_turn_can_still_read():
    allowed, _ = agent._governance_check("read_file", {"path": "x"}, {"origin": "phone"})
    assert allowed


def test_the_owner_at_his_machine_is_unaffected():
    allowed, _ = agent._governance_check("write_file", {}, {"authenticated": True})
    assert allowed


def test_the_chat_turn_stamps_the_owners_words():
    ctx = agent.prepare_confirmation_ctx("s1", "text 512 555 0199 hello", {"authenticated": True})
    assert ctx["owner_text"] == "text 512 555 0199 hello"


def _run_tool(name, inp, session_ctx):
    handler = agent.CLAUDE_TOOL_HANDLERS[name]
    tok = agent._CURRENT_OWNER_TEXT.set(
        "" if session_ctx.get("origin") == "phone" or session_ctx.get("is_background_task")
        else session_ctx.get("owner_text", ""))
    try:
        return json.loads(handler(inp))
    finally:
        agent._CURRENT_OWNER_TEXT.reset(tok)


def test_the_tools_are_registered_as_network_ring():
    assert agent.TOOL_RINGS["text_by_phone"] == 2 and agent.TOOL_RINGS["call_by_phone"] == 2
    names = {t["name"] for t in agent.CLAUDE_TOOLS}
    assert {"text_by_phone", "call_by_phone"} <= names


def test_texting_a_number_the_owner_did_not_type_is_refused(phone_home, fake_twilio, monkeypatch):
    verify_owner(monkeypatch)
    out = _run_tool("text_by_phone", {"to": STRANGER, "body": "hi"},
                    {"owner_text": "reply to the email from my landlord"})
    assert out["queued"] is False and "name the number" in out["reason"]
    assert approvals.list_approvals() == [] and fake_twilio.calls == []


def test_texting_a_number_the_owner_typed_queues_a_card(phone_home, fake_twilio, monkeypatch, quiet):
    verify_owner(monkeypatch)
    out = _run_tool("text_by_phone", {"to": "512-555-0199", "body": "hi"},  # pragma: allowlist secret
                    {"owner_text": "text (512) 555-0199 hi"})
    assert out["queued"] is True and out["sent"] is False
    assert fake_twilio.calls == []


def test_a_background_task_cannot_name_a_number_for_the_owner(phone_home, fake_twilio, monkeypatch):
    verify_owner(monkeypatch)
    out = _run_tool("text_by_phone", {"to": STRANGER, "body": "hi"},
                    {"is_background_task": True, "owner_text": "text 512 555 0199"})
    assert out["queued"] is False


def test_texting_the_owner_goes_now(phone_home, fake_twilio, monkeypatch):
    verify_owner(monkeypatch)
    monkeypatch.setattr("agent_friday.phone.service._gate_text", lambda t: t)
    out = _run_tool("text_by_phone", {"body": "the address is 1 Main St"}, {"owner_text": "text me"})
    assert out["sent"] is True
    assert fake_twilio.calls[-1]["data"]["To"] == OWNER


def test_a_call_is_always_a_card(phone_home, fake_twilio, monkeypatch, quiet):
    verify_owner(monkeypatch)
    out = _run_tool("call_by_phone", {"message": "wake up"}, {"owner_text": "call me at 7"})
    assert out["queued"] is True and fake_twilio.calls == []


# ── the governance checkpoint (governance/action_gate.py) ────────────────────

from agent_friday.governance import action_gate  # noqa: E402


def test_the_phone_tools_are_outward_to_the_checkpoint():
    assert action_gate.classify("text_by_phone", {"body": "hi"})[0] == action_gate.OUTWARD
    assert action_gate.classify("call_by_phone", {"message": "hi"})[0] == action_gate.OUTWARD
    assert "call_by_phone" in action_gate.SELF_GATED
    assert "text_by_phone" not in action_gate.SELF_GATED


def test_a_text_from_chat_waits_for_a_yes_and_from_the_background_for_a_card(phone_home):
    chat = action_gate.authorize("text_by_phone", {"body": "hi"}, {"session_id": "s1"})
    assert chat.action == "confirm"
    bg = action_gate.authorize("text_by_phone", {"body": "hi"}, {"is_background_task": True})
    assert bg.action == "card"
    tainted = action_gate.authorize("text_by_phone", {"to": "5125550199", "body": "hi"},  # pragma: allowlist secret
                                    {"session_id": "s1"}, tainted=True)
    assert tainted.action == "card"


def test_a_call_goes_to_its_own_card():
    v = action_gate.authorize("call_by_phone", {"message": "hi"}, {"session_id": "s1"})
    assert v.action == "allow" and "own approval card" in v.reason


def test_direct_sends_hold_when_the_claws_check_fails(phone_home, fake_twilio, monkeypatch):
    from agent_friday.phone import service
    verify_owner(monkeypatch)
    monkeypatch.setattr(action_gate, "verify_claws", lambda: (False, "tampered"))
    with pytest.raises(service.PhoneRefused, match="cLaws"):
        service.text_owner("hi")
    assert fake_twilio.calls == []


def test_direct_sends_hold_when_the_receipt_cannot_be_written(phone_home, fake_twilio, monkeypatch):
    from agent_friday.phone import service

    def boom(entry):
        raise OSError("disk full")
    verify_owner(monkeypatch)
    monkeypatch.setattr(action_gate, "_receipt", boom)
    with pytest.raises(service.PhoneRefused, match="receipt"):
        service.text_owner("hi")
    assert fake_twilio.calls == []


def test_direct_sends_leave_a_signed_receipt(phone_home, fake_twilio, monkeypatch):
    import json as _json
    from agent_friday.phone import service
    verify_owner(monkeypatch)
    monkeypatch.setattr(service, "_gate_text", lambda t: t)
    service.text_owner("hi")
    rows = [_json.loads(x) for x in (phone_home / "decision-bom.jsonl").read_text().splitlines()]
    assert any(r.get("tool") == "phone:sms" and r.get("hmac") for r in rows)
