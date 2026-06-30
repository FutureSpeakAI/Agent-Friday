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
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps

_log = logging.getLogger("friday.notifications")
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    FRIDAY_DIR,
    HOME,
    WIKI_DIR,
    _load_settings,
    _offline_queue_list,
    _offline_queue_remove,
    _ollama_available,
    _set_network_state,
)  # noqa: E501
from agent_friday.services.calendar_engine import (
    _collect_messages,
    _google_credentials,
)  # noqa: E501
from agent_friday.services.creations import (
    generate_daily_creation,
)  # noqa: E501
from agent_friday.services.misc_engine import (
    _load_todos,
)  # noqa: E501
from agent_friday.services.model_router import (
    _generate_text,
    _run_session_summary_job,
)  # noqa: E501
from agent_friday.services.news_engine import (
    FRONT_PAGE_SLOTS,
    WEEKLY_DIGEST_HOUR,
    WEEKLY_EDITORIAL_HOUR,
    _fetch_news_items,
    _front_page_central_now,
    _run_front_page_job,
    _run_weekly_digest_job,
    _run_weekly_editorial_job,
)  # noqa: E501
from agent_friday.services.introspection import (
    generate_self_improvement_report,
)  # noqa: E501
from agent_friday.services.voice_engine import (
    _notif_engine,
)  # noqa: E501



def _compute_derived_notifications():
    """One-off computed notifications (briefings, todos) — not queued, just merged."""
    derived = []
    # Daily briefing ready?
    meta_dir = os.path.join(WIKI_DIR, 'meta')
    if os.path.isdir(meta_dir):
        briefings = sorted(glob.glob(os.path.join(meta_dir, 'daily-briefing-*.md')), reverse=True)
        if briefings:
            latest = os.path.basename(briefings[0])
            date_str = latest.replace('daily-briefing-', '').replace('.md', '')
            derived.append({
                "id": f"derived-briefing-{date_str}",
                "kind": "briefing",
                "title": f"📰 Daily briefing ready: {date_str}",
                "body": "",
                "priority": "low",
                "read": False, "dismissed": False,
                "source": "briefing",
                "created_at": date_str,
                "target": {"workspace": "news", "tab": "briefings"},
                "derived": True,
            })

    # Proposed todos awaiting approval
    todos = _load_todos()
    proposed = [t for t in todos if t.get('status') == 'proposed']
    if proposed:
        derived.append({
            "id": "derived-proposed-todos",
            "kind": "todo",
            "title": f"📋 {len(proposed)} proposed task{'s' if len(proposed) > 1 else ''} awaiting approval",
            "body": "",
            "priority": "medium",
            "read": False, "dismissed": False,
            "source": "tasks",
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "target": {"workspace": "home"},
            "derived": True,
        })

    # Overdue todos
    overdue_count = 0
    for t in todos:
        if t.get('deadline') and t.get('status') in ('approved', 'proposed'):
            try:
                if date.fromisoformat(t['deadline']) < date.today():
                    overdue_count += 1
            except Exception:
                pass
    if overdue_count:
        derived.append({
            "id": "derived-overdue-todos",
            "kind": "overdue",
            "title": f"⚠️ {overdue_count} overdue task{'s' if overdue_count > 1 else ''}",
            "body": "",
            "priority": "high",
            "read": False, "dismissed": False,
            "source": "tasks",
            "created_at": datetime.now().strftime('%Y-%m-%d'),
            "target": {"workspace": "home"},
            "derived": True,
        })
    return derived


# ═══════════════════════════════════════════════════════════════
#  NOTIFICATION TRIGGER LOOP
# ═══════════════════════════════════════════════════════════════

