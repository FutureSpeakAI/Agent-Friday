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
                               doing "recurring autonomous work on Stephen's
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
                               rollover), and approvals.expire_stale
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
import threading
import time
import uuid
import hashlib
import hmac as _hmac
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_friday.paths import friday_home
from agent_friday.services import approvals
from agent_friday.services import qa_gates

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

def _goal_path(goal_id: str) -> Path:
    return GOALS_DIR / f"{goal_id}.json"


def _read_goal_file(goal_id: str) -> Optional[Dict[str, Any]]:
    p = _goal_path(goal_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _log.error("goal file %s unreadable/corrupt: %s", goal_id, e)
        return None


def _write_goal_file(goal: Dict[str, Any]) -> None:
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    p = _goal_path(goal["goal_id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(goal, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)  # atomic on the same filesystem — no partial-write corruption


def list_goals(*, status: Optional[str] = None) -> List[Dict[str, Any]]:
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(GOALS_DIR.glob("goal_*.json")):
        try:
            g = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if status is None or g.get("status") == status:
            out.append(g)
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
        raise ValueError(f"unknown status: {new_status!r}")

    def _fn(goal):
        cur = goal.get("status")
        if new_status == cur:
            return goal
        allowed = _ALLOWED_TRANSITIONS.get(cur, set())
        if new_status not in allowed:
            raise ValueError(f"illegal transition {cur!r} -> {new_status!r}")
        goal["status"] = new_status
        goal.setdefault("history", []).append({
            "at": _now_iso(), "from": cur, "to": new_status, "reason": reason,
        })
        return goal

    goal = _mutate_goal(goal_id, _fn)
    if goal is None:
        raise KeyError(f"no such goal: {goal_id}")
    return goal


def rollover_incomplete_milestones(goal_id: str, *, extend_seconds: int = 7 * 86400
                                    ) -> Optional[Dict[str, Any]]:
    """Push `due` (and, if also overdue, the goal's own `deadline`) forward
    by whole extend_seconds increments for every not-yet-done milestone that
    is overdue. Used by run_weekly_review — "incomplete goals roll over with
    adjusted deadlines." A no-op (no history entry) when nothing is overdue."""
    def _fn(goal):
        now = time.time()
        changed = False
        for m in goal.get("milestones") or []:
            if m.get("status") == "done":
                continue
            due = _parse_ts(m.get("due"))
            if due is not None and due < now:
                new_due = due
                while new_due < now:
                    new_due += extend_seconds
                m["due"] = _iso_from_ts(new_due)
                changed = True
        deadline_ts = _parse_ts(goal.get("deadline"))
        if (deadline_ts is not None and deadline_ts < now
                and goal.get("status") not in TERMINAL_STATUSES):
            new_deadline = deadline_ts
            while new_deadline < now:
                new_deadline += extend_seconds
            goal["deadline"] = _iso_from_ts(new_deadline)
            changed = True
        if changed:
            goal["rollovers"] = int(goal.get("rollovers") or 0) + 1
            goal.setdefault("history", []).append({
                "at": _now_iso(), "from": goal.get("status"), "to": goal.get("status"),
                "reason": "rollover: overdue milestone/goal deadlines adjusted",
            })
        return goal
    return _mutate_goal(goal_id, _fn)


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
        return {"ok": False, "text": "", "cost_mψ": 0, "error": f"agent unavailable: {e}"}

    hard_gate_approved = bool(milestone.get("_hard_gate_approved"))
    try:
        tid = _spawn_task(
            f"Goal · {milestone.get('name')}", prompt,
            description=f"goal:{goal.get('goal_id')}:milestone:{milestone.get('milestone_id')}",
            orb_icon="🎯",
            scope=None if hard_gate_approved else "goal-milestone",
        )
    except Exception as e:
        # _spawn_task fails CLOSED when a required scope can't be applied —
        # never fall back to spawning unscoped. See its own docstring.
        return {"ok": False, "text": "", "cost_mψ": 0,
                "error": f"could not safely dispatch milestone: {e}"}
    timeout = int(goal.get("milestone_timeout_seconds") or DEFAULT_MILESTONE_TIMEOUT_SECONDS)
    deadline = time.time() + timeout
    terminal = {"complete", "completed", "completed_unverified", "failed",
               "timeout", "error", "cancelled"}
    while time.time() < deadline:
        snap = _task_snapshot(tid) or {}
        if snap.get("status") in terminal:
            if snap.get("status") in ("failed", "error"):
                return {"ok": False, "text": "", "cost_mψ": 0,
                        "error": str(snap.get("result") or "agent task failed"),
                        "task_id": tid}
            return {"ok": True, "text": str(snap.get("result") or ""),
                    "cost_mψ": int(milestone.get("cost_estimate_mψ") or DEFAULT_MILESTONE_COST_MΨ),
                    "task_id": tid}
        time.sleep(1)
    return {"ok": False, "text": "", "cost_mψ": 0,
            "error": "milestone execution timed out", "task_id": tid}


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
        return {"ok": False, "text": "", "cost_mψ": 0, "error": f"executor raised: {e}"}


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

    _update_milestone(goal_id, milestone_id, status="in_progress")

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
        work = _execute_milestone_work(goal, milestone, critique_hint=critique_hint)
        total_cost += int(work.get("cost_mψ") or 0)

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
                             approval_id=appr.get("approval_id"), artifacts=[last_text[:2000]])
            return {"ok": False, "status": "pending_manual_confirmation",
                    "approval_id": appr.get("approval_id")}

        criteria = milestone.get("success_criteria") or goal.get("success_criteria") or ""
        verdict = qa_gates.evaluate_text(last_text, criteria, workspace=f"goal:{goal_id}")
        last_verdict = verdict

        if verdict.get("passed"):
            _release_goal_budget(goal_id, max(0, reserved_total - total_cost))
            now = _now_iso()
            _update_milestone(goal_id, milestone_id, status="done", done_at=now,
                             verification=verdict, artifacts=[last_text[:2000]])
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
                     artifacts=[last_text[:2000]] if last_text else [])
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

def run_due_milestones(*, max_per_tick: int = 5) -> Dict[str, Any]:
    """Scan every ACTIVE goal for milestones whose `due` has passed and are
    still 'pending', advancing each one attempt-cycle. Bounded per tick so a
    backlog can't stampede. This — not the on-demand HTTP run endpoint — is
    the primary autonomous execution path ("Friday holds goals over
    weeks"): a goal accrues real progress from the scheduler beat, the same
    way scheduler.py's own agent_prompt jobs already do their recurring
    work unattended."""
    processed: List[Dict[str, Any]] = []
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
    return {"changed": bool(processed),
            "summary": f"advanced {len(processed)} due milestone(s)",
            "processed": processed}


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
        return {"date": f.stem, "path": str(f), "content": "", "error": str(e)}


def run_weekly_review() -> Dict[str, Any]:
    """Aggregate work_log entries by goal_id into a dreams/-style Markdown
    review doc under ~/.friday/goals/reviews/<date>.md, and roll over any
    still-incomplete goal's deadlines/milestones. Registered as the weekly
    scheduler builtin 'goals_weekly_review'."""
    active = list_goals(status="active")
    lines = [f"# Weekly Goal Review — {datetime.now().strftime('%Y-%m-%d')}", ""]
    rolled: List[str] = []
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
        if overdue:
            rollover_incomplete_milestones(g["goal_id"])
            rolled.append(g["goal_id"])
            lines.append(f"- Rolled over {len(overdue)} overdue milestone(s) with adjusted deadlines.")
        lines.append("")
    doc = "\n".join(lines)
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEWS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    path.write_text(doc, encoding="utf-8")
    return {"changed": bool(active),
            "summary": f"Reviewed {len(active)} active goal(s); rolled over {len(rolled)}.",
            "path": str(path), "rolled_over": rolled}


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
_register_with_interest_model()
