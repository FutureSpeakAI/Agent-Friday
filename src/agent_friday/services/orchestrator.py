"""
Agent Friday — Dual-Role Orchestration Engine (Phase 1)
FutureSpeak.AI · Asimov's Mind

Friday as *employer*: spawns local sub-agents (Ollama, scripts, HTTP APIs),
monitors them, enforces budgets, and collects results.

Public API
----------
delegate(prompt, task_type, budget_mψ, context, *, adapter_type, budget_tokens,
         deadline_seconds, priority, parent_task_id) → WorkerResult
spawn_worker(task)     → worker_id (str)
check_worker(worker_id) → WorkerStatus enum member
collect_result(worker_id) → WorkerResult
cancel_worker(worker_id) → bool
list_active_workers()  → list[dict]
"""
from __future__ import annotations

import enum
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR


# ─────────────────────────────────────────────────────────────────────────────
#  Enums
# ─────────────────────────────────────────────────────────────────────────────

class TaskType(str, enum.Enum):
    CODE     = "CODE"
    RESEARCH = "RESEARCH"
    CREATIVE = "CREATIVE"
    ANALYSIS = "ANALYSIS"
    BROWSER  = "BROWSER"
    CUSTOM   = "CUSTOM"


class AdapterType(str, enum.Enum):
    OLLAMA        = "OLLAMA"
    CLAUDE_CODE   = "CLAUDE_CODE"
    PYTHON_SCRIPT = "PYTHON_SCRIPT"
    BROWSER       = "BROWSER"
    HTTP_API      = "HTTP_API"
    GEMINI        = "GEMINI"
    OPENROUTER    = "OPENROUTER"


class WorkerStatus(str, enum.Enum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    TIMEOUT   = "TIMEOUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"


class ResultStatus(str, enum.Enum):
    COMPLETED       = "COMPLETED"
    FAILED          = "FAILED"
    TIMEOUT         = "TIMEOUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED       = "CANCELLED"


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkerTask:
    prompt: str
    task_type: TaskType = TaskType.CUSTOM
    context: Dict[str, Any] = field(default_factory=dict)
    budget_mψ: int = 50_000        # milliPositrons
    budget_tokens: int = 4_096
    deadline_seconds: int = 300
    adapter_type: AdapterType = AdapterType.OLLAMA
    parent_task_id: Optional[str] = None
    priority: int = 3              # 1-5, 5 = highest
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class WorkerResult:
    task_id: str
    status: ResultStatus
    output: Any = ""
    artifacts: List[str] = field(default_factory=list)
    tokens_used: int = 0
    cost_mψ: int = 0
    duration_seconds: float = 0.0
    quality_score: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status.value if isinstance(self.status, ResultStatus) else self.status,
            "output": self.output,
            "artifacts": self.artifacts,
            "tokens_used": self.tokens_used,
            "cost_mψ": self.cost_mψ,
            "duration_seconds": self.duration_seconds,
            "quality_score": self.quality_score,
            "error": self.error,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Worker registry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _WorkerEntry:
    task: WorkerTask
    adapter: Any
    started_at: float
    status: WorkerStatus = WorkerStatus.PENDING
    result: Optional[WorkerResult] = None
    thread: Optional[threading.Thread] = None


_WORKERS: Dict[str, _WorkerEntry] = {}
_WORKERS_LOCK = threading.RLock()


# ─────────────────────────────────────────────────────────────────────────────
#  Adapter import helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_adapter(adapter_type: AdapterType):
    if adapter_type == AdapterType.OLLAMA:
        from agent_friday.services.worker_adapters.ollama_adapter import OllamaAdapter
        return OllamaAdapter()
    if adapter_type == AdapterType.PYTHON_SCRIPT:
        from agent_friday.services.worker_adapters.python_script_adapter import PythonScriptAdapter
        return PythonScriptAdapter()
    if adapter_type == AdapterType.HTTP_API:
        from agent_friday.services.worker_adapters.http_api_adapter import HttpApiAdapter
        return HttpApiAdapter()
    # Default fallback
    from agent_friday.services.worker_adapters.ollama_adapter import OllamaAdapter
    return OllamaAdapter()


# ─────────────────────────────────────────────────────────────────────────────
#  Budget helper
# ─────────────────────────────────────────────────────────────────────────────

def _budget_workspace(context: dict) -> str:
    return context.get("workspace_goal", context.get("workspace", "default"))


# ─────────────────────────────────────────────────────────────────────────────
#  Work log helper
# ─────────────────────────────────────────────────────────────────────────────

def _log_start(entry: _WorkerEntry):
    try:
        from agent_friday.services.work_log import log_start
        log_start(entry.task)
    except Exception:
        pass


def _log_finish(entry: _WorkerEntry):
    try:
        from agent_friday.services.work_log import log_finish
        if entry.result:
            log_finish(entry.task, entry.result)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Runner thread
# ─────────────────────────────────────────────────────────────────────────────

def _run_worker(worker_id: str):
    with _WORKERS_LOCK:
        entry = _WORKERS.get(worker_id)
    if not entry:
        return

    task = entry.task
    adapter = entry.adapter
    started = entry.started_at

    with _WORKERS_LOCK:
        entry.status = WorkerStatus.RUNNING

    _log_start(entry)

    try:
        # Budget reservation
        workspace = _budget_workspace(task.context)
        try:
            from agent_friday.services.budget_enforcer import reserve_budget
            if not reserve_budget(workspace, task.budget_mψ):
                res = WorkerResult(
                    task_id=task.task_id,
                    status=ResultStatus.BUDGET_EXCEEDED,
                    error="Budget reservation failed — monthly cap would be exceeded",
                )
                with _WORKERS_LOCK:
                    entry.status = WorkerStatus.BUDGET_EXCEEDED
                    entry.result = res
                _log_finish(entry)
                return
        except Exception:
            pass  # budget enforcement is best-effort

        # Execute via adapter
        aid = adapter.start(task)
        deadline = task.deadline_seconds
        poll_interval = 1.0
        elapsed = 0.0

        while elapsed < deadline:
            status = adapter.poll(aid)
            if status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED, WorkerStatus.CANCELLED):
                break
            time.sleep(poll_interval)
            elapsed += poll_interval

        if elapsed >= deadline and adapter.poll(aid) not in (
            WorkerStatus.COMPLETED, WorkerStatus.FAILED, WorkerStatus.CANCELLED
        ):
            adapter.cancel(aid)
            res = WorkerResult(
                task_id=task.task_id,
                status=ResultStatus.TIMEOUT,
                duration_seconds=elapsed,
                error=f"Worker exceeded deadline of {deadline}s",
            )
        else:
            res = adapter.result(aid)
            res.duration_seconds = time.time() - started

        # Release unused budget
        try:
            from agent_friday.services.budget_enforcer import release_budget
            unused = max(0, task.budget_mψ - res.cost_mψ)
            if unused:
                release_budget(workspace, unused)
        except Exception:
            pass

    except Exception as exc:
        import traceback
        res = WorkerResult(
            task_id=task.task_id,
            status=ResultStatus.FAILED,
            duration_seconds=time.time() - started,
            error=str(exc),
        )

    with _WORKERS_LOCK:
        entry.status = WorkerStatus(res.status.value) if isinstance(res.status, ResultStatus) else WorkerStatus.FAILED
        entry.result = res

    _log_finish(entry)


