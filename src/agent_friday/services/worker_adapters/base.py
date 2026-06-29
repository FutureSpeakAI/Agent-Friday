"""Base interface for all worker adapters."""
from __future__ import annotations

import enum
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent_friday.services.orchestrator import WorkerTask, WorkerResult


class WorkerStatus(str, enum.Enum):
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    TIMEOUT   = "TIMEOUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"


class BaseAdapter:
    """
    All adapters implement these four methods.

    adapter_id (returned by start) is a short-lived handle that the caller
    uses to poll/collect/cancel. It may be the same as worker_id or an
    internal process/job identifier.
    """

    def start(self, task: "WorkerTask") -> str:
        """Launch the worker. Return an adapter-local ID."""
        raise NotImplementedError

    def poll(self, adapter_id: str) -> WorkerStatus:
        """Return current status without blocking."""
        raise NotImplementedError

    def result(self, adapter_id: str) -> "WorkerResult":
        """Return the final result. Only valid once poll() returns COMPLETED/FAILED."""
        raise NotImplementedError

    def cancel(self, adapter_id: str) -> bool:
        """Request cancellation. Return True if the signal was delivered."""
        raise NotImplementedError
