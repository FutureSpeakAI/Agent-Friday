"""Why did Friday do that? One answer for one action or one task (north star
NS-29.1-1, slice A13).

The owner can always ask, of anything Friday did: what it was, why, which
model, what information, what left this PC, on whose authority, what it cost
and what changed. `explain(action_id=...)` or `explain(task_id=...)` reads
the stores Friday already keeps and gives back one section per question:

  what         the tool, the checkpoint's decision and the outcome word, with
               their times (a task: its actions, tools and outcomes counted,
               and its status code)
  why          the decision's reason as a fixed code (never its text), the
               envelope's origin and risk class, its provenance counts (owner,
               content, sender, model), the checks it named, who chose it
  model        model, provider and seat names from the turn's
               model_invocation rows (activity, by turn id) or, else, the
               task's (activity rows and the task journal's model_call
               events, by task id); `linked_by` says which
  information  the context manifests of the action's sends (and of its turn):
               sensitivity classes, gate actions and part counts, withheld
               count, estimated tokens; the turn's prompt sections counted by
               tier. Never a section's name, a part's content or a destination
  egress       the egress ledger's call rows that name the action (A10), and
               the turn's model calls: counted by channel and result, with
               their call and manifest ids. Never a destination
  authority    the card id (its status, who decided it, the policy versions),
               the grant id (its kind, status and the names of the fields
               that limit it, never their values), whether the request came
               verified from the owner (owner_route), the policy version
  cost         the cost the task journal recorded for the task's model calls;
               a card's cost estimate; else "not recorded" (tokens counted
               are said, never priced here)
  changed      the outcome receipts (keyed ids), changes to the owner's things
               (receipt ids), files made (keyed artifact ids), the task
               journal's receipts and the outcome-memory record id
  links        the unified ledger's trace of the id (A12): family, kind, link
               and ids of each row

Each section says "not recorded" when the stores hold nothing for it, and is
built on its own: one that fails is named with its error type and the others
are still given. Nothing a record says is copied: ids (U._id), names of
models and tools (_name), and fixed codes only; never content, a path, a
recipient, a section name or a destination.

Bounded: one explanation at a time (another waits BUSY_WAIT_S, then Busy);
every read stops at its own deadline (SECTION_BUDGET_S, never past
TOTAL_BUDGET_S from the start), and no section starts once the total is
spent. Files are read from their end (unified_ledger.TAIL_BYTES), a task
journal within unified_ledger's per-task byte and line caps. The links
section is the unified ledger's own trace, under its own lock and budget.

No path is ever built from what a caller sends: the ids are only compared
with values read from the stores, and a task's journal is opened by the name
the disk gives it. Nothing here writes.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from agent_friday.services import unified_ledger as U

NOT_RECORDED = "not recorded"

#: The questions, in the order they are answered.
SECTION_NAMES = ("what", "why", "model", "information", "egress", "authority", "cost",
                 "changed", "links")
SECTION_LABELS = {
    "what": "What", "why": "Why", "model": "Which model", "information": "What information",
    "egress": "What left this PC", "authority": "On whose authority", "cost": "What it cost",
    "changed": "What changed", "links": "Linked records",
}

#: No section starts after this long.
TOTAL_BUDGET_S = 6.0
#: How long one read may take.
SECTION_BUDGET_S = 2.0
#: How long an explanation waits for another to finish before it says Busy.
BUSY_WAIT_S = 3.0
#: The most actions one task's explanation covers (newest first).
MAX_ACTIONS = 50
#: The most rows one read keeps.
MAX_ROWS = 200
#: The most ids listed of one kind.
MAX_IDS = 20
#: The most cards and grants looked up.
MAX_CARDS = 5
MAX_GRANTS = 5
#: The most trace rows listed under links.
LINK_ROWS_MAX = 50

#: A model, provider, seat or tool name: letters, digits and _ . : / + -
#: only (a model may be "vendor/model"), at most 80 characters, never "..",
#: "//" or a leading "/". Anything else is not shown.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+\-]{0,79}")
#: A sensitivity class or gate action as the manifest writes it.
_CLASS_RE = re.compile(r"[A-Za-z0-9_]{1,24}")

#: A gate reason (free text) as a fixed code: the reason's own words are
#: never shown, only which kind of reason it was.
_REASON_CODES = (
    ("held:", "held"), ("not allowed", "not_allowed"),
    ("pre-approved send", "pre_approved_send"), ("pre-approved grant", "pre_approved_grant"),
    ("its own approval card is the gate", "own_card_is_the_gate"),
    ("approved on a card", "approved_on_card"), ("approved card", "approved_card"),
    ("declined on a card", "declined_on_card"), ("waiting for the owner", "waiting_for_owner"),
    ("governance check failed", "check_failed"),
    ("the signed receipt could not be written", "receipt_failed"),
    ("already done", "already_done"),
)

#: Where a request came from, as the envelope names it (action_gate.ORIGINS).
_ORIGINS = ("chat", "voice", "task", "schedule", "channel", "card", "owner_route", "route", "other")
_PROVENANCE_KEYS = ("owner", "content", "sender", "model")
_CARD_COST_KEY = "cost_estimate_mψ"


class BadId(ValueError):
    """Not one action id or one task id of the right shape."""


Busy = U.Busy


def _clock() -> float:
    """The clock every limit here is measured on (a test may move it)."""
    return time.monotonic()


# ── small helpers ────────────────────────────────────────────────────────────

def valid_id(v: Any) -> Optional[str]:
    """An action or task id: unified_ledger's id shape (letters, digits and
    _ . : + -, at most 128), never "..". None otherwise."""
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s or len(s) > 128 or ".." in s:
        return None
    return s if U._ID_RE.fullmatch(s) else None


def _name(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not _NAME_RE.fullmatch(s) or ".." in s or "//" in s:
        return None
    return s


def _cls(v: Any) -> Optional[str]:
    s = str(v or "").strip()
    return s if _CLASS_RE.fullmatch(s) else None


def _int(v: Any) -> int:
    if isinstance(v, bool):
        return 0
    try:
        return max(0, int(v))
    except (TypeError, ValueError, OverflowError):
        return 0


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) and f >= 0 else None


def _rec(e: Any) -> dict:
    r = e.get("rec") if isinstance(e, dict) else None
    return r if isinstance(r, dict) else {}


def _aid_of(rec: dict) -> Optional[str]:
    env = rec.get("envelope") if isinstance(rec.get("envelope"), dict) else {}
    dref = rec.get("decision_ref") if isinstance(rec.get("decision_ref"), dict) else {}
    return U._id(rec.get("action_id") or env.get("action_id") or dref.get("action_id"))


def _at_of(e: dict) -> float:
    return U._at(e.get("t") if e.get("t") is not None else e.get("ts"))


def _lgr(seq: Any) -> Optional[str]:
    return ("lgr_%d" % seq) if isinstance(seq, int) and not isinstance(seq, bool) and seq > 0 else None


def _add(seq: List[str], v: Optional[str]) -> None:
    if v and v not in seq and len(seq) < MAX_IDS:
        seq.append(v)


def _count(c: Dict[str, int], k: Optional[str], n: int = 1) -> None:
    if k:
        c[k] = c.get(k, 0) + n


def _counts_words(c: Dict[str, int]) -> str:
    return ", ".join("%s %d" % (k.replace("_", " "), n) for k, n in sorted(c.items()))


def reason_code(reason: Any, surface: Any = None) -> Optional[str]:
    """The kind of reason a decision gave, as a fixed code; None when it gave
    none. Never the reason's own words."""
    s = str(reason or "").strip().lower()
    if not s:
        return None
    for prefix, code in _REASON_CODES:
        if s.startswith(prefix):
            return code
    if surface and s == str(surface).strip().lower():
        return "carried_out_by_executor"       # record_external: the surface is the reason
    return "other"


