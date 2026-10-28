"""Loop no-progress head, in shadow: would a loop-stop have fired, and where?

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md (DM5; owner decisions
S1 to S9, 2026-10-24). One decision head (services/decision_heads):

  loop.no_progress   choice, stop_only   progressing | stalled | unsure

It is STOP-ONLY: in act mode (a later phase, Stephen's press only) it may end a
loop with a typed blocker; it can never say a task is done (completion stays with
receipts and evaluators, NS-17.12-1). It ships in SHADOW: after an agent turn
ends, its tool trace is replayed through the head on the head's own worker, and
one Laya receipt records whether, and at which step, the head would have
stopped it, next to whether LoopGuard did. The turn is long over; nothing waits.

The features are the ones benched in ftv/decision_eval (E2), with the rules
frozen there (rules_sha256 in the bench report): over the last WINDOW steps, no
new information and no write, or the same error class three times. Only what
the loop itself can see is used: tool names, arguments, results and errors.
Nothing here learns from the owner's turns, and the receipt holds counts and
labels, never a word.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

_log = logging.getLogger("friday.loop_heads")

HEAD = "loop.no_progress"
WINDOW = 4
_NULL = re.compile(r"^(no (new|matching) results|nothing else|no results|empty)\b", re.I)
_ERROR_CLASSES = [("permission", re.compile(r"permission|denied|forbidden|not allowed|rights", re.I)),
                  ("not_found", re.compile(r"not found|no such|missing|does not exist|404", re.I)),
                  ("timeout", re.compile(r"time(d)? ?out|did not answer", re.I))]
MAX_STEPS = 200


def _is_write(tool) -> bool:
    """A step that changed something counts as progress. Only a tool the governed
    table KNOWS (a ring entry or a reviewed annotation override) and marks as
    not read-only counts. An unknown name is not a write here: the gate treats
    the unknown as a writer for safety, but for a stop-only head that would hide
    every stall behind any unfamiliar tool (review finding 2)."""
    name = str(tool or "")
    if not name:
        return False
    try:
        from agent_friday.services import agent, tool_catalogue
        known = name in agent.TOOL_RINGS or name in (
            (tool_catalogue.load_groups().get("annotation_overrides") or {}))
        if not known:
            return False
        return not bool(tool_catalogue.annotations(name).get("readOnlyHint"))
    except Exception:  # noqa: BLE001
        return False
_MODE_TTL = 3.0
_mode_cache: Dict[str, tuple] = {}
RUN_INLINE = False


def _sentences(text: str) -> List[str]:
    return [s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def _err_class(e) -> Optional[str]:
    if not e:
        return None
    for name, rx in _ERROR_CLASSES:
        if rx.search(str(e)):
            return name
    return "other:" + str(e).strip().lower()[:24]


class Features:
    """What the loop can observe after each step (the bench's frozen features)."""

    def __init__(self):
        self.seen: set = set()
        self.keys: List[str] = []
        self.hist: List[dict] = []

    def push(self, step: dict) -> dict:
        if step.get("placeholder"):
            # A screenshot handed to the model, a held result: nothing readable came
            # back, so the step is neither progress nor a stall. It stays out of the window.
            return self._window()
        ok = bool(step.get("ok", True))
        sents = [s for s in _sentences(str(step.get("result") or "")) if not _NULL.match(s)]
        fresh = [s for s in sents if s not in self.seen]
        self.seen.update(sents)
        novelty = (len(fresh) / len(sents)) if (ok and sents) else 0.0
        key = "%s(%s)" % (step.get("tool"), json.dumps(step.get("args"), sort_keys=True, default=str))
        repeat = self.keys.count(key)
        self.keys.append(key)
        self.hist.append({"novelty": novelty, "repeat": repeat, "err": _err_class(step.get("error")),
                          "write": ok and _is_write(step.get("tool"))})
        return self._window()

    def _window(self) -> dict:
        w = self.hist[-WINDOW:]
        errs = [r["err"] for r in w if r["err"]]
        return {"full": len(w) == WINDOW, "nov_sum": sum(r["novelty"] for r in w),
                "write": any(r["write"] for r in w),
                "err_same": max((errs.count(e) for e in set(errs)), default=0)}


def stalled(f: dict) -> bool:
    if not f["full"]:
        return False
    return (f["nov_sum"] == 0.0 and not f["write"]) or f["err_same"] >= 3


def first_stall(steps: Sequence[dict]) -> Optional[int]:
    """The index of the first step after which the head says "stalled", or None."""
    feats = Features()
    for i, s in enumerate(list(steps)[:MAX_STEPS]):
        if stalled(feats.push(s)):
            return i
    return None


# --------------------------------------------------------------------------
#  the agent's tool trace -> steps
# --------------------------------------------------------------------------

#: Friday's own failure shapes: a bracketed deny or error sentinel, "Error:" / "ERROR:",
#: the verifier's "INVALID ARGUMENTS". A colon is required, so "Error handling in ..." is a result.
_ERROR_SHAPE = re.compile(r"^\s*(\[[A-Z0-9 _-]*(DENY|DENIED|ERROR|BLOCKED|REFUSED)[^\]]*\]|ERROR:|Error:|"
                          r"error:|INVALID ARGUMENTS)")
_PLACEHOLDER = re.compile(r"^\s*\[(image|screenshot) returned to (the )?model\]|^\s*\[held\b", re.I)


def steps_from_trace(tool_trace: Sequence[dict]) -> List[dict]:
    """The agent's tool_trace rows ({name, input, result}) as steps. A result in
    one of Friday's failure shapes, or a dict carrying "error", is a failed step;
    a placeholder (an image handed to the model, a held result) is marked so it
    counts neither way."""
    out = []
    for row in list(tool_trace or [])[:MAX_STEPS]:
        if not isinstance(row, dict):
            continue
        raw = row.get("result")
        if isinstance(raw, dict) and raw.get("error"):
            out.append({"tool": row.get("name"), "args": row.get("input"), "ok": False,
                        "error": str(raw.get("error"))[:200], "result": ""})
            continue
        res = raw if isinstance(raw, str) else json.dumps(raw, default=str)[:2000]
        if _PLACEHOLDER.match(res or ""):
            out.append({"tool": row.get("name"), "args": row.get("input"), "placeholder": True})
            continue
        failed = bool(_ERROR_SHAPE.match(res or ""))
        out.append({"tool": row.get("name"), "args": row.get("input"), "ok": not failed,
                    "error": res[:200] if failed else None, "result": "" if failed else res[:2000]})
    return out


# --------------------------------------------------------------------------
#  shadow
# --------------------------------------------------------------------------

def _decision() -> Optional[dict]:
    now = time.monotonic()
    hit = _mode_cache.get(HEAD)
    if hit and now - hit[0] < _MODE_TTL:
        return hit[1] or None
    try:
        from agent_friday.services import decision_heads
        eff = decision_heads.effective(HEAD)
    except Exception:  # noqa: BLE001
        eff = None
    _mode_cache[HEAD] = (now, eff or {})
    return eff


def clear_cache() -> None:
    _mode_cache.clear()


def mode() -> str:
    eff = _decision()
    return str(eff.get("mode")) if eff else "off"


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record as _off
        return bool(_off.active())
    except Exception:
        return False


_q: "queue.Queue" = queue.Queue(maxsize=16)
_worker: Optional[threading.Thread] = None
_wlock = threading.Lock()
_counters = {"queued": 0, "dropped": 0, "read": 0, "failed": 0}


def stats() -> dict:
    return dict(_counters)


def _work() -> None:
    while True:
        job = _q.get()
        try:
            job()
        except Exception as e:  # noqa: BLE001
            _counters["failed"] += 1
            _log.debug("loop shadow job failed: %s", e)
        finally:
            _q.task_done()


def _submit(job: Callable[[], None]) -> bool:
    global _worker
    if RUN_INLINE:
        job()
        return True
    with _wlock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="loop-shadow", daemon=True)
            _worker.start()
    try:
        _q.put_nowait(job)
        _counters["queued"] += 1
        return True
    except queue.Full:
        _counters["dropped"] += 1
        return False


