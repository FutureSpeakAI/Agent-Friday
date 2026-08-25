"""Friday's internal task scheduler (Part A of Self-Sufficient Friday).

Promotes the daily-only loop that lived in ``services/notifications.py`` to a
first-class, user-editable scheduler so Friday owns all her recurring work and
no longer depends on Cowork's external scheduled tasks.

What it adds over the old daily loop:
  * **interval** (every N minutes) + **daily** (HH:MM) + **weekly** (weekday +
    HH:MM) triggers, all evaluated on the same 60-second Central-time tick.
  * **schedules.json** — a JSON registry (``~/.friday/schedules.json``) that
    survives restart and is the source of truth the Settings UI reads/writes.
  * **run history** — append-only ``schedule_runs.jsonl`` with timestamp,
    duration, status, and an output summary; powers "last run / View history".
  * **retries** — configurable ``max`` attempts with ``backoff_seconds``,
    re-enqueued via the tick (never inline).
  * two task kinds — ``builtin`` (a registered Python callable that ships with
    Friday) and ``agent_prompt`` (any recurring agentic job, added from the UI
    with no code, run through the existing task/agent machinery).

Concurrency: the tick thread never runs a job inline — each due schedule is
dispatched to its own daemon thread, so a slow job can't delay the tick or the
Flask request path. ``FRIDAY_TESTING`` keeps the loop inert (like the other
daemons) but leaves the store + dispatch callable for unit tests.
"""

import json
import logging
import threading
import time as _time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("friday.scheduler")

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR, _load_settings, process_log, process_register, process_update

# ── Storage ──────────────────────────────────────────────────────────────────
SCHEDULES_FILE = FRIDAY_DIR / "schedules.json"
RUNS_FILE = FRIDAY_DIR / "schedule_runs.jsonl"
RUNS_KEEP = 500                       # compact once the file exceeds this
RUNS_PER_SCHEDULE = 25                # ...keeping this many runs PER schedule,
                                      # so a per-minute job cannot evict a daily
                                      # one's entire history (it did: 451/500)

_STORE_LOCK = threading.RLock()
_RUNS_LOCK = threading.Lock()

# ref -> {fn, label, default_trigger, default_spec, notify, weekday_only, source}
BUILTIN_TASKS: dict = {}
_RUNNING: set = set()                 # schedule ids currently dispatched
_RUNNING_LOCK = threading.Lock()


def _now_central():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        return datetime.now()