class _Stop:
    """Asked before each line: True once its deadline has passed."""

    def __init__(self, deadline: float):
        self.deadline = deadline
        self.hit = False

    def __call__(self) -> bool:
        if _clock() > self.deadline:
            self.hit = True
            return True
        return False


class _Flag:
    truncated = False


class _Run:
    """One explanation: the subject, the clock, and each store read once."""

    def __init__(self, action_id: Optional[str], task_id: Optional[str]):
        self.action_id = action_id
        self.task_id = task_id
        self.started = _clock()
        self.memo: Dict[str, Tuple[bool, Any]] = {}
        self.partial: Set[str] = set()
        self.truncated: Set[str] = set()

    def left(self) -> float:
        return TOTAL_BUDGET_S - (_clock() - self.started)

    def stop(self) -> _Stop:
        return _Stop(_clock() + max(0.0, min(SECTION_BUDGET_S, self.left())))

    def note(self, name: str, stop: _Stop, flag: Optional[_Flag] = None) -> None:
        if stop.hit:
            self.partial.add(name)
        if flag is not None and flag.truncated:
            self.truncated.add(name)

    def once(self, key: str, fn: Callable[["_Run"], Any]) -> Any:
        """`fn` read once per explanation; a failure is raised again to every
        section that needs it (and only to them)."""
        if key in self.memo:
            ok, v = self.memo[key]
            if ok:
                return v
            raise v
        try:
            v = fn(self)
        except Exception as e:  # noqa: BLE001
            self.memo[key] = (False, e)
            raise
        self.memo[key] = (True, v)
        return v

    def maybe(self, key: str, fn: Callable[["_Run"], Any], default: Any) -> Any:
        try:
            return self.once(key, fn)
        except Exception:  # noqa: BLE001 - the section that owns the read says so
            return default


# ── reads (each once per explanation) ────────────────────────────────────────

def _read_ledger(run: _Run) -> dict:
    """The decisions, outcomes and change receipts of the action (or of the
    task's actions: its decisions, and every action its journal names)."""
    from agent_friday.services import action_ledger
    got: Dict[Any, dict] = {}

    def keep(rows: Iterable[dict]) -> None:
        for e in rows:
            if isinstance(e, dict):
                got.setdefault(e.get("seq") if isinstance(e.get("seq"), int) else id(e), e)

    if run.action_id:
        aids = {run.action_id}
    else:
        tid = run.task_id
        stop = run.stop()

        def of_task(e: dict) -> bool:
            return e.get("kind") == "decision" and str(_rec(e).get("task_id") or "") == tid

        rows = action_ledger.entries(["decision"], newest_first=True, limit=MAX_ACTIONS,
                                     where=of_task, stop=stop)
        run.note("action_ledger", stop)
        keep(rows)
        aids = {a for a in (_aid_of(_rec(e)) for e in rows) if a}
        for a in run.maybe("journal", _read_journal, {}).get("action_ids", []):
            aids.add(a)
        if len(aids) > MAX_ACTIONS:
            run.truncated.add("actions")
            aids = set(sorted(aids)[:MAX_ACTIONS])
    if aids:
        stop = run.stop()

        def of_action(e: dict) -> bool:
            return e.get("kind") in ("decision", "outcome", "action") and _aid_of(_rec(e)) in aids

        keep(action_ledger.entries(["decision", "outcome", "action"], newest_first=True,
                                   limit=MAX_ROWS, where=of_action, stop=stop))
        run.note("action_ledger", stop)
    rows = sorted(got.values(), key=lambda e: (_at_of(e), e.get("seq") if isinstance(e.get("seq"), int) else 0))
    return {"decisions": [e for e in rows if e.get("kind") == "decision"],
            "outcomes": [e for e in rows if e.get("kind") == "outcome"],
            "changes": [e for e in rows if e.get("kind") == "action"],
            "action_ids": sorted(aids)}


def _aids(run: _Run) -> Set[str]:
    if run.action_id:
        return {run.action_id}
    return set(run.once("ledger", _read_ledger)["action_ids"])


