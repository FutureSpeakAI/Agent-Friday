"""A scheduler orb follows its worker's trace, never the dispatching chat."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import agent_friday.core as core
from agent_friday.services import reasoning_trace as rt
from agent_friday.services import scheduler as scheduler


@pytest.fixture(autouse=True)
def _isolated_scheduler(tmp_path, monkeypatch):
    from agent_friday.services import file_grants, stand_down

    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(scheduler, "RUNS_FILE", tmp_path / "runs.jsonl")
    monkeypatch.setattr(scheduler, "_RUNNING", set())
    monkeypatch.setattr(scheduler, "_patch_record", lambda *a, **kw: None)
    monkeypatch.setattr(scheduler, "_append_run", lambda *a, **kw: None)
    monkeypatch.setattr(scheduler, "_notify_run", lambda *a, **kw: None)
    monkeypatch.setattr(scheduler, "_orb_model_for", lambda rec: "example-model")
    monkeypatch.setattr(stand_down, "is_stood_down", lambda: False)
    file_grants._SIGNING_KEY_CACHE.clear()
    rt._reset_for_tests()
    monkeypatch.setattr(rt, "BASE_DIR_OVERRIDE", tmp_path / "traces")
    monkeypatch.setattr(rt, "settings", lambda: {"capture": True, "retention_days": 0})
    yield
    with core.PROCESSES_LOCK:
        for pid in list(core.PROCESSES):
            if pid.startswith("sched-example-trace-"):
                core.PROCESSES.pop(pid)
    rt._reset_for_tests()


@pytest.mark.parametrize("inherited", [False, True], ids=["no-caller", "caller-trace"])
@pytest.mark.parametrize("capture", [True, False], ids=["capture", "capture-off"])
def test_delayed_scheduler_orb_learns_only_its_worker_trace(client, monkeypatch, inherited, capture):
    # Keep the real dispatch closure, but start it on a fresh thread only
    # after the first client poll. No model, notification, or live job runs.
    pending = []

    class DeferredThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            pending.append(self.target)

    monkeypatch.setattr(scheduler, "threading", SimpleNamespace(Thread=DeferredThread))
    ready, release = threading.Event(), threading.Event()
    seen = {}

    def builtin():
        seen["trace_id"] = rt.current()
        rt.note("Recorded scheduled checkpoint")
        ready.set()
        assert release.wait(5), "test must release the synthetic job"
        return {"summary": "Synthetic job complete"}

    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "example_trace_job", {"fn": builtin})
    caller = rt.start("chat", "Unrelated caller", parent_id=None) if inherited else None
    monkeypatch.setattr(rt, "settings", lambda: {"capture": capture, "retention_days": 0})
    rec = {"id": "example-trace-job", "name": "Example scheduled job", "trigger": "daily",
           "task": {"kind": "builtin", "ref": "example_trace_job"}, "notify": "on_complete"}
    with rt.activate(caller):
        run_id = scheduler.dispatch(rec, manual=True)
        assert run_id
        assert rt.current() == caller, "orb creation must preserve its caller's context"
    assert len(pending) == 1
    with core.PROCESSES_LOCK:
        pids = [pid for pid in core.PROCESSES if pid.startswith("sched-example-trace-job-")]
    assert len(pids) == 1
    pid = pids[0]
    initial = client.get("/api/tasks/" + pid)
    assert initial.status_code == 200
    assert initial.json["trace_id"] is None, "a queued orb must not show its dispatcher's reasoning"

    worker = threading.Thread(target=pending[0], daemon=True)
    worker.start()
    try:
        assert ready.wait(5), "the real scheduler must reach the synthetic builtin"
        actual = seen["trace_id"]
        detail = client.get("/api/tasks/" + pid)
        assert detail.status_code == 200
        assert detail.json["process"] is True
        assert detail.json["trace_id"] == actual
        if capture:
            assert actual and actual != caller
            record = client.get("/api/traces/" + actual)
            assert record.status_code == 200
            assert record.json["focus"] == actual
            assert record.json["tree"]["trace_id"] == actual
            assert record.json["tree"]["parent_id"] is None
            assert any(e.get("text") == "Recorded scheduled checkpoint"
                       for e in record.json["tree"]["events"])
        else:
            assert actual is None
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    final = client.get("/api/tasks/" + pid)
    assert final.json["trace_id"] == actual
    assert final.json["status"] == "complete"


def test_scheduler_binds_trace_before_an_early_gate_failure(client, monkeypatch):
    from agent_friday.services import stand_down

    monkeypatch.setattr(stand_down, "is_stood_down", lambda: True)
    monkeypatch.setattr(stand_down, "reason", lambda: "Synthetic stand-down")
    caller = rt.start("chat", "Unrelated caller", parent_id=None)
    pid = "sched-example-trace-gated"
    with rt.activate(caller):
        # An existing record carrying an old trace is replaced, including
        # when the run gate raises before any builtin or provider executes.
        core.process_register(pid, name="Example gated job")
        with pytest.raises(scheduler.StoodDown, match="Synthetic stand-down"):
            scheduler._run_task({"name": "Example gated job", "_orb_id": pid})
        assert rt.current() == caller
    detail = client.get("/api/tasks/" + pid)
    actual = detail.json["trace_id"]
    assert actual and actual != caller
    trace = rt.live_trace(actual)
    assert trace["kind"] == "scheduled"
    assert trace["status"] == "failed"


def test_scheduler_does_not_recreate_a_removed_orb(monkeypatch):
    pid = "sched-example-trace-removed"
    monkeypatch.setattr(scheduler, "_run_task_inner", lambda rec: "complete")
    assert scheduler._run_task({"name": "Example removed job", "_orb_id": pid}) == "complete"
    with core.PROCESSES_LOCK:
        assert pid not in core.PROCESSES
