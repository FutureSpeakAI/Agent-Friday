"""Plan-first for big asks (docs/design/active/vibe-coding-salon.md §4.11 item 4).

A big change starts as a plan the user can read: a short markdown artifact
in the chat's panel whose metadata carries the milestones. Nothing is built
until the user approves it, by the panel's "Build this plan" or in their own
words in chat (which the model reports with `plan_approve`; a model cannot
approve its own plan). Then Friday builds milestone by milestone, each one
a few codebase steps, and closes every milestone with a status and, when it
stops, one of the five typed blockers the goals spec names
(goals-and-delivery-receipts.md §4): `missing_evidence`, `needs_user_input`,
`run_failed`, `external_wait`, `goal_not_met_yet`.

The plan lives in the artifact store, so it has versions, the user can edit
it by hand before approving (Friday sees the diff on her next turn), and the
panel renders it. Approval opens a task-ledger run (services/task_ledger) so
the milestones are a task the ledger can resume; the receipt classifier of
the goals spec plugs in there when it lands.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from agent_friday.services import artifacts as _art
from agent_friday.user_errors import UserFacingValueError

CONTEXT_HEADER = "== PLAN (this chat's panel) =="
BLOCKERS = ("missing_evidence", "needs_user_input", "run_failed", "external_wait", "goal_not_met_yet")
STATUSES = ("todo", "doing", "done", "blocked")
MAX_MILESTONES = 12


class NotApproved(Exception):
    """A milestone cannot move before the user has approved the plan."""


def _plan_of(rec: Optional[dict]) -> Optional[dict]:
    if not rec:
        return None
    meta = rec.get("meta") or {}
    p = meta.get("plan")
    return p if isinstance(p, dict) and isinstance(p.get("milestones"), list) else None


def create(cid: str, title: str, markdown: str, milestones: list, settings: Optional[dict] = None) -> dict:
    """A plan awaiting approval. Raises ValueError on an empty or oversized milestone list."""
    titles = [" ".join(str(m).split())[:160] for m in (milestones or []) if str(m).strip()]
    if not titles:
        raise UserFacingValueError("a plan needs at least one milestone")
    if len(titles) > MAX_MILESTONES:
        raise UserFacingValueError("a plan has at most %d milestones; split it" % MAX_MILESTONES)
    plan = {"milestones": [{"n": i + 1, "title": t, "status": "todo", "step": None, "blocker": None, "note": ""}
                           for i, t in enumerate(titles)],
            "approved": False, "approved_at": None, "approved_by": None, "task_id": None,
            "created_at": datetime.now().isoformat(timespec="seconds")}
    return _art.put(cid, "markdown", str(title or "Plan"), str(markdown or ""), meta={"plan": plan},
                    author="friday", settings=settings, note="plan, awaiting approval")


def current(cid: str, settings: Optional[dict] = None) -> Optional[dict]:
    """The newest plan artifact in the conversation, with content, or None."""
    for it in reversed(_art.list_for(cid, settings=settings)):
        rec = _art.get(cid, it["id"], settings=settings)
        if _plan_of(rec):
            return rec
    return None


def _new_version(cid: str, rec: dict, plan: dict, note: str, settings=None) -> dict:
    meta = dict(rec.get("meta") or {})
    meta["plan"] = plan
    return _art.put(cid, rec["kind"], rec["title"], rec["content"], meta=meta, artifact_id=rec["id"],
                    author=rec.get("author") or "friday", settings=settings, note=note)


def approve(cid: str, aid: str, by: str = "you", settings: Optional[dict] = None) -> dict:
    """The user's yes. A new version with the same text; opens the ledger run."""
    rec = _art.get(cid, aid, settings=settings)
    plan = _plan_of(rec)
    if plan is None:
        raise UserFacingValueError("that artifact is not a plan")
    if plan.get("approved"):
        return rec
    plan = dict(plan)
    plan.update({"approved": True, "approved_at": datetime.now().isoformat(timespec="seconds"), "approved_by": by})
    task_id = "plan-" + aid
    try:
        from agent_friday.services import task_ledger as _tl
        led = _tl.ensure(task_id, "Plan: %s" % rec["title"])
        if led is not None:
            led["plan"] = [m["title"] for m in plan["milestones"]]
            _tl.save(task_id, led)
            plan["task_id"] = task_id
    except Exception:
        plan["task_id"] = task_id
    out = _new_version(cid, rec, plan, "plan approved by %s" % by, settings)
    _announce(cid, out, "plan_approved")
    return out