def _turns(run: _Run) -> Set[str]:
    led = run.maybe("ledger", _read_ledger, {"decisions": [], "outcomes": []})
    out: Set[str] = set()
    for e in led.get("decisions", []):
        t = U._id(_rec(e).get("turn_id"))
        if t:
            out.add(t)
    return out


def _tasks(run: _Run) -> List[str]:
    if run.task_id:
        return [run.task_id]
    led = run.once("ledger", _read_ledger)
    out: List[str] = []
    for e in led["decisions"] + led["outcomes"]:
        t = valid_id(_rec(e).get("task_id"))
        if t and t not in out:
            out.append(t)
    return out[:3]


def _task_on_disk(want: str) -> Tuple[Optional[str], dict]:
    """The task's own name as the disk gives it (the index, else its folder),
    and its index row; (None, {}) when this PC keeps no journal for it. The
    journal is opened by that name, never by the one asked for."""
    from agent_friday.services import task_journal as tj
    try:
        idx = tj.index_read() or {}
    except Exception:  # noqa: BLE001
        idx = {}
    for t, row in idx.items():
        if t == want:
            return t, (row if isinstance(row, dict) else {})
    try:
        for n, d in enumerate(tj.tasks_dir().iterdir()):
            if n >= 20 * U.MAX_TASKS:
                break
            if d.name == want and d.is_dir():
                return d.name, {}
    except OSError:
        pass
    return None, {}


def _read_journal(run: _Run) -> dict:
    """The task journal events of the action's task (those that name the
    action, and the task's model calls), or of the task asked about."""
    from agent_friday.services import task_journal as tj
    out: dict = {"tasks": [], "status": {}, "events": [], "model_calls": [], "action_ids": []}
    try:
        tasks = _tasks(run)
    except Exception:  # noqa: BLE001 - no decision to name a task: no journal to read
        tasks = []
    for want in tasks:
        name, row = _task_on_disk(want)
        if not name:
            continue
        out["tasks"].append(name)
        st = U._code(row.get("status"))
        if st:
            out["status"][name] = st
        stop = run.stop()
        lines = [0]

        def halt() -> bool:
            lines[0] += 1
            return lines[0] > U.PER_TASK_LINES or stop()

        evs = tj.read(name, stop=halt, tail_bytes=U.PER_TASK_BYTES)
        run.note("task_journal", stop)
        if lines[0] > U.PER_TASK_LINES:
            run.truncated.add("task_journal")
        for ev in evs:
            if not isinstance(ev, dict):
                continue
            kind = ev.get("kind")
            if kind == "model_call":
                if len(out["model_calls"]) < MAX_ROWS:
                    out["model_calls"].append(ev)
                continue
            aid = U._id(ev.get("action_id"))
            if run.action_id and aid != run.action_id:
                continue
            if (kind in U.JOURNAL_KINDS or kind == "step") and len(out["events"]) < MAX_ROWS:
                out["events"].append(ev)
            if aid and aid not in out["action_ids"]:
                out["action_ids"].append(aid)
    return out


def _read_models(run: _Run) -> List[dict]:
    """model_invocation activity rows of the action's turn or its task."""
    from agent_friday.services import action_ledger
    turns = _turns(run)
    try:
        tasks = set(_tasks(run))
    except Exception:  # noqa: BLE001
        tasks = set()
    if not turns and not tasks:
        return []
    stop = run.stop()

    def match(e: dict) -> bool:
        rec = _rec(e)
        return rec.get("kind") == "model_invocation" and (
            str(rec.get("turn_id") or "") in turns or str(rec.get("task_id") or "") in tasks)

    rows = action_ledger.entries(["activity"], newest_first=True, limit=MAX_ROWS, where=match,
                                 stop=stop)
    run.note("activity", stop)
    return [_rec(e) for e in rows]


def _read_egress(run: _Run) -> List[dict]:
    """The egress ledger's call rows naming the action(s), and the turn's
    model calls (rows that name no action), newest first."""
    aids = _aids(run)
    turns = _turns(run)
    if not aids and not turns:
        return []
    flag = _Flag()
    stop = run.stop()
    out: List[dict] = []
    for raw in U._tail_lines(U._egress_path(), U.TAIL_BYTES, flag):
        if stop() or len(out) >= MAX_ROWS:
            break
        if b'"call"' not in raw:
            continue
        e = U._json(raw)
        if not e or e.get("kind") != "call":
            continue
        a, t = U._id(e.get("action_id")), U._id(e.get("turn_id"))
        if a and a in aids:
            link = "action"
        elif not a and t and t in turns:
            link = "turn"
        else:
            continue
        row = U._egress_row(e)
        if not row:
            continue
        out.append({"link": link, "channel": row["kind"], "ids": row["ids"],
                    "result": U._word(e.get("result"), U.EGRESS_WORDS), "at": row["at"]})
    run.note("egress", stop, flag)
    return out


def _read_manifests(run: _Run) -> List[dict]:
    """The context manifests of the action's sends and of its turn (whole
    lines, read here only to be counted)."""
    from agent_friday.services import context_manifest as cm
    eg = run.maybe("egress", _read_egress, [])
    mids = {r["ids"]["manifest_id"] for r in eg if r["ids"].get("manifest_id")}
    turns = _turns(run)
    needles = [n.encode("utf-8") for n in sorted(mids | turns)]
    if not needles:
        return []
    p = Path(cm.path())
    flag = _Flag()
    stop = run.stop()
    out: List[dict] = []
    for f in (p, p.with_name(p.name + ".1")):
        for raw in U._tail_lines(f, U.TAIL_BYTES, flag):
            if stop() or len(out) >= MAX_ROWS:
                break
            if not any(n in raw for n in needles):
                continue
            e = U._json(raw)
            if not e:
                continue
            mid, t = e.get("manifest_id"), e.get("turn_id")
            if (isinstance(mid, str) and mid in mids) or (isinstance(t, str) and t in turns):
                out.append(e)
    run.note("context_manifest", stop, flag)
    return out


