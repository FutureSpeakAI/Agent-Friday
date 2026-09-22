"""Seat supervisor: wires seat_queue admission and task_watchdog enforcement
into the dispatch layer (Defect E — the glue that makes C and D govern).

Invariants this module protects:

- One task per local seat. A spawn aimed at a busy local seat is admitted as
  ``queued-for-seat`` and gets NO worker thread until promoted. The caller's
  live task record (not seat_queue's internal copy) carries the status and
  the user-facing notice, so the task tray renders honest state directly.
- FIFO promotion. When a running task ends (completion or watchdog kill),
  the freed seat's oldest queued task is started via the injected thread
  factory — the same construction the dispatcher would have used at spawn.
- Watchdog verdicts are enforced from OUTSIDE the (possibly blocked) worker
  thread. ``kill`` -> record marked ``failed:watchdog-kill`` (tier 1, always),
  and for LOCAL seats — when ``watchdog_kill_reclaims_seat`` is enabled and a
  reclaim callback is wired — the seat process is reclaimed (tier 2). Cloud
  seats never get tier 2: there is no local process to reclaim.
- The thread factory owns thread creation and starting. The supervisor calls
  it exactly once per task: at admission for running tasks, at promotion for
  queued ones. It never calls it twice for the same task.
"""

from __future__ import annotations

import inspect
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from agent_friday.services import seat_queue as _seat_queue_mod
from agent_friday.services import task_watchdog as _task_watchdog

DEFAULT_CADENCE_SECONDS = 30

# The exact function object, not a reimplementation — the same rule
# seat_queue.DEFAULT_ASSESS follows.
DEFAULT_ASSESS = _task_watchdog.assess

QUEUED_STATUS = "queued-for-seat"
KILLED_STATUS = "failed:watchdog-kill"
USER_NOTICE = _seat_queue_mod.USER_NOTICE

_TERMINAL = frozenset({"completed", "failed", KILLED_STATUS, "cancelled", "killed"})


def _seat_is_local(record: Dict[str, Any]) -> bool:
    """Derive locality. An explicit seat_is_local wins; otherwise the seat
    name's prefix decides ("local/..." vs "cloud/...")."""
    if "seat_is_local" in record:
        return bool(record["seat_is_local"])
    seat = str(record.get("seat", ""))
    return seat.startswith("local/")


def _watchdog_view(record: Dict[str, Any], now: float) -> Dict[str, Any]:
    """Adapt a dispatcher task record to task_watchdog.assess's field names.

    Dispatcher records carry created_at / timeout / tool_calls; the watchdog
    contract wants started_at / timeout_seconds / tool_call_count /
    last_tool_call_at / stop_requested / status.
    """
    started = record.get("started_at", record.get("created_at", now))
    return {
        "status": record.get("status", "running"),
        "started_at": float(started),
        "timeout_seconds": float(
            record.get("timeout_seconds", record.get("timeout", 0.0))
        ),
        "tool_call_count": int(
            record.get("tool_call_count", record.get("tool_calls", 0))
        ),
        "last_tool_call_at": record.get("last_tool_call_at"),
        "stop_requested": bool(record.get("stop_requested", False)),
    }


def _wants_now(assess: Callable) -> bool:
    try:
        sig = inspect.signature(assess)
    except (TypeError, ValueError):
        return True
    return "now" in sig.parameters


