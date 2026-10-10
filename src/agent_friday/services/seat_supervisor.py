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

The background work queue (hotfix 1.0.2) is this supervisor and its
seat_queue, extended rather than duplicated:

- ``gate`` (services/background_gate.block_reason in production) decides when
  a local job may start: never during an interactive turn, and deferrable
  work only once the machine is idle. ``pump()`` re-asks it; the loop pumps
  every ``pump_seconds``.
- Inline work that runs on its caller's thread (a scheduled builtin, the
  prefix warm) is admitted with ``admit_inline``: it blocks until promoted
  and is released with ``on_task_end`` like any task.
- ``wait_while_blocked`` is the cooperative yield a RUNNING job makes before
  each local model call: it waits (the job keeps its place at the head of
  its seat) while the gate says so. Paused time is not watchdog time.
- Job keys dedupe: ``find_queued_job`` lets a second request merge into the
  queued one, and ``already_ran`` stops the same content running twice.
- ``snapshot()`` is what the Local AI chip, the tray and ``queue_status``
  read; ``run_now`` and ``cancel`` are the user's two controls.
- ``canonical_seat`` maps ``local/default`` (an undeclared model) to the
  brain's own seat, so two names for one seat never run two jobs at once.
"""

from __future__ import annotations

import collections
import inspect
import threading
import time
import uuid
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


def _is_terminal(status: Any) -> bool:
    """Terminal by this module's statuses OR the task registry's.

    The worker sets its own terminal status ('complete',
    'completed_unverified', ...) before its finally block calls
    ``on_task_end``. Overwriting that with the default 'completed' would
    relabel an unverified result as a plain completion, in memory only,
    after the journal recorded the honest one.
    """
    from agent_friday.services.task_journal import is_terminal
    return status in _TERMINAL or is_terminal(status)


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


def canonical_seat(seat: Any, brain_resolver: Optional[Callable[[], Optional[str]]] = None) -> str:
    """``local/default`` names whichever model the router will use: the brain.
    Name it, so a task with no declared model and one that declares the
    brain's id queue on the same seat."""
    seat = str(seat or "")
    if seat != "local/default":
        return seat
    try:
        if brain_resolver is None:
            from agent_friday.services import local_seats
            brain = local_seats.resolve("brain")
        else:
            brain = brain_resolver()
    except Exception:
        brain = None
    return ("local/" + str(brain)) if brain else seat


