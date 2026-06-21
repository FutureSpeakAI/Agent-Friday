"""
services/ambient_awareness.py — rolling read of the user's working state.

Friday watches the rhythm of a session — how often tasks complete, how fast you
switch workspaces, how long you've been going — and distills it into four 0..1
signals:

  • energy_level     — capacity right now (declines over a long unbroken session,
                       recovers after a break, lifted by completed work)
  • focus_quality    — steadiness vs. context-thrashing (high when you stay put)
  • stress_indicator — overdue work, failed tasks, frantic switching
  • creative_flow    — sustained, low-switch time in maker workspaces

These drive *adaptive behavior*: shorter replies when energy is low, suppressed
interruptions during creative flow, and a subtle tint on the holographic scene.

Signals come from two places: explicit ``record_signal()`` calls (workspace
switches, task completions) and a passive read of existing state (the TASKS
registry, todos, usage patterns) so the picture is reasonable even before any
signal has been recorded this session.

Sits above the futurespeak chain (for TASKS / _load_todos) and predictive
workspaces (for usage events).
"""
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
import core
from core import (
    FRIDAY_DIR,
)  # noqa: E501
from services.misc_engine import (
    _load_todos,
)  # noqa: E501
from services.predictive_workspaces import (
    _load_usage_patterns,
)  # noqa: E501


AMBIENT_STATE_FILE = FRIDAY_DIR / "ambient_state.json"
_ambient_lock = threading.Lock()

# In-memory session bookkeeping. A "session" is a continuous stretch of activity;
# a gap longer than _SESSION_IDLE_GAP starts a fresh one (so energy recovers).
_SESSION_IDLE_GAP = 20 * 60      # 20 min of silence ends a session
_CREATIVE_WS = {"content", "draft", "studio", "code", "futurespeak", "news"}

_ambient_session = {
    "session_start": _time.time(),
    "last_activity": _time.time(),
    "switches": _deque(maxlen=60),       # (ts, workspace) of recent opens
    "task_events": _deque(maxlen=40),    # (ts, "complete"|"failed")
}


def _touch_session():
    """Update activity clocks; roll a new session after a long idle gap."""
    now = _time.time()
    if now - _ambient_session["last_activity"] > _SESSION_IDLE_GAP:
        _ambient_session["session_start"] = now
        _ambient_session["switches"].clear()
        _ambient_session["task_events"].clear()
    _ambient_session["last_activity"] = now


def record_signal(kind, **data):
    """Record an ambient signal. Recognised kinds:
        workspace_switch  (workspace=...)
        task_complete
        task_failed
    Unknown kinds just refresh activity. Thread-safe, best-effort."""
    with _ambient_lock:
        _touch_session()
        now = _time.time()
        if kind == "workspace_switch":
            ws = (data.get("workspace") or "").lower()
            if ws:
                _ambient_session["switches"].append((now, ws))
        elif kind == "task_complete":
            _ambient_session["task_events"].append((now, "complete"))
        elif kind == "task_failed":
            _ambient_session["task_events"].append((now, "failed"))


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _recent_task_stats():
    """Completion stats from in-session signals, backfilled from the TASKS
    registry so the read is sane even with no signals yet."""
    complete = failed = 0
    for _ts, kind in list(_ambient_session["task_events"]):
        if kind == "complete":
            complete += 1
        elif kind == "failed":
            failed += 1
    if complete + failed == 0:
        # Backfill from the live registry (best-effort; agent.py exports TASKS).
        try:
            for t in (globals().get("TASKS") or {}).values():
                st = t.get("status")
                if st in ("complete", "completed_unverified"):
                    complete += 1
                elif st in ("failed", "timeout"):
                    failed += 1
        except Exception:
            pass
    return complete, failed


def _switch_rate():
    """Workspace switches in the last 15 minutes (proxy for context-thrashing)."""
    cutoff = _time.time() - 15 * 60
    return sum(1 for ts, _ws in list(_ambient_session["switches"]) if ts >= cutoff)


def _recent_creative_share():
    """Fraction of the last 12 workspace opens that were in maker workspaces."""
    recent = [ws for _ts, ws in list(_ambient_session["switches"])][-12:]
    if not recent:
        # Backfill from persisted usage events (last 30 min).
        try:
            cutoff = _time.time() - 30 * 60
            recent = [e.get("workspace") for e in _load_usage_patterns().get("events", [])
                      if e.get("ts", 0) >= cutoff][-12:]
        except Exception:
            recent = []
    if not recent:
        return 0.0
    return sum(1 for ws in recent if ws in _CREATIVE_WS) / len(recent)


def _overdue_count():
    try:
        todos = _load_todos()
        n = 0
        for t in todos:
            if t.get("deadline") and t.get("status") in ("approved", "proposed"):
                try:
                    if date.fromisoformat(t["deadline"]) < date.today():
                        n += 1
                except Exception:
                    pass
        return n
    except Exception:
        return 0


