"""When Friday may interrupt you: the budget, quiet hours and Focus Assist.

The census (notifications_census_2026-10-10) found about 21 interrupting events a day and 4.9 per
active hour, a third of them between 22:00 and 02:00. This module is the one place that decides
whether something that needs you may INTERRUPT (pop a card, speak, send a text) or must wait
silently in the Needs-you badge. It never decides whether the item exists: an approval is always
there to be answered, and nothing here approves, demotes, mutes or drops one.

The rules (addendum NA-2, decisions NA-D1, NA-D3, NA-D4):

* **Safety-critical kinds always interrupt** (services/notice_registry.SAFETY_CRITICAL_KINDS: spend
  halt, credential problem, budget alert, seat change, a cLaw block, security). They are exempt
  from the budget, from quiet hours and from Focus Assist, and they do not use up the budget.
* **Quiet hours** (off by default; 22:00 to 07:00 is the recommended window when you turn them on)
  and the one-shot "quiet until morning": only safety-critical items interrupt. Everything else
  waits in the badge and in Updates. A card held this way keeps its own time exactly (a hold never adds, moves or ends an expiry; one
  held for a day is released quietly into Needs you), and the text-message offer goes out when the
  hold ends (``approvals.release_held``).
* **Windows Focus Assist** is honoured only when you chose it (`honour_focus_assist`, off by
  default).
* **An interruption budget** (`interruption_budget_per_hour`, 6 by default, 0 means no limit).
  Over budget, needs-you items queue silently in the badge and are summarised ("4 approvals
  waiting").
* **Coalescing**: a burst of the same kind from the same source within 5 minutes is one card with a
  count (the engine asks `coalesces`).
* **Background jobs that would create approvals are deferred** while quiet (`defer_job`), so you
  do not wake up to nine cards.

A verdict is remembered per key for the hour, so the tray card and the popup for one approval
count once and agree. State is counts and timestamps only; it stays on this PC.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("friday.interruptions")

DEFAULT_BUDGET = 6
#: The choices Settings offers. 6 is the recommended one; nothing is preselected for you.
BUDGET_CHOICES = (3, 6, 12, 0)
RECOMMENDED_BUDGET = 6
RECOMMENDED_QUIET = ("22:00", "07:00")
WINDOW_S = 3600.0
COALESCE_S = 300.0

# Why an item did not interrupt.
DELIVERED, SAFETY, QUIET_HOURS, FOCUS, BUDGET = "delivered", "safety", "quiet_hours", "focus", "budget"

#: Job kinds that can raise approvals while they run (services/visible_jobs.TASK_KINDS names, and
#: the scheduler's agent-prompt and workflow runs). In quiet hours they wait for the morning.
APPROVAL_CREATING_JOBS = frozenset({
    "task.agent", "schedule.agent", "schedule.workflow", "workflows.draft",
    "workflows.goal_milestone", "workflows.queue_drain", "code.plan", "crew.talk",
    "channels.turn", "chat.side_work"})

_LOCK = threading.RLock()
_MEM: Dict[str, Any] = {"events": [], "seen": {}}
_PATH_OVERRIDE: List[Any] = [None]
_FOCUS_PROBE: List[Optional[Callable[[], bool]]] = [None]


@dataclass(frozen=True)
class Verdict:
    interrupt: bool
    reason: str                 # delivered | safety | quiet_hours | focus | budget
    counted: bool = False       # used one of the hour's interruptions

    def as_dict(self) -> dict:
        return {"interrupt": self.interrupt, "reason": self.reason, "counted": self.counted}


# ── settings ─────────────────────────────────────────────────────────────────

def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def budget(s: Optional[dict] = None) -> int:
    """Interrupting events allowed per hour; 0 means no limit."""
    s = _settings() if s is None else s
    v = s.get("interruption_budget_per_hour", DEFAULT_BUDGET)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        return DEFAULT_BUDGET
    return v


def parse_hm(text: Any, fallback: str) -> Tuple[int, int]:
    try:
        h, m = str(text or fallback).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return parse_hm(fallback, "00:00") if text is not None and fallback != "00:00" else (0, 0)


def in_window(now: datetime, start: Tuple[int, int], end: Tuple[int, int]) -> bool:
    """Is ``now`` inside [start, end)? A window that ends before it starts crosses midnight."""
    a, b, t = start[0] * 60 + start[1], end[0] * 60 + end[1], now.hour * 60 + now.minute
    if a == b:
        return False
    return a <= t < b if a < b else (t >= a or t < b)


def next_end(now: datetime, end: Tuple[int, int]) -> datetime:
    """The next moment the window's end time comes round."""
    at = now.replace(hour=end[0], minute=end[1], second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


def quiet_state(now: Optional[float] = None, s: Optional[dict] = None) -> dict:
    """{active, why, until}: is the owner's quiet on right now, and until when."""
    s = _settings() if s is None else s
    now = time.time() if now is None else now
    until = float(s.get("quiet_until_ts") or 0)
    if until > now:
        return {"active": True, "why": "until_morning", "until": until}
    if s.get("quiet_hours_enabled"):
        start = parse_hm(s.get("quiet_hours_start"), RECOMMENDED_QUIET[0])
        end = parse_hm(s.get("quiet_hours_end"), RECOMMENDED_QUIET[1])
        dt = datetime.fromtimestamp(now)
        if in_window(dt, start, end):
            return {"active": True, "why": "quiet_hours", "until": next_end(dt, end).timestamp()}
    return {"active": False, "why": "", "until": 0.0}


def until_morning(now: Optional[float] = None, s: Optional[dict] = None) -> float:
    """When "quiet until morning" ends: the next quiet-hours end if you set one, else 07:00."""
    s = _settings() if s is None else s
    end = parse_hm(s.get("quiet_hours_end"), RECOMMENDED_QUIET[1]) if s.get("quiet_hours_enabled") \
        else parse_hm(RECOMMENDED_QUIET[1], "07:00")
    dt = datetime.fromtimestamp(time.time() if now is None else now)
    return next_end(dt, end).timestamp()


# ── Windows Focus Assist (only when the owner chose it) ──────────────────────

def set_focus_probe(fn: Optional[Callable[[], bool]]) -> None:
    """Tests: stand in for the Windows query."""
    _FOCUS_PROBE[0] = fn


def _windows_quiet_time() -> bool:
    """True when Windows says Do Not Disturb / Focus Assist is on (QUNS_QUIET_TIME)."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
        return state.value == 6
    except Exception:
        return False


def focus_on(s: Optional[dict] = None) -> bool:
    s = _settings() if s is None else s
    if not s.get("honour_focus_assist"):
        return False                       # off until the owner chooses it
    try:
        return bool((_FOCUS_PROBE[0] or _windows_quiet_time)())
    except Exception:
        return False


# ── the hour's count ─────────────────────────────────────────────────────────

def _k(key: Any) -> str:
    """What an item is remembered under: a hash, so the state file holds no id, title or dedupe key.
    The digest is a short lookup key over Friday's own task ids and dedupe keys; no signature,
    password or secret depends on it, so SHA-1 is declared not used for security."""
    return hashlib.sha1(str(key).encode("utf-8"), usedforsecurity=False).hexdigest()[:16] if key else ""


def set_state_path(path: Any) -> None:
    """Tests: keep the state file in a scratch folder."""
    _PATH_OVERRIDE[0] = path
    with _LOCK:
        _MEM["events"], _MEM["seen"], _MEM["seq"] = [], {}, 0


def _path():
    if _PATH_OVERRIDE[0] is not None:
        return _PATH_OVERRIDE[0]
    from agent_friday.paths import friday_home
    return friday_home() / "interruptions.json"


def _load() -> None:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _MEM["events"] = [e for e in data.get("events", []) if isinstance(e, list) and len(e) == 2]
            _MEM["seen"] = {k: v for k, v in (data.get("seen") or {}).items() if isinstance(v, list)}
            _MEM["seq"] = int(data.get("seq") or 0)
    except Exception:
        pass


def _save() -> None:
    try:
        _path().write_text(json.dumps({"events": _MEM["events"], "seen": _MEM["seen"],
                                       "seq": int(_MEM.get("seq") or 0)}),
                           encoding="utf-8")
    except Exception:
        _log.debug("interruption state not saved", exc_info=True)


def _prune(now: float) -> None:
    _MEM["events"] = [e for e in _MEM["events"] if now - float(e[0]) < WINDOW_S]
    _MEM["seen"] = {k: v for k, v in _MEM["seen"].items() if now - float(v[0]) < WINDOW_S}


def used_this_hour(now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    with _LOCK:
        if not _MEM["events"] and not _MEM["seen"]:
            _load()
        _prune(now)
        return len(_MEM["events"])


# ── the verdict ──────────────────────────────────────────────────────────────

def verdict(kind: str = "", *, source: str = "", emitter: Optional[str] = None,
            key: Optional[str] = None, now: Optional[float] = None,
            settings: Optional[dict] = None) -> Verdict:
    """May this needs-you item interrupt now? Safety-critical: always. Otherwise quiet hours, then
    Focus Assist (if chosen), then the hour's budget. ``key`` makes repeats of one item (the tray
    card and the popup of one approval) count once and agree."""
    from agent_friday.services import notice_registry as _reg
    s = _settings() if settings is None else settings
    now = time.time() if now is None else now
    key = _k(key)
    if _reg.is_safety_critical(kind, emitter, source):
        return Verdict(True, SAFETY)
    with _LOCK:
        if not _MEM["events"] and not _MEM["seen"]:
            _load()
        _prune(now)
        if key and key in _MEM["seen"]:
            ts, interrupt, reason = _MEM["seen"][key][:3]
            return Verdict(bool(interrupt), str(reason), False)
        if quiet_state(now, s)["active"]:
            v = Verdict(False, QUIET_HOURS)
        elif focus_on(s):
            v = Verdict(False, FOCUS)
        else:
            cap = budget(s)
            v = Verdict(False, BUDGET) if cap and len(_MEM["events"]) >= cap \
                else Verdict(True, DELIVERED, True)
        if v.counted:
            _MEM["events"].append([now, key or ""])
        if key:
            _MEM["seen"][key] = [now, v.interrupt, v.reason]
        if v.counted or key:
            _save()
        return v


def _next_seq() -> int:
    """A number that is new for every quiet activation, kept in the state file, so each one has its own
    notice key (a dismissed notice never hides the next) without a clock in the key."""
    with _LOCK:
        if not _MEM["events"] and not _MEM["seen"] and not _MEM.get("seq"):
            _load()
        _MEM["seq"] = int(_MEM.get("seq") or 0) + 1
        _save()
        return _MEM["seq"]


#: Quiet started by voice, chat or a tool lasts at most this long, whatever the clock says.
QUIET_CAP_S = 12 * 3600


def turn_on_quiet(surface: str, now: Optional[float] = None) -> dict:
    """Turn on "quiet until morning" and leave the record of it: a notice in Updates that never merges
    with another ("Quiet until 07:00, turned on by you (voice)") and an activity-ledger line. A window
    started from voice, chat or a tool is capped at 12 hours; the page's own button is not."""
    from agent_friday.core import _save_settings
    now = time.time() if now is None else now
    until = until_morning(now)
    capped = surface != "page" and until - now > QUIET_CAP_S
    if capped:
        until = now + QUIET_CAP_S
    _save_settings({"quiet_until_ts": until})
    clock = datetime.fromtimestamp(until).strftime("%H:%M")
    seq = _next_seq()
    try:
        from agent_friday import notifications_engine as _ne
        _ne.push(title="Quiet until %s, turned on by you (%s)" % (clock, surface),
                 body="Only safety-critical things interrupt until then. End it from the Needs you tab.",
                 priority="low", source="interruptions", kind="quiet_on", tier="fyi", emitter="NQ1",
                 dedupe_key="quiet-on:%s:%d" % (surface, seq), meta={"no_coalesce": True})
    except Exception:
        _log.debug("quiet notice failed", exc_info=True)
    try:
        from agent_friday.services import activity_ledger
        activity_ledger.record("quiet_on", surface=str(surface)[:12], until_ts=int(until), capped=bool(capped))
    except Exception:
        _log.debug("quiet ledger line failed", exc_info=True)
    return {"until": until, "capped": capped, "surface": surface}