def _trigger_skill_promotions():
    """Watch SkillOpt storage for newly-promoted best_skill.md artifacts."""
    if not _notif_engine:
        return
    skills_dir = FRIDAY_DIR / "skillopt"
    if not skills_dir.exists():
        skills_dir = HOME / ".friday" / "skillopt"
    if not skills_dir.exists():
        return
    state = _notif_engine.get_trigger_state("skill_best_mtimes", {}) or {}
    changed = False
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        best = skill_dir / "best_skill.md"
        if not best.exists():
            continue
        try:
            mtime = best.stat().st_mtime
        except OSError:
            continue
        prior = state.get(skill_dir.name)
        if prior is None:
            # First sight — record, don't notify
            state[skill_dir.name] = mtime
            changed = True
            continue
        if mtime > prior + 1.0:
            state[skill_dir.name] = mtime
            changed = True
            _notif_engine.push(
                title=f"🧠 Skill improved — {skill_dir.name}",
                body=f"SkillOpt promoted a new best version of `{skill_dir.name}`.",
                priority="low",
                source="skillopt",
                kind="skill_improvement",
                meta={"skill_name": skill_dir.name, "mtime": mtime},
                actions=[
                    {"label": "Open in Observatory", "kind": "open_observatory",
                     "payload": {"skill": skill_dir.name}},
                ],
                target={"workspace": "studio"},
                dedupe_key=f"skill_promoted:{skill_dir.name}:{int(mtime)}",
            )
    if changed:
        _notif_engine.set_trigger_state("skill_best_mtimes", state)


KEY_CONTACTS = {
}


def _trigger_gmail_signals():
    """Watch a Gmail-export JSON for unanswered key-contact emails and job replies."""
    if not _notif_engine:
        return
    candidates = [
        FRIDAY_DIR / "gmail" / "inbox.json",
        FRIDAY_DIR / "gmail-cache.json",
        WIKI_DIR / "professional" / "applications" / "responses.json",
    ]
    inbox_path = next((p for p in candidates if p.exists()), None)
    if not inbox_path:
        return
    try:
        data = json.loads(inbox_path.read_text(encoding="utf-8"))
    except Exception:
        return
    messages = data if isinstance(data, list) else data.get("messages", [])
    if not isinstance(messages, list):
        return

    now = datetime.utcnow()
    for m in messages[-100:]:
        if not isinstance(m, dict):
            continue
        sender = (m.get("from") or m.get("sender") or "").lower()
        subj = m.get("subject") or ""
        ts_raw = m.get("received_at") or m.get("date") or m.get("timestamp")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", ""))
        except Exception:
            ts = None
        msg_id = str(m.get("id") or m.get("message_id") or (sender + subj))[:120]

        # Job-application response
        if (m.get("kind") == "job_response"
                or "applied" in subj.lower()
                or "application" in subj.lower()
                or m.get("category") == "applications"):
            _notif_engine.push(
                title=f"💼 Job reply — {m.get('company') or sender}",
                body=f"**{subj}**\n\n{(m.get('preview') or '')[:300]}",
                priority="high",
                source="gmail",
                kind="job_response",
                proactive_chat=True,
                chat_message=(
                    f"You have a job-application reply from "
                    f"**{m.get('company') or sender}** about *{subj}*. "
                    f"Want me to open it and draft a follow-up?"
                ),
                meta={"sender": sender, "subject": subj, "message_id": msg_id},
                target={"workspace": "messages", "lane": "career",
                        "thread_id": m.get("thread_id") or msg_id},
                dedupe_key=f"job_reply:{msg_id}",
            )
            continue

        # Stale message from a key contact
        contact = next((v for k, v in KEY_CONTACTS.items() if k in sender), None)
        if contact and ts:
            age_h = (now - ts).total_seconds() / 3600.0
            if age_h >= contact["stale_hours"] and not m.get("replied"):
                _notif_engine.push(
                    title=f"⏳ Unreplied — {contact['label']}",
                    body=(f"**{subj}**\n\n"
                          f"Sent {age_h:.0f} hours ago, no reply yet.\n\n"
                          f"{(m.get('preview') or '')[:300]}"),
                    priority=contact["priority"],
                    source="gmail",
                    kind="stale_email",
                    proactive_chat=True,
                    chat_message=(
                        f"Hey — {contact['label']} sent you an email "
                        f"{age_h:.0f} hours ago about *{subj}*. You haven't "
                        f"replied yet. Want me to draft a response?"
                    ),
                    meta={"sender": sender, "subject": subj, "age_hours": age_h},
                    target={"workspace": "messages",
                            "lane": contact.get("lane", "all"),
                            "thread_id": m.get("thread_id") or msg_id},
                    dedupe_key=f"stale:{msg_id}",
                )


