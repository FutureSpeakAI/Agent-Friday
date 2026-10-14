"""A2: the owner's spoken "quiet until morning" is internal; nothing else on the call can borrow that.

The voice router's rule-layer call carries the mark in ITS OWN governance context, built for that one call.
It is never written to the call's shared session dict, which every thread of the call can read, so a model's
tool call on the same session (the next utterance's front, an injected late result, a thread left over from
a cut-off turn) is judged as the model's: outward, a card.
"""
from __future__ import annotations

import threading
import time

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.routes import voice
from agent_friday.services import agent
from agent_friday.services import voice_engine as ve

QUIET = {"action": "quiet"}


@pytest.fixture
def seen(monkeypatch):
    """Replace the governed runner with one that records the context each call carries."""
    calls = []
    lock = threading.Lock()

    def fake(name, args, pii_lookup=None, session_ctx=None, handler=None):
        with lock:
            calls.append({"name": name, "args": dict(args), "ctx": dict(session_ctx or {})})
        gate = getattr(fake, "gate", None)
        if gate is not None and (session_ctx or {}).get("owner_utterance"):
            gate.wait(5)
        return "ok"
    monkeypatch.setattr(agent, "_execute_tool", fake)
    return calls, fake


def _class(call):
    return ag.classify(call["name"], call["args"], call["ctx"])[0]


def test_the_routers_rule_call_on_the_owners_voice_is_internal_with_no_card(seen):
    calls, _ = seen
    session = {"conversation_id": "c1"}
    ve._voice_tool_run("notifications", dict(QUIET), lambda f: None, session, owner_utterance=True)
    assert len(calls) == 1 and calls[0]["ctx"]["owner_utterance"] is True
    assert _class(calls[0]) == ag.INTERNAL
    assert "owner_utterance" not in session, "never written to the shared session"


def test_the_same_call_without_the_mark_is_a_models_and_keeps_the_outward_class(seen):
    calls, _ = seen
    ve._voice_tool_run("notifications", dict(QUIET), lambda f: None, {"conversation_id": "c1"})
    assert "owner_utterance" not in calls[0]["ctx"] and _class(calls[0]) == ag.OUTWARD


def test_a_session_that_carries_the_key_does_not_pass_it_into_a_calls_context():
    assert "owner_utterance" not in ve._voice_ctx({"owner_utterance": True, "conversation_id": "c1"})
    assert "owner_utterance" not in ve._voice_ctx({"conversation_id": "c1"})


def test_a_model_call_on_another_thread_during_the_owners_call_gets_a_card(seen):
    """The race the one-line repair would have opened: both calls run at once on one session."""
    calls, fake = seen
    fake.gate = threading.Event()
    session = {"conversation_id": "c1"}
    owner = threading.Thread(target=lambda: voice._local_voice_tool(
        "notifications", dict(QUIET), lambda f: None, session, cut=False, owner_call=True))
    owner.start()
    deadline = time.time() + 5
    while not any(c["ctx"].get("owner_utterance") for c in calls) and time.time() < deadline:
        time.sleep(0.01)
    assert any(c["ctx"].get("owner_utterance") for c in calls), "the owner's call is running"
    assert "owner_utterance" not in session, "while it runs, the shared session carries no mark"
    voice._local_voice_tool("notifications", dict(QUIET), lambda f: None, session, cut=False)   # a model's call
    fake.gate.set()
    owner.join(5)
    model = [c for c in calls if not c["ctx"].get("owner_utterance")]
    assert len(model) == 1 and _class(model[0]) == ag.OUTWARD, "the model's call is a card"
    assert [c for c in calls if c["ctx"].get("owner_utterance")] and "owner_utterance" not in session


def test_a_cut_off_turns_thread_never_hands_the_mark_to_a_later_call(seen):
    calls, fake = seen
    fake.gate = threading.Event()
    session = {"conversation_id": "c1"}
    out = voice._run_voice_tool_bounded(
        "notifications", dict(QUIET), lambda f: None, session, limit=0.05,
        runner=lambda f, a, s, sess: ve._voice_tool_run(f, a, s, sess, owner_utterance=True))
    assert "limit" in str(out).lower() or "second" in str(out).lower() or out, "the turn moved on without it"
    voice._local_voice_tool("notifications", dict(QUIET), lambda f: None, session, cut=False)
    fake.gate.set()
    time.sleep(0.1)
    later = [c for c in calls if not c["ctx"].get("owner_utterance")]
    assert later and all(_class(c) == ag.OUTWARD for c in later)
    assert "owner_utterance" not in session


def test_nothing_in_the_voice_route_writes_the_mark_to_shared_state():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    for rel in ("src/agent_friday/routes/voice.py", "src/agent_friday/services/voice_engine.py"):
        text = (root / rel).read_text(encoding="utf-8")
        for bad in ('_tool_session["owner_utterance"]', "session['owner_utterance']", 'session["owner_utterance"]',
                    "_tool_session.setdefault(\"owner_utterance\""):
            assert bad not in text, (rel, bad)


def test_the_routers_call_uses_its_own_runner_and_other_calls_do_not(monkeypatch):
    got = []
    monkeypatch.setattr(voice, "_voice_tool_run",
                        lambda n, a, s, sess=None, owner_utterance=False: got.append(owner_utterance) or "ok")
    monkeypatch.setattr(voice, "_voice_orb_start", lambda n: None)
    monkeypatch.setattr(voice, "_voice_orb_finish", lambda *a, **k: None)
    sess = {}
    voice._local_voice_tool("notifications", dict(QUIET), lambda f: None, sess, cut=False, owner_call=True)
    voice._local_voice_tool("notifications", dict(QUIET), lambda f: None, sess, cut=False)
    assert got == [True, False] and sess == {}


# ── the routed local voice turn: the mark is the router's rule call on the owner's own voice, once ──

def _routed_turn(monkeypatch, owner_voice, calls=2, text="quiet until morning"):
    """Run the real routed turn with a seat that asks for the planned tool `calls` times."""
    from agent_friday.services import laya2_encoder, laya_router
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)
    monkeypatch.setattr(laya_router, "_write", lambda *a, **k: None)
    got = []
    monkeypatch.setattr(voice, "_local_voice_tool",
                        lambda n, a, send, session, cut=True, owner_call=False: got.append((n, dict(a), owner_call)) or "ok")

    def seat_turn(user_text, on_delta, plan, run_tool, card_line=None):
        for _ in range(calls if plan["tool"] else 0):
            run_tool(plan["tool"], dict(plan["args"] or {}))
        run_tool("notifications", {"action": "quiet", "extra": 1})      # a model's different call
        return "ok"
    voice._run_routed_front_turn(text, lambda d: None, front={"contract": {"names": ["notifications"]}},
                                 tool_session={}, seat_turn=seat_turn, send=lambda o: None,
                                 session=None, owner_voice=owner_voice)
    return got


def test_the_owners_voice_marks_the_routers_rule_call_once_and_nothing_else(monkeypatch):
    got = _routed_turn(monkeypatch, owner_voice=True)
    assert [g[0] for g in got] == ["notifications"] * 3
    assert [g[2] for g in got] == [True, False, False]


def test_a_voice_that_is_not_the_owners_on_this_pc_marks_nothing(monkeypatch):
    got = _routed_turn(monkeypatch, owner_voice=False)
    assert [g[2] for g in got] == [False, False, False]


def test_a_phrase_that_is_not_the_quiet_rule_marks_nothing(monkeypatch):
    got = _routed_turn(monkeypatch, owner_voice=True, text="what time is it")
    assert all(g[2] is False for g in got)
