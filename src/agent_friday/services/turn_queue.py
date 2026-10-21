"""Honest queueing for the local brain: one admission queue per local seat.

THE PROBLEM. The local brain serves one turn at a time (llama-server runs with
``-np 1``). Without a queue, a second chat, a voice turn or a background job
sits invisibly inside its HTTP call while the first finishes: no status, no
position, a stream quiet long enough for a proxy to drop it, and no way to say
"never mind". ``seat_queue`` queues background *tasks* only, and only against
each other.

THIS MODULE is the one place that decides who is next on a local seat.

* One ``TurnQueue`` per local seat, with a capacity (the seat's ``-np``,
  default 1). Cloud seats are never queued: ``acquire(local=False)`` returns at
  once and holds nothing.
* Classes, in priority order: ``voice`` > ``chat`` > ``api`` (the Local API,
  a sub-priority of chat) > ``user_job`` (work the owner asked for) >
  ``background``. First in, first out inside a class. A waiting voice turn goes NEXT; it does
  not interrupt the turn that is running (pre-empting would evict the single
  KV slot and cost a 30-45 s re-read).
* Admission is per TURN and is held across the whole agent loop, tool
  execution included, so one conversation's prefix stays warm. The exception is
  a turn that sits in tool execution for longer than ``tool_release_s`` (60 s by
  default): it lets the seat go and re-queues at the HEAD of its class, so a
  slow tool cannot block a person who is waiting. It does not give way if
  nobody is.
* While a turn waits, ``on_status`` hears a snapshot at once and then every
  ``status_every_s`` (5 s), which is also what keeps an SSE stream alive.
  ``cancel(turn_id)`` (the Stop button) removes a waiting turn immediately; it
  never reaches the model.
* The existing background ``seat_queue.SeatQueue`` is the ``background`` class
  (``attach_seat_queue``). It is bridged, not rewritten: a background task that
  starts running on a local seat holds a ``background`` slot here, and the
  SeatQueue is told not to start another while a person is waiting or running
  on that seat. People always go before background jobs; a running background
  job is never pre-empted either.
* ``cloud_spill`` stays the one offer ("Answer this one in the cloud
  instead?"). ``acquire`` calls the ``offer`` hook at most ONCE per wait, after
  ``offer_after_s``, and never acts on it itself: the answer is a card the owner
  decides (``grant_cloud``). Never an automatic substitution.

VOICE. A voice brain turn is admitted as ``voice`` by the transport it runs
through (``services/local_scheduler.admit``: the voice front's own seat and the
brain's agent loop), not by the route. ``VOICE_WAIT_S`` and ``VOICE_WAIT_LINE``
are here so the spoken line is one string.

THE LOCAL WORK SCHEDULER (``services/local_scheduler``) extends this module:
every local transport admits through it; the gate hook (``TurnQueue.gate``)
holds ``user_job`` and ``background`` tickets back while a model swap, an idle
window or the memory floor says wait; a seat's capacity is its real ``-np``.
Work that runs inside an admitted turn does not admit again (``HELD``).

Pure stdlib, no I/O beyond the optional offer hook, importable without side
effects.
"""
from __future__ import annotations

import contextvars
import functools
import itertools
import logging
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger(__name__)

#: Priority order. Index = priority (lower goes first).
CLASSES = ("voice", "chat", "api", "user_job", "background")
_PRIORITY = {c: i for i, c in enumerate(CLASSES)}
#: Classes the scheduler's gate may hold back (a person's turn never waits on it).
GATED_CLASSES = ("user_job", "background")

#: A turn in tool execution this long lets go of the seat (configurable:
#: ``turn_queue_tool_release_s``).
DEFAULT_TOOL_RELEASE_S = 60.0
#: How often a waiting turn is told where it stands (SSE status + keepalive).
DEFAULT_STATUS_EVERY_S = 5.0
#: How long a turn waits before the one cloud offer (cloud_spill's own floor).
DEFAULT_OFFER_AFTER_S = 60.0
#: A voice turn that has waited this long gets one spoken line from the front.
VOICE_WAIT_S = 5.0
VOICE_WAIT_LINE = "One moment, I'm finishing another answer."

_WAIT_POLL_S = 0.2

#: The id of the stand-in ticket the seat queue's bridge hands the gate when it
#: asks "could a task start on this seat now". It is not a real ticket.
PROBE_ID = "seat-queue-probe"


def class_priority(cls: str) -> int:
    return _PRIORITY.get(cls, _PRIORITY["chat"])


def norm_seat(seat: Any) -> str:
    """One spelling per seat: ``local/bonsai2:27b`` and ``bonsai2:27b`` agree,
    and an empty seat or ``default`` is the default local seat."""
    s = str(seat or "").strip().lower()
    if s.startswith("local/"):
        s = s[len("local/"):]
    if not s or s == "default":
        return "default"
    # The configured brain model IS the default seat: one server, one line.
    try:
        from agent_friday.services import local_seats as _ls
        brain = str(_ls._configured_local_model() or "").strip().lower()
        if brain.startswith("local/"):
            brain = brain[len("local/"):]
        if brain and s == brain:
            return "default"
    except Exception:
        pass
    return s


_NP_RE = re.compile(r"(?:^|\s)(?:-np|--parallel)[\s=]+(\d+)")