def _read_sections(run: _Run) -> List[dict]:
    """The prompt sections the action's turn assembled (retrieval ledger):
    tier and whether it was sent, never the section's name."""
    from agent_friday.services import retrieval_ledger as rl
    turns = _turns(run)
    if not turns:
        return []
    flag = _Flag()
    stop = run.stop()
    needles = [t.encode("utf-8") for t in sorted(turns)]
    out: List[dict] = []
    for raw in U._tail_lines(Path(rl._DEFAULT_LOG), U.TAIL_BYTES, flag):
        if stop() or len(out) >= MAX_ROWS:
            break
        if not any(n in raw for n in needles):
            continue
        e = U._json(raw)
        if not e or str(e.get("turn_id") or "") not in turns:
            continue
        tier = e.get("tier")
        sent = (not e.get("applied")) or e.get("planner") in (None, "include")
        out.append({"tier": str(tier) if isinstance(tier, int) and not isinstance(tier, bool) else "unknown",
                    "sent": bool(sent)})
    run.note("retrieval_ledger", stop, flag)
    return out


def _read_artifacts(run: _Run) -> List[str]:
    """Keyed ids of the files made under the action's decisions (the artifact
    manifest's rows naming the decision, and the task journal's artifact
    events naming the action), or under the task."""
    from agent_friday.services import artifact_manifest as am
    led = run.maybe("ledger", _read_ledger, {"decisions": []})
    drefs = {U._keyed(_rec(e).get("hmac"), "dref_") for e in led.get("decisions", [])} - {None}
    out: List[str] = []
    jr = run.maybe("journal", _read_journal, {})
    for ev in jr.get("events", []):
        if ev.get("kind") == "artifact":
            _add(out, U._id(U._artifact_view_id(ev.get("artifact_id"))))
    if not drefs and not run.task_id:
        return out
    flag = _Flag()
    stop = run.stop()
    for raw in U._tail_lines(Path(am.manifest_path()), U.TAIL_BYTES, flag):
        if stop() or len(out) >= MAX_IDS:
            break
        e = U._json(raw)
        row = U._artifact_row(e) if e else None
        if not row:
            continue
        ids = row["ids"]
        if ids.get("decision_ref") in drefs or (run.task_id and ids.get("task_id") == run.task_id):
            _add(out, ids.get("artifact_id"))
    run.note("artifact_manifest", stop, flag)
    return out


# ── the sections ─────────────────────────────────────────────────────────────

def _nr(name: str, **extra: Any) -> dict:
    out = {"status": "not_recorded", "said": "%s: %s." % (SECTION_LABELS[name], NOT_RECORDED)}
    out.update(extra)
    return out


def _action_rows(run: _Run) -> List[dict]:
    """One line per action: its tool, decision, outcome and times."""
    led = run.once("ledger", _read_ledger)
    by: Dict[str, dict] = {}
    for e in led["decisions"] + led["outcomes"]:
        rec = _rec(e)
        aid = _aid_of(rec)
        if not aid:
            continue
        a = by.setdefault(aid, {"action_id": aid, "tool": None, "decision": None, "outcome": None,
                                "class": None, "decided_at": None, "closed_at": None})
        a["tool"] = a["tool"] or _name(rec.get("tool"))
        ts = U._iso(_at_of(e))
        if e.get("kind") == "decision":
            a["decision"] = U._word(rec.get("decision"), U.DECISION_WORDS) or a["decision"]
            a["class"] = U._code(rec.get("class")) or a["class"]
            a["decided_at"] = a["decided_at"] or ts
            # a decision's own outcome word is what it became only when it did
            # not let the action run (a deny is its own terminal receipt; a
            # card waits): an allow's "executed" means "it may run", not "it ran"
            if rec.get("decision") != "allow" and rec.get("outcome") and not a["outcome"]:
                a["outcome"] = U._word(rec.get("outcome"), U.OUTCOME_WORDS)
        else:
            a["outcome"] = U._word(rec.get("outcome"), U.OUTCOME_WORDS) or a["outcome"]
            a["closed_at"] = ts
    return sorted(by.values(), key=lambda a: a["decided_at"] or a["closed_at"] or "")


def _what(run: _Run) -> dict:
    acts = _action_rows(run)
    jr = run.maybe("journal", _read_journal, {})
    status = sorted(set((jr.get("status") or {}).values()))
    if not acts and not jr.get("tasks"):
        return _nr("what")
    if run.action_id:
        if not acts:
            return _nr("what")
        a = acts[0]
        said = "What: %s; the checkpoint said %s; outcome %s; decided %s%s." % (
            a["tool"] or "a tool (" + NOT_RECORDED + ")", a["decision"] or NOT_RECORDED,
            (a["outcome"] or NOT_RECORDED).replace("_", " "), a["decided_at"] or "at a time " + NOT_RECORDED,
            (", closed " + a["closed_at"]) if a["closed_at"] else "")
        return {"action": a, "said": said}
    tools: Dict[str, int] = {}
    outcomes: Dict[str, int] = {}
    for a in acts:
        _count(tools, a["tool"] or "unnamed")
        _count(outcomes, a["outcome"] or "no_outcome_recorded")
    said = "What: task %s, status %s; %d action%s%s%s." % (
        run.task_id, (status[0] if status else NOT_RECORDED), len(acts), "" if len(acts) == 1 else "s",
        (" (" + _counts_words(tools) + ")") if tools else "",
        ("; outcomes: " + _counts_words(outcomes)) if outcomes else "")
    return {"task_status": status[0] if status else None, "actions": acts[:MAX_ACTIONS],
            "tools": tools, "outcomes": outcomes, "said": said}