# ── Schedule store (schedules.json) ──────────────────────────────────────────
def _read_store() -> list:
    try:
        data = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):           # tolerate {"schedules": [...]}
            data = data.get("schedules", [])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_store(records: list):
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        SCHEDULES_FILE.write_text(json.dumps(records, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  [scheduler] store write failed: {e}")


def list_schedules() -> list:
    """All schedule records, each enriched with its computed next-run time."""
    with _STORE_LOCK:
        recs = [dict(r) for r in _read_store()]
    now = _now_central()
    for r in recs:
        r["next_run"] = _next_run_ts(r, now)
        r["running"] = r.get("id") in _RUNNING
    return recs


def get_schedule(sid):
    with _STORE_LOCK:
        for r in _read_store():
            if r.get("id") == sid:
                return dict(r)
    return None


def _upsert(record):
    with _STORE_LOCK:
        recs = _read_store()
        for i, r in enumerate(recs):
            if r.get("id") == record["id"]:
                recs[i] = record
                break
        else:
            recs.append(record)
        _write_store(recs)
    return record


def _patch_record(sid, **fields):
    """Merge fields into a stored record (used for run bookkeeping)."""
    with _STORE_LOCK:
        recs = _read_store()
        for r in recs:
            if r.get("id") == sid:
                r.update(fields)
                _write_store(recs)
                return dict(r)
    return None


# ── Public CRUD API ──────────────────────────────────────────────────────────
def _normalize_record(rec, *, source="user"):
    now = _time.time()
    trigger = rec.get("trigger", "daily")
    spec = dict(rec.get("spec") or {})
    sid = rec.get("id") or f"sch_{uuid.uuid4().hex[:10]}"
    out = {
        "id": sid,
        "name": rec.get("name") or sid,
        "trigger": trigger,
        "spec": spec,
        "task": dict(rec.get("task") or {}),
        "enabled": bool(rec.get("enabled", True)),
        "notify": rec.get("notify", "on_complete"),
        "retry": dict(rec.get("retry") or {"max": 0, "backoff_seconds": 300}),
        "timeout_seconds": int(rec.get("timeout_seconds", 1800)),
        "source": rec.get("source", source),
        "created": rec.get("created", now),
        "updated": now,
    }
    # Preserve run-bookkeeping fields if present.
    for k in ("last_run_ts", "last_run_date", "last_status", "last_summary",
              "not_before", "retry_count", "retry_pending"):
        if k in rec:
            out[k] = rec[k]
    return out


def register_schedule(record) -> dict:
    """Create a user-defined schedule from the API. Returns the stored record."""
    rec = _normalize_record(record, source="user")
    return _upsert(rec)


def update_schedule(sid, patch) -> dict | None:
    """Patch an existing schedule (name/trigger/spec/task/enabled/notify/...)."""
    with _STORE_LOCK:
        cur = get_schedule(sid)
        if not cur:
            return None
        allowed = {"name", "trigger", "spec", "task", "enabled", "notify",
                   "retry", "timeout_seconds"}
        for k, v in (patch or {}).items():
            if k in allowed:
                cur[k] = v
        cur["updated"] = _time.time()
        # Re-enabling or rescheduling clears any pending retry backoff.
        if patch and ("enabled" in patch or "trigger" in patch or "spec" in patch):
            cur["retry_pending"] = False
            cur["not_before"] = 0
        return _upsert(cur)


def delete_schedule(sid) -> bool:
    """Delete a user schedule. Built-ins can't be deleted (only disabled)."""
    with _STORE_LOCK:
        recs = _read_store()
        target = next((r for r in recs if r.get("id") == sid), None)
        if not target:
            return False
        if target.get("source") == "builtin":
            return False
        _write_store([r for r in recs if r.get("id") != sid])
    return True


# ── Built-in task registration ───────────────────────────────────────────────
def register_builtin_task(ref, fn, *, label, default_trigger="daily",
                          default_spec=None, notify="on_complete",
                          weekday_only=None, default_enabled=True):
    """Register a built-in task callable under ``ref``.

    The scheduler seeds a default schedule for it on first run (see
    ``_seed_and_reconcile``); thereafter the user's edits in schedules.json win.
    """
    BUILTIN_TASKS[ref] = {
        "fn": fn,
        "label": label,
        "default_trigger": default_trigger,
        "default_spec": dict(default_spec or {}),
        "notify": notify,
        "weekday_only": weekday_only,
        "default_enabled": default_enabled,
    }


def register_daily_job(name, hour, minute, fn):
    """Back-compat shim for the legacy daily-only API.

    Anything that still calls ``register_daily_job`` (kept exported from
    services.notifications) lands here as a built-in daily task so nothing in the
    tree breaks during the migration.
    """
    ref = name.replace("-", "_")
    register_builtin_task(ref, fn, label=name, default_trigger="daily",
                          default_spec={"hour": int(hour), "minute": int(minute)})


# ── Trigger math ─────────────────────────────────────────────────────────────
def _spec_hm(spec):
    return int(spec.get("hour", 9)), int(spec.get("minute", 0))


def _is_due(rec, now) -> bool:
    if not rec.get("enabled", True):
        return False
    with _RUNNING_LOCK:
        if rec.get("id") in _RUNNING:
            return False

    # A pending retry fires purely on its backoff timestamp, regardless of the
    # normal trigger window or the daily mark.
    if rec.get("retry_pending"):
        return now.timestamp() >= (rec.get("not_before") or 0)

    if (rec.get("not_before") or 0) > now.timestamp():
        return False

    trig = rec.get("trigger")
    spec = rec.get("spec") or {}

    if trig == "interval":
        every = max(1, int(spec.get("every_minutes", 60)))
        last = rec.get("last_run_ts") or 0
        return (now.timestamp() - last) >= every * 60

    if trig == "once":
        # One-shot (§6.2 scheduler extension): spec {at: <epoch>}. Fires when
        # the instant passes, exactly once — dispatch() auto-disables it.
        try:
            at = float(spec.get("at") or 0)
        except (TypeError, ValueError):
            return False
        return at > 0 and not rec.get("last_run_ts") and now.timestamp() >= at

    today = now.strftime("%Y-%m-%d")
    if rec.get("last_run_date") == today:
        return False
    if trig == "daily":
        return (now.hour, now.minute) >= _spec_hm(spec)
    if trig == "weekly":
        if now.weekday() != int(spec.get("weekday", 6)):
            return False
        return (now.hour, now.minute) >= _spec_hm(spec)
    return False


def _next_run_ts(rec, now):
    """Best-effort epoch of the next fire (for the UI). None when disabled."""
    if not rec.get("enabled", True):
        return None
    if rec.get("retry_pending"):
        return rec.get("not_before")
    trig = rec.get("trigger")
    spec = rec.get("spec") or {}
    try:
        if trig == "interval":
            every = max(1, int(spec.get("every_minutes", 60)))
            base = rec.get("last_run_ts") or now.timestamp()
            return base + every * 60
        if trig == "once":
            at = float(spec.get("at") or 0)
            return at if (at > 0 and not rec.get("last_run_ts")) else None
        from datetime import timedelta
        hour, minute = _spec_hm(spec)
        cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if trig == "daily":
            if cand <= now or rec.get("last_run_date") == now.strftime("%Y-%m-%d"):
                cand = cand + timedelta(days=1)
            return cand.timestamp()
        if trig == "weekly":
            wd = int(spec.get("weekday", 6))
            days_ahead = (wd - now.weekday()) % 7
            cand = cand + timedelta(days=days_ahead)
            if cand <= now:
                cand = cand + timedelta(days=7)
            return cand.timestamp()
    except Exception:
        return None
    return None


# ── Run history (schedule_runs.jsonl) ────────────────────────────────────────
def _append_run(entry):
    with _RUNS_LOCK:
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            with open(RUNS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            # Rotate PER SCHEDULE, not globally.
            #
            # A flat "keep the last N lines" is a rotation policy that lets the
            # noisiest job delete everyone else's history. Measured 2026-08-16:
            # `content_publisher`, running every 60 seconds, held 451 of the 500
            # slots, and session_summary / memory_dreaming / learning_epoch had
            # ZERO surviving records. That is not a cosmetic problem — it is why
            # "has anything been distilled to my wiki lately?" could not be
            # answered from the log at all, and the question stayed open a day.
            #
            # Each schedule now keeps its own last RUNS_PER_SCHEDULE records, so
            # a once-a-day job's history survives a once-a-minute neighbour.
            lines = RUNS_FILE.read_text(encoding="utf-8").splitlines()
            if len(lines) > RUNS_KEEP:
                per, kept = {}, []
                for ln in reversed(lines):          # newest first
                    try:
                        sid = json.loads(ln).get("id") or "?"
                    except Exception:
                        continue
                    if per.get(sid, 0) >= RUNS_PER_SCHEDULE:
                        continue
                    per[sid] = per.get(sid, 0) + 1
                    kept.append(ln)
                kept.reverse()
                RUNS_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")
        except Exception as e:
            print(f"  [scheduler] run-history write failed: {e}")


def run_history(sid=None, limit=10):
    """Return recent run records, newest first; filtered by schedule id."""
    out = []
    try:
        lines = RUNS_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return out
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if sid and r.get("id") != sid:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


# ── Dispatch ─────────────────────────────────────────────────────────────────
def _summarize(result) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result[:500]
    if isinstance(result, dict):
        for k in ("summary", "message", "title", "result"):
            if result.get(k):
                return str(result[k])[:500]
        return json.dumps(result, default=str)[:500]
    return str(result)[:500]


def _changed(result) -> bool:
    """on_change notify: did this run produce a delta worth surfacing?"""
    if isinstance(result, dict):
        if "changed" in result:
            return bool(result.get("changed"))
        if "count" in result:
            return int(result.get("count") or 0) > 0
    # agent_prompt schedules (heartbeat, job intelligence) are told to reply
    # EXACTLY "NO CHANGE" when there's nothing new. A non-empty string is truthy,
    # so without this the heartbeat "changed" every hour and spammed a
    # notification each run. Treat the sentinel (and empty) as no-change.
    if isinstance(result, str):
        norm = result.strip().rstrip(".").upper()
        return bool(norm) and norm != "NO CHANGE"
    return bool(result)


def _notify_run(rec, status, summary):
    """Push a terminal-state notification. The dispatcher decides whether to
    call this (on_complete always; on_change only on a delta; silent never on
    success). Failures always notify regardless of mode."""
    # The notification engine lives in services.voice_engine; import lazily so
    # the scheduler stays a leaf module.
    try:
        from agent_friday.services.voice_engine import _notif_engine as _ne
    except Exception:
        _ne = None
    if not _ne:
        return
    try:
        if status == "failed":
            # Failure bodies carry the WHOLE reason. The old [:300] cut the
            # 2026-08-14 heartbeat error mid-word, right before the leg that
            # named the actionable cause ("openai: No OpenAI-compatible API
            # key set") — Stephen saw a truncated blob and read it as "no
            # detail". 900 chars covers a three-leg escalation error;
            # anything longer is a traceback that belongs in the log.
            _ne.push(title=f"⚠️ Scheduled task failed: {rec.get('name')}",
                     body=(summary or "")[:900], priority="high", source="scheduler",
                     kind="scheduled_task",
                     actions=[{"label": "View history", "workspace": "system",
                               "tab": "schedules"}],
                     dedupe_key=f"sched-fail:{rec.get('id')}:{_now_central().strftime('%Y%m%d%H')}")
            return
        _ne.push(title=f"✓ {rec.get('name')} ran",
                 body=(summary or "")[:300] or "Completed.", priority="low",
                 source="scheduler", kind="scheduled_task",
                 dedupe_key=f"sched-ok:{rec.get('id')}:{_now_central().strftime('%Y%m%d%H%M')}")
    except Exception as e:
        print(f"  [scheduler] notify failed: {e}")


def _notify_status(rec, summary):
    """`notify='status'` mode: maintain ONE self-updating, already-read panel
    entry showing the latest run time (used by the hourly heartbeat). It never
    increments the unread badge and never appends a second row — so a task that
    runs every hour can't spam the notifications panel."""
    try:
        from agent_friday.services.voice_engine import _notif_engine as _ne
    except Exception:
        _ne = None
    if not _ne or not hasattr(_ne, "upsert_status"):
        return
    try:
        when = _now_central().strftime("%b %d at %H:%M")
        body = (summary or "").strip()
        if not body or body.rstrip(".").upper() == "NO CHANGE":
            body = "No new activity."
        _ne.upsert_status(
            key=f"sched-status:{rec.get('id')}",
            title=f"💓 {rec.get('name')} — last ran {when}",
            body=body[:300], source="scheduler", kind="scheduled_status",
            priority="low",
            target={"workspace": "system", "tab": "schedules"})
    except Exception as e:
        print(f"  [scheduler] status update failed: {e}")


def _make_scheduler_orb(rec) -> bool:
    """Whether a scheduled run gets its own holographic scheduler orb.

    No orb for:
      • silent schedules  — invisible maintenance (e.g. the 1-min content
        publisher); an orb every tick is the "unnecessary background task" spam.
      • agent_prompt schedules — the spawned agent already renders its own orb
        (⏰ + model badge), so a wrapper orb would just duplicate it.
    """
    kind = (rec.get("task") or {}).get("kind", "builtin")
    return rec.get("notify") != "silent" and kind != "agent_prompt"


def _orb_model_for(rec) -> str:
    """The model a scheduled run executes under, so its holographic orb always
    carries a model badge (never a blank orb)."""
    try:
        s = _load_settings() or {}
    except Exception:
        s = {}
    task = rec.get("task") or {}
    if task.get("kind") == "agent_prompt":
        return s.get("subagent_model") or core.ANTHROPIC_MODEL_DEFAULT
    return (s.get("orchestrator_model") or s.get("subagent_model")
            or core.ANTHROPIC_MODEL_DEFAULT)


def _run_task(rec):
    """Execute a schedule's task and return its result (may raise)."""
    task = rec.get("task") or {}
    kind = task.get("kind", "builtin")
    if kind == "builtin":
        ref = task.get("ref")
        meta = BUILTIN_TASKS.get(ref)
        if not meta:
            raise RuntimeError(f"unknown builtin task ref {ref!r}")
        return meta["fn"]()
    # agent_prompt — run through the existing background-task machinery so the
    # scheduled run gets its own fresh vault context, orbs, and verification.
    prompt = task.get("prompt") or ""
    if not prompt.strip():
        raise RuntimeError("agent_prompt schedule has no prompt")
    from agent_friday.services.agent import _spawn_task, _task_snapshot
    run_id = rec.get("_active_run_id")
    # A scheduled agent_prompt is represented by the spawned agent's OWN orb
    # (which already carries its model badge). Give that orb the ⏰ icon so a
    # scheduled run reads as a single timer orb — no separate scheduler wrapper
    # orb, so no duplicate/double-icon orb.
    tid = _spawn_task(rec.get("name") or "Scheduled task", prompt,
                      description=f"scheduled:{rec.get('id')}", orb_icon="⏰")
    # Link the scheduler's process orb to the spawned task so the notification
    # detail panel can stream the task's live log.
    orb_id = rec.get("_orb_id")
    if orb_id:
        try:
            process_update(orb_id, task_id=tid)
            process_log(orb_id, f"Spawned agent task {tid}")
        except Exception:
            pass
    # Associate the spawned task with this schedule for cost attribution (Part D).
    try:
        from agent_friday.services import cost_meter as _cm
        if hasattr(_cm, "register_task_attribution"):
            _cm.register_task_attribution(tid, {
                "kind": "scheduled", "schedule_id": rec.get("id"),
                "run_id": run_id, "workspace": (task.get("workspace") or "task"),
            })
    except Exception:
        pass
    timeout = int(rec.get("timeout_seconds", 1800))
    deadline = _time.time() + timeout
    terminal = {"complete", "completed", "completed_unverified", "failed",
                "timeout", "error", "cancelled"}
    while _time.time() < deadline:
        snap = _task_snapshot(tid) or {}
        if snap.get("status") in terminal:
            if snap.get("status") in ("failed", "error"):
                raise RuntimeError(snap.get("result") or "agent task failed")
            return snap.get("result") or snap.get("status")
        _time.sleep(2)
    raise TimeoutError(f"agent task exceeded {timeout}s")


def dispatch(rec, *, manual=False):
    """Run a due (or manually-triggered) schedule on its own daemon thread."""
    sid = rec.get("id")
    with _RUNNING_LOCK:
        if sid in _RUNNING and not manual:
            return None
        _RUNNING.add(sid)

    run_id = f"run_{uuid.uuid4().hex[:10]}"
    started = _time.time()
    now = _now_central()

    # Mark-before-run so a long job can't double-fire on the next tick.
    # A 'once' trigger auto-disables at fire time — it never runs twice.
    _mark = dict(last_run_ts=started, last_run_date=now.strftime("%Y-%m-%d"))
    if rec.get("trigger") == "once":
        _mark["enabled"] = False
    _patch_record(sid, **_mark)

    # Holographic orb policy:
    #   • silent schedules (e.g. the 1-min content publisher) are invisible
    #     maintenance — no orb, so they stop spawning an "unnecessary task" orb
    #     every tick.
    #   • agent_prompt schedules get their orb from the spawned agent itself
    #     (⏰ icon + model badge via _run_task), so a scheduler wrapper orb would
    #     just duplicate it — skip it.
    # The remaining (non-silent builtin) orbs carry a model badge and a single
    # ⏰ icon (the label no longer repeats the emoji).
    orb_id = f"sched-{sid}-{run_id[-6:]}" if _make_scheduler_orb(rec) else None
    if orb_id:
        try:
            process_register(orb_id, name="Scheduler",
                             label=rec.get('name'), category="monitoring",
                             icon="⏰", model=_orb_model_for(rec))
        except Exception:
            orb_id = None

    def _body():
        status, summary, err = "complete", "", None
        rec_live = dict(rec, _active_run_id=run_id, _orb_id=orb_id)
        if orb_id:
            try:
                process_log(orb_id, f"Starting: {rec.get('name')}")
            except Exception:
                pass
        try:
            result = _run_task(rec_live)
            summary = _summarize(result)
            if orb_id:
                try:
                    process_log(orb_id, f"Completed: {summary[:200]}" if summary else "Completed.")
                except Exception:
                    pass
            # Notification policy by mode:
            #   silent    → never (invisible maintenance, e.g. content publisher)
            #   status    → one self-updating, already-read panel entry (heartbeat);
            #               never a badge notification
            #   on_change → only on a real delta
            #   else      → always
            _mode = rec.get("notify", "on_complete")
            _patch_record(sid, last_status="complete", last_summary=summary,
                          retry_pending=False, retry_count=0, not_before=0)
            if _mode == "silent":
                pass
            elif _mode == "status":
                _notify_status(rec, summary)
            elif _mode == "on_change" and not _changed(result):
                pass
            else:
                _notify_run(rec, "complete", summary)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            traceback.print_exc()
            attempts = int(rec.get("retry_count", 0)) + 1
            maxr = int((rec.get("retry") or {}).get("max", 0))
            backoff = int((rec.get("retry") or {}).get("backoff_seconds", 300))
            if attempts <= maxr:
                status = "retry_scheduled"
                summary = f"{err} — retry {attempts}/{maxr} in {backoff}s"
                _patch_record(sid, last_status="retry_scheduled",
                              last_summary=summary, retry_pending=True,
                              retry_count=attempts,
                              not_before=_time.time() + backoff)
            else:
                status = "failed"
                summary = err
                _patch_record(sid, last_status="failed", last_summary=err,
                              retry_pending=False, retry_count=0, not_before=0)
                _notify_run(rec, "failed", err)
        finally:
            ended = _time.time()
            _append_run({
                "id": sid, "run_id": run_id, "name": rec.get("name"),
                "started": started, "ended": ended,
                "duration_ms": int((ended - started) * 1000),
                "status": status, "summary": summary, "error": err,
                "manual": manual,
            })
            if orb_id:
                try:
                    process_update(orb_id,
                                   status="completed" if status != "failed" else "error",
                                   progress=1.0,
                                   label=f"{rec.get('name')} — {status}")
                except Exception:
                    pass
            with _RUNNING_LOCK:
                _RUNNING.discard(sid)

    threading.Thread(target=_body, daemon=True).start()
    return run_id


def run_now(sid):
    """Dispatch a schedule immediately (UI 'Run now'). Returns the run id."""
    rec = get_schedule(sid)
    if not rec:
        return None
    return dispatch(rec, manual=True)


# ── First-run seed + reconcile ───────────────────────────────────────────────
def _seed_and_reconcile():
    """Materialize default schedules for built-ins, non-destructively.

    Fresh install → seed the full roster. Existing install → add any NEW
    built-in refs not already present (so upgrades pick up new tasks) without
    ever overwriting a user-edited record.
    """
    with _STORE_LOCK:
        recs = _read_store()
        existing_refs = {(r.get("task") or {}).get("ref")
                         for r in recs if (r.get("task") or {}).get("kind") == "builtin"}
        existing_kinds = {r.get("id") for r in recs}
        added = 0
        for ref, meta in BUILTIN_TASKS.items():
            if ref in existing_refs:
                continue
            sid = f"sch_{ref}"
            if sid in existing_kinds:
                continue
            rec = _normalize_record({
                "id": sid,
                "name": meta["label"],
                "trigger": meta["default_trigger"],
                "spec": meta["default_spec"],
                "task": {"kind": "builtin", "ref": ref},
                # Most builtins seed on; a few (the content publisher) are
                # maintenance for a feature the owner may never touch, and a
                # job nobody asked for should not run by default.
                "enabled": meta.get("default_enabled", True),
                "notify": meta["notify"],
            }, source="builtin")
            recs.append(rec)
            added += 1
        if added:
            _write_store(recs)
            print(f"  [scheduler] seeded {added} built-in schedule(s).")


# ── Default built-in roster (the 7 migrated tasks + the existing extras) ──────
def _register_default_builtin_tasks():
    """Register Friday's shipped built-in tasks. Backing fns imported lazily so
    the scheduler stays a leaf module with no import-cycle risk."""
    try:
        settings = _load_settings()
    except Exception:
        settings = {}

    # daily-creation — generative art/code/writing piece.
    try:
        from agent_friday.services.creations import generate_daily_creation
        hour = int(settings.get("daily_creation_hour", 8))
        minute = int(settings.get("daily_creation_minute", 0))
        register_builtin_task("daily_creation", generate_daily_creation,
                              label="Daily creation", default_trigger="daily",
                              default_spec={"hour": hour, "minute": minute})
    except Exception as e:
        print(f"  [scheduler] daily_creation unavailable: {e}")

    # news-briefing (morning) + front-page evening edition.
    try:
        from agent_friday.services.news_engine import _run_front_page_job, FRONT_PAGE_SLOTS
        register_builtin_task("news_morning",
                              lambda: _run_front_page_job("morning"),
                              label="Morning news briefing", default_trigger="daily",
                              default_spec={"hour": FRONT_PAGE_SLOTS["morning"], "minute": 0})
        register_builtin_task("front_page_evening",
                              lambda: _run_front_page_job("evening"),
                              label="Evening front page", default_trigger="daily",
                              default_spec={"hour": FRONT_PAGE_SLOTS["evening"], "minute": 0})
    except Exception as e:
        print(f"  [scheduler] news front-page unavailable: {e}")

    # afternoon-briefing — synthesized daily briefing (markdown).
    try:
        register_builtin_task("afternoon_briefing", _afternoon_briefing_job,
                              label="Afternoon briefing", default_trigger="daily",
                              default_spec={"hour": 16, "minute": 0})
    except Exception as e:
        print(f"  [scheduler] afternoon_briefing unavailable: {e}")

    # weekly digest (Sun) + weekly editorial (Fri) — self-guard the weekday.
    try:
        from agent_friday.services.news_engine import (_run_weekly_digest_job,
                                           _run_weekly_editorial_job,
                                           WEEKLY_DIGEST_HOUR, WEEKLY_EDITORIAL_HOUR)
        register_builtin_task("weekly_digest", _run_weekly_digest_job,
                              label="Weekly digest", default_trigger="weekly",
                              default_spec={"weekday": 6, "hour": WEEKLY_DIGEST_HOUR, "minute": 0})
        register_builtin_task("weekly_editorial", _run_weekly_editorial_job,
                              label="Weekly editorial", default_trigger="weekly",
                              default_spec={"weekday": 4, "hour": WEEKLY_EDITORIAL_HOUR, "minute": 0})
    except Exception as e:
        print(f"  [scheduler] weekly jobs unavailable: {e}")

    # self-improvement — weekly Sunday epistemic review.
    try:
        from agent_friday.services.notifications import _run_self_improvement_job
        si_hour = int(settings.get("self_improvement_hour", 9))
        register_builtin_task("self_improvement", _run_self_improvement_job,
                              label="Weekly self-improvement", default_trigger="weekly",
                              default_spec={"weekday": 6, "hour": si_hour, "minute": 0})
    except Exception as e:
        print(f"  [scheduler] self_improvement unavailable: {e}")

    # session-summary — end-of-day continuity note.
    try:
        from agent_friday.services.model_router import _run_session_summary_job
        register_builtin_task("session_summary", _run_session_summary_job,
                              label="Session summary", default_trigger="daily",
                              default_spec={"hour": 23, "minute": 30}, notify="silent")
    except Exception as e:
        print(f"  [scheduler] session_summary unavailable: {e}")

    # repo-sync — git pull across the configured repos (deterministic builtin).
    try:
        from agent_friday.services.repo_sync import run_repo_sync
        register_builtin_task("repo_sync", run_repo_sync,
                              label="Repo sync", default_trigger="daily",
                              default_spec={"hour": 6, "minute": 0}, notify="on_change")
    except Exception as e:
        print(f"  [scheduler] repo_sync unavailable: {e}")

    # memory-dreaming (v5) — nightly local consolidation of the day's turns.
    try:
        from agent_friday.services.memory_dreaming import dream as _dream
        md_hour = int((settings.get("memory_dreaming") or {}).get("hour", 3))
        register_builtin_task("memory_dreaming", _dream,
                              label="Memory dreaming", default_trigger="daily",
                              default_spec={"hour": md_hour, "minute": 0}, notify="silent")
    except Exception as e:
        print(f"  [scheduler] memory_dreaming unavailable: {e}")

    # learning-epoch (v5) — weekly mine→promote cycle over task outcomes.
    try:
        from agent_friday.services.learning_loop import run_epoch as _learn_epoch
        le_wd = int((settings.get("learning_loop") or {}).get("epoch_weekday", 6))
        register_builtin_task("learning_epoch", _learn_epoch,
                              label="Learning epoch", default_trigger="weekly",
                              default_spec={"weekday": le_wd, "hour": 4, "minute": 0},
                              notify="on_complete")
    except Exception as e:
        print(f"  [scheduler] learning_epoch unavailable: {e}")

    # goal-milestones tick (A3) — advance any due, still-pending milestone on
    # every active goal. Silent: each advanced milestone already produces its
    # own receipt/work_log entry and (when gated) an approval notification;
    # a wrapper "ran the tick" notification would just be noise.
    try:
        from agent_friday.services.goals import run_due_milestones
        register_builtin_task("goal_milestones_tick", run_due_milestones,
                              label="Goal milestones", default_trigger="interval",
                              default_spec={"every_minutes": 30}, notify="silent")
    except Exception as e:
        print(f"  [scheduler] goal_milestones_tick unavailable: {e}")

    # weekly goal review (A3) — aggregate work_log by goal_id into a review
    # doc; rolls over any still-incomplete goal's deadlines.
    try:
        from agent_friday.services.goals import run_weekly_review
        register_builtin_task("goals_weekly_review", run_weekly_review,
                              label="Weekly goal review", default_trigger="weekly",
                              default_spec={"weekday": 6, "hour": 18, "minute": 0},
                              notify="on_change")
    except Exception as e:
        print(f"  [scheduler] goals_weekly_review unavailable: {e}")

    # approvals expiry sweep (A3) — expire stale pending approvals per the Q3
    # policy table. Blocked steps stay blocked either way; this only moves a
    # timed-out "pending" card to "expired" so it stops silently sitting open.
    try:
        from agent_friday.services.approvals import expire_stale
        register_builtin_task("approvals_expiry_sweep", expire_stale,
                              label="Approval expiry sweep", default_trigger="interval",
                              default_spec={"every_minutes": 60}, notify="silent")
    except Exception as e:
        print(f"  [scheduler] approvals_expiry_sweep unavailable: {e}")

    # The Friday Edition (E0) — morning compose. Distinct time from news_morning
    # (07:00) so the two never collide; composes over existing engines only.
    try:
        from agent_friday.services.edition_engine import run_edition_job
        register_builtin_task("edition_daily", run_edition_job,
                              label="The Friday Edition", default_trigger="daily",
                              default_spec={"hour": 6, "minute": 30})
    except Exception as e:
        print(f"  [scheduler] edition_daily unavailable: {e}")


def _afternoon_briefing_job():
    """Synthesize the afternoon briefing markdown and persist it (so the
    'briefing ready' notification + News panel pick it up). Mirrors the on-demand
    /api/news/briefing/generate path but runs unattended."""
    from agent_friday.services.news_engine import _gather_live_briefing_context, _notify_briefing
    from agent_friday.services.model_router import (
        _gated_vault_control, _generate_text, _get_friday_system_prompt,
        _predict_route_provider)
    live_context = _gather_live_briefing_context()
    prompt = (
        "Generate a crisp afternoon briefing using the LIVE DATA below plus what "
        "you know about me. Cover, in order: remaining calendar for today, the "
        "day's most relevant news, open tasks needing attention, and one "
        "proactive insight. Clean markdown, lead with the most urgent item.\n\n"
        f"{live_context}"
    )
    # UNATTENDED (2026-08-25): this runs daily at 16:00 with nobody watching —
    # a gating gap here leaks silently, every day, not once. Gate exactly like
    # every other briefing call site rather than trusting the egress
    # classifier alone.
    system = _get_friday_system_prompt(
        keywords=prompt, workspace="briefing",
        provider=_predict_route_provider(keywords=prompt, workspace="briefing"),
        vault_control=_gated_vault_control())
    content = _generate_text([{"role": "user", "content": prompt}], system=system,
                             temperature=0.4, orb_label="☀️ Afternoon Briefing",
                             workspace="briefing")
    if not content or not content.strip():
        return {"changed": False, "summary": "empty briefing"}
    date_str = datetime.now().strftime("%Y-%m-%d")
    briefings_dir = FRIDAY_DIR / "wiki" / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    (briefings_dir / f"{date_str}.md").write_text(content, encoding="utf-8")
    try:
        _notify_briefing(date_str)
    except Exception:
        pass
    return {"changed": True, "summary": f"Afternoon briefing for {date_str}"}


# ── Default agent_prompt schedules (no code; user-tweakable) ──────────────────
# heartbeat + job-intelligence ship as agent_prompt schedules per the spec so
# they're maximally user-editable. They're seeded once (alongside the built-ins)
# and then owned by the user. Seeded only on a fresh store.
_DEFAULT_AGENT_SCHEDULES = [
    {
        "id": "sch_heartbeat",
        "name": "Hourly heartbeat",
        "trigger": "interval",
        "spec": {"every_minutes": 60},
        "task": {
            "kind": "agent_prompt", "workspace": "system",
            "prompt": (
                "Hourly heartbeat (observe-and-notify only — take no real-world "
                "actions). Check my calendar and inbox for anything new or "
                "time-sensitive, and review any background tasks that finished "
                "since the last hour. If something is genuinely actionable, "
                "summarize it in one or two lines. If nothing is new, reply "
                "exactly: NO CHANGE."
            ),
        },
        "enabled": True,
        # 'status': keep ONE self-updating "last ran …" entry in the panel; never
        # emit a per-run notification or bump the unread badge (anti-spam).
        "notify": "status",
    },
    {
        "id": "sch_job_intelligence",
        "name": "Job intelligence",
        "trigger": "daily",
        "spec": {"hour": 7, "minute": 30},
        "task": {
            "kind": "agent_prompt", "workspace": "research",
            "prompt": (
                "Scan for new roles relevant to my career pipeline and maintain "
                "my top-25 list. Use web search. Report only the deltas since "
                "yesterday — new roles worth adding and roles that should drop "
                "off — in a short bulleted summary. If there are no changes, "
                "reply exactly: NO CHANGE."
            ),
        },
        "enabled": True,
        "notify": "on_change",
    },
]


def _seed_default_agent_schedules():
    with _STORE_LOCK:
        recs = _read_store()
        ids = {r.get("id") for r in recs}
        added = 0
        for d in _DEFAULT_AGENT_SCHEDULES:
            if d["id"] in ids:
                continue
            recs.append(_normalize_record(d, source="builtin"))
            added += 1
        # One-time migration: the heartbeat shipped with notify='on_change',
        # which (with the _changed sentinel bug) spammed the panel hourly. Move
        # any still-default heartbeat to the quiet self-updating 'status' entry.
        migrated = 0
        for r in recs:
            if r.get("id") == "sch_heartbeat" and r.get("notify") == "on_change":
                r["notify"] = "status"
                migrated += 1
        if added or migrated:
            _write_store(recs)
            if added:
                print(f"  [scheduler] seeded {added} default agent schedule(s).")
            if migrated:
                print("  [scheduler] migrated heartbeat notify → 'status'.")


# ── The tick loop ────────────────────────────────────────────────────────────
_STARTED = False


def _uses_gpu(rec) -> bool:
    """Will running this schedule put work on the GPU?

    Conservative on purpose: anything that reaches a model counts. A schedule
    that only moves files is not worth delaying, but guessing wrong in that
    direction merely delays a chore, and guessing wrong the other way is what
    took his machine down.
    """
    kind = (rec or {}).get("kind") or (rec or {}).get("action") or ""
    if kind in ("agent_prompt", "agent", "task", "prompt", "briefing",
                "digest", "consolidation", "dreaming"):
        return True
    # Unknown kinds default to "yes, it might" — see the docstring.
    return not kind.startswith(("file", "cleanup", "prune", "publish"))


def _tick():
    now = _now_central()
    with _STORE_LOCK:
        recs = [dict(r) for r in _read_store()]
    # Anything that wakes on a timer defers while the card is leased.
    #
    # Queue-then-run, not skip: an hourly heartbeat that runs a few minutes
    # late costs nothing, whereas a heartbeat that runs DURING an image job
    # costs a slowed generation and a desktop that crawls. The record is left
    # unmarked, so the very next tick after the lease releases runs it — no
    # separate queue to drain and nothing silently dropped.
    #
    # Manual dispatch is deliberately not gated: if he asks for it now, he gets
    # it now, and he can see what else is running.
    held = None
    try:
        from agent_friday.services.residency_arbiter import exclusive_lease
        held = exclusive_lease()
    except Exception:
        held = None

    for rec in recs:
        try:
            if _is_due(rec, now):
                if held and _uses_gpu(rec):
                    _log.info("holding %s: the %s job holds the GPU; it will "
                              "run on the first tick after that releases",
                              rec.get('id'), held.get('kind') or 'current')
                    continue
                dispatch(rec)
        except Exception as e:
            _log.warning("tick error [%s]: %s", rec.get('id'), e)
    _reclaim_expired_lease()
    _away_drain_tick()


# ── P5: the away-drain, OFF by default ────────────────────────────────────────
#
# work_queue.drain() exists and holds one Arbiter lease across a whole batch,
# but nothing ever calls it on a timer — it fires only from an HTTP route the
# UI must poll, so queued `when_away` work simply waits forever if nobody
# opens the panel.
#
# Deliberately DEFAULT OFF, and that is not caution theatre. This is the one
# piece of the design that starts taking the GPU on a schedule, and taking the
# GPU is not free on this machine: Stephen lost a second monitor to VRAM
# pressure on 2026-08-17. A background job that quietly claims the card while
# he is working is a job that can take his screen. He turns it on.
#
# Even when enabled it will not start a drain without display headroom.

def away_drain_enabled() -> bool:
    try:
        return bool(((core._load_settings() or {}).get("away_drain") or {})
                    .get("enabled", False))
    except Exception:
        return False


def away_drain_state() -> dict:
    """Reportable, like the judgment gate — a scheduler that is off should be
    visibly off rather than indistinguishable from one that is on and idle."""
    enabled = away_drain_enabled()
    pending = None
    try:
        from agent_friday.services import work_queue
        pending = len(work_queue.batch_ready("heavy", min_items=1) or [])
    except Exception:
        pass
    return {
        "enabled": enabled,
        "pending_heavy": pending,
        "summary": ("ON — queued heavy work drains automatically when the GPU "
                    "has room." if enabled else
                    "OFF — queued heavy work waits until you drain it. Friday "
                    "will not take the GPU on a timer unless you turn this on."),
    }


def _away_drain_tick() -> None:
    if not away_drain_enabled():
        return
    try:
        from agent_friday.services import gpu_headroom, work_queue
        batch = work_queue.batch_ready("heavy", min_items=1)
        if not batch:
            return
        head = gpu_headroom.check(6000)
        if head.get("ok") is not True:
            _log.info("away-drain holding off: %s", head.get("reason"))
            return
        # The drain takes its own lease, but taking one while an image job
        # holds the card is the same collision by a different door.
        from agent_friday.services.residency_arbiter import exclusive_lease
        _held = exclusive_lease()
        if _held:
            _log.info("away-drain holding off: the %s job holds the GPU",
                      _held.get("kind") or "current")
            return
        from agent_friday.services.residency_arbiter import get_arbiter
        res = work_queue.drain("heavy", _drain_runner, arbiter=get_arbiter())
        _log.info("away-drain completed: %s", res)
        _announce_drain(res)
    except Exception as e:
        _log.warning("away-drain tick failed: %s", e)


def _drain_runner(item):
    from agent_friday.services import local_call
    return local_call.call("You are completing a queued background job.",
                           str(item.get("spec") or ""), "gemma4:26b",
                           max_tokens=4096)


def _announce_drain(res) -> None:
    """A drain that runs and tells nobody is the same defect as P4."""
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title="Queued work finished",
                body=str(res)[:300], proactive_chat=True,
                chat_message=f"I drained the queued heavy work while you were "
                             f"away. {str(res)[:300]}")
    except Exception as e:
        _log.warning("could not announce the drain: %s", e)


def _reclaim_expired_lease():
    """P6 — nothing called Arbiter.expire_if_due(), so a crashed lease holder
    stranded the GPU until a restart.

    The method existed and was correct; it simply had no caller. This 60-second
    tick is the natural home: it is already running, it is cheap, and a lease
    that outlives its holder is exactly the kind of failure nobody notices
    until chat has silently been on the sidekick for an hour.
    """
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if not getattr(arb, "lease", None):
            return
        res = arb.expire_if_due()
        if not isinstance(res, dict):
            return
        if res.get("ok") and "transition_s" in res:
            _log.warning("reclaimed an expired GPU lease after %.1fs — the "
                         "holder did not release it", res["transition_s"])
        elif not res.get("ok"):
            # Arbiter.release() returns ok=False and KEEPS the lease when
            # eviction fails, so a failed reclaim leaves the GPU exactly as
            # stranded as having no caller at all. Silence here would recreate
            # the bug this function fixes, one layer up.
            _log.error("GPU lease is EXPIRED but could not be reclaimed (%s) — "
                       "the card is still held and chat stays on the sidekick",
                       res.get("error"))
    except Exception as e:
        _log.debug("lease reclaim check skipped: %s", e)


def _loop():
    _log.info("Internal scheduler started.")
    _time.sleep(10)   # let the server finish coming up
    while True:
        try:
            _tick()
        except Exception as e:
            print(f"  [scheduler] tick failed: {e}")
        _time.sleep(60)


def start_scheduler():
    """Register built-ins, seed/reconcile the store, and start the tick loop.

    Replaces the old _register_default_daily_jobs() + _daily_scheduler_loop()
    pair at server boot. Inert under FRIDAY_TESTING (no daemon thread), but the
    store + dispatch stay callable so unit tests can drive them directly.
    """
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    _register_default_builtin_tasks()
    _seed_and_reconcile()
    _seed_default_agent_schedules()
    if core._TESTING:
        return
    threading.Thread(target=_loop, daemon=True).start()
