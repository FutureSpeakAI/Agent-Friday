"""The local work scheduler: one place that decides what the local models do next.

``services/turn_queue`` is the admission queue for one local seat. This module
is the rest of the scheduler, built on it:

* **Every local transport admits here.** ``admit()`` (or the ``admitted``
  decorator on a transport) is how a call to a local model asks for its turn,
  at one of five classes: ``voice`` > ``chat`` > ``api`` > ``user_job`` >
  ``background``. Work that runs inside an admitted turn on the same seat does
  not admit again (``turn_queue.HELD``). A static test walks ``src/`` and
  names any local inference call that does not pass admission.
* **Batch by model.** A model that is loaded drains its waiting jobs before
  anything asks for a swap. A background job never forces a swap while a
  person's turn runs or waits, and the swap it finally asks for takes every
  job for that one model together (``Planner``; the gate on the queues).
* **Idle windows from real activity.** Background work that can wait waits
  until the owner has been away for ``background_idle_minutes`` (default 10;
  the 1.0.2 rule, ``services/background_gate``), whether or not its model is
  loaded. The clock is the minimum of Windows' last-input time and Friday's own
  (``services/work_queue``, fed by chat sends, voice turns, admissions and the
  page's input events). A *scheduled* job, or any stretch of work marked
  time-bound (``work(..., deferrable=False)``), never waits for idle. A
  background call that no job claims is wrapped as a deferrable
  ``services/visible_jobs`` job of the Local AI queue (``WRAP_BACKGROUND``), so
  it has a row in the chip and a card that names the model.
* **One background job at a time.** A background call waits while another
  background job holds a seat (the 1.0.2 rule). Waiting background work is the
  Local AI queue (``services/seat_supervisor`` over ``seat_queue``, with the
  workspace jobs of ``services/visible_jobs`` as its foreground rows): this
  module reads it, with a podcast waiting for its turn and the leases, as the
  one list that the top-bar chip, the tray, ``/api/local-queue`` and the voice
  tool all show (``queue_snapshot``).
* **A local voice turn blocks background work; an open call does not.** The
  gate reads both the turn queue's tickets and ``background_gate``'s own
  registrations (the chat turn registry, the voice turn), so a turn that never
  admitted here still wins. An open call only keeps deferrable work waiting for
  idle.
* **A memory floor.** A job that would load a model does not start while free
  RAM is under ``local_scheduler_ram_floor_gb``; it waits and says so.
* **Leases queue.** ``lease_grant`` is what ``Arbiter.grant`` runs: a second
  lease request waits its turn (up to ``local_scheduler_lease_wait_s``) instead
  of being refused, and a lease evicts the seats only when no work of a higher
  class runs or waits.
* **Honest status, cancel, metrics.** ``status()`` and ``jobs()`` say what is
  waiting and why; ``cancel()`` takes a waiting job out; ``metrics_summary()``
  reads the local jsonl the scheduler appends to (swaps, free RAM, waits,
  background throughput). Nothing leaves the machine.

Importable without side effects beyond installing the queue gate.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import itertools
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from agent_friday.services import turn_queue as _tq

_log = logging.getLogger(__name__)

CLASSES = _tq.CLASSES
_PRIORITY = {c: i for i, c in enumerate(CLASSES)}
INTERACTIVE = ("voice", "chat", "api")          # the owner is waiting for an answer
JOB_CLASSES = ("user_job", "background")        # work that can be held back

DEFAULT_IDLE_S = 120.0
DEFAULT_RAM_FLOOR_GB = 4.0
DEFAULT_LEASE_WAIT_S = 120.0
DEFAULT_ADMIT_TIMEOUT_S = 900.0
#: How long a model we last ran counts as loaded (the Ollama daemon's 5 m keep-alive).
DEFAULT_RESIDENT_S = 300.0
VOICE_LEASE_WAIT_S = 20.0

#: Background tickets of these kinds never wait for the idle window: a
#: schedule runs at its time, and the queue (services/seat_supervisor) applies
#: idle itself to the jobs marked deferrable.
TIME_BOUND_KINDS = ("scheduled", "time_bound")

#: What each Arbiter lease kind is, for ordering. A call is the owner talking;
#: a job they asked for goes before one nobody is waiting for.
KIND_CLASS = {"voice_call": "voice", "heavy_turn": "user_job", "image_job": "user_job",
              "podcast_voice": "background", "bench_job": "background"}
#: Lease kinds that load a model (the rest only stand the pinned seats down).
LOADING_KINDS = ("heavy_turn", "image_job")
#: The admission kind and seat of a recording meeting (see ``MeetingHold``): a
#: job the owner asked for is not held back by their own recording.
MEETING_KIND = "meeting_capture"
MEETING_SEAT = "meeting-capture"


class LocalWorkCancelled(BaseException):
    """The owner took this queued job out of the line. A BaseException on
    purpose: the router's fallback ladders catch ``Exception`` and would send
    a cancelled local job to the cloud."""


# ─────────────────────────────────────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────────────────────────────────────
def _setting(key: str, default: Any) -> Any:
    try:
        from agent_friday.core import _load_settings
        v = (_load_settings() or {}).get(key, default)
        return default if v is None else v
    except Exception:
        return default


def _float_setting(key: str, default: float) -> float:
    try:
        v = float(_setting(key, default))
        return v if v >= 0 else default
    except (TypeError, ValueError):
        return default


def idle_window_s() -> float:
    """How long the owner must have been away before background work asks a
    model to load. The 1.0.2 owner setting wins: ``background_idle_minutes``
    (Settings > Advanced). ``local_scheduler_idle_s`` is kept only so an older
    settings file still loads; nothing reads it."""
    from agent_friday.services import background_gate as _bg
    return float(_bg.idle_threshold_s())


def ram_floor_gb() -> float:
    return _float_setting("local_scheduler_ram_floor_gb", DEFAULT_RAM_FLOOR_GB)


def lease_wait_s() -> float:
    return _float_setting("local_scheduler_lease_wait_s", DEFAULT_LEASE_WAIT_S)


def admit_timeout_s() -> float:
    return _float_setting("local_scheduler_admit_timeout_s", DEFAULT_ADMIT_TIMEOUT_S)


def resident_s() -> float:
    return _float_setting("local_scheduler_resident_s", DEFAULT_RESIDENT_S)


def enabled() -> bool:
    """``turn_queue_enabled`` off turns the whole queue off, as it always has."""
    return bool(_setting("turn_queue_enabled", True))


# ─────────────────────────────────────────────────────────────────────────────
#  Probes (the machine, injectable for tests)
# ─────────────────────────────────────────────────────────────────────────────
_RAM_PROBE: List[Optional[Callable[[], Optional[float]]]] = [None]
_RESIDENCY_PROBE: List[Optional[Callable[[str], bool]]] = [None]
_CLOCK: List[Callable[[], float]] = [time.time]


def set_ram_probe(fn: Optional[Callable[[], Optional[float]]]) -> None:
    _RAM_PROBE[0] = fn


def set_residency_probe(fn: Optional[Callable[[str], bool]]) -> None:
    """``fn(model) -> bool``: is this model loaded right now. Tests only; the
    default asks the Arbiter and remembers what the scheduler itself ran."""
    _RESIDENCY_PROBE[0] = fn


def free_ram_gb() -> Optional[float]:
    """Free physical RAM in GB, or None when it cannot be read (never a block)."""
    probe = _RAM_PROBE[0]
    if probe is not None:
        try:
            return probe()
        except Exception:
            return None
    if os.environ.get("FRIDAY_TESTING") == "1":
        return None             # a test machine's RAM says nothing about the code under test
    try:
        import psutil
        return psutil.virtual_memory().available / float(1 << 30)
    except Exception:
        return None


_NEED_MEMO: Dict[str, Tuple[float, float]] = {}


def model_need_gb(model: Optional[str]) -> float:
    """What loading ``model`` takes, from its record in the model store; 0.0
    when nothing says (the floor itself still applies). Kept for a minute: the
    gate asks every fraction of a second."""
    if not model:
        return 0.0
    key = str(model)
    hit = _NEED_MEMO.get(key)
    if hit is not None and time.monotonic() - hit[0] < 60.0:
        return hit[1]
    try:
        from agent_friday.services import model_store as _ms
        rec = _ms.get(key) or {}
        need = max(0.0, float(rec.get("size_bytes") or 0) / float(1 << 30))
    except Exception:
        need = 0.0
    _NEED_MEMO[key] = (time.monotonic(), need)
    return need


def memory_block(need_gb: float = 0.0, *, floor_gb: Optional[float] = None) -> str:
    """Why a load must wait for memory, or "" when it may go ahead."""
    floor = ram_floor_gb() if floor_gb is None else float(floor_gb)
    free = free_ram_gb()
    if free is None or floor <= 0:
        return ""
    sample_ram(free)
    if free - max(0.0, float(need_gb)) < floor:
        if need_gb:
            return ("waiting for memory: %.1f GB free, the load needs about %.1f GB "
                    "and %.1f GB must stay free" % (free, need_gb, floor))
        return "waiting for memory: %.1f GB free, %.1f GB must stay free" % (free, floor)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
#  Activity: when the owner was last here
# ─────────────────────────────────────────────────────────────────────────────
_ACTIVITY: Dict[str, float] = {}
_ACTIVITY_LOCK = threading.Lock()


def mark_activity(source: str = "ui", when: Optional[float] = None) -> None:
    """The owner did something: a chat send, a voice turn, a click or key in the
    page. Restarts the idle clock every background gate reads."""
    t = float(when) if when is not None else time.time()
    with _ACTIVITY_LOCK:
        _ACTIVITY[str(source)] = t
    try:
        from agent_friday.services import work_queue as _wq
        _wq.mark_active(t)
    except Exception:
        pass


def last_activity() -> Dict[str, float]:
    with _ACTIVITY_LOCK:
        return dict(_ACTIVITY)


def _registered_interactive() -> List[str]:
    """What ``background_gate`` knows is interactive now (a chat turn in the
    core registry, a spoken turn), without an open voice call: a call means the
    owner is here but does not block anything, it only keeps deferrable work
    waiting for idle."""
    try:
        from agent_friday.services import background_gate as _bg
        return [k for k in _bg.interactive_kinds() if k != _bg.KIND_VOICE_CALL]
    except Exception:
        return []


def owner_idle_s() -> float:
    """Seconds since the owner was last here; 0 while a person's turn runs or waits."""
    if _tq.people_active_anywhere() or _registered_interactive():
        return 0.0
    try:
        # The combined signal of the Local AI queue: Windows' last-input time
        # and Friday's own activity, whichever is shorter. An unreadable OS
        # clock counts as "not idle" there (it drains after six quiet hours).
        from agent_friday.services import background_gate as _bg
        return float(_bg.idle_seconds())
    except Exception:
        # Fail closed: an unreadable clock means the owner may be here.
        try:
            from agent_friday.services import background_gate as _bg
            _bg.note_failed_check("local_scheduler.owner_idle_s")
        except Exception:
            pass
        return 0.0


