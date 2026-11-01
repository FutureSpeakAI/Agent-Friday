"""The terminal-receipt invariant for outward actions (BD-11a, NS-31.5-2,
NS-36.3-12: every outward action has a decision and a terminal receipt; no
duplicates; no false done).

Every outward decision in the action ledger names its action (`action_id`,
governance/action_gate.ActionEnvelope). What became of that action is written
as an outcome entry naming the same action id, with one word from
action_gate.OUTCOMES. This module reads the ledger and says, per outward
decision:

  closed      exactly one terminal outcome (executed, failed, completed_verified,
              denied, forbidden); a decision that denied the action is its own
              terminal receipt
  duplicate   more than one terminal outcome for the same action
  waiting     no terminal outcome, and the action waits for the owner (its
              decision or a later receipt says approval_required or waiting,
              and its card, when named, is still open)
  handed_off  it waited for the owner and the owner's card has since been
              decided: carrying it out is a new action with its own receipts
  unconfirmed no terminal outcome, and a later receipt says nobody knows what
              became of it (the request that ran it answered but its executor
              never said; a send the gateway could not confirm). Neither open
              nor closed; listed so it can be checked by hand.
  in_flight   no terminal outcome yet, inside the grace period
  open        no terminal outcome after the grace period

and across actions:

  duplicate_key  one idempotency key (the same request) executed by more than
                 one action; only a key that names one request (a turn, task,
                 run or the caller's own key), as the gate refuses a repeat
  false_done     a task marked done whose outward actions did not all end
                 executed or completed_verified (an unconfirmed one is not done)

Read-only. Counts, action ids, tool names and task ids only: never a record's
arguments, reason text or target.

The default audit (no `now`, no `tasks`) is cached on the ledger's head (its
seq and hash, and the live file's size and mtime) for a few seconds, so the
Ledger line, the Doctor check and the route share one scan.
"""
from __future__ import annotations

import copy
import sys
import threading
import time
from typing import Any, Dict, Iterable, Optional

#: How far back the audit looks.
WINDOW_S = 30 * 86400.0
#: How long a decision may wait for its terminal outcome before it counts as open.
GRACE_S = 3600.0
#: How long a cached audit stands while the ledger's head has not moved (cards
#: are decided and grace periods run out without a ledger write).
CACHE_TTL_S = 15.0

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[tuple, tuple] = {}         # key -> (time computed, result)


def _head_key() -> Optional[tuple]:
    """The ledger's head as a cache key, or None when it cannot be read."""
    try:
        from agent_friday.services import action_ledger as L
        head = L._read_head() or {}
        try:
            st = L.live_path().stat()
            live = (st.st_size, st.st_mtime_ns)
        except OSError:
            live = (None, None)
        return (str(L.ledger_dir()), head.get("seq"), head.get("hash")) + live
    except Exception:  # noqa: BLE001
        return None


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
#: Task statuses that say the task is done. Never ``outcome_unknown`` (an approved action
#: it waited on may or may not have happened) nor ``completed_unverified``.
DONE_STATUSES = frozenset({"complete", "completed", "done", "completed_verified"})


def _word(rec: dict) -> str:
    """An outcome entry's outcome word (older entries carry only a status)."""
    from agent_friday.governance import action_gate as G
    w = rec.get("outcome")
    if w in G.OUTCOMES:
        return w
    return G.STATUS_OUTCOME.get(str(rec.get("status") or ""), G.FAILED)


def _action_id(rec: dict) -> Optional[str]:
    aid = rec.get("action_id")
    if not aid and isinstance(rec.get("envelope"), dict):
        aid = rec["envelope"].get("action_id")
    return str(aid) if aid else None


def _idem_key(rec: dict) -> Optional[str]:
    env = rec.get("envelope") if isinstance(rec.get("envelope"), dict) else {}
    k = env.get("idempotency_key")
    return str(k) if k else None


def _load_tasks() -> Dict[str, dict]:
    """{task_id: record} from the task index on disk and, when the agent is
    loaded, its live table (which wins). Never raises."""
    out: Dict[str, dict] = {}
    try:
        from agent_friday.services import task_journal
        for tid, row in (task_journal.index_read() or {}).items():
            if isinstance(row, dict):
                out[str(tid)] = row
    except Exception:  # noqa: BLE001
        pass
    agent = sys.modules.get("agent_friday.services.agent")
    if agent is not None:
        try:
            with agent.TASKS_LOCK:
                live = {str(k): dict(v) for k, v in agent.TASKS.items() if isinstance(v, dict)}
            out.update(live)
        except Exception:  # noqa: BLE001
            pass
    return out