def capacity_from_args(args: Any, default: int = 1) -> int:
    """The ``-np`` of a llama-server argument list (or string); ``default``
    when it names none."""
    try:
        if isinstance(args, (list, tuple)):
            args = " ".join(str(a) for a in args)
        m = _NP_RE.search(" " + str(args or ""))
        return max(1, int(m.group(1))) if m else max(1, int(default))
    except Exception:
        return max(1, int(default))


def _settings() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def tool_release_s() -> float:
    try:
        v = float(_settings().get("turn_queue_tool_release_s", DEFAULT_TOOL_RELEASE_S))
        return v if v > 0 else DEFAULT_TOOL_RELEASE_S
    except (TypeError, ValueError):
        return DEFAULT_TOOL_RELEASE_S


#: seat key -> (read at, slots). The Arbiter record changes only when a seat is
#: spawned again, so a short memo keeps the lookup off the hot path.
_NP_MEMO: Dict[str, Any] = {}
_NP_MEMO_S = 30.0


def _seat_model(seat: Any) -> str:
    """The model id a seat key stands for (``default`` is the configured brain)."""
    s = norm_seat(seat)
    if s != "default":
        return s
    try:
        from agent_friday.services import local_seats as _ls
        brain = str(_ls._configured_local_model() or "").strip()
        return brain[len("local/"):] if brain.lower().startswith("local/") else brain
    except Exception:
        return ""


def seat_np(seat: Any) -> Optional[int]:
    """The ``-np`` the seat really runs with, or None when no record says.

    Read from the live llama-server command line the Arbiter holds for the
    seat; failing that, from the serve args the model's record declares (the
    only place ``-np`` reaches a seat). Never guessed: the caller falls back
    to one slot."""
    model = _seat_model(seat)
    if not model:
        return None
    now = time.monotonic()
    hit = _NP_MEMO.get(model)
    if hit is not None and now - hit[0] < _NP_MEMO_S:
        return hit[1]
    np_ = None
    try:
        from agent_friday.services import residency_arbiter as _ra
        arb = _ra.get_arbiter()
        proc = ((getattr(getattr(arb, "llama", None), "procs", None) or {}).get(model) or (None,))[0]
        argv = getattr(proc, "args", None)
        if isinstance(argv, (list, tuple, str)) and _NP_RE.search(" " + (
                " ".join(map(str, argv)) if not isinstance(argv, str) else argv)):
            np_ = capacity_from_args(argv)
        if np_ is None:
            declared = _ra.LlamaServerBackend._declared_serve_args(model)
            if declared and _NP_RE.search(" " + " ".join(declared)):
                np_ = capacity_from_args(declared)
    except Exception:
        np_ = None
    _NP_MEMO[model] = (now, np_)
    return np_


def seat_capacity(seat: Any) -> int:
    """Slots on a local seat: the owner's ``turn_queue_seat_slots`` entry for
    it, else the seat's real ``-np`` (``seat_np``), else 1 (every shipped tier
    runs ``-np 1`` unless its record says otherwise)."""
    try:
        m = _settings().get("turn_queue_seat_slots") or {}
        if isinstance(m, dict):
            v = m.get(norm_seat(seat))
            if v is not None:
                return max(1, int(v))
    except Exception:
        pass
    np_ = seat_np(seat)
    return max(1, int(np_)) if np_ else 1


# ─────────────────────────────────────────────────────────────────────────────
#  Tickets and queues
# ─────────────────────────────────────────────────────────────────────────────
class Ticket:
    """One turn's place in a seat's queue."""

    __slots__ = ("id", "cls", "seat", "title", "conversation_id", "seq", "head",
                 "state", "enqueued_at", "admitted_at", "tool_since",
                 "offered", "cloud_granted", "parked", "label", "model", "kind",
                 "blocked", "needs", "deferrable")

    def __init__(self, id, cls, seat, title=None, conversation_id=None, label=None,
                 model=None, kind=None, needs=None, deferrable=True):
        self.id = str(id)
        self.cls = cls if cls in _PRIORITY else "chat"
        self.seat = seat
        self.title = title
        self.conversation_id = conversation_id
        self.label = label
        self.model = model          # the model this job needs (None: the seat's own)
        self.kind = kind            # the call site / job kind, for status and metrics
        self.blocked = ""           # why the scheduler's gate holds it, in words
        self.needs = needs          # what it needs, in words ("the big brain")
        self.deferrable = deferrable    # background only: False = time-bound, never waits for idle
        self.seq = 0
        self.head = False
        self.state = "new"            # new | waiting | running | done | cancelled
        self.enqueued_at = 0.0
        self.admitted_at = 0.0
        self.tool_since = None
        self.offered = False
        self.cloud_granted = False
        self.parked = False

    @property
    def is_person(self) -> bool:
        return self.cls != "background"


