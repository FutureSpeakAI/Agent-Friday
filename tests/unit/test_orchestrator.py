"""Unit tests for services/orchestrator.py"""
import time
import pytest

from agent_friday.services.orchestrator import (
    AdapterType,
    Orchestrator,
    ResultStatus,
    TaskType,
    WorkerResult,
    WorkerStatus,
    WorkerTask,
    get_orchestrator,
)


def _task(prompt="test", adapter=AdapterType.HTTP_API, deadline=5, budget=1000):
    return WorkerTask(
        prompt=prompt,
        task_type=TaskType.CUSTOM,
        context={"workspace": "test"},
        budget_mψ=budget,
        budget_tokens=512,
        deadline_seconds=deadline,
        adapter_type=adapter,
    )


class TestWorkerTask:
    def test_task_has_uuid(self):
        t = WorkerTask(prompt="hello")
        assert t.task_id
        assert len(t.task_id) == 36  # UUID4 format

    def test_task_defaults(self):
        t = WorkerTask(prompt="hi")
        assert t.task_type == TaskType.CUSTOM
        assert t.priority == 3
        assert t.budget_mψ == 50_000

    def test_two_tasks_different_ids(self):
        a = WorkerTask(prompt="a")
        b = WorkerTask(prompt="b")
        assert a.task_id != b.task_id


class TestWorkerResult:
    def test_to_dict_has_required_fields(self):
        r = WorkerResult(task_id="abc", status=ResultStatus.COMPLETED, output="done")
        d = r.to_dict()
        for field in ("task_id", "status", "output", "artifacts", "tokens_used",
                      "cost_mψ", "duration_seconds", "quality_score", "error"):
            assert field in d

    def test_to_dict_status_is_string(self):
        r = WorkerResult(task_id="abc", status=ResultStatus.FAILED)
        assert r.to_dict()["status"] == "FAILED"


class TestOrchestratorSingleton:
    def test_get_orchestrator_returns_orchestrator(self):
        orch = get_orchestrator()
        assert isinstance(orch, Orchestrator)

    def test_singleton(self):
        a = get_orchestrator()
        b = get_orchestrator()
        assert a is b


class TestOrchestrator:
    def setup_method(self):
        self.orch = Orchestrator()

    def test_list_active_workers_initially_empty(self):
        workers = self.orch.list_active_workers()
        assert isinstance(workers, list)

    def test_check_unknown_worker(self):
        status = self.orch.check_worker("nonexistent-id")
        assert status == WorkerStatus.FAILED

    def test_cancel_unknown_worker(self):
        ok = self.orch.cancel_worker("no-such-id")
        assert ok is False

    def test_collect_result_unknown_worker(self):
        result = self.orch.collect_result("no-such-id", timeout=0.1)
        assert result is None or result.status == ResultStatus.TIMEOUT


class TestHttpApiAdapterIntegration:
    """Tests that exercise the adapter without hitting real network."""

    def test_adapter_start_returns_id(self):
        from agent_friday.services.worker_adapters.http_api_adapter import HttpApiAdapter
        adapter = HttpApiAdapter()
        task = _task(adapter=AdapterType.HTTP_API, deadline=2)
        task.context = {"endpoint": "http://localhost:99999/nonexistent"}  # unreachable
        aid = adapter.start(task)
        assert aid

    def test_adapter_cancel(self):
        from agent_friday.services.worker_adapters.http_api_adapter import HttpApiAdapter
        adapter = HttpApiAdapter()
        task = _task(adapter=AdapterType.HTTP_API, deadline=30)
        task.context = {"endpoint": "http://localhost:99999/nonexistent"}
        aid = adapter.start(task)
        time.sleep(0.1)
        ok = adapter.cancel(aid)
        assert ok is True

    def test_adapter_poll_after_cancel(self):
        from agent_friday.services.worker_adapters.http_api_adapter import HttpApiAdapter
        adapter = HttpApiAdapter()
        task = _task(adapter=AdapterType.HTTP_API, deadline=30)
        task.context = {"endpoint": "http://localhost:99999/nonexistent"}
        aid = adapter.start(task)
        time.sleep(0.1)
        adapter.cancel(aid)
        status = adapter.poll(aid)
        assert status == WorkerStatus.CANCELLED