def interactive_busy_reason() -> str:
    """Why idle work must not start now because a person's turn is in the queue."""
    if _tq.background_pause_reason():
        return "You are in a meeting"
    if _tq.people_active_anywhere() or _registered_interactive():
        return "Friday is answering you"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
#  Residency: which models are loaded
# ─────────────────────────────────────────────────────────────────────────────
_RECENT: Dict[str, float] = {}          # model key -> when the scheduler last ran it
_STICKY: Dict[str, Any] = {"model": None, "at": 0.0}
_OWNED_MEMO: Dict[str, Tuple[float, bool]] = {}      # the Arbiter's answer, kept for a second
_STATE_LOCK = threading.Lock()


def _key(model: Any) -> str:
    return _tq.norm_seat(model)


def is_resident(model: Any) -> bool:
    """Is ``model`` loaded now, so a job on it costs no swap.

    The default seat (the pinned brain) is the Arbiter's to swap, never a job's.
    Any other model counts when an Arbiter-owned seat serves it, or when the
    scheduler ran it within ``resident_s`` (the daemon keeps it loaded that long).
    Where there is no Arbiter (a scratch server, a test) every model counts as
    loaded and the scheduler only orders the work."""
    probe = _RESIDENCY_PROBE[0]
    if probe is not None:
        try:
            return bool(probe(str(model or "")))
        except Exception:
            return True
    key = _key(model)
    if key == "default":
        return True
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        if get_arbiter() is None:
            return True         # no Arbiter in this process: swaps are invisible, so only the order is kept
    except Exception:
        return True
    with _STATE_LOCK:
        last = _RECENT.get(key)
        memo = _OWNED_MEMO.get(key)
    if last is not None and _CLOCK[0]() - last < resident_s():
        return True
    if memo is not None and time.monotonic() - memo[0] < 1.0:
        return memo[1]
    try:
        from agent_friday.services.residency_arbiter import owned_provider
        owned = owned_provider(str(model)) is not None
    except Exception:
        owned = False
    with _STATE_LOCK:
        _OWNED_MEMO[key] = (time.monotonic(), owned)
    return owned


def note_started(model: Any, cls: str) -> bool:
    """A job is starting on ``model``. Returns True when that is a swap (the
    model was not loaded). Remembers the model for the batching rules."""
    swap = not is_resident(model)
    key = _key(model)
    with _STATE_LOCK:
        if swap:
            # One swapped-in model at a time: loading this one stands the last one down.
            for other in [k for k in _RECENT if k != key]:
                del _RECENT[other]
            _STICKY["model"], _STICKY["at"] = key, _CLOCK[0]()
        _RECENT[key] = _CLOCK[0]()
    if swap:
        emit("swap", model=key, cls=cls)
    return swap


def note_finished(model: Any) -> None:
    with _STATE_LOCK:
        _RECENT[_key(model)] = _CLOCK[0]()


# ─────────────────────────────────────────────────────────────────────────────
#  The batching plan (pure) and the gate that applies it
# ─────────────────────────────────────────────────────────────────────────────
class Job:
    """What the planner needs to know about one queued job."""
    __slots__ = ("id", "cls", "model", "enq")

    def __init__(self, id, cls, model, enq):
        self.id, self.cls, self.model, self.enq = id, cls, _key(model), enq


class Planner:
    """Which job goes next, given what is loaded. Pure: the live gate and the
    scripted-day simulation both run this."""

    @staticmethod
    def target_model(waiting: Iterable[Job], resident: Callable[[str], bool],
                     sticky: Optional[str]) -> Optional[str]:
        """The one unloaded model whose jobs go next: among the best class that
        has an unloaded job, the model last swapped in while it still has jobs
        waiting, else the model with the most jobs waiting (the oldest job
        breaks a tie)."""
        pool = [j for j in waiting if j.cls in JOB_CLASSES and not resident(j.model)]
        if not pool:
            return None
        best = min(_PRIORITY[j.cls] for j in pool)
        counts: Dict[str, List[float]] = {}
        for j in pool:
            if _PRIORITY[j.cls] == best:
                counts.setdefault(j.model, []).append(j.enq)
        if sticky in counts:
            return sticky
        return sorted(counts, key=lambda m: (-len(counts[m]), min(counts[m])))[0]

    @staticmethod
    def choose(waiting: List[Job], resident: Callable[[str], bool],
               sticky: Optional[str] = None) -> Optional[Job]:
        """The next job to start, or None when none may. A person's turn goes
        first, in class order. Then, one class at a time (jobs you asked for,
        then background): jobs on a loaded model, oldest first, and only when
        none is left the unloaded model ``target_model`` picks, oldest first.
        An unloaded model's job never starts while a person is waiting."""
        if not waiting:
            return None
        people = [j for j in waiting if j.cls in INTERACTIVE]
        if people:
            return sorted(people, key=lambda j: (_PRIORITY[j.cls], j.enq))[0]
        for tier in JOB_CLASSES:
            jobs_ = sorted([j for j in waiting if j.cls == tier], key=lambda j: j.enq)
            for j in jobs_:
                if resident(j.model):
                    return j
            target = Planner.target_model(jobs_, resident, sticky)
            for j in jobs_:
                if j.model == target:
                    return j
        return None


