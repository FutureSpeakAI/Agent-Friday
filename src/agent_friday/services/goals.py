"""
Agent Friday — Durable Goals: State Machine, Verification & Human Gates
FutureSpeak.AI · Asimov's Mind

V6_WHOLENESS_SPEC.md §4 Phase 5 / AUTONOMY_SPEC.md §8 (A3). The goal-holding
brain: a persistent Goal entity that survives weeks and restarts, executes its
own milestones, VERIFIES its own output against stated success criteria,
notices when it's wrong and repairs (bounded), escalates to a human when
repair doesn't fix it, and never — under any circumstance — marks a milestone
done on a failed check.

Reuse contract (V6 §2 substrate table — harvest, don't rebuild)
-----------------------------------------------------------------
  * services/qa_gates.py     — evaluate_text() is the verification primitive;
                               milestone completion is judged against
                               success_criteria through the EXACT same
                               self-critique gate the creative pipeline uses.
  * services/approvals.py    — (A3, this phase) the human-gate queue. Every
                               Q3-gated milestone action, every failed-
                               verification escalation, and every manual-
                               verification-mode confirmation is an approval
                               card there. dissent_gate.check_dissent() is
                               attached to each one (via approvals.py).
  * services/work_log.py     — log_start/log_finish's own documented contract
                               ("task should be a WorkerTask, or any object
                               with .task_id, .prompt, etc.") is used directly
                               with a lightweight stand-in object so
                               goal_ancestry_json (a field work_log ALREADY
                               HAD, built for exactly this) carries goal_id/
                               milestone_id — see _log_work_entry.
  * services/budget_enforcer.py — reserve_budget/release_budget are called
                               for every milestone attempt (pseudo-workspace
                               "goal:<goal_id>") for cross-goal observability
                               in the existing Budgets panel. The AUTHORITATIVE
                               per-goal hard-stop, however, is this module's
                               OWN spent_mψ / budget_cap_mψ ledger on the goal
                               record itself — see "Budget model" below for
                               why budget_enforcer's calendar-month reset
                               can't be the sole enforcement point for a
                               goal that may span a month boundary.
  * services/agent.py         — _spawn_task/_task_snapshot is the DEFAULT
                               executor (mirrors how scheduler.py's own
                               "agent_prompt" schedules already bridge through
                               this exact primitive) — full agent tool loop,
                               not a bare completion. _default_executor passes
                               _spawn_task a services.subagents scope name
                               ("goal-milestone", a safe read/write/research
                               allow-list) UNLESS this exact dispatch was
                               classified hard by the Q3 gate AND explicitly
                               human-approved (see milestone["_hard_gate_
                               approved"], stamped in _run_milestone_locked) —
                               so an auto-approved (or mis-classified)
                               milestone can never reach an outward/spend/
                               irreversible/shell tool unsupervised, even
                               though its background task is otherwise
                               "authenticated" for ring-2 tool calls the same
                               way every spawned task is.
  * services/orchestrator.py  — studied; not used as the default executor
                               (its worker adapters are Ollama/script/http,
                               not the rich multi-tool agent loop a goal
                               doing "recurring autonomous work on the user's
                               real projects" needs) but the SAME reuse
                               instinct (a swappable executor, see
                               set_executor) lets a caller wire a milestone
                               to orchestrator.delegate() instead when a
                               lighter-weight worker call is more appropriate
                               — that path gets budget/work_log wiring for
                               free from orchestrator._run_worker itself.
  * services/scheduler.py     — register_builtin_task wires three jobs in
                               (added additively there): goal_milestones_tick
                               (interval — advances due milestones),
                               goals_weekly_review (weekly aggregation +
                               a rollover proposal for missed dates), and
                               approvals.expire_stale
                               (interval sweep).
  * services/interest_model.py — register_goals_provider() is called at
                               import time here (see bottom of file) so
                               dissent_gate sees active goals as part of
                               "what the user has signalled they want."
  * governance/proof_of_integrity.py + privacy/vault_crypto — receipt
                               signing follows dissent_gate.record_dissent_
                               event()'s EXACT pattern (HMAC via the
                               governance key, vault_crypto.sign_entry with a
                               manual-HMAC fallback) for consistency, kept as
                               its own small local copy (same "leaf module,
                               tiny parallel copy" convention dissent_gate.py
                               itself uses relative to behavioral_monitor).

Goal entity (V6 P5's exact schema, persisted as one JSON file per goal under
~/.friday/goals/<goal_id>.json — restart-survivable):
    {goal_id, title, description, owner, status, deadline,
     milestones: [{name, due, done_at, verification}], success_criteria,
     linked_schedules, linked_chains, budget_cap_mψ, approval_required,
     verification_mode, receipts: []}
(plus internal bookkeeping fields — spent_mψ, history, rollovers,
milestone.status/attempts/cost_estimate_mψ/approval_id/artifacts — additive
to the spec's minimum contract, the same way scheduler.py's own records carry
bookkeeping fields like _active_run_id beyond their documented shape.)

State machine
-------------
proposed -> approved -> active -> {paused, completed, failed, cancelled}
paused -> active | cancelled
failed -> active | cancelled   (a failed goal can be revived or retired)
completed / cancelled are terminal. Enforced by transition_goal(); an illegal
move raises ValueError, never silently no-ops (except the same-status case,
which is a true no-op).

Verify -> notice -> repair -> escalate (never silent completion)
------------------------------------------------------------------
run_milestone() executes one attempt-cycle: dispatch work -> qa_gates.
evaluate_text() against success_criteria -> on pass, done + signed receipt;
on fail, fold the critique into the next attempt (bounded by
max_repair_retries) -> if still failing after the bound, ESCALATE to a human
via an approvals.py card (kind="goal_milestone_escalation") and stop. The
milestone's status is "escalated", never "done" — see _accept_escalation for
the ONLY path that can mark it done afterward (an explicit human approval,
which also flags the resulting verification as human_override=True so it's
never confused with a real automated pass).

Budget model
------------
budget_enforcer.py's cap resets every calendar month (services/
budget_enforcer.py::_month_key) — correct for its designed purpose (per-
workspace monthly spend) but wrong as the SOLE enforcement point for "a
multi-week goal's total budget_cap_mψ must hard-stop," since a goal spanning
a month boundary would otherwise get a fresh month's allowance mid-flight.
So this module tracks its OWN cumulative goal["spent_mψ"] against
goal["budget_cap_mψ"] as the authoritative, duration-independent hard-stop
(see _reserve_goal_budget), while STILL calling budget_enforcer.reserve_
budget/release_budget for the pseudo-workspace "goal:<goal_id>" so the
existing Budgets panel/dashboard sees goal spend too — reuse for
observability, not as a substitute for the real cap.

_reserve_goal_budget is an ATOMIC check-and-commit: a fitting reservation is
added to goal["spent_mψ"] in the SAME read-modify-write cycle that checks it
against budget_cap_mψ (via _mutate_goal's lock), not a check now / debit
later two-step. This closes two related overspend paths a "check now, spend
whenever the work finishes" design would otherwise leave open (both were
audited findings against an earlier version of this module that only
reserved ONCE, before run_milestone's own repair loop, and only ever debited
spent_mψ at the very end via a since-removed _add_spend):
  1. WITHIN one milestone's own bounded verify->repair loop, run_milestone
     now calls _reserve_goal_budget again before EVERY attempt after the
     first (see _run_milestone_locked) — so a milestone that keeps failing
     verification cannot run its full max_repair_retries+1 attempts on a
     single up-front reservation and accumulate real cost past the cap
     before it's ever re-checked; a reservation that would exceed the cap
     aborts the loop straight to escalation instead of dispatching further.
  2. ACROSS two DIFFERENT milestones of the same goal running concurrently
     (e.g. the scheduler's goal_milestones_tick beat and an on-demand
     POST .../milestones/<id>/run racing each other), each _reserve_goal_
     budget call reads the CURRENT persisted spent_mψ (including whatever
     the other caller has already committed), not a stale snapshot from
     before either started — so two concurrent reservations against the
     same remaining headroom cannot both succeed.
_release_goal_budget is the mirror-image credit: once a milestone's real
cost is known, it subtracts back the unused delta from the (possibly
multi-attempt) sum of reservations actually committed, leaving spent_mψ
reflecting exactly the real cumulative cost — the same escrow pattern
budget_enforcer's own reserve_budget/release_budget already use.

Design rules (consistent with the rest of the codebase):
  * Only stdlib + core._load_settings + services.approvals + services.
    qa_gates imported at module level (both leaf-ish siblings); agent.py,
    work_log.py, budget_enforcer.py, interest_model.py, scheduler.py are
    lazy, in-function imports — defensive cross-service style matching
    dissent_gate.py/interest_model.py/persona_eval.py.
  * Never silently marks a milestone done on a failed verification. That is
    the one invariant this whole module exists to hold.
  * Atomic per-goal file writes (write-to-temp + replace) so a crash mid-
    write never corrupts a goal record; a coarse RLock serializes read-
    modify-write across all goal files (goals execute at low concurrency —
    a single scheduler tick or an occasional API call — so this is
    deliberately simple rather than a per-file lock table).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
import hashlib
import hmac as _hmac
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_friday.paths import contained, friday_home, safe_name
from agent_friday.services import approvals
from agent_friday.services import qa_gates
from agent_friday.user_errors import ExceptionText, UserFacingValueError, exception_text
from agent_friday.services.visible_jobs import visible_job

_log = logging.getLogger("friday.goals")

# Friday's state root. Resolved centrally so FRIDAY_HOME redirects this
# module along with everything else (see agent_friday/paths.py).
FRIDAY_DIR = friday_home()
GOALS_DIR = FRIDAY_DIR / "goals"
REVIEWS_DIR = GOALS_DIR / "reviews"
GOVERNANCE_DIR = FRIDAY_DIR / "governance"
RECEIPTS_LOG = GOVERNANCE_DIR / "goal_receipts.jsonl"

_LOCK = threading.RLock()
_RECEIPTS_LOCK = threading.Lock()

# ── State machine ────────────────────────────────────────────────────────────
STATUSES = ("proposed", "approved", "active", "paused", "completed", "failed", "cancelled")
TERMINAL_STATUSES = ("completed", "cancelled")

_ALLOWED_TRANSITIONS: Dict[str, set] = {
    "proposed":  {"approved", "cancelled"},
    "approved":  {"active", "cancelled"},
    "active":    {"paused", "completed", "failed", "cancelled"},
    "paused":    {"active", "cancelled"},
    "failed":    {"active", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}

MILESTONE_STATUSES = ("pending", "in_progress", "verifying", "escalated",
                      "blocked", "done", "failed")

DEFAULT_MAX_REPAIR_RETRIES = 2
DEFAULT_MILESTONE_COST_MΨ = 1000
DEFAULT_MILESTONE_TIMEOUT_SECONDS = 1800


# ═══════════════════════════════════════════════════════════════════════════
#  TIME HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_overdue(due_value: Any, *, now: Optional[float] = None) -> bool:
    due = _parse_ts(due_value)
    if due is None:
        return False
    return due < (now if now is not None else time.time())


# ═══════════════════════════════════════════════════════════════════════════
#  STORE — one JSON file per goal under ~/.friday/goals/
# ═══════════════════════════════════════════════════════════════════════════

#: The on-disk shape of a goal file. A file without the field is version 1
#: as well (every goal written before the field existed has this shape).
#: Fields this code does not know are kept on every rewrite, so a goal written
#: by a newer Friday survives a rollback to this one.
GOAL_SCHEMA_VERSION = 1

#: This process's identity. A milestone's run record names the process that
#: started it, so a run left by an earlier process (a restart, a crash, an
#: update) is told apart from one this process is still running.
BOOT_ID = uuid.uuid4().hex


def _goal_path(goal_id: str) -> Path:
    return contained(GOALS_DIR, safe_name("%s.json" % goal_id, what="goal id"))


def _backup_path(p: Path) -> Path:
    return p.with_name(p.name + ".bak")


def _load_goal(p: Path) -> Optional[Dict[str, Any]]:
    """One goal file, or None when it is missing, torn or not a goal."""
    try:
        g = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(g, dict) or not isinstance(g.get("goal_id"), str):
        return None
    return g


def _read_goal_file(goal_id: str) -> Optional[Dict[str, Any]]:
    """The goal, from its file or, when that is torn or gone, from the copy
    kept beside it at the previous write. A crash in the middle of a write
    loses at most that one write, never the goal."""
    try:
        p = _goal_path(goal_id)
    except ValueError:
        return None
    g = _load_goal(p) if p.exists() else None
    if g is not None:
        return g
    bak = _backup_path(p)
    g = _load_goal(bak) if bak.exists() else None
    if g is not None:
        _log.warning("goal file %s was unreadable; using its last good copy", goal_id)
        return g
    if p.exists():
        _log.error("goal file %s unreadable/corrupt and no good copy beside it", goal_id)
    return None


def _fsync_write(path: Path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _write_goal_file(goal: Dict[str, Any]) -> None:
    """Write-to-temp, fsync, then replace, keeping the previous good file as
    `<id>.json.bak`. The replace is atomic on one filesystem; the fsync makes
    the new bytes durable before the old ones are given up, so power loss
    leaves either the old goal or the new one, never a torn one."""
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    goal.setdefault("schema", GOAL_SCHEMA_VERSION)
    p = _goal_path(goal["goal_id"])
    tmp = p.with_suffix(".json.tmp")
    _fsync_write(tmp, json.dumps(goal, indent=2, default=str).encode("utf-8"))
    if p.exists() and _load_goal(p) is not None:
        try:
            _fsync_write(_backup_path(p), p.read_bytes())
        except OSError as e:
            _log.warning("goal %s: could not keep the previous copy: %s", goal["goal_id"], e)
    os.replace(tmp, p)


def _goal_ids_on_disk() -> List[str]:
    ids = set()
    for f in GOALS_DIR.glob("goal_*.json"):
        ids.add(f.name[:-len(".json")])
    for f in GOALS_DIR.glob("goal_*.json.bak"):
        ids.add(f.name[:-len(".json.bak")])
    return sorted(ids)


def list_goals(*, status: Optional[str] = None) -> List[Dict[str, Any]]:
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for gid in _goal_ids_on_disk():
        g = _read_goal_file(gid)
        if g is None:
            continue
        if status is None or g.get("status") == status:
            out.append(g)
    return out


def unreadable_goals() -> List[Dict[str, Any]]:
    """Goals whose file and last good copy are both unreadable. They are
    reported, never silently dropped: the owner is told a goal exists that
    Friday cannot read, and the file stays where it is for repair."""
    if not GOALS_DIR.exists():
        return []
    out = []
    for gid in _goal_ids_on_disk():
        if _read_goal_file(gid) is None:
            out.append({"goal_id": gid, "file": "goals/%s.json" % gid})
    return out


def get_goal(goal_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _read_goal_file(goal_id)


def _mutate_goal(goal_id: str, fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]
                  ) -> Optional[Dict[str, Any]]:
    """Read-modify-write a goal file under the lock. `fn(goal)` mutates in
    place and/or returns a replacement dict; None from fn means "use the
    (mutated) input". A raise inside fn propagates WITHOUT writing (the lock
    still releases via the context manager) — used by transition_goal to
    reject an illegal move without corrupting the stored record."""
    with _LOCK:
        goal = _read_goal_file(goal_id)
        if goal is None:
            return None
        result = fn(goal)
        goal = result if isinstance(result, dict) else goal
        goal["updated_at"] = _now_iso()
        _write_goal_file(goal)
        return goal


def _find_milestone(goal: Dict[str, Any], milestone_id: str) -> Optional[Dict[str, Any]]:
    for m in goal.get("milestones") or []:
        if m.get("milestone_id") == milestone_id:
            return m
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  CREATE / UPDATE
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_milestone(m: Optional[dict]) -> Dict[str, Any]:
    m = dict(m or {})
    return {
        "milestone_id": m.get("milestone_id") or f"ms_{uuid.uuid4().hex[:8]}",
        "name": (m.get("name") or "Milestone").strip()[:200],
        "due": m.get("due"),
        "done_at": m.get("done_at"),
        "verification": m.get("verification"),
        "status": m.get("status") if m.get("status") in MILESTONE_STATUSES else "pending",
        "attempts": int(m.get("attempts") or 0),
        "success_criteria": (m.get("success_criteria") or "").strip(),
        "cost_estimate_mψ": int(m.get("cost_estimate_mψ") or DEFAULT_MILESTONE_COST_MΨ),
        "artifacts": list(m.get("artifacts") or []),
        "approval_id": m.get("approval_id"),
    }


_MUTABLE_GOAL_FIELDS = {
    "title", "description", "deadline", "success_criteria",
    "linked_schedules", "linked_chains", "budget_cap_mψ",
    "approval_required", "verification_mode", "max_repair_retries",
    "milestone_timeout_seconds", "dissent_constraints", "owner",
}


def create_goal(*, title: str, description: str = "", owner: str = "owner",
                deadline: Any = None, milestones: Optional[List[dict]] = None,
                success_criteria: str = "", linked_schedules: Optional[List[str]] = None,
                linked_chains: Optional[List[str]] = None, budget_cap_mψ: int = 0,
                approval_required: bool = False, verification_mode: str = "auto",
                status: str = "proposed") -> Dict[str, Any]:
    """Create a new Goal. Defaults to status='proposed' (V6 P5's own state
    machine start) — pass status='active' for a goal that's already been
    blessed (e.g. tests, or a caller that does its own proposal flow)."""
    now = _now_iso()
    gid = f"goal_{uuid.uuid4().hex[:10]}"
    st = status if status in STATUSES else "proposed"
    goal: Dict[str, Any] = {
        "goal_id": gid,
        "title": (title or "").strip()[:200] or "Untitled goal",
        "description": (description or "").strip(),
        "owner": owner or "owner",
        "status": st,
        "deadline": deadline,
        "milestones": [_normalize_milestone(m) for m in (milestones or [])],
        "success_criteria": (success_criteria or "").strip(),
        "linked_schedules": list(linked_schedules or []),
        "linked_chains": list(linked_chains or []),
        "budget_cap_mψ": int(budget_cap_mψ or 0),
        "spent_mψ": 0,
        "approval_required": bool(approval_required),
        "verification_mode": verification_mode if verification_mode in ("auto", "manual") else "auto",
        "max_repair_retries": DEFAULT_MAX_REPAIR_RETRIES,
        "milestone_timeout_seconds": DEFAULT_MILESTONE_TIMEOUT_SECONDS,
        "dissent_constraints": [],
        "receipts": [],
        "rollovers": 0,
        "history": [{"at": now, "from": None, "to": st, "reason": "created"}],
        "created_at": now,
        "updated_at": now,
    }
    _write_goal_file(goal)
    return goal


def update_goal(goal_id: str, patch: dict) -> Optional[Dict[str, Any]]:
    def _fn(goal):
        for k, v in (patch or {}).items():
            if k in _MUTABLE_GOAL_FIELDS:
                goal[k] = v
        return goal
    return _mutate_goal(goal_id, _fn)


def add_milestone(goal_id: str, milestone: dict) -> Optional[Dict[str, Any]]:
    def _fn(goal):
        goal.setdefault("milestones", []).append(_normalize_milestone(milestone))
        return goal
    return _mutate_goal(goal_id, _fn)


def _update_milestone(goal_id: str, milestone_id: str, **fields) -> Optional[Dict[str, Any]]:
    def _fn(goal):
        m = _find_milestone(goal, milestone_id)
        if m is not None:
            m.update(fields)
        return goal
    return _mutate_goal(goal_id, _fn)


def transition_goal(goal_id: str, new_status: str, *, reason: str = "") -> Dict[str, Any]:
    """Validated state-machine move. Raises KeyError if the goal doesn't
    exist, ValueError on an illegal transition (current status not
    permitted to move to new_status) — same status is a true no-op."""
    if new_status not in STATUSES:
        raise UserFacingValueError(f"unknown status: {new_status!r}")

    def _fn(goal):
        cur = goal.get("status")
        if new_status == cur:
            return goal
        allowed = _ALLOWED_TRANSITIONS.get(cur, set())
        if new_status not in allowed:
            raise UserFacingValueError(f"illegal transition {cur!r} -> {new_status!r}")
        goal["status"] = new_status
        goal.setdefault("history", []).append({
            "at": _now_iso(), "from": cur, "to": new_status, "reason": reason,
        })
        return goal

    goal = _mutate_goal(goal_id, _fn)
    if goal is None:
        raise KeyError(f"no such goal: {goal_id}")
    return goal


#: Approval-card kind for moving a goal's missed dates forward.
ROLLOVER_KIND = "goal_deadline_rollover"


def _plan_rollover(goal: Dict[str, Any], *, extend_seconds: int = 7 * 86400,
                   now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The date moves a rollover would make, without making them: every
    not-done overdue milestone `due` (and an overdue goal `deadline`) pushed
    forward by whole extend_seconds increments. None when nothing is overdue."""
    now = time.time() if now is None else now
    extend_seconds = max(1, int(extend_seconds))

    def _forward(ts: float) -> str:
        while ts < now:
            ts += extend_seconds
        return _iso_from_ts(ts)

    moves = []
    for m in goal.get("milestones") or []:
        if m.get("status") == "done":
            continue
        due = _parse_ts(m.get("due"))
        if due is not None and due < now:
            moves.append({"milestone_id": m.get("milestone_id"), "name": m.get("name"),
                          "from": m.get("due"), "to": _forward(due)})
    deadline = None
    deadline_ts = _parse_ts(goal.get("deadline"))
    if (deadline_ts is not None and deadline_ts < now
            and goal.get("status") not in TERMINAL_STATUSES):
        deadline = {"from": goal.get("deadline"), "to": _forward(deadline_ts)}
    if not moves and deadline is None:
        return None
    return {"milestones": moves, "deadline": deadline}


