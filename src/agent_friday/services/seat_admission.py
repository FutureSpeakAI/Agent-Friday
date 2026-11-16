"""Honest seat admission and cancellation teardown (Defect F).

Two invariants live here:

1. Admission honesty — the seat a task queues under must be the seat it
   will actually run on. A task that declares no model does not get the
   historical hardcoded cloud assumption (which exempted it from local-seat
   queueing); it resolves through the router's real default-seat resolver.
   A faulting resolver fails LOCAL — the constrained seat — so a fault can
   never exempt a task from admission control.

2. Cancellation teardown — cancelling a task must terminalize the record
   (status 'cancelled', `ended_at` stamped) and fire `on_task_end` exactly
   once so the seat frees and the FIFO queue promotes. Teardown is
   idempotent, and a teardown callback that raises still leaves the record
   terminal: a seat-freeing failure must never manufacture a zombie task
   stuck in status 'running'.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Mapping, MutableMapping, Optional

# The constrained seat. Faults during default-seat resolution fail here so
# that a broken resolver can never grant a task the uncontended cloud lane.
FAIL_SAFE_SEAT = "local/default"

# Terminal statuses. A record already in one of these is never re-torn-down
# and never un-terminalized.
TERMINAL_STATUSES = frozenset({"cancelled", "completed", "failed"})

# Substrings that mark a declared model as locally served. Classification is
# deliberately conservative: anything not recognizably local is treated as a
# cloud model, because misclassifying cloud-as-local only over-queues (safe),
# while local-as-cloud exempts a local job from queueing (the defect).
_LOCAL_MODEL_MARKERS = (
    "bonsai",
    "local",
    "ollama",
    "llama",
    "gemma",
    "qwen",
    "mistral",
    "phi",
)


def _is_local_model(model: str) -> bool:
    lowered = model.lower()
    return any(marker in lowered for marker in _LOCAL_MODEL_MARKERS)


def resolve_admission_seat(
    record: Mapping[str, object],
    *,
    default_seat_resolver: Callable[[], Optional[str]],
) -> str:
    """Return the seat this task record must queue under.

    An empty, missing, or whitespace-only `model` field resolves through
    `default_seat_resolver` — the router's real answer for what seat an
    undeclared task will actually run on. A resolver that raises or returns
    a falsy value fails to `FAIL_SAFE_SEAT` (local): admission control must
    degrade toward the constrained seat, never away from it.

    A declared model resolves by classification: recognizably local models
    queue under the local seat, everything else under cloud.
    """
    model = record.get("model") or ""
    if not isinstance(model, str):
        model = str(model)
    model = model.strip()

    if not model:
        try:
            seat = default_seat_resolver()
        except Exception:
            return FAIL_SAFE_SEAT
        if not seat or not isinstance(seat, str):
            return FAIL_SAFE_SEAT
        return seat

    if _is_local_model(model):
        return f"local/{model}"
    return f"cloud/{model}"


def cancel_task(
    record: MutableMapping[str, object],
    *,
    on_task_end: Callable[[MutableMapping[str, object]], None],
    now: Callable[[], str] = lambda: datetime.now().isoformat(timespec="seconds"),
) -> bool:
    """Cancel a task record, terminalizing it and freeing its seat.

    Returns True when this call performed the cancellation, False when the
    record was already terminal (idempotent no-op).

    Ordering is the invariant: the record goes terminal BEFORE the
    `on_task_end` teardown runs, so a teardown that raises still leaves the
    record status 'cancelled' with `ended_at` stamped — never a zombie stuck
    in 'running'. `on_task_end` fires exactly once per record lifetime; an
    already-terminal record (completed, failed, or previously cancelled) is
    left untouched and gets no second teardown.
    """
    if record.get("status") in TERMINAL_STATUSES:
        return False

    # Terminalize first: the zombie-killer. Seat-freeing failure below must
    # not be able to undo or prevent this.
    record["status"] = "cancelled"
    if not record.get("ended_at"):
        record["ended_at"] = now()

    try:
        on_task_end(record)
    except Exception:
        # The seat-freeing hook failed; the record stays terminal. The
        # failure is the caller's to observe via its own logging — admission
        # control's contract is only that no zombie survives.
        pass

    return True