def _card_status(approval_id: Optional[str], cache: dict) -> Optional[str]:
    if not approval_id:
        return None
    if approval_id not in cache:
        try:
            from agent_friday.services import approvals
            cache[approval_id] = str((approvals.get_approval(approval_id) or {}).get("status") or "") or None
        except Exception:  # noqa: BLE001
            cache[approval_id] = None
    return cache[approval_id]


def audit(window: float = WINDOW_S, *, now: Optional[float] = None,
          tasks: Optional[Iterable[dict]] = None, include_archive: bool = False,
          grace_s: float = GRACE_S, limit: int = 50) -> dict:
    """The invariant over outward decisions from the last `window` seconds.

    `tasks`: task records ({task_id or id, status}) to check for false done;
    by default the task index and the agent's live table. Only signed ledger
    entries are read. Never raises on a record it cannot read.

    With neither `now` nor `tasks` given, a result computed in the last
    CACHE_TTL_S seconds over the same ledger head is returned (a copy)."""
    if now is not None or tasks is not None:
        return _audit(window, now=now, tasks=tasks, include_archive=include_archive,
                      grace_s=grace_s, limit=limit)
    head = _head_key()
    key = None if head is None else head + (float(window), bool(include_archive), float(grace_s), int(limit))
    t = time.time()
    if key is not None:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
        if hit and t - hit[0] < CACHE_TTL_S:
            return copy.deepcopy(hit[1])
    res = _audit(window, now=t, tasks=None, include_archive=include_archive,
                 grace_s=grace_s, limit=limit)
    if key is not None:
        with _CACHE_LOCK:
            for k in [k for k in _CACHE if k[:len(head)] != head]:
                del _CACHE[k]               # one ledger head at a time: the newest
            _CACHE[key] = (t, copy.deepcopy(res))
    return res


