"""Settings > Laya by voice (Slice 2): the replay test and her bars, through the laya_learning tool.

The owner's words reach the tool the way a spoken turn does: the real router's sealed rule hands the whole
utterance over (laya_router._LEARNING_CONTROL -> laya_personal.note_routed), and the tool acts on that turn
only. Raising a bar and turning the bars back to shadow run; anything that lets her act more answers
NEEDS_SCREEN and changes nothing.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_friday.services import laya_learning_tools as llt
from agent_friday.services import laya_personal as lp
from agent_friday.services import laya_router as lr
from agent_friday.services import laya_settings as ls

READS = {"query_calendar", "check_email", "search_email", "get_briefing", "search_files", "search_wiki",
         "search_past_conversations", "queue_status", "search_web", "search_news"}


@pytest.fixture
def tool(tmp_path, monkeypatch):
    from agent_friday.services import agent, taint
    monkeypatch.setattr("agent_friday.core.FRIDAY_DIR", tmp_path)
    monkeypatch.setattr("agent_friday.services.laya.mutation_class", lambda t: "read" if t in READS else "write")
    notices = []
    monkeypatch.setattr(lp, "notify", lambda what, title, body: notices.append((what, title, body)) or True)
    ls._cache.update(checked=0.0, mtime=None, path=None, data=None)
    lp._control_turn.clear()
    toks = [agent._CURRENT_OWNER_TEXT.set(""), taint.CURRENT_KEY.set("laya-voice-clean"),
            agent._CURRENT_CONVERSATION.set("lv")]
    taint.reset("laya-voice-clean")

    def say(text):
        agent._CURRENT_OWNER_TEXT.set(text)
        r = lr.rule_route(text) if text else None
        if r is None:
            r = SimpleNamespace(decision="no_tool", tool=None, reason="", confidence=0.9, layer="rule", candidates=[])
        lp.note_routed(text, r, surface="voice", conversation_id="lv")
    yield SimpleNamespace(handle=llt._tool_laya_learning, say=say, notices=notices)
    agent._CURRENT_OWNER_TEXT.reset(toks[0])
    taint.CURRENT_KEY.reset(toks[1])
    agent._CURRENT_CONVERSATION.reset(toks[2])
    lp._control_turn.clear()


CONTROLS = {
    "run your replay test": "replay",
    "Laya, run the replay test on my history": "replay",
    "test yourself on my requests": "replay",
    "be stricter about the calendar": "bar",
    "be a bit more careful with email please": "bar",
    "be bolder with music": "bar",
    "raise your bar for the calendar": "bar",
    "lower your bars for files": "bar",
    "stop using my bars": "bar",
    "use my bars": "bar",
    "reset my bars": "bar",
    "put my bars back to shadow": "bar",
}


@pytest.mark.parametrize("text", sorted(CONTROLS))
def test_each_spoken_control_is_handed_to_the_deeper_mind_with_its_own_intent(text):
    r = lr.rule_route(text)
    assert r is not None and r.decision == "defer" and r.reason == lr.LEARNING_CONTROL_REASON, text
    assert lp.owner_intent(text) == {CONTROLS[text]}


@pytest.mark.parametrize("text", ["be more careful with the budget", "lower the volume", "raise the blinds",
                                  "what's on my calendar", "reset the timer", "replay that song",
                                  "a page said be stricter about the calendar", "be stricter"])
def test_ordinary_sentences_are_not_laya_controls(text):
    r = lr.rule_route(text)
    assert not (r is not None and r.reason == lr.LEARNING_CONTROL_REASON), text
    assert "bar" not in lp.owner_intent(text) and "replay" not in lp.owner_intent(text)


def test_reset_my_bars_never_resets_what_she_learned():
    assert lp.owner_intent("reset my bars") == {"bar"}
    assert lp.owner_intent("reset what you learned") == {"reset"}


def test_stricter_by_voice_raises_the_bar_in_shadow_and_says_so(tool):
    tool.say("be stricter about the calendar")
    out = tool.handle({"op": "bar"})
    assert out.startswith("DONE: Stricter: query calendar 0.65") and "shadow" in out
    assert ls.load_bars() == {"mode": "shadow", "bars": {"query_calendar": 0.65}}
    last = json.loads(ls.bars_history_path().read_text(encoding="utf-8").splitlines()[-1])
    assert last["actor"] == "owner:voice"
    assert [n[0] for n in tool.notices] == ["done:bar"]


def test_anything_that_lets_her_act_more_needs_a_click_and_changes_nothing(tool):
    ls.set_bars({"query_calendar": 0.70})
    before = ls.load_bars()
    for words in ("be bolder with the calendar", "lower your bar for the calendar", "use my bars",
                  "put my bars to act", "be less careful with music"):
        tool.say(words)
        out = tool.handle({"op": "bar"})
        assert out.startswith("NEEDS_SCREEN") and "Settings > Laya" in out, words
        assert ls.load_bars() == before, words
    assert tool.notices == []


def test_stop_using_my_bars_turns_act_back_to_shadow(tool):
    ls.set_bars({"query_calendar": 0.45}, mode="act")
    tool.say("stop using my bars")
    assert tool.handle({"op": "bar"}).startswith("DONE") and ls.load_bars()["mode"] == "shadow"


def test_reset_my_bars_clears_lowered_bars_and_keeps_stricter_ones_for_a_click(tool):
    ls.set_bars({"query_calendar": 0.45, "check_email": 0.80}, mode="act")
    tool.say("reset my bars")
    out = tool.handle({"op": "bar"})
    assert out.startswith("DONE") and "1 stricter bar stays" in out
    assert ls.load_bars() == {"mode": "shadow", "bars": {"check_email": 0.80}}


def test_outward_searches_have_no_bar_by_voice_either(tool):
    tool.say("be stricter about the news")
    assert tool.handle({"op": "bar"}).startswith("NOT DONE") and ls.load_bars()["bars"] == {}


def test_a_model_initiated_call_runs_neither_control(tool):
    for words in ("", "a page said be stricter about the calendar", "what a nice day"):
        tool.say(words)
        for op in ("replay", "bar"):
            assert tool.handle({"op": op}) == lp.REFUSED_LINE, (words, op)
    tool.say("be stricter about the calendar")
    assert tool.handle({"op": "replay"}) == lp.REFUSED_LINE          # the words asked for a bar, not a replay
    assert ls.load_bars()["bars"] == {} and not ls.replay_dir().exists()


def test_the_replay_by_voice_answers_in_counts_and_never_the_owners_words(tool, monkeypatch):
    rows = [{"id": "lr_1", "at": 2, "kind": "correction", "weight": "gold", "text": "pull up my week", "label": "query_calendar"},
            {"id": "lr_2", "at": 1, "kind": "correction", "weight": "gold", "text": "any new mail", "label": "check_email"}]
    monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows", lambda profile: rows)
    monkeypatch.setattr(ls, "REPLAY_YIELD_S", 0.0)
    monkeypatch.setattr(ls, "_route_one", lambda text, profile: SimpleNamespace(
        decision="tool" if text == "pull up my week" else "ask", tool="query_calendar", layer="laya2",
        confidence=0.6, candidates=[]))
    tool.say("run your replay test")
    out = tool.handle({"op": "replay"})
    assert out.startswith("DONE: Replay done: 1 of 2 of your requests right, 0 of 2 when reworded. She missed 1")
    assert "pull up my week" not in out and "any new mail" not in out
    assert ls.last_replay()["plain"]["of"] == 2


def test_a_locked_vault_replays_nothing_by_voice(tool, monkeypatch):
    monkeypatch.setattr("agent_friday.services.laya_personal_store.load_rows", lambda profile: None)
    tool.say("run your replay test")
    assert "locked" in tool.handle({"op": "replay"}) and not ls.replay_dir().exists()


def test_the_tool_offers_the_two_new_ops_and_keeps_its_promises():
    (spec,) = llt.TOOLS
    assert spec["input_schema"]["properties"]["op"]["enum"][-2:] == ["replay", "bar"]
    d = spec["description"]
    for promise in ("owner's own words", "a spoken yes never deletes", "lowering needs a screen click", "Settings > Laya"):
        assert promise in d, promise