def loop_guard_first_fire(steps: Sequence[dict]) -> Optional[int]:
    """Where Friday's shipped LoopGuard (services/turn_budget) fires on these
    steps, replayed: the head's receipt sits beside the incumbent's answer."""
    try:
        from agent_friday.services.turn_budget import LoopGuard
        g = LoopGuard()
        for i, s in enumerate(steps):
            if g.observe(s.get("tool"), s.get("args")):
                return i
    except Exception:  # noqa: BLE001
        pass
    return None


def _generation() -> Optional[int]:
    try:
        from agent_friday.services import off_record as _off
        return int(_off.generation())
    except Exception:
        return None


def shadow_turn(tool_trace: Sequence[dict], *, surface: str = "agent", resumed: int = 0) -> bool:
    """After an agent turn: replay its tool trace through the head and leave one
    receipt. Nothing is changed; the turn has already ended. False when the head
    is off, the owner is off the record, or the turn used fewer than WINDOW tools.
    ``resumed`` rows carried over from an earlier run are not counted again.

    The receipt is written only if the owner is still on the record in the same
    private-memory generation as when the turn ended (services/off_record), so a
    turn that went off the record while it waited leaves nothing (LESSONS 274-284)."""
    rows = list(tool_trace or [])[max(0, int(resumed or 0)):]
    if mode() == "off" or _off_record() or len(rows) < WINDOW:
        return False
    gen = _generation()

    def job():
        if _off_record() or _generation() != gen:
            return
        from agent_friday.services import laya
        steps = steps_from_trace(rows)
        lg = loop_guard_first_fire(steps)
        r = laya.ask({"text": "", "surface": "background",
                      "opts": {"steps": steps, "loop_guard_at": lg, "origin": surface, "log": False}},
                     {HEAD: {"type": "choice", "preset": HEAD}}, "shadow.%s" % HEAD, budget_ms=1000)
        if not _off_record() and _generation() == gen:
            laya.persist_receipt(r.get("receipt_id"))
        _counters["read"] += 1
    return _submit(job)


def drain(timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _q.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return _q.unfinished_tasks == 0


def preset(ctx, spec: dict) -> dict:
    """laya.ask preset "loop.no_progress": stalled (and where) for a replayed trace."""
    steps = list(ctx.opts.get("steps") or [])
    if not steps:
        return {"answer": {"value": "unsure", "p": None, "set": None, "abstain": True}, "layer": "rule",
                "verdict": None, "native": None, "why": "no steps"}
    at = first_stall(steps)
    eff = _decision() or {}
    label = "stalled" if at is not None else "progressing"
    lg = ctx.opts.get("loop_guard_at")
    return {"answer": {"value": label, "p": None, "set": None, "abstain": False},
            "layer": "rule", "verdict": None, "native": at,
            "why": "%s after step %s of %d; LoopGuard %s; %s" % (
                label, "-" if at is None else at + 1, len(steps),
                "after step %d" % (lg + 1) if isinstance(lg, int) else "did not fire",
                eff.get("tag") or "untagged")}
