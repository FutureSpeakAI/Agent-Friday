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
    # Liveness (TV7) for the rows a reader watches: a running task whose
    # heartbeat has gone stale renders as stalled, never as running; the
    # "now:" line, cost so far and last_seen come from the record.
    try:
        from agent_friday.services import task_journal as _tj
        for t in tasks:
            if t.get('status') in ('running', 'queued', 'interrupted') and t.get('task_id'):
                t.update(_tj.liveness(t['task_id'], t.get('status')))
    except Exception:
        pass
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
    """Two meanings, chosen by the task's state (docs/design/active/
    task-visibility.md TV10/TV13): a RUNNING task is cancelled and its
    journal is kept — a record must survive the thing it records; a finished
    task is deleted, journal and all. This is the user-visible delete; nothing
    deletes a journal automatically unless the user set a retention period."""
    from agent_friday.services import task_journal as _tj
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        running = bool(t) and t.get('status') in ('queued', 'running')
    if running:
        from agent_friday.services.agent import _task_set
        _task_set(task_id, status='cancelled', ended=_time.time(),
                  result='[Cancelled] Stopped by the user; the record is kept.')
        return jsonify({"status": "cancelled", "journal": "kept"})
    with TASKS_LOCK:
        existed_in_cache = TASKS.pop(task_id, None) is not None
    existed_on_disk = _tj.delete(task_id)
    if not (existed_in_cache or existed_on_disk):
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"status": "deleted", "journal": "deleted"})


# ── Reading the record (task-visibility.md §4.5, TV6, TV11) ──────────────────
# Three reads over the same journal: the raw events (with a seq cursor and
# explicit gap reporting), a prompt-sized digest, and a tail. A principal
# other than the local user — an orchestrator holding the observer credential
# — gets every free-text field sealed through the egress gate and a ledger
# row saying what left; the user's own browser gets the record intact.

def _principal():
    try:
        from flask import g as _g
        return getattr(_g, "friday_principal", "user") or "user"
    except Exception:
        return "user"


def _serve_sealed(payload, task_id, route, *, events=0, reasoning=False):
    from agent_friday.services import task_journal as _tj
    who = _principal()
    sealed, redacted, withheld = _tj.seal_for_principal(payload, who)
    if who != "user":
        try:
            from agent_friday.services import activity_ledger as _al
            _al.record("journal_read", task_id=task_id, principal=who, route=route,
                       events=int(events), reasoning=bool(reasoning),
                       redacted=int(redacted), withheld=int(withheld))
        except Exception:
            pass
        if isinstance(sealed, dict):
            sealed["sealed_for"] = who
            sealed["redacted_fields"] = redacted
            sealed["withheld_fields"] = withheld
    return sealed


def _int_arg(name, default=0):
    try:
        return int(request.args.get(name, default) or default)
    except ValueError:
        return default


@tasks_bp.route('/api/tasks/<task_id>/journal')
def task_journal_events(task_id):
    """The task's append-only journal, oldest first. `since` is a seq cursor
    (events with seq > since). Gaps in the sequence are REPORTED, never
    hidden: `gaps` lists missing ranges, `cursor_ahead` says the cursor is
    past the newest event, `complete` is true only when neither applies."""
    from agent_friday.services import task_journal as _tj
    since = _int_arg('since', 0)
    limit = _int_arg('limit', 0) or None
    res = _tj.read_with_gaps(task_id, since=since, limit=limit)
    state = _tj.read_state(task_id)
    if not res["events"] and state is None and res["journal_last_seq"] == 0:
        return jsonify({"error": "Task not found"}), 404
    payload = {"task_id": task_id, "events": res["events"], "state": state,
               "last_seq": res["last_seq"], "journal_last_seq": res["journal_last_seq"],
               "gaps": res["gaps"], "cursor_ahead": res["cursor_ahead"], "complete": res["complete"]}
    return jsonify(_serve_sealed(payload, task_id, "journal", events=len(res["events"]),
                                 reasoning=any(e.get("kind") == "reasoning" for e in res["events"])))


