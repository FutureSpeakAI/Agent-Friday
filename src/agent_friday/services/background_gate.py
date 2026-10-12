"""When a background local-model job may start, and when a running one waits.

The local seat serves one request at a time. Interactive turns (a typed chat
turn, a voice turn) are never queued here: they always win. Background jobs
(wiki distillation, scheduled briefings, the prefix warm, spawned tasks) go
through ONE queue, ``seat_queue.SeatQueue`` driven by
``seat_supervisor.SeatSupervisor``. This module holds no queue. It supplies
the signals that queue consults:

* who is interactive right now (the chat turn registry in ``core`` and the
  voice registrations made here);
* how long the computer has been idle (Windows last-input time, combined with
  Friday's own activity in ``work_queue``);
* the one rule both admission and the cooperative wait apply,
  ``block_reason(record)``: why this job may not take the seat now, or "". A record marked
  ``memory_floor`` (CPU work such as the Library reading folders) also waits while free RAM is
  under the scheduler's floor (``local_scheduler_ram_floor_gb``, the one a model load keeps).

and the choke point every local model request passes,
``before_local_model_call()``. A request made inside a background job waits
there, before it is sent, while an interactive turn is active (and, for
deferrable work, while the user is at the keyboard). A request already sent is
never interrupted: cancelling mid-generation would throw away the seat's
prompt cache for nothing.

Idle is the MINIMUM of the signals that can be read. When the OS signal
cannot be read (another platform, a service session, an API failure) the
machine is treated as NOT idle, so deferrable work waits; ``DRAIN_CAP_S``
then lets it run once Friday itself has seen no activity for six hours, so a
night's distillation still happens on a machine whose idle time is unknown.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger(__name__)

#: The Settings default for ``background_idle_minutes``.
DEFAULT_IDLE_MINUTES = 10
#: The choices the Settings row offers (minutes).
IDLE_MINUTES_CHOICES = (5, 10, 15, 30, 60)
MIN_IDLE_MINUTES = 1
MAX_IDLE_MINUTES = 240

#: With no readable OS idle time, deferrable work still runs after Friday has
#: seen no chat, voice or UI activity for this long (six hours: a night).
DRAIN_CAP_S = 6 * 3600.0

#: How often a waiting job re-checks the signals.
POLL_S = 0.5

# The reasons the queue shows. Plain words, never the job's content.
REASON_INTERACTIVE = "waits for your chat to finish"
REASON_IDLE = "waits for idle"
REASON_SEAT = "waits for the local model"
REASON_PAUSED = "paused by you"

#: An open voice call, as opposed to a voice TURN in progress ("voice"). A
#: call means the owner is here: deferrable work waits for it to end. Only a
#: turn blocks everything else.
KIND_VOICE_CALL = "voice-call"


class JobCancelled(RuntimeError):
    """A background job was cancelled from the Local AI queue."""

    def __init__(self, msg: str = "Cancelled from the Local AI queue.") -> None:
        super().__init__(msg)


# ─────────────────────────────────────────────────────────────────────────────
#  Idle
# ─────────────────────────────────────────────────────────────────────────────

def _read_os_idle_s() -> Optional[float]:
    """Seconds since the last keyboard or mouse input in this session, or None.

    user32.GetLastInputInfo reports the tick of the last input event; the
    difference from GetTickCount (both 32-bit milliseconds) is the idle time,
    masked so the 49.7-day wraparound stays correct.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        get_tick = ctypes.windll.kernel32.GetTickCount
        get_tick.restype = ctypes.c_uint32
        now_ms = int(get_tick())
        return float(((now_ms - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0)
    except Exception:
        return None


#: Replaceable in tests: () -> seconds or None.
OS_IDLE_READER: Callable[[], Optional[float]] = _read_os_idle_s


def friday_idle_seconds() -> float:
    """Seconds since Friday last saw the user (UI, chat or voice activity)."""
    try:
        from agent_friday.services import work_queue as _wq
        return float(_wq.idle_seconds())
    except Exception:
        return 0.0


_THRESHOLD_CACHE: Dict[str, float] = {"at": 0.0, "value": DEFAULT_IDLE_MINUTES * 60.0}


def idle_minutes_setting(settings: Optional[dict] = None) -> int:
    """``background_idle_minutes`` from settings, clamped, default 10."""
    if settings is None:
        try:
            from agent_friday.core import _load_settings
            settings = _load_settings() or {}
        except Exception:
            settings = {}
    try:
        v = int(settings.get("background_idle_minutes", DEFAULT_IDLE_MINUTES))
    except (TypeError, ValueError):
        v = DEFAULT_IDLE_MINUTES
    return max(MIN_IDLE_MINUTES, min(MAX_IDLE_MINUTES, v))


def idle_threshold_s() -> float:
    """The idle threshold in seconds, re-read at most every five seconds."""
    now = time.monotonic()
    if now - _THRESHOLD_CACHE["at"] > 5.0:
        _THRESHOLD_CACHE["value"] = idle_minutes_setting() * 60.0
        _THRESHOLD_CACHE["at"] = now
    return _THRESHOLD_CACHE["value"]


def idle_state() -> Dict[str, Any]:
    """How idle the machine is, and whether deferrable work may run."""
    try:
        os_s = OS_IDLE_READER()
    except Exception:
        os_s = None
    fri = friday_idle_seconds()
    thr = idle_threshold_s()
    if os_s is None:
        idle = fri >= DRAIN_CAP_S
        return {"idle": idle, "idle_s": fri, "os_idle_s": None,
                "friday_idle_s": fri, "threshold_s": thr, "os_readable": False,
                "drain_cap_s": DRAIN_CAP_S}
    idle_s = min(float(os_s), fri)
    return {"idle": idle_s >= thr, "idle_s": idle_s, "os_idle_s": float(os_s),
            "friday_idle_s": fri, "threshold_s": thr, "os_readable": True,
            "drain_cap_s": DRAIN_CAP_S}


def idle_seconds() -> float:
    """The combined idle time (the minimum of the readable signals); 0 when it
    cannot be read (see ``safe_idle_state``)."""
    return float(safe_idle_state()["idle_s"])


# ── A check that fails holds background work (fail closed) ────────────────────
#
# An idle check that raises must never let background work start while the
# owner may be at the keyboard. Every caller that decides whether background
# work may run reads the idle state through ``safe_idle_state`` or answers a
# failed gate with ``failed_check_reason``: the work waits, the failure is
# logged once per place, and DRAIN_CAP_S still bounds the wait (Friday's own
# clock, the same cap an unreadable OS clock gets). Interactive turns, voice
# and work a person is waiting on (``foreground``, ``user_job``) are never
# held by a failed check: their callers decide them before asking.

_FAILED_CHECKS: set = set()
_FAILED_CHECKS_LOCK = threading.Lock()


def note_failed_check(where: str) -> None:
    """Log, once per place, that an idle check raised (call from an except block)."""
    with _FAILED_CHECKS_LOCK:
        first = where not in _FAILED_CHECKS
        _FAILED_CHECKS.add(where)
    if first:
        _log.warning("background gate: the idle check in %s failed; background work "
                     "waits until it reads again", where, exc_info=True)


def drained_after_failure() -> bool:
    """The drain cap for a failed check: Friday has seen nobody for DRAIN_CAP_S."""
    try:
        return float(friday_idle_seconds()) >= DRAIN_CAP_S
    except Exception:
        return False


def failed_check_reason(where: str, words: str = REASON_IDLE) -> str:
    """The reason background work waits after the check at ``where`` raised:
    ``words`` until the drain cap has passed, then "" (call from an except block)."""
    note_failed_check(where)
    return "" if drained_after_failure() else words


def safe_idle_state() -> Dict[str, Any]:
    """``idle_state()``, or a NOT-idle state when it raises (idle once the drain
    cap has passed). Never raises."""
    try:
        return idle_state()
    except Exception:
        note_failed_check("idle_state")
    drained = drained_after_failure()
    try:
        fri = float(friday_idle_seconds())
    except Exception:
        fri = 0.0
    return {"idle": drained, "idle_s": fri if drained else 0.0, "os_idle_s": None,
            "friday_idle_s": fri, "threshold_s": DEFAULT_IDLE_MINUTES * 60.0,
            "os_readable": False, "drain_cap_s": DRAIN_CAP_S, "failed": True}


# ─────────────────────────────────────────────────────────────────────────────
#  Interactive activity
# ─────────────────────────────────────────────────────────────────────────────

_INTERACTIVE: Dict[str, Dict[str, Any]] = {}
_INTERACTIVE_LOCK = threading.Lock()


def _touch_friday_activity() -> None:
    try:
        from agent_friday.services import work_queue as _wq
        _wq.mark_active()
    except Exception:
        pass


def interactive_begin(tag: str, kind: str = "voice",
                      alive: Optional[Callable[[], bool]] = None) -> None:
    """Register an interactive activity (a voice session or turn).

    ``alive`` lets an entry whose owner died without ending it drop out by
    itself rather than holding background work forever.
    """
    with _INTERACTIVE_LOCK:
        _INTERACTIVE[str(tag)] = {"kind": kind, "since": time.time(), "alive": alive}
    _touch_friday_activity()


def interactive_end(tag: str) -> None:
    with _INTERACTIVE_LOCK:
        _INTERACTIVE.pop(str(tag), None)
    _touch_friday_activity()


@contextlib.contextmanager
def interactive(tag: str, kind: str = "voice"):
    interactive_begin(tag, kind)
    try:
        yield
    finally:
        interactive_end(tag)


def interactive_kinds() -> List[str]:
    """The interactive activities running now: "chat" per live chat turn (the
    core turn registry) and the kind of each registration made here. No
    background job is exempt from any of them: no turn waits synchronously on
    a job, so a job spawned by a turn simply starts after it."""
    out: List[str] = []
    with _INTERACTIVE_LOCK:
        for tag, rec in list(_INTERACTIVE.items()):
            alive = rec.get("alive")
            try:
                if alive is not None and not alive():
                    _INTERACTIVE.pop(tag, None)
                    continue
            except Exception:
                pass
            out.append(str(rec.get("kind") or "voice"))
    try:
        from agent_friday import core
        with core._TURNS_LOCK:
            turns = list(core._TURNS.items())
        for _tid, rec in turns:
            th = rec.get("thread")
            if th is not None and th.is_alive():
                out.append("chat")
    except Exception:
        pass
    if out:
        _touch_friday_activity()
    return out


def turn_queue_people() -> bool:
    """A typed chat, a spoken turn or a Local API call is running or waiting in
    the local scheduler's turn queue (services/turn_queue). The same rule as
    the registrations above, read from the queue the 1.1 transports admit
    through, so a turn that never registered here still wins. A job the owner
    asked for (``user_job``) is not counted: it may be waiting on this one."""
    try:
        from agent_friday.services import turn_queue as _tq
        for q in _tq.queues():
            for t in list(q.running_tickets()) + list(q.waiting_tickets()):
                if t.cls in ("voice", "chat", "api"):
                    return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  The one rule
# ─────────────────────────────────────────────────────────────────────────────

def block_reason(record: Dict[str, Any]) -> str:
    """Why this background job may not hold the seat right now, or "".

    Applied by the queue before a job starts and by the cooperative wait
    before each of a running job's model calls. Cloud seats are never asked.
    Run now bypasses the idle wait only; an interactive turn always wins.
    """
    if not record.get("seat_is_local"):
        return ""
    if record.get("user_paused"):
        # The owner's Pause on this job (seat_supervisor.pause): it holds even work they asked for.
        return REASON_PAUSED
    if record.get("foreground"):
        # Work the person (or a turn of theirs) is waiting on, launched from a
        # workspace button or from a tool inside a chat turn
        # (services/visible_jobs). It queues behind other jobs for the seat and
        # reports its place, but never waits for the interactive turn that may
        # itself be waiting on it, and never for idle.
        return ""
    kinds = interactive_kinds()
    if any(k != KIND_VOICE_CALL for k in kinds) or turn_queue_people():
        return REASON_INTERACTIVE
    if record.get("deferrable") and not record.get("run_now"):
        # An open call is not idle, even when nobody has touched a key.
        if KIND_VOICE_CALL in kinds or not safe_idle_state()["idle"]:
            return REASON_IDLE
    if record.get("memory_floor"):
        # CPU work that holds memory while it runs (the Library reading folders) keeps the same
        # free-RAM floor a model load keeps, and pauses, not fails, when the PC runs short.
        try:
            from agent_friday.services import local_scheduler as _ls
            return _ls.memory_block(0.0)
        except Exception:
            return ""
    return ""


# ─────────────────────────────────────────────────────────────────────────────
#  The job context and the choke point
# ─────────────────────────────────────────────────────────────────────────────

#: The background job running in this context: ``{"id": job_id}`` once
#: admitted, or ``{"pending": True, ...spec}`` for inline work (a scheduled
#: builtin) that is admitted at its first local model call.
CURRENT_JOB: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "friday_background_job", default=None)

#: Replaceable in tests: () -> the SeatSupervisor.
_SUPERVISOR_PROVIDER: Optional[Callable[[], Any]] = None


def _supervisor():
    if _SUPERVISOR_PROVIDER is not None:
        return _SUPERVISOR_PROVIDER()
    from agent_friday.services.agent import _seat_supervisor
    return _seat_supervisor()


@contextlib.contextmanager
def admitted_job(job_id: str):
    """Mark this thread's work as the admitted background job ``job_id``."""
    tok = CURRENT_JOB.set({"id": job_id})
    try:
        yield
    finally:
        CURRENT_JOB.reset(tok)


@contextlib.contextmanager
def background_job(*, kind: str, key: str, label: str, deferrable: bool):
    """Inline background work (a scheduled builtin): admitted through the
    queue at its first LOCAL model call and released when the block ends.
    Work that never reaches a local model never queues."""
    job = {"pending": True, "kind": kind, "key": key, "label": label,
           "deferrable": bool(deferrable)}
    tok = CURRENT_JOB.set(job)
    status = "failed"
    try:
        yield job
        status = "completed"
    finally:
        hold = job.pop("hold", None)
        if hold is not None:
            try:
                hold.__exit__(None, None, None)
            except Exception:
                pass
        CURRENT_JOB.reset(tok)
        if job.get("id"):
            try:
                _supervisor().on_task_end(job["id"], status=status)
            except Exception:
                pass


def before_local_model_call(model: Optional[str] = None) -> None:
    """The choke point: called before every request to a LOCAL seat.

    Outside a background job (an interactive turn, an on-demand request) it
    returns at once. Inside one it admits pending inline work through the
    queue, then waits while ``block_reason`` holds. Raises JobCancelled when
    the job was cancelled from the queue.
    """
    job = CURRENT_JOB.get()
    if not job:
        return
    sup = _supervisor()
    if job.get("pending"):
        seat = "local/" + (str(model).strip() if model else "default")
        job_id = sup.admit_inline({
            # A workspace job (services/visible_jobs) queues under its own task
            # id, so the queue row and the task card are one thing.
            "id": job.get("task_id"),
            "foreground": bool(job.get("foreground")),
            "kind": job.get("kind"), "job_key": job.get("key"),
            "label": job.get("label"), "deferrable": job.get("deferrable"),
            "seat": seat, "seat_is_local": True,
        })
        job["pending"] = False
        job["id"] = job_id
        # The job's own model calls run on this thread under the slot it was
        # just admitted to: they must not queue again behind it (turn_queue.HELD).
        try:
            from agent_friday.services import turn_queue as _tq
            hold = _tq.holding(seat, "user_job" if job.get("foreground") else "background")
            hold.__enter__()
            job["hold"] = hold
        except Exception:
            pass
    if job.get("id"):
        sup.wait_while_blocked(job["id"])
