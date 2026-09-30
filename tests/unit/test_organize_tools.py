"""The organize tools are typed, governed, and the same in voice as in chat.

Each is a tool with a tight, flat schema (voice renders schemas without nested
objects), a handler, a privilege ring, a governance class, provenance roles
for its arguments, and a voice declaration of its own
(tests/unit/test_voice_organize.py holds the voice contract's tests).
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import agent
from agent_friday.services import taint
from agent_friday.services import voice_engine

TOOLS = ("organize_email", "organize_files", "organize_wiki", "undo_action", "answer_card")


def _schema(name):
    return next(t for t in agent.CLAUDE_TOOLS if t.get("name") == name)


@pytest.mark.parametrize("name", TOOLS)
def test_each_tool_is_registered_everywhere(name):
    assert name in agent.CLAUDE_TOOL_HANDLERS
    assert name in agent.TOOL_RINGS
    assert ag.known(name)
    assert name in taint.TOOL_ROLES
    assert name in {t[0] for t in voice_engine._VOICE_LIVE_TOOLS}


@pytest.mark.parametrize("name", TOOLS)
def test_each_schema_is_flat_and_speakable(name):
    t = _schema(name)
    assert len(t["description"]) <= 700, "a description is read on every turn and by voice"
    for key, prop in t["input_schema"]["properties"].items():
        assert prop.get("type") in ("string", "boolean", "integer", "array"), key
        if prop.get("type") == "array":
            assert prop["items"] == {"type": "string"}, key


def test_the_voice_session_declares_them_once():
    names = voice_engine._voice_tool_names()
    for name in TOOLS:
        assert names.count(name) == 1, name


def test_mail_is_outward_and_raises_its_own_card():
    assert "organize_email" in ag.OUTWARD_TOOLS and "organize_email" in ag.SELF_GATED
    v = ag._decide("organize_email", ag.OUTWARD, "", {"session_id": "s"}, False)
    assert v.action == "allow" and "own approval card" in v.reason


def test_answering_a_card_is_internal_and_carries_no_provenance_roles():
    assert "answer_card" in ag.INTERNAL_TOOLS
    assert taint.TOOL_ROLES["answer_card"] == {}


def test_a_local_item_from_outside_content_is_shown_not_enforced():
    assert taint.POLICY["local_item"] == "note"
    assert taint.TOOL_ROLES["organize_files"]["items"] == "local_item"
    assert taint.TOOL_ROLES["organize_email"]["query"] == "detail"


def test_the_gate_classifies_undo_by_the_receipt(monkeypatch):
    from agent_friday.services import action_journal as journal
    monkeypatch.setattr(journal, "get", lambda rid: {"receipt_id": rid, "domain": "email"}
                        if rid == "rcpt_mail" else {"receipt_id": rid, "domain": "files"})
    assert ag.classify("undo_action", {"receipt_id": "rcpt_mail"})[0] == ag.OUTWARD
    assert ag.classify("undo_action", {"receipt_id": "rcpt_file"})[0] == ag.INTERNAL


def test_local_voice_gives_tools_the_owners_words():
    """A spoken yes decides a card only through the owner's own words; the
    local voice turn must hand them to its tool calls, marked as spoken."""
    import inspect
    from agent_friday.routes import voice
    src = inspect.getsource(voice)
    at = src.index('"surface": "voice-local"')
    assert '"owner_text": str(user_text or "")[:4000]' in src[at:at + 200]


def test_a_card_result_tells_the_model_to_read_it_back():
    text = agent._organize_result({"status": "pending_approval", "approval_id": "appr_1",
                                   "readback": "I found 3 conversations. Shall I archive 3 conversations?"})
    assert text.startswith("CARD_RAISED: nothing has changed yet")
    assert "card_id=appr_1" in text


def test_a_refusal_reaches_the_model_as_words(monkeypatch):
    from agent_friday.services import item_actions as ia

    def refuse(*a, **k):
        raise ia.Refused("more than one page is called 'Dana': a; b. Which one?")
    monkeypatch.setattr(ia, "organize_wiki", refuse)
    out = agent._tool_organize_wiki({"action": "archive", "pages": ["Dana"]})
    assert out.startswith("NOT DONE: more than one page")