def _why_one(rec: dict) -> dict:
    env = rec.get("envelope") if isinstance(rec.get("envelope"), dict) else {}
    prov = env.get("provenance_summary")
    plan = env.get("verification_plan") if isinstance(env.get("verification_plan"), dict) else {}
    return {
        "action_id": _aid_of(rec),
        "decision": U._word(rec.get("decision"), U.DECISION_WORDS),
        "reason_code": reason_code(rec.get("reason"), rec.get("surface")),
        "detail": U._code(rec.get("detail")),
        "origin": env.get("origin") if env.get("origin") in _ORIGINS else None,
        "risk_class": U._code(env.get("risk_class") or rec.get("class")),
        "provenance": ({k: _int(prov.get(k)) for k in _PROVENANCE_KEYS}
                       if isinstance(prov, dict) else None),
        "checks": [c for c in (U._code(x) for x in (plan.get("checks") or [])
                               if isinstance(plan.get("checks"), list)) if c][:10],
        "chosen_by": U._code(rec.get("chosen_by")),
        "surface": U._code(rec.get("surface")),
        "tainted": bool(rec.get("tainted")) if rec.get("tainted") is not None else None,
    }


def _why(run: _Run) -> dict:
    led = run.once("ledger", _read_ledger)
    ws = [_why_one(_rec(e)) for e in led["decisions"]]
    if not ws:
        return _nr("why")
    if run.action_id:
        w = ws[-1]
        p = w["provenance"]
        said = "Why: the checkpoint said %s (reason: %s%s); it came from %s; risk class %s; " \
               "its details came from %s; checks %s." % (
                   w["decision"] or NOT_RECORDED, (w["reason_code"] or NOT_RECORDED).replace("_", " "),
                   (", " + w["detail"].replace("_", " ")) if w["detail"] else "",
                   (w["origin"] or NOT_RECORDED).replace("_", " "), w["risk_class"] or NOT_RECORDED,
                   ("owner %(owner)d, content %(content)d, sender %(sender)d, model %(model)d" % p)
                   if p else "a source " + NOT_RECORDED,
                   ", ".join(c.replace("_", " ") for c in w["checks"]) or NOT_RECORDED)
        return {"decision": w, "decisions": len(ws), "said": said}
    decisions: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    origins: Dict[str, int] = {}
    prov = {k: 0 for k in _PROVENANCE_KEYS}
    for w in ws:
        _count(decisions, w["decision"])
        _count(reasons, w["reason_code"])
        _count(origins, w["origin"])
        for k, n in (w["provenance"] or {}).items():
            prov[k] += n
    said = "Why: %d decision%s (%s); reasons: %s; from: %s." % (
        len(ws), "" if len(ws) == 1 else "s", _counts_words(decisions) or NOT_RECORDED,
        _counts_words(reasons) or NOT_RECORDED, _counts_words(origins) or NOT_RECORDED)
    return {"decisions": ws[-MAX_ACTIONS:], "by_decision": decisions, "by_reason": reasons,
            "by_origin": origins, "provenance": prov, "said": said}


def _model(run: _Run) -> dict:
    turns = _turns(run)
    rows = run.once("models", _read_models)
    jr = run.maybe("journal", _read_journal, {})
    by_turn = [r for r in rows if str(r.get("turn_id") or "") in turns]
    if by_turn:
        use, linked = by_turn, "turn"
    else:
        use = [r for r in rows if r.get("task_id")] + list(jr.get("model_calls") or [])
        linked = "task" if use else None
    if not use:
        return _nr("model")
    agg: Dict[Tuple[Optional[str], Optional[str], Optional[str]], dict] = {}
    for r in use:
        k = (_name(r.get("model")), _name(r.get("provider")), _name(r.get("seat")))
        a = agg.setdefault(k, {"model": k[0], "provider": k[1], "seat": k[2], "calls": 0,
                               "tokens_in": 0, "tokens_out": 0})
        a["calls"] += 1
        a["tokens_in"] += _int(r.get("tokens_in"))
        a["tokens_out"] += _int(r.get("tokens_out"))
    models = sorted(agg.values(), key=lambda a: -a["calls"])[:MAX_IDS]
    parts = ["%s via %s on the %s seat (%d call%s)" % (
        a["model"] or "a model (" + NOT_RECORDED + ")", a["provider"] or "a provider " + NOT_RECORDED,
        a["seat"] or NOT_RECORDED, a["calls"], "" if a["calls"] == 1 else "s") for a in models]
    said = "Which model: %s; linked by %s." % ("; ".join(parts),
                                                "this turn" if linked == "turn" else "its task")
    return {"linked_by": linked, "models": models, "said": said}


def _information(run: _Run) -> dict:
    ms = run.once("manifests", _read_manifests)
    secs = run.maybe("sections", _read_sections, [])
    if not ms and not secs:
        return _nr("information")
    classes: Dict[str, int] = {}
    actions: Dict[str, int] = {}
    ids: List[str] = []
    parts = withheld = tokens = 0
    for m in ms:
        _add(ids, U._id(m.get("manifest_id")))
        for c in (m.get("sensitivity_classes") or []) if isinstance(m.get("sensitivity_classes"), list) else []:
            _count(classes, _cls(c))
        for s in (m.get("sources") or []) if isinstance(m.get("sources"), list) else []:
            if isinstance(s, dict):
                n = _int(s.get("count")) or 1
                parts += n
                _count(actions, _cls(s.get("action")), n)
        withheld += _int(m.get("withheld"))
        tokens += _int(m.get("estimated_tokens"))
    tiers: Dict[str, int] = {}
    sent = 0
    for s in secs:
        _count(tiers, "tier_" + s["tier"])
        sent += 1 if s["sent"] else 0
    bits = []
    if ms:
        bits.append("%d send%s' context manifest%s: classes %s; %d part%s (%s); %d withheld; "
                    "about %d tokens" % (len(ms), "" if len(ms) == 1 else "s", "" if len(ms) == 1 else "s",
                                         _counts_words(classes) or NOT_RECORDED, parts,
                                         "" if parts == 1 else "s", _counts_words(actions) or NOT_RECORDED,
                                         withheld, tokens))
    else:
        bits.append("no send's context manifest was found")
    if secs:
        bits.append("the turn assembled %d prompt section%s (%s), %d sent" % (
            len(secs), "" if len(secs) == 1 else "s", _counts_words(tiers), sent))
    return {"manifests": len(ms), "manifest_ids": ids, "sensitivity_classes": classes,
            "gate_actions": actions, "parts": parts, "withheld": withheld,
            "estimated_tokens": tokens,
            "turn_sections": {"count": len(secs), "by_tier": tiers, "sent": sent} if secs else None,
            "said": "What information: " + "; ".join(bits) + ". Never their content."}