class SeatSupervisor:
    """Glue between _spawn_task, seat_queue, and task_watchdog.

    Collaborators are injected so the contract tests run without real threads
    or processes:

    - queue: a seat_queue.SeatQueue (or contract-compatible object)
    - thread_factory: callable(record); owns creating AND starting the worker.
      Called exactly once per task.
    - settings: mapping; honors ``watchdog_kill_reclaims_seat`` (default True)
    - reclaim_seat: callable(seat) for tier-2 reclaim; local seats only
    - assess: verdict function; defaults to task_watchdog.assess
    """

    def __init__(
        self,
        queue: Optional[Any] = None,
        thread_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
        settings: Optional[Dict[str, Any]] = None,
        reclaim_seat: Optional[Callable[[str], None]] = None,
        assess: Callable[..., str] = DEFAULT_ASSESS,
        cadence_seconds: float = DEFAULT_CADENCE_SECONDS,
        on_queued_wait: Optional[Callable[[Dict[str, Any], float], None]] = None,
    ) -> None:
        self.queue = queue if queue is not None else _seat_queue_mod.SeatQueue()
        self.thread_factory = thread_factory
        self.settings = dict(settings or {})
        self.reclaim_seat = reclaim_seat
        self.assess = assess
        self.cadence_seconds = cadence_seconds
        # Called once per pass for each record still WAITING for a seat, with
        # how long it has waited. Injected rather than imported so this module
        # keeps knowing nothing about approvals or money: the policy (ask
        # before paying a cloud provider) lives at the wiring site, and this
        # file keeps its one job, which is who runs next.
        self.on_queued_wait = on_queued_wait
        self._records: Dict[str, Dict[str, Any]] = {}
        self._started: set = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._loop_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ spawn

    def wire_spawn(self, record: Dict[str, Any]) -> str:
        """Admission gate for _spawn_task. Returns the admitted status.

        'running'         -> the thread factory was invoked (worker started).
        'queued-for-seat' -> NO thread; the caller's record is mutated in
                             place with the queued status and the user notice.
        """
        task_id = record["id"]
        with self._lock:
            record["seat_is_local"] = _seat_is_local(record)
            self._records[task_id] = record
            status = self.queue.submit(record)
            record["status"] = status
            if status == QUEUED_STATUS:
                record["user_notice"] = USER_NOTICE
                # When the wait STARTED. Without it "how long has this been
                # waiting" would be measured from task creation, which counts
                # time the task spent running somewhere else.
                record["queued_at"] = time.time()
                return status
            self._start(record)
            return status

    def _start(self, record: Dict[str, Any]) -> None:
        if self.thread_factory is None:
            raise RuntimeError("seat_supervisor: no thread_factory configured")
        task_id = record["id"]
        if task_id in self._started:
            return  # never start the same task twice
        self._started.add(task_id)
        self.thread_factory(record)

    def _notify_waiting(self, now: float) -> None:
        """Tell the wiring site about every task still waiting, and for how long.

        Best-effort and outside the lock: the callback may raise an approval
        card or touch disk, and none of that may stall promotion or take the
        supervisor loop down with it.
        """
        cb = self.on_queued_wait
        if cb is None:
            return
        with self._lock:
            waiting = [r for r in self._records.values()
                       if r.get("status") == QUEUED_STATUS]
        for record in waiting:
            since = record.get("queued_at") or record.get("created") or now
            try:
                cb(record, max(0.0, now - float(since)))
            except Exception:
                pass

    # ------------------------------------------------------------ completion

    def on_task_end(self, task_id: str, status: str = "completed") -> Optional[str]:
        """Called when a worker ends (its finally block, or kill enforcement).
        Marks the live record terminal, frees the seat via the queue, and
        starts whichever task the queue promoted. Returns the promoted
        task_id, or None."""
        with self._lock:
            record = self._records.get(task_id)
            if record is not None and record.get("status") not in _TERMINAL:
                record["status"] = status
            self.queue.complete(task_id)
            return self._sync_promotions()

    def _sync_promotions(self) -> Optional[str]:
        """seat_queue promotes on its own copies; sync any newly-running
        queued task back onto the live record and start its thread."""
        for task_id, record in self._records.items():
            if task_id in self._started:
                continue
            stored = self.queue.get(task_id)
            if stored is not None and stored.get("status") == "running":
                record["status"] = "running"
                record.pop("user_notice", None)
                self._start(record)
                return task_id
        return None

    # -------------------------------------------------------------- watchdog

    def assess_pass(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """One supervisor pass: assess every live running record, enforce
        kill verdicts (two-tier), promote freed seats. Returns events."""
        if now is None:
            now = time.time()
        events: List[Dict[str, Any]] = []
        self._notify_waiting(now)
        with self._lock:
            for task_id, record in list(self._records.items()):
                if record.get("status") != "running":
                    continue
                view = _watchdog_view(record, now)
                if _wants_now(self.assess):
                    verdict = self.assess(view, now=now)
                else:
                    verdict = self.assess(view)
                if verdict != "kill":
                    continue
                seat = record.get("seat", "")
                # Tier 1: terminal record, seat freed. Always.
                record["status"] = KILLED_STATUS
                record["kill_tier"] = 1
                # Tier 2: process reclaim. Local seats only, behind the setting.
                if (
                    record.get("seat_is_local", _seat_is_local(record))
                    and self.settings.get("watchdog_kill_reclaims_seat", True)
                    and self.reclaim_seat is not None
                ):
                    self.reclaim_seat(seat)
                    record["kill_tier"] = 2
                promoted = self.on_task_end(task_id, status=KILLED_STATUS)
                events.append(
                    {
                        "task_id": task_id,
                        "verdict": "kill",
                        "kill_tier": record["kill_tier"],
                        "seat": seat,
                        "promoted": promoted,
                    }
                )
        return events

    # ------------------------------------------------------------------ loop

    def start(self) -> None:
        """Start the background supervisor loop (daemon thread)."""
        if self._loop_thread is not None and self._loop_thread.is_alive():
            return
        self._stop.clear()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="seat-supervisor", daemon=True
        )
        self._loop_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=self.cadence_seconds + 5)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.assess_pass()
            except Exception:  # pragma: no cover — the loop must never die
                pass
            self._stop.wait(self.cadence_seconds)
