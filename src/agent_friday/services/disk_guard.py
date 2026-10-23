"""services/disk_guard.py -- a full disk pauses background work, visibly (North Star 32.13).

"Friday stops nonessential writes, pauses background work, preserves receipts where possible,
warns the owner, and offers cleanup of caches and model files. It does not delete personal data
automatically."

The scheduler asks `holds(rec)` before it starts a due job. Below LOW_MIB free on the drive
that holds Friday's data the guard is "low": every scheduled job waits except the few that
keep the owner safe or free space (ESSENTIAL_JOBS), and a job that waits is not marked, so it
runs on the first tick after space comes back. It stays low until RESUME_MIB is free again, so
it does not flap. Entering low raises one card in Needs you (emitter DF1) saying what paused and
how to free space; leaving it closes the card. Receipts and conversations keep writing; nothing
is deleted by this module, ever. A free-space reading that fails is "unknown" and pauses
nothing (a failed check never stops Friday's work).

`status()` is the Doctor's line.
"""
from __future__ import annotations

import shutil
import threading
import time
from typing import Any, Dict, Optional

#: Below this much free space (MiB) background work pauses.
LOW_MIB = 1024
#: It resumes once this much is free again.
RESUME_MIB = 2048
#: How often the drive is read (seconds).
CHECK_EVERY_S = 60.0
#: Scheduled jobs that keep running on a full disk: they keep the owner safe (approval cards
#: expire, reminders fire) or free space (log retention).
ESSENTIAL_JOBS = frozenset({"approvals_expiry_sweep", "calendar_reminders", "context_log_retention"})
#: The card's dedupe and resolve key.
CARD_KEY = "disk-full"

_lock = threading.Lock()
_state: Dict[str, Any] = {"low": False, "checked": 0.0, "free_mib": None, "since": None, "carded": False,
                          "card_busy": False}


def _data_dir():
    from agent_friday import paths
    return paths.friday_home()


def _free_mib() -> Optional[float]:
    try:
        d = _data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(str(d)).free / (1024 * 1024)
    except Exception:
        return None


def _words(free_mib: float) -> str:
    return ("Only %d MB is free on the drive with Friday's data. Scheduled background work is "
            "paused so nothing is left half-written; your conversations and receipts still save, "
            "and reminders still fire. To free space, remove model files you no longer use "
            "(Settings > Models) or empty your downloads and caches. Friday deletes nothing of "
            "yours by itself, and the paused work runs once space is back." % max(0, int(free_mib)))


def _card(free: float) -> bool:
    """Raise the card; True only once it is saved (the store swallows a failed save, so it is
    read back). False -> it is tried again at the next reading of the drive."""
    try:
        from agent_friday import notifications_engine as ne
        ne.push(title="Friday's drive is almost full", body=_words(free), priority="high",
                source="disk", kind="disk_full", emitter="DF1", dedupe_key=CARD_KEY,
                resolve_key=CARD_KEY, target={"type": "url", "url": "/doctor"})
        return any(isinstance(n, dict) and n.get("dedupe_key") == CARD_KEY and not n.get("dismissed")
                   for n in ne._load())
    except Exception:
        return False


#: Tests set this to raise the card on the calling thread.
SYNC_CARD = False


def _raise_card(free: float) -> None:
    """On its own thread: the scheduler's tick never waits on the card (it may text the owner)."""
    def run():
        ok = _card(free)
        with _lock:
            _state["card_busy"] = False
            if ok and _state["low"]:
                _state["carded"] = True
    if SYNC_CARD:
        run()
    else:
        threading.Thread(target=run, name="disk-guard-card", daemon=True).start()


#: The holder name in the Local AI queue's background pause (services/turn_queue).
QUEUE_HOLDER = "disk-full"


def _enter(free: float) -> None:
    try:
        # The Local AI queue's background work gives way too: no background ticket is admitted
        # and a running background job waits at its next checkpoint (services/turn_queue).
        from agent_friday.services import turn_queue
        turn_queue.request_background_pause(QUEUE_HOLDER, "the drive with Friday's data is almost full")
    except Exception:
        pass
    try:
        from agent_friday.services import activity_ledger
        activity_ledger.record("disk_full", free_mib=int(free), state="paused")
    except Exception:
        pass


def _leave(free: float) -> None:
    try:
        from agent_friday.services import turn_queue
        turn_queue.clear_background_pause(QUEUE_HOLDER)
    except Exception:
        pass
    try:
        from agent_friday import notifications_engine as ne
        ne.resolve(CARD_KEY)
    except Exception:
        pass
    try:
        from agent_friday.services import activity_ledger
        activity_ledger.record("disk_full", free_mib=int(free), state="resumed")
    except Exception:
        pass


def check(*, force: bool = False, now: Optional[float] = None) -> Dict[str, Any]:
    """Read the drive (at most once a minute unless forced) and move between ok and low."""
    now = time.time() if now is None else now
    entered = left = card_due = None
    with _lock:
        read = force or now - _state["checked"] >= CHECK_EVERY_S
        if read:
            free = _free_mib()
            _state["checked"] = now
            _state["free_mib"] = free
            if free is not None:
                if not _state["low"] and free < LOW_MIB:
                    _state["low"], _state["since"], _state["carded"] = True, now, False
                    entered = free
                elif _state["low"] and free >= RESUME_MIB:
                    _state["low"], _state["since"], _state["carded"] = False, None, False
                    left = free
            # The card is tried when the drive is read (not on every call), one try at a time.
            if _state["low"] and not _state["carded"] and not _state.get("card_busy"):
                card_due = _state["free_mib"]
                _state["card_busy"] = True
        snap = dict(_state)
    if entered is not None:
        _enter(entered)
    if card_due is not None:
        _raise_card(card_due)
    if left is not None:
        _leave(left)
    return snap


def paused() -> bool:
    return bool(check()["low"])


def job_ref(rec: Optional[dict]) -> str:
    """A built-in job's registered name (its record id is 'sch_<ref>'); any other job's id."""
    rec = rec or {}
    task = rec.get("task") if isinstance(rec.get("task"), dict) else {}
    if task.get("kind") == "builtin" and task.get("ref"):
        return str(task["ref"])
    sid = str(rec.get("id") or "")
    return sid[4:] if sid.startswith("sch_") else sid


def holds(rec: Optional[dict]) -> bool:
    """True when this scheduled job must wait for disk space."""
    if not paused():
        return False
    return job_ref(rec) not in ESSENTIAL_JOBS


def status() -> Dict[str, Any]:
    """The Doctor's line: free space, the threshold, and what is paused."""
    s = check()
    free = s.get("free_mib")
    from agent_friday.services import failure_state
    return {
        "state": "unknown" if free is None else ("low" if s["low"] else "ok"),
        "free_mib": None if free is None else int(free),
        "low_mib": LOW_MIB, "resume_mib": RESUME_MIB, "since": s.get("since"),
        "words": failure_state.WORDS[failure_state.DISK_FULL] if s["low"] else "",
        "keeps_running": sorted(ESSENTIAL_JOBS),
    }
