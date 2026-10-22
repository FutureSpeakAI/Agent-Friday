"""Goal checking on receipts (north star §17.12; BD-12 slice G7).

A milestone is checked against what is on record for its run before
anything is read into the words that run produced:

  1. its task's own journal: every tool call, and whether it went through
     (decided by Friday from the call's result, never by the model: a card
     raised, a refusal, an unknown tool or bad arguments is not "went through");
  2. the signed action receipts carrying its task id (decision-bom.jsonl),
     each signature checked, counted only when the checkpoint allowed an
     outward action;
  3. the files its task WROTE (task_ledger.files_written: only paths given
     to a local-write tool that went through), looked up on disk now and
     counted only if they changed after the run began.

Each condition the goal names as proof is judged on those, and the verdict
cites the items that meet it:

  file           a file the run wrote that exists now, named if the condition
                 names one ("itinerary.md")
  message_sent   an outward tool call that went through AND a signed receipt
                 that allowed it
  receipt        a signed receipt that allowed an outward action
  link           an address returned by a write or outward tool that went
                 through, quoted exactly in the result
  owner_confirms not Friday's to judge: the goal can end met only on the
                 owner's judgment
  text_check     graded on the run's words

When the record holds, the success criteria (and any text_check) are graded
on the words as well: the record shows the work happened, the grade that it
is what was asked. If a source cannot be read, the check says it could not
run, never that the proof is missing.

Goal-level proof (evidence_required) is judged when the goal completes,
against every milestone's run (judge_goal).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_FILENAME = re.compile(r"[\w.-]+\.[A-Za-z0-9]{1,8}\b")
_RECEIPT_TAIL = 32 * 1024 * 1024
_KEEP_CALLS = 2000


class _Unreadable(Exception):
    pass


def _outward_tools() -> set:
    try:
        from agent_friday.governance import action_gate as ag
        return set(getattr(ag, "OUTWARD_TOOLS", ()) or ())
    except Exception:
        return set()


def _ring(name: str) -> int:
    try:
        from agent_friday.services import agent
        return int(agent.TOOL_RINGS.get(name, 2))
    except Exception:
        return 2


def _journal_calls(task_id: str) -> List[Dict[str, Any]]:
    from agent_friday.services import task_journal as tj
    try:
        evs = tj.read(task_id)
    except Exception as e:
        raise _Unreadable(f"the run's journal ({type(e).__name__})")
    calls = [e for e in evs if isinstance(e, dict) and e.get("kind") == "tool_call"]
    return calls[-_KEEP_CALLS:]


def _receipts(task_id: str) -> List[Dict[str, Any]]:
    import json
    from agent_friday.governance import action_gate as ag
    try:
        p = ag.receipts_path()
        if not p.exists():
            return []
        size = p.stat().st_size
        with open(p, "rb") as fh:
            if size > _RECEIPT_TAIL:
                fh.seek(size - _RECEIPT_TAIL)
                fh.readline()
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        raise _Unreadable(f"the action receipts ({type(e).__name__})")
    out = []
    for raw in lines:
        if task_id not in raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("task_id") == task_id:
            try:
                ok = bool(ag.verify_receipt(row))
            except Exception:
                ok = False
            out.append(dict(row, _verified=ok))
    return out


def _files(task_id: str, since: Optional[float]) -> List[Dict[str, Any]]:
    if since is None:
        return []                         # when the run began is unknown: no file can count
    from agent_friday.services import task_ledger as tl
    led = tl.load(task_id) or {}
    out = []
    for f in (led.get("files_written") or [])[:200]:
        try:
            p = Path(os.path.expanduser(str(f)))
            st = p.stat()
        except (OSError, ValueError):
            continue
        if p.is_file() and st.st_mtime >= since - 1:
            out.append({"path": str(p), "mtime": st.st_mtime, "bytes": st.st_size})
    return out


def collect(task_ids: List[str], since: Optional[float]) -> Dict[str, Any]:
    """What is on record for one or more runs. `errors` names any source
    that could not be read."""
    ev: Dict[str, Any] = {"calls": [], "receipts": [], "files": [], "errors": []}
    for tid in [t for t in task_ids if t]:
        for key, fn in (("calls", lambda t=tid: _journal_calls(t)), ("receipts", lambda t=tid: _receipts(t)),
                        ("files", lambda t=tid: _files(t, since))):
            try:
                ev[key].extend(fn())
            except _Unreadable as e:
                ev["errors"].append(str(e))
            except Exception as e:
                ev["errors"].append(f"{key} ({type(e).__name__})")
    return ev


def _allowed_outward(receipts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    outward = _outward_tools()
    return [r for r in receipts if r.get("_verified") and r.get("decision") == "allow"
            and (str(r.get("class") or "").lower() == "outward" or r.get("tool") in outward)]


def judge(condition: Dict[str, str], ev: Dict[str, Any], text: str) -> Dict[str, Any]:
    """One condition against the record: {what, kind, met, cites, why}.
    `met` is None for a condition only the owner can judge."""
    kind, what = condition.get("kind") or "text_check", str(condition.get("what") or "")
    out: Dict[str, Any] = {"what": what, "kind": kind, "met": False, "cites": [], "why": ""}
    if kind == "file":
        names = {n.lower() for n in _FILENAME.findall(what)}
        hits = [f for f in ev["files"] if not names or Path(f["path"]).name.lower() in names]
        out["cites"] = [{"source": "file on disk", "path": f["path"], "bytes": f["bytes"]} for f in hits[:5]]
        out["met"] = bool(hits)
        out["why"] = "" if hits else ("no file named " + ", ".join(sorted(names)) + " was written by the run"
                                       if names else "the run wrote no file that is there now")
    elif kind == "message_sent":
        allowed = _allowed_outward(ev["receipts"])
        allowed_tools = {r.get("tool") for r in allowed}
        sends = [e for e in ev["calls"] if e.get("ok") is True and e.get("name") in allowed_tools]
        out["cites"] = [{"source": "task journal", "seq": e.get("seq"), "tool": e.get("name")} for e in sends[:5]] + \
                       [{"source": "action receipt", "tool": r.get("tool"), "decision": "allow"} for r in allowed[:5]]
        out["met"] = bool(sends)
        out["why"] = "" if sends else "no outward action went through with a signed receipt"
    elif kind == "receipt":
        allowed = _allowed_outward(ev["receipts"])
        out["cites"] = [{"source": "action receipt", "tool": r.get("tool"), "decision": "allow"} for r in allowed[:5]]
        out["met"] = bool(allowed)
        out["why"] = "" if allowed else "no signed receipt allowed an outward action for the run"
    elif kind == "link":
        outward = _outward_tools()
        returned = set()
        for e in ev["calls"]:
            if e.get("ok") is True and (e.get("name") in outward or _ring(str(e.get("name"))) == 1):
                returned.update(_URL.findall(str(e.get("result_summary") or "")))
        urls = [u for u in _URL.findall(text or "") if u in returned]
        out["cites"] = [{"source": "task journal", "url": u} for u in urls[:5]]
        out["met"] = bool(urls)
        out["why"] = "" if urls else "no address in the result was returned by an action that went through"
    elif kind == "owner_confirms":
        out["met"] = None
        out["why"] = "only you can confirm this"
    return out


def verify(goal: Dict[str, Any], milestone: Dict[str, Any], text: str, criteria: str, *,
           task_id: Optional[str], since: Optional[float],
           grade: Callable[[str, str], Dict[str, Any]]) -> Dict[str, Any]:
    """The verdict for one milestone run."""
    conditions = [c for c in (milestone.get("evidence") or []) if isinstance(c, dict)]
    typed = [c for c in conditions if (c.get("kind") or "text_check") not in ("text_check",)]
    texts = [c for c in conditions if (c.get("kind") or "text_check") == "text_check"]
    ev = collect([task_id] if task_id else [], since)
    record = {"tool_calls": len(ev["calls"]),
              "tool_calls_through": sum(1 for e in ev["calls"] if e.get("ok") is True),
              "receipts_allowed": len(_allowed_outward(ev["receipts"])),
              "files_written": [f["path"] for f in ev["files"]][:10]}
    if not typed:
        verdict = dict(grade(text, criteria + ("\nAlso: " + "; ".join(str(c.get("what")) for c in texts)
                                               if texts else "")))
        verdict.update(basis="text", record=record,
                       conditions=[{"what": c.get("what"), "kind": "text_check", "met": bool(verdict.get("passed")),
                                    "cites": [], "why": "graded on the run's words"} for c in texts])
        return verdict
    judged = [judge(c, ev, text) for c in typed]
    unmet = [j for j in judged if j["met"] is False]
    if unmet and ev["errors"]:
        return {"status": "skipped", "passed": False, "score": None, "threshold": 1.0,
                "critique": "the check could not read " + ", ".join(ev["errors"]), "suggestions": "",
                "basis": "receipts", "conditions": judged, "record": record}
    if unmet:
        return {"status": "ok", "passed": False, "score": 0.0, "threshold": 1.0,
                "critique": "; ".join(f"“{j['what']}”: {j['why']}" for j in unmet), "suggestions": "",
                "basis": "receipts", "conditions": judged, "record": record}
    # The record holds: now the words, against the success criteria and any
    # text_check, so the work is also what was asked.
    tv = dict(grade(text, criteria + ("\nAlso: " + "; ".join(str(c.get("what")) for c in texts) if texts else "")))
    for c in texts:
        judged.append({"what": c.get("what"), "kind": "text_check", "met": bool(tv.get("passed")),
                       "cites": [], "why": "graded on the run's words"})
    needs_owner = any(j["met"] is None for j in judged)
    return dict(tv, basis="receipts+text", conditions=judged, record=record,
                needs_owner_confirm=needs_owner or None)


def judge_goal(goal: Dict[str, Any]) -> Dict[str, Any]:
    """The goal's own proof (evidence_required), judged at completion against
    every milestone's run. {met: bool|None, conditions, errors}."""
    conds = [c for c in (goal.get("evidence_required") or []) if isinstance(c, dict)
             and (c.get("kind") or "text_check") != "text_check"]
    if not conds:
        return {"met": True, "conditions": [], "errors": []}
    tids, starts = [], []
    for m in goal.get("milestones") or []:
        run = (m.get("last_run") or m.get("run") or {}) if isinstance(m, dict) else {}
        if run.get("task_id"):
            tids.append(run["task_id"])
        if run.get("started_at"):
            starts.append(float(run["started_at"]))
    ev = collect(tids, min(starts) if starts else None)
    texts = " ".join(str((m.get("artifacts") or [""])[0]) for m in goal.get("milestones") or [] if isinstance(m, dict))
    judged = [judge(c, ev, texts) for c in conds]
    if any(j["met"] is False for j in judged):
        met: Optional[bool] = False
    elif any(j["met"] is None for j in judged):
        met = None
    else:
        met = True
    return {"met": met, "conditions": judged, "errors": ev["errors"]}