@tasks_bp.route('/api/tasks/<task_id>/digest')
def task_digest(task_id):
    """A compact view sized for a prompt: status, cost, model and seat, the
    last N checkpoints and decisions, halts. Reasoning prose only with
    `?reasoning=1` — an explicit request — and for an observer it then
    passes through the gate with a ledger row like everything else."""
    from agent_friday.services import task_journal as _tj
    n = max(1, min(_int_arg('n', 20), 200))
    want_reasoning = request.args.get('reasoning', '0') in ('1', 'true', 'yes')
    d = _tj.digest(task_id, n=n, include_reasoning=want_reasoning)
    if d is None:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(_serve_sealed(d, task_id, "digest", events=len(d.get("checkpoints") or []),
                                 reasoning=want_reasoning))


def _sse_frames(task_id, since, principal, max_wait_s=None, poll_s=1.0, idle_close_s=30.0):
    """Generator for the SSE tail: replays everything after `since` (reporting
    a gap first if the cursor cannot be joined to the record), then follows
    new events until the task is terminal and quiet. Every frame carries
    `id: <seq>` so a reconnecting client resumes with Last-Event-ID and a
    dropped event is detected as a gap rather than silently skipped."""
    import json as _json
    from agent_friday.services import task_journal as _tj
    cursor = since
    started = _time.time()
    last_activity = started
    first = True
    while True:
        res = _tj.read_with_gaps(task_id, since=cursor)
        if first:
            first = False
            if res["cursor_ahead"]:
                yield ("event: gap\ndata: " + _json.dumps({"cursor": cursor, "journal_last_seq": res["journal_last_seq"],
                        "detail": "cursor is beyond the record; the journal was replaced or deleted"}) + "\n\n")
        for gp in res["gaps"]:
            yield "event: gap\ndata: " + _json.dumps(gp) + "\n\n"
        events = res["events"]
        if events:
            sealed, redacted, withheld = _tj.seal_for_principal({"events": events}, principal)
            for ev in sealed["events"]:
                yield f"id: {ev.get('seq')}\nevent: {ev.get('kind')}\ndata: {_json.dumps(ev, default=str)}\n\n"
            cursor = events[-1]["seq"]
            last_activity = _time.time()
            if principal != "user":
                try:
                    from agent_friday.services import activity_ledger as _al
                    _al.record("journal_read", task_id=task_id, principal=principal, route="events",
                               events=len(events), reasoning=any(e.get("kind") == "reasoning" for e in events),
                               redacted=redacted, withheld=withheld)
                except Exception:
                    pass
        st = _tj.read_state(task_id) or {}
        terminal = _tj.is_terminal(st.get("status"))
        if terminal and _time.time() - last_activity > 2 * poll_s:
            yield "event: end\ndata: " + _json.dumps({"status": st.get("status"), "last_seq": cursor}) + "\n\n"
            return
        if max_wait_s is not None and _time.time() - started >= max_wait_s:
            return
        if _time.time() - last_activity > idle_close_s and not terminal:
            yield ": keepalive\n\n"
            last_activity = _time.time()
        _time.sleep(poll_s)


