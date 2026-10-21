"""Goal states, typed blockers, continuation checks and the no-progress
breaker (north star §17.3, §17.8, §17.9, §17.10; burn-down BD-12 slice G2).

services/goals.py owns the goal files and the milestone engine. This module
owns the rules those obey, so each rule is stated once:

* **States (§17.3).** A goal is proposed (draft), approved, one of the four
  live states, paused, failed, met (verified or unverified), abandoned or
  archived. The live state (active, waiting, needs owner, at risk) is not
  chosen by anyone: it is read off the milestones, the deadline and the
  budget every time the goal is written (`live_state`). Only the
  verification engine sets "met, verified"; the owner's own "this is done"
  is "met, unverified" and says so (`owner_judgment`).
* **Typed blockers (§17.8).** Every milestone that is not moving carries a
  blocker of one of the nine kinds, with plain words for the owner. Only
  `goal_not_met_yet` lets work continue on its own.
* **Continuation (§17.9).** Friday starts the next piece of work on her own
  only when every precondition holds (`continuation_hold` says which one
  does not, as a typed blocker).
* **No progress (§17.10).** An attempt that produces the same result as an
  earlier one, the same critique, or the same blocker again, stops the
  milestone with a `no_progress` blocker that says what was tried, instead of
  trying again or failing generically.

Pure functions over goal dicts; no I/O.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

# ── States ────────────────────────────────────────────────────────────────

LIVE = ("active", "waiting", "needs_owner", "at_risk")
MET = ("met_verified", "met_unverified")
STATUSES = ("proposed", "approved") + LIVE + ("paused", "failed") + MET + ("abandoned", "archived")
#: The engine never works on these (the owner reopens to go on).
TERMINAL = MET + ("abandoned", "archived")

#: Names older goal files and callers use.
LEGACY = {"draft": "proposed", "cancelled": "abandoned"}

#: Moves the owner (or the routes) may make. Live states move among
#: themselves on their own (`live_state`); a move into any live state is
#: written as "active" and then refined.
_ALLOWED: Dict[str, set] = {
    "proposed": {"approved", "abandoned"},
    "approved": {"active", "abandoned"},
    "paused": {"active", "abandoned"},
    "failed": {"active", "abandoned", "archived"},
    "met_verified": {"archived", "active"},
    "met_unverified": {"archived", "active"},
    "abandoned": {"archived", "active"},
    "archived": {"active"},
}
for _s in LIVE:
    _ALLOWED[_s] = {"paused", "abandoned", "failed", "met_unverified"}

STATE_WORDS = {
    "proposed": "proposed, not started", "approved": "approved, not started",
    "active": "active", "waiting": "waiting on an answer or an outside event",
    "needs_owner": "needs you", "at_risk": "at risk", "paused": "paused",
    "failed": "stopped after a failure", "met_verified": "met, and the evidence checks out",
    "met_unverified": "marked met without automatic proof", "abandoned": "ended by you",
    "archived": "archived",
}


def normalize(goal: Dict[str, Any]) -> str:
    """The goal's status in today's names. A legacy "completed" goal is met:
    verified when every milestone passed its own check with no override,
    otherwise unverified (it says what it rests on)."""
    s = str(goal.get("status") or "proposed")
    if s == "completed":
        return "met_verified" if _all_verified(goal) else "met_unverified"
    s = LEGACY.get(s, s)
    return s if s in STATUSES else "proposed"


def _all_verified(goal: Dict[str, Any]) -> bool:
    ms = [m for m in goal.get("milestones") or [] if isinstance(m, dict)]
    if not ms:
        return False
    for m in ms:
        v = m.get("verification") or {}
        # Only a check that actually ran and scored counts: a skipped check
        # (no output, no model reachable, an unreadable score) passes the
        # milestone but proves nothing, so the goal is met, unverified.
        if m.get("status") != "done" or not v.get("passed") or v.get("human_override") \
                or v.get("status") != "ok" or v.get("score") is None:
            return False
    return True


def target(goal: Dict[str, Any], requested: str) -> str:
    """Where an owner's requested move lands. "completed" is the owner's own
    judgment (met, unverified); "met_verified" is refused here."""
    r = str(requested or "")
    if r == "completed":
        return "met_unverified"
    r = LEGACY.get(r, r)
    if r in LIVE:
        return "active"
    return r


#: The owner's own moves (§17.13), in the words the Goals card uses.
OWNER_MOVES = (
    ("approved", "Approve"), ("paused", "Pause"), ("active", "Resume"),
    ("met_unverified", "Mark it met (my judgment)"),
    ("abandoned", "End this goal"), ("archived", "Archive"),
)


def owner_moves(current: str) -> List[Dict[str, str]]:
    """The moves the owner can make from `current`, labelled. Resume reads
    "Start" from an approved goal and "Reopen" from a finished one. Never
    met_verified: only the goal's own checks set that."""
    out = []
    for tgt, label in OWNER_MOVES:
        if tgt == current or tgt == "met_verified":
            continue
        if tgt == "active" and current in LIVE:
            continue
        if may_move(current, tgt):
            if tgt == "active" and current in TERMINAL + ("failed",):
                label = "Reopen"
            elif tgt == "active" and current == "approved":
                label = "Start"
            out.append({"to": tgt, "label": label})
    return out