def _jobs_from(tickets: Iterable[_tq.Ticket]) -> List[Job]:
    out = []
    for t in tickets:
        if t.state != "waiting":
            continue
        out.append(Job(t.id, t.cls, t.model or _tq._seat_model(t.seat), t.enqueued_at))
    return out


_LAST_RAM_SAMPLE = [0.0]


def sample_ram(free: Optional[float] = None) -> None:
    """Record free RAM for the metrics (at most one sample a 20 s)."""
    now = time.time()
    if now - _LAST_RAM_SAMPLE[0] < 20.0:
        return
    _LAST_RAM_SAMPLE[0] = now
    if free is None:
        free = free_ram_gb()
    if free is not None:
        emit("ram", free_gb=round(float(free), 2))


def _model_label(model: Any) -> str:
    key = _key(model)
    return "the local brain" if key == "default" else "the %s model" % key


def gate(t: _tq.Ticket) -> str:
    """Why this ``user_job`` / ``background`` ticket may not start yet, in
    words; "" when it may. Installed on every seat queue."""
    why = _gate_core(t)
    if why:
        return why
    # One background job at a time (1.0.2), per lane: another background job
    # holding a seat in the same lane goes first. (Everything above is about
    # WHEN and on WHICH model; this is the owner's cap on how much runs at
    # once.) CPU work (seat "cpu/...", the Library reading folders) and model
    # work are separate lanes: neither uses the other's hardware, so a folder
    # being read never holds a model job back, and a model job never holds a
    # folder back. The memory floor still guards both.
    if t.cls == "background":
        # (The seat queue asking "could a task start here" is already one job to
        # a seat, so a job on its own seat, which may be about to yield it, is
        # not a reason to wait.)
        probe = t.id == _tq.PROBE_ID
        lane = _lane(t.seat)
        try:
            if any(r.cls == "background" and r.id != t.id and not (probe and r.seat == t.seat)
                   and _lane(r.seat) == lane for r in _tq.all_running()):
                return "waiting for the background job that is already running"
        except Exception:
            pass
    return ""


def _lane(seat: Any) -> str:
    """"cpu" for work that runs on the processor and loads no model, else "model"."""
    return "cpu" if str(seat or "").startswith("cpu/") else "model"


def _hardware_in_play() -> bool:
    """Is there a real arbiter, or a test's residency probe, behind the models.
    Where there is neither (a scratch server, a unit test of some other module)
    the scheduler only orders the work: nothing swaps, and the owner's absence
    is not something a test process can wait for."""
    if _RESIDENCY_PROBE[0] is not None:
        return True
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        return get_arbiter() is not None
    except Exception:
        return False


def idle_block() -> str:
    """Why work that waits for the owner to be away must wait, or "". The 1.0.2
    rule (``background_gate.idle_state``: the minimum of Windows' last-input
    time and Friday's own activity, ``background_idle_minutes``); an open voice
    call is not idle even when nobody touches a key."""
    if not _hardware_in_play():
        return ""
    try:
        from agent_friday.services import background_gate as _bg
        if _bg.KIND_VOICE_CALL in _bg.interactive_kinds():
            return "waiting for you to be idle"
        st = _bg.safe_idle_state()
        # An unreadable OS clock (or a check that failed) counts as not idle until
        # the gate's drain cap (``["idle"]``); otherwise the owner's window decides.
        idle = (float(st["idle_s"]) >= idle_window_s()) if st["os_readable"] else bool(st["idle"])
        if not idle:
            return "waiting for you to be idle"
    except Exception:
        # Fail closed: a broken check never lets background work in while the owner may be here.
        try:
            from agent_friday.services import background_gate as _bg
            return _bg.failed_check_reason("local_scheduler.idle_block", "waiting for you to be idle")
        except Exception:
            return "waiting for you to be idle"
    return ""


def _gate_core(t: _tq.Ticket) -> str:
    model = t.model or _tq._seat_model(t.seat)
    if t.cls == "background" and _tq.background_pause_reason():
        # A recording meeting: no background job is admitted, resident model or not.
        return "waiting for your meeting to finish"
    try:
        waiting = [w for w in _tq.all_waiting() if w.id != t.id]
        running = _tq.all_running()
    except Exception:
        return ""
    if not str(t.seat or "").startswith("cpu/"):
        # CPU work loads no model, so it never decides which model is loaded next.
        waiting = [w for w in waiting if not str(w.seat or "").startswith("cpu/")]
    interactive = ([w for w in waiting if w.cls in INTERACTIVE]
                   + [r for r in running if r.cls in INTERACTIVE])
    if t.cls == "user_job":
        # A job the owner asked for is not held back by their own recording.
        interactive = [x for x in interactive if x.kind != MEETING_KIND]
    # A person's turn that never admitted here (a chat turn in the core
    # registry, a spoken turn) counts the same: the local queue's own rule.
    people = bool(interactive) or (_hardware_in_play() and bool(_registered_interactive()))
    if t.cls == "background":
        # A person's turn first, on any seat; then the 1.0.2 idle window
        # (background_idle_minutes). A scheduled job is time-bound and never
        # waits for idle; the queue (seat_supervisor) applies idle itself to
        # jobs marked deferrable, so those do not wait for it twice.
        if people:
            return "waiting for your chat to finish"
        if t.kind not in TIME_BOUND_KINDS and t.deferrable:
            why = idle_block()
            if why:
                return why
    # CPU work (the Library reading a folder, seat "cpu/...") loads no model: there is no swap to
    # avoid and no model whose jobs to take together, so only a meeting or a person's turn, the idle
    # window and the memory floor decide when it goes.
    cpu_work = str(t.seat or "").startswith("cpu/")
    if is_resident(model) and not cpu_work:
        return ""                      # no swap: the seat's own queue orders it
    # An unloaded model: a swap. Everything below is about not forcing one at
    # a bad moment, and about taking each model's jobs together.
    if people:
        return "waiting for your chat to finish"
    if cpu_work:
        return memory_block(0.0)
    mine = Job(t.id, t.cls, model, t.enqueued_at)
    # Jobs of its own class or a higher one decide its place; a lower class never does.
    queued = [j for j in _jobs_from(waiting) if _PRIORITY[j.cls] <= _PRIORITY[t.cls]] + [mine]
    if any(j.cls in JOB_CLASSES and is_resident(j.model) for j in queued):
        return "finishing the jobs on the model that is already loaded first"
    with _STATE_LOCK:
        sticky = _STICKY["model"]
    target = Planner.target_model(queued, is_resident, sticky)
    if target is not None and target != mine.model:
        n = sum(1 for j in queued if j.model == target and j.cls in JOB_CLASSES)
        return "taking %d job%s for %s together first" % (
            n, "" if n == 1 else "s", _model_label(target))
    return memory_block(model_need_gb(model))


_tq.set_gate(gate)


# ─────────────────────────────────────────────────────────────────────────────
#  Admission: the call every local transport makes
# ─────────────────────────────────────────────────────────────────────────────
_CLASS: contextvars.ContextVar = contextvars.ContextVar("local_scheduler_class", default=None)
_LABEL: contextvars.ContextVar = contextvars.ContextVar("local_scheduler_label", default=None)
_NEEDS: contextvars.ContextVar = contextvars.ContextVar("local_scheduler_needs", default=None)
_DEFERRABLE: contextvars.ContextVar = contextvars.ContextVar("local_scheduler_deferrable", default=True)


