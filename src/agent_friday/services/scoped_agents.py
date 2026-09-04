"""
Agent Friday — Scoped Subagent Delegation
Inspired by patterns in Goose (Apache-2.0). All code is original.

Tracks scoped tasks (spawn_scoped_task/get_scoped_task/list_scoped_tasks,
genuinely used by routes/ext_security.py). CORRECTION (gauntlet-2026-09-03
F56): "restricted tool sets for parallel safe execution" overclaimed --
ScopedTask.is_tool_allowed()/check_tool_permission() are this module's own
enforcement primitives, and neither is called anywhere outside this file.
The REAL governance gate on the tool-execution path (agent.py's Ring
dispatch) enforces scope via a separate module, services/subagents.py's
scope_check(), confirmed wired in there. This module tracks and lists
scoped tasks; it does not itself restrict what tool a task can call.
Whether to also wire this module's own check into the real dispatch path
(defense in depth, since two independently-implemented enforcement points
existing is itself worth a second look) was not decided here -- that
touches a live security boundary and deserves more than a docstring-sweep
pass.
"""
import threading, uuid, time
from datetime import datetime, timezone

# Active scoped tasks
_SCOPED_TASKS = {}
_SCOPED_LOCK = threading.Lock()


class ScopedTask:
    def __init__(self, task_id: str, prompt: str, allowed_tools: list,
                 timeout: int = 300, parent_id: str = None):
        self.task_id = task_id
        self.prompt = prompt
        self.allowed_tools = set(allowed_tools)
        self.timeout = timeout
        self.parent_id = parent_id
        self.status = "queued"
        self.result = None
        self.error = None
        self.started_at = None
        self.completed_at = None

    def is_tool_allowed(self, tool_name: str) -> bool:
        if not self.allowed_tools:
            return True  # Empty = all allowed (backwards compat)
        return tool_name in self.allowed_tools

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "prompt": self.prompt[:100] + "..." if len(self.prompt) > 100 else self.prompt,
            "allowed_tools": list(self.allowed_tools),
            "status": self.status,
            "timeout": self.timeout,
            "parent_id": self.parent_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "has_result": self.result is not None,
        }


def spawn_scoped_task(prompt: str, tools: list = None, timeout: int = 300,
                      parent_id: str = None) -> str:
    """Spawn a task that can only use the specified tools."""
    task_id = f"scoped-{uuid.uuid4().hex[:8]}"
    task = ScopedTask(task_id, prompt, tools or [], timeout, parent_id)
    with _SCOPED_LOCK:
        _SCOPED_TASKS[task_id] = task
    return task_id


def get_scoped_task(task_id: str) -> ScopedTask:
    return _SCOPED_TASKS.get(task_id)


def check_tool_permission(task_id: str, tool_name: str) -> bool:
    """Check if a tool is allowed for the given scoped task."""
    task = _SCOPED_TASKS.get(task_id)
    if not task:
        return True  # Not a scoped task = no restrictions
    return task.is_tool_allowed(tool_name)


def complete_scoped_task(task_id: str, result: str = None, error: str = None):
    task = _SCOPED_TASKS.get(task_id)
    if task:
        task.status = "complete" if not error else "failed"
        task.result = result
        task.error = error
        task.completed_at = datetime.utcnow().isoformat()


def list_scoped_tasks(include_completed: bool = False) -> list:
    with _SCOPED_LOCK:
        tasks = list(_SCOPED_TASKS.values())
    if not include_completed:
        tasks = [t for t in tasks if t.status in ("queued", "running")]
    return [t.to_dict() for t in tasks]


def cleanup_old_tasks(max_age_hours: int = 24):
    """Remove completed tasks older than max_age_hours.

    Fixed (gauntlet-2026-09-03 F56): `cutoff` was computed but never
    compared against anything, and the removal loop was a bare `pass`
    ("Actually keep them for now") -- this never removed a single task,
    regardless of age, since the function was written. `completed_at` is
    a naive UTC ISO-format string (datetime.utcnow().isoformat()), not a
    float timestamp -- parsed and explicitly marked UTC before comparing
    against `cutoff` (time.time()), so this doesn't silently drift by the
    local UTC offset on a naive .timestamp() call.
    """
    cutoff = time.time() - (max_age_hours * 3600)
    with _SCOPED_LOCK:
        to_remove = []
        for tid, task in _SCOPED_TASKS.items():
            if task.status in ("complete", "failed") and task.completed_at:
                try:
                    completed_ts = (datetime.fromisoformat(task.completed_at)
                                   .replace(tzinfo=timezone.utc).timestamp())
                except (TypeError, ValueError):
                    continue
                if completed_ts < cutoff:
                    to_remove.append(tid)
        for tid in to_remove:
            del _SCOPED_TASKS[tid]
