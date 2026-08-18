import os
import io
import json
import glob
import subprocess
import base64
import secrets
import sys
import traceback
import uuid
import threading
import asyncio
import re
import html
import calendar
import time as _time
import hashlib as _hashlib
import hmac as _hmac
import queue as _queue
import difflib as _difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    PROCESSES,
    PROCESSES_LOCK,
    login_required,
)  # noqa: E501
from agent_friday.services.agent import (
    TASKS,
    TASKS_LOCK,
    _FOLLOW_UP_LOCK,
    _FOLLOW_UP_QUEUES,
    _task_snapshot,
)  # noqa: E501

tasks_bp = Blueprint('tasks', __name__)



# ── Task Tray HTTP endpoints (consumed by the frontend TaskTray) ──
@tasks_bp.route('/api/tasks')
def list_tasks():
    """Tasks for the frontend TaskTray / notifications panel.

    Surfaces two sources so the viewer reflects real activity instead of sitting
    empty: (1) the TASKS registry (agent tasks spawned via /api/tasks or
    spawn_task), and (2) live PROCESSES — the holographic orbs that briefings,
    vault access, context compression, model pulls, etc. register. Process
    entries are flagged `process: True` so the frontend can skip them for the
    "task complete" chat notification and for orb-syncing (the /api/processes
    poll already owns those orbs)."""
    tasks = _task_snapshot() or []
    seen = {t.get('task_id') for t in tasks if t.get('task_id')}
    _status_map = {'completed': 'complete', 'error': 'failed', 'running': 'running'}
    now = _time.time()
    with PROCESSES_LOCK:
        for pid, p in list(PROCESSES.items()):
            if pid in seen:
                continue
            # Skip ephemeral inline chat/voice orbs (category 'default') — those
            # turns are already reflected by the chat "thinking" state, and
            # surfacing every one would clutter the tray.
            if (p.get('category') or 'default') == 'default':
                continue
            started = p.get('started', now)
            ended = p.get('ended')
            tasks.append({
                'task_id': pid,
                'name': p.get('label') or p.get('name') or 'Process',
                'status': _status_map.get(p.get('status', 'running'), 'running'),
                'progress': p.get('progress', 0),
                'icon': p.get('icon'),
                'model': p.get('model'),
                'category': p.get('category'),
                'created': started,
                'started': started,
                'elapsed': int((ended or now) - started),
                'process': True,
                # THE "— waiting for activity —" BUG.
                #
                # Raised five times, diagnosed twice, and both diagnoses were
                # about the wrong thing. The placeholder is not a rendering
                # question and the emitter was never broken: `process_log`
                # exists, its docstring literally says "so the notification
                # detail panel shows activity", orbs carry a `log` AND a
                # `steps` thread — and this dict, the one the tray actually
                # renders, simply never copied either of them.
                #
                # Measured live during a heartbeat, three entries in the tray,
                # all `running`, two of them named "Hourly heartbeat":
                #   task  28541f5d  log: 4 lines ("Asking gemma4:12b (local)…")
                #   orb   openai-f  log: ABSENT  -> "— waiting for activity —"
                #   orb   local-c7  log: ABSENT  -> "— waiting for activity —"
                # He was clicking the orb. It shares a name with the task, so
                # from outside they are the same row, and the orb was always
                # empty — which is why it was ALWAYS empty rather than
                # sometimes.
                # Orb's own log, else its step thread, else — when the orb is
                # backed by a real task — that task's log. `get_task` already
                # follows this link for the detail view; the list never did,
                # which is why the row he clicks looked dead and the row he
                # doesn't click had the lines.
                'log': (list(p.get('log') or [])
                        or _steps_as_log(p.get('steps'))
                        or _linked_task_log(p.get('task_id'))),
                'steps': list(p.get('steps') or []),
                'linked_task_id': p.get('task_id'),
            })
    return jsonify({"tasks": tasks})