def _egress(run: _Run) -> dict:
    rows = run.once("egress", _read_egress)
    if not rows:
        return _nr("egress", by_channel={}, count=0)
    by_channel: Dict[str, int] = {}
    by_result: Dict[str, int] = {}
    by_link: Dict[str, int] = {}
    calls: List[str] = []
    manifests: List[str] = []
    for r in rows:
        _count(by_channel, r["channel"])
        _count(by_result, r["result"] or "unknown")
        _count(by_link, r["link"])
        _add(calls, r["ids"].get("call_id"))
        _add(manifests, r["ids"].get("manifest_id"))
    said = "What left this PC: %d send%s (%s; %s)%s. Destinations are not shown here." % (
        len(rows), "" if len(rows) == 1 else "s", _counts_words(by_channel), _counts_words(by_result),
        (", %d of them the turn's model calls" % by_link["turn"]) if by_link.get("turn") else "")
    return {"count": len(rows), "by_channel": by_channel, "by_result": by_result,
            "by_link": by_link, "call_ids": calls, "manifest_ids": manifests, "said": said}


def _limiting_fields(g: dict) -> List[str]:
    """The names of the grant record fields that limit this grant, in
    grant_record.FIELD_ORDER; never their values."""
    try:
        from agent_friday.governance.grant_record import FIELD_ORDER, OWNER
    except Exception:  # noqa: BLE001
        FIELD_ORDER, OWNER = (), "owner"
    created = _num(g.get("created_at"))
    starts = _num(g.get("starts_at"))
    on = {
        "starts_at": starts is not None and (created is None or starts > created + 1.0),
        "expires_at": _num(g.get("expires_at")) is not None,
        "max_uses": g.get("max_uses") is not None,
        "principal": str(g.get("principal") or OWNER) != OWNER,
        "tools": bool(g.get("tools")),
        "arg_constraints": bool(g.get("arg_constraints")),
        "data_scope": bool(g.get("data_scope")),
        "destination_scope": bool(g.get("destination_scope")),
        "money_cap": g.get("money_cap") is not None,
        "time_cap_s": g.get("time_cap_s") is not None,
        "quiet_hours": str(g.get("quiet_hours") or "allow") != "allow",
        "remote_allowed": g.get("remote_allowed") is False,
    }
    return [f for f in FIELD_ORDER if on.get(f)]


def _grant_view(gid: str, cache: dict) -> Optional[dict]:
    from agent_friday.governance import grant_record as gr
    if "scoped" not in cache:
        try:
            from agent_friday.governance import action_gate
            cache["scoped"] = [gr.from_scoped(g).to_dict() for g in action_gate._read_grants()
                               if isinstance(g, dict)]
        except Exception:  # noqa: BLE001
            cache["scoped"] = []
    hit = next((g for g in cache["scoped"] if g.get("id") == gid), None)
    if hit is None:
        if "all" not in cache:
            cache["all"] = (gr.all_records() or {}).get("records") or []
        hit = next((g for g in cache["all"] if g.get("id") == gid), None)
    if hit is None:
        return {"grant_id": gid, "found": False}
    subj = hit.get("subject") if isinstance(hit.get("subject"), dict) else {}
    return {"grant_id": gid, "found": True, "kind": U._code(hit.get("kind")),
            "status": U._code(hit.get("status")), "subject_kind": U._code(subj.get("kind")),
            "limited_by": _limiting_fields(hit), "used": _int(hit.get("used_count")),
            "max_uses": _int(hit.get("max_uses")) if hit.get("max_uses") is not None else None}


def _card_view(cid: str) -> dict:
    from agent_friday.services import approvals
    c = approvals.get_approval(cid)
    if not isinstance(c, dict):
        return {"card_id": cid, "found": False}
    return {"card_id": cid, "found": True, "status": U._code(c.get("status")),
            "decided_by": U._code(c.get("decided_source")),
            "policy_class": U._code(c.get("policy_class")),
            "policy_version": U._id(c.get("policy_version")),
            "decided_policy_version": U._id(c.get("decided_policy_version"))}


