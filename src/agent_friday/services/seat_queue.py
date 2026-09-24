"""Per-seat task queue for Defect D (RSI loop): one job per local seat at a time.

Regression case: 2026-09-21 (Monday) — multiple workflow tasks were dispatched
onto the single-slot bonsai2 seat simultaneously. They serialized invisibly at
the HTTP layer with no status, and the user had to cancel them by hand. This
module gives the queue an honest, inspectable status: a second local task does
not start — it waits, and the user can see *why* and *how long*.

Pure functions over dicts, matching the style of task_watchdog.py.
No I/O, no threading, no imports of agent.py.

Required API (all pure, importable without side effects):

    SeatQueue()                         # holds per-seat running/queued state
    SeatQueue.submit(task: dict) -> str # "running" | "queued-for-seat"
    SeatQueue.complete(task_id: str) -> None
    SeatQueue.get(task_id: str) -> dict | None
    SeatQueue.assess_all(assess=None) -> None   # consults DEFAULT_ASSESS by default
    USER_NOTICE: str                  # the honest one-liner shown to the user
    DEFAULT_ASSESS = task_watchdog.assess

Local seats serialize independently of each other (one slot per seat). Cloud
seats are exempt: they run concurrently, no queueing.

Watchdog wiring: assess_all() runs the assessor over every *running* record.
A "kill" verdict terminates that task (status -> "killed") and frees its seat
for the next task in line (FIFO promotion). Any other verdict ("ok",
"overdue", "stuck") leaves the running task alone — only "kill" terminates
and promotes.
"""
from __future__ import annotations

import time

from agent_friday.services import task_watchdog

# The watchdog that rules from the OUTSIDE — the wedge that ran 8869s against
# an 1800s budget must be the default assessor. This is the exact function
# object, not a reimplementation.
DEFAULT_ASSESS = task_watchdog.assess

# The one-liner shown when a local task is held. It must state the two things
# the user needs to know: (a) local AI runs ONE job at a time, (b) it must be
# given TIME to run.
USER_NOTICE: str = (
    "Local AI runs ONE job at a time. This task is queued and must be given "
    "time to run before it can start."
)


def _needs_now(assess) -> bool:
    """Does ``assess`` need an explicit ``now`` keyword?

    The default assessor (task_watchdog.assess) requires ``now`` as a keyword
    argument. Callers may also pass a one-arg assessor (e.g. a test fake). We
    probe the signature rather than guessing, so ``assess_all`` is correct for
    both shapes.
    """
    try:
        import inspect

        sig = inspect.signature(assess)
    except (TypeError, ValueError):
        # Unprobed callable (e.g. a C builtin) — assume it wants now.
        return True

    return "now" in sig.parameters


class SeatQueue:
    """Serializes task submission per local seat; FIFO-queues the rest.

    State is held as plain dicts keyed by task_id, plus a per-seat pointer to
    the currently-running task. No I/O, no threads — pure over the record dicts.
    Every stored record carries a ``status`` field (stamped by this module; the
    incoming task dict is not guaranteed to have one).
    """

    def __init__(self) -> None:
        # task_id -> record (status is always present, stamped by submit)
        self._records: dict[str, dict] = {}
        # seat -> task_id currently running on that local seat (None when free)
        self._running: dict[str, str] = {}

    # -- submission -------------------------------------------------------

    def submit(self, task: dict) -> str:
        """Add ``task`` to the queue. Returns the status assigned.

        Local seat already busy -> held with status 'queued-for-seat' and the
        USER_NOTICE. Otherwise (cloud seat, or free local seat) -> 'running'.
        """
        seat = task.get("seat")
        is_local = bool(task.get("seat_is_local", False))

        # Work on a copy so we never mutate the caller's dict.
        rec = dict(task)

        if not is_local:
            # Cloud seats are exempt from serialization: always run.
            status = "running"
            self._running[seat] = rec.get("id")  # harmless bookkeeping
        else:
            # Local seat: only one running at a time.
            if self._running.get(seat) is None:
                status = "running"
                self._running[seat] = rec.get("id")
            else:
                status = "queued-for-seat"
                # Attach the honest notice to the held record.
                rec["user_notice"] = USER_NOTICE

        # Stamp the status so the stored record is inspectable via get().
        rec["status"] = status
        self._records[rec.get("id")] = rec
        return status

    # -- inspection -------------------------------------------------------

    def get(self, task_id: str):
        """Return the stored record for ``task_id``, or None if unknown."""
        return self._records.get(task_id)

    def _queued_for_seat(self, seat) -> list[str]:
        """Queued task ids for a seat, oldest first (FIFO)."""
        return [
            task_id
            for task_id, rec in self._records.items()
            if rec.get("status") == "queued-for-seat" and rec.get("seat") == seat
        ]

    def _promote(self, seat) -> None:
        """If the seat is free and there's a queued task, promote the oldest."""
        if self._running.get(seat) is not None:
            return
        for task_id in self._queued_for_seat(seat):
            rec = self._records[task_id]
            rec["status"] = "running"
            rec.pop("user_notice", None)  # no longer held; notice no longer needed
            self._running[seat] = task_id
            return  # one promotion per call

    # -- lifecycle -------------------------------------------------------

    def complete(self, task_id: str) -> None:
        """Mark a task complete and free its seat (promote next in line)."""
        rec = self._records.get(task_id)
        if rec is None:
            return
        rec["status"] = "completed"
        seat = rec.get("seat")
        if self._running.get(seat) == task_id:
            self._running[seat] = None
        self._promote(seat)

    # -- watchdog wiring ---------------------------------------------------

    def assess_all(self, assess=None) -> None:
        """Run the assessor over every running task.

        A "kill" verdict terminates that task (status -> "killed") and frees
        its seat so the next queued task is promoted. Any other verdict
        ("ok", "overdue", "stuck") leaves the running task alone.

        ``assess`` defaults to DEFAULT_ASSESS (task_watchdog.assess). The
        assessor is invoked per-record; it may be the two-arg task_watchdog.assess
        (needs ``now``) or a one-arg assessor (e.g. a test fake).
        """
        if assess is None:
            assess = DEFAULT_ASSESS

        now = time.time()

        for task_id, rec in list(self._records.items()):
            if rec.get("status") != "running":
                continue
            verdict = self._assess_one(assess, rec, now)
            if verdict == "kill":
                rec["status"] = "killed"
                rec.pop("user_notice", None)
                seat = rec.get("seat")
                if self._running.get(seat) == task_id:
                    self._running[seat] = None
                self._promote(seat)

    def _assess_one(self, assess, rec, now):
        """Invoke ``assess`` on one record, adapting to its signature."""
        if _needs_now(assess):
            return assess(rec, now=now)
        return assess(rec)