#: Plain-words labels by job kind, used when a job carries no label of its own.
#: Labels never quote what the job is about.
KIND_LABELS = {
    "wiki_distill": "wiki notes from today's voice chat",
    "scheduled": "a scheduled job",
    "prefix_warm": "getting the local model ready",
    "runner": "a background job",
    "task": "a background task",
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
    - gate: callable(record) -> "" or the reason a local job waits (the
      background work queue's rule; services/background_gate.block_reason)
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
        gate: Optional[Callable[[Dict[str, Any]], str]] = None,
        pump_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 0.5,
        brain_resolver: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        self.queue = (queue if queue is not None
                      else _seat_queue_mod.SeatQueue(gate=gate))
        # The same rule the queue applies at start, re-asked before each model
        # call of a running job (cooperative yield).
        self.gate = gate if gate is not None else getattr(self.queue, "gate", None)
        self.pump_seconds = pump_seconds
        self._sleep = sleep
        self.poll_seconds = poll_seconds
        self.brain_resolver = brain_resolver
        # Inline jobs: id -> Event set when promoted (no thread to start).
        self._inline_events: Dict[str, threading.Event] = {}
        # job key -> digest of the content it last ran with (bounded).
        self._ran: "collections.OrderedDict[str, str]" = collections.OrderedDict()
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
            if record["seat_is_local"]:
                record["seat"] = canonical_seat(record.get("seat"), self.brain_resolver)
            self._records[task_id] = record
            # A child of a running INLINE job shares its parent's slot: the
            # parent (a scheduled builtin waiting on what it spawned) would
            # otherwise hold the seat its own child queues for.
            parent = self._records.get(record.get("nested_in") or "")
            if (parent is not None and parent.get("inline")
                    and parent.get("status") == "running"
                    and parent.get("seat") == record.get("seat")):
                record["status"] = "running"
                record["started_at_q"] = time.time()
                self._start(record)
                return "running"
            status = self.queue.submit(record)
            record["status"] = status
            if status == QUEUED_STATUS:
                record["user_notice"] = USER_NOTICE
                stored = self.queue.get(task_id) or {}
                record["wait_reason"] = stored.get("wait_reason")
                # When the wait STARTED. Without it "how long has this been
                # waiting" would be measured from task creation, which counts
                # time the task spent running somewhere else.
                record["queued_at"] = time.time()
                return status
            record["started_at_q"] = time.time()
            self._start(record)
            return status

    def _start(self, record: Dict[str, Any]) -> None:
        task_id = record["id"]
        if task_id in self._started:
            return  # never start the same task twice
        event = self._inline_events.get(task_id)
        if event is not None:
            self._started.add(task_id)
            event.set()
            return
        if self.thread_factory is None:
            raise RuntimeError("seat_supervisor: no thread_factory configured")
        self._started.add(task_id)
        self.thread_factory(record)

    # ------------------------------------------------------------ the queue

    def find_queued_job(self, key: str, seat: Optional[str] = None) -> Optional[str]:
        """The queued job carrying ``key`` (merge target), or None."""
        if not key:
            return None
        with self._lock:
            if seat is not None and str(seat).startswith("local/"):
                seat = canonical_seat(seat, self.brain_resolver)
            return self.queue.find_queued(key, seat)

    def merge(self, task_id: str, **fields: Any) -> bool:
        """Fold a later request into the queued job ``task_id``: its fields
        (the latest payload) replace the waiting ones. One job, not two."""
        with self._lock:
            record = self._records.get(task_id)
            stored = self.queue.get(task_id)
            if record is None or record.get("status") != QUEUED_STATUS:
                return False
            record.update(fields)
            if stored is not None:
                stored.update(fields)
            record["merged"] = int(record.get("merged") or 0) + 1
            return True

    def already_ran(self, key: str, digest: str) -> bool:
        """Did a job with this key already run with exactly this content?"""
        if not key or not digest:
            return False
        with self._lock:
            return self._ran.get(key) == digest

    def pump(self) -> List[str]:
        """Re-ask the gate for queued work; start whatever it now allows."""
        started: List[str] = []
        with self._lock:
            pump = getattr(self.queue, "pump", None)
            if pump is not None:
                pump()
            while True:
                tid = self._sync_promotions()
                if tid is None:
                    break
                started.append(tid)
            for task_id, record in self._records.items():
                if record.get("status") == QUEUED_STATUS:
                    stored = self.queue.get(task_id) or {}
                    record["wait_reason"] = stored.get("wait_reason")
        return started

    def admit_inline(self, spec: Dict[str, Any], *, wait: bool = True,
                     timeout: Optional[float] = None) -> Optional[str]:
        """Admit work that runs on the CALLER's thread. Blocks until it is
        this job's turn and returns its id; the caller ends it with
        ``on_task_end(id)``. ``wait=False`` returns None (and leaves nothing
        queued) when it cannot start at once. Raises JobCancelled when the
        job is cancelled while waiting, TimeoutError past ``timeout``."""
        from agent_friday.services.background_gate import JobCancelled
        job_id = spec.get("id") or ("job-" + uuid.uuid4().hex[:12])
        record = dict(spec)
        record["id"] = job_id
        record["inline"] = True
        record.setdefault("kind", "task")
        record["created"] = time.time()
        event = threading.Event()
        with self._lock:
            self._inline_events[job_id] = event
            status = self.wire_spawn(record)
            if status == QUEUED_STATUS and not wait:
                self.queue.cancel(job_id)
                record["status"] = "cancelled"
                self._inline_events.pop(job_id, None)
                self._records.pop(job_id, None)
                return None
        deadline = None if timeout is None else time.monotonic() + timeout
        while not event.is_set():
            if record.get("cancel_requested"):
                with self._lock:
                    self._inline_events.pop(job_id, None)
                raise JobCancelled()
            if deadline is not None and time.monotonic() > deadline:
                self.cancel(job_id)
                raise TimeoutError("the local model stayed busy")
            self.pump()
            event.wait(self.poll_seconds)
        if record.get("cancel_requested"):
            self.on_task_end(job_id, status="cancelled")
            raise JobCancelled()
        return job_id

    def _running_block_reason(self, record: Dict[str, Any]) -> str:
        if self.gate is None:
            return ""
        try:
            return str(self.gate(record) or "")
        except Exception:
            return ""

    def wait_while_blocked(self, task_id: str) -> None:
        """The cooperative yield, called before each of a running job's local
        model calls: wait while an interactive turn is active, or while the
        user is back for deferrable work. The job keeps the seat (it is the
        head of the line, and its prompt prefix is still the seat's cache);
        nothing mid-generation is interrupted. Raises JobCancelled."""
        from agent_friday.services.background_gate import JobCancelled
        while True:
            with self._lock:
                record = self._records.get(task_id)
            if record is None:
                return
            if record.get("cancel_requested"):
                raise JobCancelled()
            reason = self._running_block_reason(record)
            if not reason:
                if record.pop("paused_reason", None) is not None:
                    record.pop("paused_at", None)
                return
            record["paused_reason"] = reason
            record.setdefault("paused_at", time.time())
            self._sleep(self.poll_seconds)

    def run_now(self, task_id: str) -> bool:
        """Skip the idle wait for this job. It still runs one at a time and
        still yields to interactive turns."""
        with self._lock:
            record = self._records.get(task_id)
            if record is None or _is_terminal(record.get("status")):
                return False
            for rec in (record, self.queue.get(task_id)):
                if rec is not None:
                    rec["run_now"] = True
                    rec["head"] = True
        self.pump()
        return True

    def cancel(self, task_id: str,
               on_cancel: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        """Cancel a job: a queued one leaves the queue at once; a running one
        stops before its next model call."""
        with self._lock:
            record = self._records.get(task_id)
            if record is None or _is_terminal(record.get("status")):
                return False
            record["cancel_requested"] = True
            if record.get("status") == QUEUED_STATUS:
                self.queue.cancel(task_id)
                record["status"] = "cancelled"
                record.pop("wait_reason", None)
                event = self._inline_events.pop(task_id, None)
                if event is not None:
                    event.set()
        if on_cancel is not None:
            try:
                on_cancel(record)
            except Exception:
                pass
        self.pump()
        return True

    def snapshot(self) -> Dict[str, Any]:
        """The local queue as the chip, the tray and queue_status show it:
        the running job (if any) and the queued ones in promotion order.
        Labels only, never content."""
        def _row(tid: str, rec: Dict[str, Any], state: str) -> Dict[str, Any]:
            kind = str(rec.get("job_kind") or rec.get("kind") or "task")
            label = (rec.get("job_label") or rec.get("label") or KIND_LABELS.get(kind)
                     or KIND_LABELS["task"])
            why = ""
            if state == "queued":
                why = rec.get("wait_reason") or _seat_queue_mod.SEAT_BUSY_REASON
            elif rec.get("paused_reason"):
                why = rec["paused_reason"]
                state = "paused"
            return {"id": tid, "label": str(label)[:80], "kind": kind,
                    "state": state, "why": why, "seat": rec.get("seat"),
                    "deferrable": bool(rec.get("deferrable")),
                    "run_now": bool(rec.get("run_now")),
                    "queued_at": rec.get("queued_at"),
                    "merged": int(rec.get("merged") or 0)}
        with self._lock:
            running = []
            for tid, rec in self._records.items():
                if (rec.get("status") == "running" and rec.get("seat_is_local")
                        and tid in self._started):
                    running.append(_row(tid, rec, "running"))
            queued_ids = []
            q = getattr(self.queue, "queued", None)
            if q is not None:
                queued_ids = [t for t in q() if t in self._records]
            queued = [_row(t, self._records[t], "queued") for t in queued_ids
                      if self._records[t].get("status") == QUEUED_STATUS]
        return {"running": running, "queued": queued,
                "count": len(queued), "jobs": running + queued}

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
            if record is not None:
                if not _is_terminal(record.get("status")):
                    record["status"] = status
                record.pop("paused_reason", None)
                record.pop("wait_reason", None)
                key, digest = record.get("job_key"), record.get("job_digest")
                if (key and digest and task_id in self._started
                        and status == "completed" and not record.get("cancel_requested")):
                    self._ran[key] = digest
                    self._ran.move_to_end(key)
                    while len(self._ran) > 256:
                        self._ran.popitem(last=False)
                if record.get("inline"):
                    # An inline job has no TASKS record to keep it visible;
                    # once it ends, it leaves the supervisor too.
                    self._records.pop(task_id, None)
            self._inline_events.pop(task_id, None)
            self.queue.complete(task_id)
            promoted = self._sync_promotions()
        self.pump()
        return promoted

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
                record.pop("wait_reason", None)
                record["started_at_q"] = time.time()
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
                if record.get("paused_reason"):
                    continue  # yielding to the user is not being stuck
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
        next_assess = 0.0
        step = max(0.05, min(self.pump_seconds, self.cadence_seconds))
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_assess:
                try:
                    self.assess_pass()
                except Exception:  # pragma: no cover — the loop must never die
                    pass
                next_assess = now + self.cadence_seconds
            try:
                self.pump()
            except Exception:  # pragma: no cover
                pass
            self._stop.wait(step)


# ---------------------------------------------------------------- the words

def _count_words(n: int) -> str:
    return "%d task%s queued" % (n, "" if n == 1 else "s")


def spoken_summary(snap: Dict[str, Any]) -> str:
    """The Local AI queue in one or two sentences (queue_status, voice)."""
    running = list(snap.get("running") or [])
    queued = list(snap.get("queued") or [])
    if not running and not queued:
        return "Nothing is waiting for the local AI."
    parts = []
    if running:
        r = running[0]
        if r.get("state") == "paused":
            parts.append("Running %s, paused: it %s." % (r["label"], r.get("why") or "waits"))
        else:
            parts.append("Running %s." % r["label"])
    if queued:
        q = queued[0]
        parts.append("%s. Next: %s, which %s." % (_count_words(len(queued)).capitalize(),
                                                  q["label"], q.get("why") or "waits"))
    return " ".join(parts)


def tray_tooltip(snap: Optional[Dict[str, Any]], limit: int = 127) -> Optional[str]:
    """``Local AI: 3 tasks queued · next: <label> · waits for idle``, or
    ``Local AI: running: <label>``; None when the queue is empty. Windows
    refuses a tray tip over 127 characters, so it is cut to fit."""
    if not snap:
        return None
    running = list(snap.get("running") or [])
    queued = list(snap.get("queued") or [])
    if queued:
        q = queued[0]
        text = "Local AI: %s · next: %s · %s" % (
            _count_words(len(queued)), q.get("label") or "a background task",
            q.get("why") or "waits")
    elif running:
        text = "Local AI: running: %s" % (running[0].get("label") or "a background task")
    else:
        return None
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text