def _apply_rollover_plan(goal_id: str, plan: Dict[str, Any], *, reason: str
                         ) -> Optional[Dict[str, Any]]:
    """Move each date in `plan` only if it still reads as the plan's `from`,
    so a date the owner changed since is never overwritten."""
    def _fn(goal):
        changed = False
        for mv in plan.get("milestones") or []:
            m = _find_milestone(goal, mv.get("milestone_id") or "")
            if m and m.get("status") != "done" and m.get("due") == mv.get("from"):
                m["due"] = mv.get("to")
                changed = True
        dl = plan.get("deadline")
        if dl and goal.get("deadline") == dl.get("from"):
            goal["deadline"] = dl.get("to")
            changed = True
        if changed:
            goal["rollovers"] = int(goal.get("rollovers") or 0) + 1
            goal.setdefault("history", []).append({
                "at": _now_iso(), "from": goal.get("status"), "to": goal.get("status"),
                "reason": reason,
            })
        return goal
    return _mutate_goal(goal_id, _fn)


def rollover_incomplete_milestones(goal_id: str, *, extend_seconds: int = 7 * 86400
                                    ) -> Optional[Dict[str, Any]]:
    """Move a goal's overdue dates forward now. This is the owner's own,
    explicit rollover; nothing scheduled calls it. The weekly review raises a
    rollover proposal instead (propose_deadline_rollover). A no-op (no
    history entry) when nothing is overdue."""
    goal = get_goal(goal_id)
    if goal is None:
        return None
    plan = _plan_rollover(goal, extend_seconds=extend_seconds)
    if plan is None:
        return goal
    return _apply_rollover_plan(
        goal_id, plan, reason="rollover: overdue milestone/goal deadlines adjusted")


