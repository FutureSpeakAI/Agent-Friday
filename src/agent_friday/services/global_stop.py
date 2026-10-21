"""Stop everything: one control that halts every kind of work Friday is doing.

Before this, stopping was per lane: a chat turn had its own Stop, a task its own
"stop after this step", a workflow chain its own stop, browser sessions their own
"stop all", queue jobs a pause, computer control a kill switch. Nothing stopped all
of them at once, and nothing stopped a model call that was already streaming.

``stop()`` does, from the top-bar button, Escape-held, typed words ("stop
everything"), spoken words (Laya's rule layer, so it needs no model), or the
``task_control`` tool's ``stop_all``:

  * the in-memory flag (``core.GLOBAL_STOP``) is set FIRST, so every check below
    sees it before any lane is walked: running chat turns stop at their next round
    (``core.turn_stop_requested``), a streaming model reply is cut at once (the SSE
    reader closes the stream, model_router), every task and website task sees its
    stop (``task_journal.stop_requested``), no tool runs (``agent._execute_tool``),
    no new turn, task, worker, voice call or scheduled run starts, background and
    trial work waits (``background_gate``), and any local model call refuses;
  * then each lane is told directly: every registered chat turn, every unfinished
    task (durably, through the task journal), delegated workers, the browser
    sessions Friday drives, live voice calls (closed), computer control (the kill
    switch, plus any held modifier keys released), and the background queue;
  * the state is persisted, so a restart comes back stopped, not running;
  * a signed receipt goes into the governance receipt chain (decision-bom.jsonl).

Stopping never needs approval and is open to every channel the owner uses: it only
ends work. RESUMING is different. It re-opens outward work, so ``resume()`` is a
press on Friday's own page on this PC (``screen_click.screen_session``); a word,
a tool call or a program cannot resume. Resume writes its receipt first and does
not resume if the receipt cannot be written. Work that was stopped stays stopped;
resume lets new and queued work run again. Computer control stays off until the
owner turns it on in Settings, as after its own kill switch.

A state file that cannot be read is treated as STOPPED (fail closed): the owner
pressed Stop for a reason, and Resume is always one press away.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("friday.global_stop")

_LOCK = threading.RLock()
_HALTERS: Dict[int, Callable[[], Any]] = {}
_NEXT = [0]
_LOADED = [False]

#: Where a stop may come from. Anything else is recorded as "other".
SOURCES = ("button", "keyboard", "typed", "voice", "tool", "tray", "test", "other")

REFUSAL = ("[NOT RUN] Everything is stopped: the owner pressed Stop. Nothing new runs until "
           "they press Resume on Friday's own screen.")
SPOKEN_STOPPED = "Stopped everything. Press Resume on the screen when you want me back."
RESUMED_TEXT = ("Resumed. New and queued work can run again; what was stopped stays stopped, "
                "and computer control stays off until you turn it on in Settings.")

#: "stop everything", "stop it all", "halt all work", "emergency stop", "everybody stop".
#: Anchored at the start, so "don't stop everything" or "how do I stop everything" never match.
#: Matched against the words with every run of spaces and commas folded to one space
#: (``_fold``), so the pattern has no repeated whitespace class to backtrack over.
STOP_PHRASE = re.compile(
    r"^(?:(?:um+|uh+|ok|okay|hey|friday|please|now) )*"
    r"(?:(?:stop|halt|freeze|cancel) (?:everything|it all|all of it|all(?: the)?(?: (?:work|tasks|jobs))?)"
    r"(?: (?:now|right now|immediately))?"
    r"|emergency stop|everybody stop|stop all work)"
    r"(?: (?:friday|please|now))*[.!]*$",
    re.I)
_FOLD = re.compile(r"[\s,]+")
#: Longer than any stop phrase: anything this long is a sentence, not the command.
_MAX_STOP_WORDS_LEN = 80


def _fold(text: str) -> str:
    return _FOLD.sub(" ", text[:_MAX_STOP_WORDS_LEN + 1]).strip()


class EverythingStopped(RuntimeError):
    """Raised by a gate when work tries to start while everything is stopped."""

    def __init__(self, what: str = "") -> None:
        super().__init__(("Everything is stopped; %s did not run." % what) if what else
                         "Everything is stopped; nothing new runs until Resume.")


class ResumeHeld(RuntimeError):
    """Resume could not write its receipt, so nothing resumed."""


# ── state ─────────────────────────────────────────────────────────────────────

def _flag() -> threading.Event:
    import agent_friday.core as core
    return core.GLOBAL_STOP


def _path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "global_stop.json"


def _read_file() -> Dict[str, Any]:
    p = _path()
    if not p.exists():
        return {"stopped": False}
    try:
        raw = json.loads(p.read_text(encoding="utf-8") or "{}")
        if not isinstance(raw, dict) or not isinstance(raw.get("stopped"), bool):
            raise ValueError("not a stop record")
        return raw
    except Exception as exc:  # fail closed: an unreadable stop is still a stop
        _log.warning("stop state unreadable, treating as stopped: %s", exc)
        return {"stopped": True, "since": time.time(), "by": "unknown", "source": "other",
                "reason": "the saved stop state could not be read"}


def _write_file(st: Dict[str, Any]) -> Optional[str]:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, p)
        return None
    except Exception as exc:
        _log.error("could not save the stop state: %s", exc)
        return str(exc)[:200]


_STATE: Dict[str, Any] = {"stopped": False}


def load_on_boot() -> Dict[str, Any]:
    """Read the saved state once at start-up, before the scheduler or any worker starts."""
    with _LOCK:
        st = _read_file()
        _STATE.clear()
        _STATE.update(st)
        if st.get("stopped"):
            _flag().set()
        else:
            _flag().clear()
        _LOADED[0] = True
        if st.get("stopped"):
            _pause_background()
        return dict(_STATE)


def active() -> bool:
    """True while everything is stopped. Cheap: an Event read."""
    try:
        return _flag().is_set()
    except Exception:
        return False


def state() -> Dict[str, Any]:
    with _LOCK:
        st = dict(_STATE)
    st["stopped"] = active()
    return st


def raise_if_stopped(what: str = "") -> None:
    if active():
        raise EverythingStopped(what)


def refusal(what: str = "") -> str:
    return REFUSAL if not what else REFUSAL.replace("Nothing new runs", "%s did not run. Nothing new runs" % what)


def is_stop_everything(text: Any) -> bool:
    if not isinstance(text, str) or len(text.strip()) > _MAX_STOP_WORDS_LEN:
        return False
    return bool(STOP_PHRASE.match(_fold(text)))


def on_stop(fn: Callable[[], Any]) -> Callable[[], None]:
    """Register a halter (a live voice call's hang-up). Returns its unregister function.
    Registered while stopped, it runs at once."""
    with _LOCK:
        _NEXT[0] += 1
        key = _NEXT[0]
        _HALTERS[key] = fn
    if active():
        _call(fn)

    def _off() -> None:
        with _LOCK:
            _HALTERS.pop(key, None)
    return _off


def _call(fn: Callable[[], Any]) -> bool:
    try:
        fn()
        return True
    except Exception as exc:
        _log.warning("a halter failed: %s", exc)
        return False


# ── the lanes ─────────────────────────────────────────────────────────────────

def _halt_turns() -> int:
    import agent_friday.core as core
    with core._TURNS_LOCK:
        ids = list(core._TURNS.keys())
    return sum(1 for tid in ids if core.turn_request_stop(tid))


def _halt_tasks() -> int:
    from agent_friday.services import agent, task_journal as tj
    n = 0
    with agent.TASKS_LOCK:
        rows = [(tid, dict(t)) for tid, t in agent.TASKS.items()]
    for tid, t in rows:
        if tj.is_terminal(t.get("status")) or t.get("status") in ("deleted",):
            continue
        try:
            tj.request_stop(tid)
            tj.append(tid, "stop_requested", by="global_stop")
        except Exception as exc:
            _log.warning("task %s: stop not journalled: %s", tid, exc)
        with agent.TASKS_LOCK:
            if tid in agent.TASKS:
                agent.TASKS[tid]["stop_requested"] = True
        n += 1
    return n


def _halt_workers() -> int:
    from agent_friday.services import orchestrator as orch
    with orch._WORKERS_LOCK:
        ids = [wid for wid, e in orch._WORKERS.items()
               if str(getattr(getattr(e, "status", None), "value", getattr(e, "status", ""))).upper()
               in ("PENDING", "RUNNING")]
    o = orch.Orchestrator()
    return sum(1 for wid in ids if o.cancel_worker(wid))


def _halt_browser() -> int:
    from agent_friday.services import browser_session
    browser_session.stop_all_owned_sessions()
    return 1


def _halt_computer_control() -> int:
    from agent_friday.services import agent
    agent._CC_PERMISSION.clear()
    agent._CC_KILL.set()
    # Release modifier keys Friday may be holding down, so the owner's own keyboard works.
    try:
        if getattr(agent, "_HAS_PYAUTOGUI", False):
            for k in ("shift", "ctrl", "alt", "win", "command", "option"):
                try:
                    agent._pag.keyUp(k)
                except Exception:
                    pass
            try:
                agent._pag.mouseUp()
            except Exception:
                pass
    except Exception:
        pass
    return 1


def _pause_background() -> None:
    try:
        from agent_friday.services import turn_queue
        turn_queue.request_background_pause("global_stop", "everything is stopped")
    except Exception as exc:
        _log.warning("background pause not set: %s", exc)


def _halt_halters() -> int:
    with _LOCK:
        fns = list(_HALTERS.values())
    return sum(1 for fn in fns if _call(fn))


_LANES = (
    ("chat_turns", _halt_turns),
    ("tasks", _halt_tasks),
    ("workers", _halt_workers),
    ("browser", _halt_browser),
    ("voice_calls", _halt_halters),
    ("computer_control", _halt_computer_control),
    ("background", lambda: (_pause_background(), 1)[1]),
)


def _receipt(decision: str, *, by: str, source: str, reason: str = "",
             halted: Optional[Dict[str, Any]] = None) -> None:
    from agent_friday.governance import action_gate
    entry: Dict[str, Any] = {"tool": "global_stop", "class": "INTERNAL", "decision": decision,
                             "surface": source, "chosen_by": by}
    if reason:
        entry["reason"] = reason[:200]
    if halted is not None:
        entry["halted"] = halted
    action_gate._receipt(entry)


def _norm_source(source: str) -> str:
    s = str(source or "").strip().lower()
    return s if s in SOURCES else "other"


def stop(*, by: str = "owner", source: str = "button", reason: str = "") -> Dict[str, Any]:
    """Stop everything. Idempotent; never needs approval; never raises."""
    source = _norm_source(source)
    _flag().set()                       # first: every gate sees it before any lane is walked
    now = time.time()
    with _LOCK:
        was = bool(_STATE.get("stopped"))
        if not was:
            _STATE.clear()
            _STATE.update({"stopped": True, "since": now, "by": str(by or "owner")[:40],
                           "source": source, "reason": str(reason or "")[:200]})
        st = dict(_STATE)
        save_error = _write_file(st)
    halted: Dict[str, Any] = {}
    for name, fn in _LANES:
        try:
            halted[name] = fn()
        except Exception as exc:
            _log.warning("stop: the %s lane failed: %s", name, exc)
            halted[name] = "error"
    receipt_error = None
    try:
        _receipt("stop" if not was else "stop_again", by=str(by or "owner"), source=source,
                 reason=reason, halted=halted)
    except Exception as exc:
        receipt_error = str(exc)[:200] or type(exc).__name__
        _log.error("stop receipt not written: %s", receipt_error)
    _log.warning("EVERYTHING STOPPED (by=%s, source=%s): %s", by, source, halted)
    out = dict(st, stopped=True, halted=halted, receipt=receipt_error is None,
               already=was, text=SPOKEN_STOPPED)
    if receipt_error:
        out["receipt_error"] = receipt_error
    if save_error:
        out["save_error"] = save_error
    return out


def resume(*, by: str = "owner", source: str = "button") -> Dict[str, Any]:
    """Let work run again. The caller has checked the owner's press. Receipt first:
    if it cannot be written nothing resumes (ResumeHeld)."""
    source = _norm_source(source)
    if not active():
        return dict(state(), stopped=False, already=True)
    try:
        _receipt("resume", by=str(by or "owner"), source=source)
    except Exception as exc:
        raise ResumeHeld("the resume receipt could not be written, so nothing resumed: %s"
                         % (str(exc)[:160] or type(exc).__name__)) from exc
    with _LOCK:
        prev = dict(_STATE)
        _STATE.clear()
        _STATE.update({"stopped": False, "resumed_at": time.time(), "resumed_by": str(by)[:40],
                       "stopped_since": prev.get("since")})
        save_error = _write_file(dict(_STATE))
        if save_error:
            # Saved state still says stopped: a restart would come back stopped. Say so.
            _log.error("resume not saved: %s", save_error)
        _flag().clear()
    try:
        from agent_friday.services import turn_queue
        turn_queue.clear_background_pause("global_stop")
    except Exception:
        pass
    out = dict(state(), stopped=False, text=RESUMED_TEXT)
    if save_error:
        out["save_error"] = save_error
    return out


def _reset_for_tests() -> None:
    with _LOCK:
        _STATE.clear()
        _STATE.update({"stopped": False})
        _HALTERS.clear()
    try:
        _flag().clear()
    except Exception:
        pass
    try:
        from agent_friday.services import turn_queue
        turn_queue.clear_background_pause("global_stop")
    except Exception:
        pass
