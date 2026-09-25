"""A local-only scheduled job stays local-only on the thread that does the work.

`local_only_guard.local_only` is thread-local. The scheduler entered it around
`_spawn_task`, but the task runs on its own worker thread, and only a cloud pin
was carried across, so the worker, its continuation legs and any resume ran
with no guard at all.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent
from agent_friday.services import local_only_guard as g
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


def test_the_spawned_task_records_that_it_is_local_only():
    with g.local_only("Nightly digest"):
        tid = agent._spawn_task("digest", "x", runner=lambda t: {"status": "complete", "result": ""})
    assert agent.TASKS[tid]["local_only"] == {"label": "Nightly digest"}


def test_a_cloud_pin_is_not_mistaken_for_local_only():
    with g.cloud_pinned("claude-haiku-4-5", "Heartbeat"):
        tid = agent._spawn_task("hb", "x", runner=lambda t: {"status": "complete", "result": ""})
    assert agent.TASKS[tid].get("local_only") is None


def test_the_worker_thread_runs_under_the_guard(monkeypatch):
    with g.local_only("Nightly digest"):
        tid = agent._spawn_task("digest", "x", runner=lambda t: {"status": "complete", "result": ""})
    seen = {}
    monkeypatch.setattr(agent, "_task_worker_untraced",
                        lambda *a, **k: seen.update(active=g.is_active(), label=g.label()))
    agent._task_worker(tid, "digest", "x")
    assert seen == {"active": True, "label": "Nightly digest"}
    assert g.is_active() is False


def test_a_resume_after_restart_is_local_only_from_the_ledger(monkeypatch):
    """A new process has no TASKS record flag; the ledger carries it."""
    tid = "lo-resume"
    tj.index_put(tid, "digest", "running", 1_000_000.0)
    tj.write_state(tid, {"task_id": tid, "name": "digest", "status": "running",
                         "created": 1_000_000.0})
    led = tl.ensure(tid, "digest the day")
    tl.remember_run(led, name="digest", model="bonsai2:27b", local_only="Nightly digest")
    tl.record_step(led, "read_file", {"path": "a"}, "ok")
    tl.save(tid, led)
    with tl._LOCK:
        tl._LIVE.clear()
    agent.TASKS.pop(tid, None)
    seen = {}

    def worker(*a, **k):
        seen.update(active=g.is_active())
        with agent.TASKS_LOCK:
            agent.TASKS.setdefault(tid, {})["status"] = "complete"
    monkeypatch.setattr(agent, "_task_worker_untraced", worker)
    tr.resume(tid)
    assert seen["active"] is True


def test_the_worker_remembers_local_only_in_the_ledger(monkeypatch):
    """So a continuation leg or a later resume keeps it."""
    tid = "lo-ledger"
    with agent.TASKS_LOCK:
        agent.TASKS[tid] = {"id": tid, "name": "digest", "status": "queued", "log": [],
                            "local_only": {"label": "Nightly digest"}}
    monkeypatch.setattr(agent, "_generate_agent", lambda *a, **k: ("done", []))
    monkeypatch.setattr(agent, "_register_agent_orb", lambda *a, **k: None)
    agent._task_worker(tid, "digest", "digest the day")
    assert (tl.load(tid) or {}).get("run", {}).get("local_only") == "Nightly digest"


def test_a_checkpoint_resume_of_a_local_only_task_stays_local_only(monkeypatch):
    tid = "lo-ckpt"
    tj.index_put(tid, "digest", "running", 1_000_000.0)
    tj.write_state(tid, {"task_id": tid, "name": "digest", "status": "running",
                         "created": 1_000_000.0})
    with g.local_only("Nightly digest"):
        tr.checkpoint(tid, convo=[{"role": "user", "content": "x"},
                                  {"role": "assistant", "content": "working"}], iteration=2)
    seen = {}
    monkeypatch.setattr(agent, "_call_claude_agent",
                        lambda *a, **k: seen.update(active=g.is_active()) or ("ok", []))
    tr.resume(tid)
    assert seen["active"] is True