# ═══════════════════════════════════════════════════════════════
#  DAILY SCHEDULER
#  A single background thread that fires registered jobs once per day
#  at a target wall-clock hour (America/Chicago). "Run if past the
#  target and not yet run today" means a job auto-catches-up whenever
#  the server starts up after its time — so it runs whenever the
#  server is up, not only if the process happened to be alive at the
#  exact minute. The Front Page news task can register here too.
# ═══════════════════════════════════════════════════════════════

DAILY_CREATION_HOUR = 8     # 8 AM Central (prior Cowork routine ran ~2 PM)
DAILY_CREATION_MINUTE = 0

_DAILY_JOBS = []            # list of {name, hour, minute, fn}
_DAILY_STATE_FILE = FRIDAY_DIR / "daily_scheduler_state.json"
_daily_state_lock = threading.Lock()


def register_daily_job(name, hour, minute, fn):
    """Register a function to run once per day at hour:minute Central.

    Back-compat shim: the daily-only loop was promoted to the generalized
    scheduler in services/scheduler.py. This now delegates to
    ``register_builtin_task(..., default_trigger="daily")`` so anything still
    calling the legacy API lands in the new scheduler. The old in-process
    _DAILY_JOBS list + _daily_scheduler_loop are retained below for reference but
    are no longer started at boot.
    """
    try:
        from agent_friday.services import scheduler as _sched
        _sched.register_daily_job(name, hour, minute, fn)
    except Exception as e:
        print(f"  [daily-scheduler] shim delegate failed ({e}); using legacy list")
        _DAILY_JOBS.append({"name": name, "hour": int(hour),
                            "minute": int(minute), "fn": fn})


def _daily_state_read():
    try:
        return json.loads(_DAILY_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _daily_state_mark(name, date_str):
    with _daily_state_lock:
        state = _daily_state_read()
        state[name] = date_str
        try:
            _DAILY_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"  [daily-scheduler] state write failed: {e}")


def _daily_scheduler_loop():
    """Tick every minute; run any job whose Central time has passed today and
    that hasn't already run today. Each job runs in its own thread so a slow
    job (e.g. a Claude call) never delays the others."""
    if not _DAILY_JOBS:
        return
    try:
        from zoneinfo import ZoneInfo
        _tz = ZoneInfo("America/Chicago")
    except Exception:
        _tz = None
    names = ", ".join(j["name"] for j in _DAILY_JOBS)
    _log.info("Daily scheduler started (%s).", names)
    _time.sleep(10)  # let the server finish coming up
    while True:
        now = datetime.now(_tz) if _tz else datetime.now()
        today = now.strftime("%Y-%m-%d")
        state = _daily_state_read()
        for job in _DAILY_JOBS:
            try:
                if state.get(job["name"]) == today:
                    continue
                due = (now.hour, now.minute) >= (job["hour"], job["minute"])
                if not due:
                    continue
                # Mark BEFORE running so a long job can't double-fire on the
                # next tick, and so a crash mid-job doesn't retry all day.
                _daily_state_mark(job["name"], today)
                fn = job["fn"]
                threading.Thread(
                    target=lambda f=fn, n=job["name"]: _run_daily_job(f, n),
                    daemon=True,
                ).start()
            except Exception as e:
                print(f"  [daily-scheduler:{job['name']}] {e}")
        _time.sleep(60)