class TurnQueue:
    """The admission queue for ONE local seat."""

    def __init__(self, seat: str, capacity: int = 1, clock: Callable[[], float] = time.monotonic):
        self.seat = seat
        self.capacity = max(1, int(capacity))
        self._clock = clock
        self._cv = threading.Condition()
        self._waiting: List[Ticket] = []
        self._running: Dict[str, Ticket] = {}
        self._seq = itertools.count(1)
        self._idle_hooks: List[Callable[[str], None]] = []
        self._last_busy: Optional[float] = None
        #: ``gate(ticket) -> str``: why a ``user_job`` / ``background`` ticket may
        #: not start yet ("" when it may). Set by services/local_scheduler.
        self.gate: Optional[Callable[[Ticket], str]] = None
        self._blocked: Dict[str, str] = {}
        self.pinned = False       # capacity was set by a caller, not read from the seat

    # -- inspection -------------------------------------------------------
    def _order_key(self, t: Ticket):
        return (_PRIORITY[t.cls], 0 if t.head else 1, t.seq)

    def _ordered_locked(self) -> List[Ticket]:
        return sorted(self._waiting, key=self._order_key)

    def people_active(self) -> bool:
        """A person is running or waiting here. Background work gives way."""
        with self._cv:
            return (any(t.is_person for t in self._waiting)
                    or any(t.is_person for t in self._running.values()))

    def idle_for(self) -> Optional[float]:
        """Seconds since anything last ran or waited here; 0.0 while something
        does, None when nothing has since this process started."""
        with self._cv:
            if self._waiting or self._running:
                return 0.0
            if self._last_busy is None:
                return None
            return max(0.0, self._clock() - self._last_busy)

    def depth(self) -> int:
        with self._cv:
            return len(self._waiting) + len(self._running)

    def snapshot_for(self, ticket: Ticket) -> Dict[str, Any]:
        with self._cv:
            return self._snapshot_locked(ticket)

    def _snapshot_locked(self, ticket: Ticket) -> Dict[str, Any]:
        order = self._ordered_locked()
        ahead_waiting = []
        for t in order:
            if t is ticket:
                break
            ahead_waiting.append(t)
        running = list(self._running.values())
        ahead = [self._ahead_entry(t, True) for t in running] + \
                [self._ahead_entry(t, False) for t in ahead_waiting]
        now = self._clock()
        return {
            "state": ticket.state,
            "seat": self.seat,
            "position": len(ahead),            # turns in front of this one
            "ahead": ahead,
            "running_kind": (running[0].cls if running else None),
            "waited_s": round(max(0.0, now - ticket.enqueued_at), 1) if ticket.enqueued_at else 0.0,
            "cloud_offered": bool(ticket.offered),
            "blocked": ticket.blocked or "",
        }

    @staticmethod
    def _ahead_entry(t: Ticket, running: bool) -> Dict[str, Any]:
        # Titles are the owner's own chats only. A turn from another caller (the
        # local API, a paired phone, a job) is named by kind, never by title.
        own = t.cls in ("voice", "chat")
        return {"kind": t.cls, "running": running,
                "title": (t.title if own and t.title else None),
                "label": (t.label if t.cls in ("background", "user_job") else None)}

    # -- admission --------------------------------------------------------
    def _can_run_locked(self, t: Ticket) -> bool:
        if len(self._running) >= self.capacity:
            return False
        # The first ticket the gate lets through: a held-back job (waiting for
        # a swap, an idle window, memory) does not stand in front of the rest.
        for cand in self._ordered_locked():
            if cand.id in self._blocked:
                continue
            return cand is t
        return False

    def _gate_reason(self, t: Ticket) -> str:
        g = self.gate
        if g is None or t.cls not in GATED_CLASSES:
            return ""
        try:
            return str(g(t) or "")
        except Exception:
            # A job the owner asked for is never held by a broken check; background
            # work is (fail closed, bounded by background_gate's drain cap).
            if t.cls != "background":
                _log.debug("turn_queue: gate failed", exc_info=True)
                return ""
            return _failed_check_words("turn_queue.gate")

    def _enqueue_locked(self, t: Ticket, *, head: bool = False) -> None:
        t.seq = next(self._seq)
        t.head = head
        t.state = "waiting"
        t.enqueued_at = self._clock()
        t.offered = False
        self._waiting.append(t)
        self._cv.notify_all()

    def _start_locked(self, t: Ticket) -> None:
        if t in self._waiting:
            self._waiting.remove(t)
        t.state = "running"
        t.admitted_at = self._clock()
        t.parked = False
        self._running[t.id] = t

    def wait_turn(self, t: Ticket, *, head: bool = False,
                  on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
                  should_cancel: Optional[Callable[[], bool]] = None,
                  status_every_s: Optional[float] = None,
                  offer: Optional[Callable[[Ticket, float, int], Any]] = None,
                  offer_after_s: Optional[float] = None,
                  max_wait_s: Optional[float] = None) -> str:
        """Block until ``t`` may run. Returns 'admitted', 'cancelled' or
        'cloud' (the owner said yes to the one cloud offer), or 'timeout':
        ``max_wait_s`` passed while the ticket waited only on other tickets
        (never while the gate held it back on purpose)."""
        # Resolved per call, not at definition, so a setting or a test that
        # changes the module default is honoured.
        if status_every_s is None:
            status_every_s = DEFAULT_STATUS_EVERY_S
        if offer_after_s is None:
            offer_after_s = DEFAULT_OFFER_AFTER_S
        with self._cv:
            self._enqueue_locked(t, head=head)
        last_emit = None
        while True:
            snap = None
            outcome = None
            held_back = self._gate_reason(t)        # outside the lock: it reads other queues
            with self._cv:
                if held_back:
                    self._blocked[t.id] = held_back
                else:
                    self._blocked.pop(t.id, None)
                t.blocked = held_back
                if t.state == "cancelled" or (should_cancel is not None and _safe(should_cancel)):
                    if t in self._waiting:
                        self._waiting.remove(t)
                    t.state = "cancelled"
                    outcome = "cancelled"
                elif t.cloud_granted:
                    if t in self._waiting:
                        self._waiting.remove(t)
                    t.state = "done"
                    outcome = "cloud"
                elif not held_back and self._can_run_locked(t):
                    self._start_locked(t)
                    outcome = "admitted"
                elif (max_wait_s is not None and not held_back
                      and self._clock() - t.enqueued_at >= max_wait_s):
                    if t in self._waiting:
                        self._waiting.remove(t)
                    t.state = "done"
                    outcome = "timeout"
                else:
                    now = self._clock()
                    if last_emit is None or now - last_emit >= status_every_s:
                        last_emit = now
                        snap = self._snapshot_locked(t)
                    waited = now - t.enqueued_at
                    do_offer = (offer is not None and not t.offered and waited >= offer_after_s)
                    if do_offer:
                        t.offered = True       # at most once per wait, set BEFORE the call
                        position = len(self._snapshot_locked(t)["ahead"])
            if outcome is not None:
                with self._cv:
                    self._blocked.pop(t.id, None)
                    t.blocked = ""
                    self._cv.notify_all()       # the queue behind us re-reads its place
                if outcome != "admitted":
                    self._maybe_idle()
                return outcome
            if snap is not None and on_status is not None:
                _safe(on_status, snap)
            if do_offer:
                _safe(offer, t, waited, position)
            with self._cv:
                if t.state == "waiting":
                    self._cv.wait(_WAIT_POLL_S)

    def release(self, t: Ticket) -> None:
        """Give the slot back. Idempotent."""
        with self._cv:
            if t in self._waiting:
                self._waiting.remove(t)
            self._blocked.pop(t.id, None)
            was_running = self._running.pop(t.id, None) is not None
            if t.state in ("running", "waiting", "new"):
                t.state = "done"
            t.tool_since = None
            self._cv.notify_all()
        if was_running:
            self._last_busy = self._clock()
            self._maybe_idle()

    def park(self, t: Ticket) -> bool:
        """Let the seat go while the turn sits in a slow tool. Returns True
        when it actually gave the slot up (somebody is waiting and it was
        running)."""
        with self._cv:
            if t.id not in self._running or not self._waiting:
                return False
            self._running.pop(t.id, None)
            t.state = "new"
            t.parked = True
            self._cv.notify_all()
            return True

    def cancel(self, turn_id: str) -> bool:
        """Remove a WAITING turn. A running turn is stopped the usual way."""
        with self._cv:
            for t in self._waiting:
                if t.id == str(turn_id):
                    t.state = "cancelled"
                    self._cv.notify_all()
                    return True
        return False

    def grant_cloud(self, turn_id: str) -> bool:
        """The owner said yes to the cloud offer for this waiting turn."""
        with self._cv:
            for t in self._waiting:
                if t.id == str(turn_id):
                    t.cloud_granted = True
                    self._cv.notify_all()
                    return True
        return False

    def waiting_tickets(self) -> List[Ticket]:
        with self._cv:
            return self._ordered_locked()

    def running_tickets(self) -> List[Ticket]:
        with self._cv:
            return list(self._running.values())

    # -- background bridge ------------------------------------------------
    def background_started(self, task_id: str, label: Optional[str] = None,
                           model: Optional[str] = None, cls: str = "background") -> Ticket:
        # ``cls`` is ``user_job`` for a foreground job (a workspace button or a
        # tool inside the owner's own turn: services/visible_jobs), which holds
        # its seat as work a person asked for, not as background work.
        t = Ticket(task_id, cls if cls in GATED_CLASSES else "background", self.seat,
                   label=label, model=model, kind="task")
        with self._cv:
            t.seq = next(self._seq)
            t.state = "running"
            t.enqueued_at = t.admitted_at = self._clock()
            self._running[t.id] = t
        return t

    def background_finished(self, task_id: str) -> None:
        with self._cv:
            had = self._running.pop(str(task_id), None) is not None
            self._cv.notify_all()
        if had:
            self._last_busy = self._clock()
            self._maybe_idle()

    def on_idle(self, hook: Callable[[str], None]) -> None:
        if hook not in self._idle_hooks:
            self._idle_hooks.append(hook)

    def _maybe_idle(self) -> None:
        """No person is running or waiting: background work may start."""
        if self.people_active():
            return
        for hook in list(self._idle_hooks):
            _safe(hook, self.seat)


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        _log.debug("turn_queue: hook failed", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  The registry, and the call site API
# ─────────────────────────────────────────────────────────────────────────────
_QUEUES: Dict[str, TurnQueue] = {}
_QUEUES_LOCK = threading.RLock()       # re-entrant: a module imported under it may register a gate
#: turn id -> (queue, ticket) for every turn waiting or running here.
_TICKETS: Dict[str, Ticket] = {}
_TICKETS_LOCK = threading.Lock()


def queue_for(seat: Any, capacity: Optional[int] = None) -> TurnQueue:
    key = norm_seat(seat)
    # Read outside the lock: it may import the settings module, which imports
    # this package's other services.
    slots = capacity if capacity is not None else (
        seat_capacity(key) if key not in _QUEUES else 1)
    with _QUEUES_LOCK:
        q = _QUEUES.get(key)
        if q is None:
            q = TurnQueue(key, slots)
            _QUEUES[key] = q
            for hook in _GLOBAL_IDLE_HOOKS:
                q.on_idle(hook)
        elif capacity is not None:
            q.capacity = max(1, int(capacity))
        if capacity is not None:
            q.pinned = True
        if _GLOBAL_GATE[0] is not None:
            q.gate = _GLOBAL_GATE[0]
        return q


_GLOBAL_IDLE_HOOKS: List[Callable[[str], None]] = []
#: The scheduler's gate, given to every queue (existing and future).
_GLOBAL_GATE: List[Optional[Callable[[Ticket], str]]] = [None]


def set_gate(gate: Optional[Callable[[Ticket], str]]) -> None:
    """Install (or clear) the gate every seat queue consults for ``user_job``
    and ``background`` tickets. services/local_scheduler installs its own."""
    _GLOBAL_GATE[0] = gate
    with _QUEUES_LOCK:
        for q in _QUEUES.values():
            q.gate = gate


def queues() -> List[TurnQueue]:
    with _QUEUES_LOCK:
        return list(_QUEUES.values())


# -- the owner is in a meeting: background work gives way ----------------------
#: holder -> why. While any holder is here no ``background`` ticket is admitted
#: (the scheduler's gate reads this), and a background job that is already
#: running is asked to wait at its next checkpoint (``background_checkpoint``).
_BG_PAUSE: Dict[str, str] = {}
_BG_PAUSE_LOCK = threading.Lock()
#: A paused job waits at most this long at one checkpoint, so a pause that is
#: never cleared cannot strand work for good.
BACKGROUND_CHECKPOINT_MAX_S = 900.0


def request_background_pause(holder: str, reason: str = "") -> None:
    """Ask background work to give way until ``clear_background_pause(holder)``."""
    with _BG_PAUSE_LOCK:
        _BG_PAUSE[str(holder)] = str(reason or "you are busy")


def clear_background_pause(holder: str) -> None:
    with _BG_PAUSE_LOCK:
        _BG_PAUSE.pop(str(holder), None)


def background_pause_reason() -> str:
    """Why background work must wait, in words; "" when it need not."""
    with _BG_PAUSE_LOCK:
        return next(iter(_BG_PAUSE.values()), "")


def background_checkpoint(max_wait_s: Optional[float] = None,
                          sleep: Callable[[float], Any] = time.sleep) -> bool:
    """A checkpoint for a running background job: if a pause was asked for,
    wait here until it is cleared (or ``max_wait_s``). Does nothing on a thread
    that is not doing background work. True when it had to wait."""
    if inherited_class() != "background" or not background_pause_reason():
        return False
    limit = BACKGROUND_CHECKPOINT_MAX_S if max_wait_s is None else float(max_wait_s)
    t0 = time.monotonic()
    import agent_friday.core as _core_gs
    while (background_pause_reason() and time.monotonic() - t0 < limit
           and not _core_gs.GLOBAL_STOP.is_set()):
        sleep(0.25)
    # Stop everything (services/global_stop) ends a background job at once; it never
    # times out into running again.
    if _core_gs.GLOBAL_STOP.is_set():
        from agent_friday.services.background_gate import JobCancelled
        raise JobCancelled("Everything is stopped; this background job was stopped.")
    return True


def people_active_anywhere() -> bool:
    """A person's turn (voice, chat, Local API, a job they asked for) runs or
    waits on any local seat."""
    return any(q.people_active() for q in queues())


def all_waiting() -> List[Ticket]:
    """Every waiting ticket on every seat, highest class first."""
    out: List[Ticket] = []
    for q in queues():
        out.extend(q.waiting_tickets())
    out.sort(key=lambda t: (_PRIORITY[t.cls], t.enqueued_at))
    return out


def all_running() -> List[Ticket]:
    out: List[Ticket] = []
    for q in queues():
        out.extend(q.running_tickets())
    return out


def reset_for_tests() -> None:
    """Forget every queue, ticket and hook. Tests only."""
    with _QUEUES_LOCK:
        _QUEUES.clear()
        del _GLOBAL_IDLE_HOOKS[:]
    with _TICKETS_LOCK:
        _TICKETS.clear()
    with _BG_PAUSE_LOCK:
        _BG_PAUSE.clear()
    _NP_MEMO.clear()


class Admission:
    """What ``acquire`` hands back. ``outcome`` is 'admitted', 'bypass' (a
    cloud seat: never queued), 'cancelled' or 'cloud'."""

    def __init__(self, outcome: str, ticket: Optional[Ticket] = None,
                 queue: Optional[TurnQueue] = None, waited_s: float = 0.0,
                 params: Optional[Dict[str, Any]] = None):
        self.outcome = outcome
        self.ticket = ticket
        self.queue = queue
        self.waited_s = waited_s
        self._params = params or {}
        self._token = None
        self._held_token = None

    @property
    def held(self) -> bool:
        return self.outcome == "admitted" and self.ticket is not None

    def release(self) -> None:
        t, q = self.ticket, self.queue
        if self._token is not None:
            try:
                CURRENT.reset(self._token)
            except Exception:
                pass
            self._token = None
        if self._held_token is not None:
            try:
                HELD.reset(self._held_token)
            except Exception:
                pass
            self._held_token = None
        if t is None or q is None:
            return
        if t.cls in ("voice", "chat"):
            _note_activity()            # the idle clock counts from the turn's end
        with _TICKETS_LOCK:
            if _TICKETS.get(t.id) is t:
                _TICKETS.pop(t.id, None)
        q.release(t)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


#: The admission of the turn running on this thread, so the tool executor can
#: tell the queue when a tool starts and ends without any plumbing.
CURRENT: contextvars.ContextVar = contextvars.ContextVar("turn_queue_current", default=None)

#: ``((seat, class, ticket id), ...)`` for every admission this context is inside. Work
#: that runs inside an admitted turn on the same seat does not admit again (it
#: would wait for the slot its own caller holds); on another seat it admits at
#: the class of the turn it serves.
HELD: contextvars.ContextVar = contextvars.ContextVar("turn_queue_held", default=())


def held_class(seat: Any) -> Optional[str]:
    """The class this context already holds ``seat`` at, else None."""
    key = norm_seat(seat)
    for s, c, _tid in HELD.get():
        if s == key:
            return c
    return None


def inherited_class() -> Optional[str]:
    """The highest class any enclosing admission holds (the work serves it)."""
    held = HELD.get()
    if not held:
        return None
    return min((c for _s, c, _tid in held), key=class_priority)


def own_ticket_ids() -> List[str]:
    """The ids of the tickets this context holds (a lease request made from
    inside a turn must not wait for the turn that is making it)."""
    return [tid for _s, _c, tid in HELD.get() if tid]


class holding:
    """Mark a thread as already holding ``seat`` at ``cls``, for work whose slot
    was taken elsewhere (a background task started by the seat queue holds its
    slot from ``background_started``; its worker thread runs under this)."""

    def __init__(self, seat: Any, cls: str = "background"):
        self._entry = (norm_seat(seat), cls if cls in _PRIORITY else "chat", None)
        self._token = None

    def __enter__(self):
        self._token = HELD.set(HELD.get() + (self._entry,))
        return self

    def __exit__(self, *exc):
        try:
            HELD.reset(self._token)
        except Exception:
            pass
        return False


def _note_activity() -> None:
    """A person is here: the idle clock (services/work_queue) restarts."""
    try:
        from agent_friday.services import work_queue as _wq
        _wq.mark_active()
    except Exception:
        pass


def acquire(seat: Any, cls: str, turn_id: str, *, title: Optional[str] = None,
            conversation_id: Optional[str] = None, local: bool = True,
            capacity: Optional[int] = None, label: Optional[str] = None,
            model: Optional[str] = None, kind: Optional[str] = None,
            needs: Optional[str] = None, deferrable: bool = True,
            max_wait_s: Optional[float] = None,
            on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
            should_cancel: Optional[Callable[[], bool]] = None,
            status_every_s: Optional[float] = None,
            offer: Optional[Callable[[Ticket, float, int], Any]] = None,
            offer_after_s: Optional[float] = None,
            tool_release_after_s: Optional[float] = None) -> Admission:
    """Wait for the seat, then hold it until ``Admission.release()``.

    A cloud seat (``local=False``) is never queued. A cancelled wait returns
    ``outcome == 'cancelled'`` and holds nothing, so the caller must not go on
    to the model. ``outcome == 'timeout'`` (only with ``max_wait_s``) holds
    nothing either: the caller decides whether to go on without a slot.
    """
    if not local:
        return Admission("bypass")
    q = queue_for(seat, capacity)
    if capacity is None and not q.pinned:
        q.capacity = seat_capacity(q.seat)       # the seat's real -np, as it is now
    if cls in ("voice", "chat", "user_job"):
        _note_activity()
    t = Ticket(turn_id, cls, q.seat, title=title, conversation_id=conversation_id,
               label=label, model=model, kind=kind, needs=needs, deferrable=deferrable)
    with _TICKETS_LOCK:
        _TICKETS[t.id] = t
    params = {"on_status": on_status, "should_cancel": should_cancel,
              "status_every_s": status_every_s, "offer": offer,
              "offer_after_s": offer_after_s,
              "tool_release_after_s": (tool_release_s() if tool_release_after_s is None
                                       else float(tool_release_after_s))}
    t0 = time.monotonic()
    outcome = q.wait_turn(t, on_status=on_status, should_cancel=should_cancel,
                          status_every_s=status_every_s, offer=offer,
                          offer_after_s=offer_after_s, max_wait_s=max_wait_s)
    adm = Admission(outcome, t, q, waited_s=time.monotonic() - t0, params=params)
    if outcome != "admitted":
        with _TICKETS_LOCK:
            _TICKETS.pop(t.id, None)
        return adm
    adm._token = CURRENT.set(adm)
    adm._held_token = HELD.set(HELD.get() + ((q.seat, t.cls, t.id),))
    return adm


def cancel(turn_id: str) -> bool:
    """Take a waiting turn out of whatever queue it is in."""
    with _TICKETS_LOCK:
        t = _TICKETS.get(str(turn_id))
    if t is None:
        return False
    with _QUEUES_LOCK:
        q = _QUEUES.get(norm_seat(t.seat))
    return bool(q and q.cancel(t.id))


def grant_cloud(turn_id: str) -> bool:
    """The owner approved the cloud offer for a waiting turn."""
    with _TICKETS_LOCK:
        t = _TICKETS.get(str(turn_id))
    if t is None:
        return False
    with _QUEUES_LOCK:
        q = _QUEUES.get(norm_seat(t.seat))
    return bool(q and q.grant_cloud(t.id))


def status_for(turn_id: str) -> Optional[Dict[str, Any]]:
    """Where a turn stands, or None when it is not in a queue."""
    with _TICKETS_LOCK:
        t = _TICKETS.get(str(turn_id))
    if t is None:
        return None
    with _QUEUES_LOCK:
        q = _QUEUES.get(norm_seat(t.seat))
    return q.snapshot_for(t) if q else None


# -- a slow tool lets the seat go ---------------------------------------------
def tool_begin() -> Optional[Callable[[], None]]:
    """Called by the tool executor when a tool starts. Returns the ``end``
    callback, or None when this thread holds no queue slot.

    After ``tool_release_s`` of the SAME tool, if somebody is waiting, the turn
    lets the seat go; ``end`` then waits for the seat again, at the head of its
    class, before the next model call.
    """
    adm = CURRENT.get()
    if adm is None or not adm.held:
        background_checkpoint()         # a background job's tool boundary is its checkpoint
        return None
    t, q = adm.ticket, adm.queue
    after = float(adm._params.get("tool_release_after_s") or DEFAULT_TOOL_RELEASE_S)
    state = {"done": False}
    lock = threading.Lock()

    def _fire():
        with lock:
            if state["done"]:
                return
            q.park(t)

    timer = threading.Timer(after, _fire)
    timer.daemon = True
    timer.start()

    def _end():
        with lock:
            state["done"] = True
        timer.cancel()
        if not t.parked:
            return
        p = adm._params
        outcome = q.wait_turn(
            t, head=True, on_status=p.get("on_status"),
            should_cancel=p.get("should_cancel"),
            status_every_s=p.get("status_every_s"),
            offer=None)
        if outcome != "admitted":
            # Cancelled while it waited to come back: nothing more may run.
            adm.outcome = "cancelled"

    return _end


def guard_tool(fn):
    """Decorator for the one tool executor: a tool running longer than
    ``tool_release_s`` frees the seat for a waiting person. A no-op on any
    thread that holds no slot (background tasks, cloud turns, tests)."""
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        end = tool_begin()
        try:
            return fn(*args, **kwargs)
        finally:
            if end is not None:
                try:
                    end()
                except Exception:
                    _log.debug("turn_queue: tool end failed", exc_info=True)
    _wrapped.__turn_queue_guarded__ = True
    return _wrapped


# ─────────────────────────────────────────────────────────────────────────────
#  The cloud offer (one per wait, never automatic)
# ─────────────────────────────────────────────────────────────────────────────
def cloud_offer_hook(ticket: Ticket, waited_s: float, position: int):
    """The ``offer`` hook for a chat turn: raises cloud_spill's one card. It
    changes nothing by itself; the owner's yes arrives as ``grant_cloud``."""
    try:
        from agent_friday.services import cloud_spill as _cs
        record = {"id": ticket.id, "turn_id": ticket.id,
                  "name": ticket.title or "your message",
                  "model": ticket.seat, "seat": ticket.seat,
                  "seat_is_local": True, "status": "queued-for-seat"}
        return _cs.offer(record, wait_s=waited_s, queue_position=position + 1)
    except Exception:
        _log.debug("turn_queue: cloud offer failed", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  The background bridge (seat_queue.SeatQueue is the `background` class)
# ─────────────────────────────────────────────────────────────────────────────
def _failed_check_words(where: str) -> str:
    """Why background work waits when the scheduler's gate raised (call from an
    except block): "waiting for you to be idle" until background_gate's drain cap
    has passed, then "". Interactive turns never reach a gate."""
    try:
        from agent_friday.services import background_gate as _bg
        return _bg.failed_check_reason(where, "waiting for you to be idle")
    except Exception:
        return "waiting for you to be idle"


def attach_seat_queue(seat_queue, on_idle: Optional[Callable[[str], None]] = None) -> None:
    """Make ``seat_queue`` (a ``SeatQueue``) the background class.

    * its tasks hold a ``background`` slot here while they run;
    * it may not start a task on a seat where a person is running or waiting;
    * ``on_idle(seat)`` is called when that stops being true, so the waiting
      task is started at once rather than at the next supervisor pass.
    """
    def _probe(seat, rec):
        # Only a deferrable job waits for an idle window, and the queue applies
        # that itself, in its own words (background_gate.block_reason): the
        # probe never asks the gate for idle, so a job waits once and says one
        # thing. Everything else the gate decides (a person's turn, the jobs
        # already running, a model swap, the memory floor) still applies.
        kind = ("task" if isinstance(rec, dict) and rec.get("deferrable") and not rec.get("run_now")
                else "time_bound")
        return Ticket(PROBE_ID, "background", norm_seat(seat),
                      model=_seat_model(seat), kind=kind, deferrable=False)

    def _foreground(rec) -> bool:
        # Work a person is waiting on (services/visible_jobs): it queues for
        # the seat but never waits for the interactive turn that may itself be
        # waiting on it, and never for an idle window or a model swap.
        return isinstance(rec, dict) and bool(rec.get("foreground"))

    def gate(seat, rec=None) -> bool:
        if _foreground(rec):
            return True
        if queue_for(seat).people_active() or background_pause_reason():
            return False
        # A task whose model is not loaded is a swap: it waits for the same
        # things any other background job does (an idle window, the memory
        # floor, the model that is loaded finishing its jobs first).
        g = _GLOBAL_GATE[0]
        if g is None:
            return True
        try:
            return not g(_probe(seat, rec))
        except Exception:
            return not _failed_check_words("turn_queue.seat_gate")

    def started(seat, task_id, rec=None):
        rec = rec if isinstance(rec, dict) else {}
        queue_for(seat).background_started(
            task_id, label=rec.get("label") or rec.get("name"),
            model=rec.get("model") or _seat_model(seat),
            cls="user_job" if _foreground(rec) else "background")

    def finished(seat, task_id):
        with _QUEUES_LOCK:
            q = _QUEUES.get(norm_seat(seat))
        if q is not None:
            q.background_finished(task_id)

    def why(seat, rec=None) -> str:
        if _foreground(rec):
            return ""
        if queue_for(seat).people_active():
            return "waits for your chat to finish"
        if background_pause_reason():
            return "waits for your meeting to finish"
        g = _GLOBAL_GATE[0]
        if g is None:
            return ""
        try:
            return str(g(_probe(seat, rec)) or "")
        except Exception:
            return _failed_check_words("turn_queue.seat_why")

    # `gate` on the SeatQueue is the Local AI queue's own rule for one job
    # (services/background_gate.block_reason); this bridge sets the seat-level
    # one beside it, so the two never replace each other.
    seat_queue.seat_gate = gate
    seat_queue.seat_why = why
    seat_queue.on_run = started
    seat_queue.on_done = finished
    if on_idle is not None:
        with _QUEUES_LOCK:
            _GLOBAL_IDLE_HOOKS.append(on_idle)
            for q in _QUEUES.values():
                q.on_idle(on_idle)


# ─────────────────────────────────────────────────────────────────────────────
#  Words (for voice and the API; the chat line is composed in the page)
# ─────────────────────────────────────────────────────────────────────────────
_NUM_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _count_word(n: int) -> str:
    return _NUM_WORDS[n] if 0 <= n < len(_NUM_WORDS) else str(n)


def describe(snap: Optional[Dict[str, Any]]) -> str:
    """One plain sentence for a snapshot (the voice tool and the API)."""
    if not snap or snap.get("state") != "waiting":
        return "Nothing of yours is waiting."
    n = int(snap.get("position") or 0)
    running = snap.get("running_kind")
    if n <= 1:
        what = ("a background job" if running == "background" else "another chat")
        text = "The local brain is busy with %s. You are next." % what
    else:
        text = "%s chats are ahead of you." % _count_word(n).capitalize()
    titles = [a.get("title") for a in (snap.get("ahead") or []) if a.get("title")]
    if titles:
        text += " Waiting for: " + titles[0] + "."
    return text


def waiting_turns() -> List[Dict[str, Any]]:
    """Every turn that is waiting right now, with its place (for the voice
    tools and the API). Titles only for the owner's own chats."""
    with _TICKETS_LOCK:
        tickets = list(_TICKETS.values())
    out = []
    for t in tickets:
        if t.state != "waiting":
            continue
        snap = status_for(t.id)
        if snap:
            out.append({"turn_id": t.id, "cls": t.cls, "title": t.title,
                        "conversation_id": t.conversation_id, **snap})
    return out


def overview() -> List[Dict[str, Any]]:
    """Every local seat that has a running or waiting turn: who holds it and
    who is waiting, in order. Titles are the owner's own chats only."""
    with _QUEUES_LOCK:
        queues = list(_QUEUES.values())
    out = []
    for q in queues:
        running = q.running_tickets()
        waiting = q.waiting_tickets()
        if not running and not waiting:
            continue
        out.append({
            "seat": q.seat, "capacity": q.capacity,
            "running": [{"kind": t.cls, "title": t.title if t.cls in ("voice", "chat") else None,
                         "label": t.label} for t in running],
            "waiting": [{"turn_id": t.id, "kind": t.cls,
                         "title": t.title if t.cls in ("voice", "chat") else None,
                         "label": t.label if t.cls in ("background", "user_job") else None,
                         "blocked": t.blocked or "",
                         "conversation_id": t.conversation_id} for t in waiting],
        })
    return out


def cancel_waiting_turns(title: str = "") -> Dict[str, Any]:
    """Take the owner's WAITING chat or voice turns out of the queue (all of
    them, or the one whose title contains ``title``). A running turn and a
    background job are never touched. Returns ``{"cancelled": [titles],
    "waiting": [titles]}``; ``waiting`` is what is left to choose from when
    ``title`` was needed and not given."""
    want = str(title or "").strip().lower()
    mine = [w for item in overview() for w in item["waiting"]
            if w["kind"] in ("voice", "chat")]
    if want:
        mine = [w for w in mine if want in str(w.get("title") or "").lower()]
    elif len(mine) > 1:
        return {"cancelled": [], "waiting": [w.get("title") or "an untitled chat" for w in mine]}
    done = []
    for w in mine:
        if cancel(w["turn_id"]):
            done.append(w.get("title") or "an untitled chat")
    return {"cancelled": done, "waiting": []}