def _linked_task_log(linked_tid):
    """The log of the task an orb represents, when it names one."""
    if not linked_tid:
        return []
    try:
        t = _task_snapshot(linked_tid)
        return list((t or {}).get("log") or [])
    except Exception:
        return []


def _steps_as_log(steps):
    """Render an orb's step thread as readable log lines.

    A process that reports structured steps but no prose (image generation
    does exactly this — phase/step/steps) still has plenty to show; it just
    was not in a shape the panel could read.
    """
    out = []
    for s in (steps or [])[-40:]:
        if not isinstance(s, dict):
            out.append(str(s))
            continue
        stamp = ""
        try:
            stamp = _time.strftime("%H:%M:%S", _time.localtime(s["ts"])) + " "
        except Exception:
            pass
        kind = s.get("type")
        if kind == "tool":
            out.append("%s→ tool %s → %s (%sms)" % (
                stamp, s.get("name"), s.get("status"), s.get("duration_ms")))
        elif kind == "phase":
            n, tot = s.get("step"), s.get("steps")
            out.append("%s%s%s" % (
                stamp, s.get("name") or "working",
                (" — step %s of %s" % (n, tot)) if tot else ""))
        else:
            out.append("%s%s" % (stamp, s.get("name") or json.dumps(s)[:80]))
    return out


@tasks_bp.route('/api/tasks/<task_id>')
def get_task(task_id):
    task = _task_snapshot(task_id)
    if task:
        return jsonify(task)

    # Fall back to PROCESSES when the id isn't a TASK (e.g. scheduler orbs,
    # vault-access orbs, and other process_register() entries).  Synthesise a
    # task-shaped response so the notification detail panel can show steps/log.
    import time as _t
    with PROCESSES_LOCK:
        proc = PROCESSES.get(task_id)
        if proc:
            proc = dict(proc)

    if not proc:
        return jsonify({"error": "Task not found"}), 404

    # If the process is backed by a real task (e.g. agent_prompt scheduled
    # jobs, background-task agent orbs), follow the link and return that
    # task's live log — enriched (B3) with the orb's model + correlation ids
    # so the thread panel shows which model served the loop.
    linked_tid = proc.get("task_id")
    if linked_tid:
        linked = _task_snapshot(linked_tid)
        if linked:
            linked = dict(linked)
            linked.setdefault("model", proc.get("model"))
            linked.setdefault("orb_id", proc.get("id") or task_id)
            return jsonify(linked)

    now = _t.time()
    started = proc.get("started", now)
    ended = proc.get("ended")
    steps = proc.get("steps") or []
    proc_log = proc.get("log") or []
    combined_log = proc_log + [f"[step] {s}" for s in steps if s not in proc_log]
    # Map raw process statuses to the task vocabulary the detail panel keys
    # its RESULT rendering on ('completed' never matched 'complete', so a
    # finished process's result silently never displayed).
    _status_map = {"completed": "complete", "error": "failed"}
    _raw_status = proc.get("status", "running")
    return jsonify({
        "task_id": task_id,
        "name": proc.get("label") or proc.get("name") or "Process",
        "status": _status_map.get(_raw_status, _raw_status),
        "progress": proc.get("progress", 0),
        "log": combined_log,
        # B3: expose the full enriched process record — the timed step entries
        # (tier-redacted args/results) and the serving model — so the existing
        # thread panel that polls this route renders the enriched trace.
        "steps": steps,
        "model": proc.get("model"),
        "linked_task_id": linked_tid,
        "result": proc.get("result"),
        "model": proc.get("model"),
        "category": proc.get("category"),
        "elapsed": int((ended or now) - started),
        "process": True,
    })


@tasks_bp.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    with TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id]['status'] = 'cancelled'
            del TASKS[task_id]
            return jsonify({"status": "cancelled"})
    return jsonify({"error": "Task not found"}), 404