def _audit(window: float, *, now: Optional[float], tasks: Optional[Iterable[dict]],
           include_archive: bool, grace_s: float, limit: int) -> dict:
    """`audit` without the cache: one scan of the ledger."""
    from agent_friday.governance import action_gate as G
    from agent_friday.services import action_ledger as L
    now = time.time() if now is None else float(now)
    since = now - float(window)
    rows = L.entries(["decision", "outcome"], newest_first=False, archive=include_archive,
                     verified=True,
                     where=lambda e: isinstance(e.get("t"), (int, float)) and e["t"] >= since)

    decisions = []                       # (entry, rec)
    by_aid: Dict[str, list] = {}
    by_seq: Dict[Any, list] = {}
    loose = []                           # outcomes with an action id of their own
    for e in rows:
        rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
        if e.get("kind") == "decision":
            if rec.get("class") == G.OUTWARD:
                decisions.append((e, rec))
            continue
        out = {"seq": e.get("seq"), "word": _word(rec), "approval": rec.get("approval"),
               "tool": rec.get("tool")}
        aid = _action_id(rec)
        if aid and rec.get("action_id_origin") != "outcome":
            by_aid.setdefault(aid, []).append(out)
        elif rec.get("decision_seq") is not None:
            by_seq.setdefault(rec.get("decision_seq"), []).append(out)
        else:
            loose.append(out)

    cards: dict = {}
    counts = {k: 0 for k in ("closed", "duplicate", "waiting", "handed_off", "unconfirmed",
                             "in_flight", "open")}
    found: Dict[str, list] = {"open": [], "duplicate": [], "unconfirmed": [], "duplicate_key": [],
                              "false_done": []}
    state_of: Dict[str, tuple] = {}      # action_id -> (state, terminal word)
    executed_by_key: Dict[str, set] = {}
    used_loose: set = set()
    for e, rec in decisions:
        aid = _action_id(rec) or "seq:%s" % e.get("seq")
        outs = list(by_aid.get(aid, [])) + list(by_seq.get(e.get("seq"), []))
        if not outs and rec.get("approval"):
            # an outcome written before its decision was known names the same card
            for i, o in enumerate(loose):
                if i not in used_loose and o["approval"] == rec.get("approval") \
                        and str(o["tool"]) == str(rec.get("tool")):
                    used_loose.add(i)
                    outs.append(o)
                    break
        terms = [o["word"] for o in outs if o["word"] in G.TERMINAL_OUTCOMES]
        waits = [o["word"] for o in outs if o["word"] not in G.TERMINAL_OUTCOMES]
        word = None
        if len(terms) > 1:
            state, word = "duplicate", terms[-1]
        elif terms:
            state, word = "closed", terms[0]
        elif G.UNCONFIRMED in waits:
            # nobody said what became of it: not open (something was written), not closed
            state, word = "unconfirmed", G.UNCONFIRMED
        elif rec.get("decision") == "deny":
            word = rec.get("outcome") if rec.get("outcome") in G.TERMINAL_OUTCOMES else \
                G.verdict_outcome("deny", str(rec.get("class") or ""), str(rec.get("reason") or ""))
            state = "closed"
        elif waits or rec.get("decision") in ("card", "confirm") \
                or rec.get("outcome") in (G.APPROVAL_REQUIRED, G.WAITING):
            card = _card_status(rec.get("approval") or next(
                (o["approval"] for o in outs if o.get("approval")), None), cards)
            state = "handed_off" if card and card != "pending" else "waiting"
        elif isinstance(e.get("t"), (int, float)) and now - e["t"] < grace_s:
            state = "in_flight"
        else:
            state = "open"
        counts[state] += 1
        state_of[aid] = (state, word)
        item = {"action_id": aid, "tool": rec.get("tool"), "seq": e.get("seq")}
        if state == "open":
            found["open"].append(item)
        elif state == "duplicate":
            found["duplicate"].append(dict(item, terminal=len(terms)))
        elif state == "unconfirmed":
            found["unconfirmed"].append(item)
        if word in (G.EXECUTED, G.COMPLETED_VERIFIED):
            # a key scoped by a session or a schedule is not one request (the
            # same message may rightly go out again tomorrow): the gate's own
            # rule (action_gate.recorded_key_names_one_request), so the two agree
            key = _idem_key(rec) if G.recorded_key_names_one_request(rec) else None
            if key:
                executed_by_key.setdefault(key, set()).add(aid)

    for key, aids in executed_by_key.items():
        if len(aids) > 1:
            found["duplicate_key"].append({"idempotency_key": key, "actions": sorted(aids)[:10],
                                           "count": len(aids)})

    by_task: Dict[str, list] = {}
    for e, rec in decisions:
        if rec.get("task_id"):
            by_task.setdefault(str(rec["task_id"]), []).append(
                _action_id(rec) or "seq:%s" % e.get("seq"))
    if by_task:
        if tasks is None:
            table = _load_tasks()
        else:
            table = {str(t.get("task_id") or t.get("id")): t for t in tasks
                     if isinstance(t, dict) and (t.get("task_id") or t.get("id"))}
        for tid, aids in by_task.items():
            t = table.get(tid)
            if not t or str(t.get("status") or "").strip().lower() not in DONE_STATUSES:
                continue
            short, why = [], set()
            for a in aids:
                st, word = state_of.get(a, ("open", None))
                if word in (G.EXECUTED, G.COMPLETED_VERIFIED) or st == "handed_off":
                    continue
                short.append(a)
                why.add(word or st)
            if short:
                found["false_done"].append({"task_id": tid, "status": t.get("status"),
                                            "actions": short[:10], "states": sorted(why)})

    summary = {"decisions": len(decisions), **counts,
               "duplicate_key": len(found["duplicate_key"]),
               "false_done": len(found["false_done"])}
    return {"ok": not (counts["open"] or counts["duplicate"] or found["duplicate_key"]
                       or found["false_done"]),
            "counts": summary,
            "open": found["open"][:limit], "duplicate": found["duplicate"][:limit],
            "unconfirmed": found["unconfirmed"][:limit],
            "duplicate_key": found["duplicate_key"][:limit],
            "false_done": found["false_done"][:limit],
            "window_days": round(float(window) / 86400.0, 2)}


def doctor_check() -> tuple:
    """(state, code) for the Doctor: counts only. Unverified when an outward
    action has no terminal receipt, more than one, the same request ran twice,
    or a task said done without its outward actions done."""
    try:
        c = audit()["counts"]
    except Exception as e:  # noqa: BLE001
        return "unverified", "outward_audit_unavailable:" + type(e).__name__
    code = "open:%d,duplicate:%d,duplicate_key:%d,false_done:%d,closed:%d,waiting:%d,unconfirmed:%d" % (
        c["open"], c["duplicate"], c["duplicate_key"], c["false_done"], c["closed"], c["waiting"],
        c.get("unconfirmed", 0))
    if c["open"] or c["duplicate"] or c["duplicate_key"] or c["false_done"]:
        return "unverified", code
    return "healthy", code