def milestone(cid: str, aid: str, n: int, status: str, *, step: Optional[str] = None,
              blocker: Optional[str] = None, note: str = "", settings: Optional[dict] = None) -> dict:
    """Move one milestone. `blocked` needs a typed blocker; `done` may name the step."""
    rec = _art.get(cid, aid, settings=settings)
    plan = _plan_of(rec)
    if plan is None:
        raise UserFacingValueError("that artifact is not a plan")
    if not plan.get("approved"):
        raise NotApproved("the plan has not been approved; nothing is built before the user says so")
    if status not in STATUSES:
        raise UserFacingValueError("status must be one of %s" % ", ".join(STATUSES))
    if status == "blocked" and blocker not in BLOCKERS:
        raise UserFacingValueError("a blocked milestone needs one of the typed blockers: %s" % ", ".join(BLOCKERS))
    ms = [dict(m) for m in plan["milestones"]]
    try:
        m = ms[int(n) - 1]
        if int(n) < 1:
            raise IndexError
    except (IndexError, TypeError, ValueError):
        raise UserFacingValueError("no milestone %r" % (n,))
    m["status"] = status
    m["blocker"] = blocker if status == "blocked" else None
    m["note"] = " ".join(str(note or "").split())[:300]
    if step:
        m["step"] = str(step)[:40]
    m["updated_at"] = datetime.now().isoformat(timespec="seconds")
    plan = dict(plan)
    plan["milestones"] = ms
    try:
        from agent_friday.services import task_ledger as _tl
        if plan.get("task_id"):
            led = _tl.load(plan["task_id"])
            if led is not None:
                led["done"] = [x["title"] for x in ms if x["status"] == "done"]
                led["next"] = next((x["title"] for x in ms if x["status"] in ("todo", "doing")), "")
                _tl.save(plan["task_id"], led)
    except Exception:
        pass
    out = _new_version(cid, rec, plan, "milestone %d %s%s" % (int(n), status, (" (%s)" % blocker) if blocker else ""), settings)
    _announce(cid, out, "plan_milestone")
    return out


def _announce(cid: str, rec: dict, what: str) -> None:
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.broadcast({"type": "artifact_put", "conversation_id": cid, "artifact_id": rec["id"],
                               "version": rec["version"], "kind": rec["kind"], "title": rec["title"],
                               "author": "you", "plan_event": what}, kind="chat")
    except Exception:
        pass


def next_milestone(plan: dict) -> Optional[dict]:
    for m in plan.get("milestones") or []:
        if m.get("status") in ("todo", "doing"):
            return m
    return None


def context_block(cid: str, settings: Optional[dict] = None) -> str:
    """What the model is told each turn: wait, or build the next milestone, or done."""
    try:
        rec = current(cid, settings=settings)
    except Exception:
        return ""
    plan = _plan_of(rec)
    if plan is None:
        return ""
    lines = ["", "", CONTEXT_HEADER, "Plan artifact %s · \"%s\" · v%d" % (rec["id"], rec["title"], rec["version"])]
    for m in plan["milestones"]:
        tail = ""
        if m.get("step"):
            tail += " · step %s" % m["step"]
        if m.get("blocker"):
            tail += " · blocker %s" % m["blocker"]
        if m.get("note"):
            tail += " · %s" % m["note"]
        lines.append("- %d. [%s] %s%s" % (m["n"], m["status"], m["title"], tail))
    if not plan.get("approved"):
        lines.append("The plan is AWAITING the user's approval (the panel's \"Build this plan\", or their own words in chat). "
                     "Do not build anything yet. If the user just said to go ahead, call plan_approve with their words; "
                     "if they asked for changes, update the plan with artifact_put(artifact_id=%s)." % rec["id"])
    else:
        nxt = next_milestone(plan)
        if nxt is None:
            lines.append("Plan approved and COMPLETE: every milestone is done or closed with a blocker. Say so in one line.")
        else:
            lines.append("Plan approved. NEXT: milestone %d, \"%s\". Build it in one or a few codebase_edit steps, then call "
                         "plan_milestone(n=%d, status='done', step=<sha>) or, if you must stop, status='blocked' with one typed "
                         "blocker (%s) and a note the user can act on." % (nxt["n"], nxt["title"], nxt["n"], ", ".join(BLOCKERS)))
    return "\n".join(lines) + "\n"