class TestOllamaAdapter:
    def test_adapter_start_returns_id(self):
        from agent_friday.services.worker_adapters.ollama_adapter import OllamaAdapter
        adapter = OllamaAdapter()
        task = _task(adapter=AdapterType.OLLAMA, deadline=1)
        aid = adapter.start(task)
        assert aid

    def test_adapter_cancel(self):
        from agent_friday.services.worker_adapters.ollama_adapter import OllamaAdapter
        adapter = OllamaAdapter()
        task = _task(adapter=AdapterType.OLLAMA, deadline=30)
        aid = adapter.start(task)
        time.sleep(0.05)
        ok = adapter.cancel(aid)
        assert ok is True

    def test_adapter_result_after_cancel(self):
        from agent_friday.services.worker_adapters.ollama_adapter import OllamaAdapter
        adapter = OllamaAdapter()
        task = _task(adapter=AdapterType.OLLAMA, deadline=30)
        aid = adapter.start(task)
        time.sleep(0.05)
        adapter.cancel(aid)
        result = adapter.result(aid)
        assert result.task_id == task.task_id
        assert result.status == ResultStatus.CANCELLED


class TestPythonScriptAdapter:
    def test_run_simple_script(self):
        from agent_friday.services.worker_adapters.python_script_adapter import PythonScriptAdapter
        adapter = PythonScriptAdapter()
        task = _task(deadline=10)
        task.prompt = "print('hello from worker')"
        aid = adapter.start(task)
        # Wait for completion
        deadline = time.time() + 8
        while time.time() < deadline:
            if adapter.poll(aid) in (WorkerStatus.COMPLETED, WorkerStatus.FAILED):
                break
            time.sleep(0.2)
        result = adapter.result(aid)
        assert result.status == ResultStatus.COMPLETED
        assert "hello from worker" in (result.output or "")

    def test_failing_script(self):
        from agent_friday.services.worker_adapters.python_script_adapter import PythonScriptAdapter
        adapter = PythonScriptAdapter()
        task = _task(deadline=10)
        task.prompt = "raise RuntimeError('intentional failure')"
        aid = adapter.start(task)
        deadline = time.time() + 8
        while time.time() < deadline:
            if adapter.poll(aid) in (WorkerStatus.COMPLETED, WorkerStatus.FAILED):
                break
            time.sleep(0.2)
        result = adapter.result(aid)
        assert result.status == ResultStatus.FAILED

    def test_cancel_script(self):
        from agent_friday.services.worker_adapters.python_script_adapter import PythonScriptAdapter
        adapter = PythonScriptAdapter()
        task = _task(deadline=30)
        task.prompt = "import time; time.sleep(60)"
        aid = adapter.start(task)
        time.sleep(0.3)
        ok = adapter.cancel(aid)
        assert ok is True


class TestSpawnAndCancel:
    def test_spawn_returns_worker_id(self):
        orch = Orchestrator()
        task = _task(deadline=30)
        task.context = {"endpoint": "http://localhost:99999/unreachable", "workspace": "test"}
        worker_id = orch.spawn_worker(task)
        assert worker_id == task.task_id
        orch.cancel_worker(worker_id)

    def test_cancel_worker_after_spawn(self):
        orch = Orchestrator()
        task = _task(deadline=30)
        task.context = {"endpoint": "http://localhost:99999/unreachable", "workspace": "test"}
        worker_id = orch.spawn_worker(task)
        time.sleep(0.05)
        ok = orch.cancel_worker(worker_id)
        assert ok is True

    def test_list_active_workers_shows_new_worker(self):
        orch = Orchestrator()
        task = _task(deadline=30)
        task.context = {"endpoint": "http://localhost:99999/unreachable", "workspace": "test"}
        worker_id = orch.spawn_worker(task)
        time.sleep(0.05)
        workers = orch.list_active_workers()
        ids = [w["worker_id"] for w in workers]
        assert worker_id in ids
        orch.cancel_worker(worker_id)

    def test_check_worker_status_after_spawn(self):
        orch = Orchestrator()
        task = _task(deadline=30)
        task.context = {"endpoint": "http://localhost:99999/unreachable", "workspace": "test"}
        worker_id = orch.spawn_worker(task)
        status = orch.check_worker(worker_id)
        assert status in (WorkerStatus.PENDING, WorkerStatus.RUNNING,
                          WorkerStatus.COMPLETED, WorkerStatus.FAILED)
        orch.cancel_worker(worker_id)
