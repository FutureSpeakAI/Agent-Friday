"""Eval: "plan a weekend for my family and add it to my calendar", spoken,
with the routing mode cloud_only and the cloud consent answered unrestricted.

The voice model cannot create calendar events itself; a request this size is
handed to Friday (`delegate_to_friday`), and a question for her own memory
goes to the deeper mind (`ask_friday`). Both run through the real voice tool
runner (`voice_engine._voice_tool_run`), which keeps what each call returned
for the turn. When the turn is saved, the words that were spoken are checked
against those receipts by the same write-claim law the typed chat uses: a
spoken "I've added it to your calendar" with no calendar write that succeeded
this turn is saved saying it was NOT added, and the receipts ride into later
turns' history. The cloud voice bridge runs the same check on every flushed
turn and hands the live model the correction to say aloud.
"""
from __future__ import annotations

import inspect

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import agent as ag
from agent_friday.services import voice_engine as ve

PROMPT = "plan a weekend for my family and add it to my calendar"
PLAN = "Saturday is a picnic at Riverside Park and Sunday is the zoo."
SAID_ADDED = PLAN + " I've added both to your calendar."
SAID_HANDED = ("That's a good one for Friday to plan properly. I've handed it over, "
               "and she'll add the events to your calendar once the plan is ready.")
DENIED = "[VAULT-ZT DENY] cloud_denied_tier_TIER_2"


@pytest.fixture
def cloud_only():
    from agent_friday import core
    s = core._load_settings()
    mr = dict(s.get("model_routing") or {})
    mr.update({"mode": "cloud_only", "vault_local_only": False,
               "cloud_consent": {"answered": True, "choice": "cloud_unrestricted",
                                 "at": "2026-09-06T00:00:00+00:00",
                                 "capability_snapshot": None}})
    s["model_routing"] = mr
    core._save_settings(s, _internal_cloud_consent_write=True)
    from agent_friday.services import conversations
    return conversations.create("Voice eval")["id"]


@pytest.fixture
def spawned(monkeypatch):
    out = []

    def _spawn(name, prompt, **kw):
        out.append({"name": name, "prompt": prompt, **kw})
        return "task_eval_%d" % len(out)
    monkeypatch.setattr(ag, "_spawn_task", _spawn)
    return out


@pytest.fixture
def deeper_mind_denied(monkeypatch):
    """The deeper mind plans the weekend and its calendar writes are refused
    by the vault's cloud rule."""
    from agent_friday.services import local_seats
    monkeypatch.setattr(local_seats, "resolve", lambda *a, **k: "brain-eval-seat")
    monkeypatch.setattr(ve, "_local_session", lambda s: False)
    monkeypatch.setattr(rv, "_build_voice_system_prompt",
                        lambda *a, **k: ("system", {"volatile": ""}))

    def _agent(messages, **kw):
        return ("I planned the weekend.", [
            {"name": "create_calendar_event",
             "input": {"title": "Picnic at Riverside Park"}, "result": DENIED},
            {"name": "create_calendar_event", "input": {"title": "Zoo"}, "result": DENIED}])
    monkeypatch.setattr(ag, "_generate_agent", _agent)


def _session(cid):
    return {"conversation_id": cid, "spoken": []}


def _saved_reply(cid):
    from agent_friday.services import conversations
    return [m for m in conversations.messages(cid) if m.get("role") == "friday"][-1]


def test_the_request_is_handed_over_and_a_spoken_add_is_saved_as_not_done(
        cloud_only, spawned):
    session = _session(cloud_only)
    res = ve._voice_tool_run("delegate_to_friday",
                             {"request": PROMPT, "title": "Weekend plan"},
                             lambda f: None, session)
    assert str(res).startswith("DELEGATED:"), res
    assert len(spawned) == 1 and PROMPT in spawned[0]["prompt"], \
        "the work is CALLED (handed to Friday), not described"

    # The voice model then claims the events are on the calendar. Nothing that
    # writes a calendar ran this turn: the hand-over is not the write.
    rv._persist_local_turn(session, PROMPT, SAID_ADDED, cloud_only)
    saved = _saved_reply(cloud_only)
    assert "I've added both to your calendar" not in saved["text"]
    assert saved["text"] == PLAN + (" I have not added this to your calendar yet — "
                                    "no calendar write succeeded this turn.")
    assert [r["tool"] for r in saved["meta"]["receipts"]] == ["delegate_to_friday"]


def test_an_honest_hand_over_is_saved_as_said(cloud_only, spawned):
    session = _session(cloud_only)
    ve._voice_tool_run("delegate_to_friday", {"request": PROMPT}, lambda f: None, session)
    rv._persist_local_turn(session, PROMPT, SAID_HANDED, cloud_only)
    assert _saved_reply(cloud_only)["text"] == SAID_HANDED


def test_a_denied_create_by_the_deeper_mind_is_saved_as_not_done(cloud_only, deeper_mind_denied):
    session = _session(cloud_only)
    ve._voice_tool_run("ask_friday", {"question": PROMPT}, lambda f: None, session)
    rv._persist_local_turn(session, PROMPT, SAID_ADDED, cloud_only)
    saved = _saved_reply(cloud_only)
    assert saved["text"] == PLAN + (" I have not added this to your calendar yet — "
                                    "the calendar write was denied this turn.")
    statuses = [(r["tool"], r["status"]) for r in saved["meta"]["receipts"]]
    assert statuses.count(("create_calendar_event", "denied")) == 2


def test_a_later_typed_turn_reads_what_the_voice_turn_did(cloud_only, deeper_mind_denied):
    from agent_friday.routes import chat as chat_mod
    session = _session(cloud_only)
    ve._voice_tool_run("ask_friday", {"question": PROMPT}, lambda f: None, session)
    rv._persist_local_turn(session, PROMPT, SAID_ADDED, cloud_only)
    replies = [m["content"] for m in chat_mod._conv_context(cloud_only)
               if m["role"] == "assistant"]
    assert replies[-1].startswith(
        "[Tool receipts for this reply: create_calendar_event x2 DENIED (vault): "
        "Picnic at Riverside Park; Zoo | ask_friday ok]\n")


def test_the_receipts_are_spent_with_the_turn(cloud_only, spawned):
    session = _session(cloud_only)
    ve._voice_tool_run("delegate_to_friday", {"request": PROMPT}, lambda f: None, session)
    rv._persist_local_turn(session, PROMPT, SAID_HANDED, cloud_only)
    # Next turn: no tool ran, so the hand-over above backs nothing here.
    rv._persist_local_turn(session, "and the zoo?", SAID_ADDED, cloud_only)
    assert "I have not added" in _saved_reply(cloud_only)["text"]


def test_the_live_call_checks_every_flushed_turn():
    """The cloud voice bridge's turn flush runs the guard before it saves the
    turn, and queues the spoken correction (routes/voice.py _flush_turn)."""
    src = inspect.getsource(rv)
    body = src[src.index("def _flush_turn("):]
    body = body[:body.index("_persist_voice_turn(user_text")]
    assert "_guard_voice_reply(" in body
    assert "_inject_q.append((_note, \"notice\"))" in body
