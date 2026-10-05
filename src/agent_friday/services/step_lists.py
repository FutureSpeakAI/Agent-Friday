"""Narrated step lists the owner can stop (See & Touch, phase 5).

A workflow runs as a chain of tasks. This keeps one short list per run (the title, the numbered steps and a state
for each: waiting, doing, done, held, skipped, stopped, failed), shows it on the owner's screen as a panel with a
Stop button, tells a live voice call each step as it begins (in counts, never a step's name), and stops the run
when the owner says so: the Stop button, Esc, or the spoken "stop".

There is no second engine. The state of each step is read from the chain's own task records
(`agent.chain_run_status`); this module only remembers which list is current, pushes what changed to the page, and
asks the one stop that exists (`agent.stop_workflow_chain`: the step that is running finishes, the next never
starts). Memory only: nothing here is written to disk.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

log = logging.getLogger(__name__)

STATES = ("waiting", "doing", "done", "held", "skipped", "stopped", "failed")
_TERMINAL = ("done", "skipped", "stopped", "failed")
#: A finished list stays the "active" one for this long, so "stop" right after it ended says so.
LINGER_S = 60.0

_LOCK = threading.Lock()
_LISTS: dict[str, dict] = {}
_CURRENT: dict[str, str] = {}            # chain slug -> the list of its current run


def _state(status: str) -> str:
    s = str(status or "pending").lower()
    if s in ("queued", "running"):
        return "doing"
    if s in ("completed", "complete", "done"):
        return "done"
    if s in ("failed", "timeout"):
        return "failed"
    if s in ("cancelled", "stopped", "interrupted"):
        return "stopped"
    if s == "skipped":
        return "skipped"
    if "approval" in s or "held" in s or "waiting" in s:
        return "held"
    return "waiting"


def _push(actions: list) -> None:
    try:
        from agent_friday.services import desktop_bus
        desktop_bus.push(actions)
    except Exception:
        pass


def _narrate(conversation_id: str, n: int, total: int) -> None:
    """One short clause to a live voice call as a step begins: a count, never the step's name."""
    if not conversation_id:
        return
    try:
        from agent_friday.services import voice_live_channel as vlc
        if vlc.is_live(conversation_id):
            vlc.deliver(conversation_id, "Step %d of %d has started." % (n, total), kind="progress")
    except Exception:
        pass


def _finished(rec: dict) -> bool:
    return all(s["state"] in _TERMINAL for s in rec["steps"])


def begin_workflow(slug: str, title: str = "", conversation_id: str = "") -> dict | None:
    """A new run of `slug` has started: make its list and show it."""
    with _LOCK:
        _CURRENT.pop(slug, None)
    return sync_workflow(slug, title=title, conversation_id=conversation_id, fresh=True)


def sync_workflow(slug: str, *, title: str = "", conversation_id: str = "", fresh: bool = False) -> dict | None:
    """Bring the list of `slug`'s current run up to date from the chain's task records, push what changed, and
    narrate each step that has just begun. Never raises."""
    try:
        from agent_friday.services import agent
        st = agent.chain_run_status(slug)
    except Exception:
        return None
    if not st:
        return None
    steps = [{"n": s["index"] + 1, "text": str(s.get("name") or "Step %d" % (s["index"] + 1))[:80],
              "state": _state(s.get("status"))} for s in st.get("steps") or []]
    if not steps:
        return None
    now = time.time()
    with _LOCK:
        lid = _CURRENT.get(slug)
        rec = _LISTS.get(lid) if lid else None
        created = rec is None
        if created:
            lid = "sl_" + uuid.uuid4().hex[:8]
            rec = {"id": lid, "kind": "workflow", "target": slug, "title": str(title or st.get("name") or slug)[:80],
                   "conversation_id": conversation_id or "", "steps": [], "at": now, "finished_at": 0.0}
            _LISTS[lid] = rec
            _CURRENT[slug] = lid
            for old in list(_LISTS)[:-30]:
                _LISTS.pop(old, None)
        before = {s["n"]: s["state"] for s in rec["steps"]}
        rec["steps"] = steps
        if conversation_id and not rec["conversation_id"]:
            rec["conversation_id"] = conversation_id
        if _finished(rec) and not rec["finished_at"]:
            rec["finished_at"] = now
        changed = [s for s in steps if before.get(s["n"]) != s["state"]]
        snapshot = {"id": lid, "title": rec["title"], "steps": [dict(s) for s in steps]}
        cid = rec["conversation_id"]
    if created:
        _push([{"type": "steps", "id": lid, "title": snapshot["title"], "steps": snapshot["steps"], "kind": "workflow"}])
    else:
        for s in changed:
            _push([{"type": "step_update", "id": lid, "n": s["n"], "state": s["state"]}])
    for s in changed:
        if s["state"] == "doing" and before.get(s["n"]) != "doing":
            _narrate(cid, s["n"], len(steps))
    return snapshot


def active(now: float | None = None) -> dict | None:
    """The newest list that is still running (or only just ended)."""
    now = now or time.time()
    with _LOCK:
        recs = [r for r in _LISTS.values() if not r["finished_at"] or now - r["finished_at"] < LINGER_S]
        rec = max(recs, key=lambda r: r["at"], default=None)
        return dict(rec, steps=[dict(s) for s in rec["steps"]]) if rec else None


def get(list_id: str) -> dict | None:
    with _LOCK:
        rec = _LISTS.get(str(list_id))
        return dict(rec, steps=[dict(s) for s in rec["steps"]]) if rec else None


def stop(list_id: str = "", *, by: str = "you") -> dict:
    """Stop the run a list shows: the step that is running finishes, the next never starts. With no id, the
    newest list that is still running. {"ok", "text", "id"}; nothing to stop is an honest no."""
    rec = get(list_id) if list_id else active()
    if rec is None:
        return {"ok": False, "text": "Nothing is running that I can stop."}
    if all(s["state"] in _TERMINAL for s in rec["steps"]):
        return {"ok": False, "id": rec["id"], "text": "That already finished."}
    from agent_friday.services import agent
    res = agent.stop_workflow_chain(rec["target"], by=by)
    sync_workflow(rec["target"])
    if not res.get("ok"):
        return {"ok": False, "id": rec["id"], "text": res.get("reason") or "It is not running."}
    left = sum(1 for s in rec["steps"] if s["state"] in ("waiting", "doing", "held"))
    return {"ok": True, "id": rec["id"],
            "text": "Stopped by you. The step that is running finishes; the %s after it will not start." % (
                "step" if left <= 2 else "%d steps" % max(0, left - 1))}


def reset() -> None:
    with _LOCK:
        _LISTS.clear()
        _CURRENT.clear()