# ─────────────────────────────────────────────────────────────────────────────
#  Public Orchestrator class
# ─────────────────────────────────────────────────────────────────────────────

class Orchestrator:
    """Manages local worker lifecycle — spawn, monitor, collect, cancel."""

    def spawn_worker(self, task: WorkerTask) -> str:
        adapter = _get_adapter(task.adapter_type)
        entry = _WorkerEntry(
            task=task,
            adapter=adapter,
            started_at=time.time(),
        )
        with _WORKERS_LOCK:
            _WORKERS[task.task_id] = entry

        t = threading.Thread(target=_run_worker, args=(task.task_id,), daemon=True)
        entry.thread = t
        t.start()
        return task.task_id

    def check_worker(self, worker_id: str) -> WorkerStatus:
        with _WORKERS_LOCK:
            entry = _WORKERS.get(worker_id)
        if not entry:
            return WorkerStatus.FAILED
        return entry.status

    def collect_result(self, worker_id: str, timeout: float = 300.0) -> Optional[WorkerResult]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with _WORKERS_LOCK:
                entry = _WORKERS.get(worker_id)
            if not entry:
                return None
            if entry.result is not None:
                return entry.result
            time.sleep(0.5)
        return WorkerResult(
            task_id=worker_id,
            status=ResultStatus.TIMEOUT,
            error="collect_result timed out waiting for worker",
        )

    def cancel_worker(self, worker_id: str) -> bool:
        with _WORKERS_LOCK:
            entry = _WORKERS.get(worker_id)
        if not entry:
            return False
        try:
            entry.adapter.cancel(worker_id)
        except Exception:
            pass
        with _WORKERS_LOCK:
            entry.status = WorkerStatus.CANCELLED
            if entry.result is None:
                entry.result = WorkerResult(
                    task_id=worker_id,
                    status=ResultStatus.CANCELLED,
                )
        return True

    def list_active_workers(self) -> List[dict]:
        now = time.time()
        out = []
        with _WORKERS_LOCK:
            for wid, entry in _WORKERS.items():
                out.append({
                    "worker_id": wid,
                    "task_id": entry.task.task_id,
                    "task_type": entry.task.task_type.value,
                    "adapter_type": entry.task.adapter_type.value,
                    "status": entry.status.value,
                    "elapsed_seconds": round(now - entry.started_at, 1),
                    "priority": entry.task.priority,
                    "budget_mψ": entry.task.budget_mψ,
                })
        return out

    def delegate(
        self,
        prompt: str,
        task_type: TaskType = TaskType.CUSTOM,
        budget_mψ: int = 50_000,
        context: Optional[dict] = None,
        *,
        adapter_type: AdapterType = AdapterType.OLLAMA,
        budget_tokens: int = 4_096,
        deadline_seconds: int = 300,
        priority: int = 3,
        parent_task_id: Optional[str] = None,
    ) -> WorkerResult:
        """High-level: spawn a worker, block until done, return the result."""
        task = WorkerTask(
            prompt=prompt,
            task_type=task_type,
            context=context or {},
            budget_mψ=budget_mψ,
            budget_tokens=budget_tokens,
            deadline_seconds=deadline_seconds,
            adapter_type=adapter_type,
            parent_task_id=parent_task_id,
            priority=priority,
        )
        worker_id = self.spawn_worker(task)
        return self.collect_result(worker_id, timeout=deadline_seconds + 5.0) or WorkerResult(
            task_id=task.task_id,
            status=ResultStatus.FAILED,
            error="delegate: collect_result returned None",
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_ORCHESTRATOR: Optional[Orchestrator] = None
_ORCH_LOCK = threading.Lock()


def get_orchestrator() -> Orchestrator:
    global _ORCHESTRATOR
    with _ORCH_LOCK:
        if _ORCHESTRATOR is None:
            _ORCHESTRATOR = Orchestrator()
    return _ORCHESTRATOR
