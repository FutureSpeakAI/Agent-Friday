"""The PC's live process orbs, for an owner's phone or tablet (spec PHONE_WORKSPACES_PARITY section 12.2, PW-G11).

The desktop draws one orb per running task or subagent from /api/processes. This reads the same feed in process,
so the phone and the desktop cannot disagree about what is running, which model answered, or when it ended.

Rules, each held by a test in tests/unit/test_tv_pc_orbs.py:

* an orb is listed only while its process runs; a finished process leaves, and no "done" orb exists to draw;
* the label is the PC's own model value, never a guess;
* nothing a tool was given or returned leaves the PC: only a tool's name, as the stage;
* no string carries a URL or a host path;
* a PC that cannot answer sends 503, never an empty list that reads as "nothing is running".
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
POLL_S = 1.5                       # the desktop's own orb poll
HEARTBEAT_S = 5.0
MAX_STREAM_S = 600.0               # the phone reconnects; each connection starts with a snapshot

DONE, FAILED, STOPPED = "done", "failed", "stopped"
_OUTCOME = {"completed": DONE, "done": DONE, "complete": DONE,
            "cancelled": STOPPED, "interrupted": STOPPED}
_STATE_OF_TASK = {"waiting_for_approval": "waiting_on_you", "waiting_for_input": "waiting_on_you",
                  "cancel_requested": "stopping"}
_URL = re.compile(r"\b(?:https?|ftp|file)://\S+|\bwww\.\S+", re.I)
_HOST_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|(?<![\w.])/(?:[\w.-]+/)+[\w.-]*)\S*")


class Unavailable(Exception):
    """The PC could not say what is running."""


def plain(value, limit: int = TEXT_MAX) -> str:
    """Words the PC chose, with any URL or host path taken out and the length capped."""
    text = _HOST_PATH.sub("", _URL.sub("", str(value or "")))
    return " ".join(text.split())[:limit]


def _stage(row: dict, task: dict) -> str:
    steps = row.get("steps") or []
    last = steps[-1] if steps and isinstance(steps[-1], dict) else {}
    tool = plain(last.get("name"), 60)
    return ("using " + tool) if tool else plain(task.get("now") or row.get("label") or row.get("name"))


def _state(task: dict) -> str:
    if task.get("stalled"):
        return "stalled"
    return _STATE_OF_TASK.get(task.get("state"), "running")


def _kind(row: dict) -> str:
    if str(row.get("id") or "").startswith("crew"):
        return "crew"
    return "task" if row.get("task_id") else "voice_turn"


def _orb(row: dict, task: dict) -> dict:
    from agent_friday.services import visible_jobs
    model = plain(row.get("model"), 80)
    capability = row.get("stop_capability")
    return {
        "id": plain(row.get("id"), 96), "job_id": row.get("task_id") or None, "parent_id": None,
        "kind": _kind(row), "title": plain(row.get("label") or row.get("name")), "stage": _stage(row, task),
        "state": _state(task),
        "model": {"label": model, "local": bool(model) and visible_jobs.is_local_model(model)},
        "started_at": int(row.get("started") or 0),
        "last_event_at": int(row.get("updated") or row.get("started") or 0),
        "approval_id": None,
        "can_stop": capability in ("cancel_now", "after_this_step", "stop_everything"),
        "can_stop_after_step": capability == "after_this_step",
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
    """(live orbs by id, ended processes by id), from the feed the desktop draws its own orbs from."""
    from agent_friday.routes.tasks import ORB_TERMINAL_STATES
    try:
        rows = _get("/api/processes")["processes"]
    except (KeyError, TypeError, ValueError) as e:
        raise Unavailable("processes") from e
    try:
        tasks = {t["task_id"]: t for t in _get("/api/tasks")["tasks"] if isinstance(t, dict) and t.get("task_id")}
    except (Unavailable, KeyError, TypeError, ValueError):
        tasks = {}                       # stage and state fall back to the process's own words
    live, ended = {}, {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if row.get("status") in ORB_TERMINAL_STATES or row.get("status") in _OUTCOME:
            ended[row["id"]] = row
        elif row.get("orb_visible") and not row.get("dismissed"):
            live[row["id"]] = _orb(row, tasks.get(row.get("task_id")) or {})
    return dict(list(live.items())[:MAX_ORBS]), ended


def changes(before: dict, live: dict, ended: dict) -> list[tuple[str, dict]]:
    """The events that turn what the phone has (`before`) into what the PC shows now."""
    out = [("orb.added" if orb_id not in before else "orb.changed", orb)
           for orb_id, orb in live.items() if before.get(orb_id) != orb]
    for orb_id in before:
        if orb_id not in live:
            row = ended.get(orb_id) or {}
            out.append(("orb.removed", {"id": orb_id, "job_id": before[orb_id]["job_id"],
                                        "outcome": _OUTCOME.get(row.get("status"), FAILED if row else STOPPED),
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
            live, ended = read()
        except Unavailable:
            return                       # the phone sees the stream end and draws no orbs
        sent = [("orb.snapshot", {"orbs": list(live.values())})] if rev == 0 else changes(known, live, ended)
        for event, data in sent:
            rev += 1
            yield _frame(event, data, rev)
        known = live
        sleep(POLL_S)


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