def propose_deadline_rollover(goal_id: str, *, extend_seconds: int = 7 * 86400
                              ) -> Optional[Dict[str, Any]]:
    """Raise one approval card proposing new dates for a goal's missed ones.

    Nothing moves until the owner approves the card (_on_rollover_decided).
    The card's subject is the exact set of missed dates, so the same miss
    raises one card however many reviews see it, and a declined proposal is
    not raised again. None when nothing is overdue."""
    goal = get_goal(goal_id)
    if goal is None:
        return None
    plan = _plan_rollover(goal, extend_seconds=extend_seconds)
    if plan is None:
        return None
    sig_src = json.dumps({"m": [[mv["milestone_id"], mv["from"]] for mv in plan["milestones"]],
                          "d": (plan["deadline"] or {}).get("from")}, sort_keys=True)
    sig = hashlib.sha256(sig_src.encode("utf-8")).hexdigest()[:12]
    lines = [f"- {mv.get('name') or mv.get('milestone_id')}: due {mv['from']} -> {mv['to']}"
             for mv in plan["milestones"]]
    if plan["deadline"]:
        lines.append(f"- Goal deadline: {plan['deadline']['from']} -> {plan['deadline']['to']}")
    title = goal.get("title") or goal_id
    return approvals.create_approval(
        kind=ROLLOVER_KIND, subject_type="goal", subject_id=f"{goal_id}:rollover:{sig}",
        title=f"Missed dates on {title}: move them forward?",
        description="\n".join(lines),
        action_description=f"Move the missed dates on goal {title!r} forward",
        action_class="internal", force_gate=True,
        payload={"goal_id": goal_id, "extend_seconds": int(extend_seconds), **plan})


def _on_rollover_decided(approval: Dict[str, Any]) -> None:
    payload = approval.get("payload") or {}
    goal_id = payload.get("goal_id")
    if not goal_id:
        return
    status = approval.get("status")
    if status == "approved":
        _apply_rollover_plan(goal_id, payload,
                             reason="rollover approved by owner: missed dates moved forward")
    elif status in ("denied", "expired"):
        why = "declined by owner" if status == "denied" else "expired"

        def _fn(goal):
            goal.setdefault("history", []).append({
                "at": _now_iso(), "from": goal.get("status"), "to": goal.get("status"),
                "reason": f"rollover {why}: missed dates left as they are",
            })
            return goal
        _mutate_goal(goal_id, _fn)


def _maybe_complete_goal(goal_id: str) -> Optional[Dict[str, Any]]:
    def _fn(goal):
        if goal.get("status") != "active":
            return goal
        milestones = goal.get("milestones") or []
        if milestones and all(m.get("status") == "done" for m in milestones):
            goal["status"] = "completed"
            goal.setdefault("history", []).append({
                "at": _now_iso(), "from": "active", "to": "completed",
                "reason": "all milestones completed",
            })
        return goal
    return _mutate_goal(goal_id, _fn)


def get_receipts(goal_id: str) -> Optional[List[dict]]:
    goal = get_goal(goal_id)
    return None if goal is None else list(goal.get("receipts") or [])


# ═══════════════════════════════════════════════════════════════════════════
#  BUDGET — per-goal hard cap (duration-independent) + budget_enforcer reuse
# ═══════════════════════════════════════════════════════════════════════════

def _reserve_goal_budget(goal_id: str, amount_mψ: int) -> bool:
    """Atomically check-AND-commit this spend against the goal's OWN
    cumulative cap (0/falsy cap = unlimited) in the SAME read-modify-write
    cycle _mutate_goal already serializes via goals._LOCK — not a check now,
    debit whenever the work finishes later. On success, `amount_mψ` is
    immediately added to goal["spent_mψ"] as a held reservation; the caller
    MUST later call _release_goal_budget with whatever portion went unused
    (mirroring an escrow: debit the full estimate up front, credit back the
    unused delta once the real cost is known — exactly how budget_enforcer's
    own reserve_budget/release_budget already work). See module docstring,
    'Budget model', for why this must be atomic-per-call rather than a single
    check before a milestone's whole (possibly multi-attempt) repair loop.

    Also asks budget_enforcer to reserve the same amount against the
    pseudo-workspace "goal:<goal_id>" for cross-goal visibility in the
    existing Budgets panel — best-effort, never the deciding factor."""
    amount = max(0, int(amount_mψ or 0))

    reserved = {"ok": False}

    def _fn(goal):
        cap = int(goal.get("budget_cap_mψ") or 0)
        spent = int(goal.get("spent_mψ") or 0)
        if cap > 0 and spent + amount > cap:
            reserved["ok"] = False
            return goal  # unchanged — nothing committed
        goal["spent_mψ"] = spent + amount
        reserved["ok"] = True
        return goal

    goal = _mutate_goal(goal_id, _fn)
    if goal is None or not reserved["ok"]:
        return False
    try:
        from agent_friday.services import budget_enforcer as be
        be.reserve_budget(f"goal:{goal_id}", amount)
    except Exception as e:
        _log.debug("budget_enforcer.reserve_budget unavailable: %s", e)
    return True


def _release_goal_budget(goal_id: str, unused_mψ: int) -> None:
    """Credit back the unused portion of one or more prior
    _reserve_goal_budget commits — the mirror-image debit that closes the
    escrow opened by each successful reservation for this run. Subtracts
    from goal["spent_mψ"] (floored at 0) so the net effect of a reserve/
    release pair (or several, across a repair loop's multiple attempts) is
    exactly the real cumulative cost, never the estimate."""
    amount = max(0, int(unused_mψ or 0))
    if not amount:
        return

    def _fn(goal):
        goal["spent_mψ"] = max(0, int(goal.get("spent_mψ") or 0) - amount)
        return goal
    _mutate_goal(goal_id, _fn)
    try:
        from agent_friday.services import budget_enforcer as be
        be.release_budget(f"goal:{goal_id}", amount)
    except Exception as e:
        _log.debug("budget_enforcer.release_budget unavailable: %s", e)


# ═══════════════════════════════════════════════════════════════════════════
#  RECEIPTS — signed proof-of-work, following A2's record_dissent_event()
#  pattern for consistency (see module docstring)
# ═══════════════════════════════════════════════════════════════════════════

