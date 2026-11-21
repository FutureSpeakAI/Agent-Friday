"""Local voice with the brain parked for the call: what the front says about a change it cannot run now.

Work handed to the deeper mind while the call has the brain parked is queued until the call ends
(services/voice_engine._tool_delegate_to_friday -> _queue_after_call). The spoken line must say that, and must be
true: it is PARKED_ACK exactly when the work is queued and DEFER_ACK exactly when it is not (one predicate,
``voice_engine.delegate_waits_for_call_end``, decides both).
"""
from __future__ import annotations

import pytest

import agent_friday.routes.voice as rv
from agent_friday.services import laya_router as lr
from agent_friday.services import voice_engine as ve

PRIVATE = {"engine": "local", "conversation_id": "c1", "async_routing": "local_only"}
FOLLOWS_ROUTING = {"engine": "local", "conversation_id": "c1", "async_routing": "follow_model_routing"}
CLOUD = {"engine": "gemini", "conversation_id": "c1"}


def _defer(text, **kw):
    return lr.Route("defer", layer="rule", confidence=0.95, reason="an imperative that changes something")


def _plan(session):
    return rv._voice_route_plan("send the report to the team", ["delegate_to_friday"], route_fn=_defer,
                                session=session)


@pytest.fixture
def spawned(monkeypatch):
    calls = []
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "_spawn_task", lambda *a, **k: calls.append((a, k)) or "task-1")
    monkeypatch.setattr(ve, "_voice_local_only", lambda: False)
    return calls


def test_a_change_with_the_brain_parked_says_it_waits_for_the_end_of_the_call(monkeypatch, spawned):
    monkeypatch.setattr(ve, "_brain_serving", lambda: None)
    plan = _plan(PRIVATE)
    ack = plan["ack"].lower()
    assert plan["tool"] == "delegate_to_friday"
    assert plan["ack"] == rv.PARKED_ACK
    assert "after" in ack and "call" in ack and "approval" in ack
    assert "report back" not in ack


@pytest.mark.parametrize("session,serving", [
    (PRIVATE, "bonsai2:27b"),
    (PRIVATE, None),
    (FOLLOWS_ROUTING, None),
    (FOLLOWS_ROUTING, "bonsai2:27b"),
    (CLOUD, None),
])
def test_the_line_is_parked_exactly_when_the_work_is_queued(monkeypatch, spawned, session, serving):
    monkeypatch.setattr(ve, "_brain_serving", lambda: serving)
    out = ve._tool_delegate_to_friday({"request": "send the report to the team"}, dict(session))
    queued = out.startswith("QUEUED_AFTER_CALL")
    assert ve.delegate_waits_for_call_end(session) == queued
    assert _plan(dict(session))["ack"] == (rv.PARKED_ACK if queued else rv.DEFER_ACK)
    assert bool(spawned) == (not queued), "queued work must not start during the call; unqueued work must"


def test_a_call_that_follows_model_routing_with_the_brain_parked_keeps_the_usual_line(monkeypatch, spawned):
    monkeypatch.setattr(ve, "_brain_serving", lambda: None)
    assert _plan(FOLLOWS_ROUTING)["ack"] == rv.DEFER_ACK