def _run_daily_job(fn, name):
    try:
        fn()
    except Exception as e:
        print(f"  [daily-scheduler:{name}] run failed: {e}")


def _skillopt_llm_researcher(skill_name, context):
    """LLM-backed hypothesis generator for the SkillOpt auto-research loop.

    Builds a compact regression report from the research context, asks the
    configured provider for diagnosis + concrete SKILL.md edits as JSON, and
    validates the shape. Unparseable or empty output degrades to the engine's
    built-in heuristics so a finding is still recorded. Lives in the services
    layer because skillopt_engine is a leaf module that must not import upward
    to reach the model router.
    """
    from agent_friday.services.model_router import _generate_text
    import agent_friday.skillopt_engine as _sopt

    recent = (context.get("recent_executions") or [])[:8]
    exec_lines = []
    for r in recent:
        exec_lines.append(
            f"- composite={r.get('composite_score', '?')} "
            f"duration_ms={r.get('duration_ms', '?')} "
            f"error={r.get('error') or 'none'}"
        )
    content = (context.get("current_content") or "")[:4000]
    prompt = (
        "You are improving an agent skill definition (a SKILL.md file).\n"
        f"Skill: {skill_name}\n"
        f"Best composite score: {context.get('best_score')}\n"
        f"Rolling mean over the last 10 runs: {context.get('rolling_mean')}\n"
        "Recent executions:\n" + "\n".join(exec_lines) +
        "\n\nCurrent SKILL.md:\n---\n" + content + "\n---\n\n"
        "Diagnose why performance regressed and propose concrete edits.\n"
        'Reply with ONLY a JSON object of the form {"hypotheses": ["..."], '
        '"edits": [{"op": "append", "summary": "...", "content": "..."}]} — '
        'op may be "append", "replace" (both use "content") or "patch" '
        '(uses "from" and "to"). At most 3 edits.'
    )
    raw = _generate_text(prompt) or ""
    hypotheses, edits = [], []
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        hypotheses = [str(h).strip() for h in (data.get("hypotheses") or [])
                      if str(h).strip()][:5]
        edits = [e for e in (data.get("edits") or [])
                 if isinstance(e, dict)
                 and e.get("op") in ("append", "patch", "replace")][:3]
    except Exception:
        pass
    if not hypotheses and not edits:
        return _sopt.AutoResearchLoop._heuristic_research(skill_name, context)
    return {"hypotheses": hypotheses, "edits": edits}


def _skillopt_nightly():
    """Nightly closed-loop tick: run SkillOpt auto-research over drifted skills."""
    try:
        import agent_friday.skillopt_engine as _sopt
        _sopt.set_researcher(_skillopt_llm_researcher)
    except Exception as e:
        print(f"  [skillopt] researcher wiring failed: {e}")
    try:
        import agent_friday.skill_capture as _skcap
        result = _skcap.run_nightly()
        print(f"  [skillopt] nightly research: {result}")
    except Exception as e:
        print(f"  [skillopt] nightly failed: {e}")


# ── Weekly self-improvement ─────────────────────────────────────────────────
# Friday reviews her own recent behaviour once a week: epistemic calibration +
# sycophancy, derives focus areas, and writes a private first-person reflection.
# The whole loop lives inside Friday — no external orchestrator needed.
SELF_IMPROVEMENT_HOUR = 9          # 9 AM Central, configurable via settings
SELF_IMPROVEMENT_WEEKDAY = 6       # Sunday (Mon=0 … Sun=6)