def hold_reason_now(now: Optional[float] = None, settings: Optional[dict] = None) -> str:
    """Why nothing needing the owner may interrupt right now, from the clock alone ("quiet",
    "focus"), or "" when only the hour's budget could still say no. Reads no state file."""
    s = _settings() if settings is None else settings
    if quiet_state(now, s)["active"]:
        return QUIET_HOURS
    return FOCUS if focus_on(s) else ""


def budget_full(now: Optional[float] = None, settings: Optional[dict] = None) -> bool:
    """Is the hour's interruption budget used up right now? A read: it records nothing and charges
    nothing, so a held card can be left alone without a verdict being rewritten every minute."""
    s = _settings() if settings is None else settings
    cap = budget(s)
    return bool(cap) and used_this_hour(now) >= cap


def held(key: str, now: Optional[float] = None) -> bool:
    """Was this item judged "may not interrupt" within the hour? (A page that connects later lists it
    without popping it open.) False for an item with no verdict."""
    now = time.time() if now is None else now
    with _LOCK:
        if not _MEM["events"] and not _MEM["seen"]:
            _load()
        _prune(now)
        v = _MEM["seen"].get(_k(key))
        return bool(v) and not v[1]


def forget(key: str) -> None:
    """A decided or expired item no longer holds a verdict."""
    with _LOCK:
        _MEM["seen"].pop(_k(key), None)
        _save()


