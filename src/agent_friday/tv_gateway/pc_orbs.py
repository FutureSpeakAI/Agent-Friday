"""The PC's process orbs, for an owner's phone or tablet: ONE feed, the one the desktop draws its own orbs from.

The desktop's orbs are /api/processes (the PROCESSES registry every job registers in), drawn by window.fridaySyncOrbs:
every row with ``orb_visible`` and not ``dismissed``, a finished one included until the server's own five-minute clock
(``ORB_VISIBLE_AFTER_DONE_S``) or a dismiss takes it away, a failed one with a warning. This module applies exactly that
rule to the same rows, in process, and sends the fields the desktop's orb draws from, so the phone and the desktop cannot
disagree about what is running, how far along it is, which model is doing it, or how it ended. There is no second
aggregation of jobs here.

Rules, each held by a test in tests/unit/test_tv_pc_orbs.py:

* an orb is listed exactly when the desktop draws it (``orb_visible`` and not ``dismissed``): running, waiting, and a
  finished or failed one while the desktop still shows it; it leaves when the desktop's does;
* state, progress, category, icon and steps are the PC's own values, never a guess; progress is null when the PC does
  not know it (the desktop then draws a travelling arc, not an amount);
* nothing a tool was given or returned leaves the PC: a step is its tool's name and its status only;
* no string carries a URL or a host path;
* a PC that cannot answer sends 503, never an empty list that reads as "nothing is running";
* ``POST /tv/v1/orbs/<id>/stop`` does what the desktop orb's Cancel does (a task stops after its step, any other process
  is cancelled as a process), behind the ``pc_act`` right, and passes on what the PC says happened.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Iterator, Optional

from flask import Flask, Response, g, jsonify, stream_with_context

from agent_friday.tv_gateway import pc_read

_log = logging.getLogger(__name__)

MAX_ORBS = 100
TEXT_MAX = 120
MAX_STEPS = 12
POLL_S = 1.5                       # the desktop's own orb poll
HEARTBEAT_S = 5.0
MAX_STREAM_S = 600.0               # the phone reconnects; each connection starts with a snapshot

DONE, FAILED, STOPPED = "done", "failed", "stopped"
_OUTCOME = {"completed": DONE, "done": DONE, "complete": DONE, "finished": DONE,
            "failed": FAILED, "error": FAILED, "timeout": FAILED,
            "cancelled": STOPPED, "canceled": STOPPED, "interrupted": STOPPED, "stopped": STOPPED}
_STATE_OF_TASK = {"waiting_for_approval": "waiting_on_you", "waiting_for_input": "waiting_on_you",
                  "awaiting_approval": "waiting_on_you", "cancel_requested": "stopping"}
_CATEGORIES = ("search", "communication", "monitoring", "default", "media", "code", "research", "schedule", "system",
               "mail", "general")
_TRACE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_URL = re.compile(r"\b(?:https?|ftp|file)://\S+|\bwww\.\S+", re.I)
_HOST_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|(?<![\w.])/(?:[\w.-]+/)+[\w.-]*)\S*")


class Unavailable(Exception):
    """The PC could not say what is running."""


def plain(value, limit: int = TEXT_MAX) -> str:
    """Words the PC chose, with any URL or host path taken out and the length capped."""
    text = _HOST_PATH.sub("", _URL.sub("", str(value or "")))
    return " ".join(text.split())[:limit]


def drawn(row: dict) -> bool:
    """The desktop's own rule for drawing an orb (window.fridaySyncOrbs: ``orb_visible === false || dismissed`` skips it).
    Held by test_the_phone_gets_exactly_the_rows_the_desktop_draws."""
    return isinstance(row, dict) and bool(row.get("id")) and row.get("orb_visible") is not False and not row.get("dismissed")


def _stage(row: dict, task: dict) -> str:
    steps = row.get("steps") or []
    last = steps[-1] if steps and isinstance(steps[-1], dict) else {}
    tool = plain(last.get("name"), 60)
    return ("using " + tool) if tool else plain(task.get("now") or row.get("label") or row.get("name"))


def _state(row: dict, task: dict) -> str:
    """running, stalled, waiting_on_you, stopping, or how it ended (done, failed, stopped) while the desktop shows it."""
    status = str(row.get("status") or "").lower()
    ended = _OUTCOME.get(status)
    if ended == STOPPED:                       # the desktop marks a cancel orb_failed too (its warning icon stays)
        return STOPPED
    if row.get("orb_failed"):
        return FAILED
    if ended:
        return ended
    from agent_friday.routes.tasks import ORB_TERMINAL_STATES
    if status in ORB_TERMINAL_STATES:          # ended in a way the PC can't name (outcome_unknown): never drawn as running
        return STOPPED
    if task.get("stalled"):
        return "stalled"
    return _STATE_OF_TASK.get(task.get("state"), _STATE_OF_TASK.get(str(row.get("status") or ""), "running"))


def _kind(row: dict) -> str:
    if str(row.get("id") or "").startswith("crew"):
        return "crew"
    return "task" if row.get("task_id") else "voice_turn"


def _progress(row: dict) -> Optional[float]:
    p = row.get("progress")
    if isinstance(p, bool) or not isinstance(p, (int, float)) or p != p:
        return None
    return max(0.0, min(1.0, float(p)))


def _steps(row: dict) -> list:
    out = []
    for s in (row.get("steps") or [])[-MAX_STEPS:]:
        if isinstance(s, dict) and plain(s.get("name"), 60):
            out.append({"name": plain(s.get("name"), 60), "status": plain(s.get("status"), 20) or None})
    return out


def _int(v) -> Optional[int]:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _orb(row: dict, task: dict) -> dict:
    from agent_friday.services import visible_jobs
    model = plain(row.get("model"), 80)
    capability = row.get("stop_capability")
    state = _state(row, task)
    live = state not in (DONE, FAILED, STOPPED)
    category = str(row.get("category") or "default").lower()
    trace = row.get("trace_id")
    return {
        "id": plain(row.get("id"), 96), "job_id": row.get("task_id") or None, "parent_id": None,
        "kind": _kind(row), "title": plain(row.get("label") or row.get("name")), "stage": _stage(row, task),
        "state": state,
        "status": plain(row.get("status"), 30) or "running",
        "progress": _progress(row),
        "category": category if category in _CATEGORIES else "default",
        "icon": "⚠" if row.get("orb_failed") else (plain(row.get("icon"), 4) or None),
        "failed": bool(row.get("orb_failed")) or state == FAILED,
        "steps": _steps(row),
        "step_n": _int(row.get("step_n")), "step_total": _int(row.get("step_total")),
        "trace_id": trace if isinstance(trace, str) and _TRACE.match(trace) else None,
        "model": {"label": model, "local": bool(model) and visible_jobs.is_local_model(model)},
        "started_at": int(row.get("started") or 0),
        "last_event_at": int(row.get("updated") or row.get("ended") or row.get("started") or 0),
        "ended_at": _int(row.get("ended")) if not live else None,
        "approval_id": None,
        "can_stop": live and capability in ("cancel_now", "after_this_step", "stop_everything"),
        "can_stop_after_step": live and capability == "after_this_step",
        "origin": "pc",
    }


def _get(path: str) -> dict:
    status, body, _ = pc_read._desk(path)
    if status != 200:
        raise Unavailable(path)
    data = json.loads(body)
    if not isinstance(data, dict):
        raise Unavailable(path)
    return data


def read() -> tuple[dict, dict]:
    """(the orbs the desktop draws, by id; every row by id), from the feed the desktop draws its own orbs from."""
    try:
        rows = _get("/api/processes")["processes"]
    except (KeyError, TypeError, ValueError) as e:
        raise Unavailable("processes") from e
    try:
        tasks = {t["task_id"]: t for t in _get("/api/tasks")["tasks"] if isinstance(t, dict) and t.get("task_id")}
    except (Unavailable, KeyError, TypeError, ValueError):
        tasks = {}                       # stage and state fall back to the process's own words
    shown, every = {}, {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        every[row["id"]] = row
        if drawn(row):
            shown[row["id"]] = _orb(row, tasks.get(row.get("task_id")) or {})
    return dict(list(shown.items())[:MAX_ORBS]), every


def changes(before: dict, live: dict, rows: dict) -> list[tuple[str, dict]]:
    """The events that turn what the phone has (`before`) into what the desktop draws now."""
    out = [("orb.added" if orb_id not in before else "orb.changed", orb)
           for orb_id, orb in live.items() if before.get(orb_id) != orb]
    for orb_id in before:
        if orb_id not in live:
            row = rows.get(orb_id) or {}
            last = before[orb_id].get("state")
            outcome = last if last in (DONE, FAILED, STOPPED) else _OUTCOME.get(str(row.get("status") or "").lower(),
                                                                                  FAILED if row.get("orb_failed") else STOPPED)
            out.append(("orb.removed", {"id": orb_id, "job_id": before[orb_id]["job_id"], "outcome": outcome,
                                        "receipt_id": plain(row.get("receipt_id"), 120) or None}))
    return out


def _frame(event: str, data: dict, rev: Optional[int] = None) -> str:
    head = ("id: %d\n" % rev) if rev is not None else ""
    return "%sevent: %s\ndata: %s\n\n" % (head, event, json.dumps(data, separators=(",", ":")))


def _still_allowed(dev: dict) -> bool:
    from agent_friday.services import device_pairing
    return pc_read.allowed(dev) and device_pairing.has_right(dev, pc_read.RIGHT)


def _events(dev: dict, now=time.time, sleep=time.sleep) -> Iterator[str]:
    """One connection: a snapshot, then only what changes, and a heartbeat that re-checks the right."""
    rev, known, started, last_beat = 0, {}, now(), None
    while now() - started < MAX_STREAM_S:
        if last_beat is None or now() - last_beat >= HEARTBEAT_S:
            if not _still_allowed(dev):
                return
            yield _frame("heartbeat", {"as_of": int(now())})
            last_beat = now()
        try:
            live, rows = read()
        except Unavailable:
            return                       # the phone sees the stream end and draws no orbs
        sent = [("orb.snapshot", {"orbs": list(live.values())})] if rev == 0 else changes(known, live, rows)
        for event, data in sent:
            rev += 1
            yield _frame(event, data, rev)
        known = live
        sleep(POLL_S)


def _desk_post(path: str):
    from agent_friday.tv_gateway import pc_act
    return pc_act._desk_post(path)


def stop(orb_id: str) -> tuple[int, dict]:
    """What the desktop orb's Cancel does (FridayOrbHands.commitCancel): a task stops after its current step; any other
    process is cancelled as a process. The PC's own answer comes back, so a stop that did nothing says so."""
    try:
        _, rows = read()
    except Unavailable:
        return 503, {"ok": False, "error": "the PC could not say what is running right now", "code": "pc_unavailable"}
    row = rows.get(orb_id)
    if not row or not drawn(row):
        return 404, {"ok": False, "error": "that is no longer running on the PC", "code": "unknown_orb"}
    if _state(row, {}) in (DONE, FAILED, STOPPED):
        return 409, {"ok": False, "error": "that has already finished", "code": "not_running"}
    tid = row.get("task_id")
    how = "after_this_step" if tid and pc_read.ID_PATTERN.match(str(tid)) else "cancelled"
    try:
        if how == "after_this_step":
            status, body = _desk_post("/api/tasks/%s/stop-after-step" % tid)
        else:
            status, body = _desk_post("/api/processes/%s/cancel" % orb_id)
    except Exception as e:
        _log.warning("pc_orbs stop failed: %s", type(e).__name__)
        return 503, {"ok": False, "error": "the PC could not stop it right now", "code": "pc_unavailable"}
    if status == 409:
        return 409, {"ok": False, "error": "it was not running, so nothing was stopped", "code": "not_running",
                     "status": plain((body or {}).get("status"), 30) or None}
    if status >= 400:
        return 503 if status >= 500 else status, {"ok": False, "error": "the PC did not stop it", "code": "not_stopped"}
    return 200, {"ok": True, "how": how, "stopped": [plain(s, 40) for s in (body or {}).get("stopped") or []][:6]}