def _authority(run: _Run) -> dict:
    led = run.once("ledger", _read_ledger)
    jr = run.maybe("journal", _read_journal, {})
    cards: List[str] = []
    grants: List[str] = []
    excluded: List[dict] = []
    pvs: List[str] = []
    origins: Dict[str, int] = {}
    for e in led["decisions"]:
        rec = _rec(e)
        env = rec.get("envelope") if isinstance(rec.get("envelope"), dict) else {}
        _add(cards, U._id(rec.get("approval") or rec.get("approval_id")))
        _add(grants, U._id(rec.get("grant")))
        gx = U._id(rec.get("grant_excluded") or rec.get("grant_withheld"))
        if gx and len(excluded) < MAX_IDS:
            excluded.append({"grant_id": gx, "kept_out_by": U._code(rec.get("grant_excluded_by"))})
        _add(pvs, U._id(rec.get("policy_version") or env.get("policy_version")))
        _count(origins, env.get("origin") if env.get("origin") in _ORIGINS else None)
    for ev in jr.get("events", []):
        if ev.get("kind") in ("approval", "receipt"):
            _add(cards, U._id(ev.get("approval_id")))
    if not (cards or grants or excluded or pvs or origins):
        return _nr("authority")
    card_views = []
    for cid in cards[:MAX_CARDS]:
        try:
            card_views.append(_card_view(cid))
        except Exception as e:  # noqa: BLE001 - the card store failing hides nothing else
            card_views.append({"card_id": cid, "found": None, "error": type(e).__name__})
    cache: dict = {}
    grant_views = []
    for gid in grants[:MAX_GRANTS]:
        try:
            grant_views.append(_grant_view(gid, cache))
        except Exception as e:  # noqa: BLE001
            grant_views.append({"grant_id": gid, "found": None, "error": type(e).__name__})
    owner_route = bool(origins.get("owner_route"))
    bits = []
    for c in card_views:
        bits.append("card %s (%s%s)" % (c["card_id"], (c.get("status") or NOT_RECORDED).replace("_", " "),
                                        (", decided by " + c["decided_by"].replace("_", " "))
                                        if c.get("decided_by") else ""))
    for g in grant_views:
        bits.append("grant %s (%s%s)" % (
            g["grant_id"], ((g.get("kind") or "") + " " + (g.get("status") or "")).strip() or NOT_RECORDED,
            ("; limited by " + ", ".join(f.replace("_", " ") for f in g["limited_by"]))
            if g.get("limited_by") else ""))
    if not cards and not grants:
        bits.append("no card or grant (" + NOT_RECORDED + ")")
    said = "On whose authority: %s; from %s%s; policy version %s." % (
        "; ".join(bits), _counts_words(origins) or "an origin " + NOT_RECORDED,
        " (verified as the owner's)" if owner_route else "",
        ", ".join(pvs) or NOT_RECORDED)
    return {"cards": card_views, "grants": grant_views, "grants_kept_out": excluded,
            "origins": origins, "owner_route": owner_route, "policy_versions": pvs, "said": said}


def _cost(run: _Run) -> dict:
    jr = run.maybe("journal", _read_journal, {})
    calls = list(jr.get("model_calls") or [])
    priced = [c for c in (_num(ev.get("cost_usd")) for ev in calls) if c is not None]
    recorded = round(sum(priced), 6) if priced else None
    estimate = 0
    led = run.maybe("ledger", _read_ledger, {"decisions": []})
    cards: List[str] = []
    for e in led.get("decisions", []):
        _add(cards, U._id(_rec(e).get("approval")))
    if cards:
        from agent_friday.services import approvals
        for cid in cards[:MAX_CARDS]:
            try:
                c = approvals.get_approval(cid) or {}
            except Exception:  # noqa: BLE001
                c = {}
            estimate += _int(c.get(_CARD_COST_KEY))
    tok_in = tok_out = 0
    for r in run.maybe("models", _read_models, []) + calls:
        tok_in += _int(r.get("tokens_in"))
        tok_out += _int(r.get("tokens_out"))
    tokens = {"in": tok_in, "out": tok_out} if (tok_in or tok_out) else None
    if recorded is None and not estimate:
        said = "What it cost: %s%s." % (NOT_RECORDED, (" (%d tokens in and %d out were counted, but no price "
                                                        "was recorded for them)" % (tok_in, tok_out))
                                        if tokens else "")
        return _nr("cost", tokens=tokens, said=said)
    bits = []
    if recorded is not None:
        bits.append("$%.6f recorded for %d of %d model call%s %s" % (
            recorded, len(priced), len(calls), "" if len(calls) == 1 else "s",
            "of this action's task" if run.action_id else "of this task"))
    if estimate:
        bits.append("the card estimated %d mψ" % estimate)
    return {"usd_recorded": recorded, "priced_calls": len(priced), "model_calls": len(calls),
            "linked_by": "task" if recorded is not None else None,
            "card_estimate_mpsi": estimate or None, "tokens": tokens,
            "said": "What it cost: " + "; ".join(bits) + "."}


def _changed(run: _Run) -> dict:
    led = run.once("ledger", _read_ledger)
    outcomes = []
    for e in led["outcomes"]:
        rec = _rec(e)
        if len(outcomes) >= MAX_ACTIONS:
            break
        outcomes.append({"action_id": _aid_of(rec), "outcome": U._word(rec.get("outcome"), U.OUTCOME_WORDS),
                         "terminal": bool(rec.get("terminal")), "receipt_id": _lgr(e.get("seq")),
                         "decision_receipt_id": _lgr(rec.get("decision_seq")),
                         "at": U._iso(_at_of(e))})
    changes = []
    for e in led["changes"][:MAX_IDS]:
        rec = _rec(e)
        changes.append({"receipt_id": U._id(rec.get("receipt_id")) or _lgr(e.get("seq")),
                        "status": "undone" if rec.get("undone") else U._word(rec.get("status"), U.ACTION_WORDS)})
    artifacts = run.maybe("artifacts", _read_artifacts, [])
    jr = run.maybe("journal", _read_journal, {})
    receipts: List[str] = []
    for ev in jr.get("events", []):
        if ev.get("kind") == "receipt":
            _add(receipts, U._id(ev.get("receipt_id")))
    memory = []
    try:
        from agent_friday.services import outcome_memory as om
        refs = {"action:%s" % a for a in _aids(run)}
        for r in om._all() if refs else []:
            if isinstance(r, dict) and r.get("subject_ref") in refs and len(memory) < MAX_IDS:
                eff = om.effective(r)
                memory.append({"id": U._id(r.get("id")),
                               "result": U._code(eff.get("result")), "verified": bool(eff.get("verified"))})
    except Exception:  # noqa: BLE001 - said below as not recorded
        memory = []
    if not (outcomes or changes or artifacts or receipts or memory):
        return _nr("changed")
    bits = []
    if outcomes:
        bits.append("outcome %s" % ", ".join("%s (receipt %s)" % ((o["outcome"] or NOT_RECORDED).replace("_", " "),
                                                                   o["receipt_id"] or NOT_RECORDED)
                                             for o in outcomes[-5:]))
    else:
        bits.append("no outcome receipt (" + NOT_RECORDED + ")")
    if changes:
        bits.append("%d change%s to your things (%s)" % (len(changes), "" if len(changes) == 1 else "s",
                                                         ", ".join(c["receipt_id"] or "?" for c in changes[:5])))
    if artifacts:
        bits.append("%d file%s made (%s)" % (len(artifacts), "" if len(artifacts) == 1 else "s",
                                             ", ".join(artifacts[:5])))
    if receipts:
        bits.append("task journal receipt%s %s" % ("" if len(receipts) == 1 else "s", ", ".join(receipts[:5])))
    if memory:
        bits.append("remembered as outcome %s" % ", ".join("%s (%s%s)" % (
            m["id"], m["result"] or NOT_RECORDED, ", checked" if m["verified"] else "") for m in memory[:5]))
    else:
        bits.append("outcome memory: " + NOT_RECORDED)
    return {"outcomes": outcomes, "changes": changes, "artifact_ids": artifacts,
            "journal_receipt_ids": receipts, "outcome_memory": memory,
            "said": "What changed: " + "; ".join(bits) + "."}