@tasks_bp.route('/api/agent/steer', methods=['POST'])
@login_required
def api_agent_steer():
    """Push a follow-up prompt into a running task's dual-loop queue.

    POST body: { "task_id": "...", "message": "..." }
    The message is injected as a new user turn after the current agent pass finishes.
    """
    data = request.get_json() or {}
    task_id = (data.get('task_id') or '').strip()
    message = (data.get('message') or '').strip()
    if not task_id or not message:
        return jsonify({"error": "task_id and message are required"}), 400
    with TASKS_LOCK:
        if task_id not in TASKS:
            return jsonify({"error": "Task not found"}), 404
    with _FOLLOW_UP_LOCK:
        _FOLLOW_UP_QUEUES.setdefault(task_id, []).append(message)
    return jsonify({"ok": True, "task_id": task_id, "queued": message[:120]})


# How long a finished orb keeps ORBITING. Separate from how long its record
# stays explorable, which is the distinction the old code was missing: it kept
# monitoring processes for 900s and drew an orb for every one of them, so
# fifteen minutes of green orbs accumulated around the avatar.
ORB_VISIBLE_AFTER_DONE_S = 30
# A failed orb stays until acknowledged — but "until acknowledged" turned into
# "forever" because nothing could acknowledge it. Two caps keep persistence
# from becoming accumulation, without a failure ever vanishing unseen: it stops
# orbiting after this long, and only this many orbit at once. The RECORD
# survives either way (24h), so nothing is lost — it just stops crowding out
# the work in progress.
ORB_FAILED_MAX_AGE_S = 3600
ORB_FAILED_MAX_VISIBLE = 5


@tasks_bp.route('/api/processes')
def list_processes():
    """Live processes, each carrying whether it should still be ORBITING.

    Two lifetimes, deliberately different:

      * **orbit** — a completed orb is gone 30 seconds after it finishes.
        Stephen: "I do not want them hanging around in orbit around Friday's
        avatar for longer than that."
      * **record** — the detail (model, intent, log, result) stays explorable
        for the full retention window. The original comment here was right that
        transparency needs the detail to outlive the orb; it just expressed
        that by keeping the ORB alive too.

    A FAILED run never expires on the 30-second timer. A success that vanishes
    is fine — you saw it succeed, or you did not need to. A failure that
    vanishes before you looked at it is the machine hiding something, and this
    codebase has spent a day removing exactly that kind of quiet.
    """
    # An orphan should disappear on its own rather than wait to be asked
    # about. Runs before the lock: _reap_orphans takes it itself.
    try:
        _reap_orphans()
    except Exception:
        pass

    with PROCESSES_LOCK:
        out = []
        now = _time.time()
        for pid, p in list(PROCESSES.items()):
            row = dict(p)
            row["elapsed"] = int(now - row.get("started", now))
            if row.get("ended"):
                row["elapsed"] = int(row["ended"] - row["started"])

            status = row.get("status")
            failed = status in ("error", "failed", "cancelled", "timeout")
            ended = row.get("ended")
            if not ended:
                row["orb_visible"] = True                  # still working
            elif failed:
                # Persistent, but not permanent, and not a swarm. An orb older
                # than the cap stops ORBITING while its record stays queryable
                # — the failure is still there to read, it has just stopped
                # standing in front of everything else. Newest failures win the
                # remaining slots (applied after this loop).
                row["orb_visible"] = (not row.get("dismissed")
                                      and (now - ended) <= ORB_FAILED_MAX_AGE_S)
            else:
                row["orb_visible"] = (now - ended) <= ORB_VISIBLE_AFTER_DONE_S
            row["orb_failed"] = failed
            out.append(row)

            # Record retention, unchanged for successes. A failure is kept far
            # longer because it is the one a human still has questions about.
            if status in ("completed", "error", "failed", "timeout") and ended:
                if failed and not row.get("dismissed"):
                    _keep = 86400
                else:
                    _keep = 900 if row.get("category") == "monitoring" else 30
                if now - ended > _keep:
                    del PROCESSES[pid]

        # Cap how many failures orbit at once, newest first. Twenty failed
        # orbits around the avatar is not twenty times the information — it is
        # a wall. The ones pushed out are still in this response (and still in
        # the tray), they just stop competing for the ring.
        _vis_failed = sorted(
            [r for r in out if r.get("orb_failed") and r.get("orb_visible")],
            key=lambda r: r.get("ended") or 0, reverse=True)
        for _r in _vis_failed[ORB_FAILED_MAX_VISIBLE:]:
            _r["orb_visible"] = False
        out_failed_total = sum(1 for r in out if r.get("orb_failed")
                               and not r.get("dismissed"))
    return jsonify({"processes": out, "failed_pending": out_failed_total})


