"""Stand Friday down and give the machine back.

"I need my machine" in Settings > Models was a placebo. It POSTed to
/api/machine/level, which answered -- in a raw browser alert() -- "Noted, but
nothing enforces machine levels yet ... This click does not release or stand
anything down." The route said so itself: STUBBED, deliberately, because
headroom.md **D1** (working/away/yield) was undecided.

D1 is decided. "I need my machine" releases the machine:

  * local models are unloaded from the GPU. Laya is CPU-resident, so it stays;
  * every background and scheduled job is paused;
  * the header shows "Friday is stood down -- Resume";
  * interactive chat still works, on a cloud seat, with its usual visible model
    label -- or says it is waiting when cloud is off. Standing down is about the
    CARD, not about refusing to talk;
  * it lasts until Resume, or auto-resumes after a chosen number of hours;
  * background work never wakes the GPU while stood down.

TWO PROPERTIES THIS LIVES OR DIES ON.

State is PERSISTED, because a stand-down that forgets itself across a tray
restart hands the card straight back -- the opposite of what was asked for. And a
paused job SKIPS VISIBLY: the scheduler raises `StoodDown` and the run is
recorded as skipped with a reason, rather than quietly not happening, which is the
invisible-success defect this codebase keeps rediscovering.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.stand_down")

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {}

#: Set by tests to observe `_release_gpu` without touching a real card.
_TEST_CALLS: list = []


def _path() -> Path:
    # Resolved lazily so a redirected FRIDAY_DIR (tests, a scratch home) is
    # honoured no matter when this module was first imported.
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "stand_down.json"


def _invalidate() -> None:
    with _LOCK:
        _CACHE.clear()


def _read() -> Dict[str, Any]:
    with _LOCK:
        if _CACHE.get("state") is not None:
            return dict(_CACHE["state"])
    blank = {"active": False, "since": 0.0, "requested_by": "",
             "auto_resume_at": None}
    try:
        p = _path()
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8") or "{}")
            if isinstance(raw, dict):
                blank.update({k: raw.get(k, blank[k]) for k in blank})
    except Exception as exc:
        # A state file we cannot read must not be read as "stood down": that
        # would strand the machine with no way back through the UI.
        _log.warning("stand-down state unreadable, treating as working: %s", exc)
    with _LOCK:
        _CACHE["state"] = dict(blank)
    return dict(blank)


def _write(st: Dict[str, Any]) -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, sort_keys=True, indent=2),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception as exc:
        _log.error("could not persist stand-down state: %s", exc)
    with _LOCK:
        _CACHE["state"] = dict(st)


def _release_gpu() -> None:
    """Unload every local model Friday owns. Patched out in tests.

    Through the arbiter's own release: every seat it owns, spawned or adopted,
    is stopped. (This used to call `adopt_or_reap(set())` on the Arbiter, which
    has no such method -- it is the llama-server backend's -- so every
    stand-down logged an error and released nothing; and that method spares a
    seat a local provider routes to, which Friday's own seat is.) Laya is not on
    the card (CPU encoder), so it keeps working.
    """
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if arb is None:
            _log.info("stand-down: no arbiter in this process; nothing to release")
            return
        report = arb.release_gpu("stood down")
        _log.info("stand-down released the GPU: %s", report)
    except Exception as exc:
        # Report it; do NOT fail the stand-down. The user asked for the machine
        # and the pause of background work is the larger half of that.
        _log.error("stand-down could not release the GPU: %s", exc)


def _in_background(fn) -> None:
    """Loading a 27B takes a minute; nobody waits on it. Inline in tests."""
    threading.Thread(target=fn, daemon=True, name="friday-reclaim-gpu").start()


def _reclaim_gpu() -> None:
    """Give Friday its seats back (Resume, or the window ran out)."""
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if arb is None:
            return
        report = arb.reclaim_gpu()
        _log.info("resume reloaded the seats: %s", report)
    except Exception as exc:
        _log.error("resume could not reload the seats: %s", exc)


def _expired(st: Dict[str, Any]) -> bool:
    try:
        return bool(st.get("active") and st.get("auto_resume_at")
                    and float(st["auto_resume_at"]) <= time.time())
    except Exception:
        return False


def is_active_now() -> bool:
    """Stood down right now, with no side effects (the arbiter asks this
    under its lock; `state()` may reload seats when a window has expired)."""
    st = _read()
    return bool(st.get("active")) and not _expired(st)


def state() -> Dict[str, Any]:
    """Current state, with an expired auto-resume already applied (and the
    seats reloaded, as Resume would)."""
    st = _read()
    if _expired(st):
        st = {"active": False, "since": 0.0,
              "requested_by": st.get("requested_by") or "",
              "auto_resume_at": None}
        _write(st)
        _log.info("stand-down auto-resumed on its own window")
        _in_background(_reclaim_gpu)
    return st


def is_stood_down() -> bool:
    return bool(state().get("active"))


def stand_down(*, requested_by: str = "", hours: Optional[float] = None) -> Dict[str, Any]:
    """Release the machine. Idempotent.

    `hours=None` means "until Resume". Any number sets an auto-resume window.
    """
    now = time.time()
    auto = None
    if hours:
        try:
            auto = now + float(hours) * 3600.0
        except Exception:
            auto = None
    st = {"active": True, "since": now,
          "requested_by": (requested_by or "").strip(), "auto_resume_at": auto}
    _write(st)
    _release_gpu()
    _log.warning("Friday stood down at the user's request (by=%r, auto_resume=%s)",
                 requested_by, auto)
    return st


def resume(*, requested_by: str = "") -> Dict[str, Any]:
    st = {"active": False, "since": 0.0,
          "requested_by": (requested_by or "").strip(), "auto_resume_at": None}
    _write(st)
    _log.info("Friday resumed (by=%r)", requested_by)
    _in_background(_reclaim_gpu)
    return st


def blocks_interactive_chat() -> bool:
    """Always False. Standing down is about the CARD, not about going silent.

    A stand-down that also refused to talk would be a worse product than the
    placebo it replaces. Chat continues on a cloud seat with its usual visible
    label; if cloud is off, the chat path says it is waiting -- which is the
    honest answer, not silence.
    """
    return False


def may_use_local_gpu(*, interactive: bool = False) -> bool:
    """Whether anything may load a model onto the card right now.

    False for both background AND interactive work while stood down: the machine
    was handed to the user, and quietly reloading 20 GB because they typed a
    message is exactly what they asked to stop. Interactive turns route to a cloud
    seat instead, visibly.
    """
    if not is_stood_down():
        return True
    return False


def reason() -> str:
    """One sentence for a skip note or a notification."""
    st = state()
    if not st.get("active"):
        return ""
    since = st.get("since") or 0
    mins = int(max(0, time.time() - float(since)) // 60)
    tail = ""
    if st.get("auto_resume_at"):
        left = int(max(0, float(st["auto_resume_at"]) - time.time()) // 60)
        tail = " It resumes on its own in about %d min." % left
    return ("Friday is stood down — you asked for the machine %d min ago, so the "
            "GPU is released and background jobs are paused.%s" % (mins, tail))