def get_ambient_state():
    """Compute the live ambient state. Returns a JSON-friendly dict with the four
    signals (0..1), a coarse label, behavior hints, and the contributing inputs."""
    with _ambient_lock:
        now = _time.time()
        session_minutes = max(0.0, (now - _ambient_session["session_start"]) / 60.0)
        idle_minutes = max(0.0, (now - _ambient_session["last_activity"]) / 60.0)

    complete, failed = _recent_task_stats()
    total_tasks = complete + failed
    completion_rate = (complete / total_tasks) if total_tasks else 0.7  # neutral-ish prior
    switches = _switch_rate()
    creative_share = _recent_creative_share()
    overdue = _overdue_count()

    # ── Energy: starts high, decays over a long unbroken session, lifted by
    # completed work, dinged by failures. Recovers as idle time accrues.
    fatigue = _clamp01(session_minutes / 180.0)        # ~3h continuous → fully fatigued
    recovery = _clamp01(idle_minutes / 30.0) * 0.4
    energy = 0.85 - 0.5 * fatigue + recovery + 0.10 * min(complete, 3) - 0.08 * min(failed, 3)
    energy = _clamp01(energy)

    # ── Focus: high when you stay put, erodes with rapid switching.
    focus = 0.85 - 0.11 * switches
    if creative_share > 0.5:
        focus += 0.08
    focus = _clamp01(focus)

    # ── Stress: overdue work + failures + frantic switching.
    stress = 0.10 + 0.12 * min(overdue, 4) + 0.10 * min(failed, 4) + 0.06 * max(0, switches - 3)
    stress = _clamp01(stress)

    # ── Creative flow: sustained, low-switch time in maker workspaces.
    flow = creative_share * (0.6 + 0.4 * focus)
    if switches > 5:
        flow *= 0.6
    flow = _clamp01(flow)

    # Coarse label for quick reads.
    if flow >= 0.6:
        label = "creative_flow"
    elif stress >= 0.6:
        label = "stressed"
    elif energy <= 0.35:
        label = "low_energy"
    elif focus >= 0.7:
        label = "focused"
    else:
        label = "steady"

    state = {
        "energy_level": round(energy, 3),
        "focus_quality": round(focus, 3),
        "stress_indicator": round(stress, 3),
        "creative_flow": round(flow, 3),
        "label": label,
        "inputs": {
            "session_minutes": round(session_minutes, 1),
            "idle_minutes": round(idle_minutes, 1),
            "task_completion_rate": round(completion_rate, 2),
            "tasks_complete": complete,
            "tasks_failed": failed,
            "switch_rate_15m": switches,
            "creative_share": round(creative_share, 2),
            "overdue_todos": overdue,
        },
        "hints": ambient_behavior_hints_for(energy, focus, stress, flow, label),
        "scene_mood": ambient_scene_mood_for(label),
        "updated": datetime.now().isoformat(),
    }
    _persist_ambient_state(state)
    return state


def ambient_behavior_hints_for(energy, focus, stress, flow, label):
    """Map signals → concrete behavior directives Friday's prompt/UX can honor."""
    response_length = "normal"
    if energy <= 0.35 or stress >= 0.6:
        response_length = "short"
    elif focus >= 0.7 and stress < 0.4:
        response_length = "thorough"

    return {
        "response_length": response_length,
        "suppress_interruptions": bool(flow >= 0.6 or focus >= 0.8),
        "defer_nonurgent_notifications": bool(flow >= 0.6 or stress >= 0.65),
        "tone": ("gentle" if stress >= 0.6 else
                 "calm" if energy <= 0.35 else
                 "crisp" if focus >= 0.7 else "warm"),
        "offer_breaks": bool(energy <= 0.3),
    }


def ambient_scene_mood_for(label):
    """Map the coarse ambient label to a *subtle* holographic system mood. These
    are the low-key moods (no high-energy EXCITED/EXECUTING) so the scene only
    breathes with state rather than flashing."""
    return {
        "creative_flow": "CREATIVE",
        "focused": "FOCUSED",
        "stressed": "PROTECTIVE",
        "low_energy": "CALM",
        "steady": "REFLECTIVE",
    }.get(label, "REFLECTIVE")


def ambient_prompt_directive():
    """A short paragraph to splice into a system prompt so the model adapts its
    register to the user's current state. Returns '' if state is unremarkable."""
    try:
        st = get_ambient_state()
    except Exception:
        return ""
    h = st.get("hints", {})
    bits = []
    if h.get("response_length") == "short":
        bits.append("Keep replies short and low-effort to read")
    elif h.get("response_length") == "thorough":
        bits.append("The user is focused — depth is welcome")
    if h.get("offer_breaks"):
        bits.append("energy is low, so be encouraging and don't pile on")
    if h.get("suppress_interruptions"):
        bits.append("the user is in flow — don't interrupt with tangents")
    if h.get("tone"):
        bits.append(f"tone: {h['tone']}")
    if not bits:
        return ""
    return "== AMBIENT STATE ==\n" + "; ".join(bits) + ".\n"


def _persist_ambient_state(state):
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        AMBIENT_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