@tasks_bp.route('/api/processes/<pid>/cancel', methods=['POST'])
def cancel_process(pid):
    """Stop a running process and give the GPU back.

    Correctness, not decoration. An image job holds the Arbiter's EXCLUSIVE
    lease: until 2026-08-16 the only ways out were to wait it out or kill the
    server, and killing the server strands the lease with no language seats
    resident. Ninety seconds in and changing your mind should not cost you the
    machine.

    Best-effort and honest about it — the reply says what was actually stopped
    rather than claiming success for everything it tried.
    """
    stopped = []
    with PROCESSES_LOCK:
        proc = dict(PROCESSES.get(pid) or {})
    if not proc:
        return jsonify({"ok": False, "error": "no such process"}), 404

    # 1. The job flag, FIRST and unconditionally.
    #
    #    This used to go straight to ComfyUI's /interrupt and the Arbiter's
    #    lease, and both of those only exist partway through a job. The orb is
    #    registered before the lease is granted, so a cancel clicked in that
    #    window found nothing running, truthfully answered "nothing to
    #    interrupt" — and the generation carried on to completion regardless.
    #    The flag has no such window: it is set here and every stage of
    #    `local_image.generate` reads it.
    if str(pid).startswith("image-"):
        try:
            from agent_friday.services import local_image as _li
            _li.request_cancel(pid)
            stopped.append("the job")
            # Then ComfyUI, if it has got far enough to be sampling. Failure
            # here is expected and harmless when it hasn't.
            if _li.interrupt_comfy():
                stopped.append("image sampling")
        except Exception:
            pass

    # 2. The lease. The generation releases it itself on the way out, which is
    #    the tidier path because it also stops ComfyUI in the right order — so
    #    only step in if the job did not, and give it a moment to.
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if arb is not None and arb.lease:
            _deadline = _time.time() + 8
            while arb.lease and _time.time() < _deadline:
                _time.sleep(0.5)
            if arb.lease:
                arb.release()
                stopped.append("GPU lease")
            else:
                stopped.append("GPU lease (released by the job)")
    except Exception:
        pass

    with PROCESSES_LOCK:
        p2 = PROCESSES.get(pid)
        if p2 is not None:
            p2["status"] = "cancelled"
            p2["ended"] = _time.time()
            p2["label"] = "Cancelled: %s" % (p2.get("label") or "process")
    return jsonify({"ok": True, "stopped": stopped,
                    "note": ("nothing to interrupt — the process had already "
                             "finished or held no lease") if not stopped
                    else None})


@tasks_bp.route('/api/processes/dismiss-failed', methods=['POST'])
def dismiss_failed_processes():
    """Clear every failed orb at once.

    2026-08-16, Stephen: "The error orbs won't go away." The previous round got
    half of this right — a failure that vanishes on the same 30-second timer as
    a success is a failure the machine hid from you — and then shipped no way
    to acknowledge one. Persistent became permanent, and they accumulated
    around the avatar until they were the only thing there.

    A failure still never disappears on its own. It leaves when he says so.
    """
    cleared = 0
    with PROCESSES_LOCK:
        for _pid, row in PROCESSES.items():
            if (row.get("status") in ("error", "failed", "cancelled", "timeout")
                    and not row.get("dismissed")):
                row["dismissed"] = True
                cleared += 1
    return jsonify({"ok": True, "cleared": cleared})


