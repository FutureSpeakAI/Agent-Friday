"""
services/predictive_workspaces.py — Predictive workspace pre-warming.

Friday learns which workspaces you reach for and *when*. Every time a workspace
opens, the frontend POSTs the visit here; we append a timestamped event to
``~/.friday/usage_patterns.json`` and keep a per-day-of-week frequency model.

From that model we can answer "what is the user likely to want right now?" — a
ranked list that drives two things:
  • the dock's subtle "Suggested" glow (frontend reads /api/workspace/predictions)
  • boot / hourly *pre-warming*, where we proactively touch the caches a predicted
    workspace depends on (news cache, message cache, …) so it renders instantly.

Pure stdlib + core. Sits low in the service DAG (core only) so anything above it
can call predict_workspaces() / record_workspace_usage().
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


# ── Storage ───────────────────────────────────────────────────
USAGE_PATTERNS_FILE = FRIDAY_DIR / "usage_patterns.json"
_usage_lock = threading.Lock()
_MAX_USAGE_EVENTS = 4000          # rolling cap so the file never grows unbounded

# Workspaces that aren't "real" destinations to learn/predict (avoid noise).
_USAGE_IGNORE = {"", "home", "system"}


def _load_usage_patterns():
    try:
        data = json.loads(USAGE_PATTERNS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return data
    except Exception:
        pass
    return {"events": [], "updated": None}


def _save_usage_patterns(data):
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        USAGE_PATTERNS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  [predictive] usage save failed: {e}")


def record_workspace_usage(workspace, *, ts=None):
    """Append a workspace-open event. Cheap, thread-safe, best-effort.

    Stores day-of-week (0=Mon) and hour so the model can key on both. Returns the
    event dict (or None if ignored)."""
    workspace = (workspace or "").strip().lower()
    if workspace in _USAGE_IGNORE:
        return None
    now = datetime.fromtimestamp(ts) if ts else datetime.now()
    event = {
        "workspace": workspace,
        "ts": now.timestamp(),
        "dow": now.weekday(),
        "hour": now.hour,
    }
    with _usage_lock:
        data = _load_usage_patterns()
        events = data.get("events", [])
        events.append(event)
        if len(events) > _MAX_USAGE_EVENTS:
            events = events[-_MAX_USAGE_EVENTS:]
        data["events"] = events
        data["updated"] = now.isoformat()
        _save_usage_patterns(data)
    return event


def build_usage_model():
    """Collapse raw events into frequency tables.

    Returns:
      {
        "overall": {ws: count},
        "by_dow": {dow: {ws: count}},
        "by_dow_hour": {"dow:hourbucket": {ws: count}},  # 3-hour buckets
        "total": n,
      }
    """
    data = _load_usage_patterns()
    events = data.get("events", [])
    overall, by_dow, by_dow_hour = {}, {}, {}
    for e in events:
        ws = e.get("workspace")
        if not ws:
            continue
        dow = e.get("dow")
        hour = e.get("hour")
        overall[ws] = overall.get(ws, 0) + 1
        if dow is not None:
            d = by_dow.setdefault(dow, {})
            d[ws] = d.get(ws, 0) + 1
            if hour is not None:
                bucket = f"{dow}:{int(hour) // 3}"
                b = by_dow_hour.setdefault(bucket, {})
                b[ws] = b.get(ws, 0) + 1
    return {
        "overall": overall,
        "by_dow": by_dow,
        "by_dow_hour": by_dow_hour,
        "total": len(events),
    }


def predict_workspaces(dow=None, hour=None, top=6):
    """Rank workspaces by likelihood of being wanted at (dow, hour).

    Score blends three signals with decreasing weight: time-of-day-on-this-weekday
    (strongest), this-weekday-overall, and all-time-overall (weakest). Recency
    gives a small boost so a freshly-formed habit surfaces fast. Returns
    [{workspace, score (0..1), reason, count}]."""
    now = datetime.now()
    if dow is None:
        dow = now.weekday()
    if hour is None:
        hour = now.hour
    model = build_usage_model()
    total = model["total"]
    if not total:
        return []

    overall = model["overall"]
    dow_tbl = model["by_dow"].get(dow, {})
    bucket = f"{dow}:{int(hour) // 3}"
    hour_tbl = model["by_dow_hour"].get(bucket, {})

    overall_sum = sum(overall.values()) or 1
    dow_sum = sum(dow_tbl.values()) or 1
    hour_sum = sum(hour_tbl.values()) or 1

    # Recent (last 7d) opens get a small recency nudge.
    recent = {}
    cutoff = now.timestamp() - 7 * 86400
    for e in _load_usage_patterns().get("events", []):
        if e.get("ts", 0) >= cutoff and e.get("workspace"):
            recent[e["workspace"]] = recent.get(e["workspace"], 0) + 1
    recent_sum = sum(recent.values()) or 1

    scores = {}
    for ws in set(overall) | set(dow_tbl) | set(hour_tbl):
        s = (
            0.55 * (hour_tbl.get(ws, 0) / hour_sum)
            + 0.28 * (dow_tbl.get(ws, 0) / dow_sum)
            + 0.12 * (overall.get(ws, 0) / overall_sum)
            + 0.05 * (recent.get(ws, 0) / recent_sum)
        )
        if s > 0:
            scores[ws] = s

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top]
    out = []
    for ws, s in ranked:
        if hour_tbl.get(ws):
            reason = f"You often open {ws} around this time"
        elif dow_tbl.get(ws):
            reason = f"Frequent on {calendar.day_name[dow]}s"
        else:
            reason = "Part of your routine"
        out.append({
            "workspace": ws,
            "score": round(s, 4),
            "reason": reason,
            "count": overall.get(ws, 0),
        })
    return out


# ── Pre-warming ───────────────────────────────────────────────
# A workspace "warmer" is any zero-arg callable that touches the caches/files a
# workspace renders from. We resolve them lazily from the global namespace (the
# service DAG re-exports everything upward) so this module stays import-light and
# never hard-depends on a function that may move. Missing warmers are no-ops.
def _resolve_warmer(*names):
    g = globals()
    for n in names:
        fn = g.get(n)
        if callable(fn):
            return fn
    return None


def _warm_workspace(ws):
    """Best-effort cache warm for a single workspace id. Returns True if a real
    warmer ran."""
    ws = (ws or "").lower()
    try:
        if ws == "messages":
            fn = _resolve_warmer("_trigger_message_cache", "_collect_messages")
            if fn:
                fn()
                return True
        elif ws == "news":
            fn = _resolve_warmer("_load_front_page", "_news_archive_today",
                                  "_get_cached_news")
            if fn:
                fn()
                return True
        elif ws == "calendar":
            fn = _resolve_warmer("_collect_calendar_events", "_get_calendar_events")
            if fn:
                fn()
                return True
        elif ws == "wiki":
            fn = _resolve_warmer("_generate_wiki_indexes")
            if fn:
                fn()
                return True
        elif ws == "contacts" or ws == "trust":
            fn = _resolve_warmer("_load_trust_graph")
            if fn:
                fn()
                return True
    except Exception as e:
        print(f"  [predictive] warm {ws} failed: {e}")
    return False


def prewarm_predicted(top=3):
    """Pre-warm the top predicted workspaces for *now*. Safe to call from a
    background thread. Returns the list of workspace ids that were warmed."""
    preds = predict_workspaces(top=top)
    warmed = []
    for p in preds:
        ws = p["workspace"]
        if _warm_workspace(ws):
            warmed.append(ws)
    if warmed:
        print(f"  [predictive] pre-warmed: {', '.join(warmed)}")
    return warmed


def _prewarm_predicted_boot():
    """One-shot boot pre-warm (give the server a moment to finish starting)."""
    _time.sleep(12)
    try:
        prewarm_predicted()
    except Exception as e:
        print(f"  [predictive] boot pre-warm skipped: {e}")


def _predictive_prewarm_loop():
    """Re-warm on each hour boundary so the predicted set tracks time-of-day."""
    _time.sleep(20)
    last_hour = -1
    while True:
        try:
            h = datetime.now().hour
            if h != last_hour:
                last_hour = h
                prewarm_predicted()
        except Exception as e:
            print(f"  [predictive] hourly pre-warm error: {e}")
        _time.sleep(600)  # check every 10 min; acts only on hour change