@contextlib.contextmanager
def work(label: str, cls: Optional[str] = None, needs: Optional[str] = None,
         quiet: bool = True, deferrable: Optional[bool] = None):
    """Name a stretch of work for the queue and the status line, and give it a
    class: every local call made inside it admits under both. A cancelled
    queued call ends the stretch quietly instead of raising out of it
    (``quiet=False`` lets ``LocalWorkCancelled`` through, for a caller that
    has its own failure path). A background call outside any queue job is
    deferrable work (it waits for idle); ``deferrable=False`` marks a stretch
    that is time-bound (a schedule that is due, an episode someone asked for)."""
    toks = [(_LABEL, _LABEL.set(label)), (_NEEDS, _NEEDS.set(needs or _NEEDS.get()))]
    if deferrable is not None:
        toks.append((_DEFERRABLE, _DEFERRABLE.set(bool(deferrable))))
    if cls in _PRIORITY:
        toks.append((_CLASS, _CLASS.set(cls)))
    try:
        yield
    except LocalWorkCancelled:
        if not quiet:
            raise
        _log.info("local scheduler: %s was cancelled while it waited", label)
    finally:
        for var, tok in reversed(toks):
            try:
                var.reset(tok)
            except Exception:
                pass


def _request_context() -> bool:
    try:
        from flask import has_request_context
        return bool(has_request_context())
    except Exception:
        return False


def _in_chat_turn() -> bool:
    """This thread is running a typed chat turn (the core turn registry)."""
    try:
        from agent_friday import core
        return bool(getattr(core._TURN_LOCAL, "turn_id", None))
    except Exception:
        return False


def resolve_class(explicit: Optional[str] = None, session_ctx: Optional[dict] = None) -> str:
    """The class a call admits at: what the caller said; the enclosing
    ``work()``; a spoken turn; the turn it serves; a background task; a typed
    chat turn; a web request (the owner is waiting on it); else background."""
    if explicit in _PRIORITY:
        return explicit
    ctx_cls = _CLASS.get()
    if ctx_cls in _PRIORITY:
        return ctx_cls
    sc = session_ctx if isinstance(session_ctx, dict) else {}
    if sc.get("is_voice"):
        return "voice"
    inherited = _tq.inherited_class()
    if inherited:
        return inherited
    if sc.get("is_background_task"):
        return "background"
    if _in_chat_turn():
        return "chat"
    if _request_context():
        return "user_job"
    return "background"


class Grant:
    """What ``admit`` hands back. Use it as a context manager, or ``release()``."""

    def __init__(self, kind: str, model: Any, cls: str, adm: Optional[_tq.Admission] = None,
                 how: str = "admitted", waited_s: float = 0.0, swap: bool = False):
        self.kind, self.model, self.cls = kind, model, cls
        self.adm, self.how, self.waited_s, self.swap = adm, how, waited_s, swap
        self._t0 = time.monotonic()
        self._done = False
        self.on_release = None

    @property
    def nested(self) -> bool:
        return self.how == "nested"

    def release(self, exc: Optional[BaseException] = None) -> None:
        if self._done:
            return
        self._done = True
        if self.on_release is not None:
            try:
                self.on_release(exc)
            except Exception:
                _log.debug("local scheduler: queue job end failed", exc_info=True)
            emit("job_done", cls=self.cls, kind=self.kind,
                 seconds=round(time.monotonic() - self._t0, 2))
        if self.adm is not None:
            self.adm.release()
            note_finished(self.model)
            if self.cls in JOB_CLASSES:
                emit("job_done", cls=self.cls, kind=self.kind,
                     seconds=round(time.monotonic() - self._t0, 2))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def _wait_line(say: Callable[[str], Any]) -> Callable[[dict], None]:
    """A queue-status hook that says the voice wait line once, when a spoken
    turn has waited ``VOICE_WAIT_S``."""
    said = [False]

    def on_status(snap: dict) -> None:
        if said[0] or snap.get("state") != "waiting":
            return
        if float(snap.get("waited_s") or 0.0) >= _tq.VOICE_WAIT_S:
            said[0] = True
            try:
                say(_tq.VOICE_WAIT_LINE + " ")
            except Exception:
                _log.debug("local scheduler: wait line not spoken", exc_info=True)
    return on_status


def admit(kind: str, *, model: Any = None, cls: Optional[str] = None,
          label: Optional[str] = None, needs: Optional[str] = None,
          session_ctx: Optional[dict] = None, local: bool = True,
          timeout_s: Optional[float] = None,
          say_wait: Optional[Callable[[str], Any]] = None) -> Grant:
    """Wait for the local model's turn, then hold it until ``Grant.release()``.

    ``kind`` names the call site (it is the key of the status line and the
    metrics). A call already inside an admitted turn on the same seat passes
    straight through. A cancelled wait raises ``LocalWorkCancelled``. A wait
    that outlasts ``local_scheduler_admit_timeout_s`` behind other tickets
    (never one the gate holds back on purpose) goes ahead unadmitted and is
    recorded as such, so a call can never be stranded behind a slot its own
    caller holds."""
    if not local or not enabled():
        return Grant(kind, model, "n/a", how="bypass")
    seat = _tq.norm_seat(model)
    held = _tq.held_class(seat)
    if held is not None:
        return Grant(kind, model, held, how="nested")
    # A call inside a job of the Local AI queue (background_gate.background_job
    # or an admitted worker) is admitted by that queue, once, with the job; it
    # does not queue a second time behind the job it belongs to.
    try:
        from agent_friday.services import background_gate as _bg
        if _bg.CURRENT_JOB.get():
            return Grant(kind, model, "background", how="nested")
    except Exception:
        pass
    use_cls = resolve_class(cls, session_ctx)
    if use_cls == "background" and WRAP_BACKGROUND[0] and _setting("local_scheduler_wrap_background", True):
        queued = _admit_as_queue_job(kind, model, label)
        if queued is not None:
            return queued
    tid = "%s:%s" % (kind, uuid.uuid4().hex[:10])
    limit = admit_timeout_s() if timeout_s is None else timeout_s
    adm = _tq.acquire(seat, use_cls, tid, label=label or _LABEL.get(), model=str(model or ""),
                      kind=kind, needs=needs or _NEEDS.get(), deferrable=_DEFERRABLE.get(),
                      on_status=(_wait_line(say_wait) if say_wait and use_cls == "voice" else None),
                      max_wait_s=(limit if limit and limit > 0 else None))
    if adm.outcome == "cancelled":
        raise LocalWorkCancelled(kind)
    if adm.outcome == "timeout":
        emit("failopen", cls=use_cls, kind=kind, waited_s=round(adm.waited_s, 1))
        _log.warning("local scheduler: %s waited %.0f s for %s and went ahead unadmitted",
                     kind, adm.waited_s, seat)
        return Grant(kind, model, use_cls, how="failopen", waited_s=adm.waited_s)
    if adm.outcome != "admitted":
        return Grant(kind, model, use_cls, how=adm.outcome)
    swap = note_started(model, use_cls) if use_cls in JOB_CLASSES else False
    emit("wait", cls=use_cls, kind=kind, seconds=round(adm.waited_s, 3))
    sample_ram()
    return Grant(kind, model, use_cls, adm=adm, waited_s=adm.waited_s, swap=swap)


class MeetingHold:
    """A recording meeting's place in the scheduler.

    The meeting is admitted at the ``voice`` class (the owner is here) and
    holds that ticket until ``release()``. It sits on a seat of its own, so it
    never takes the slot a chat turn needs, yet to every gate it reads as a
    person who is busy: ``people_active`` is true, the idle clock stays at
    zero, no ``background`` ticket is admitted, and a background job that is
    already running is asked to wait at its next checkpoint
    (``turn_queue.background_checkpoint``). The admission is taken on a thread
    of its own so the request thread that starts the meeting keeps no
    admission context behind it."""

    def __init__(self, meeting_id: str):
        self.id = str(meeting_id)
        self.holder = "meeting:" + self.id
        self.how = ""
        self._ready = threading.Event()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="meeting-scheduler-hold")

    def start(self, timeout_s: float = 5.0) -> "MeetingHold":
        self._thread.start()
        self._ready.wait(timeout_s)
        return self

    def _run(self) -> None:
        grant = None
        try:
            _tq.request_background_pause(self.holder, "you are in a meeting")
            grant = admit(MEETING_KIND, model=MEETING_SEAT, cls="voice",
                          label="Recording a meeting")
            self.how = grant.how
        except BaseException:                       # never strand start()
            _log.warning("local scheduler: a meeting could not be admitted", exc_info=True)
        finally:
            self._ready.set()
        self._done.wait()
        if grant is not None:
            grant.release()
        _tq.clear_background_pause(self.holder)

    def release(self) -> None:
        self._done.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(5.0)
        _tq.clear_background_pause(self.holder)       # even if the thread never ran


