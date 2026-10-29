"""Outcome memory (north star §4.7, NS-4.7-1; BD-12 slice G9).

What Friday remembers about how a piece of work turned out, as a structured
record with provenance and validity, never as free text:

``decision``       the ref of what was decided (an approval card, a goal's
                   definition receipt, an action id) and a short summary the
                   owner can read;
``evidence_refs``  ids only: signed goal receipts, approval ids, action ids;
``attempts``       a count and the task / step refs of the runs;
``outcome``        succeeded | failed | partial | unknown, and ``verified``
                   (True only when a check observed it, never a model's word);
``unresolved``     a few short lines, in Friday's fixed words or the names the
                   owner gave the goal's steps (no model output, no critique);
``corrections``    the owner's corrections, as events (who, when, which field,
                   the value before and after); the effective outcome is the
                   newest of the recorded outcome and the owner's corrections;
``revisit_at``     when it is worth another look (REVISIT_DAYS by outcome);
``stale_after``    when it may no longer be true (STALE_DAYS by kind);
``status``         open | resolved as stored; ``stale`` is computed on read
                   (``view``), never written back.

Written when a goal milestone ends (passed, failed its check, accepted by the
owner), when a goal ends (met, failed, ended by the owner), and when an
approved outward action taken inside a task closes with a verified or failed
outcome. Off the record nothing is written (``off_record.skip``).

Store: one file, ``<friday home>/outcomes/records.bin``, the whole JSON
encrypted with Friday's key store (credential_store.protect), written atomically
under a lock. If nothing can encrypt it, nothing is written (never plain text).
Bounded: RECORDS_MAX records, resolved ones dropped oldest first.

Recall (``context_block``) shows ids, the summary, the "as of" date and the stale
flag, and says in words that this is memory, not the current state. Home gets
one quiet item when revisits are due (desktop_card_sources). The owner sees,
corrects, resolves and forgets records at /api/outcomes (routes/goals.py).
Forgetting a goal, an action, a task or one record removes its records and
returns a deletion receipt (ids and counts only), also kept in the action ledger.
Forgetting a person (services/forget_registry) removes every record whose words
name them.

Leaf module: no Flask; never raises to a caller.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_log = logging.getLogger("friday.outcome_memory")

OUTCOMES = ("succeeded", "failed", "partial", "unknown")
STATUSES = ("open", "resolved")
KINDS = ("milestone", "goal", "action")
#: Days until a record is worth another look, by its (effective) outcome. A
#: record that still has unresolved items comes back after the shorter time.
REVISIT_DAYS = {"failed": 30, "partial": 30, "unknown": 30, "succeeded": 90}
UNRESOLVED_REVISIT_DAYS = 30
#: Days after which what a record says may no longer be true, by kind.
STALE_DAYS = {"milestone": 180, "goal": 365, "action": 90}
RECORDS_MAX = 500
LIST_MAX = 200
SUMMARY_MAX = 160
UNRESOLVED_MAX = 5
UNRESOLVED_ITEM_MAX = 120
REFS_MAX = 20
CORRECTIONS_MAX = 50
NOTE_MAX = 200
RECALL_TOP = 3

#: Tests point the store somewhere else.
DIR_OVERRIDE: Optional[Path] = None

_LOCK = threading.RLock()


# ── storage ──────────────────────────────────────────────────────────────────

def _dir() -> Path:
    if DIR_OVERRIDE is not None:
        return Path(DIR_OVERRIDE)
    from agent_friday.paths import friday_home
    return friday_home() / "outcomes"


def _path() -> Path:
    return _dir() / "records.bin"


class _NotWritable(Exception):
    pass


def _protect(raw: bytes) -> bytes:
    from agent_friday.services import credential_store as cs
    try:
        blob, method = cs.protect(raw)
    except Exception as e:
        raise _NotWritable(type(e).__name__) from None
    if method == "plaintext" or blob == raw:
        raise _NotWritable("no encryption")
    return blob


def _unprotect(blob: bytes) -> bytes:
    from agent_friday.services import credential_store as cs
    if cs.looks_protected(blob):
        return cs.unprotect(blob)
    raise ValueError("the outcome store is not encrypted")


def _load() -> Dict[str, Any]:
    p = _path()
    try:
        blob = p.read_bytes()
    except (FileNotFoundError, OSError):
        return {"version": 1, "records": {}}
    try:
        data = json.loads(_unprotect(blob).decode("utf-8"))
        if isinstance(data, dict) and isinstance(data.get("records"), dict):
            return data
    except Exception as e:
        _log.warning("outcome memory could not be read (%s)", type(e).__name__)
        raise
    return {"version": 1, "records": {}}


def _save(data: Dict[str, Any]) -> None:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    blob = _protect(raw)
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    p = _path()
    tmp = p.with_name(p.name + ".%s.tmp" % uuid.uuid4().hex[:8])
    with open(tmp, "wb") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.skip("outcome_memory"))
    except Exception:
        return False


# ── small helpers ────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _short(v: Any, n: int = SUMMARY_MAX) -> str:
    s = "".join(c for c in " ".join(str(v if v is not None else "").split()) if ord(c) >= 32)
    return s[:n]


def _ref(v: Any) -> Optional[str]:
    s = _short(v, 120)
    return s or None


def _refs(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for v in values or []:
        r = _ref(v)
        if r and r not in out:
            out.append(r)
    return out[-REFS_MAX:]


def _unresolved(items: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for v in items or []:
        s = _short(v, UNRESOLVED_ITEM_MAX)
        if s and s not in out:
            out.append(s)
    return out[:UNRESOLVED_MAX]


def _revisit_ts(outcome: str, unresolved: List[str], base: float) -> float:
    days = REVISIT_DAYS.get(outcome, UNRESOLVED_REVISIT_DAYS)
    if unresolved:
        days = min(days, UNRESOLVED_REVISIT_DAYS)
    return base + days * 86400


def _stale_ts(kind: str, base: float) -> float:
    return base + STALE_DAYS.get(kind, 180) * 86400


def _trim(records: Dict[str, Any]) -> None:
    if len(records) <= RECORDS_MAX:
        return
    order = sorted(records.values(),
                   key=lambda r: (r.get("status") != "resolved", _ts(r.get("updated_at")) or 0))
    for r in order[:len(records) - RECORDS_MAX]:
        records.pop(r.get("id"), None)


# ── writing ──────────────────────────────────────────────────────────────────

def record(*, kind: str, subject: Dict[str, Any], decision_ref: Any, decision_summary: Any,
           outcome: str, verified: bool, evidence_refs: Iterable[Any] = (),
           attempts: int = 0, attempt_refs: Iterable[Any] = (),
           unresolved: Iterable[Any] = (), now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Write (or update) the record for `subject`. One open record per subject:
    a later end of the same milestone, goal or action updates it (evidence and
    attempt refs are kept, corrections are kept, the revisit date moves).
    Returns the stored record, or None off the record or when nothing could
    be written. Never raises."""
    if kind not in KINDS or outcome not in OUTCOMES:
        return None
    if _off_record():
        return None
    subj = {k: _ref(v) for k, v in (subject or {}).items()
            if k in ("goal_id", "milestone_id", "action_id", "task_id") and _ref(v)}
    ref = subject_ref(kind, subj)
    if not ref:
        return None
    t = _now() if now is None else float(now)
    try:
        with _LOCK:
            data = _load()
            recs = data["records"]
            rec = next((r for r in recs.values() if r.get("subject_ref") == ref), None)
            if rec is None:
                rec = {"id": "out_" + uuid.uuid4().hex[:12], "kind": kind, "subject": subj,
                       "subject_ref": ref, "created_at": _iso(t), "corrections": [],
                       "evidence_refs": [], "attempts": {"count": 0, "refs": []}}
                recs[rec["id"]] = rec
            unres = _unresolved(unresolved)
            rec["subject"] = dict(rec.get("subject") or {}, **subj)
            rec["decision"] = {"ref": _ref(decision_ref), "summary": _short(decision_summary)}
            rec["evidence_refs"] = _refs(list(rec.get("evidence_refs") or []) + list(evidence_refs or []))
            prior = rec.get("attempts") or {}
            rec["attempts"] = {"count": max(int(attempts or 0), 0),
                               "refs": _refs(list(prior.get("refs") or []) + list(attempt_refs or []))}
            rec["outcome"] = {"result": outcome, "verified": bool(verified), "at": _iso(t)}
            rec["unresolved"] = unres
            rec["revisit_at"] = _iso(_revisit_ts(outcome, unres, t))
            rec["stale_after"] = _iso(_stale_ts(kind, t))
            rec["status"] = "open"
            rec.pop("resolved_at", None)
            rec["updated_at"] = _iso(t)
            _trim(recs)
            _save(data)
            return json.loads(json.dumps(rec))
    except Exception as e:
        _log.warning("outcome memory: record not written (%s)", type(e).__name__)
        return None


