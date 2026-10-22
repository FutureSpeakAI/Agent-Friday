"""A task moved to the cloud keeps every limit it had (the goals lane's finding).

Before this change cloud_spill.on_decision respawned an approved spill with only the
name, prompt, description, chain and the cloud model. The subagent scope, tool list,
follow-up, conversation, schedule grant scope, workflow and crew context, the cloud/local
policy and the outside-content flag were all dropped, so a goal milestone (scope
'goal-milestone') queued for the local seat and moved to the cloud ran unscoped, with
every tool.

Now the spill carries the same limits requeue_task does (agent._descendant_options plus
tools, follow-up and crew context), a scope that cannot be applied leaves the task in its
queue, and a task whose limits a cloud run cannot honour (pinned to its seat, local-only,
result promised to stay on this PC, off the record, crew work) is neither offered nor moved.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cloud_spill as cs


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(cs, "_settings", lambda: {
        "capability_routing": {"subagent": {"model": "claude-sonnet-5"}}})
    monkeypatch.setattr(cs, "spend_context", lambda: {"today_usd": 1.0, "hard_stop": None})


def _queued(**extra):
    rec = {"id": "t-9", "task_id": "t-9", "name": "Goal - draft the brief",
           "status": "queued-for-seat", "seat": "local/bonsai2:27b", "seat_is_local": True,
           "model": "bonsai2:27b", "prompt": "draft it", "description": "goal:g1:milestone:m1",
           "conversation_id": "conv-9", "scope": "goal-milestone",
           "tools": ["read_file", "write_file"], "on_complete": {"spawn": "next"},
           "schedule_id": "sch-1", "cloud_pin": None, "local_only": None}
    rec.update(extra)
    return rec


def _card():
    return {"kind": cs.KIND, "status": "approved", "approval_id": "ap-9",
            "payload": {"task_id": "t-9", "cloud_model": "claude-sonnet-5"}}


@pytest.fixture
def agent(monkeypatch):
    import agent_friday.services.agent as ag
    monkeypatch.setattr(ag, "_seat_supervisor", lambda: type(
        "S", (), {"on_task_end": staticmethod(lambda *a, **k: None),
                  "wire_spawn": staticmethod(lambda rec: "running")})())
    yield ag
    with ag.TASKS_LOCK:
        ag.TASKS.pop("t-9", None)
    with ag._PENDING_TASK_THREADS_LOCK:
        ag._PENDING_TASK_THREADS.pop("t-9", None)


def test_a_spilled_goal_task_keeps_its_scope_tools_and_owner(agent, monkeypatch):
    spawned = {}
    monkeypatch.setattr(agent, "_spawn_task",
                        lambda name, prompt, **kw: spawned.update(name=name, prompt=prompt, **kw) or "new-9")
    with agent.TASKS_LOCK:
        agent.TASKS["t-9"] = _queued()
    cs.on_decision(_card())
    assert spawned["model"] == "claude-sonnet-5"
    assert spawned["scope"] == "goal-milestone", "the spilled goal task ran unscoped"
    assert spawned["tools"] == ["read_file", "write_file"]
    assert spawned["on_complete"] == {"spawn": "next"}
    assert spawned["conversation_id"] == "conv-9"
    assert spawned["schedule_id"] == "sch-1"
    # The outside-content flag and the cloud/local policy travel through the parent.
    assert spawned["parent_task_id"] == "t-9"
    assert "inherited_policy" in spawned
    assert spawned["pin_to_seat"] is False and "task_id" not in spawned


@pytest.mark.parametrize("limit", [
    {"pin_to_seat": True}, {"local_only": True}, {"local_only_result": True},
    {"off_record": True}, {"crew_context": {"agent_id": "a"}},
])
def test_limits_a_cloud_run_cannot_honour_keep_it_waiting(agent, monkeypatch, limit):
    monkeypatch.setattr(agent, "_spawn_task", lambda *a, **k: pytest.fail("moved to the cloud"))
    rec = _queued(**limit)
    assert cs.should_offer(rec, wait_s=600) is False
    with agent.TASKS_LOCK:
        agent.TASKS["t-9"] = rec
    cs.on_decision(_card())
    with agent.TASKS_LOCK:
        assert agent.TASKS["t-9"]["status"] == "queued-for-seat"


def test_a_scope_that_cannot_be_applied_leaves_the_task_in_its_queue(agent, monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("scope 'goal-milestone' could not be applied")
    monkeypatch.setattr(agent, "_spawn_task", refuse)
    parked = object()
    with agent.TASKS_LOCK:
        agent.TASKS["t-9"] = _queued()
    with agent._PENDING_TASK_THREADS_LOCK:
        agent._PENDING_TASK_THREADS["t-9"] = parked
    cs.on_decision(_card())
    with agent._PENDING_TASK_THREADS_LOCK:
        assert agent._PENDING_TASK_THREADS.get("t-9") is parked, "the queued worker was lost"
    with agent.TASKS_LOCK:
        assert agent.TASKS["t-9"]["status"] == "queued-for-seat"


def test_the_real_spawn_applies_the_scope_to_the_new_task(agent, monkeypatch):
    """End to end through the real _spawn_task: the new task id is scoped."""
    from agent_friday.services import subagents
    started = []
    monkeypatch.setattr(agent.threading.Thread, "start", lambda self: started.append(self))
    with agent.TASKS_LOCK:
        agent.TASKS["t-9"] = _queued(tools=None, on_complete=None, schedule_id=None)
    cs.on_decision(_card())
    with agent.TASKS_LOCK:
        new = [t for t, r in agent.TASKS.items() if r.get("parent_task_id") == "t-9"]
        assert agent.TASKS["t-9"]["status"] == "superseded"
    assert new, "nothing was spawned"
    try:
        sc = subagents.get_task_scope(new[0])
        assert sc is not None and getattr(sc, "name", "") == "goal-milestone"
    finally:
        with agent.TASKS_LOCK:
            for t in new:
                agent.TASKS.pop(t, None)
