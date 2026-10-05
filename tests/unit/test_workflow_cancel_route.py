"""Audit B5: a running workflow can be stopped.

A chain is a series of tasks that advance one after another. There was no way to stop one once it started.
Stop now ends the step that is running after its step (it is asked to stop at its next checkpoint), starts no
other, and the run's record says `stopped` with the steps that never began marked skipped. A step still
waiting for a seat never starts. Only the owner's own click or word stops it; stopping needs no approval
because it only ends work.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import agent
from agent_friday.services import task_journal as tj


class FakeSpawn:
    """Stands in for agent._spawn_task: records the task as running without running an agent."""

    def __init__(self):
        self.n = 0
        self.calls = []

    def __call__(self, name, prompt, description="", **kw):
        self.n += 1
        tid = "t%d" % self.n
        self.calls.append((tid, kw.get("chain"), kw.get("chain_step"), kw.get("conversation_id")))
        with agent.TASKS_LOCK:
            agent.TASKS[tid] = {"task_id": tid, "name": name, "description": description, "prompt": prompt, "status": "running",
                                "created": time.time() + self.n * 0.001, "started": time.time(), "ended": None, "log": [],
                                "result": "", "chain": kw.get("chain"), "chain_step": kw.get("chain_step", 0),
                                "conversation_id": kw.get("conversation_id")}
        return tid


@pytest.fixture
def world(tmp_path, monkeypatch):
    from agent_friday.services import step_lists
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
    monkeypatch.setattr(tj, "steer", lambda *a, **k: None)
    agent.save_workflow_chain({"name": "Morning brief", "steps": [
        {"name": "Gather", "prompt": "gather the news"}, {"name": "Write", "prompt": "write it up"}, {"name": "Send", "prompt": "file the card"}]})
    yield spawn
    with agent.TASKS_LOCK:
        agent.TASKS.clear()
    agent._CHAIN_STOP.clear()
    agent._CHAIN_STOPPED.clear()
    tj._STOP_REQUESTED.clear()
    step_lists.reset()


def _finish(tid, status="complete"):
    with agent.TASKS_LOCK:
        agent.TASKS[tid].update(status=status, ended=time.time())


def test_stopping_a_running_chain_asks_the_running_step_to_stop_and_says_so(world):
    tid = agent.run_workflow_chain("Morning brief")
    res = agent.stop_workflow_chain("Morning brief")
    assert res["ok"] and res["stopping"] == [tid] and res["after_step"] == 0
    assert tj.stop_requested(tid), "the step that is running is asked to stop at its next checkpoint"
    assert agent.chain_run_status("morning-brief")["state"] == "running", "it is still finishing its step"


def test_a_stop_that_lands_between_steps_means_the_next_step_never_starts(world):
    tid = agent.run_workflow_chain("Morning brief")
    agent.stop_workflow_chain("Morning brief")
    _finish(tid)                                               # step 1 finishes normally
    assert agent._advance_task_chain(tid, "Here is the news.") is None
    assert [c[2] for c in world.calls] == [0], "step 2 was never spawned"
    st = agent.chain_run_status("morning-brief")
    assert st["state"] == "stopped"
    assert [(s["name"], s["status"]) for s in st["steps"]] == [("Gather", "completed"), ("Write", "skipped"), ("Send", "skipped")]
    assert st["steps"][1]["reason"] == "stopped by you"


def test_a_step_that_stops_at_its_checkpoint_ends_the_run_as_stopped(world):
    tid = agent.run_workflow_chain("Morning brief")
    agent.stop_workflow_chain("Morning brief")
    _finish(tid, status="cancelled")                           # what stop-after-step leaves
    st = agent.chain_run_status("morning-brief")
    assert st["state"] == "stopped"
    assert st["steps"][0]["status"] == "stopped" and st["steps"][0]["reason"] == "stopped by you"
    assert [s["status"] for s in st["steps"][1:]] == ["skipped", "skipped"]


def test_a_chain_that_is_not_running_says_so_and_a_new_run_is_not_the_stopped_one(world):
    assert agent.stop_workflow_chain("Morning brief") == {"ok": False, "reason": "it is not running"}
    assert agent.stop_workflow_chain("No such")["ok"] is False
    tid = agent.run_workflow_chain("Morning brief")
    agent.stop_workflow_chain("Morning brief")
    _finish(tid)
    agent._advance_task_chain(tid, "x")
    tid2 = agent.run_workflow_chain("Morning brief")
    assert agent._CHAIN_STOP == {} and agent._CHAIN_STOPPED == {}
    _finish(tid2)
    nxt = agent._advance_task_chain(tid2, "again")
    assert nxt is not None and world.calls[-1][2] == 1, "a fresh run advances"


def test_a_step_still_waiting_for_a_seat_never_starts(world):
    tid = agent.run_workflow_chain("Morning brief")
    with agent.TASKS_LOCK:
        agent.TASKS[tid]["status"] = "queued"
    agent.stop_workflow_chain("Morning brief")
    with agent.TASKS_LOCK:
        assert agent.TASKS[tid]["status"] == "cancelled"
        assert "before it started" in agent.TASKS[tid]["result"]


def test_the_route_is_the_owners_click_and_answers_honestly(world):
    import agent_friday.core as core
    from agent_friday.routes import workflows as rw
    if rw.workflows_bp.name not in core.app.blueprints:
        core.app.register_blueprint(rw.workflows_bp)
    core.app.config["TESTING"] = True
    client = core.app.test_client()
    r = client.post("/api/workflows/chains/morning-brief/stop")
    assert r.status_code == 409 and r.get_json()["status"] == "not_running"
    agent.run_workflow_chain("Morning brief")
    r = client.post("/api/workflows/chains/morning-brief/stop")
    assert r.status_code == 200 and r.get_json()["stopped"] is True
    assert client.post("/api/workflows/chains/nope/stop").status_code == 404


def test_the_run_remembers_the_conversation_it_reports_to_through_every_step(world):
    tid = agent.run_workflow_chain("Morning brief", conversation_id="conv-7")
    _finish(tid)
    agent._advance_task_chain(tid, "ok")
    assert [c[3] for c in world.calls] == ["conv-7", "conv-7"]