def _self_reflect(prompt, system=None):
    """Best-effort first-person reflection via the user's configured provider.

    Uses _generate_text (router-aware) so it works on Ollama/OpenAI/Anthropic
    and never hard-fails keyless-local. Returns '' on any failure so the report
    still generates with its heuristic content.
    """
    try:
        return _generate_text(prompt, system=system, max_tokens=600,
                              orb_label="🪞 Self-Reflection", workspace="system")
    except Exception as e:
        print(f"  [self-improvement] reflection generation failed: {e}")
        return ""


def _notify_self_improvement(report, manual=False):
    """Push the 'weekly self-improvement note ready' notification."""
    if not (_notif_engine and report):
        return
    try:
        focus = report.get("focus_areas") or []
        if focus:
            body = "Focus this week: " + ", ".join(f.get("area", "") for f in focus[:3])
        else:
            body = "Metrics are all within healthy bands — maintain it."
        dk = f"self-improvement:{report.get('week_id')}"
        if manual:
            dk += f":manual:{datetime.now().strftime('%H%M%S')}"
        _notif_engine.push(
            title="🪞 Friday's weekly self-improvement note is ready",
            body=body,
            priority="low",
            source="self-improvement",
            kind="self_improvement",
            actions=[{"label": "View Note", "workspace": "system", "tab": "self-improvement"}],
            target={"workspace": "system", "tab": "self-improvement"},
            dedupe_key=dk,
            meta={"week": report.get("week_id"), "manual": manual,
                  "analyzed": report.get("responses_analyzed", 0)},
        )
    except Exception as e:
        print(f"  [self-improvement] notification failed: {e}")


def _run_self_improvement_job():
    """Scheduled entry point: only runs on Sundays. Generates the weekly
    self-improvement report (with reflection) and pushes a notification.

    Returns the report dict, or None on a non-matching weekday / failure."""
    try:
        cnow = _front_page_central_now()
        if cnow.weekday() != SELF_IMPROVEMENT_WEEKDAY:
            return None
        report = generate_self_improvement_report(limit=30, reflect=_self_reflect,
                                                  when=cnow.replace(tzinfo=None))
        _notify_self_improvement(report)
        print(f"  [self-improvement] weekly note written for {report.get('week_id')} "
              f"({report.get('responses_analyzed', 0)} responses)")
        return report
    except Exception as e:
        print(f"  [self-improvement] weekly job failed: {e}")
        return None


# Register the daily creation. The hour is configurable via settings.
def _register_default_daily_jobs():
    try:
        settings = _load_settings()
        hour = int(settings.get("daily_creation_hour", DAILY_CREATION_HOUR))
        minute = int(settings.get("daily_creation_minute", DAILY_CREATION_MINUTE))
    except Exception:
        hour, minute = DAILY_CREATION_HOUR, DAILY_CREATION_MINUTE
    register_daily_job("daily-creation", hour, minute, generate_daily_creation)
    # Friday's Front Page — two editions a day at 7 AM and 6 PM Central.
    register_daily_job("front-page-morning", FRONT_PAGE_SLOTS["morning"], 0,
                       lambda: _run_front_page_job("morning"))
    register_daily_job("front-page-evening", FRONT_PAGE_SLOTS["evening"], 0,
                       lambda: _run_front_page_job("evening"))
    # Friday's Weekly Digest — Sundays at 8 AM Central (the job itself
    # no-ops on other weekdays).
    register_daily_job("friday-weekly-digest", WEEKLY_DIGEST_HOUR, 0,
                       _run_weekly_digest_job)
    # Friday's Weekly Editorial — Fridays at 7 PM Central (Friday's
    # independent opinion piece; the job no-ops on other weekdays).
    register_daily_job("friday-weekly-editorial", WEEKLY_EDITORIAL_HOUR, 0,
                       _run_weekly_editorial_job)
    # Friday's Weekly Self-Improvement — Sundays (the job no-ops on other
    # weekdays). She scores her own epistemics + sycophancy and reflects.
    try:
        si_hour = int(_load_settings().get("self_improvement_hour", SELF_IMPROVEMENT_HOUR))
    except Exception:
        si_hour = SELF_IMPROVEMENT_HOUR
    register_daily_job("friday-self-improvement", si_hour, 0,
                       _run_self_improvement_job)
    # End-of-day session summary — distill the day's conversation into a
    # continuity note at 11:30 PM Central. The job backfills the last few days,
    # so a server that was off overnight still catches up on its next start.
    register_daily_job("session-summary", 23, 30, _run_session_summary_job)