def _sign_receipt(entry: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from agent_friday.governance.proof_of_integrity import get_governance_key
        key = get_governance_key()
    except Exception as e:
        _log.error("receipt signing key unavailable: %s", e)
        return {**entry, "hmac": "unsigned:governance_key_unavailable"}
    try:
        from agent_friday.privacy import vault_crypto as _vc
        return _vc.sign_entry(entry, key)
    except Exception:
        canonical = json.dumps(entry, sort_keys=True, default=str).encode("utf-8")
        h = _hmac.new(key, canonical, hashlib.sha256).hexdigest()
        return {**entry, "hmac": h}


def verify_receipt(entry: Dict[str, Any]) -> bool:
    try:
        from agent_friday.governance.proof_of_integrity import get_governance_key
        from agent_friday.privacy import vault_crypto as _vc
        return _vc.verify_entry(entry, get_governance_key())
    except Exception:
        return False


def _make_receipt(goal: Dict[str, Any], milestone: Dict[str, Any], *, kind: str,
                  verdict: Optional[dict], cost_mψ: int, artifact_text: str = "",
                  note: str = "") -> Dict[str, Any]:
    """what ran, what was verified, cost, artifacts — per AUTONOMY_SPEC A3
    item 5. Signed via _sign_receipt (HMAC over the governance key, same
    primitive dissent_gate.record_dissent_event uses)."""
    entry = {
        "receipt_id": f"rcpt_{uuid.uuid4().hex[:12]}",
        "timestamp": _now_iso(),
        "goal_id": goal.get("goal_id"),
        "milestone_id": milestone.get("milestone_id"),
        "milestone_name": milestone.get("name"),
        "kind": kind,
        "what_ran": f"{goal.get('title')}: {milestone.get('name')}",
        "verified": ({
            "passed": bool(verdict.get("passed")),
            "score": verdict.get("score"),
            "critique": verdict.get("critique"),
        } if verdict else None),
        "cost_mψ": int(cost_mψ or 0),
        "artifacts": [artifact_text[:2000]] if artifact_text else [],
        "note": note,
    }
    return _sign_receipt(entry)


def _append_receipt(goal_id: str, receipt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    def _fn(goal):
        goal.setdefault("receipts", []).append(receipt)
        return goal
    goal = _mutate_goal(goal_id, _fn)
    try:
        RECEIPTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _RECEIPTS_LOCK:
            with RECEIPTS_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(receipt, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        _log.error("receipt log append FAILED: %s", e)
    return goal


# ═══════════════════════════════════════════════════════════════════════════
#  WORK LOG — goal_id -> work_log.goal_ancestry_json (the field already
#  exists there for exactly this; see V6 §2 substrate table)
# ═══════════════════════════════════════════════════════════════════════════

def _log_work_entry(goal: Dict[str, Any], milestone: Dict[str, Any],
                    verdict: Optional[dict], cost_mψ: int, *, status: str,
                    receipt: Optional[Dict[str, Any]] = None) -> None:
    """Uses work_log's own documented duck-typed contract ("task should be a
    WorkerTask (or any object with .task_id, .prompt, etc.)") rather than
    routing through orchestrator's Worker path, since milestone execution
    runs through the default agent executor (or a swapped-in one), not the
    local worker adapters orchestrator.py wraps.

    `receipt` (when given) has its receipt_id/kind/hmac woven into
    goal_ancestry_json too — "appended to the goal AND to work_log" (V6 P5)
    means a work_log ROW should be traceable back to the exact signed
    receipt it corresponds to, not just to the goal in general. The receipt
    itself stays signed and canonical on the goal record (work_log's own
    schema has no signature column — see governance.proof_of_integrity for
    that layer); this is the cross-reference, not a duplicate signing."""
    try:
        from agent_friday.services import work_log as wl
    except Exception as e:
        _log.warning("work_log unavailable, skipping goal work-log entry: %s", e)
        return
    ctx = {
        "goal_id": goal.get("goal_id"), "goal_title": goal.get("title"),
        "milestone_id": milestone.get("milestone_id"),
        "milestone_name": milestone.get("name"),
        "workspace_goal": f"goal:{goal.get('goal_id')}",
        "workspace": f"goal:{goal.get('goal_id')}",
        "receipt_id": (receipt or {}).get("receipt_id"),
        "receipt_kind": (receipt or {}).get("kind"),
        "receipt_hmac": (receipt or {}).get("hmac"),
    }
    task = SimpleNamespace(
        task_id=f"goal-{goal.get('goal_id')}-{milestone.get('milestone_id')}-{uuid.uuid4().hex[:6]}",
        prompt=_build_milestone_prompt(goal, milestone), adapter_type="agent",
        task_type="GOAL_MILESTONE", context=ctx,
    )
    try:
        wl.log_start(task)
        result = SimpleNamespace(
            status=status, tokens_used=0, cost_mψ=int(cost_mψ or 0),
            quality_score=float((verdict or {}).get("score") or 0.0),
            error=None if status == "COMPLETED" else (verdict or {}).get("critique"),
            artifacts=[receipt["receipt_id"]] if receipt else [],
        )
        wl.log_finish(task, result)
    except Exception as e:
        _log.warning("work_log entry failed for goal %s milestone %s: %s",
                    goal.get("goal_id"), milestone.get("milestone_id"), e)


def _work_log_entries_for_goal(goal_id: str, limit: int = 200) -> List[dict]:
    try:
        from agent_friday.services import work_log as wl
        entries = wl.get_log(limit=limit)
    except Exception:
        return []
    out = []
    for e in entries:
        anc = e.get("goal_ancestry_json")
        if isinstance(anc, dict) and anc.get("goal_id") == goal_id:
            out.append(e)
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  EXECUTOR SEAM — swappable (mirrors interest_model.register_goals_provider)
# ═══════════════════════════════════════════════════════════════════════════

def _build_milestone_prompt(goal: Dict[str, Any], milestone: Dict[str, Any], *,
                            critique_hint: str = "") -> str:
    criteria = (milestone.get("success_criteria") or goal.get("success_criteria")
                or "(use your best judgment; be concrete and complete)")
    parts = [
        "You are working autonomously on a durable goal the owner already approved.",
        f"Goal: {goal.get('title')}",
        f"Goal description: {goal.get('description') or '(none given)'}",
        f"Current milestone: {milestone.get('name')}",
        f"Success criteria for THIS milestone: {criteria}",
        "Do the work now and produce the concrete deliverable/output text — "
        "do not just describe what you would do.",
    ]
    if critique_hint:
        parts.append(f"IMPORTANT — a prior attempt at this milestone fell short: {critique_hint}")
    return "\n\n".join(parts)


def _default_executor(goal: Dict[str, Any], milestone: Dict[str, Any], *,
                      critique_hint: str = "") -> Dict[str, Any]:
    """Dispatch through the full agent tool loop via services.agent.
    _spawn_task, polling to a terminal state — mirrors scheduler.py's own
    bridge for "agent_prompt" schedules. Returns
    {"ok", "text", "cost_mψ", "error"?, "task_id"?}.

    Defense in depth (V6 invariant 5 / AUTONOMY_SPEC A3 finding — "any
    registered network tool can be invoked with zero re-classification
    against Q3 and zero human approval"): the spawned task is dispatched
    under the "goal-milestone" subagent scope (safe read/write/research
    allow-list — see services.subagents.BUILTIN_SCOPES) UNLESS
    _run_milestone_locked stamped milestone["_hard_gate_approved"]=True,
    meaning THIS exact dispatch was classified hard/gated by the Q3 gate AND
    a human explicitly approved it. This holds regardless of how the Q3 text
    classifier scored the milestone's name/description — an auto-approved
    (or mis-classified) milestone's background task simply cannot reach an
    outward/spend/irreversible/shell tool, full stop."""
    prompt = _build_milestone_prompt(goal, milestone, critique_hint=critique_hint)
    try:
        from agent_friday.services.agent import _spawn_task, _task_snapshot
    except Exception as e:
        return {"ok": False, "text": "", "cost_mψ": 0, "error": ExceptionText(f"agent unavailable: {e}")}

    hard_gate_approved = bool(milestone.get("_hard_gate_approved"))
    try:
        tid = _spawn_task(
            f"Goal · {milestone.get('name')}", prompt,
            description=f"goal:{goal.get('goal_id')}:milestone:{milestone.get('milestone_id')}",
            orb_icon="🎯",
            scope=None if hard_gate_approved else "goal-milestone",
            task_id=milestone.get("_task_id"),
        )
    except Exception as e:
        # _spawn_task fails CLOSED when a required scope can't be applied —
        # never fall back to spawning unscoped. See its own docstring.
        return {"ok": False, "text": "", "cost_mψ": 0,
                "error": ExceptionText(f"could not safely dispatch milestone: {e}")}
    # The goal points at its task from this moment on: if Friday stops while
    # the task runs, the next start finds the task through this record and
    # follows the task's own honest-resume verdict (see reconcile_on_boot).
    _note_run(goal.get("goal_id"), milestone.get("milestone_id"), task_id=tid)
    timeout = int(goal.get("milestone_timeout_seconds") or DEFAULT_MILESTONE_TIMEOUT_SECONDS)
    return _await_task(tid, milestone, timeout)


#: Task statuses that mean the task's work is over (task_journal.is_terminal
#: covers the same spellings plus "interrupted", which is NOT over: it can
#: still be resumed by the owner).
_TASK_SUCCESS = ("complete", "completed", "completed_unverified", "done")
_TASK_FAILED = ("failed", "error", "timeout", "cancelled", "killed", "superseded")


def _task_outcome(status: Optional[str]) -> str:
    """'running', 'done', 'failed', 'interrupted' or 'missing'."""
    s = str(status or "")
    if not s:
        return "missing"
    if s == "interrupted":
        return "interrupted"
    if s in _TASK_SUCCESS or s.startswith("complete"):
        return "done"
    if s in _TASK_FAILED or s.startswith("failed"):
        return "failed"
    return "running"


def _await_task(tid: str, milestone: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    deadline = time.time() + max(1, int(timeout))
    while time.time() < deadline:
        status, result = _task_status(tid)
        outcome = _task_outcome(status)
        if outcome == "failed":
            return {"ok": False, "text": "", "cost_mψ": 0,
                    "error": str(result or "agent task failed"), "task_id": tid}
        if outcome == "done":
            return {"ok": True, "text": str(result or ""),
                    "cost_mψ": int(milestone.get("cost_estimate_mψ") or DEFAULT_MILESTONE_COST_MΨ),
                    "task_id": tid}
        if outcome == "interrupted":
            return {"ok": False, "text": "", "cost_mψ": 0, "task_id": tid,
                    "interrupted": True, "error": "the task was interrupted"}
        time.sleep(1)
    return {"ok": False, "text": "", "cost_mψ": 0,
            "error": "milestone execution timed out", "task_id": tid}


def _task_status(tid: str) -> Tuple[Optional[str], str]:
    """(status, result) of a background task: the live registry first, then
    its journal on disk. (None, "") when neither knows the task."""
    try:
        from agent_friday.services.agent import _task_snapshot
        snap = _task_snapshot(tid)
        if isinstance(snap, dict) and snap.get("status"):
            return snap.get("status"), str(snap.get("result") or "")
    except Exception:
        pass
    try:
        from agent_friday.services import task_journal as tj
        st = tj.read_state(tid)
        if isinstance(st, dict) and st.get("status"):
            return st.get("status"), str(st.get("result") or "")
    except Exception:
        pass
    return None, ""


_EXECUTOR: Callable[..., Dict[str, Any]] = _default_executor


def set_executor(fn: Optional[Callable[..., Dict[str, Any]]]) -> None:
    """Swap the milestone-work executor. fn(goal, milestone, *,
    critique_hint="") -> {"ok", "text", "cost_mψ", "error"?}. Passing None
    restores the default (services.agent._spawn_task) executor. The test/
    integration seam — mirrors interest_model.register_goals_provider's
    style — so unit tests never spin a real background agent thread."""
    global _EXECUTOR
    _EXECUTOR = fn if fn is not None else _default_executor


def _execute_milestone_work(goal: Dict[str, Any], milestone: Dict[str, Any], *,
                            critique_hint: str = "") -> Dict[str, Any]:
    try:
        return _EXECUTOR(goal, milestone, critique_hint=critique_hint)
    except Exception as e:
        return {"ok": False, "text": "", "cost_mψ": 0, "error": ExceptionText(f"executor raised: {e}")}


# ═══════════════════════════════════════════════════════════════════════════
#  INTEREST MODEL — dissent_gate's "what does the user want" snapshot
# ═══════════════════════════════════════════════════════════════════════════

def _safe_interest_model() -> Dict[str, Any]:
    try:
        from agent_friday.services import interest_model as im
        return im.assemble_interest_model()
    except Exception as e:
        _log.warning("interest_model assembly unavailable, using empty snapshot: %s", e)
        return {}


# ═══════════════════════════════════════════════════════════════════════════
#  run_milestone — verify -> notice -> repair -> escalate, gated by Q3
# ═══════════════════════════════════════════════════════════════════════════
# _RUNNING_MILESTONES mirrors services/scheduler.py's own _RUNNING/_RUNNING_
# LOCK guard: the decision-hook resume path (approvals.decide() -> a fresh
# run_milestone() call) and the scheduler tick (run_due_milestones()) are two
# independent callers that could otherwise race on the SAME milestone (e.g.
# the owner approves the instant a tick was already about to dispatch it),
# double-executing (and double-spending) it. This makes a concurrent second
# call a clean no-op instead.

_RUNNING_MILESTONES: set = set()
_RUNNING_MILESTONES_LOCK = threading.Lock()


@visible_job('Goal milestone', kind='goal_milestone', workspace='goals')
def run_milestone(goal_id: str, milestone_id: str) -> Dict[str, Any]:
    """Advance one milestone by one attempt-cycle (which itself contains a
    bounded internal repair loop). NEVER marks a milestone done on a failed
    verification. Returns a status envelope; never raises.

    If the action is Q3-gated (or the goal forces approval_required) and not
    yet approved, creates/reuses an approval card and returns without
    executing — approvals.py's decision hook re-invokes this once approved.
    """
    run_key = f"{goal_id}:{milestone_id}"
    with _RUNNING_MILESTONES_LOCK:
        if run_key in _RUNNING_MILESTONES:
            return {"ok": False, "status": "already_running"}
        _RUNNING_MILESTONES.add(run_key)
    try:
        return _run_milestone_locked(goal_id, milestone_id)
    except Exception as e:
        # A fault inside Friday must not leave the milestone looking busy for
        # ever (nothing would ever run or settle it): it waits for the owner.
        _log.error("goal %s milestone %s: attempt failed inside Friday: %s", goal_id, milestone_id, e)
        try:
            goal = get_goal(goal_id) or {}
            m = _find_milestone(goal, milestone_id) or {}
            if _live_states(m):
                _block(goal_id, milestone_id, {
                    "type": "run_failed", "task_id": (m.get("run") or {}).get("task_id"),
                    "needs_confirmation": True,
                    "text": (f"“{m.get('name')}” stopped because of a fault inside Friday. "
                             "Nothing was run again. Start it again when you're ready.")},
                    note="stopped by a fault inside Friday; nothing was re-run")
        except Exception:
            pass
        return {"ok": False, "status": "error", "error": ExceptionText(str(e))}
    finally:
        with _RUNNING_MILESTONES_LOCK:
            _RUNNING_MILESTONES.discard(run_key)


def _run_milestone_locked(goal_id: str, milestone_id: str) -> Dict[str, Any]:
    goal = get_goal(goal_id)
    if goal is None:
        return {"ok": False, "error": "goal not found"}
    if goal.get("status") != "active":
        return {"ok": False, "error": f"goal is {goal.get('status')!r}, not active"}
    milestone = _find_milestone(goal, milestone_id)
    if milestone is None:
        return {"ok": False, "error": "milestone not found"}
    if milestone.get("status") == "done":
        return {"ok": True, "status": "already_done"}
    # A milestone whose earlier attempt was cut short by a restart is never
    # dispatched again from here: what that attempt did is decided by its
    # task's honest-resume verdict, and a fresh start needs the owner's own
    # restart_milestone(..., confirm_unknown_outcome=True).
    if _orphaned_run(milestone):
        _reconcile_milestone(goal_id, milestone_id, background=True)
        return {"ok": False, "status": "interrupted",
                "error": "this milestone was interrupted by a restart; "
                         "it continues only through its task or your restart"}
    if _live_states(milestone):
        # A run of this process owns it (a live attempt, or a task picked up
        # after a restart): never dispatch a second attempt over it.
        return {"ok": False, "status": "already_running"}
    if (milestone.get("blocker") or {}).get("type") in _OWNER_BLOCKERS:
        return {"ok": False, "status": "interrupted", "blocker": milestone.get("blocker"),
                "error": "this milestone waits for you: "
                         + str((milestone.get("blocker") or {}).get("text") or "")}

    # The Q3/dissent gate MUST see the REAL instruction the executor will
    # run, not just the milestone's cosmetic name + the goal's cosmetic
    # description (AUTONOMY_SPEC A3 finding: a milestone named "Weekly
    # maintenance" under a goal described as "Routine housekeeping" was
    # auto-approved even though its success_criteria actually said "Delete
    # all files in the old backups folder and email a confirmation to
    # admin@example.com" — classify()/dissent_gate never saw that text).
    # success_criteria is what _build_milestone_prompt actually hands the
    # executor as its instruction (see that function) — include it here too
    # so classification and dissent-checking operate on the same text the
    # agent is actually asked to do.
    criteria_text = (milestone.get("success_criteria") or goal.get("success_criteria") or "").strip()
    action_description = (
        f"{milestone.get('name')} — {goal.get('description') or goal.get('title')}"
        + (f" — success criteria: {criteria_text}" if criteria_text else "")
    )

    gate = approvals.gate_action(
        kind="goal_milestone", subject_type="goal_milestone",
        subject_id=f"{goal_id}:{milestone_id}",
        title=f"{goal.get('title')} — {milestone.get('name')}",
        description=goal.get("description", ""), action_description=action_description,
        force_gate=bool(goal.get("approval_required")),
        cost_estimate_mψ=int(milestone.get("cost_estimate_mψ") or DEFAULT_MILESTONE_COST_MΨ),
        interest_model=_safe_interest_model(),
    )
    if gate["status"] == "pending":
        appr = gate["approval"] or {}
        _update_milestone(goal_id, milestone_id, status="blocked", approval_id=appr.get("approval_id"))
        return {"ok": False, "status": "pending_approval", "approval_id": appr.get("approval_id")}
    if gate["status"] == "denied":
        appr = gate["approval"] or {}
        _update_milestone(goal_id, milestone_id, status="blocked", approval_id=appr.get("approval_id"))
        receipt = _make_receipt(goal, milestone, kind="denied", verdict=None, cost_mψ=0,
                                note=f"approval {appr.get('status')}")
        _append_receipt(goal_id, receipt)
        return {"ok": False, "status": "denied", "receipt": receipt}

    # gate["status"] in ("auto_approved", "approved") -> proceed. Only
    # "approved" means a human was actually asked (Q3 classified this hard/
    # gated) and said yes — stamp that onto the milestone dict (in-memory
    # only; never persisted — see _default_executor) so the executor seam
    # can tell "genuinely cleared a human gate" apart from "auto-approved
    # because Q3/dissent read it as soft" and scope the dispatch down in the
    # latter case regardless of classification accuracy (defense in depth
    # for the finding above).
    milestone["_hard_gate_approved"] = (gate["status"] == "approved")
    cost_estimate = int(milestone.get("cost_estimate_mψ") or DEFAULT_MILESTONE_COST_MΨ)
    if not _reserve_goal_budget(goal_id, cost_estimate):
        _update_milestone(goal_id, milestone_id, status="blocked")
        receipt = _make_receipt(goal, milestone, kind="budget_blocked", verdict=None, cost_mψ=0,
                                note="per-goal budget cap exceeded")
        _append_receipt(goal_id, receipt)
        return {"ok": False, "status": "budget_exceeded", "receipt": receipt}

    # The run record goes to disk BEFORE any work is dispatched: which process
    # started it and when. The executor adds the task id the moment the task
    # exists (_note_run). After a restart this is how the goal finds out what
    # its last attempt did instead of guessing.
    _update_milestone(goal_id, milestone_id, status="in_progress", blocker=None,
                      run={"boot": BOOT_ID, "started_at": time.time(), "task_id": None,
                           "stage": "prepared", "reserved_mψ": cost_estimate})

    max_retries = int(goal.get("max_repair_retries", DEFAULT_MAX_REPAIR_RETRIES))
    base_attempts = int(milestone.get("attempts") or 0)
    critique_hint = ""
    last_verdict: Optional[dict] = None
    last_text = ""
    total_cost = 0
    reserved_total = cost_estimate  # escrow committed so far (pre-loop reservation above)
    attempt = 0
    budget_exhausted_mid_repair = False

    while attempt <= max_retries:
        if attempt > 0:
            # Every attempt AFTER the first must re-clear the SAME per-goal
            # hard-stop the first attempt cleared. Without this re-check,
            # the bounded repair loop below could dispatch up to
            # max_repair_retries+1 real executor attempts on a SINGLE
            # up-front reservation, accumulating real cost past
            # budget_cap_mψ before the cap was ever consulted again
            # (AUTONOMY_SPEC A3 finding). _reserve_goal_budget is an atomic
            # check-and-commit against the CURRENT persisted spent_mψ (see
            # its docstring), so this also closes the same window across two
            # DIFFERENT milestones of the same goal racing each other.
            if not _reserve_goal_budget(goal_id, cost_estimate):
                budget_exhausted_mid_repair = True
                break
            reserved_total += cost_estimate
        attempt += 1
        _update_milestone(goal_id, milestone_id, attempts=base_attempts + attempt)
        # Each attempt is its own task: forget the previous attempt's task id
        # first, so a restart in this attempt never adopts the last one's work.
        # The task's id is chosen HERE and written down before the task
        # exists, so after a crash the goal knows exactly which task was its
        # own: never a guess from a task's name or description.
        attempt_task_id = f"goal-{uuid.uuid4().hex}"
        _note_run(goal_id, milestone_id, task_id=attempt_task_id, stage="dispatching",
                  attempt=attempt, attempt_started_at=time.time(), reserved_mψ=reserved_total)
        milestone["_task_id"] = attempt_task_id
        work = _execute_milestone_work(goal, milestone, critique_hint=critique_hint)
        total_cost += int(work.get("cost_mψ") or 0)
        if work.get("task_id") and work.get("task_id") != attempt_task_id:
            _note_run(goal_id, milestone_id, task_id=work.get("task_id"))
        if work.get("task_id") and "timed out" in str(work.get("error") or ""):
            # The task may still be running (and acting). A repair attempt
            # dispatched over it could do the same thing twice: stop here,
            # and let the goal pick its result up if it finishes.
            _block(goal_id, milestone_id, {
                "type": "interrupted", "task_id": work.get("task_id"), "needs_confirmation": True,
                "task_resumable": False, "task_needs_confirmation": False,
                "text": _interrupted_words(milestone.get("name") or "this milestone", {}, "still_running")},
                note="its task outlasted the milestone's time; no repair attempt was dispatched over it")
            return {"ok": False, "status": "interrupted", "task_id": work.get("task_id")}
        if work.get("interrupted"):
            # The task stopped without finishing (its process went away).
            # Whether its last step happened is the task's question, not a
            # reason to dispatch a new attempt over it.
            # Its reservation stays counted as spent: what it used is unknown.
            _reconcile_milestone(goal_id, milestone_id, background=True, force=True)
            return {"ok": False, "status": "interrupted", "task_id": work.get("task_id")}

        if not work.get("ok"):
            last_text = ""
            last_verdict = {"status": "error", "passed": False, "score": None,
                            "critique": work.get("error") or "execution failed",
                            "suggestions": ""}
            critique_hint = (f"The previous attempt failed to even run: "
                            f"{work.get('error')}. Try a different, more robust approach.")
            continue

        last_text = work.get("text") or ""

        if (goal.get("verification_mode") or "auto") == "manual":
            _release_goal_budget(goal_id, max(0, reserved_total - total_cost))
            appr = approvals.create_approval(
                kind="goal_milestone_confirm", subject_type="goal_milestone",
                subject_id=f"{goal_id}:{milestone_id}:confirm",
                title=f"Confirm milestone: {milestone.get('name')}",
                description=last_text[:1000], action_description=action_description,
                force_gate=True, cost_estimate_mψ=0, interest_model=_safe_interest_model(),
                payload={"draft_text": last_text[:4000]},
            )
            _update_milestone(goal_id, milestone_id, status="verifying",
                             approval_id=appr.get("approval_id"), artifacts=[last_text[:2000]],
                             **_closed_run(goal_id, milestone_id))
            return {"ok": False, "status": "pending_manual_confirmation",
                    "approval_id": appr.get("approval_id")}

        criteria = milestone.get("success_criteria") or goal.get("success_criteria") or ""
        verdict = qa_gates.evaluate_text(last_text, criteria, workspace=f"goal:{goal_id}")
        last_verdict = verdict

        if verdict.get("passed"):
            _release_goal_budget(goal_id, max(0, reserved_total - total_cost))
            now = _now_iso()
            _update_milestone(goal_id, milestone_id, status="done", done_at=now,
                             verification=verdict, artifacts=[last_text[:2000]],
                             **_closed_run(goal_id, milestone_id))
            receipt = _make_receipt(goal, milestone, kind="milestone_complete", verdict=verdict,
                                    cost_mψ=total_cost, artifact_text=last_text)
            _append_receipt(goal_id, receipt)
            _log_work_entry(goal, milestone, verdict, total_cost, status="COMPLETED", receipt=receipt)
            _maybe_complete_goal(goal_id)
            return {"ok": True, "status": "done", "verification": verdict, "receipt": receipt}

        # Failed verification -> fold critique into the next attempt (bounded).
        critique_hint = (
            f"A previous attempt scored {verdict.get('score')} "
            f"(threshold {verdict.get('threshold')}). Critique: {verdict.get('critique')}. "
            f"Suggestions: {verdict.get('suggestions')}. Produce a materially "
            "improved result that directly addresses this critique."
        )

    # Repair attempts exhausted -- either the retry bound ran out, or the
    # per-goal budget cap fired mid-repair (budget_exhausted_mid_repair) --
    # escalate to a human gate either way. NEVER mark done.
    _release_goal_budget(goal_id, max(0, reserved_total - total_cost))
    escalation_reason = (
        f"per-goal budget cap reached mid-repair (after {attempt} attempt(s), "
        f"before dispatching another)"
        if budget_exhausted_mid_repair else
        f"verification failed after {attempt} attempt(s)"
    )
    _update_milestone(goal_id, milestone_id, status="escalated", verification=last_verdict,
                     artifacts=[last_text[:2000]] if last_text else [],
                     **_closed_run(goal_id, milestone_id))
    receipt = _make_receipt(goal, milestone, kind="verification_failed_escalated",
                            verdict=last_verdict, cost_mψ=total_cost, artifact_text=last_text,
                            note=escalation_reason)
    _append_receipt(goal_id, receipt)
    _log_work_entry(goal, milestone, last_verdict, total_cost, status="FAILED", receipt=receipt)
    appr = approvals.create_approval(
        kind="goal_milestone_escalation", subject_type="goal_milestone",
        subject_id=f"{goal_id}:{milestone_id}:escalation",
        title=f"{escalation_reason.capitalize()}: {milestone.get('name')}",
        description=(last_verdict or {}).get("critique", "") or escalation_reason,
        action_description=action_description,
        force_gate=True, cost_estimate_mψ=0, interest_model=_safe_interest_model(),
        payload={"last_text": last_text[:4000], "verdict": last_verdict,
                "budget_exhausted_mid_repair": budget_exhausted_mid_repair},
    )
    _update_milestone(goal_id, milestone_id, approval_id=appr.get("approval_id"))
    return {"ok": False, "status": "escalated", "approval_id": appr.get("approval_id"),
            "verification": last_verdict, "receipt": receipt,
            "budget_exhausted_mid_repair": budget_exhausted_mid_repair}


# ═══════════════════════════════════════════════════════════════════════════
#  DECISION HOOKS — resume blocked milestones once approvals.decide() fires
# ═══════════════════════════════════════════════════════════════════════════

def _subject_goal_and_milestone(approval: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """goal_id, milestone_id from a "goal_id:milestone_id[:suffix]" subject_id
    (goals.py's own subject_id convention — see run_milestone/escalation/
    confirm creation sites). Neither id ever contains a colon (both are
    uuid-hex-suffixed, colon-free tokens), so this is unambiguous regardless
    of how many suffix segments follow."""
    parts = (approval.get("subject_id") or "").split(":")
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _on_milestone_gate_decided(approval: Dict[str, Any]) -> None:
    ids = _subject_goal_and_milestone(approval)
    if not ids:
        return
    goal_id, milestone_id = ids
    status = approval.get("status")
    if status == "approved":
        run_milestone(goal_id, milestone_id)
    elif status in ("denied", "expired"):
        _update_milestone(goal_id, milestone_id, status="blocked")


def _accept_escalated_milestone(goal_id: str, milestone_id: str) -> None:
    goal = get_goal(goal_id)
    if goal is None:
        return
    milestone = _find_milestone(goal, milestone_id)
    if milestone is None:
        return
    verdict = dict(milestone.get("verification") or {})
    verdict["passed"] = True
    verdict["human_override"] = True
    now = _now_iso()
    _update_milestone(goal_id, milestone_id, status="done", done_at=now, verification=verdict)
    receipt = _make_receipt(goal, milestone, kind="milestone_human_approved_override",
                            verdict=verdict, cost_mψ=0,
                            note="owner approved despite a failed automated verification")
    _append_receipt(goal_id, receipt)
    _log_work_entry(goal, milestone, verdict, 0, status="COMPLETED", receipt=receipt)
    _maybe_complete_goal(goal_id)


def _on_escalation_decided(approval: Dict[str, Any]) -> None:
    ids = _subject_goal_and_milestone(approval)
    if not ids:
        return
    goal_id, milestone_id = ids
    status = approval.get("status")
    if status == "approved":
        _accept_escalated_milestone(goal_id, milestone_id)
    elif status in ("denied", "expired"):
        _update_milestone(goal_id, milestone_id, status="failed")


def _accept_manual_confirmation(goal_id: str, milestone_id: str, approval: Dict[str, Any]) -> None:
    goal = get_goal(goal_id)
    if goal is None:
        return
    milestone = _find_milestone(goal, milestone_id)
    if milestone is None:
        return
    draft_text = (approval.get("payload") or {}).get("draft_text", "")
    verdict = {"status": "manual", "passed": True, "score": None,
              "critique": "manually confirmed by owner (verification_mode=manual)"}
    now = _now_iso()
    _update_milestone(goal_id, milestone_id, status="done", done_at=now, verification=verdict)
    receipt = _make_receipt(goal, milestone, kind="milestone_manual_confirmed", verdict=verdict,
                            cost_mψ=0, artifact_text=draft_text)
    _append_receipt(goal_id, receipt)
    _log_work_entry(goal, milestone, verdict, 0, status="COMPLETED", receipt=receipt)
    _maybe_complete_goal(goal_id)


def _on_confirm_decided(approval: Dict[str, Any]) -> None:
    ids = _subject_goal_and_milestone(approval)
    if not ids:
        return
    goal_id, milestone_id = ids
    status = approval.get("status")
    if status == "approved":
        _accept_manual_confirmation(goal_id, milestone_id, approval)
    elif status in ("denied", "expired"):
        _update_milestone(goal_id, milestone_id, status="failed")


# ═══════════════════════════════════════════════════════════════════════════
#  SCHEDULER-DRIVEN AUTOMATION — registered as scheduler builtins (see the
#  additive edit to services/scheduler.py's _register_default_builtin_tasks)
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
#  SURVIVING A RESTART — the goal follows its task's honest-resume verdict
# ═══════════════════════════════════════════════════════════════════════════
# A milestone's work runs as one background task per attempt. When Friday
# stops (a crash, a reboot, an update) the task journal marks the task
# interrupted, and services/task_resume decides, with rules that already
# exist, whether it may continue: a step whose outcome is unknown (a send, a
# purchase, any write) is never repeated without the owner. The goal never
# second-guesses that. On the next start it reads what its task became:
#
#   no task was ever started  -> nothing ran; the milestone is pending again
#   the task finished         -> its result is CHECKED, never redone
#   the task is running again -> (resumed safely) the goal waits for it
#   the task failed           -> a typed blocker for the owner, nothing re-run
#   the task is interrupted   -> a typed blocker that says why, nothing re-run;
#                                if the owner resumes the task, the goal picks
#                                its result up on the next tick (adopt_resumed)
#
# A fresh start of a milestone whose earlier attempt had begun is the owner's
# call alone: restart_milestone(..., confirm_unknown_outcome=True).

#: Blocker types that only the owner (or the task they resume) can clear.
_OWNER_BLOCKERS = ("interrupted", "run_failed")


def _note_run(goal_id: Optional[str], milestone_id: Optional[str], **fields) -> None:
    """Merge fields into the milestone's run record, durably."""
    if not goal_id or not milestone_id:
        return

    def _fn(goal):
        m = _find_milestone(goal, milestone_id)
        if m is not None:
            run = dict(m.get("run") or {})
            run.update(fields)
            m["run"] = run
        return goal
    try:
        _mutate_goal(goal_id, _fn)
    except Exception as e:
        _log.warning("goal %s: could not record its run: %s", goal_id, e)


def _closed_run(goal_id: str, milestone_id: str) -> Dict[str, Any]:
    """Fields that end the milestone's current run: it moves to last_run."""
    goal = get_goal(goal_id) or {}
    m = _find_milestone(goal, milestone_id) or {}
    run = m.get("run")
    out: Dict[str, Any] = {"run": None}
    if run:
        out["last_run"] = dict(run, ended_at=time.time())
    return out


def _run_key(goal_id: str, milestone_id: str) -> str:
    return f"{goal_id}:{milestone_id}"


def _live_states(milestone: Dict[str, Any]) -> bool:
    """The milestone says work is under way (not waiting on an owner card)."""
    status = milestone.get("status")
    if status == "verifying" and milestone.get("approval_id"):
        return False                      # an owner card holds it, and survives
    return status in ("in_progress", "verifying")


def _orphaned_run(milestone: Dict[str, Any], goal_id: Optional[str] = None) -> bool:
    """True when the milestone says work is under way but no attempt of this
    process owns it: it was left by an earlier process."""
    if not _live_states(milestone):
        return False
    if (milestone.get("run") or {}).get("boot") == BOOT_ID:
        return False
    if goal_id is not None:
        with _RUNNING_MILESTONES_LOCK:
            if _run_key(goal_id, milestone.get("milestone_id") or "") in _RUNNING_MILESTONES:
                return False
    return True


def _claim(goal_id: str, milestone_id: str, *, force: bool = False) -> Optional[Dict[str, Any]]:
    """Atomically take an orphaned run for this process: the check and the
    write happen under one lock, so two settlers (the boot pass, a tick, a
    run request) can never both act on the same run. Returns the run record
    as it was, or None when there is nothing to settle (or someone else
    already took it)."""
    taken: Dict[str, Any] = {}

    def _fn(goal):
        m = _find_milestone(goal, milestone_id)
        if m is None:
            return goal
        if force:
            if not _live_states(m):
                return goal
        elif not _orphaned_run(m, goal_id):
            return goal
        run = dict(m.get("run") or {})
        taken["run"] = dict(run)
        taken["had_run"] = bool(m.get("run"))
        m["run"] = dict(run, boot=BOOT_ID, settling=uuid.uuid4().hex)
        taken["token"] = m["run"]["settling"]
        return goal
    _mutate_goal(goal_id, _fn)
    return taken or None


def _successor(tid: str) -> str:
    """The task that continues `tid` when it was re-queued at boot (a task
    waiting for a local seat is re-admitted under a new id and the old one
    marked superseded; services/reconcile.py records the new id)."""
    try:
        from agent_friday.services import task_journal as tj
        for _ in range(3):
            nxt = None
            for ev in reversed(tj.read(tid)):
                if ev.get("kind") == "decision" and ev.get("point") == "readmit" and ev.get("chosen"):
                    nxt = str(ev["chosen"])
                    break
            if not nxt:
                return tid
            tid = nxt
    except Exception:
        pass
    return tid


def _task_now(tid: str) -> Tuple[str, str, str]:
    """(task id, outcome, result) following a re-queue to its successor."""
    status, result = _task_status(tid)
    if str(status or "") == "superseded":
        nxt = _successor(tid)
        if nxt != tid:
            tid = nxt
            status, result = _task_status(tid)
    return tid, _task_outcome(status), result


def _block(goal_id: str, milestone_id: str, blocker: Dict[str, Any], *, note: str) -> None:
    goal = get_goal(goal_id)
    milestone = _find_milestone(goal or {}, milestone_id)
    if goal is None or milestone is None:
        return
    blocker = dict(blocker, at=_now_iso())
    _update_milestone(goal_id, milestone_id, status="blocked", blocker=blocker,
                      **_closed_run(goal_id, milestone_id))
    _append_receipt(goal_id, _make_receipt(goal, milestone, kind="milestone_interrupted",
                                           verdict=None, cost_mψ=0, note=note))


def _interrupted_words(name: str, verdict: Dict[str, Any], outcome: str) -> str:
    """Plain words for an interrupted milestone. The task-resume reason names
    only tools and states (never content); it is still bounded here."""
    reason = str(verdict.get("reason") or "").strip().rstrip(".")[:160]
    if outcome == "missing":
        return (f"Friday stopped while “{name}” was running, and I can't tell whether its work "
                "had started or what it finished. Nothing was run again. Start the milestone "
                "again only if you're sure nothing needs checking first.")
    if outcome == "still_running":
        return (f"The task for “{name}” was still running when the milestone's time ran out. "
                "Nothing was run again; Friday checks its result if it finishes.")
    if verdict.get("resumable") and verdict.get("needs_confirmation"):
        return (f"Friday stopped while “{name}” was running, and one of its steps may or may "
                f"not have happened{(' (' + reason + ')') if reason else ''}. Nothing was "
                "repeated. Check the task in Tasks: resume it there, or start the milestone again.")
    if verdict.get("resumable"):
        return (f"Friday stopped while “{name}” was running. Its task can pick up where it "
                "left off without repeating anything: press Resume on it in Tasks.")
    return (f"Friday stopped while “{name}” was running, and its task can't pick up again"
            f"{(' (' + reason + ')') if reason else ''}. Nothing was repeated. You can start "
            "the milestone again.")


def _reconcile_milestone(goal_id: str, milestone_id: str, *, background: bool = True,
                         force: bool = False) -> str:
    """Settle one milestone whose run an earlier process left behind. Returns
    what it became: 'pending', 'adopted', 'blocked' or 'unchanged'. Starts no
    new work: the only thing it may start is the CHECK of a finished task's
    result, or waiting on a task the honest-resume rules already resumed."""
    taken = _claim(goal_id, milestone_id, force=force)
    if not taken:
        return "unchanged"
    goal = get_goal(goal_id)
    m = _find_milestone(goal or {}, milestone_id)
    if goal is None or m is None:
        return "unchanged"
    run = taken["run"]
    name = m.get("name") or "this milestone"
    tid = run.get("task_id")
    if not tid and taken["had_run"] and run.get("stage") != "dispatching":
        # The run record says the work was never handed to a task: the gate
        # and the budget had run, nothing else. Nothing ran, so it can run
        # again; its unspent reservation is given back.
        _update_milestone(goal_id, milestone_id, status="pending", blocker=None,
                          **_closed_run(goal_id, milestone_id))
        _release_goal_budget(goal_id, int(run.get("reserved_mψ") or 0))
        _append_receipt(goal_id, _make_receipt(
            goal, m, kind="milestone_interrupted_before_work", verdict=None, cost_mψ=0,
            note="Friday stopped before this milestone's work started; nothing ran, "
                 "so it runs again when it is next due."))
        return "pending"
    if not tid:
        # A run with no record (written before records existed) or one cut
        # off as it was being handed to its task: what it did is unknown.
        _block(goal_id, milestone_id, {
            "type": "interrupted", "task_id": None, "needs_confirmation": True,
            "task_resumable": False, "task_needs_confirmation": False,
            "text": _interrupted_words(name, {}, "missing")},
            note="interrupted by a restart; its task is unknown; nothing was re-run")
        return "blocked"
    tid, outcome, _result = _task_now(tid)
    if outcome in ("running", "done"):
        token = uuid.uuid4().hex
        _update_milestone(goal_id, milestone_id, status="in_progress", blocker=None,
                          run=dict(run, boot=BOOT_ID, task_id=tid, adopted=token))
        _adopt(goal_id, milestone_id, tid, token, background=background)
        return "adopted"
    if outcome == "failed":
        _block(goal_id, milestone_id, {
            "type": "run_failed", "task_id": tid, "needs_confirmation": True,
            "text": (f"Friday stopped while “{name}” was running, and its task then ended "
                     "with an error. Nothing was run again. Look at the task in Tasks; "
                     "start the milestone again only when you're ready.")},
            note="interrupted by a restart; its task ended with an error; nothing was re-run")
        return "blocked"
    verdict: Dict[str, Any] = {}
    if outcome == "interrupted":
        try:
            from agent_friday.services import task_resume
            verdict = task_resume.resumability(tid) or {}
        except Exception:
            verdict = {}
    _block(goal_id, milestone_id, {
        "type": "interrupted", "task_id": tid, "needs_confirmation": True,
        "task_resumable": bool(verdict.get("resumable")),
        "task_needs_confirmation": bool(verdict.get("needs_confirmation")),
        "text": _interrupted_words(name, verdict, outcome)},
        note="interrupted by a restart; waiting for the owner; nothing was re-run")
    return "blocked"


def _adopt(goal_id: str, milestone_id: str, tid: str, token: str, *, background: bool) -> None:
    if background:
        threading.Thread(target=_adopt_job, args=(goal_id, milestone_id, tid, token),
                         name=f"goal-adopt-{milestone_id}", daemon=True).start()
    else:
        _adopt_job(goal_id, milestone_id, tid, token)


def _still_adopted(goal_id: str, milestone_id: str, tid: str, token: str) -> Optional[Dict[str, Any]]:
    """The milestone, if this adoption still owns it (nothing else has
    finished, restarted or re-adopted it meanwhile)."""
    goal = get_goal(goal_id)
    m = _find_milestone(goal or {}, milestone_id)
    if goal is None or m is None or m.get("status") != "in_progress":
        return None
    run = m.get("run") or {}
    if run.get("task_id") != tid or run.get("adopted") != token or run.get("boot") != BOOT_ID:
        return None
    return m


@visible_job('Goal milestone (picked up after a restart)', kind='goal_milestone', workspace='goals')
def _adopt_job(goal_id: str, milestone_id: str, tid: str, token: str) -> Dict[str, Any]:
    """Wait for a task the honest-resume rules let continue (or that had
    already finished), then CHECK its result. Never dispatches the work.
    Owns the milestone through its adoption token, not an in-memory key, so
    it can never be shut out and leave the milestone stuck."""
    goal = get_goal(goal_id)
    m = _still_adopted(goal_id, milestone_id, tid, token)
    if goal is None or m is None:
        return {"ok": False, "status": "superseded"}
    timeout = int(goal.get("milestone_timeout_seconds") or DEFAULT_MILESTONE_TIMEOUT_SECONDS)
    try:
        res = _await_task(tid, m, timeout)
        return _settle_adopted(goal_id, milestone_id, tid, token, m, res)
    except Exception as e:
        # A fault inside Friday must never leave an adopted milestone looking
        # busy for good: it waits for the owner, nothing re-run.
        _log.error("goal %s milestone %s: adoption failed inside Friday: %s", goal_id, milestone_id, e)
        try:
            if _still_adopted(goal_id, milestone_id, tid, token) is not None:
                _block(goal_id, milestone_id, {
                    "type": "run_failed", "task_id": tid, "needs_confirmation": True,
                    "text": (f"After a restart, checking “{m.get('name')}” stopped because of a fault "
                             "inside Friday. Nothing was run again. Start it again when you're ready.")},
                    note="adoption stopped by a fault inside Friday; nothing was re-run")
        except Exception:
            pass
        return {"ok": False, "status": "error"}


def _settle_adopted(goal_id: str, milestone_id: str, tid: str, token: str,
                    m: Dict[str, Any], res: Dict[str, Any]) -> Dict[str, Any]:
    with _ADOPT_LOCK:
        if _still_adopted(goal_id, milestone_id, tid, token) is None:
            return {"ok": False, "status": "superseded"}
        if res.get("ok"):
            return _finish_adopted(goal_id, milestone_id, res.get("text") or "", tid)
        name = m.get("name") or "this milestone"
        if res.get("interrupted"):
            _reconcile_milestone(goal_id, milestone_id, background=True, force=True)
            return {"ok": False, "status": "interrupted"}
        if "timed out" in str(res.get("error") or ""):
            _block(goal_id, milestone_id, {
                "type": "interrupted", "task_id": tid, "needs_confirmation": True,
                "task_resumable": False, "task_needs_confirmation": False,
                "text": _interrupted_words(name, {}, "still_running")},
                note="picked up after a restart; its task outlasted the milestone's time; nothing was re-run")
            return {"ok": False, "status": "blocked"}
        _block(goal_id, milestone_id, {
            "type": "run_failed", "task_id": tid, "needs_confirmation": True,
            "text": (f"After a restart, the task for “{name}” ended without a result. "
                     "Nothing was run again. Look at the task in Tasks.")},
            note="picked up after a restart; its task did not finish; nothing was re-run")
        return {"ok": False, "status": "blocked"}


#: One adoption decides at a time (a check and its write), so two can never
#: both judge the same finished task.
_ADOPT_LOCK = threading.RLock()


def _finish_adopted(goal_id: str, milestone_id: str, text: str, tid: str) -> Dict[str, Any]:
    """Judge a finished task's result exactly as a live attempt is judged,
    without running the work again. A failed check escalates to the owner
    (no repair attempt: a repair would be new work the owner did not see)."""
    goal = get_goal(goal_id)
    milestone = _find_milestone(goal or {}, milestone_id)
    if goal is None or milestone is None:
        return {"ok": False, "error": "milestone not found"}
    criteria_text = (milestone.get("success_criteria") or goal.get("success_criteria") or "").strip()
    action_description = (f"{milestone.get('name')} — {goal.get('description') or goal.get('title')}"
                          + (f" — success criteria: {criteria_text}" if criteria_text else ""))
    note = f"picked up after a restart: task {tid} had finished; its result was checked, not redone"
    if (goal.get("verification_mode") or "auto") == "manual":
        appr = approvals.create_approval(
            kind="goal_milestone_confirm", subject_type="goal_milestone",
            subject_id=f"{goal_id}:{milestone_id}:confirm",
            title=f"Confirm milestone: {milestone.get('name')}",
            description=text[:1000], action_description=action_description,
            force_gate=True, cost_estimate_mψ=0, interest_model=_safe_interest_model(),
            payload={"draft_text": text[:4000]},
        )
        _update_milestone(goal_id, milestone_id, status="verifying",
                          approval_id=appr.get("approval_id"), artifacts=[text[:2000]],
                          **_closed_run(goal_id, milestone_id))
        return {"ok": False, "status": "pending_manual_confirmation",
                "approval_id": appr.get("approval_id")}
    verdict = qa_gates.evaluate_text(text, criteria_text, workspace=f"goal:{goal_id}")
    if verdict.get("passed"):
        _update_milestone(goal_id, milestone_id, status="done", done_at=_now_iso(),
                          verification=verdict, artifacts=[text[:2000]],
                          **_closed_run(goal_id, milestone_id))
        receipt = _make_receipt(goal, milestone, kind="milestone_complete", verdict=verdict,
                                cost_mψ=0, artifact_text=text, note=note)
        _append_receipt(goal_id, receipt)
        _log_work_entry(goal, milestone, verdict, 0, status="COMPLETED", receipt=receipt)
        _maybe_complete_goal(goal_id)
        return {"ok": True, "status": "done", "receipt": receipt}
    # The owner's card first, then the state that points at it: a milestone
    # never says "waits for you" without the card it waits on.
    appr = approvals.create_approval(
        kind="goal_milestone_escalation", subject_type="goal_milestone",
        subject_id=f"{goal_id}:{milestone_id}:escalation",
        title=f"After a restart, the result did not pass its check: {milestone.get('name')}",
        description=(verdict or {}).get("critique", "") or "verification failed",
        action_description=action_description,
        force_gate=True, cost_estimate_mψ=0, interest_model=_safe_interest_model(),
        payload={"last_text": text[:4000], "verdict": verdict},
    )
    _update_milestone(goal_id, milestone_id, status="escalated", verification=verdict,
                      artifacts=[text[:2000]] if text else [],
                      approval_id=appr.get("approval_id"),
                      **_closed_run(goal_id, milestone_id))
    receipt = _make_receipt(goal, milestone, kind="verification_failed_escalated",
                            verdict=verdict, cost_mψ=0, artifact_text=text,
                            note=note + "; the result did not pass its check")
    _append_receipt(goal_id, receipt)
    return {"ok": False, "status": "escalated", "approval_id": appr.get("approval_id")}


def adopt_resumed(*, background: bool = True) -> List[Dict[str, Any]]:
    """Every scheduler tick: settle runs an earlier process left, and pick up
    the result of any interrupted task the owner has since resumed. Starts no
    milestone work of its own."""
    out: List[Dict[str, Any]] = []
    for g in list_goals():
        if g.get("status") in TERMINAL_STATUSES:
            continue
        gid = g.get("goal_id")
        for m in g.get("milestones") or []:
            mid = m.get("milestone_id")
            if not mid:
                continue
            try:
                if _orphaned_run(m, gid):
                    result = _reconcile_milestone(gid, mid, background=background)
                    if result != "unchanged":
                        out.append({"goal_id": gid, "milestone_id": mid, "result": result})
                    continue
                blocker = m.get("blocker") or {}
                tid = blocker.get("task_id")
                if m.get("status") != "blocked" or blocker.get("type") != "interrupted" or not tid:
                    continue
                tid, outcome, _r = _task_now(tid)
                if outcome in ("running", "done"):
                    token = uuid.uuid4().hex
                    claimed: Dict[str, bool] = {}

                    def _fn(goal, _tid=tid, _token=token, _mid=mid, _blocker=blocker):
                        mm = _find_milestone(goal, _mid)
                        if mm is None or mm.get("status") != "blocked" or mm.get("blocker") != _blocker:
                            return goal
                        mm.update(status="in_progress", blocker=None,
                                  run={"boot": BOOT_ID, "task_id": _tid, "adopted": _token,
                                       "started_at": time.time()})
                        claimed["ok"] = True
                        return goal
                    _mutate_goal(gid, _fn)
                    if claimed:
                        _adopt(gid, mid, tid, token, background=background)
                        out.append({"goal_id": gid, "milestone_id": mid, "result": "adopted"})
                elif outcome == "failed":
                    _update_milestone(gid, mid, blocker=dict(
                        blocker, type="run_failed", task_id=tid,
                        text=(f"The task for “{m.get('name')}” ended with an error. "
                              "Nothing was run again.")))
                    out.append({"goal_id": gid, "milestone_id": mid, "result": "blocked"})
            except Exception as e:
                _log.warning("goal %s milestone %s: could not settle after restart: %s", gid, mid, e)
    return out


def reconcile_on_boot(*, background: bool = True) -> Dict[str, Any]:
    """Called once at server start, after the task journal has marked and
    (where safe) resumed the previous process's tasks. Never raises. What it
    finds is shown on Home (services/desktop_card_sources, the goal source)."""
    try:
        settled = adopt_resumed(background=background)
    except Exception as e:
        _log.error("goal reconciliation failed: %s", e)
        settled = []
    unreadable = []
    try:
        unreadable = unreadable_goals()
    except Exception:
        pass
    return {"settled": settled, "unreadable": unreadable}


def restart_milestone(goal_id: str, milestone_id: str, *,
                      confirm_unknown_outcome: bool = False) -> Dict[str, Any]:
    """The owner starts a stopped milestone again. Its earlier attempt had
    begun, so what that attempt did (a send, a purchase, a write) cannot be
    known from here: the owner must say so explicitly. Refused while the
    task can still finish on its own."""
    goal = get_goal(goal_id)
    m = _find_milestone(goal or {}, milestone_id)
    if goal is None or m is None:
        return {"ok": False, "status": "not_found", "error": "milestone not found"}
    blocker = m.get("blocker") or {}
    if m.get("status") != "blocked" or blocker.get("type") not in _OWNER_BLOCKERS:
        return {"ok": False, "status": "not_stopped",
                "error": "this milestone was not stopped by a restart or a failed run"}
    tid = blocker.get("task_id")
    outcome = _task_now(tid)[1] if tid else "missing"
    if outcome == "running" or (outcome == "done" and blocker.get("type") == "interrupted"):
        # Its task is going again, or finished and is about to be checked
        # (adopt_resumed picks up an interrupted blocker's finished task).
        return {"ok": False, "status": "task_continuing",
                "error": "its task is running again or has finished; Friday will check its result"}
    if blocker.get("needs_confirmation", True) and not confirm_unknown_outcome:
        return {"ok": False, "status": "needs_confirmation", "blocker": blocker,
                "error": ("The earlier attempt had started, so some of its steps may already "
                          "have happened. Confirm to start it again anyway.")}
    released: Dict[str, bool] = {}

    def _fn(g):
        mm = _find_milestone(g, milestone_id)
        if mm is None or mm.get("status") != "blocked" or mm.get("blocker") != blocker:
            return g
        mm.update(status="pending", blocker=None, last_blocker=blocker)
        released["ok"] = True
        return g
    _mutate_goal(goal_id, _fn)
    if not released:
        return {"ok": False, "status": "changed", "error": "the milestone changed meanwhile; look again"}
    _append_receipt(goal_id, _make_receipt(
        goal, m, kind="milestone_restarted_by_owner", verdict=None, cost_mψ=0,
        note=("owner started it again after a stop"
              + ("; the owner confirmed the earlier attempt's outcome was unknown"
                 if confirm_unknown_outcome else ""))))
    result = run_milestone(goal_id, milestone_id)
    return {"ok": True, "status": "restarted", "run": result}


def blocker_words(milestone: Dict[str, Any]) -> str:
    """One plain sentence about what a milestone waits on, or ""."""
    b = milestone.get("blocker") or {}
    if b.get("text"):
        return str(b["text"])
    status = milestone.get("status")
    if status == "blocked" and milestone.get("approval_id"):
        return f"“{milestone.get('name')}” waits for your answer on its card."
    if status == "escalated":
        return f"“{milestone.get('name')}” did not pass its check and waits for you."
    if status == "verifying" and milestone.get("approval_id"):
        return f"“{milestone.get('name')}” is done and waits for you to confirm it."
    return ""


def run_due_milestones(*, max_per_tick: int = 5) -> Dict[str, Any]:
    """Scan every ACTIVE goal for milestones whose `due` has passed and are
    still 'pending', advancing each one attempt-cycle. Bounded per tick so a
    backlog can't stampede. This — not the on-demand HTTP run endpoint — is
    the primary autonomous execution path ("Friday holds goals over
    weeks"): a goal accrues real progress from the scheduler beat, the same
    way scheduler.py's own agent_prompt jobs already do their recurring
    work unattended."""
    processed: List[Dict[str, Any]] = []
    try:
        settled = adopt_resumed()
    except Exception as e:
        _log.warning("goal tick: settling interrupted milestones failed: %s", e)
        settled = []
    now = time.time()
    for g in list_goals(status="active"):
        if len(processed) >= max_per_tick:
            break
        for m in g.get("milestones") or []:
            if len(processed) >= max_per_tick:
                break
            if m.get("status") != "pending":
                continue
            due = _parse_ts(m.get("due"))
            if due is None or due > now:
                continue
            result = run_milestone(g["goal_id"], m["milestone_id"])
            processed.append({"goal_id": g["goal_id"], "milestone_id": m["milestone_id"],
                              "status": result.get("status")})
    return {"changed": bool(processed or settled),
            "summary": f"advanced {len(processed)} due milestone(s)"
                       + (f"; settled {len(settled)} after a restart" if settled else ""),
            "processed": processed, "settled": settled}


def latest_review() -> Optional[Dict[str, Any]]:
    if not REVIEWS_DIR.exists():
        return None
    files = sorted(REVIEWS_DIR.glob("*.md"))
    if not files:
        return None
    f = files[-1]
    try:
        return {"date": f.stem, "path": str(f), "content": f.read_text(encoding="utf-8")}
    except Exception as e:
        return {"date": f.stem, "path": str(f), "content": "", "error": exception_text(e)}


def run_weekly_review() -> Dict[str, Any]:
    """Aggregate work_log entries by goal_id into a dreams/-style Markdown
    review doc under ~/.friday/goals/reviews/<date>.md. Missed dates are
    written into the review and raised as one rollover approval card per
    goal; the review itself never moves a date. Registered as the weekly
    scheduler builtin 'goals_weekly_review'."""
    active = list_goals(status="active")
    lines = [f"# Weekly Goal Review — {datetime.now().strftime('%Y-%m-%d')}", ""]
    proposed: List[str] = []
    for g in active:
        entries = _work_log_entries_for_goal(g["goal_id"])
        milestones = g.get("milestones") or []
        done = sum(1 for m in milestones if m.get("status") == "done")
        cap = g.get("budget_cap_mψ") or 0
        lines.append(f"## {g.get('title')} ({g.get('goal_id')})")
        lines.append(f"- Progress: {done}/{len(milestones)} milestones done")
        lines.append(f"- Spent: {g.get('spent_mψ', 0)} mψ / cap {cap or '∞'} mψ")
        lines.append(f"- Work-log entries: {len(entries)}")
        overdue = [m for m in milestones if m.get("status") != "done" and _is_overdue(m.get("due"))]
        deadline_missed = _is_overdue(g.get("deadline"))
        if overdue or deadline_missed:
            missed = f"{len(overdue)} overdue milestone(s)"
            if deadline_missed:
                missed += " and an overdue goal deadline"
            card = propose_deadline_rollover(g["goal_id"])
            if card and card.get("status") == "pending":
                proposed.append(g["goal_id"])
                lines.append(f"- {missed.capitalize()}. New dates await your approval "
                             f"({card.get('approval_id')}); nothing was moved.")
            else:
                lines.append(f"- {missed.capitalize()}. Dates left as they are.")
        lines.append("")
    doc = "\n".join(lines)
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEWS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    path.write_text(doc, encoding="utf-8")
    return {"changed": bool(active),
            "summary": (f"Reviewed {len(active)} active goal(s); "
                        f"{len(proposed)} rollover(s) await approval."),
            "path": str(path), "rolled_over": [], "rollover_proposed": proposed}


# ═══════════════════════════════════════════════════════════════════════════
#  INTEREST MODEL PROVIDER — "Register the goals provider into interest_model
#  at service init" (AUTONOMY_SPEC A3, item 7)
# ═══════════════════════════════════════════════════════════════════════════

def _active_goals_for_interest_model() -> List[Dict[str, Any]]:
    """Shaped for interest_model.assemble_interest_model()'s "goals" key:
    [{goal_id, title, constraints:[...]}]. `constraints` is empty unless a
    goal explicitly records prohibition-style text in its
    dissent_constraints list — dissent_gate only ever acts on constraint
    text actually PHRASED as a prohibition (see dissent_gate.py /
    interest_model.py docstrings), so an empty list here is the normal,
    expected case, not a gap."""
    out: List[Dict[str, Any]] = []
    try:
        for g in list_goals(status="active"):
            out.append({
                "goal_id": g.get("goal_id"),
                "title": g.get("title"),
                "constraints": list(g.get("dissent_constraints") or []),
            })
    except Exception as e:
        _log.warning("active goals lookup for interest_model failed: %s", e)
    return out


def _register_with_interest_model() -> None:
    try:
        from agent_friday.services import interest_model as im
        im.register_goals_provider(_active_goals_for_interest_model)
    except Exception as e:
        _log.warning("interest_model goals-provider registration failed: %s", e)


# ── Module init: register decision hooks + the interest_model provider ──────
approvals.register_decision_hook("goal_milestone", _on_milestone_gate_decided)
approvals.register_decision_hook("goal_milestone_escalation", _on_escalation_decided)
approvals.register_decision_hook("goal_milestone_confirm", _on_confirm_decided)
approvals.register_decision_hook(ROLLOVER_KIND, _on_rollover_decided)
_register_with_interest_model()
