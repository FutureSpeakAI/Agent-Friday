"""Starting a goal from conversation (north star §17.4; BD-12 slice G6).

"I want to get the garage cleared out by spring" becomes a goal through the
same definition the Goals card uses (services/goal_definition): Friday turns
what was said into a draft, asks only the questions the goal still needs, and
leaves the draft, with its plain summary, waiting in System > Goals. Only the
owner creates it, by confirming that summary with a click there
(POST /api/goals/define). The tool never creates, starts or changes a goal.

`draft_goal` is loaded on demand (WORKSPACE_TOOLS["on_demand"]), so it adds
nothing to the always-on tool catalogue.

Drafts live in goals/drafts.json: at most MAX_DRAFTS, newest kept. Off the
record nothing is kept: the summary and questions are returned for the
conversation only.

A draft written from conversation is marked as Friday's, and anything in it
that lets Friday do more on her own than the defaults is called out at the top
of its summary, so a looser setting is never confirmed by accident.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE = "on_demand"
MAX_DRAFTS = 20
_LOCK = threading.Lock()


def _path() -> Path:
    from agent_friday.services import goals
    return Path(goals.GOALS_DIR) / "drafts.json"


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return True


def _read() -> List[Dict[str, Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def _write(items: List[Dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(items[-MAX_DRAFTS:], indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def looser_than_usual(goal: Dict[str, Any]) -> List[str]:
    """What a draft lets Friday do on her own beyond the defaults, in words."""
    from agent_friday.services import goal_definition as gd
    rank = {"never": 0, "ask_me": 1, "on_its_own": 2}
    a = goal.get("autonomy") or {}
    return [gd.CATEGORY_WORDS[c] for c in gd.AUTONOMY_CATEGORIES
            if rank.get(a.get(c), 0) > rank[gd.DEFAULT_AUTONOMY[c]]]


def framed_summary(goal: Dict[str, Any]) -> str:
    from agent_friday.services import goal_definition as gd
    lines = ["Friday drafted this goal from your conversation. Nothing happens until you confirm it."]
    loose = looser_than_usual(goal)
    if loose:
        lines.append("Looser than usual: on its own it would " + ", ".join(loose) + ". "
                     "Check that is what you want.")
    return "\n".join(lines) + "\n\n" + gd.summary(goal)


def drafts() -> List[Dict[str, Any]]:
    """The drafts waiting for the owner, newest first, each with its
    questions, summary and summary_id (what /api/goals/define needs)."""
    from agent_friday.services import goal_definition as gd
    out = []
    for d in reversed(_read()):
        goal = gd.normalize(d.get("spec") or {})
        out.append({"draft_id": d.get("draft_id"), "created_at": d.get("created_at"),
                    "title": goal["title"] or "(untitled)", "spec": d.get("spec") or {},
                    "questions": gd.questions(goal), "summary": framed_summary(goal),
                    "summary_id": gd.summary_id(goal)})
    return out


def discard(draft_id: str) -> bool:
    with _LOCK:
        items = _read()
        kept = [d for d in items if d.get("draft_id") != draft_id]
        if len(kept) == len(items):
            return False
        _write(kept)
    return True


def handle_draft_goal(inp=None) -> str:
    """The tool: what the owner said, as a goal draft. Returns the questions
    still to ask and the plain summary; creates nothing."""
    from agent_friday.services import goal_definition as gd
    inp = inp if isinstance(inp, dict) else {}
    spec = {k: inp.get(k) for k in ("title", "intent", "completion_condition", "evaluator",
                                     "evidence_required", "stop_conditions", "constraints", "people",
                                     "deadline", "budget", "autonomy", "milestones") if inp.get(k) is not None}
    # A draft from conversation never carries a principal or permitted tools
    # a model chose: the owner's own definition form sets those.
    goal = gd.normalize(spec)
    questions = gd.questions(goal)
    draft_id = None
    kept = False
    if not _off_record():
        draft_id = inp.get("draft_id") if isinstance(inp.get("draft_id"), str) else None
        with _LOCK:
            items = _read()
            if draft_id and any(d.get("draft_id") == draft_id for d in items):
                items = [dict(d, spec=spec, updated_at=time.time()) if d.get("draft_id") == draft_id else d
                         for d in items]
            else:
                draft_id = "gd_" + uuid.uuid4().hex[:10]
                items.append({"draft_id": draft_id, "created_at": time.time(), "spec": spec})
            _write(items)
        kept = True
    return json.dumps({
        "status": "drafted",
        "draft_id": draft_id,
        "questions": [q["ask"] for q in questions],
        "summary": framed_summary(goal),
        "next": (("Ask the owner these, one at a time, then call draft_goal again with the same draft_id and "
                  "their answers. " if questions else "")
                 + ("The draft waits in System > Goals, where the owner reads the summary and confirms it. "
                    "You cannot create or start it; say so plainly." if kept else
                    "Off the record nothing is kept: read the summary to the owner; they can define it in "
                    "System > Goals when they are back on the record.")),
    }, ensure_ascii=False)


TOOLS = [
    {"name": "draft_goal",
     "description": (
         "Turn something the owner wants to achieve into a goal draft for them to confirm. Pass what they said "
         "(title, intent, completion_condition, evidence_required, stop_conditions, milestones, people, "
         "deadline). Returns only the questions still needed and a plain summary; the draft waits in "
         "System > Goals. It creates and starts nothing: only the owner's click there does. Call again with "
         "draft_id to add their answers."),
     "input_schema": {"type": "object", "properties": {
         "draft_id": {"type": "string", "description": "To update a draft made earlier in this conversation."},
         "title": {"type": "string"},
         "intent": {"type": "string", "description": "What they want, in their words."},
         "completion_condition": {"type": "string", "description": "What would tell them it is done."},
         "evaluator": {"type": "string", "enum": ["friday_checks", "owner_confirms"]},
         "evidence_required": {"type": "array", "items": {"type": "string"}},
         "stop_conditions": {"type": "array", "items": {"type": "string"}},
         "constraints": {"type": "array", "items": {"type": "string"}},
         "people": {"type": "array", "items": {"type": "string"}},
         "deadline": {"type": "string", "description": "YYYY-MM-DD"},
         "budget": {"type": "object", "properties": {"runtime_minutes": {"type": "integer"},
                                                       "money_cents": {"type": "integer"}}},
         "autonomy": {"type": "object", "description": "Only what the owner said; omit for the defaults."},
         "milestones": {"type": "array", "items": {"type": "object", "properties": {
             "name": {"type": "string"}, "outcome": {"type": "string"},
             "planned_actions": {"type": "array", "items": {"type": "string"}}}}}}}},
]
RINGS = {"draft_goal": 1}
HANDLERS = {"draft_goal": handle_draft_goal}


def register(claude_tools, handlers, rings, workspace_tools=None):
    target = (workspace_tools.setdefault(WORKSPACE, [])
              if workspace_tools is not None else claude_tools)
    known = {t["name"] for t in target}
    for t in TOOLS:
        if t["name"] not in known:
            target.append(t)
    handlers.update(HANDLERS)
    rings.update(RINGS)