def _trigger_message_cache():
    """Keep the Comms Center cache warm: pull + classify live Gmail so the
    Messages workspace and its badge work even before the UI is opened. No-ops
    quietly when Google isn't linked (the cache then just isn't refreshed)."""
    if _google_credentials() is None:
        return
    try:
        _collect_messages(limit=40)  # side effect: refreshes cache.json
    except Exception as e:
        print(f"  [message-cache] {e}")


def _notification_trigger_loop():
    """Single background tick that runs all triggers safely."""
    if not _notif_engine:
        return
    triggers = [
        ("skill_promotions", _trigger_skill_promotions),
        ("gmail", _trigger_gmail_signals),
        ("message_cache", _trigger_message_cache),
    ]
    _log.info("Notification trigger loop started.")
    # Wait a bit so the server can finish coming up
    _time.sleep(8)
    while True:
        for name, fn in triggers:
            try:
                fn()
            except Exception as e:
                print(f"  [notif-trigger:{name}] {e}")
        _time.sleep(60)  # poll every minute


# ═══════════════════════════════════════════════════════════════
#  NETWORK MONITOR  (offline-first resilience)
# ═══════════════════════════════════════════════════════════════
# A single background thread probes connectivity every 30s and updates
# core.NETWORK_STATE (read by GET /api/system/network-status). On the
# offline→online edge it flushes the offline queue, re-warms the news feed, and
# pushes a notification; on the →offline edge it pushes a heads-up that Friday
# has switched to local inference. The _load_settings offline overlay does the
# actual provider switch — this loop just drives the state + side effects.

NETWORK_PROBE_HOSTS = [("dns.google", 443), ("8.8.8.8", 443), ("1.1.1.1", 443)]
NETWORK_PROBE_INTERVAL = 30        # seconds between probes
NETWORK_PROBE_TIMEOUT = 3.0        # per-host connect timeout


def _network_probe():
    """Try a fast TCP connect to a reliable host. Returns (ok, latency_ms, host)."""
    import socket
    for host, port in NETWORK_PROBE_HOSTS:
        t0 = _time.time()
        try:
            with socket.create_connection((host, port), timeout=NETWORK_PROBE_TIMEOUT):
                return True, round((_time.time() - t0) * 1000, 1), host
        except Exception:
            continue
    return False, None, NETWORK_PROBE_HOSTS[0][0]


def _flush_offline_queue(reason="reconnect"):
    """Replay every queued cloud task. Returns {flushed, failed, kept}.

    Each entry is dispatched by `kind`; on success it is removed, on a handled
    failure it is left queued for the next attempt. Unknown kinds are dropped
    (logged) so a typo can't wedge the queue forever.
    """
    entries = _offline_queue_list()
    if not entries:
        return {"flushed": 0, "failed": 0, "kept": 0}
    flushed = failed = 0
    print(f"  [offline-queue] flushing {len(entries)} task(s) ({reason})")
    for entry in entries:
        kind = (entry.get("kind") or "").strip()
        payload = entry.get("payload") or {}
        try:
            handled = _offline_dispatch(kind, payload)
            if handled:
                _offline_queue_remove(entry.get("id"))
                flushed += 1
            else:
                # Unknown kind — drop it rather than retry forever.
                print(f"  [offline-queue] dropping unknown kind {kind!r}")
                _offline_queue_remove(entry.get("id"))
        except Exception as e:
            failed += 1
            print(f"  [offline-queue:{kind}] replay failed (kept): {e}")
    kept = len(_offline_queue_list())
    if flushed and _notif_engine:
        try:
            _notif_engine.push(
                title="Back online — queued work flushed",
                body=f"Replayed {flushed} task(s) that were waiting for connectivity.",
                priority="low", source="network", kind="info",
                dedupe_key=f"offline-flush:{date.today().isoformat()}",
            )
        except Exception:
            pass
    return {"flushed": flushed, "failed": failed, "kept": kept}


