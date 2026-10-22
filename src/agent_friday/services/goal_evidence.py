"""Goal checking on receipts (north star §17.12; BD-12 slice G7).

A milestone is checked against what is on record before anything is read
into the words its run produced:

  1. its task's own journal: every tool call it made, whether it went
     through, and what came back (task_journal, written as it ran);
  2. the signed action receipts carrying its task id (decision-bom.jsonl),
     each signature checked;
  3. the files its task wrote, looked up on disk now (task_ledger.files),
     counted only if they exist and changed after the run began.

Each condition the goal names as proof (goal_definition EVIDENCE_KINDS) is
judged on those, and the verdict cites the items that meet it:

  file           a file the run wrote that exists now (named, if the
                 condition names one, e.g. "itinerary.md")
  message_sent   a send tool call that went through, with its receipt
  receipt        a signed action receipt for the run
  link           an address the run produced that a tool call returned
  owner_confirms cannot be checked by Friday: it waits for the owner
  text_check     the only kind graded on the run's words (qa_gates), and
                 the verdict says so

Only when every condition is met by a citation (and any text_check passes)
does the milestone pass, scored 1.0, basis "receipts". A goal with no typed
proof is graded on its words as before (basis "text"), and still lists what
its run did, so the owner can see what the grade rests on.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_SEND = re.compile(r"(send|reply|forward|post|publish|sms|message)", re.I)
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_FILENAME = re.compile(r"[\w.-]+\.[A-Za-z0-9]{1,8}\b")
MAX_EVENTS = 2000


def _journal_calls(task_id: Optional[str]) -> List[Dict[str, Any]]:
    if not task_id:
        return []
    try:
        from agent_friday.services import task_journal as tj
        evs = tj.read(task_id, limit=MAX_EVENTS)
    except Exception:
        return []
    return [e for e in evs if isinstance(e, dict) and e.get("kind") == "tool_call"]


def _receipts(task_id: Optional[str]) -> List[Dict[str, Any]]:
    if not task_id:
        return []
    try:
        from agent_friday.governance import action_gate as ag
        p = ag.receipts_path()
        if not p.exists():
            return []
        size = p.stat().st_size
        with open(p, "rb") as fh:
            if size > 8 * 1024 * 1024:
                fh.seek(size - 8 * 1024 * 1024)
                fh.readline()
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except Exception:
        return []
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
                row["_verified"] = bool(ag.verify_receipt({k: v for k, v in row.items() if k != "_verified"}))
            except Exception:
                row["_verified"] = False
            out.append(row)
    return out


def _files(task_id: Optional[str], since: Optional[float]) -> List[Dict[str, Any]]:
    if not task_id:
        return []
    try:
        from agent_friday.services import task_ledger as tl
        led = tl.load(task_id) or {}
    except Exception:
        led = {}
    out = []
    for f in (led.get("files") or [])[:200]:
        try:
            p = Path(os.path.expanduser(str(f)))
            st = p.stat()
        except (OSError, ValueError):
            continue
        if p.is_file() and (since is None or st.st_mtime >= since - 1):
            out.append({"path": str(p), "mtime": st.st_mtime, "bytes": st.st_size})
    return out


def collect(task_id: Optional[str], since: Optional[float] = None) -> Dict[str, List[Dict[str, Any]]]:
    """What is on record for one run: tool calls, signed receipts, files."""
    return {"calls": _journal_calls(task_id), "receipts": _receipts(task_id), "files": _files(task_id, since)}


def _cite_call(e: Dict[str, Any]) -> Dict[str, Any]:
    return {"source": "task journal", "seq": e.get("seq"), "tool": e.get("name"), "ok": bool(e.get("ok"))}


def judge(condition: Dict[str, str], ev: Dict[str, List[Dict[str, Any]]], text: str) -> Dict[str, Any]:
    """One condition against the record. Returns {what, kind, met, cites, why}."""
    kind, what = condition.get("kind") or "text_check", str(condition.get("what") or "")
    out: Dict[str, Any] = {"what": what, "kind": kind, "met": False, "cites": [], "why": ""}
    if kind == "file":
        names = {n.lower() for n in _FILENAME.findall(what)}
        hits = [f for f in ev["files"] if not names or Path(f["path"]).name.lower() in names]
        out["cites"] = [{"source": "file on disk", "path": f["path"], "bytes": f["bytes"]} for f in hits[:5]]
        out["met"] = bool(hits)
        out["why"] = "" if hits else ("no file named " + ", ".join(sorted(names)) if names else
                                       "the run wrote no file that is there now")
    elif kind == "message_sent":
        sends = [e for e in ev["calls"] if e.get("ok") and _SEND.search(str(e.get("name") or ""))]
        signed = [r for r in ev["receipts"] if r.get("_verified") and _SEND.search(str(r.get("tool") or ""))]
        out["cites"] = [_cite_call(e) for e in sends[:5]] + \
                       [{"source": "action receipt", "tool": r.get("tool"), "decision": r.get("decision")}
                        for r in signed[:5]]
        out["met"] = bool(sends)
        out["why"] = "" if sends else "no send went through in the run's record"
    elif kind == "receipt":
        signed = [r for r in ev["receipts"] if r.get("_verified")]
        out["cites"] = [{"source": "action receipt", "tool": r.get("tool"), "decision": r.get("decision")}
                        for r in signed[:5]]
        out["met"] = bool(signed)
        out["why"] = "" if signed else "no signed action receipt carries the run's task"
    elif kind == "link":
        returned = " ".join(str(e.get("result_summary") or "") for e in ev["calls"] if e.get("ok"))
        urls = [u for u in _URL.findall(text or "") if u in returned]
        out["cites"] = [{"source": "task journal", "url": u} for u in urls[:5]]
        out["met"] = bool(urls)
        out["why"] = "" if urls else "no address in the result was returned by a tool call"
    elif kind == "owner_confirms":
        out["why"] = "only you can confirm this"
    return out


def verify(goal: Dict[str, Any], milestone: Dict[str, Any], text: str, criteria: str, *,
           task_id: Optional[str], since: Optional[float], grade) -> Dict[str, Any]:
    """The verdict for one milestone run. `grade(text, criteria)` is the text
    check (qa_gates.evaluate_text), called only for text_check conditions or
    a goal with no typed proof."""
    conditions = list(milestone.get("evidence") or [])
    if not conditions and _is_last(goal, milestone):
        conditions = list(goal.get("evidence_required") or [])
    ev = collect(task_id, since)
    record = {"tool_calls": len(ev["calls"]), "tool_calls_ok": sum(1 for e in ev["calls"] if e.get("ok")),
              "receipts": len(ev["receipts"]), "files": [f["path"] for f in ev["files"]][:10]}
    typed = [c for c in conditions if isinstance(c, dict) and (c.get("kind") or "text_check") != "text_check"]
    if not typed:
        verdict = dict(grade(text, criteria))
        verdict.update(basis="text", record=record,
                       conditions=[{"what": c.get("what"), "kind": "text_check", "met": bool(verdict.get("passed")),
                                    "cites": [], "why": "graded on the run's words"}
                                   for c in conditions if isinstance(c, dict)])
        return verdict
    judged = [judge(c, ev, text) for c in typed]
    unmet = [j for j in judged if not j["met"]]
    texts = [c for c in conditions if isinstance(c, dict) and (c.get("kind") or "text_check") == "text_check"]
    text_verdict = None
    if not unmet and texts:
        # Receipts first: the words are graded only once the record holds.
        text_verdict = grade(text, criteria + "\nAlso: " + "; ".join(str(c.get("what")) for c in texts))
        for c in texts:
            judged.append({"what": c.get("what"), "kind": "text_check", "met": bool(text_verdict.get("passed")),
                           "cites": [], "why": "graded on the run's words"})
    passed = not unmet and (text_verdict is None or bool(text_verdict.get("passed")))
    critique = "; ".join(f"“{j['what']}”: {j['why']}" for j in judged if not j["met"])
    return {"status": "ok", "passed": passed, "score": 1.0 if passed else 0.0, "threshold": 1.0,
            "critique": critique or ("" if passed else (text_verdict or {}).get("critique", "")),
            "suggestions": "", "basis": "receipts" + ("+text" if text_verdict is not None else ""),
            "conditions": judged, "record": record}


def _is_last(goal: Dict[str, Any], milestone: Dict[str, Any]) -> bool:
    ms = [m for m in goal.get("milestones") or [] if isinstance(m, dict)]
    open_ms = [m for m in ms if m.get("status") != "done"
               and m.get("milestone_id") != milestone.get("milestone_id")]
    return not open_ms
