"""The TurnPlan: what the planner knows about one turn, frozen as a contract.

Spec: lu_features_spec section 9.4 (the planner) and 2.5 (receipts). In
Beta 1.1 wave 1 the plan is computed and recorded in SHADOW ONLY: prompts,
tool surfaces and routing are never read from it. Later packages fill in the
heads that are None here (slices, tool groups, difficulty); the field names
are frozen now so they can.

Rules:

  UNKNOWN IS NONE  A head that did not run is None (or empty), never a
                   guess. A degraded verdict carries no shape, mutation or
                   oos reading, because the stand-in values `reflex_turn`
                   returns for a missed turn are not readings.
  NO CONTENT       A plan holds labels, numbers and names. Never the words of
                   the turn, a tool argument or a slice's text.
  OFF THE RECORD   `record` writes nothing and keeps nothing while
                   `services/off_record` is active; the turn simply has no plan.
  NEVER BLOCKS     `record` and `begin_turn` swallow their own failures.

`current_turn_id()` is the turn the calling thread is running, so the
retrieval ledger, the egress log and the activity ledger can tag their rows
with the same id the turn receipt is keyed by.
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger("friday.turn_plan")

PLAN_VERSION = 1
PLAN_DIRNAME = "turns"
PLAN_NAME = "turn_plans.jsonl"
_KEEP = 256

#: Route proposals the spec allows (section 9.5d). In wave 1 only "reflex" is
#: ever produced, and only as a proposal.
ROUTE_PROPOSALS = ("reflex", "local_fast", "local_brain", "cloud")


@dataclass(frozen=True)
class TurnPlan:
    plan_version: int
    turn_id: str
    shape: Optional[str] = None
    mutation: Optional[str] = None
    oos: Optional[bool] = None
    #: per head: calibrated probability, or None where the head did not run
    confidence: Dict[str, Optional[float]] = field(default_factory=dict)
    #: name -> {"action": include|withhold|summary|stub, "p": float, "reason": str}
    slices: Dict[str, dict] = field(default_factory=dict)
    tool_groups: List[str] = field(default_factory=list)
    resident_tools: List[str] = field(default_factory=list)
    route_proposal: Optional[str] = None
    route_reason: Optional[str] = None
    #: a resolved one-tool reflex (services/laya_resolver), if any
    reflex: Optional[dict] = None
    #: [(tool, args)] read-only prefetch proposals
    prefetch: List[Tuple[str, dict]] = field(default_factory=list)
    effort: Optional[str] = None
    max_tokens: Optional[int] = None
    degraded: bool = False
    degraded_reason: Optional[str] = None
    timings_ms: Dict[str, float] = field(default_factory=dict)
    #: when the plan was made (unix seconds); the receipt orders by it
    ts: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def plan_from_reflex(verdict: Optional[dict], *, turn_id: str,
                     reflex: Optional[dict] = None,
                     ts: Optional[float] = None) -> TurnPlan:
    """A TurnPlan from a `reflex_turn.classify` verdict. Pure.

    Only what the verdict actually says is carried. `reflex_turn` runs the
    shape, mutation and out-of-scope heads: those become `shape`, `mutation`,
    `oos` and their confidences (the OOS head has no probability of its own,
    so its confidence is None). Every other head is left empty.
    """
    v = verdict or {}
    now = time.time() if ts is None else float(ts)
    elapsed = v.get("elapsed_ms")
    timings = {"reflex_turn": float(elapsed)} if isinstance(elapsed, (int, float)) else {}
    if v.get("degraded") or not v:
        reason = str(v.get("reason") or "no verdict")
        # The stand-in values of a missed turn ("brain", "unknown") are not
        # readings of the turn, so none of them enter the plan. `effort` is
        # kept: it is what the brain is in fact given today.
        return TurnPlan(plan_version=PLAN_VERSION, turn_id=str(turn_id),
                        confidence={"shape": None, "mutation": None, "oos": None},
                        effort=v.get("effort"), degraded=True,
                        degraded_reason=reason, timings_ms=timings, ts=now)
    route = v.get("route")
    proposal = "reflex" if route == "reflex" else None
    return TurnPlan(
        plan_version=PLAN_VERSION, turn_id=str(turn_id),
        shape=v.get("shape"), mutation=v.get("mutation"), oos=v.get("oos"),
        confidence={"shape": v.get("shape_conf"),
                    "mutation": v.get("mutation_conf"), "oos": None},
        route_proposal=proposal,
        route_reason=(f"tier 1 route={route}; margin={v.get('margin')}"
                      if route else None),
        reflex=reflex, effort=v.get("effort"), degraded=False,
        timings_ms=timings, ts=now)


# ---------------------------------------------------------------------------
#  THE CURRENT TURN (so other ledgers can tag their rows)
# ---------------------------------------------------------------------------

_current: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "friday_turn_plan_turn_id", default=None)
_current_version: "contextvars.ContextVar[Optional[int]]" = contextvars.ContextVar(
    "friday_turn_plan_version", default=None)


def begin_turn(turn_id: Optional[str]) -> None:
    """Name the turn this thread is running. Off the record: no turn id."""
    try:
        from agent_friday.services import off_record as _off
        if _off.active():
            turn_id = None
    except Exception:
        pass
    _current.set(str(turn_id) if turn_id else None)
    _current_version.set(None)


def current_turn_id() -> Optional[str]:
    return _current.get()


def current_plan_version() -> Optional[int]:
    return _current_version.get()


# ---------------------------------------------------------------------------
#  STORE (metadata only)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_recent: "OrderedDict[str, dict]" = OrderedDict()


def plans_path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / PLAN_DIRNAME / PLAN_NAME


def record(plan: TurnPlan) -> bool:
    """Keep the plan for its turn. Returns False (and writes nothing) off the record."""
    try:
        from agent_friday.services import off_record as _off
        if _off.active():
            return False
    except Exception:
        pass
    try:
        row = plan.to_dict()
        with _lock:
            _recent[plan.turn_id] = row
            while len(_recent) > _KEEP:
                _recent.popitem(last=False)
            path = plans_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
        if _current.get() == plan.turn_id:
            _current_version.set(plan.plan_version)
        return True
    except Exception as e:
        _log.debug("turn plan not recorded: %s", e)
        return False


def get(turn_id: str) -> Optional[dict]:
    """The recorded plan for a turn (newest wins), or None."""
    tid = str(turn_id or "")
    with _lock:
        hit = _recent.get(tid)
    if hit is not None:
        return dict(hit)
    try:
        path = plans_path()
        if not path.exists():
            return None
        found = None
        with path.open(encoding="utf-8") as f:
            for raw in f:
                if tid and tid in raw:
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    if row.get("turn_id") == tid:
                        found = row
        return found
    except Exception:
        return None


def attach_slices(turn_id: str, slices: Dict[str, dict],
                  took_ms: Optional[float] = None) -> bool:
    """Put the slice-need head's reading into a turn's plan (shadow: nothing
    reads it to change a prompt unless the owner opted in; services/slice_head).

    Keeps whatever the plan already holds and replaces only `slices`, so the
    newest preparation of the turn (a fallback leg) wins. Off the record the
    plan is not kept. Never raises.
    """
    try:
        row = get(turn_id)
        if row:
            names = {f for f in TurnPlan.__dataclass_fields__}
            plan = TurnPlan(**{k: v for k, v in row.items() if k in names})
            timings = dict(plan.timings_ms)
        else:
            plan = TurnPlan(plan_version=PLAN_VERSION, turn_id=str(turn_id),
                            ts=time.time())
            timings = {}
        if took_ms is not None:
            timings["slice_head"] = round(float(took_ms), 2)
        return record(replace(plan, slices=dict(slices), timings_ms=timings))
    except Exception as e:
        _log.debug("slices not attached: %s", e)
        return False


def shadow_plan(verdict: Optional[dict], turn_id: str) -> Optional[TurnPlan]:
    """Build and record the plan for a turn from its tier-1 verdict. Never raises."""
    try:
        plan = plan_from_reflex(verdict, turn_id=turn_id)
        record(plan)
        return plan
    except Exception:
        return None