def register(app: Flask) -> None:
    @app.get("/tv/v1/orbs")
    def pc_orbs():
        denied = pc_read._guard("orbs")
        if denied is not None:
            return denied
        try:
            live, _ = read()
        except Exception as e:
            _log.warning("pc_orbs read failed: %s", type(e).__name__)
            return pc_read._refuse(503, "the PC could not say what is running right now", code="pc_unavailable")
        return jsonify({"ok": True, "as_of": int(time.time()), "orbs": list(live.values()), "count": len(live)})

    @app.get("/tv/v1/orbs/events")
    def pc_orbs_events():
        denied = pc_read._guard("orbs")
        if denied is not None:
            return denied
        return Response(stream_with_context(_events(g.device)), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/tv/v1/orbs/<orb_id>/stop")
    def pc_orb_stop(orb_id):
        """Stop one orb, as the desktop orb's Cancel does. Behind ``pc_act`` (the right to control the PC)."""
        from agent_friday.tv_gateway import pc_act
        denied = pc_act._guard()
        if denied is not None:
            return denied
        if not pc_read.ID_PATTERN.match(orb_id):
            return pc_read._refuse(400, "not an orb id", code="bad_id")
        status, answer = stop(orb_id)
        from agent_friday.services import device_pairing
        # every attempt, with what came of it (as pc_act does): a refused or failed stop is in the receipts too
        device_pairing._receipt("device_orb_stop", getattr(g, "device", None) or {}, orb=orb_id,
                                how=answer.get("how"), outcome="stopped" if status == 200 else answer.get("code"))
        resp = jsonify(answer)
        resp.status_code = status
        return resp
