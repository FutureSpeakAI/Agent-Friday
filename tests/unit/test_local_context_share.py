"""Private context reaches the cloud voice model only as the user approves it.

The cloud model asks the local model a question needing private context; the
answer is scrubbed (people become relationship placeholders, identifiers
become numbered placeholders), gated, and shown on a card exactly as it would
be sent. He sends it, edits it, or declines it, on screen or by voice. These
tests use a stand-in for the local model and the real scrubber, gate, approval
store, decision hook, grants and live-call channel.
"""
import pytest

import agent_friday.routes.voice as rv
import agent_friday.services.voice_engine as ve
from agent_friday.services import approvals
from agent_friday.services import egress_gate as eg
from agent_friday.services import local_context as lc
from agent_friday.services import sensitivity_classifier as sc
from agent_friday.services import voice_live_channel as vlc

CID = "conv-share-test"
RAW = ("On weekends {{person: Dana | their partner}} likes the farmers market and "
       "long walks with {{person: Sam | a friend}}. Next Saturday they have lunch "
       "booked at noon. Dana's number is 512-555-0199 and her email is "
       "dana.x@example.com.")
VALUES = ("Dana", "Sam", "512-555-0199", "555-0199", "dana.x@example.com")


@pytest.fixture
def world(monkeypatch, tmp_path):
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "_grants_file", lambda: tmp_path / "grants.json")
    monkeypatch.setattr(sc, "_embedding_tier", lambda t: (0, 0.0))
    monkeypatch.setattr(eg, "_rate_limit", lambda: None)
    monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: False)
    lc.register()
    heard = []
    vlc.register(CID, lambda text, kind: heard.append((kind, text)))
    lc.SENT_LOG.clear()
    yield heard
    vlc.unregister(CID)


def _ask(**k):
    return lc.request("What do they enjoy on weekends, and what is on next weekend?",
                      conversation_id=k.get("cid", CID), cloud_model="gemini-3.8-live",
                      answer_fn=lambda q: (k.get("raw", RAW), "local-brain-seat"))


def _card(out):
    assert out["status"] == "pending", out
    return approvals.get_approval(out["approval_id"])


def test_the_card_shows_the_exact_payload_and_no_raw_values(world):
    rec = _card(_ask())
    p = rec["payload"]
    for v in VALUES:
        assert v not in p["text"], f"{v!r} reached the payload"
        assert v not in rec["description"] and v not in rec["title"]
        assert v not in str(p["placeholders"])
    assert "[their partner]" in p["text"] and "[a friend]" in p["text"]
    assert "[phone number 1]" in p["text"] and "[email address 1]" in p["text"]
    cats = {x["placeholder"]: x["category"] for x in p["placeholders"]}
    assert cats["[their partner]"] == "a person (name removed)"
    assert cats["[phone number 1]"] == "a phone number (removed)"
    assert p["local_model"] == "local-brain-seat" and p["cloud_model"] == "gemini-3.8-live"
    assert rec["description"] == p["text"]


def test_what_is_sent_equals_the_card_byte_for_byte(world):
    rec = _card(_ask())
    shown = rec["payload"]["text"]
    approvals.decide_with_outcome(rec["approval_id"], "approve")
    assert world == [("context", shown)], "the call must receive exactly the card's text"
    assert lc.SENT_LOG[-1]["text"] == shown
    # ...and the bridge hands it to the model unchanged, after a fixed lead.
    assert rv._injection_text(shown, "context") == rv._INJECT_LEADS["context"] + shown
    # Approved once, executed once: a second approval changes nothing.
    approvals.decide_with_outcome(rec["approval_id"], "approve")
    assert len(world) == 1


def test_a_declined_card_sends_nothing(world):
    rec = _card(_ask())
    shown = rec["payload"]["text"]
    approvals.decide_with_outcome(rec["approval_id"], "deny")
    assert not [t for k, t in world if k == "context"]
    assert all(shown not in t for _k, t in world), "the declined text leaked in a note"
    assert lc.SENT_LOG == []