def subject_ref(kind: str, subject: Dict[str, Any]) -> Optional[str]:
    if kind == "milestone" and subject.get("goal_id") and subject.get("milestone_id"):
        return "milestone:%s/%s" % (subject["goal_id"], subject["milestone_id"])
    if kind == "goal" and subject.get("goal_id"):
        return "goal:%s" % subject["goal_id"]
    if kind == "action" and subject.get("action_id"):
        return "action:%s" % subject["action_id"]
    return None


def _mutate(rid: str, fn) -> Dict[str, Any]:
    try:
        with _LOCK:
            data = _load()
            rec = data["records"].get(str(rid or ""))
            if rec is None:
                return {"ok": False, "error": "not_found"}
            err = fn(rec)
            if err:
                return {"ok": False, "error": err}
            rec["updated_at"] = _iso(_now())
            _save(data)
            return {"ok": True, "record": view(rec)}
    except _NotWritable:
        return {"ok": False, "error": "not_writable"}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def correct(rid: str, *, outcome: Optional[str] = None, unresolved: Optional[List[Any]] = None,
            note: Optional[str] = None, by: str = "owner") -> Dict[str, Any]:
    """The owner corrects a record: one event per changed field, appended (the
    recorded outcome is never overwritten). An owner's outcome is the effective
    outcome from then on, until a newer recorded outcome; it is never marked
    verified (an owner's word is not a check)."""
    if outcome is not None and outcome not in OUTCOMES:
        return {"ok": False, "error": "outcome is one of " + ", ".join(OUTCOMES)}
    if outcome is None and unresolved is None and not note:
        return {"ok": False, "error": "nothing to correct"}
    if unresolved is not None and not isinstance(unresolved, list):
        return {"ok": False, "error": "unresolved is a list of short lines"}

    def fn(rec):
        t = _iso(_now())
        events = list(rec.get("corrections") or [])
        cur = effective(rec)
        if outcome is not None:
            events.append({"id": "cor_" + uuid.uuid4().hex[:10], "at": t, "by": by, "field": "outcome",
                           "before": cur["result"], "after": outcome})
        if unresolved is not None:
            after = _unresolved(unresolved)
            events.append({"id": "cor_" + uuid.uuid4().hex[:10], "at": t, "by": by, "field": "unresolved",
                           "before": list(rec.get("unresolved") or []), "after": after})
            rec["unresolved"] = after
        if note:
            events.append({"id": "cor_" + uuid.uuid4().hex[:10], "at": t, "by": by, "field": "note",
                           "after": _short(note, NOTE_MAX)})
        rec["corrections"] = events[-CORRECTIONS_MAX:]
        eff = effective(rec)
        base = _ts(t) or _now()
        rec["revisit_at"] = _iso(_revisit_ts(eff["result"], list(rec.get("unresolved") or []), base))
        rec["status"] = "open"
        rec.pop("resolved_at", None)
    return _mutate(rid, fn)


