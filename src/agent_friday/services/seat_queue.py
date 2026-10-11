"""Per-seat task queue for Defect D (RSI loop): one job per local seat at a time.

Regression case: multiple workflow tasks dispatched onto the single-slot bonsai2
seat simultaneously serialize invisibly at the HTTP layer with no status, and
the user has to cancel them by hand. This
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

The background work queue (hotfix 1.0.2) is this queue, not a second one:

- An optional ``gate(record) -> reason`` decides whether a local task may
  take its free seat now (an interactive turn is active, or deferrable work
  waits for idle). A gated task stays ``queued-for-seat`` with its
  ``wait_reason``; ``pump()`` re-asks the gate. A faulting gate fails open.
- Promotion takes the first queued task the gate allows, head-of-line ones
  (``head``: a job that yielded, or one the user asked to run now) first,
  FIFO otherwise. Without a gate and head flags the order is plain FIFO.
- ``seat_gate`` / ``seat_why`` / ``on_run`` / ``on_done`` are the bridge to the
  local work scheduler's admission queue (services/turn_queue,
  ``attach_seat_queue``): a person's turn on the seat goes first, and a running
  task holds a ``background`` slot there. ``lend_slot`` / ``reclaim_slot`` hand
  that slot back while a running task is paused for a person's turn, so the
  turn is never queued behind the job it is waiting on.
- ``find_queued(key)`` lets a request whose job key is already waiting merge
  into it instead of queueing a second job.
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

#: Why a queued task waits when the only obstacle is the busy seat.
SEAT_BUSY_REASON: str = "waits for the local model"

#: Why a queued task waits when a person's turn holds or awaits its seat.
PEOPLE_FIRST_REASON: str = "waits for your chat to finish"

QUEUED = "queued-for-seat"


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

    def __init__(self, gate=None) -> None:
        # task_id -> record (status is always present, stamped by submit)
        self._records: dict[str, dict] = {}
        # seat -> task_id currently running on that local seat (None when free)
        self._running: dict[str, str] = {}
        # gate(record) -> "" when the task may take a free local seat now,
        # else the plain-words reason it waits (the Local AI queue's rule:
        # services/background_gate.block_reason). None: the seat is the only rule.
        self.gate = gate
        # The bridge to services/turn_queue (attach_seat_queue), which makes
        # this the `background` class of the seat's admission queue. All four
        # stay None on a bare SeatQueue, which behaves exactly as before.
        #   seat_gate(seat, rec) -> bool  False while a person holds or awaits the seat
        #   seat_why(seat, rec) -> str    the plain words for that
        #   on_run(seat, id, record)  a task started running on a local seat
        #   on_done(seat, id)         that task freed the seat
        self.seat_gate = None
        self.seat_why = None
        self.on_run = None
        self.on_done = None

    def _seat_block(self, seat, rec=None) -> str:
        """Why a person's turn (not a rule about this job) holds the seat, or ""."""
        if self.seat_gate is None:
            return ""
        try:
            if self.seat_gate(seat, rec):
                return ""
        except Exception:
            return ""  # a broken gate must never strand background work
        why = ""
        if self.seat_why is not None:
            try:
                why = str(self.seat_why(seat, rec) or "")
            except Exception:
                why = ""
        return why or PEOPLE_FIRST_REASON

    def _gate_reason(self, rec: dict) -> str:
        """The plain-words reason this task may not take a free local seat now:
        a person's turn first, then the Local AI queue's rule. "" = it may."""
        held = self._seat_block(rec.get("seat"), rec)
        if held:
            return held
        if self.gate is None:
            return ""
        try:
            return str(self.gate(rec) or "")
        except Exception:
            # Fail open: a broken gate must never strand background work.
            return ""

    def _notify(self, hook, *args) -> None:
        if hook is None:
            return
        try:
            hook(*args)
        except Exception:
            pass

    # -- submission -------------------------------------------------------

    def submit(self, task: dict) -> str:
        """Add ``task`` to the queue. Returns the status assigned.

        Local seat already busy, or the gate says wait -> held with status
        'queued-for-seat', the USER_NOTICE and its ``wait_reason``. Otherwise
        (cloud seat, or free local seat the gate allows) -> 'running'.
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
            # Local seat: only one running at a time, only when no person is
            # running or waiting on it, and only when the gate allows it.
            reason = (SEAT_BUSY_REASON if self._running.get(seat) is not None
                      else self._gate_reason(rec))
            if not reason:
                status = "running"
                self._running[seat] = rec.get("id")
                self._notify(self.on_run, seat, rec.get("id"), rec)
            else:
                status = QUEUED
                # Attach the honest notice to the held record.
                rec["user_notice"] = USER_NOTICE
                rec["wait_reason"] = reason

        # Stamp the status so the stored record is inspectable via get().
        rec["status"] = status
        self._records[rec.get("id")] = rec
        return status

    # -- inspection -------------------------------------------------------

    def get(self, task_id: str):
        """Return the stored record for ``task_id``, or None if unknown."""
        return self._records.get(task_id)

    def running_on(self, seat):
        """The task id running on ``seat``, or None."""
        return self._running.get(seat)

    def find_queued(self, key, seat=None):
        """The id of the queued task carrying job key ``key`` (on ``seat`` when
        given), or None."""
        if not key:
            return None
        for task_id, rec in self._records.items():
            if (rec.get("status") == QUEUED and rec.get("job_key") == key
                    and (seat is None or rec.get("seat") == seat)):
                return task_id
        return None

    def queued(self) -> list[str]:
        """Every queued task id, in the order each seat would promote them."""
        out = []
        for seat in self._seats_with_queued():
            out.extend(self._queued_for_seat(seat))
        return out

    def _seats_with_queued(self) -> list:
        seen = []
        for rec in self._records.values():
            if rec.get("status") == QUEUED and rec.get("seat") not in seen:
                seen.append(rec.get("seat"))
        return seen

    def _queued_for_seat(self, seat) -> list[str]:
        """Queued task ids for a seat: head-of-line first, then oldest first
        (FIFO). sorted() is stable, so equal keys keep insertion order."""
        ids = [
            task_id
            for task_id, rec in self._records.items()
            if rec.get("status") == QUEUED and rec.get("seat") == seat
        ]
        return sorted(ids, key=lambda t: 0 if self._records[t].get("head") else 1)

    def _promote(self, seat):
        """If the seat is free, promote the first queued task the gate allows.
        Returns the promoted id, or None. Every task left waiting gets its
        current reason."""
        if self._running.get(seat) is not None:
            for task_id in self._queued_for_seat(seat):
                rec = self._records[task_id]
                # The reason that will still hold when the seat frees comes
                # first: "waits for idle" says more than "the model is busy".
                rec["wait_reason"] = self._gate_reason(rec) or SEAT_BUSY_REASON
            return None
        promoted = None
        for task_id in self._queued_for_seat(seat):
            rec = self._records[task_id]
            if promoted is not None:
                rec["wait_reason"] = self._gate_reason(rec) or SEAT_BUSY_REASON
                continue
            reason = self._gate_reason(rec)
            if reason:
                rec["wait_reason"] = reason
                continue
            rec["status"] = "running"
            rec.pop("user_notice", None)  # no longer held; notice no longer needed
            rec.pop("wait_reason", None)
            self._running[seat] = task_id
            self._notify(self.on_run, seat, task_id, rec)
            promoted = task_id  # one promotion per call
        return promoted

    def pump(self) -> list[str]:
        """Re-ask the gate for every seat that has queued work and promote
        what it now allows. Returns the promoted ids."""
        out = []
        for seat in self._seats_with_queued():
            tid = self._promote(seat)
            if tid is not None:
                out.append(tid)
        return out

    def startable_behind(self, task_id: str):
        """The first task queued on ``task_id``'s seat that the gate would
        start now, or None. Asked when the running task pauses: a pause that
        does not hold the next task back should not hold the seat either."""
        rec = self._records.get(task_id)
        if rec is None:
            return None
        for tid in self._queued_for_seat(rec.get("seat")):
            if tid != task_id and not self._gate_reason(self._records[tid]):
                return tid
        return None

    def yield_seat(self, task_id: str, reason: str) -> bool:
        """The RUNNING task gives its seat up and waits at the head of the
        line with ``reason``; the next task the gate allows is promoted."""
        rec = self._records.get(task_id)
        if rec is None or rec.get("status") != "running":
            return False
        seat = rec.get("seat")
        if self._running.get(seat) == task_id:
            self._running[seat] = None
            if not rec.pop("slot_lent", False):
                self._notify(self.on_done, seat, task_id)
        rec["status"] = QUEUED
        rec["head"] = True
        rec["wait_reason"] = reason
        self._promote(seat)
        return True

    def lend_slot(self, task_id: str) -> bool:
        """A RUNNING task that is paused before its next model call (a person's
        turn is active) hands its admission slot back to the turn queue, so the
        turn it waits for is not queued behind it. It keeps its seat here."""
        rec = self._records.get(task_id)
        if rec is None or rec.get("status") != "running" or rec.get("slot_lent"):
            return False
        rec["slot_lent"] = True
        self._notify(self.on_done, rec.get("seat"), task_id)
        return True

    def reclaim_slot(self, task_id: str) -> bool:
        """The paused task resumes: it takes its admission slot back."""
        rec = self._records.get(task_id)
        if rec is None or not rec.pop("slot_lent", False):
            return False
        if rec.get("status") == "running":
            self._notify(self.on_run, rec.get("seat"), task_id, rec)
        return True

    def cancel(self, task_id: str) -> bool:
        """Withdraw a QUEUED task. A running one is not stopped here."""
        rec = self._records.get(task_id)
        if rec is None or rec.get("status") != QUEUED:
            return False
        rec["status"] = "cancelled"
        rec.pop("user_notice", None)
        rec.pop("wait_reason", None)
        return True

    def promote_ready(self) -> None:
        """Promote on every seat that has queued work and is now free (the
        gate opened). A no-op for a seat still held."""
        self.pump()

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
            self._notify(self.on_done, seat, task_id)
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
                    self._notify(self.on_done, seat, task_id)
                self._promote(seat)

    def _assess_one(self, assess, rec, now):
        """Invoke ``assess`` on one record, adapting to its signature."""
        if _needs_now(assess):
            return assess(rec, now=now)
        return assess(rec)