def test_an_edit_is_sent_exactly_as_saved(world):
    rec = _card(_ask())
    mine = "On weekends [their partner] likes the market. Nothing is booked yet."
    res = lc.edit(rec["approval_id"], mine)
    assert res["ok"] and res["approval"]["payload"]["text"] == mine
    assert res["approval"]["payload"]["version"] == 2
    approvals.decide_with_outcome(rec["approval_id"], "approve")
    assert world == [("context", mine)]


def test_an_edit_the_check_would_change_is_asked_about_not_altered(world):
    rec = _card(_ask())
    mine = "Call them at 512-555-0199 about Saturday."
    res = lc.edit(rec["approval_id"], mine)
    assert res.get("needs_confirm") and res["yours"] == mine
    assert "[phone number 1]" in res["checked"]
    assert approvals.get_approval(rec["approval_id"])["payload"]["version"] == 1   # nothing saved
    kept = lc.edit(rec["approval_id"], mine, accept="mine")        # his words, his choice
    assert kept["ok"] and kept["approval"]["payload"]["text"] == mine
    approvals.decide_with_outcome(rec["approval_id"], "approve")
    assert world == [("context", mine)]


def test_voice_approve_counts_only_his_own_words(world):
    rec = _card(_ask())
    aid = rec["approval_id"]
    refused = lc.decide_by_voice(aid, "what's the weather like", False, "approve")
    assert not refused["ok"] and approvals.get_approval(aid)["status"] == "pending"
    assert world == []
    ok = lc.decide_by_voice(aid, "yes, send it", False, "approve")
    assert ok["ok"] and ok["status"] == "approved"
    assert world == [("context", rec["payload"]["text"])]


def test_voice_decline_sends_nothing(world):
    rec = _card(_ask())
    res = lc.decide_by_voice(rec["approval_id"], "no, don't send it", False, "deny")
    assert res["ok"] and res["status"] == "denied"
    assert not [t for k, t in world if k == "context"]


def test_a_room_of_several_people_needs_friday_named(world):
    rec = _card(_ask())
    aid = rec["approval_id"]
    assert not lc.decide_by_voice(aid, "send it", True, "approve")["ok"]
    assert lc.decide_by_voice(aid, "Friday, send it", True, "approve")["ok"]


def test_the_voice_tools_use_his_words_from_the_call(world, monkeypatch):
    rec = _card(_ask())
    monkeypatch.setattr(ve, "_voice_room_mode", lambda: False)
    out = ve._voice_tool_run("answer_share_request",
                             {"request_id": rec["approval_id"], "decision": "approve"},
                             lambda f: None, session={"conversation_id": CID,
                                                      "owner_text": "hmm let me think"})
    assert out.startswith("NOT RECORDED")
    out = ve._voice_tool_run("answer_share_request",
                             {"request_id": rec["approval_id"], "decision": "approve"},
                             lambda f: None, session={"conversation_id": CID,
                                                      "owner_text": "okay, send it"})
    assert out.startswith("Recorded: they approved")


def test_revise_by_voice_changes_the_draft_on_his_machine(world):
    rec = _card(_ask(raw="They like the market on Saturday. They also like hiking."))
    aid = rec["approval_id"]
    assert lc.revise_by_voice(aid, "change Saturday to Sunday")["ok"]
    assert "Sunday" in approvals.get_approval(aid)["payload"]["text"]
    assert lc.revise_by_voice(aid, "leave out the part about hiking")["ok"]
    text = approvals.get_approval(aid)["payload"]["text"]
    assert "hiking" not in text and "market on Sunday" in text


def test_a_conversation_grant_shares_without_a_card(world):
    lc.grant_for_conversation(CID)
    out = _ask()
    assert out["status"] == "sent" and out["under_grant"]
    assert world and world[0][0] == "context" and "Dana" not in world[0][1]
    other = _ask(cid="conv-other")
    assert other["status"] == "pending", "a grant covers only its own conversation"


def test_local_only_mode_refuses_the_request(monkeypatch):
    monkeypatch.setattr(ve, "_load_settings", lambda: {"model_routing": {"mode": "local_only"}})
    out = ve._voice_tool_run("ask_local_for_context", {"question": "anything"},
                             lambda f: None, session={"conversation_id": CID})
    assert out.startswith("NOT DONE") and "local-only" in out
