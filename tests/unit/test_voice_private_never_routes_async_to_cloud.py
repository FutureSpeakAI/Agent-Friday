"""Private voice never sends deep work to the cloud (local voice spec P3).

With ``voice_async_routing = local_only`` and the brain parked for the call,
a deep question or a delegated job is queued for the end of the call (the
front says "after we hang up"), with zero model calls now; when the call
ends and the brain is back, each runs as a background task PINNED to the
brain. If the brain never comes back, nothing runs elsewhere and the
conversation is told. In Automatic the owner's routing applies.
"""
import pytest

import agent_friday.routes.voice as rv
from agent_friday.services import voice_engine as ve


@pytest.fixture
def spy(monkeypatch):
    calls = {"gen": 0, "spawn": []}

    def fake_generate(*a, **k):
        calls["gen"] += 1
        return "cloud answer", []

    def fake_spawn(name, prompt, **kw):
        calls["spawn"].append(dict(kw, name=name, prompt=prompt))
        return "t-1"
    monkeypatch.setattr("agent_friday.services.agent._generate_agent", fake_generate)
    monkeypatch.setattr("agent_friday.services.agent._spawn_task", fake_spawn)
    return calls


def _private():
    return {"engine": "local", "conversation_id": "c1", "async_routing": "local_only"}


def test_a_deep_question_waits_for_the_call_to_end(spy, monkeypatch):
    monkeypatch.setattr(ve, "_brain_serving", lambda: None)
    s = _private()
    out = ve._tool_ask_friday({"question": "summarise my notes on the move"}, s)
    assert out.startswith("QUEUED_AFTER_CALL") and "after you hang up" in out
    assert s["after_call"][0]["request"] == "summarise my notes on the move"
    assert spy["gen"] == 0 and spy["spawn"] == [], "nothing may run during the call"


def test_a_delegated_job_waits_too(spy, monkeypatch):
    monkeypatch.setattr(ve, "_brain_serving", lambda: None)
    s = _private()
    out = ve._tool_delegate_to_friday({"request": "draft the report"}, s)
    assert out.startswith("QUEUED_AFTER_CALL") and spy["spawn"] == []


def test_with_the_brain_serving_delegation_is_pinned_to_it(spy, monkeypatch):
    monkeypatch.setattr(ve, "_brain_serving", lambda: "bonsai2:27b")
    out = ve._tool_delegate_to_friday({"request": "draft the report"}, _private())
    assert out.startswith("DELEGATED:t-1")
    task, = spy["spawn"]
    assert task["model"] == "bonsai2:27b" and task["pin_to_seat"] is True
    assert task["conversation_id"] == "c1"


def test_after_the_call_each_question_runs_pinned_on_the_brain(spy, monkeypatch):
    states = iter([None, None, "bonsai2:27b"])
    monkeypatch.setattr(ve, "_brain_serving", lambda: next(states))
    items = [{"request": "summarise my notes", "title": "Notes"}]
    rv._run_after_call(items, "c1", wait_s=5, poll_s=0.01)
    import time
    end = time.monotonic() + 3
    while not spy["spawn"] and time.monotonic() < end:
        time.sleep(0.01)
    task, = spy["spawn"]
    assert task["model"] == "bonsai2:27b" and task["pin_to_seat"] is True
    assert task["conversation_id"] == "c1" and "summarise my notes" in task["prompt"]


def test_if_the_brain_never_returns_nothing_runs_and_the_owner_is_told(spy, monkeypatch):
    told = []
    monkeypatch.setattr(ve, "_brain_serving", lambda: None)
    monkeypatch.setattr(rv, "_persist_voice_turn",
                        lambda u, a, conversation_id=None, provider=None: told.append(a))
    rv._run_after_call([{"request": "x", "title": "x"}], "c1", wait_s=0.05, poll_s=0.01)
    import time
    end = time.monotonic() + 3
    while not told and time.monotonic() < end:
        time.sleep(0.01)
    assert spy["spawn"] == [] and spy["gen"] == 0
    assert told and "couldn't get to" in told[0]


def test_a_pinned_task_carries_the_pin_into_every_leg():
    import inspect
    from agent_friday.services import agent
    src = inspect.getsource(agent._task_worker_untraced)
    assert src.count('"pin_to_seat": _task_pinned(task_id)') >= 2


def test_automatic_hands_a_deep_question_to_the_owners_routing(spy, monkeypatch):
    monkeypatch.setattr(ve, "_brain_serving", lambda: None)
    monkeypatch.setattr(ve, "_voice_local_only", lambda: False)
    s = {"engine": "local", "conversation_id": "c1",
         "async_routing": "follow_model_routing"}
    out = ve._tool_ask_friday({"question": "q"}, s)
    assert out.startswith("DELEGATED:t-1")
    task, = spy["spawn"]
    assert not task.get("pin_to_seat"), "Automatic follows the owner's routing"
