"""Audit B5: chat and voice can stop a running task or workflow, or send a running task a message.

Stopping only ends work, so it is ring 1 with no card. A steer's words are checked for where they came from like
any instruction. A cloud voice never hears a task's name.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent, step_lists, taint
from agent_friday.services import task_journal as tj
from tests.unit.test_workflow_cancel_route import FakeSpawn, _finish


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "WORKFLOWS_DIR", tmp_path / "workflows")
    spawn = FakeSpawn()
    monkeypatch.setattr(agent, "_spawn_task", spawn)
    with agent.TASKS_LOCK:
        agent.TASKS.clear()
    agent._CHAIN_STOP.clear()
    agent._CHAIN_STOPPED.clear()
    tj._STOP_REQUESTED.clear()
    step_lists.reset()
    monkeypatch.setattr(tj, "write_state", lambda *a, **k: None)
    monkeypatch.setattr(tj, "read_state", lambda *a, **k: None)
    steers = []
    monkeypatch.setattr(tj, "steer", lambda msg, source="", task_id=None, **k: steers.append((msg, source, task_id)))
    monkeypatch.setattr(agent, "_cloud_voice", lambda: False)
    monkeypatch.setattr(agent, "_voice_room", lambda: False)
    agent.save_workflow_chain({"name": "Morning brief", "steps": [{"name": "Gather", "prompt": "a"}, {"name": "Write", "prompt": "b"}]})
    yield spawn, steers
    with agent.TASKS_LOCK:
        agent.TASKS.clear()
    agent._CHAIN_STOP.clear()
    agent._CHAIN_STOPPED.clear()
    tj._STOP_REQUESTED.clear()
    step_lists.reset()


def _task(tid, name, status="running"):
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"task_id": tid, "name": name, "description": "", "status": status, "created": time.time(),
                            "started": time.time(), "ended": None, "log": [], "result": "", "chain": None, "chain_step": 0}


def test_stop_with_no_target_stops_the_running_workflow(world):
    tid = agent.run_workflow_chain("Morning brief")
    out = agent._tool_task_control({"op": "stop"})
    assert out.startswith("TASK_STOPPED:") and tj.stop_requested(tid)


def test_stop_names_a_workflow(world):
    tid = agent.run_workflow_chain("Morning brief")
    out = agent._tool_task_control({"op": "stop", "target": "Morning brief"})
    assert out.startswith("TASK_STOPPED:") and "next never starts" in out and tj.stop_requested(tid)


def test_stop_names_a_task_by_words_and_asks_it_to_stop_after_its_step(world):
    _task("a1", "Research flights to Lisbon")
    _task("b2", "Tidy the downloads folder")
    out = agent._tool_task_control({"op": "stop", "target": "lisbon"})
    assert out.startswith("TASK_STOPPED:") and "Research flights" in out
    assert tj.stop_requested("a1") and not tj.stop_requested("b2")


def test_stop_with_two_tasks_running_asks_which(world):
    _task("a1", "Research flights")
    _task("b2", "Tidy downloads")
    out = agent._tool_task_control({"op": "stop"})
    assert out.startswith("NOT DONE:") and "2 tasks" in out
    assert not tj.stop_requested("a1") and not tj.stop_requested("b2")


def test_stop_with_nothing_running_says_so(world):
    assert agent._tool_task_control({"op": "stop"}).startswith("TASK_NONE:")
    assert agent._tool_task_control({"op": "stop", "target": "nothing like it"}).startswith("TASK_NONE:")


def test_a_single_running_task_is_stopped_without_naming_it(world):
    _task("a1", "Research flights")
    assert agent._tool_task_control({"op": "stop"}).startswith("TASK_STOPPED:")
    assert tj.stop_requested("a1")


def test_steer_sends_the_words_to_the_one_task_it_names(world):
    spawn, steers = world
    _task("a1", "Research flights")
    out = agent._tool_task_control({"op": "steer", "target": "flights", "message": "only direct ones"})
    assert out.startswith("TASK_STEERED:")
    assert steers == [("only direct ones", "agent:friday", "a1")]
    assert agent._FOLLOW_UP_QUEUES["a1"][-1] == "only direct ones"


def test_steer_needs_words_and_exactly_one_task(world):
    _task("a1", "Research flights")
    _task("b2", "Tidy downloads")
    assert agent._tool_task_control({"op": "steer", "target": "flights"}).startswith("NOT DONE:")
    assert "2 tasks" in agent._tool_task_control({"op": "steer", "message": "hurry"})
    assert agent._tool_task_control({"op": "pause"}).startswith("NOT DONE:")


def test_a_steer_is_checked_for_where_its_words_came_from_and_stopping_needs_no_card():
    assert taint.TOOL_ROLES["task_control"] == {"message": "instruction"}
    assert action_gate.classify("task_control", {"op": "stop"})[0] == action_gate.INTERNAL


def test_a_cloud_voice_never_hears_the_task_name(world, monkeypatch):
    monkeypatch.setattr(agent, "_cloud_voice", lambda: True)
    _task("a1", "Quarterly Harbor Legal review")
    out = agent._tool_task_control({"op": "stop", "target": "harbor"})
    assert out.startswith("TASK_STOPPED:") and "Harbor" not in out
    assert "Harbor" not in agent._tool_task_control({"op": "stop", "target": "zzz"})
