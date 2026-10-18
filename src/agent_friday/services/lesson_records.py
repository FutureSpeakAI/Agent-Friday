"""A record for everything Friday learns: when it applies, what taught it, and
how it has done since.

Three kinds of learned thing get a record:

``skill``      a skill Friday wrote with ``learn_skill`` (ref: its folder name).
``lesson``     a line in FRIDAY.md, Friday's own lessons on the core memory card
               (ref: the SHA-256 of the line; the text itself is NOT copied
               here, so an owner delete leaves nothing behind, see ``purge``).
``heuristic``  an active or candidate learning-loop heuristic (learning.db). Its
               record is read through from learning_loop, never copied.

Each record says:

* ``applies_when``  the cue: a skill's triggers and description, a lesson's
  "when" (or "every conversation" when none was given), a heuristic's task type;
* ``taught_by``     a pointer to what taught it, taken from the call's own
  context, never from the model's arguments: conversation id, turn id, task id,
  surface, model, and who wrote it (friday, review, dream);
* ``uses`` / ``outcomes``  how it has done since: a skill counts a use when its
  full procedure is loaded in a conversation (``loaded``, read_skill) or when
  a background task's request matches its triggers so it is offered to Friday
  (``offered``); a task's verified/unverified end is its outcome (conversations
  have no outcome signal yet, so their uses stay unjudged); a lesson is in
  every prompt (``exposure: always_on``), so it counts every finished task
  since it was added; a heuristic carries the learning loop's trials and wins;
* ``replaces`` / ``replaced_by``  the lesson a reworded one replaced, kept as a
  retired record so an old-vs-new comparison has both sides.

``comparison(a, b)`` puts two records side by side (uses, success rate, Wilson
lower bound, whether exposure is attributable). It decides nothing: it is the
input for a future "keep the old version or the new one" card the owner sees.

Store: ``~/.friday/learning_records/records.json`` (atomic replace, a lock).
Leaf module: no Flask, never raises to a caller.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.paths import friday_home

KINDS = ("skill", "lesson", "heuristic")
#: Open (unfinished) contexts kept per record, oldest dropped first.
OPEN_CONTEXTS_MAX = 50
#: Records kept per kind (retired ones are dropped oldest first past this).
RECORDS_MAX = 2000
_TEXT_MAX = 300

_LOCK = threading.RLock()


def _path() -> Path:
    return friday_home() / "learning_records" / "records.json"


def lesson_ref(text: str) -> str:
    """The ref of a FRIDAY.md line: a hash of its cleaned text."""
    try:
        from agent_friday.services.core_memory import _clean
        text = _clean(text)
    except Exception:
        text = " ".join(str(text or "").split())
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:24]


def record_id(kind: str, ref: str) -> str:
    return f"{kind}:{ref}"


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("records"), dict):
            return data
    except (FileNotFoundError, OSError, ValueError):
        pass
    return {"version": 1, "records": {}}


def _save(data: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def _short(v) -> Optional[str]:
    s = " ".join(str(v if v is not None else "").split())[:_TEXT_MAX]
    return s or None


def current_taught_by(source: str = "friday") -> Dict[str, Any]:
    """Where a learned thing came from, read from the running tool call's own
    context (agent's context variables and the turn id). Identifiers only."""
    out: Dict[str, Any] = {"source": source}
    try:
        from agent_friday.services import agent
        ctx = agent._CURRENT_TOOL_CONTEXT.get() or {}
        out["conversation"] = agent._CURRENT_CONVERSATION.get() or ctx.get("conversation_id")
        out["task"] = ctx.get("task_id")
        out["surface"] = agent._CURRENT_SURFACE.get() or None
        out["model"] = agent._CURRENT_MODEL.get() or None
    except Exception:
        pass
    try:
        from agent_friday.services import turn_plan
        out["turn"] = turn_plan.current_turn_id()
    except Exception:
        pass
    return {k: (_short(v) if k != "source" else v) for k, v in out.items() if v not in (None, "")}


def context_key(taught_by: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The unit an outcome is judged on: the task when there is one, else the
    conversation."""
    tb = taught_by if taught_by is not None else current_taught_by()
    if tb.get("task"):
        return f"task:{tb['task']}"
    if tb.get("conversation"):
        return f"conversation:{tb['conversation']}"
    return None


def _blank(kind: str, ref: str, now: float) -> Dict[str, Any]:
    return {"id": record_id(kind, ref), "kind": kind, "ref": ref, "status": "active",
            "applies_when": None, "taught_by": {}, "created": now, "updated": now,
            "replaces": None, "replaced_by": None,
            "exposure": "always_on" if kind == "lesson" else "on_match",
            "revisions": 0, "uses": 0, "loaded": 0, "offered": 0, "last_used": None,
            "outcomes": {"success": 0, "failure": 0}, "open": {}}


def _trim(records: Dict[str, Any]) -> None:
    for kind in KINDS:
        rows = [r for r in records.values() if r.get("kind") == kind]
        if len(rows) <= RECORDS_MAX:
            continue
        retired = sorted((r for r in rows if r.get("status") != "active"),
                         key=lambda r: r.get("updated") or 0)
        for r in retired[:len(rows) - RECORDS_MAX]:
            records.pop(r["id"], None)


def note_taught(kind: str, ref: str, *, applies_when: Any = None,
                taught_by: Optional[Dict[str, Any]] = None,
                replaces: Optional[str] = None) -> Dict[str, Any]:
    """A new learned thing, or a revision of one that already has a record
    (the original teacher is kept; the revision count goes up)."""
    if kind not in KINDS or not ref:
        return {"ok": False, "error": "unknown kind or empty ref"}
    try:
        with _LOCK:
            data = _load()
            recs = data["records"]
            now = time.time()
            rid = record_id(kind, ref)
            rec = recs.get(rid)
            if rec is None:
                rec = _blank(kind, ref, now)
                rec["taught_by"] = dict(taught_by or current_taught_by())
                recs[rid] = rec
            else:
                rec["revisions"] = int(rec.get("revisions") or 0) + 1
                rec["status"] = "active"
                rec["revised_by"] = dict(taught_by or current_taught_by())
            if applies_when is not None:
                rec["applies_when"] = _short(applies_when)
            if replaces and replaces != rid:
                rec["replaces"] = replaces
                old = recs.get(replaces)
                if old is not None:
                    old["status"] = "replaced"
                    old["replaced_by"] = rid
                    old["updated"] = now
            rec["updated"] = now
            _trim(recs)
            _save(data)
            return {"ok": True, "id": rid}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_status(kind: str, ref: str, status: str) -> Dict[str, Any]:
    """active | retired | archived | replaced. A record is never deleted by
    this (see purge for the owner's delete)."""
    try:
        with _LOCK:
            data = _load()
            rec = data["records"].get(record_id(kind, ref))
            if rec is None:
                return {"ok": True, "missing": True}
            rec["status"] = str(status)
            rec["updated"] = time.time()
            _save(data)
            return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_use(kind: str, ref: str, context: Optional[str] = None) -> Dict[str, Any]:
    """The learned thing was put to use (a skill's procedure was loaded). The
    context is remembered so the task's outcome can be credited to it."""
    try:
        with _LOCK:
            data = _load()
            rec = data["records"].get(record_id(kind, ref))
            if rec is None:
                return {"ok": True, "missing": True}
            now = time.time()
            rec["uses"] = int(rec.get("uses") or 0) + 1
            rec["loaded"] = int(rec.get("loaded") or 0) + 1
            rec["last_used"] = now
            ctx = context if context is not None else context_key()
            if ctx:
                opened = dict(rec.get("open") or {})
                opened[ctx] = now
                if len(opened) > OPEN_CONTEXTS_MAX:
                    for k in sorted(opened, key=opened.get)[:len(opened) - OPEN_CONTEXTS_MAX]:
                        opened.pop(k, None)
                rec["open"] = opened
            _save(data)
            return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_outcome(context: Optional[str], success: bool) -> Dict[str, Any]:
    """A task (or conversation) finished. Every record used in it gets the
    outcome once; every active always-on lesson counts it as one more task it
    was in the prompt for."""
    if not context:
        return {"ok": True, "credited": 0}
    try:
        with _LOCK:
            data = _load()
            credited = 0
            key = "success" if success else "failure"
            for rec in data["records"].values():
                opened = dict(rec.get("open") or {})
                in_context = context in opened
                always_on = rec.get("exposure") == "always_on" and rec.get("status") == "active"
                if not (in_context or always_on):
                    continue
                if in_context:
                    opened.pop(context, None)
                    rec["open"] = opened
                else:
                    # In the prompt for this task without a use of its own.
                    rec["uses"] = int(rec.get("uses") or 0) + 1
                outcomes = dict(rec.get("outcomes") or {"success": 0, "failure": 0})
                outcomes[key] = int(outcomes.get(key) or 0) + 1
                rec["outcomes"] = outcomes
                rec["updated"] = time.time()
                credited += 1
            if credited:
                _save(data)
            return {"ok": True, "credited": credited}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def note_task_outcome(task_id: str, prompt: str, success: bool) -> Dict[str, Any]:
    """A background task finished. Skills Friday wrote whose triggers match the
    task's request were offered to her for it (the prompt names them), so each
    counts one use ("offered") and gets the outcome; then everything loaded in
    the task and every always-on lesson is credited (note_outcome). A record
    is credited at most once per task."""
    if not task_id:
        return {"ok": True, "credited": 0}
    ctx = f"task:{task_id}"
    try:
        from agent_friday import skill_registry
        offered = [s.name for s in skill_registry.match_skills(prompt or "", limit=3)
                   if skill_registry.friday_wrote(s)]
    except Exception:
        offered = []
    try:
        with _LOCK:
            data = _load()
            now = time.time()
            for name in offered:
                rec = data["records"].get(record_id("skill", name))
                if rec is None:
                    continue
                rec["offered"] = int(rec.get("offered") or 0) + 1
                opened = dict(rec.get("open") or {})
                if ctx not in opened:
                    rec["uses"] = int(rec.get("uses") or 0) + 1
                    rec["last_used"] = now
                    opened[ctx] = now
                rec["open"] = opened
            if offered:
                _save(data)
    except Exception:
        pass
    return note_outcome(ctx, success)


def purge(kind: str, ref: str) -> Dict[str, Any]:
    """Drop a record entirely: the owner deleted or rewrote the line it is
    about, and a deleted line must not survive in a second file
    (services/core_memory)."""
    try:
        with _LOCK:
            data = _load()
            rid = record_id(kind, ref)
            gone = data["records"].pop(rid, None)
            for rec in data["records"].values():
                if rec.get("replaces") == rid:
                    rec["replaces"] = None
                if rec.get("replaced_by") == rid:
                    rec["replaced_by"] = None
            if gone is not None:
                _save(data)
            return {"ok": True, "purged": gone is not None}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _heuristic_records() -> List[Dict[str, Any]]:
    try:
        from agent_friday.services import learning_loop
        return learning_loop.heuristic_records()
    except Exception:
        return []


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in rec.items() if k != "open"}
    out["open_contexts"] = len(rec.get("open") or {})
    return out


def records(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every record (heuristics read through from the learning loop), newest
    first."""
    try:
        with _LOCK:
            rows = [_public(r) for r in _load()["records"].values()]
    except Exception:
        rows = []
    if kind in (None, "heuristic"):
        rows += _heuristic_records()
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    rows.sort(key=lambda r: r.get("updated") or r.get("created") or 0, reverse=True)
    return rows


def get(rid: str) -> Optional[Dict[str, Any]]:
    for r in records():
        if r.get("id") == rid:
            return r
    return None


def _wilson(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return round(max(0.0, (centre - margin) / denom), 4)


def summary(rec: Dict[str, Any]) -> Dict[str, Any]:
    o = rec.get("outcomes") or {}
    s, f = int(o.get("success") or 0), int(o.get("failure") or 0)
    n = s + f
    return {"id": rec.get("id"), "status": rec.get("status"), "uses": int(rec.get("uses") or 0),
            "judged": n, "success": s, "failure": f,
            "success_rate": round(s / n, 4) if n else None, "wilson_lower": _wilson(s, n),
            "attributable": rec.get("exposure") != "always_on"}


def comparison(old_id: str, new_id: str) -> Dict[str, Any]:
    """Two records side by side for an old-vs-new decision. Reports only; the
    owner decides. ``comparable`` is False while either side has no judged
    outcome or neither is attributable to its own uses."""
    a, b = get(old_id), get(new_id)
    if a is None or b is None:
        return {"ok": False, "error": "record not found", "old": old_id, "new": new_id}
    sa, sb = summary(a), summary(b)
    return {"ok": True, "old": sa, "new": sb,
            "comparable": bool(sa["judged"] and sb["judged"]
                               and (sa["attributable"] or sb["attributable"]))}