def hold_for_meeting(meeting_id: str) -> MeetingHold:
    return MeetingHold(meeting_id).start()


#: A direct background call outside any queue job becomes a deferrable job of
#: the Local AI queue: visible in /api/local-queue, idle-gated by
#: background_idle_minutes, one at a time. Tests that are not about that pin it off.
WRAP_BACKGROUND: List[bool] = [True]
_LOW_LEVEL_KINDS = ("scheduled", "time_bound")


def _admit_as_queue_job(kind: str, model: Any, label: Optional[str]) -> Optional[Grant]:
    """Admit a background-class call through the Local AI queue as a deferrable
    visible job (``services/visible_jobs``: a task card naming the model, and a
    row in the queue under the same id) and hand back a Grant that ends the job
    on release. None: not queued (the queue is not running here, the call is
    time-bound, or it already belongs to a job)."""
    import sys
    vj = None
    try:
        from agent_friday.services import background_gate as _bg
        from agent_friday.services import visible_jobs as _vj
        if _bg.CURRENT_JOB.get() or kind in _LOW_LEVEL_KINDS or not _DEFERRABLE.get():
            return None
        if _bg._SUPERVISOR_PROVIDER is None and (
                "agent_friday.services.agent" not in sys.modules
                or os.environ.get("FRIDAY_TESTING") == "1"):
            # No Local AI queue is running here (a scratch process, or a test that
            # did not give it one): the call goes straight to its ticket. A test
            # that wants the queue hands it over (``background_gate._SUPERVISOR_PROVIDER``).
            return None
        text = str(label or _LABEL.get() or _KIND_WORDS.get(kind, "background AI work"))
        vj = _vj.visible_job(text, kind="background_call", eager=False, deferrable=True,
                             label=text)
        vj.__enter__()
        if not _bg.CURRENT_JOB.get():
            vj.__exit__(None, None, None)           # nested in a job that is not queued: not ours
            return None
        try:
            _bg.before_local_model_call(model)      # admitted now: waits its turn, one at a time
        except _bg.JobCancelled as e:
            vj.__exit__(type(e), e, None)
            raise LocalWorkCancelled(kind)
        except BaseException as e:
            vj.__exit__(type(e), e, None)
            raise
    except LocalWorkCancelled:
        raise
    except Exception:
        _log.debug("local scheduler: queue unavailable, admitting directly", exc_info=True)
        return None
    g = Grant(kind, model, "background", how="queued")
    g.on_release = lambda exc=None: vj.__exit__(type(exc) if exc else None, exc, None)
    return g


def admitted(kind: str, *, model_arg: str = "model", seat: Optional[str] = None,
             cls: Optional[str] = None, local: Optional[Callable[[dict], bool]] = None,
             on_cancel: Optional[Callable[[], Any]] = None,
             session_arg: str = "session_ctx",
             model_fallback: Optional[Callable[[], Any]] = None,
             timeout_s: Optional[float] = None,
             say_wait_arg: Optional[str] = None):
    """Decorator for a local transport: every call admits under ``kind``.

    ``model_arg`` names the parameter that carries the model (``model_fallback``
    names it when the call leaves it out); ``seat`` fixes it (the voice front's
    own server). ``local(bound_args) -> bool`` lets a transport that serves
    both kinds (an OpenAI-compatible call) admit only its local legs.
    ``on_cancel`` is what a cancelled call returns instead of raising
    (``local_call.call`` never raises). ``timeout_s`` is how long the call
    waits behind other tickets before it goes ahead unadmitted (a short one for
    a check that sits in front of a person's send). ``say_wait_arg`` names a
    parameter that speaks (``on_delta``): a spoken turn that waits five seconds
    says the one wait line from turn_queue.VOICE_WAIT_LINE."""
    def deco(fn):
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            sig = None

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = {}
            if sig is not None:
                try:
                    bound = dict(sig.bind_partial(*args, **kwargs).arguments)
                except TypeError:
                    bound = dict(kwargs)
            is_local = True
            if local is not None:
                try:
                    is_local = bool(local(bound))
                except Exception:
                    is_local = False
            if not is_local:
                return fn(*args, **kwargs)      # outside the try: a failing call is never made twice
            model = seat if seat is not None else bound.get(model_arg)
            if model is None and model_fallback is not None:
                try:
                    model = model_fallback()
                except Exception:
                    model = None
            sc = bound.get(session_arg)
            try:
                say = bound.get(say_wait_arg) if say_wait_arg else None
                grant = admit(kind, model=model, cls=cls, session_ctx=sc, timeout_s=timeout_s,
                              say_wait=say if callable(say) else None)
            except LocalWorkCancelled:
                if on_cancel is not None:
                    return on_cancel()
                raise
            try:
                result = fn(*args, **kwargs)
            except BaseException as e:
                grant.release(e)
                raise
            grant.release()
            return result

        wrapper.__local_scheduler_admitted__ = kind
        return wrapper
    return deco


# ─────────────────────────────────────────────────────────────────────────────
#  Leases: a second request waits its turn
# ─────────────────────────────────────────────────────────────────────────────
class _Waiter:
    __slots__ = ("id", "kind", "cls", "seq", "since", "reason", "cancelled")

    def __init__(self, kind, cls, seq):
        self.id = "lease:%s:%d" % (kind, seq)
        self.kind, self.cls, self.seq = kind, cls, seq
        self.since = time.monotonic()
        self.reason = ""
        self.cancelled = False


_LEASE_WAITERS: List[_Waiter] = []
_LEASE_LOCK = threading.Lock()
_LEASE_SEQ = itertools.count(1)
_LEASE_LAST = {"kind": None}
LEASE_POLL_S = 0.25


def evicting_conflict(cls: str) -> str:
    """Why a lease of class ``cls`` may not stand the seats down now: a turn of
    its own class or a higher one is running, or work of a higher class is
    waiting. "" when it may. The turn that is asking for the lease (the chat
    turn whose tool wants an image) is not a conflict with itself."""
    prio = _PRIORITY.get(cls, _PRIORITY["user_job"])
    own = set(_tq.own_ticket_ids())
    try:
        for r in _tq.all_running():
            if r.id in own or str(r.kind or "").startswith("voice_front"):
                continue        # the voice front is its own process; a lease leaves it be
            if _PRIORITY[r.cls] <= prio:
                return "a %s is running on the local brain" % _class_word(r.cls)
        for w in _tq.all_waiting():
            if w.id not in own and _PRIORITY[w.cls] < prio:
                return "a %s is waiting for the local brain" % _class_word(w.cls)
    except Exception:
        return ""
    return ""


def _class_word(cls: str) -> str:
    return {"voice": "voice turn", "chat": "chat", "api": "Local API request",
            "user_job": "job you asked for", "background": "background job"}.get(cls, cls)


def _next_waiter() -> Optional[_Waiter]:
    live = [w for w in _LEASE_WAITERS if not w.cancelled]
    if not live:
        return None
    last = _LEASE_LAST["kind"]
    return sorted(live, key=lambda w: (_PRIORITY.get(w.cls, 9), 0 if w.kind == last else 1, w.seq))[0]