def _offline_dispatch(kind, payload):
    """Run one queued task by kind. Returns True if the kind was recognized."""
    if kind == "front_page":
        _run_front_page_job(payload.get("slot") or "morning")
        return True
    if kind == "weekly_digest":
        _run_weekly_digest_job()
        return True
    if kind == "weekly_editorial":
        _run_weekly_editorial_job()
        return True
    if kind == "daily_creation":
        generate_daily_creation()
        return True
    if kind == "notify":
        if _notif_engine:
            _notif_engine.push(
                title=payload.get("title") or "Queued reminder",
                body=payload.get("body") or "",
                priority=payload.get("priority", "medium"),
                source="offline-queue", kind="info",
            )
        return True
    return False


def _on_network_transition(old, new, host=None):
    """Fire side effects on a connectivity state change (called once per edge)."""
    came_online = new == "online" and old in ("offline", "degraded", "unknown")
    went_offline = new == "offline" and old != "offline"

    if went_offline:
        print("  [network] OFFLINE — switching to local inference (Ollama).")
        if _notif_engine:
            try:
                local_ok = _ollama_available()
                _notif_engine.push(
                    title="You're offline",
                    body=("Friday switched to local models — chat, news, and voice "
                          "keep working on-device." if local_ok else
                          "No connection. Install/run Ollama for full offline operation; "
                          "cloud tasks will be queued until you're back online."),
                    priority="medium", source="network", kind="warning",
                    dedupe_key="network-offline",
                )
            except Exception:
                pass

    if came_online:
        print("  [network] ONLINE — flushing queue and refreshing feeds.")
        # 1. Flush queued cloud tasks.
        try:
            _flush_offline_queue(reason="reconnect")
        except Exception as e:
            print(f"  [network] queue flush failed: {e}")
        # 2. Re-warm the live news feed so the next render is fresh, not cached.
        try:
            _fetch_news_items(limit_per=4)
        except Exception:
            pass
        # 3. Tell the user we're back.
        if _notif_engine:
            try:
                _notif_engine.push(
                    title="Back online",
                    body="Connection restored. Friday is back on cloud models and feeds are refreshing.",
                    priority="low", source="network", kind="info",
                    dedupe_key="network-online",
                )
            except Exception:
                pass


def _network_monitor_loop():
    """Probe connectivity every 30s and drive core.NETWORK_STATE + side effects."""
    _log.info("Network monitor started (probe every %ds).", NETWORK_PROBE_INTERVAL)
    _time.sleep(5)  # let the server finish coming up before the first probe
    while True:
        try:
            ok, latency, host = _network_probe()
            if ok:
                old, new = _set_network_state(
                    "online", latency_ms=latency, host=host,
                    consecutive_failures=0)
            else:
                with core._NETWORK_LOCK:
                    fails = core.NETWORK_STATE.get("consecutive_failures", 0) + 1
                # One miss is a blip (degraded); two+ misses is offline.
                status = "degraded" if fails < 2 else "offline"
                old, new = _set_network_state(
                    status, latency_ms=None, host=host,
                    consecutive_failures=fails)
            if new != old:
                try:
                    _on_network_transition(old, new, host=host)
                except Exception as e:
                    print(f"  [network] transition handler failed: {e}")
        except Exception as e:
            print(f"  [network-monitor] {e}")
        _time.sleep(NETWORK_PROBE_INTERVAL)