# ── coalescing ───────────────────────────────────────────────────────────────

def coalesces(prev_at: float, now: Optional[float] = None) -> bool:
    """Is the earlier card of the same kind and source recent enough to take this one in?"""
    now = time.time() if now is None else now
    return 0 <= now - float(prev_at or 0) <= COALESCE_S


# ── background jobs that would create approvals ──────────────────────────────

def creates_approvals(kind: str = "", *, flag: Optional[bool] = None) -> bool:
    return bool(flag) or str(kind or "") in APPROVAL_CREATING_JOBS


def defer_job(kind: str = "", *, creates: Optional[bool] = None, now: Optional[float] = None,
              settings: Optional[dict] = None) -> dict:
    """{defer, reason, until}: should a background job that would raise approvals wait? In quiet
    hours (or "quiet until morning", or Focus Assist when chosen) it waits until the quiet ends. A
    job you started yourself is never passed here."""
    if not creates_approvals(kind, flag=creates):
        return {"defer": False, "reason": "", "until": 0.0}
    s = _settings() if settings is None else settings
    q = quiet_state(now, s)
    if q["active"]:
        return {"defer": True, "reason": QUIET_HOURS, "until": q["until"]}
    if focus_on(s):
        return {"defer": True, "reason": FOCUS, "until": 0.0}
    return {"defer": False, "reason": "", "until": 0.0}


