"""A spoken approval decides an approval card only if the card's question was
voiced on this session, or the owner's own words name the card's action.

Gemini Live: the gate used to refuse only a yes that fully matched the router's
anchored pattern, so "yes, Friday", "yeah sure", "uh, yes" and "yes yes" reached
``decide_by_voice`` (an unanchored search) and approved a card nobody had heard.
The local deep-agent path (surface "voice-local") had no readback check at all.
Both now ask one helper (``item_actions.unvoiced_card_approval_blocked``); a
refusal ("no") is never blocked; a verified Live card is good for the very next
owner turn only.

No session, no network: the tool runner and the card store are stubs.
"""
import asyncio

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import agent as ag
from agent_friday.services import approvals as ap
from agent_friday.services import item_actions as ia
from agent_friday.services import voice_engine

CALL = "conv-live"
CARD = "apr_live"


def _card():
    return {"approval_id": CARD, "status": "pending", "payload": {
        "handler": ia.HANDLER, "conversation_id": CALL, "action": "archive", "count": 3,
        "label": "", "preview": [{"subject": "Lunch plans", "sender": "Mara Quinn"}],
        "lines": ["Lunch plans"]}}


@pytest.fixture
def store(monkeypatch):
    cards = [_card()]
    monkeypatch.setattr(ap, "list_approvals", lambda status=None, **kw: list(cards))
    monkeypatch.setattr(ap, "get_approval", lambda i: next((c for c in cards if c["approval_id"] == i), None))
    monkeypatch.setattr(ia, "_quiet", lambda: False)
    return cards


class _Live:
    def __init__(self, monkeypatch, room=False):
        self.ran = []
        self.session = {"conversation_id": CALL, "owner_text": "", "card_watch": rv._LiveCardWatch()}
        monkeypatch.setattr(voice_engine, "_voice_room_mode", lambda: room)

        async def run(fname, fargs, send, session=None, limit=None, runner=None):
            self.ran.append((fname, dict(fargs)))
            return "Recorded."
        monkeypatch.setattr(rv, "_voice_tool_with_limit", run)

    def tool(self, name, args=None):
        return asyncio.run(rv._live_card_tool(name, args or {}, lambda o: True, self.session))

    def say(self, words, decision="approve"):
        self.session["owner_text"] = words
        return self.tool("answer_card", {"card_id": CARD, "decision": decision})

    def answered(self):
        return [r for r in self.ran if r[0] == "answer_card"]

    def voice_the_question(self):
        """The card's sealed line, said whole by Friday."""
        _ids, line = rv._sealed_card_line("card_id=" + CARD, CALL)
        self.session["card_watch"].arm([CARD], line)
        self.session["card_watch"].heard(line)


@pytest.fixture
def live(monkeypatch, store):
    return lambda **kw: _Live(monkeypatch, **kw)


def test_yes_friday_in_room_mode_approves_nothing_on_live(live):
    lv = live(room=True)
    out = lv.say("yes, Friday")
    assert out.startswith("NOT RECORDED") and lv.answered() == []


@pytest.mark.parametrize("words", ["yeah sure", "uh, yes", "yes yes", "Yes, please do.", "yep"])
def test_a_yes_in_any_wording_approves_nothing_for_an_unvoiced_card_on_live(live, words):
    lv = live()
    out = lv.say(words)
    assert out.startswith("NOT RECORDED") and lv.answered() == [], words


def test_naming_the_action_still_approves_on_live(live):
    lv = live()
    assert lv.say("yes, archive them") == "Recorded."
    assert len(lv.answered()) == 1


def test_a_different_action_named_does_not_count(live):
    lv = live()
    assert lv.say("yes, delete them").startswith("NOT RECORDED")
    assert lv.answered() == []


def test_a_no_still_denies_an_unvoiced_card_on_live(live):
    lv = live()
    assert lv.say("no, leave them", decision="decline") == "Recorded."
    assert lv.answered() == [("answer_card", {"card_id": CARD, "decision": "decline"})]


def test_a_voiced_card_takes_a_bare_yes_on_the_very_next_turn(live):
    lv = live()
    lv.voice_the_question()
    lv.session["card_watch"].owner_turn()                 # the owner starts answering
    assert lv.say("yes") == "Recorded."
    assert lv.session["voiced_cards"] == [CARD]           # the agent's rule is told


def test_a_voiced_card_is_stale_two_owner_turns_later(live):
    lv = live()
    lv.voice_the_question()
    lv.session["card_watch"].owner_turn()
    lv.session["card_watch"].owner_turn()                 # a different utterance in between
    out = lv.say("yes")
    assert out.startswith("NOT RECORDED") and lv.answered() == []


# ---- the local deep-agent path (surface "voice-local") ----------------------

class _Deep:
    """``agent._tool_answer_card`` as the deep agent loop calls it."""

    def __init__(self, monkeypatch, voiced=()):
        self.decided = []
        monkeypatch.setattr(ag, "_voice_room", lambda: False)
        monkeypatch.setattr(ap, "decide_with_outcome",
                            lambda i, v, decided_by="": (self.decided.append((i, v)) or ({"status": v}, True)))
        monkeypatch.setattr(ia, "wait_for", lambda i, t: {"receipt_id": "r1"})
        monkeypatch.setattr(ia, "_said", lambda done: "ok")
        self.ctx = {"voiced_cards": list(voiced)} if voiced else {}

    def call(self, words, decision="approve"):
        toks = [ag._CURRENT_SURFACE.set("voice-local"), ag._CURRENT_OWNER_TEXT.set(words),
                ag._CURRENT_TOOL_CONTEXT.set(dict(self.ctx))]
        try:
            return ag._tool_answer_card({"card_id": CARD, "decision": decision})
        finally:
            for var, tok in zip((ag._CURRENT_SURFACE, ag._CURRENT_OWNER_TEXT, ag._CURRENT_TOOL_CONTEXT), toks):
                var.reset(tok)


def test_the_voice_local_deep_agent_approves_nothing_for_an_unvoiced_card(monkeypatch, store):
    d = _Deep(monkeypatch)
    for words in ("yes", "yeah sure", "uh, yes", "yes, Friday"):
        assert d.call(words).startswith("NOT RECORDED"), words
    assert d.decided == []


def test_the_voice_local_deep_agent_approves_when_the_words_name_the_action(monkeypatch, store):
    d = _Deep(monkeypatch)
    assert d.call("yes, archive them").startswith("Approved")
    assert d.decided == [(CARD, "approve")]


def test_the_voice_local_deep_agent_approves_a_card_the_call_voiced(monkeypatch, store):
    d = _Deep(monkeypatch, voiced=[CARD])
    assert d.call("yes").startswith("Approved")
    assert d.decided == [(CARD, "approve")]


def test_the_voice_local_deep_agent_still_declines_on_a_no(monkeypatch, store):
    d = _Deep(monkeypatch)
    assert d.call("no", decision="decline").startswith("Recorded: declined")
    assert d.decided == [(CARD, "deny")]


def test_the_call_session_hands_its_voiced_cards_to_the_tool_context():
    ctx = voice_engine._voice_ctx({"conversation_id": CALL, "owner_text": "yes",
                                   "voiced_cards": [CARD]})
    assert ctx["voiced_cards"] == [CARD]
    assert "voiced_cards" not in voice_engine._voice_ctx({"conversation_id": CALL})