def may_move(current: str, new: str) -> bool:
    if new == current:
        return True
    if new == "met_verified":
        return False                      # only verification sets it
    return new in _ALLOWED.get(current, set())


# ── Typed blockers ───────────────────────────────────────────────────────

BLOCKERS = ("needs_user_input", "external_wait", "run_failed", "missing_evidence",
            "goal_not_met_yet", "policy_blocked", "budget_exhausted", "no_progress",
            "conflict_detected")

#: Older blocker types (G1) and the category each belongs to.
_TYPE_CATEGORY = {"interrupted": "needs_user_input", "run_failed": "run_failed"}

#: Categories that put the goal in "needs owner" (the rest wait on the world).
_NEEDS_OWNER = {"needs_user_input", "run_failed", "missing_evidence", "policy_blocked",
                "budget_exhausted", "no_progress", "conflict_detected"}


def blocker(category: str, text: str, **extra) -> Dict[str, Any]:
    if category not in BLOCKERS:
        raise ValueError("unknown blocker category: %r" % category)
    out = {"type": extra.pop("type", category), "category": category, "text": text,
           "at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
    out.update(extra)
    return out


def category_of(milestone: Dict[str, Any], *, include_hold: bool = True) -> Optional[str]:
    """The §17.8 kind of what holds this milestone, or None when nothing does.
    A continuation hold (`hold`, recomputed every tick) counts unless asked
    not to: the hold is shown to the owner, but it is never its own cause."""
    if milestone.get("status") in ("done", "in_progress"):
        return None
    if include_hold and milestone.get("status") == "pending" \
            and (milestone.get("hold") or {}).get("category") in BLOCKERS:
        return milestone["hold"]["category"]
    b = milestone.get("blocker") or {}
    if b.get("category") in BLOCKERS:
        return b["category"]
    if b.get("type") in _TYPE_CATEGORY:
        return _TYPE_CATEGORY[b["type"]]
    status = milestone.get("status")
    if status == "failed":
        return "run_failed"
    if status == "escalated":
        return "missing_evidence"
    if status == "verifying" and milestone.get("approval_id"):
        return "external_wait"
    if status == "blocked":
        return "external_wait" if milestone.get("approval_id") else "needs_user_input"
    if status == "pending":
        return "goal_not_met_yet"
    return None


# ── The live state, read off the goal ───────────────────────────────────────

AT_RISK_HOURS = 48
AT_RISK_BUDGET = 0.8


def _ts(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def risk_reasons(goal: Dict[str, Any], now: Optional[float] = None) -> List[str]:
    now = now or time.time()
    out = []
    open_ms = [m for m in goal.get("milestones") or [] if m.get("status") != "done"]
    dl = _ts(goal.get("deadline"))
    if open_ms and dl is not None:
        if dl < now:
            out.append("its deadline has passed with work still open")
        elif dl - now < AT_RISK_HOURS * 3600:
            out.append("its deadline is less than two days away with work still open")
    for m in open_ms:
        due = _ts(m.get("due"))
        if due is not None and due < now - 86400:
            out.append(f"“{m.get('name')}” is more than a day late")
            break
    cap = int(goal.get("budget_cap_mψ") or 0)
    if cap and int(goal.get("spent_mψ") or 0) >= AT_RISK_BUDGET * cap:
        out.append("it has used most of its budget")
    return out


def live_state(goal: Dict[str, Any], now: Optional[float] = None) -> str:
    # Holds (recomputed every tick: "waits for the step before it", "no
    # expected result yet") are shown on the milestone but don't move the
    # goal's state, so it doesn't flip back and forth with each tick.
    cats = [category_of(m, include_hold=False) for m in goal.get("milestones") or []
            if isinstance(m, dict)]
    if any(c in _NEEDS_OWNER for c in cats):
        return "needs_owner"
    if any(c == "external_wait" for c in cats):
        return "waiting"
    if risk_reasons(goal, now):
        return "at_risk"
    return "active"


def refresh(goal: Dict[str, Any], now: Optional[float] = None) -> Optional[str]:
    """Bring a live goal's state up to date in place. Returns the old state
    when it changed (the caller writes the history line), else None."""
    cur = normalize(goal)
    if cur != goal.get("status"):
        goal["status"] = cur
    if cur not in LIVE:
        return None
    new = live_state(goal, now)
    if new != cur:
        goal["status"] = new
        return cur
    return None


# ── Continuation (§17.9) ──────────────────────────────────────────────────

def continuation_hold(goal: Dict[str, Any], milestone: Dict[str, Any], *,
                      now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """None when Friday may start this milestone's work on her own; otherwise
    the typed blocker that says which precondition does not hold."""
    name = milestone.get("name") or "this milestone"
    if normalize(goal) not in LIVE:
        return blocker("policy_blocked", f"The goal is {STATE_WORDS.get(normalize(goal), normalize(goal))}.")
    if category_of(milestone, include_hold=False) != "goal_not_met_yet":
        return blocker("needs_user_input", f"“{name}” is not ready to continue on its own.")
    for other in goal.get("milestones") or []:
        if other is milestone or other.get("milestone_id") == milestone.get("milestone_id"):
            continue
        st = other.get("status")
        if st == "in_progress" or (st == "verifying" and not other.get("approval_id")):
            return blocker("external_wait", f"“{other.get('name')}” is still running; "
                           "the next piece starts once its result is recorded.",
                           type="checkpoint")
        if category_of(other, include_hold=False) == "external_wait":
            return blocker("external_wait", f"An answer is still waiting on “{other.get('name')}”; "
                           "nothing more starts on its own until you answer it.",
                           type="approval_pending")
    deps = [d for d in milestone.get("depends_on") or [] if d]
    if deps:
        done = {m.get("milestone_id") for m in goal.get("milestones") or [] if m.get("status") == "done"}
        missing = [d for d in deps if d not in done]
        if missing:
            return blocker("external_wait", f"“{name}” waits for the steps it depends on.",
                           type="dependency", waiting_for=missing)
    cap = int(goal.get("budget_cap_mψ") or 0)
    est = int(milestone.get("cost_estimate_mψ") or 0)
    if cap and int(goal.get("spent_mψ") or 0) + est > cap:
        return blocker("budget_exhausted", f"Starting “{name}” would go over the goal's budget.")
    used_up = runtime_hold(goal)
    if used_up:
        return used_up
    criteria = (milestone.get("success_criteria") or goal.get("success_criteria") or "").strip()
    if not criteria:
        return blocker("needs_user_input",
                       f"I don't know what would prove “{name}” done, so I won't start it on my "
                       "own. Say what result you expect.", type="no_expected_evidence")
    return route_hold(goal)


def runtime_hold(goal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The runtime-budget blocker once a goal has used its minutes of work
    (§17.2). Checked on the tick and before every dispatch."""
    b = goal.get("budget") if isinstance(goal.get("budget"), dict) else {}
    try:
        minutes = int(b.get("runtime_minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    if minutes and float(goal.get("spent_runtime_s") or 0) >= minutes * 60:
        return blocker("budget_exhausted", f"This goal has used its {minutes} minutes of work.",
                       type="runtime")
    return None


ROUTE_MODES = ("local_only", "local_preferred", "cloud_preferred", "cloud_only")


def valid_routes(value: Any) -> Optional[list]:
    """A clean allowed_routes list, or None when the value is not one."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v in ROUTE_MODES for v in value):
        return None
    return list(dict.fromkeys(value))


def route_hold(goal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The route blocker when the current routing mode is not one this goal
    allows. Checked before every dispatch, not only on the scheduler's tick;
    an unreadable list or mode fails closed."""
    routes = goal.get("allowed_routes")
    if not routes:
        return None
    clean = valid_routes(routes)
    try:
        from agent_friday.services import cloud_mode_policy as cmp
        mode = cmp.current_mode()
    except Exception:
        mode = None
    if clean is None or mode not in clean:
        shown = ", ".join(clean) if clean else "a route I can't read"
        return blocker("policy_blocked", f"This goal may only run with {shown}; "
                       f"the current mode is {mode or 'unknown'}.", type="route")
    return None


def superseded(goal: Dict[str, Any], attempt_started_at: Optional[float]) -> bool:
    """True when the owner changed the goal's plan after this attempt began:
    a repair attempt then waits instead of continuing the old plan."""
    revised = _ts(goal.get("plan_revised_at"))
    return revised is not None and attempt_started_at is not None and revised > attempt_started_at


# ── No progress (§17.10) ─────────────────────────────────────────────────

#: Fingerprints of recent attempts kept on the milestone.
MARKS_KEPT = 5
#: The same blocker this many times in a row is no progress.
SAME_BLOCKER_LIMIT = 3


def fingerprint(text: Any) -> str:
    norm = " ".join(str(text or "").lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def repeated(marks: Iterable[Dict[str, Any]], *, output: str = "", critique: str = "") -> Optional[str]:
    """Why the newest attempt is no progress, or None. `marks` are earlier
    attempts' {"output", "critique"} fingerprints."""
    out_fp = fingerprint(output) if output else None
    cr_fp = fingerprint(critique) if critique else None
    for m in marks:
        if out_fp and m.get("output") == out_fp:
            return "it produced the same result as an earlier attempt"
        if cr_fp and m.get("critique") == cr_fp:
            return "its check found the same problem as last time"
    return None


def blocked_again(history: List[str], category: str) -> bool:
    """True when this milestone has stopped for the same reason
    SAME_BLOCKER_LIMIT times running."""
    run = history[-(SAME_BLOCKER_LIMIT - 1):] + [category]
    return len(run) >= SAME_BLOCKER_LIMIT and len(set(run)) == 1


def no_progress_words(name: str, attempts: int, why: str) -> str:
    return (f"I stopped working on “{name}” because {why} ({attempts} attempt"
            f"{'s' if attempts != 1 else ''} so far; what they did has happened). Trying again "
            "the same way would only repeat it. Change the plan or the criteria, or tell me "
            "to start it again.")
