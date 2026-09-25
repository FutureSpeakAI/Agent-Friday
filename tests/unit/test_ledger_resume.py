"""A local-seat task resumes from its ledger after a restart, on its own.

Local tasks run the OpenAI-shaped loop, which has no transcript checkpoint,
so before the ledger an interrupted local task could only be started over.
And tasks come back from the journal after a restart already "interrupted",
which the boot reconciler never auto-resumed.
"""
from __future__ import annotations

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import task_journal as tj
from agent_friday.services import task_ledger as tl
from agent_friday.services import task_resume as tr


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    tj.reset_for_tests()
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)
    with tl._LOCK:
        tl._LIVE.clear()
    yield
    with tl._LOCK:
        tl._LIVE.clear()


def _interrupted_task(tid, *, steps=3, pending=None):
    tj.index_put(tid, "inspection", "running", 1_000_000.0)
    tj.write_state(tid, {"task_id": tid, "name": "inspection", "status": "running",
                         "created": 1_000_000.0})
    led = tl.ensure(tid, "inspect every batch")
    tl.remember_run(led, name="inspection", model="bonsai2:27b", tools=["fetch_batch"])
    for i in range(steps):
        tl.record_step(led, "fetch_batch", {"batch": i}, "batch=%d defects=1" % i)
    led["pending"] = pending
    tl.save(tid, led)
    with tl._LOCK:
        tl._LIVE.clear()           # a new process: nothing cached


def test_a_task_with_only_a_ledger_is_resumable():
    _interrupted_task("t1")
    v = tr.resumability("t1")
    assert v["resumable"] is True and v["via"] == "ledger" and v["iteration"] == 3
    assert v["needs_confirmation"] is False


def test_an_unsafe_step_in_flight_gates_the_ledger_resume(monkeypatch):
    monkeypatch.setattr(tr, "replay_safe", lambda name: name == "fetch_batch")
    _interrupted_task("t2", pending={"name": "send_email", "args": "{}"})
    v = tr.resumability("t2")
    assert v["resumable"] is True and v["needs_confirmation"] is True
    assert "send_email" in v["reason"]
    with pytest.raises(tr.ResumeRefused):
        tr.resume("t2")


def test_a_task_without_steps_is_not_resumable():
    _interrupted_task("t3", steps=0)
    assert tr.resumability("t3")["resumable"] is False


def test_a_ledger_resume_reruns_the_tasks_own_worker_from_its_ledger(monkeypatch):
    _interrupted_task("t4")
    ran = {}

    def worker(task_id, name, prompt, description='', orb_icon='x', model=None, tools=None):
        ran.update(task_id=task_id, prompt=prompt, model=model, tools=tools)
        with ag.TASKS_LOCK:
            ag.TASKS.setdefault(task_id, {})["status"] = "complete"
            ag.TASKS[task_id]["result"] = "finished"
    monkeypatch.setattr(ag, "_task_worker", worker)
    text, _ = tr.resume("t4")
    assert text == "finished"
    assert ran["task_id"] == "t4" and ran["model"] == "bonsai2:27b" and ran["tools"] == ["fetch_batch"]
    assert "[Task Ledger]" in ran["prompt"] and "do not repeat DONE" in ran["prompt"]
    assert "3 step(s)" in ran["prompt"] or "DONE: 3" in ran["prompt"]
    assert tl.load("t4")["resume_attempts"] == 1


def test_boot_auto_resumes_ledger_tasks_and_leaves_gated_ones(monkeypatch):
    monkeypatch.setattr(tr, "replay_safe", lambda name: name == "fetch_batch")
    monkeypatch.setattr(tr, "auto_enabled", lambda: True)
    _interrupted_task("ok-1")
    _interrupted_task("gated-1", pending={"name": "send_email", "args": "{}"})
    started = []
    from agent_friday.services import reconcile as rc
    monkeypatch.setattr(rc, "_resume_in_background", lambda tid: started.append(tid))
    summary = ag._restore_tasks_from_journal()
    assert started == ["ok-1"]
    assert summary["auto_resumed"] == ["ok-1"]


def test_boot_only_offers_when_auto_resume_is_turned_off(monkeypatch):
    monkeypatch.setattr(tr, "auto_enabled", lambda: False)
    _interrupted_task("ok-2")
    started = []
    from agent_friday.services import reconcile as rc
    monkeypatch.setattr(rc, "_resume_in_background", lambda tid: started.append(tid))
    ag._restore_tasks_from_journal()
    assert started == []


def test_a_crash_looping_checkpoint_does_not_get_fresh_tries_from_the_ledger():
    _interrupted_task("t5")
    tr.checkpoint("t5", convo=[{"role": "user", "content": "x"}], iteration=2)
    for _ in range(tr.MAX_ATTEMPTS):
        tr._bump_attempts("t5")
    v = tr.resumability("t5")
    assert v["resumable"] is False and "times without finishing" in v["reason"]