def _links(run: _Run) -> dict:
    want = run.action_id or run.task_id
    try:
        out = U.unified(id=want, limit=U.PAGE_MAX)
    except U.Busy:
        return {"status": "busy", "said": "Linked records: the records are being read for another "
                                          "view; try again in a moment."}
    rows = out.get("rows") or []
    if not rows:
        return _nr("links", out_of_time=bool(out.get("out_of_time")))
    fams: Dict[str, int] = {}
    shown = []
    for r in rows:
        _count(fams, r.get("family"))
        if len(shown) < LINK_ROWS_MAX:
            shown.append({"family": r.get("family"), "kind": r.get("kind"), "link": r.get("link"),
                          "ts": r.get("ts"), "ids": dict(r.get("ids") or {})})
    said = "Linked records: %d across %s%s. Trace this id on the Ledger page for each one." % (
        len(rows), _counts_words(fams), " (some stores ran out of time)" if out.get("out_of_time") else "")
    return {"count": len(rows), "families": fams, "rows": shown,
            "out_of_time": bool(out.get("out_of_time")), "said": said}


#: (name, builder). Each is built on its own: one that fails is named with
#: its error's type and the others are still given.
SECTIONS: List[Tuple[str, Callable[[_Run], dict]]] = [
    ("what", _what), ("why", _why), ("model", _model), ("information", _information),
    ("egress", _egress), ("authority", _authority), ("cost", _cost), ("changed", _changed),
    ("links", _links),
]


def _section(run: _Run, name: str, fn: Callable[[_Run], dict]) -> dict:
    label = SECTION_LABELS.get(name, name)
    if run.left() <= 0:
        return {"status": "out_of_time", "said": "%s: not read this time (out of time)." % label}
    try:
        out = fn(run)
    except U.Busy:
        return {"status": "busy", "said": "%s: the records were busy; try again in a moment." % label}
    except Exception as e:  # noqa: BLE001 - one section failing never hides the others
        # the error's type only: its message can carry a path
        return {"status": "error", "error": type(e).__name__,
                "said": "%s: could not be read this time." % label}
    if not isinstance(out, dict):
        return _nr(name)
    out = dict(out)
    out.setdefault("status", "recorded")
    return out


def _found(run: _Run) -> bool:
    led = run.maybe("ledger", _read_ledger, {})
    jr = run.maybe("journal", _read_journal, {})
    return bool(led.get("decisions") or led.get("outcomes") or led.get("changes")
                or jr.get("tasks") or run.maybe("egress", _read_egress, []))


#: One explanation at a time: each opens several stores.
_RUN_LOCK = threading.Lock()


def _check(action_id: Any, task_id: Any) -> Tuple[Optional[str], Optional[str]]:
    given_a = action_id not in (None, "")
    given_t = task_id not in (None, "")
    if given_a == given_t:
        raise BadId("name one action id or one task id")
    v = valid_id(action_id if given_a else task_id)
    if v is None:
        raise BadId("that is not an id")
    return (v, None) if given_a else (None, v)


def explain(action_id: Optional[str] = None, task_id: Optional[str] = None) -> dict:
    """What Friday did for this action (or task), and why, which model, what
    information, what left this PC, on whose authority, what it cost and what
    changed: one section each, in SECTION_NAMES order, each saying "not
    recorded" when nothing is kept for it.

    Raises BadId unless exactly one id of the right shape is given, and Busy
    when another explanation held the lock BUSY_WAIT_S. Otherwise never
    raises: a section that fails says so with its error's type."""
    aid, tid = _check(action_id, task_id)
    if not _RUN_LOCK.acquire(timeout=BUSY_WAIT_S):
        raise Busy("another explanation is being read")
    try:
        view = U._VIEW_KEY.set(U._load_view_key())
        try:
            run = _Run(aid, tid)
            sections = {name: _section(run, name, fn) for name, fn in SECTIONS}
            found = _found(run)
        finally:
            U._VIEW_KEY.reset(view)
    finally:
        _RUN_LOCK.release()
    order = [n for n, _f in SECTIONS]
    head = ("Action %s" % aid) if aid else ("Task %s" % tid)
    if not found:
        head += ": nothing Friday keeps names this id."
    lines = [head] + [sections[n].get("said", "") for n in order]
    if run.partial:
        lines.append("Some stores ran out of time (%s), so older records may be missing."
                     % ", ".join(sorted(n.replace("_", " ") for n in run.partial)))
    return {"subject": {"action_id": aid} if aid else {"task_id": tid}, "found": found,
            "order": order, "sections": sections, "text": "\n".join(lines),
            "out_of_time": bool(run.partial) or any(s.get("status") == "out_of_time"
                                                     for s in sections.values()),
            "partial": sorted(run.partial), "truncated": sorted(run.truncated),
            "bounded": {"total_seconds": TOTAL_BUDGET_S, "read_seconds": SECTION_BUDGET_S,
                        "actions": MAX_ACTIONS, "rows": MAX_ROWS, "tail_bytes": U.TAIL_BYTES}}