def resolve(rid: str, *, note: Optional[str] = None, by: str = "owner") -> Dict[str, Any]:
    """The owner says this needs no more looking at. Nothing is deleted."""
    def fn(rec):
        t = _iso(_now())
        rec["status"] = "resolved"
        rec["resolved_at"] = t
        events = list(rec.get("corrections") or [])
        events.append({"id": "cor_" + uuid.uuid4().hex[:10], "at": t, "by": by, "field": "status",
                       "before": "open", "after": "resolved",
                       **({"note": _short(note, NOTE_MAX)} if note else {})})
        rec["corrections"] = events[-CORRECTIONS_MAX:]
    return _mutate(rid, fn)


# ── reading ──────────────────────────────────────────────────────────────────

def effective(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The outcome as it stands: the newest of the recorded outcome and the
    owner's outcome corrections. `source` says which."""
    o = rec.get("outcome") or {}
    best = {"result": o.get("result") or "unknown", "verified": bool(o.get("verified")),
            "source": "check" if o.get("verified") else "recorded", "at": o.get("at")}
    at = _ts(o.get("at")) or 0
    for c in rec.get("corrections") or []:
        if c.get("field") != "outcome" or c.get("after") not in OUTCOMES:
            continue
        ct = _ts(c.get("at")) or 0
        if ct >= at:
            best = {"result": c["after"], "verified": False, "source": "owner", "at": c.get("at")}
            at = ct
    return best


def view(rec: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    """A record as the owner and recall see it: stale and due computed now."""
    t = _now() if now is None else float(now)
    out = json.loads(json.dumps(rec))
    stale_at = _ts(rec.get("stale_after"))
    revisit = _ts(rec.get("revisit_at"))
    stale = stale_at is not None and t >= stale_at
    out["stale"] = stale
    out["recorded_status"] = rec.get("status") or "open"
    out["status"] = "stale" if stale else out["recorded_status"]
    out["revisit_due"] = (out["recorded_status"] == "open" and revisit is not None and t >= revisit)
    out["effective"] = effective(rec)
    out["as_of"] = str(out["effective"].get("at") or rec.get("updated_at") or "")[:10]
    return out


def _all() -> List[Dict[str, Any]]:
    try:
        with _LOCK:
            return list(_load()["records"].values())
    except Exception:
        return []


def records(*, limit: int = 100, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Every record (bounded), newest first, as views."""
    n = max(1, min(int(limit or 100), LIST_MAX))
    rows = sorted(_all(), key=lambda r: _ts(r.get("updated_at")) or 0, reverse=True)
    return [view(r, now) for r in rows[:n]]


def count() -> int:
    return len(_all())


def get(rid: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    for r in _all():
        if r.get("id") == rid:
            return view(r, now)
    return None


def for_subject(ref: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    for r in _all():
        if r.get("subject_ref") == ref:
            return view(r, now)
    return None


def due_revisits(now: Optional[float] = None, *, limit: int = 20) -> List[Dict[str, Any]]:
    """Open records whose revisit date has come, oldest due first."""
    rows = [view(r, now) for r in _all()]
    rows = [r for r in rows if r["revisit_due"]]
    rows.sort(key=lambda r: _ts(r.get("revisit_at")) or 0)
    return rows[:max(1, int(limit))]


_WORD = re.compile(r"[^\W_]{4,}", re.UNICODE)


def _words(s: Any) -> set:
    return {w.casefold() for w in _WORD.findall(str(s or ""))}


def recall(message: str, now: Optional[float] = None, *, top: int = RECALL_TOP) -> List[Dict[str, Any]]:
    """The records whose summary shares words with the message, as recall
    items: id, summary, outcome, as of, stale. Never the current state."""
    want = _words(message)
    if not want:
        return []
    scored = []
    for r in _all():
        hit = len(want & _words((r.get("decision") or {}).get("summary")))
        if hit:
            scored.append((hit, _ts(r.get("updated_at")) or 0, r))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    out = []
    for _h, _t, r in scored[:max(1, int(top))]:
        v = view(r, now)
        out.append({"id": v["id"], "summary": (v.get("decision") or {}).get("summary") or "",
                    "outcome": v["effective"]["result"], "verified": v["effective"]["verified"],
                    "source": v["effective"]["source"], "as_of": v["as_of"], "stale": v["stale"]})
    return out


RECALL_HEADER = ("Past outcomes (memory as of the date shown, not the current state; "
                 "check the goal or task itself before saying how things stand now):")


def context_block(message: str, now: Optional[float] = None) -> str:
    """The recall lines for a turn's context, or "" when nothing applies."""
    try:
        items = recall(message, now)
    except Exception:
        return ""
    if not items:
        return ""
    lines = ["", "", RECALL_HEADER]
    for it in items:
        how = it["outcome"] + (" (checked)" if it["verified"] else
                               " (your correction)" if it["source"] == "owner" else " (not checked)")
        line = "- %s, as of %s: %s: %s." % (it["id"], it["as_of"] or "unknown date", it["summary"], how)
        if it["stale"]:
            line += " STALE: this may no longer be true."
        lines.append(line)
    return "\n".join(lines) + "\n"


# ── forgetting ───────────────────────────────────────────────────────────────

def _receipt(scope: str, ids: List[str]) -> Dict[str, Any]:
    """The deletion receipt: what was asked, how many went and their ids (no
    summaries). Kept in the action ledger when something was removed."""
    rec = {"receipt_id": "fgt_" + uuid.uuid4().hex[:12], "store": "outcome_memory",
           "scope": scope, "removed": len(ids), "ids": ids, "at": _iso(_now())}
    if ids:
        try:
            from agent_friday.services import action_ledger
            action_ledger.try_append("outcome_memory_forget", dict(rec))
        except Exception:
            pass
    return rec


def _purge(match, scope: str) -> Dict[str, Any]:
    try:
        with _LOCK:
            data = _load()
            gone = [rid for rid, r in data["records"].items() if match(r)]
            for rid in gone:
                data["records"].pop(rid, None)
            if gone:
                _save(data)
    except _NotWritable:
        return {"ok": False, "error": "not_writable", "removed": 0}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "removed": 0}
    return {"ok": True, **_receipt(scope, sorted(gone))}


SUBJECT_KINDS = ("goal", "milestone", "action", "task")


def forget_subject(kind: str, ident: str, *, milestone_id: Optional[str] = None) -> Dict[str, Any]:
    """Remove every record about a goal (its milestones and the actions taken
    for it too), a milestone, an action or a task. Returns a deletion receipt."""
    ident = _ref(ident) or ""
    if kind not in SUBJECT_KINDS or not ident:
        return {"ok": False, "error": "say which goal, milestone, action or task", "removed": 0}

    def match(r):
        s = r.get("subject") or {}
        if kind == "goal":
            return s.get("goal_id") == ident
        if kind == "milestone":
            return s.get("goal_id") == ident and (milestone_id is None or s.get("milestone_id") == milestone_id)
        if kind == "action":
            return s.get("action_id") == ident
        return s.get("task_id") == ident or ident in ((r.get("attempts") or {}).get("refs") or [])
    return _purge(match, "%s:%s" % (kind, ident))


def forget_record(rid: str) -> Dict[str, Any]:
    rid = _ref(rid) or ""
    if not get(rid):
        return {"ok": False, "error": "not_found", "removed": 0}
    return _purge(lambda r: r.get("id") == rid, "record:" + rid)


def _texts(r: Dict[str, Any]) -> str:
    parts = [(r.get("decision") or {}).get("summary") or ""]
    parts += list(r.get("unresolved") or [])
    for c in r.get("corrections") or []:
        for k in ("after", "before", "note"):
            v = c.get(k)
            parts += v if isinstance(v, list) else [v] if isinstance(v, str) else []
    return " \n ".join(str(p) for p in parts).casefold()


def _names_match(names):
    pats = [re.compile(r"(?<!\w)" + re.escape(str(n).casefold()) + r"(?!\w)")
            for n in names if str(n or "").strip()]

    def match(r):
        text = _texts(r)
        return any(p.search(text) for p in pats)
    return match


def people_find(names, emails) -> int:
    m = _names_match(set(names or ()) | set(emails or ()))
    return sum(1 for r in _all() if m(r))


def people_purge(names, emails) -> int:
    out = _purge(_names_match(set(names or ()) | set(emails or ())), "person")
    return int(out.get("removed") or 0)


def export() -> Dict[str, Any]:
    """Everything this store holds, readable (the owner's export)."""
    return {"store": "outcome_memory", "records": [view(r) for r in _all()]}


# ── what writes it ───────────────────────────────────────────────────────────

#: Goal receipt kinds that end a milestone, with the outcome each records.
MILESTONE_END_KINDS = {
    "milestone_complete": "succeeded",
    "milestone_human_approved_override": "succeeded",
    "milestone_manual_confirmed": "succeeded",
    "verification_failed_escalated": "failed",
}
#: Goal states that end a goal.
GOAL_END_STATES = ("met_verified", "met_unverified", "failed", "abandoned")


def _checked(v: Any) -> bool:
    """A check that actually ran and scored (goal_state._all_verified's rule)."""
    v = v if isinstance(v, dict) else {}
    return (v.get("status") == "ok" and v.get("score") is not None
            and not v.get("human_override") and not v.get("needs_owner_confirm"))


def _task_refs(m: Dict[str, Any]) -> List[str]:
    out = []
    for key in ("last_run", "run"):
        r = m.get(key)
        if isinstance(r, dict) and r.get("task_id"):
            out.append(r["task_id"])
    if m.get("_task_id"):
        out.append(m["_task_id"])
    return out


def on_milestone_receipt(goal: Optional[Dict[str, Any]], receipt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A goal receipt was appended (services/goals._append_receipt). A
    milestone-ending receipt writes the milestone's outcome record."""
    try:
        kind = (receipt or {}).get("kind")
        result = MILESTONE_END_KINDS.get(kind)
        if not result or not isinstance(goal, dict):
            return None
        mid = receipt.get("milestone_id")
        m = next((x for x in goal.get("milestones") or [] if x.get("milestone_id") == mid), None) or {}
        v = m.get("verification") or (receipt.get("verified") or {})
        name = _short(m.get("name") or receipt.get("milestone_name") or "a step", 60)
        title = _short(goal.get("title") or "a goal", 60)
        if result == "succeeded":
            verified = kind == "milestone_complete" and bool(v.get("passed")) and _checked(v)
            unresolved = []
        else:
            verified = _checked(v)
            unresolved = ["“%s” did not pass its check; it waits for you" % name]
        return record(
            kind="milestone",
            subject={"goal_id": goal.get("goal_id"), "milestone_id": mid},
            decision_ref=m.get("approval_id") or "goal:%s" % goal.get("goal_id"),
            decision_summary="Step “%s” of goal “%s”" % (name, title),
            outcome=result, verified=verified,
            evidence_refs=[receipt.get("receipt_id")] + ([m.get("approval_id")] if m.get("approval_id") else []),
            attempts=int(m.get("attempts") or 0), attempt_refs=_task_refs(m),
            unresolved=unresolved)
    except Exception as e:
        _log.warning("outcome memory: milestone hook failed (%s)", type(e).__name__)
        return None


def on_goal_end(goal: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A goal moved into an end state (services/goals._mutate_goal)."""
    try:
        if not isinstance(goal, dict):
            return None
        st = goal.get("status")
        if st not in GOAL_END_STATES:
            return None
        ms = [m for m in goal.get("milestones") or [] if isinstance(m, dict)]
        done = [m for m in ms if m.get("status") == "done"]
        left = [m for m in ms if m.get("status") != "done"]
        if st == "met_verified":
            result, verified = "succeeded", True
        elif st == "met_unverified":
            result, verified = "succeeded", False
        elif st == "failed":
            result, verified = "failed", False
        else:
            result, verified = ("partial" if done else "unknown"), False
        receipts = [r for r in goal.get("receipts") or [] if isinstance(r, dict)]
        defined = next((r.get("receipt_id") for r in receipts if r.get("kind") == "goal_defined"), None)
        words = {"met_verified": "met, checked", "met_unverified": "met, on judgment",
                 "failed": "failed", "abandoned": "ended by you"}[st]
        return record(
            kind="goal", subject={"goal_id": goal.get("goal_id")},
            decision_ref=defined or "goal:%s" % goal.get("goal_id"),
            decision_summary="Goal “%s” (%s)" % (_short(goal.get("title") or "a goal", 80), words),
            outcome=result, verified=verified,
            evidence_refs=[r.get("receipt_id") for r in receipts
                           if r.get("kind") in MILESTONE_END_KINDS],
            attempts=sum(int(m.get("attempts") or 0) for m in ms),
            attempt_refs=[m.get("milestone_id") for m in ms],
            unresolved=["“%s” is not done" % _short(m.get("name") or "a step", 60) for m in left])
    except Exception as e:
        _log.warning("outcome memory: goal hook failed (%s)", type(e).__name__)
        return None


def _current_task_id() -> Optional[str]:
    try:
        from agent_friday.services import task_journal
        tid = task_journal.current_task()
        if tid:
            return str(tid)
    except Exception:
        pass
    try:
        from agent_friday.services import agent
        ctx = agent._CURRENT_TOOL_CONTEXT.get() or {}
        if ctx.get("task_id"):
            return str(ctx["task_id"])
    except Exception:
        pass
    return None


def _goal_for_task(task_id: str):
    try:
        from agent_friday.services import goals
        for g in goals.list_goals():
            if g.get("status") in ("archived",):
                continue
            for m in g.get("milestones") or []:
                if task_id in _task_refs(m):
                    return g, m
    except Exception:
        pass
    return None, None


def on_action_closed(*, tool: str, action_id: Optional[str], outcome_word: str,
                     approval: Optional[str] = None, task_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """An approved outward action closed (governance/action_gate.record_outcome)
    with completed_verified or failed. Written only when a task is attached
    (the running task, or the one named); the goal and milestone are found
    from the task when the task is a goal's step."""
    try:
        if outcome_word not in ("completed_verified", "failed") or not action_id:
            return None
        tid = task_id or _current_task_id()
        if not tid:
            return None
        g, m = _goal_for_task(tid)
        tool_words = _short(tool or "an outward action", 40)
        if g is not None:
            summary = "Outward action “%s” for step “%s” of goal “%s”" % (
                tool_words, _short(m.get("name") or "a step", 50), _short(g.get("title") or "a goal", 50))
        else:
            summary = "Outward action “%s” in a background task" % tool_words
        ok = outcome_word == "completed_verified"
        subj = {"action_id": action_id, "task_id": tid}
        if g is not None:
            subj.update(goal_id=g.get("goal_id"), milestone_id=m.get("milestone_id"))
        return record(
            kind="action", subject=subj,
            decision_ref=action_id, decision_summary=summary,
            outcome="succeeded" if ok else "failed", verified=ok,
            evidence_refs=[action_id] + ([approval] if approval else []),
            attempts=1, attempt_refs=[tid],
            unresolved=[] if ok else ["“%s” did not go through" % tool_words])
    except Exception as e:
        _log.warning("outcome memory: action hook failed (%s)", type(e).__name__)
        return None


# ── Home ─────────────────────────────────────────────────────────────────────

def home_item(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """One quiet line for Home when revisits are due, else None."""
    due = due_revisits(now)
    if not due:
        return None
    n = len(due)
    body = ("%d past outcome%s %s due for another look. Open Goals to see %s."
            % (n, "" if n == 1 else "s", "is" if n == 1 else "are", "it" if n == 1 else "them"))
    return {"count": n, "ids": [r["id"] for r in due], "title": "Outcomes to revisit", "body": body,
            "updated_at": max((_ts(r.get("revisit_at")) or 0 for r in due), default=None)}