def schedule_creates_approvals(rec: dict) -> bool:
    """A scheduled run that is an agent prompt or a workflow can raise approvals; a built-in
    routine (the briefing, the front page) does not."""
    task = (rec or {}).get("task") or {}
    return bool(task.get("creates_approvals")) or task.get("kind", "builtin") != "builtin"


# ── what to say about it ─────────────────────────────────────────────────────

def waiting_phrase(approvals: int = 0, other: int = 0) -> str:
    """"4 approvals waiting" / "1 approval and 2 other things waiting" / "" when nothing waits."""
    parts = []
    if approvals:
        parts.append("%d approval%s" % (approvals, "" if approvals == 1 else "s"))
    if other:
        parts.append("%d other thing%s" % (other, "" if other == 1 else "s")
                     if approvals else "%d thing%s" % (other, "" if other == 1 else "s"))
    return (" and ".join(parts) + " waiting") if parts else ""


def status(now: Optional[float] = None) -> dict:
    """What the Settings rows and the lane show."""
    s = _settings()
    q = quiet_state(now, s)
    return {"budget": budget(s), "budget_choices": list(BUDGET_CHOICES),
            "recommended_budget": RECOMMENDED_BUDGET, "used_this_hour": used_this_hour(now),
            "quiet_hours_enabled": bool(s.get("quiet_hours_enabled")),
            "quiet_hours_start": str(s.get("quiet_hours_start") or RECOMMENDED_QUIET[0]),
            "quiet_hours_end": str(s.get("quiet_hours_end") or RECOMMENDED_QUIET[1]),
            "recommended_quiet": list(RECOMMENDED_QUIET),
            "honour_focus_assist": bool(s.get("honour_focus_assist")),
            "quiet": q}
