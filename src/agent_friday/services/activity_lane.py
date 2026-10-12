"""The four places, read for the panel and for voice: Needs you, Activity, Updates, Receipts.

The notification addendum (NA-3) splits what Friday tells you into four places with one rule each:

* **Needs you**: only what blocks on your decision (approvals, a failure you must fix, a money or
  credential problem). Approvals of one kind and source show as one batch card
  (services/approval_batches). The badge counts these.
* **Activity**: every task there is, whoever started it (chat, voice, a workspace button, a
  schedule, an idle job, Laya): its model, status, a link to its reasoning trace, its place in the
  queue and Stop where the job honours it. The top-bar queue chip is the way in. It reads the task
  records (``agent.TASKS``, the ones ``services/visible_jobs`` registers jobs into) and the Local AI
  queue, and builds no second list.
* **Updates**: results and FYIs, as a digest. Pulled; a quiet dot, never a popup.
* **Receipts & traces**: pulled only, from the item that made them ("why?") or from Trust.

This module only READS. It decides nothing, approves nothing and starts nothing. Replies for the ear
are written to be said in one or two sentences.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

_log = logging.getLogger("friday.activity_lane")

#: A finished task stays in Activity this long, then it is a receipt only.
FINISHED_VISIBLE_S = 30 * 60
LIVE = ("queued", "running", "paused", "interrupted")

_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _n(k: int) -> str:
    return _WORDS[k] if 0 <= k < len(_WORDS) else str(k)


def _ne():
    from agent_friday import notifications_engine as ne
    return ne


# ── Needs you ────────────────────────────────────────────────────────────────

def needs_you(limit: int = 200) -> Dict[str, Any]:
    """Blocking decisions: the approval batches and singles, and the other needs-you cards."""
    from agent_friday.services import approval_batches as ab
    from agent_friday.services import interruption_policy as ip
    try:
        from agent_friday.services import approvals as _ap
        _ap.release_held()            # the page polls this: a hold that has ended is released here
    except Exception:
        pass
    plan = ab.pending_plan()
    singles = [{"approval_id": r.get("approval_id"), "kind": r.get("kind"),
                "title": r.get("title") or "", "why": (r.get("description") or "")[:200],
                "action": (r.get("action_description") or "")[:200],
                "created_at": r.get("created_at"), "expires_at": r.get("expires_at"),
                "screen_only": r.get("kind") in _screen_only()} for r in plan["singles"]]
    others = []
    for c in _ne().list_notifications(limit=limit):
        if c.get("kind") == "approval_pending" or c.get("dismissed"):
            continue
        if (c.get("place") or ("needs_you" if c.get("tier") == "needs_you" else "")) != "needs_you":
            continue
        others.append({"id": c.get("id"), "kind": c.get("kind"), "title": c.get("title") or "",
                       "body": (c.get("body") or "")[:300], "count": int(c.get("count") or 1),
                       "created_at": c.get("created_at"), "actions": c.get("actions") or [],
                       "target": c.get("target") or {}, "safety_critical": bool(c.get("safety_critical")),
                       "read": bool(c.get("read"))})
    n_appr = len(plan["singles"]) + sum(b["count"] for b in plan["batches"])
    return {"count": n_appr + len(others), "approvals": n_appr,
            "batches": plan["batches"], "singles": singles, "others": others,
            "summary": ip.waiting_phrase(n_appr, len(others)), "quiet": ip.quiet_state()}


def _screen_only() -> frozenset:
    try:
        from agent_friday.services import approvals
        return frozenset(approvals.SCREEN_ONLY_KINDS)
    except Exception:
        return frozenset()


# ── Activity ─────────────────────────────────────────────────────────────────

def _task_rows(now: float) -> List[dict]:
    from agent_friday.services import agent
    try:
        from agent_friday.services import reasoning_trace as rt
    except Exception:
        rt = None
    recs = agent._task_snapshot() or []
    queued = sorted([r for r in recs if r.get("status") == "queued"],
                    key=lambda r: float(r.get("created") or 0))
    pos = {r.get("task_id"): i + 1 for i, r in enumerate(queued)}
    rows = []
    for r in recs:
        tid = r.get("task_id") or r.get("id")
        status = r.get("status") or ""
        if not tid or r.get("dismissed"):
            continue
        ended = r.get("ended")
        if status not in LIVE and not (ended and now - float(ended) <= FINISHED_VISIBLE_S):
            continue
        trace = r.get("trace_id") or ""
        if not trace and rt is not None:
            try:
                trace = rt.for_task(tid) or ""
            except Exception:
                trace = ""
        live = status in ("queued", "running")
        rows.append({
            "id": tid, "source": "task", "kind": r.get("job_kind") or "task",
            "label": r.get("job_label") or r.get("name") or "A task",
            "model": r.get("model") or "", "status": status, "trace_id": trace,
            "queue_position": int(r.get("queue_position") or 0) or (pos.get(tid, 0)),
            # Stop only where the job says it honours it (services/visible_jobs sets `stoppable`); a
            # record that does not say is not stoppable. Stop asks for a stop after the current step and
            # never deletes the record, even if the task has finished since the list was drawn.
            "stoppable": bool(live and r.get("stoppable") is True),
            "stop": ({"method": "POST", "url": "/api/tasks/%s/stop-after-step" % tid}
                     if live and r.get("stoppable") is True else None),
            "private": bool(r.get("off_record") or r.get("local_only")),
            "workspace": r.get("workspace") or "", "started": r.get("started"),
            "ended": ended, "off_record": bool(r.get("off_record")),
            "seat_is_local": bool(r.get("seat_is_local")),
        })
    return rows


def _queue_rows(known: set, behind: int = 0) -> List[dict]:
    """The Local AI queue's own jobs that are not tasks. ``behind`` is how many queued tasks are
    already ahead of them, so the positions in Activity run on from one list to the next."""
    from agent_friday.services import agent
    snap = agent.local_queue_snapshot() or {}
    rows = []
    place = behind
    for j in snap.get("jobs") or []:
        jid = j.get("id")
        if not jid or jid in known:
            continue
        queued = j.get("state") == "queued"
        if queued:
            place += 1
        rows.append({
            "id": jid, "source": "local_queue", "kind": j.get("kind") or "local",
            "label": j.get("label") or "Background work", "model": j.get("model") or "",
            "status": "queued" if queued else (j.get("state") or "running"),
            "trace_id": "", "queue_position": place if queued else 0,
            # Cancel only where the scheduler says the job can be taken out (the chip's own rule).
            "stoppable": bool(j.get("cancellable", True)),
            "stop": ({"method": "POST", "url": "/api/local-queue/%s/cancel" % jid}
                     if j.get("cancellable", True) else None),
            "run_now": bool(queued and j.get("deferrable") and not j.get("run_now")),
            "why": j.get("why") or "", "workspace": "", "started": None, "ended": None})
    return rows


def _process_rows(known: set) -> List[dict]:
    rows = []
    try:
        from agent_friday import core
        with core.PROCESSES_LOCK:
            procs = list(core.PROCESSES.items())
    except Exception:
        return rows
    for pid, p in procs:
        if pid in known or (p.get("task_id") and p.get("task_id") in known):
            continue
        if (p.get("category") or "default") == "default" or p.get("status", "running") != "running":
            continue
        rows.append({"id": pid, "source": "process", "kind": p.get("category") or "process",
                     "label": p.get("label") or p.get("name") or "Working", "model": p.get("model") or "",
                     "status": "running", "trace_id": p.get("trace_id") or "", "queue_position": 0,
                     "stoppable": False, "stop": None, "workspace": "", "started": p.get("started"),
                     "ended": None})
    return rows


def _mark_pausable(rows: List[dict]) -> None:
    """Pause only where it works: a job in the Local AI queue's own line (services/seat_supervisor) waits at
    its next model call, or is not started, until it is resumed (POST /api/local-queue/<id>/pause|resume).
    Cloud work, a process orb and a row the scheduler only lists have no such checkpoint, so no Pause."""
    try:
        from agent_friday.services import local_scheduler
        snap = local_scheduler._supervisor_snapshot()
        mine = {str(j.get("id")): bool(j.get("paused_by_you")) for j in snap.get("jobs") or []}
    except Exception:
        mine = {}
    for r in rows:
        live = r.get("status") in ("running", "queued", "paused")
        r["pausable"] = bool(live and str(r.get("id")) in mine)
        r["paused_by_you"] = bool(r["pausable"] and mine.get(str(r.get("id"))))


def activity(now: Optional[float] = None) -> Dict[str, Any]:
    """Every task: running first, then queued in order, then recently finished."""
    now = time.time() if now is None else now
    rows = _task_rows(now)
    known = {r["id"] for r in rows}
    rows += _queue_rows(known, sum(1 for r in rows if r["status"] == "queued"))
    known |= {r["id"] for r in rows}
    rows += _process_rows(known)
    _mark_pausable(rows)
    rank = {"running": 0, "paused": 1, "queued": 2, "interrupted": 3}
    rows.sort(key=lambda r: (rank.get(r["status"], 9),
                             r["queue_position"] or 0, -(float(r.get("ended") or r.get("started") or 0))))
    running = [r for r in rows if r["status"] == "running"]
    queued = [r for r in rows if r["status"] == "queued"]
    return {"tasks": rows, "running": len(running), "queued": len(queued),
            "count": len(running) + len(queued)}


# ── Updates (the digest) ─────────────────────────────────────────────────────

def _is_today(created_at: str, day: datetime) -> bool:
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(str(created_at).replace("Z", "")).replace(
            tzinfo=timezone.utc).astimezone()
        return dt.date() == day.date()
    except Exception:
        return False


def updates(today_only: bool = False, now: Optional[float] = None) -> Dict[str, Any]:
    """Results and FYIs, grouped by kind with a count. Pulled; nothing here pops."""
    day = datetime.fromtimestamp(time.time() if now is None else now)
    groups: Dict[str, dict] = {}
    for c in _ne().list_notifications(limit=200):
        if c.get("dismissed") or (c.get("place") or "") not in ("updates", "activity", ""):
            continue
        if c.get("tier") == "needs_you" or c.get("kind") == "approval_pending":
            continue
        if today_only and not _is_today(c.get("created_at") or "", day):
            continue
        g = groups.setdefault(c.get("kind") or "info", {
            "kind": c.get("kind") or "info", "count": 0, "items": []})
        g["count"] += int(c.get("count") or 1)
        g["items"].append({"id": c.get("id"), "title": c.get("title") or "",
                           "body": (c.get("body") or "")[:200], "count": int(c.get("count") or 1),
                           "created_at": c.get("created_at"), "target": c.get("target") or {}})
    out = sorted(groups.values(), key=lambda g: -g["count"])
    return {"groups": out, "count": sum(g["count"] for g in out), "today_only": bool(today_only)}


# ── Receipts & traces (pulled) ───────────────────────────────────────────────

def receipts(limit: int = 30, task_id: str = "") -> Dict[str, Any]:
    """Recent reasoning traces, newest first: id, kind, label, model, status. Content stays behind
    /api/traces/<id>; this is the list a "why?" and the Trust page open from."""
    try:
        from agent_friday.services import reasoning_trace as rt
        res = rt.search(task_id=task_id or None, limit=max(1, min(int(limit), 100))) or {}
    except Exception:
        _log.debug("receipts unavailable", exc_info=True)
        return {"traces": [], "count": 0}
    items = res.get("traces") or res.get("items") or res.get("results") or []
    rows = [{"trace_id": t.get("trace_id") or t.get("id"), "kind": t.get("kind") or "",
             "label": t.get("label") or "", "model": t.get("model") or "",
             "status": t.get("status") or "", "started": t.get("started") or t.get("started_at"),
             "task_id": t.get("task_id") or ""} for t in items if isinstance(t, dict)]
    return {"traces": rows, "count": len(rows)}


def why(notice_id: str) -> Dict[str, Any]:
    """Why did this notice reach you the way it did? Plain words, and the trace if it has one."""
    from agent_friday.services import notice_registry as reg
    card = next((c for c in _ne().list_notifications(include_dismissed=True, limit=1000)
                 if c.get("id") == notice_id), None)
    if card is None:
        return {"ok": False, "error": "not found"}
    em = reg.get(card.get("emitter")) or reg.resolve(card.get("kind") or "", card.get("source") or "")
    place = card.get("place") or reg.place_of(card.get("kind") or "", card.get("source") or "")
    reason = card.get("hold_reason") or ""
    if card.get("safety_critical"):
        how = "It is safety-critical, so it always reaches you at once."
    elif card.get("interrupt") is False and place == "needs_you":
        how = {"quiet_hours": "It waited in your badge because quiet hours were on.",
               "budget": "It waited in your badge because you had reached your hourly limit.",
               "focus": "It waited in your badge because Windows Focus Assist was on."
               }.get(reason, "It waited in your badge without interrupting you.")
    elif place == "needs_you":
        how = "It needs your decision, so it is in Needs you."
    else:
        how = "It is a result for your information, so it sits in Updates and never pops up."
    meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
    trace = ""
    tid = meta.get("task_id") or ""
    if tid:
        try:
            from agent_friday.services import reasoning_trace as rt
            trace = rt.for_task(tid) or ""
        except Exception:
            trace = ""
    return {"ok": True, "id": notice_id, "kind": card.get("kind"), "place": place,
            "emitter": em.id if em else None, "from": em.where if em else "",
            "sentence": "%s %s" % (_from_sentence(card, em), how), "trace_id": trace,
            "held": bool(card.get("held")), "hold_reason": reason}


def _from_sentence(card: dict, em: Any) -> str:
    src = (card.get("source") or "").replace("-", " ").replace("_", " ").strip()
    return "It came from %s." % (src if src else "Friday")


# ── For the ear ──────────────────────────────────────────────────────────────

def spoken_needs_you(limit: int = 4) -> str:
    n = needs_you()
    if not n["count"]:
        return "Nothing needs you right now."
    parts = []
    for b in n["batches"][:limit]:
        parts.append("%s actions from one source want a decision together" % _n(b["count"]))
    for s in n["singles"][:max(0, limit - len(parts))]:
        parts.append(str(s["title"]).strip()[:100])
    for o in n["others"][:max(0, limit - len(parts))]:
        parts.append(str(o["title"]).strip()[:100] + (" (%d times)" % o["count"] if o["count"] > 1 else ""))
    head = "%s thing%s need%s you" % (_n(n["count"]).capitalize(), "" if n["count"] == 1 else "s",
                                      "s" if n["count"] == 1 else "")
    return "%s: %s." % (head, "; ".join(p for p in parts if p))


def spoken_running(limit: int = 4, names: bool = True) -> str:
    """What runs and what waits. ``names`` is True only for a model on this computer; a cloud model hears
    counts. A private task (off the record, or local-only) is never named to anyone: "a private task"."""
    a = activity()
    if not a["count"]:
        return "Nothing is running and nothing is waiting."
    run = [t for t in a["tasks"] if t["status"] == "running"]
    wait = [t for t in a["tasks"] if t["status"] == "queued"]
    if not names:
        bits = []
        if run:
            bits.append("%s running" % _n(len(run)).capitalize())
        if wait:
            bits.append("%s waiting" % (_n(len(wait)) if run else _n(len(wait)).capitalize()))
        return " and ".join(bits) + "."

    def said(t):
        return "a private task" if t.get("private") else t["label"]
    parts = []
    if run:
        parts.append("%s running: %s" % (_n(len(run)).capitalize(), "; ".join(said(t) for t in run[:limit])))
    if wait:
        parts.append("%s waiting, in order: %s" % (_n(len(wait)).capitalize(),
                                                   "; ".join(said(t) for t in wait[:limit])))
    return ". ".join(parts) + "."


def spoken_updates(limit: int = 6) -> str:
    u = updates(today_only=True)
    if not u["count"]:
        return "No new updates today."
    said = []
    for g in u["groups"]:
        for it in g["items"]:
            said.append(str(it["title"]).strip()[:100] + (" (%d times)" % it["count"] if it["count"] > 1 else ""))
    more = len(said) - limit
    tail = " And %s more." % _n(more) if more > 0 else ""
    return "%s update%s today: %s.%s" % (_n(u["count"]).capitalize(), "" if u["count"] == 1 else "s",
                                          "; ".join(said[:limit]), tail)
