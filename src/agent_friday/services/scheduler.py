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
import threading
import time as _time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR, _load_settings, process_register, process_update

# ── Storage ──────────────────────────────────────────────────────────────────
SCHEDULES_FILE = FRIDAY_DIR / "schedules.json"
RUNS_FILE = FRIDAY_DIR / "schedule_runs.jsonl"
RUNS_KEEP = 500                       # cap the run-history file to the last N runs

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
                          weekday_only=None):
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
            # Rotate: keep only the last RUNS_KEEP lines.
            lines = RUNS_FILE.read_text(encoding="utf-8").splitlines()
            if len(lines) > RUNS_KEEP:
                RUNS_FILE.write_text("\n".join(lines[-RUNS_KEEP:]) + "\n",
                                     encoding="utf-8")
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
            _ne.push(title=f"⚠️ Scheduled task failed: {rec.get('name')}",
                     body=(summary or "")[:300], priority="high", source="scheduler",
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
    tid = _spawn_task(rec.get("name") or "Scheduled task", prompt,
                      description=f"scheduled:{rec.get('id')}")
    # Associate the spawned task with this schedule for cost attribution (Part D).
    try:
        from services import cost_meter as _cm
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
    _patch_record(sid, last_run_ts=started, last_run_date=now.strftime("%Y-%m-%d"))

    orb_id = f"sched-{sid}-{run_id[-6:]}"
    try:
        process_register(orb_id, name="Scheduler",
                         label=f"⏰ {rec.get('name')}", category="monitoring",
                         icon="⏰", model=None)
    except Exception:
        orb_id = None

    def _body():
        status, summary, err = "complete", "", None
        rec_live = dict(rec, _active_run_id=run_id)
        try:
            result = _run_task(rec_live)
            summary = _summarize(result)
            # on_change: suppress the notification when nothing changed.
            do_notify = True
            if rec.get("notify") == "on_change" and not _changed(result):
                do_notify = False
            _patch_record(sid, last_status="complete", last_summary=summary,
                          retry_pending=False, retry_count=0, not_before=0)
            if do_notify:
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
                                   label=f"⏰ {rec.get('name')} — {status}")
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
                "enabled": True,
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


def _afternoon_briefing_job():
    """Synthesize the afternoon briefing markdown and persist it (so the
    'briefing ready' notification + News panel pick it up). Mirrors the on-demand
    /api/news/briefing/generate path but runs unattended."""
    from agent_friday.services.news_engine import _gather_live_briefing_context, _notify_briefing
    from agent_friday.services.model_router import _generate_text, _get_friday_system_prompt
    live_context = _gather_live_briefing_context()
    prompt = (
        "Generate a crisp afternoon briefing using the LIVE DATA below plus what "
        "you know about me. Cover, in order: remaining calendar for today, the "
        "day's most relevant news, open tasks needing attention, and one "
        "proactive insight. Clean markdown, lead with the most urgent item.\n\n"
        f"{live_context}"
    )
    system = _get_friday_system_prompt(keywords=prompt, workspace="briefing")
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
        "notify": "on_change",
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
        if added:
            _write_store(recs)
            print(f"  [scheduler] seeded {added} default agent schedule(s).")


# ── The tick loop ────────────────────────────────────────────────────────────
_STARTED = False


def _tick():
    now = _now_central()
    with _STORE_LOCK:
        recs = [dict(r) for r in _read_store()]
    for rec in recs:
        try:
            if _is_due(rec, now):
                dispatch(rec)
        except Exception as e:
            print(f"  [scheduler:{rec.get('id')}] tick error: {e}")


def _loop():
    print("  [FRIDAY] Internal scheduler started.")
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