def lease_grant(arb, kind: str, *, role=None, ttl_s: int = 300,
                wait_s: Optional[float] = None, cls: Optional[str] = None) -> dict:
    """``Arbiter.grant``: queue behind a held lease and behind higher-class
    work, then take the lease. Returns the same dict ``_grant_now`` does; a
    request that waited its whole allowance returns ``ok: False`` with the
    reason it was still waiting."""
    use_cls = cls if cls in _PRIORITY else KIND_CLASS.get(kind, "user_job")
    if wait_s is None:
        wait_s = lease_wait_s()
        if kind == "voice_call":
            wait_s = min(wait_s, VOICE_LEASE_WAIT_S)
    if wait_s <= 0:
        return arb._grant_now(kind, role=role, ttl_s=ttl_s)       # no line: the old answer
    deadline = time.monotonic() + float(wait_s)
    w = _Waiter(kind, use_cls, next(_LEASE_SEQ))
    with _LEASE_LOCK:
        _LEASE_WAITERS.append(w)
    t0 = time.monotonic()
    result: dict = {}
    try:
        while True:
            reason = ""
            if w.cancelled:
                return {"ok": False, "cancelled": True, "queued": True,
                        "error": "The %s request was taken out of the line." % kind}
            held = getattr(arb, "lease", None)
            with _LEASE_LOCK:
                nxt = _next_waiter()
            if held is not None:
                reason = "waiting for the %s to finish" % _lease_word(held.get("kind"))
            elif nxt is not None and nxt is not w:
                reason = "waiting behind the %s request" % _lease_word(nxt.kind)
            else:
                reason = evicting_conflict(use_cls)
                if not reason and kind in LOADING_KINDS:
                    reason = memory_block(0.0)
            w.reason = reason
            if not reason:
                result = arb._grant_now(kind, role=role, ttl_s=ttl_s)
                err = str(result.get("error") or "")
                if result.get("ok") or "already held" not in err:
                    if result.get("ok"):
                        _LEASE_LAST["kind"] = kind
                        emit("lease", kind=kind, cls=use_cls,
                             waited_s=round(time.monotonic() - t0, 2))
                    return result
                w.reason = reason = err
            if time.monotonic() >= deadline:
                msg = "Waited %d s for the %s lease: %s." % (int(time.monotonic() - t0), kind, reason)
                return {"ok": False, "error": msg, "queued": True, "timed_out": True}
            time.sleep(min(LEASE_POLL_S, max(0.0, deadline - time.monotonic())))
    finally:
        with _LEASE_LOCK:
            if w in _LEASE_WAITERS:
                _LEASE_WAITERS.remove(w)


def _lease_word(kind: Any) -> str:
    return {"voice_call": "voice call", "heavy_turn": "heavy model turn",
            "image_job": "image job", "podcast_voice": "podcast voice",
            "bench_job": "benchmark"}.get(str(kind), str(kind or "lease"))


def lease_waiters() -> List[dict]:
    with _LEASE_LOCK:
        live = [w for w in _LEASE_WAITERS if not w.cancelled]
    return [{"id": w.id, "kind": w.kind, "class": w.cls, "reason": w.reason,
             "waited_s": round(time.monotonic() - w.since, 1)} for w in live]


# ─────────────────────────────────────────────────────────────────────────────
#  Metrics: a local jsonl, summarised in Settings
# ─────────────────────────────────────────────────────────────────────────────
_METRICS_LOCK = threading.Lock()
METRICS_MAX_BYTES = 2 * 1024 * 1024


def metrics_path():
    from agent_friday.paths import friday_home
    return friday_home() / "runtime" / "scheduler" / "metrics.jsonl"