# How long a "running" orb may keep orbiting with nothing behind it.
#
# A process that starts a thread and dies without calling process_update stays
# `running` forever, and `orb_visible` is unconditionally True while a row has
# no `ended` — so a crashed job orbits until the server restarts. Stephen ended
# the day with a desk full of them: "a bunch of error orbs and reasoning orbs
# floating on my desktop but I don't want or need them."
ORB_RUNNING_MAX_AGE_S = 1800


def _reap_orphans(now=None):
    """Retire `running` orbs whose work is plainly gone. Returns the ids.

    Conservative on purpose: only rows with no end time, older than the cap, and
    NOT holding the GPU lease. A live image job legitimately runs for minutes
    and must never be reaped out from under him — that is his work, not litter.
    """
    import time as _t
    now = now or _t.time()
    leased = set()
    try:
        from agent_friday.services.residency_arbiter import exclusive_lease
        _l = exclusive_lease() or {}
        if _l.get("model_id"):
            leased.add(str(_l.get("model_id")))
            leased.add(str(_l.get("role")))
            leased.add(str(_l.get("kind")))
    except Exception:
        pass

    reaped = []
    with PROCESSES_LOCK:
        for pid, row in list(PROCESSES.items()):
            if row.get("ended") or row.get("status") != "running":
                continue
            if (now - (row.get("started") or now)) <= ORB_RUNNING_MAX_AGE_S:
                continue
            blob = " ".join(str(row.get(k) or "") for k in ("label", "category", "kind"))
            if any(x and x in blob for x in leased):
                continue          # the GPU is genuinely working on this
            row["status"] = "error"
            row["ended"] = now
            row["dismissed"] = True   # it was never seen working; do not now
            row["label"] = (str(row.get("label") or pid) +
                            " (stopped without reporting - cleared)")
            reaped.append(pid)
    return reaped


@tasks_bp.route('/api/orbs/clear', methods=['POST'])
def clear_orbs():
    """Clear the desk. Body: {"scope": "finished"|"all"} (default "finished").

    "finished" retires everything that has stopped — completed, failed,
    cancelled, timed out — and reaps orphaned `running` rows. Anything actually
    working is left alone.

    "all" additionally retires live rows. Only for the case where he wants a
    clean desk and does not care what is mid-flight; it does not kill the work,
    it just stops the orb representing it from orbiting.

    This exists because per-orb dismissal was the only exit and there were more
    orbs than anyone wants to click. A record stays queryable after clearing —
    what stops is the orbiting.
    """
    import time as _t
    body = request.get_json(silent=True) or {}
    scope = str(body.get("scope") or "finished").strip().lower()
    now = _t.time()

    reaped = _reap_orphans(now)
    cleared = []
    with PROCESSES_LOCK:
        for pid, row in list(PROCESSES.items()):
            if row.get("dismissed"):
                continue
            done = bool(row.get("ended")) or row.get("status") != "running"
            if scope == "all" or done:
                row["dismissed"] = True
                if not row.get("ended"):
                    row["ended"] = now
                cleared.append(pid)
    return jsonify({"ok": True, "cleared": len(cleared),
                    "orphans_reaped": len(reaped),
                    "ids": cleared, "orphan_ids": reaped})


@tasks_bp.route('/api/processes/<pid>/dismiss', methods=['POST'])
def dismiss_process(pid):
    """Acknowledge a failed orb so it stops orbiting.

    Failures persist until dismissed rather than on a timer — a timer just
    means the failure disappears while you are looking somewhere else.
    """
    with PROCESSES_LOCK:
        p = PROCESSES.get(pid)
        if p is None:
            return jsonify({"ok": False, "error": "no such process"}), 404
        p["dismissed"] = True
    return jsonify({"ok": True})
