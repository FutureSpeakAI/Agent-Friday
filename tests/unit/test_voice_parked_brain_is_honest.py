"""Local voice with the brain parked for the call: what the front says about a change it cannot run now.

Work handed to the deeper mind while the call has the brain parked is queued until the call ends
(services/voice_engine._queue_after_call). The spoken line must say that, in plain words, and must never promise
a bare "it'll report back" (which reads as "done soon", and on the live desktop ran a send and a file task
minutes after the owner had hung up). With the brain serving the line is unchanged.
"""
from __future__ import annotations

import agent_friday.routes.voice as rv
from agent_friday.services import laya_router as lr
from agent_friday.services import voice_engine as ve


def _defer(text, **kw):
    return lr.Route("defer", layer="rule", confidence=0.95, reason="an imperative that changes something")


def _plan(session):
    return rv._voice_route_plan("send the report to the team", ["delegate_to_friday"], route_fn=_defer,
                                session=session)


def test_a_change_with_the_brain_parked_says_it_waits_for_the_end_of_the_call(monkeypatch):
    monkeypatch.setattr(ve, "_brain_serving", lambda: None)
    plan = _plan({"engine": "local", "conversation_id": "c1", "async_routing": "local_only"})
    ack = plan["ack"].lower()
    assert plan["tool"] == "delegate_to_friday"
    assert plan["ack"] == rv.PARKED_ACK
    assert "after" in ack and "call" in ack and "approval" in ack
    assert "report back" not in ack


def test_a_change_with_the_brain_serving_keeps_the_usual_line(monkeypatch):
    monkeypatch.setattr(ve, "_brain_serving", lambda: "bonsai2:27b")
    plan = _plan({"engine": "local", "conversation_id": "c1", "async_routing": "local_only"})
    assert plan["ack"] == rv.DEFER_ACK


def test_a_cloud_voice_session_keeps_the_usual_line(monkeypatch):
    monkeypatch.setattr(ve, "_brain_serving", lambda: None)
    plan = _plan({"engine": "gemini", "conversation_id": "c1"})
    assert plan["ack"] == rv.DEFER_ACK