def emit(ev: str, **fields) -> None:
    """Append one event to the local metrics file. Never raises, never leaves the PC."""
    try:
        rec = {"t": round(time.time(), 2), "ev": ev}
        rec.update(fields)
        p = metrics_path()
        with _METRICS_LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > METRICS_MAX_BYTES:
                keep = p.read_bytes()[-(METRICS_MAX_BYTES // 2):]
                keep = keep[keep.find(b"\n") + 1:]
                p.write_bytes(keep)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        _log.debug("local scheduler: metric not written", exc_info=True)


def _pct(vals: List[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return round(s[i], 2)


def summarize(events: Iterable[dict], *, hours: float = 24.0, now: Optional[float] = None) -> dict:
    """Swaps per hour, the lowest free RAM per hour, queue wait per class
    (p50/p95) and background throughput, over the last ``hours``."""
    now = time.time() if now is None else now
    since = now - hours * 3600.0
    swaps = 0
    ram_by_hour: Dict[int, float] = {}
    waits: Dict[str, List[float]] = {}
    jobs_done = 0
    failopen = 0
    for e in events:
        t = float(e.get("t") or 0)
        if t < since:
            continue
        ev = e.get("ev")
        if ev == "swap":
            swaps += 1
        elif ev == "ram":
            h = int(t // 3600)
            v = float(e.get("free_gb") or 0)
            ram_by_hour[h] = min(v, ram_by_hour.get(h, v))
        elif ev == "wait":
            waits.setdefault(str(e.get("cls")), []).append(float(e.get("seconds") or 0))
        elif ev == "job_done" and e.get("cls") == "background":
            jobs_done += 1
        elif ev == "failopen":
            failopen += 1
    span = max(hours, 1e-9)
    return {
        "hours": hours,
        "swaps": swaps,
        "swaps_per_hour": round(swaps / span, 2),
        "min_free_ram_gb_per_hour": [round(v, 2) for _h, v in sorted(ram_by_hour.items())],
        "min_free_ram_gb": (round(min(ram_by_hour.values()), 2) if ram_by_hour else None),
        "ram_floor_gb": ram_floor_gb(),
        "wait_s": {c: {"n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95)}
                   for c, v in sorted(waits.items(), key=lambda kv: _PRIORITY.get(kv[0], 9))},
        "background_jobs_done": jobs_done,
        "background_jobs_per_day": round(jobs_done * 24.0 / span, 2),
        "unadmitted_calls": failopen,
    }


def read_events(limit: int = 20000) -> List[dict]:
    try:
        p = metrics_path()
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
    except Exception:
        return []


def metrics_summary(hours: float = 24.0) -> dict:
    return summarize(read_events(), hours=hours)


# ─────────────────────────────────────────────────────────────────────────────
#  What is waiting, in words: jobs(), status(), cancel()
# ─────────────────────────────────────────────────────────────────────────────
_KIND_WORDS = {
    "local_call.call": "a local model call", "model_router.ollama": "a model call",
    "model_router.openai": "a model call", "voice_front.turn": "a spoken answer",
}


def _needs_phrase(model: Any, needs: Optional[str]) -> str:
    return needs or _model_label(model)


def _ticket_job(t: _tq.Ticket, state: str) -> dict:
    model = t.model or _tq._seat_model(t.seat)
    label = t.label or _KIND_WORDS.get(str(t.kind), str(t.kind or "local work"))
    interactive = t.cls in INTERACTIVE
    reason = t.blocked or ("" if state == "running" else "waiting its turn")
    return {"id": t.id, "source": "queue", "state": state, "class": t.cls,
            "interactive": interactive, "label": ("a chat" if t.cls == "chat" and not t.label
                                                  else label),
            "kind": t.kind, "model": model, "needs": _needs_phrase(model, t.needs),
            "reason": reason,
            "waited_s": round(max(0.0, time.monotonic() - t.enqueued_at), 1)
            if t.enqueued_at else 0.0,
            "cancellable": state == "waiting" and not interactive}


#: name -> (list_fn() -> [job dicts], cancel_fn(id) -> bool). Tests replace them.
_SOURCES: Dict[str, Tuple[Callable[[], List[dict]], Callable[[str], bool]]] = {}


def register_source(name: str, list_fn: Callable[[], List[dict]],
                    cancel_fn: Callable[[str], bool]) -> None:
    _SOURCES[name] = (list_fn, cancel_fn)


def clear_sources() -> None:
    _SOURCES.clear()


def _supervisor_snapshot() -> dict:
    """The Local AI queue's own rows (services/seat_supervisor): every
    background job that holds or awaits a seat, task or inline."""
    import sys
    try:
        from agent_friday.services import background_gate as _bg
        if _bg._SUPERVISOR_PROVIDER is None and "agent_friday.services.agent" not in sys.modules:
            return {}                   # nothing has queued anything yet
        snap = _bg._supervisor().snapshot()
        return snap if isinstance(snap, dict) else {}
    except Exception:
        _log.debug("local scheduler: supervisor snapshot unavailable", exc_info=True)
        return {}


def _task_jobs() -> List[dict]:
    """Background jobs on the Local AI queue (seat_supervisor), running or waiting."""
    out = []
    snap = _supervisor_snapshot()
    now = time.time()
    for row in list(snap.get("running") or []) + list(snap.get("queued") or []):
        waiting = row.get("state") == "queued"
        since = row.get("queued_at")
        out.append({"id": str(row.get("id")), "source": "task",
                    "state": "waiting" if waiting else "running",
                    # Work a person is waiting on (a workspace button, a tool in
                    # their own turn: services/visible_jobs) is a job they asked for.
                    "class": "user_job" if row.get("foreground") else "background",
                    "interactive": False,
                    "label": row.get("label") or "a background task",
                    "kind": row.get("kind") or "task",
                    "model": str(row.get("seat") or ""),
                    "needs": _needs_phrase(_tq.norm_seat(row.get("seat")), None),
                    "reason": row.get("why") or "",
                    "paused": row.get("state") == "paused",
                    "deferrable": bool(row.get("deferrable")),
                    "run_now": bool(row.get("run_now")),
                    "merged": int(row.get("merged") or 0),
                    "waited_s": (round(max(0.0, now - float(since)), 1) if since else 0.0),
                    "cancellable": True})
    return out


def _cancel_task(job_id: str) -> bool:
    """Cancel a job on the Local AI queue: a queued one never starts, a running
    one stops before its next model call."""
    agent = _loaded("agent_friday.services.agent")
    if agent is None:
        return False
    try:
        return bool(agent.local_queue_cancel(job_id))
    except Exception:
        return False


def run_now(job_id: str) -> bool:
    """Skip the idle wait for one deferrable job. It still runs one at a time
    and still yields to a person's turn."""
    try:
        from agent_friday.services import background_gate as _bg
        return bool(_bg._supervisor().run_now(str(job_id)))
    except Exception:
        return False


def _loaded(name: str) -> Any:
    """The module ``name`` once it is fully imported, else None; never starts
    an import. A module is in sys.modules from the moment its import begins:
    one another thread is still importing is waited for (the import system's
    module lock), never read half made."""
    import sys
    if name not in sys.modules:
        return None
    import importlib
    try:
        return importlib.import_module(name)
    except Exception:
        return None


_PODCAST_MEMO: List[Any] = [0.0, []]


def _podcast_jobs() -> List[dict]:
    """Episodes waiting for their turn or being spoken. Read from disk, so kept
    for five seconds: the chip and Settings both poll."""
    pe = _loaded("agent_friday.services.podcast_engine")
    if pe is None:
        return []
    if time.monotonic() - _PODCAST_MEMO[0] < 5.0:
        return list(_PODCAST_MEMO[1])
    out = _podcast_jobs_now(pe)
    _PODCAST_MEMO[0], _PODCAST_MEMO[1] = time.monotonic(), out
    return list(out)


def _podcast_seat() -> str:
    """The local model an episode will be written on, or "" when none is serving."""
    try:
        from agent_friday.services.scheduler import _resolve_local_seat
        return str(_resolve_local_seat() or "")
    except Exception:
        return ""


def _podcast_jobs_now(pe) -> List[dict]:
    """An episode's WRITING is a job of the Local AI queue (``podcast_engine.queue_job``),
    so that stage is the queue's own row, not listed twice. What this adds is an
    episode that has not reached the queue yet (waiting for stand-down or the
    GPU) and the stages that load no language model (speaking, checking)."""
    out = []
    try:
        eps = pe.pending()
    except Exception:
        return []
    queue_has_podcast = any(r.get("kind") == pe.QUEUE_KIND
                            for r in (_supervisor_snapshot().get("running") or []))
    seat = _podcast_seat() if eps else ""
    for ep in eps:
        status = ep.get("status")
        if status == "writing" and queue_has_podcast:
            continue
        running = status in ("writing", "speaking", "checking")
        reason = "" if running else (pe._gate_reason(ep) or "waiting its turn")
        out.append({"id": "podcast:%s" % ep.get("id"), "source": "podcast",
                    "state": "running" if running else "waiting", "class": "background",
                    "interactive": False, "label": "podcast", "kind": "podcast",
                    "model": seat, "needs": "the big brain" if status in ("queued", "waiting", "writing")
                    else "the voice", "reason": reason,
                    "waited_s": round(max(0.0, time.time() - float(ep.get("created_at") or time.time())), 1),
                    "cancellable": not running})
    return out


def _cancel_podcast(job_id: str) -> bool:
    pe = _loaded("agent_friday.services.podcast_engine")
    if pe is None or not job_id.startswith("podcast:"):
        return False
    return pe.cancel(job_id.split(":", 1)[1]) is not None


def _lease_jobs() -> List[dict]:
    return [{"id": w["id"], "source": "lease", "state": "waiting", "class": w["class"],
             "interactive": w["class"] in INTERACTIVE, "label": _lease_word(w["kind"]),
             "kind": "lease:%s" % w["kind"], "model": "", "needs": "the GPU",
             "reason": w["reason"] or "waiting its turn", "waited_s": w["waited_s"],
             "cancellable": w["class"] not in INTERACTIVE} for w in lease_waiters()]


def _cancel_lease(job_id: str) -> bool:
    with _LEASE_LOCK:
        for w in _LEASE_WAITERS:
            if w.id == job_id:
                w.cancelled = True
                return True
    return False


def _capability_jobs() -> List[dict]:
    """The idle capability re-assessment / voice measurement, when one is wanted."""
    m = _loaded("agent_friday.services.local_voice_readiness")
    return m._jobs_listing() if m is not None else []


def _cancel_capability(job_id: str) -> bool:
    m = _loaded("agent_friday.services.local_voice_readiness")
    return bool(m is not None and m._cancel_listed(job_id))


def _library_index_jobs() -> List[dict]:
    """Folders the Library will read but has not started, or has paused (yours, battery, off the record).
    A folder being read is the queue's own row."""
    m = _loaded("agent_friday.services.library.index_job")
    return m.listed_jobs() if m is not None else []


def _cancel_library_index(job_id: str) -> bool:
    m = _loaded("agent_friday.services.library.index_job")
    return bool(m is not None and m.cancel_listed(job_id))


def _default_sources() -> Dict[str, Tuple[Callable, Callable]]:
    return {"task": (_task_jobs, _cancel_task), "podcast": (_podcast_jobs, _cancel_podcast),
            "lease": (_lease_jobs, _cancel_lease),
            "local_capability": (_capability_jobs, _cancel_capability),
            "library_index": (_library_index_jobs, _cancel_library_index)}


def _active_sources() -> Dict[str, Tuple[Callable, Callable]]:
    """The built-in sources plus every registered one (a registered name replaces
    the built-in of that name). Registering a source never hides the others: the
    queue's own tasks, podcasts and leases stay listed once work orders register."""
    out = _default_sources()
    out.update(_SOURCES)
    return out


def jobs(*, include_running: bool = True) -> List[dict]:
    """Every job the scheduler knows: running first, then waiting in the order
    they will go (class, then arrival). Interactive turns are marked as such."""
    out: List[dict] = []
    seen = set()
    # The Local AI queue's rows first: a background task also holds a ticket
    # here under the same id, and the queue's row says more (why it waits,
    # paused, merged). One job, one row.
    task_src = _active_sources().get("task")
    if task_src is not None and task_src[0] is _task_jobs:
        for j in _task_jobs():
            out.append(j)
            seen.add(j["id"])
    try:
        if include_running:
            for t in _tq.all_running():
                if t.id not in seen:
                    out.append(_ticket_job(t, "running"))
                    seen.add(t.id)
        for t in _tq.all_waiting():
            if t.id not in seen:
                out.append(_ticket_job(t, "waiting"))
                seen.add(t.id)
    except Exception:
        _log.debug("local scheduler: queue not readable", exc_info=True)
    for name, (list_fn, _c) in _active_sources().items():
        if list_fn is _task_jobs:
            continue                    # listed first, above
        try:
            for j in list_fn() or []:
                if j.get("id") not in seen:
                    out.append(j)
        except Exception:
            _log.debug("local scheduler: source %s failed", name, exc_info=True)
    if not include_running:
        out = [j for j in out if j["state"] == "waiting"]
    order = {"running": 0, "waiting": 1}
    out.sort(key=lambda j: (order.get(j["state"], 2), _PRIORITY.get(j["class"], 9),
                            -float(j.get("waited_s") or 0)))
    # Every row says its place: 0 while it runs, 1 for the next to go, and so on.
    place = 0
    for j in out:
        if j["state"] == "waiting":
            place += 1
            j["position"] = place
        else:
            j["position"] = 0
    return out


def _clause(j: dict) -> str:
    bits = []
    if j.get("needs"):
        bits.append("needs " + j["needs"])
    if j.get("reason"):
        bits.append(j["reason"])
    return "; ".join(bits)


def status() -> dict:
    """The honest picture, for the chip, Settings and the voice tool."""
    all_jobs = jobs()
    running = [j for j in all_jobs if j["state"] == "running"]
    waiting = [j for j in all_jobs if j["state"] == "waiting"]
    job_waiting = [j for j in waiting if not j["interactive"]]
    chip = ""
    speech = "Nothing is waiting. The local brain is free."
    if job_waiting:
        n = len(job_waiting)
        nxt = job_waiting[0]
        chip = "%d job%s waiting · next: %s (%s)" % (n, "" if n == 1 else "s", nxt["label"], _clause(nxt))
        speech = "%s job%s waiting. Next is %s; %s." % (
            _tq._count_word(n).capitalize(), " is" if n == 1 else "s are", nxt["label"], _clause(nxt))
        if n == 1:
            speech = "One job is waiting: %s; %s." % (nxt["label"], _clause(nxt))
    elif waiting:
        speech = "A chat is waiting for the local brain."
    elif running:
        r = running[0]
        speech = "The local brain is busy with %s." % (
            r["label"] if r["class"] != "chat" else "a chat")
    return {"ok": True, "enabled": enabled(), "running": running, "waiting": waiting,
            "job_count": len(job_waiting), "chip": chip, "speech": speech,
            "idle_s": round(owner_idle_s(), 1), "idle_window_s": idle_window_s(),
            "free_ram_gb": (round(free_ram_gb(), 1) if free_ram_gb() is not None else None),
            "ram_floor_gb": ram_floor_gb(), "activity": last_activity()}


def queue_snapshot() -> dict:
    """THE Local AI queue, in one shape, for ``GET /api/local-queue`` (the
    top-bar chip), the tray tooltip and the ``queue_status`` voice tool.

    One source with ``status()``: ``jobs()``. The Local AI queue's own rows
    (``seat_supervisor.snapshot``) keep their shape (id, label, kind, state,
    why, deferrable, run_now, queued_at, merged); the other background and
    user-requested work the scheduler holds (a podcast waiting for the big
    brain, a GPU lease, a call at a transport) joins them as rows of the same
    shape. Labels and reasons only, never a job's content."""
    sup = _supervisor_snapshot()
    running: List[dict] = []
    queued: List[dict] = []
    seen = set()
    # The queue's rows first, in the order it will promote them.
    for r in list(sup.get("running") or []) + list(sup.get("queued") or []):
        row = dict(r)
        row["source"] = "task"
        row["cancellable"] = True
        seen.add(str(row.get("id")))
        (queued if row.get("state") == "queued" else running).append(row)
    try:
        listed = jobs()
    except Exception:
        listed = []
    for j in listed:
        if str(j["id"]) in seen or j.get("interactive") or j.get("class") not in JOB_CLASSES:
            continue
        row = {"id": j["id"], "label": str(j.get("label") or "a background task")[:80],
               # The chip words a kind (LOCAL_AI_KIND_WORDS): a call at a
               # transport is a "model_call", a waiting lease a "gpu_lease".
               "kind": {"queue": "model_call", "lease": "gpu_lease"}.get(
                   str(j.get("source")), j.get("kind") or "task"),
               "state": "queued" if j["state"] == "waiting" else "running",
               "why": j.get("reason") or "", "seat": j.get("model") or None,
               "deferrable": False, "run_now": False, "queued_at": None, "merged": 0,
               "source": j.get("source"), "cancellable": bool(j.get("cancellable", True))}
        if isinstance(j.get("progress"), dict):
            row["progress"] = dict(j["progress"])
        (queued if row["state"] == "queued" else running).append(row)
    snap = {"running": running, "queued": queued, "count": len(queued),
            "jobs": running + queued}
    ij = _loaded("agent_friday.services.library.index_job")
    # _loaded waits out another thread's import; a stand-in without the function reads as unknown.
    any_paused = getattr(ij, "any_paused", None)
    if any_paused is not None:
        snap["library"] = {"paused": bool(any_paused())}         # the chip's Pause / Resume
    try:
        from agent_friday.services import background_gate as _bg
        st = _bg.idle_state()
        snap["idle"] = {"idle": bool(st["idle"]), "idle_s": int(st["idle_s"]),
                        "threshold_s": int(st["threshold_s"]),
                        "os_readable": bool(st["os_readable"])}
    except Exception:
        snap["idle"] = None
    return snap


def cancel(job_id: str, *, _skip_queue: bool = False) -> dict:
    """Take one waiting job out of the line. A running job is not stopped here
    (its own Stop does that); an interactive turn is cancelled from the chat."""
    job_id = str(job_id or "")
    if _tq.cancel(job_id):
        return {"ok": True, "cancelled": job_id}
    for name, (_l, cancel_fn) in _active_sources().items():
        if _skip_queue and cancel_fn is _cancel_task:
            continue                    # the caller IS the queue's cancel
        try:
            if cancel_fn(job_id):
                return {"ok": True, "cancelled": job_id}
        except Exception:
            _log.debug("local scheduler: cancel via %s failed", name, exc_info=True)
    return {"ok": False, "error": "That job is not waiting any more."}


def find_waiting(words: str) -> List[dict]:
    """Waiting, cancellable jobs whose label, kind or model mention ``words``."""
    want = str(words or "").strip().lower()
    cands = [j for j in jobs(include_running=False) if j.get("cancellable")]
    if not want:
        return cands
    return [j for j in cands if want in " ".join(
        str(j.get(k) or "") for k in ("label", "kind", "model", "needs")).lower()]


def cancel_by_words(words: str) -> dict:
    """"Cancel the podcast job": one match is cancelled; several are listed."""
    found = find_waiting(words)
    if not found:
        return {"ok": False, "message": "Nothing waiting matches that, so there is nothing to cancel."}
    if len(found) > 1 and not str(words or "").strip():
        return {"ok": False, "choices": [j["label"] for j in found],
                "message": "Which one? Waiting: " + "; ".join(j["label"] for j in found) + "."}
    done = [j["label"] for j in found if cancel(j["id"]).get("ok")]
    if not done:
        return {"ok": False, "message": "Those were already running, so they are not waiting to cancel."}
    return {"ok": True, "cancelled": done,
            "message": "Cancelled %s. It never reached the model." % "; ".join(done)}


def reset_for_tests() -> None:
    """Forget residency memory, probes, sources and lease waiters. Tests only."""
    with _STATE_LOCK:
        _RECENT.clear()
        _OWNED_MEMO.clear()
        _NEED_MEMO.clear()
        _STICKY.update(model=None, at=0.0)
    _RAM_PROBE[0] = None
    _RESIDENCY_PROBE[0] = None
    _CLOCK[0] = time.time
    _SOURCES.clear()
    _PODCAST_MEMO[0], _PODCAST_MEMO[1] = 0.0, []
    _LAST_RAM_SAMPLE[0] = 0.0
    _LEASE_LAST["kind"] = None
    with _LEASE_LOCK:
        del _LEASE_WAITERS[:]
    with _ACTIVITY_LOCK:
        _ACTIVITY.clear()
    _tq.set_gate(gate)