@tasks_bp.route('/api/tasks/<task_id>/events')
def task_events(task_id):
    """The tail. JSON by default (same shape as /journal, cursor via `since`);
    `?stream=1` returns Server-Sent Events with `id:` = seq on every frame.
    A gap between the client's cursor and the record is announced as an
    `event: gap` frame before any data — a tail that looks complete and is
    not would defeat the reason the journal exists."""
    from agent_friday.services import task_journal as _tj
    since = _int_arg('since', 0)
    if not since:
        try:
            since = int(request.headers.get('Last-Event-ID', 0) or 0)
        except ValueError:
            since = 0
    if request.args.get('stream', '0') not in ('1', 'true', 'yes'):
        res = _tj.read_with_gaps(task_id, since=since)
        if not res["events"] and res["journal_last_seq"] == 0 and _tj.read_state(task_id) is None:
            return jsonify({"error": "Task not found"}), 404
        payload = {"task_id": task_id, "events": res["events"], "last_seq": res["last_seq"],
                   "journal_last_seq": res["journal_last_seq"], "gaps": res["gaps"],
                   "cursor_ahead": res["cursor_ahead"], "complete": res["complete"]}
        return jsonify(_serve_sealed(payload, task_id, "events", events=len(res["events"]),
                                     reasoning=any(e.get("kind") == "reasoning" for e in res["events"])))
    if _tj.read_state(task_id) is None and not _tj.read(task_id):
        return jsonify({"error": "Task not found"}), 404
    principal = _principal()
    max_wait = _int_arg('max_wait', 0) or None
    from flask import Response as _Resp, stream_with_context as _swc
    return _Resp(_swc(_sse_frames(task_id, since, principal, max_wait_s=max_wait)),
                 mimetype='text/event-stream',
                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@tasks_bp.route('/api/tasks/observer-token', methods=['POST', 'DELETE'])
@login_required
def observer_token():
    """Mint (POST) or revoke (DELETE) the read-only observer credential for
    orchestrators. User-only by construction: a request presenting an
    observer token is refused before it reaches here (core.check_auth), so
    an observer can neither mint nor revoke. The plaintext is returned once."""
    from agent_friday.services import observer_access as _obs
    if request.method == 'DELETE':
        return jsonify({"ok": True, "revoked": _obs.revoke()})
    token = _obs.mint()
    return jsonify({"ok": True, "token": token, "header": _obs.HEADER,
                    "scope": "read-only", "routes": list(_obs.READ_ONLY_PREFIXES),
                    "note": "Shown once. Steer, cancel, delete and settings are refused to this credential."})


@tasks_bp.route('/api/tasks/retention', methods=['GET', 'POST'])
@login_required
def task_retention():
    """Read or set task_journal.retention_days. 0 keeps every journal
    forever (the default); a positive number deletes journals of tasks that
    finished more than that many days ago, and only those."""
    from agent_friday.services import task_journal as _tj
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        try:
            days = int(data.get('retention_days', 0))
        except (TypeError, ValueError):
            return jsonify({"error": "retention_days must be an integer"}), 400
        if days < 0:
            return jsonify({"error": "retention_days must be 0 or more"}), 400
        from agent_friday.core import _load_settings_raw, _save_settings
        block = dict((_load_settings_raw().get('task_journal') or {}))
        block['retention_days'] = days
        _save_settings({'task_journal': block})
        deleted = _tj.apply_retention() if days > 0 else []
        return jsonify({"ok": True, "retention_days": days, "deleted": deleted})
    return jsonify({"ok": True, **_tj.settings()})


@tasks_bp.route('/api/agent/steer', methods=['POST'])
@login_required
def api_agent_steer():
    """Push a follow-up prompt into a running task's dual-loop queue.

    POST body: { "task_id": "...", "message": "...", "source": "<optional>" }
    The message is injected as a new user turn after the current agent pass finishes.

    `source` names who is steering, for the record only. It is recorded as
    `agent:<name>` when given (an orchestrator such as Fable or Astra acting
    on the user's behalf from the user's own session), otherwise `user`. It
    grants nothing: the read-only observer credential cannot reach this
    route at all (core.check_auth), so a steer always comes from the user's
    principal, and the source only says which hand the user used.
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
    source = _steer_source(data.get('source'))
    # Task journal (TV10): every steer is an event with a source.
    try:
        from agent_friday.services import task_journal as _tj
        _tj.steer(message, source=source, task_id=task_id)
    except Exception:
        pass
    return jsonify({"ok": True, "task_id": task_id, "queued": message[:120], "source": source})


def _steer_source(raw) -> str:
    """`user` unless the caller names an agent; then `agent:<slug>`, slug
    limited to [a-z0-9_-] and 32 chars so the journal never stores free text
    in a field that is rendered as an identity."""
    import re as _re
    s = str(raw or '').strip().lower()
    if s.startswith('agent:'):
        s = s[len('agent:'):]
    s = _re.sub(r'[^a-z0-9_-]+', '-', s).strip('-')[:32]
    return f"agent:{s}" if s and s != 'user' else 'user'


@tasks_bp.route('/api/tasks/<task_id>/stop-after-step', methods=['POST'])
@login_required
def stop_after_step(task_id):
    """Ask a running task to stop at its next checkpoint (TV10). The current
    step finishes; the next never starts; the record ends with a halt that
    names the step. User-only: an observer's POST is refused before this."""
    from agent_friday.services import task_journal as _tj
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        running = bool(t) and t.get('status') in ('queued', 'running')
    if not t:
        return jsonify({"error": "Task not found"}), 404
    if not running:
        return jsonify({"error": "task is not running", "status": t.get('status')}), 409
    _tj.request_stop(task_id)
    _tj.steer("stop after this step", source="user", task_id=task_id)
    return jsonify({"ok": True, "task_id": task_id, "stop_requested": True})


@tasks_bp.route('/api/tasks/<task_id>/rerun', methods=['POST'])
@login_required
def rerun_task(task_id):
    """Re-run an interrupted or finished task from its own prompt as a NEW
    task (TV8: nothing resumes automatically; this is the human choosing).
    The old record is untouched except for a decision pointing at the new
    task. User-only."""
    from agent_friday.services import task_journal as _tj
    from agent_friday.services.agent import _spawn_task
    with TASKS_LOCK:
        t = dict(TASKS.get(task_id) or {})
    if not t:
        t = _tj.read_state(task_id) or {}
    if not t:
        return jsonify({"error": "Task not found"}), 404
    if t.get('status') in ('queued', 'running'):
        return jsonify({"error": "task is still running", "status": t.get('status')}), 409
    prompt = t.get('prompt') or ''
    if not prompt.strip():
        return jsonify({"error": "no prompt recorded for this task; it cannot be re-run"}), 409
    new_id = _spawn_task(t.get('name') or 'Task', prompt, description=t.get('description') or '',
                         chain=t.get('chain'), chain_step=int(t.get('chain_step') or 0),
                         model=t.get('model'))
    _tj.decision("rerun", new_id, reason=f"re-run by the user from task {task_id}",
                 task_id=task_id, alternatives=["leave as is"])
    _tj.append(new_id, "decision", point="rerun_of", chosen=task_id,
               reason="spawned by the user from that task's recorded prompt")
    return jsonify({"ok": True, "task_id": new_id, "rerun_of": task_id})


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
        Maintainer ruling: "I do not want them hanging around in orbit
        around Friday's avatar for longer than that."
      * **record** — the detail (model, intent, log, result) stays explorable
        for the full retention window. The original comment here was right that
        transparency needs the detail to outlive the orb; it just expressed
        that by keeping the ORB alive too.

    A FAILED run never expires on the 30-second timer. A success that vanishes
    is fine — you saw it succeed, or you did not need to. A failure that
    vanishes before you looked at it is the machine hiding something.
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
    lease: without this the only ways out are to wait it out or kill the
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
    elif str(pid).startswith("video-"):
        # Same shape as the image branch above — local_video shares
        # local_image's cancel-flag/interrupt plumbing rather than duplicating
        # it, so this is the one other place that needs to know its prefix.
        try:
            from agent_friday.services import local_video as _lv
            _lv.request_cancel(pid)
            stopped.append("the job")
            if _lv.interrupt_comfy():
                stopped.append("video sampling")
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

    A failure that vanishes on the same 30-second timer as a success is a
    failure the machine hid from you — but persistent must not become
    permanent, or failed orbs accumulate around the avatar until they are the
    only thing there.

    A failure still never disappears on its own. It leaves when the user
    says so.
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
# no `ended` — so a crashed job would orbit until the server restarts, and
# a desktop fills with "error orbs and reasoning orbs" nobody wants or needs.
ORB_RUNNING_MAX_AGE_S = 1800


def _reap_orphans(now=None):
    """Retire `running` orbs whose work is plainly gone. Returns the ids.

    Conservative on purpose: only rows with no end time, older than the cap, and
    NOT holding the GPU lease. A live image job legitimately runs for minutes
    and must never be reaped out from under the user — that is their work,
    not litter.
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
