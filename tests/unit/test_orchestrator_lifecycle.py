"""Unit tests for the orchestration engine — spawn, monitor, collect, cancel,
budget enforcement, and worker registry bookkeeping.

A FakeAdapter replaces the real (Ollama/HTTP) adapters so no external process
or network is touched. The runner thread drives the same lifecycle code paths.
"""
from __future__ import annotations

import time

import pytest

from agent_friday.services import orchestrator as orch
from agent_friday.services.orchestrator import (
    Orchestrator, WorkerTask, WorkerStatus, ResultStatus, WorkerResult,
    TaskType, AdapterType,
)


class FakeAdapter:
    """Deterministic adapter: completes immediately with a canned result."""
    def __init__(self, *, status=WorkerStatus.COMPLETED, cost=1000, delay=0.0,
                 result_status=ResultStatus.COMPLETED):
        self._status = status
        self._cost = cost
        self._delay = delay
        self._result_status = result_status
        self.cancelled = False
        self._started = 0.0

    def start(self, task):
        self._started = time.time()
        return "fake-aid"

    def poll(self, aid):
        if self.cancelled:
            return WorkerStatus.CANCELLED
        if time.time() - self._started < self._delay:
            return WorkerStatus.RUNNING
        return self._status

    def result(self, aid):
        return WorkerResult(task_id="t", status=self._result_status,
                            output="done", cost_mψ=self._cost, tokens_used=42)

    def cancel(self, aid):
        self.cancelled = True


@pytest.fixture
def orchestrator(monkeypatch):
    # Force every spawn to use our FakeAdapter regardless of adapter_type.
    monkeypatch.setattr(orch, "_get_adapter", lambda t: FakeAdapter())
    # Neutralize budget + work-log side effects for the pure lifecycle tests.
    monkeypatch.setattr(orch, "_log_start", lambda e: None)
    monkeypatch.setattr(orch, "_log_finish", lambda e: None)
    return Orchestrator()


def _task(**kw):
    kw.setdefault("prompt", "do the thing")
    kw.setdefault("deadline_seconds", 5)
    return WorkerTask(**kw)


class TestSpawnAndCollect:
    def test_spawn_returns_task_id(self, orchestrator):
        wid = orchestrator.spawn_worker(_task())
        assert isinstance(wid, str)

    def test_collect_result_completes(self, orchestrator):
        res = orchestrator.delegate("hello", deadline_seconds=5)
        assert res.status == ResultStatus.COMPLETED
        assert res.output == "done"

    def test_check_worker_reports_status(self, orchestrator):
        wid = orchestrator.spawn_worker(_task())
        orchestrator.collect_result(wid, timeout=5)
        assert orchestrator.check_worker(wid) in (
            WorkerStatus.COMPLETED, WorkerStatus.RUNNING)

    def test_check_unknown_worker_is_failed(self, orchestrator):
        assert orchestrator.check_worker("no-such-worker") == WorkerStatus.FAILED

    def test_collect_unknown_worker_none(self, orchestrator):
        assert orchestrator.collect_result("no-such", timeout=0.5) is None


class TestCancel:
    def test_cancel_marks_cancelled(self, orchestrator, monkeypatch):
        # A slow adapter so we can cancel mid-flight.
        monkeypatch.setattr(orch, "_get_adapter",
                            lambda t: FakeAdapter(delay=10, status=WorkerStatus.RUNNING))
        wid = orchestrator.spawn_worker(_task(deadline_seconds=30))
        time.sleep(0.2)
        assert orchestrator.cancel_worker(wid) is True
        assert orchestrator.check_worker(wid) == WorkerStatus.CANCELLED

    def test_cancel_unknown_worker_false(self, orchestrator):
        assert orchestrator.cancel_worker("nope") is False


class TestListActive:
    def test_list_active_workers_includes_spawned(self, orchestrator):
        wid = orchestrator.spawn_worker(_task())
        listed = [w["worker_id"] for w in orchestrator.list_active_workers()]
        assert wid in listed

    def test_list_entries_have_expected_fields(self, orchestrator):
        orchestrator.spawn_worker(_task())
        w = orchestrator.list_active_workers()[0]
        for key in ("worker_id", "task_type", "adapter_type", "status",
                    "elapsed_seconds", "priority"):
            assert key in w


class TestBudgetEnforcement:
    def test_budget_reservation_failure_blocks_worker(self, orchestrator, monkeypatch):
        # Force reserve_budget to reject → worker must report BUDGET_EXCEEDED.
        import agent_friday.services.budget_enforcer as be
        monkeypatch.setattr(be, "reserve_budget", lambda ws, amt: False)
        res = orchestrator.delegate("expensive", budget_mψ=999_999, deadline_seconds=5)
        assert res.status == ResultStatus.BUDGET_EXCEEDED

    def test_successful_reservation_allows_completion(self, orchestrator, monkeypatch):
        import agent_friday.services.budget_enforcer as be
        monkeypatch.setattr(be, "reserve_budget", lambda ws, amt: True)
        monkeypatch.setattr(be, "release_budget", lambda ws, amt: None)
        res = orchestrator.delegate("cheap", budget_mψ=1000, deadline_seconds=5)
        assert res.status == ResultStatus.COMPLETED


class TestTimeout:
    def test_worker_exceeding_deadline_times_out(self, orchestrator, monkeypatch):
        # Adapter never completes; short deadline → TIMEOUT.
        monkeypatch.setattr(orch, "_get_adapter",
                            lambda t: FakeAdapter(delay=100, status=WorkerStatus.RUNNING))
        res = orchestrator.delegate("slow", deadline_seconds=1)
        assert res.status == ResultStatus.TIMEOUT


class TestSingleton:
    def test_get_orchestrator_is_singleton(self):
        assert orch.get_orchestrator() is orch.get_orchestrator()


class TestResultSerialization:
    def test_to_dict_shape(self):
        r = WorkerResult(task_id="x", status=ResultStatus.COMPLETED, output="y",
                         cost_mψ=5, tokens_used=3)
        d = r.to_dict()
        assert d["status"] == "COMPLETED"
        assert d["task_id"] == "x"
        assert d["output"] == "y"
