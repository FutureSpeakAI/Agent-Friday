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
import core
from core import (
    FRIDAY_DIR,
)  # noqa: E501
from services.agent import (
    _resolve_workspace,
)  # noqa: E501
from services.calendar_engine import (
    _collect_messages,
    _fetch_calendar_today,
    _google_section_error,
)  # noqa: E501
from services.model_router import (
    _get_career_context,
    _load_vault_summary,
)  # noqa: E501
from services.news_engine import (
    _fetch_news_items,
    _gather_live_briefing_context,
)  # noqa: E501

# ═══════════════════════════════════════════════════════════════
#  VOICE EVERYWHERE — per-workspace voice context + "Start my day"
# ═══════════════════════════════════════════════════════════════
# Every workspace gets a /api/<workspace>/voice-context endpoint that assembles
# the live, time-sensitive data that workspace is about (calendar, email, news,
# tasks, pipeline) into a single spoken-ready opening turn. The frontend queues
# that turn the moment the Live voice socket opens, so a voice session started
# from a workspace begins already grounded in what's on screen — no "what would
# you like to talk about?" cold start. The Live session already carries Friday's
# full vault/wiki context in its system prompt; these builders inject only the
# fresh data that isn't baked into that static prompt.

voice_context_bp = Blueprint('voice_context', __name__)


WORKSPACE_VOICE_LABELS = {
    'home': 'Home', 'news': 'News', 'calendar': 'Calendar',
    'messages': 'Comms Center', 'career': 'Career', 'futurespeak': 'FutureSpeak',
    'wiki': 'Wiki', 'trust': 'Trust', 'finance': 'Finance', 'health': 'Health',
    'family': 'Family', 'contacts': 'Contacts',
    'studio': 'Studio', 'code': 'Code', 'content': 'Content', 'system': 'System',
}


def _vc_calendar():
    try:
        events = _fetch_calendar_today()
        err = _google_section_error(events)
        if err:
            return ""  # not connected — let Friday speak from general context
        if not events:
            return "My calendar looks clear today."
        lines = []
        for ev in events[:10]:
            when = (ev.get('start_time') or '')[:16].replace('T', ' ')
            loc = f" — {ev['location']}" if ev.get('location') else ""
            lines.append(f"- {ev.get('title', '(untitled)')} at {when}{loc}")
        return "My calendar (today and tomorrow):\n" + "\n".join(lines)
    except Exception:
        return ""


def _vc_news(limit=5):
    try:
        items = _fetch_news_items(limit_per=2)[:limit]
        if not items:
            return ""
        cached = " (cached — I'm offline)" if items[0].get("cached") else ""
        lines = [f"- {it.get('title', '')} ({it.get('source', '')})" for it in items]
        return f"Top headlines right now{cached}:\n" + "\n".join(lines)
    except Exception:
        return ""


def _vc_messages():
    try:
        cards, source = _collect_messages(limit=20)
        if source == 'empty':
            return ""
        urgent = [c for c in cards
                  if c.get('urgent') or c.get('lane') in ('urgent', 'priority')][:6]
        if urgent:
            lines = [f"- {c.get('sender') or c.get('from') or ''}: "
                     f"{c.get('subject') or c.get('title') or ''}" for c in urgent]
            return "Email that may need attention:\n" + "\n".join(lines)
        recent = ", ".join((c.get('subject') or c.get('title') or '')[:40]
                           for c in cards[:3] if (c.get('subject') or c.get('title')))
        return (f"{len(cards)} recent messages, nothing flagged urgent."
                + (f" Newest: {recent}." if recent else ""))
    except Exception:
        return ""


def _vc_tasks():
    try:
        path = FRIDAY_DIR / "todos.json"
        if not path.exists():
            return ""
        todos = json.loads(path.read_text(encoding='utf-8'))
        active = [t for t in todos if isinstance(t, dict)
                  and t.get('status') in ('proposed', 'approved', 'open', 'in_progress')]
        if not active:
            return ""
        lines = [f"- {t.get('title') or t.get('task') or ''}" for t in active[:8]]
        return "Active tasks and commitments:\n" + "\n".join(lines)
    except Exception:
        return ""


def _vc_career():
    try:
        ctx = _get_career_context()
        if not ctx:
            return ""
        bits = []
        if ctx.get('applications_count'):
            bits.append(f"{ctx['applications_count']} applications tracked")
        if ctx.get('pipeline_summary'):
            bits.append("Pipeline:\n" + ctx['pipeline_summary'][:600])
        return "\n".join(bits)
    except Exception:
        return ""


def _vc_trust():
    try:
        vault = _load_vault_summary()
        people = vault.get('trust_people') or {}
        if not people:
            return ""
        top = ", ".join(f"{n} ({d.get('relationship', '?')})"
                        for n, d in list(people.items())[:8])
        return f"People in my trust circle: {top}."
    except Exception:
        return ""


# Per-workspace data builders. Anything not listed falls through to a generic
# prompt (the Live session still has full vault/wiki context for that domain).
_VOICE_CONTEXT_BUILDERS = {
    'home': lambda: "\n\n".join(filter(None, [_vc_calendar(), _vc_tasks(), _vc_news(3)])),
    'news': lambda: _vc_news(8),
    'calendar': _vc_calendar,
    'messages': _vc_messages,
    'career': _vc_career,
    'futurespeak': _vc_career,
    'trust': _vc_trust,
    'contacts': _vc_trust,
}


def _build_workspace_voice_prompt(ws_id):
    """Assemble the opening voice turn for a workspace. Returns (label, prompt)."""
    label = WORKSPACE_VOICE_LABELS.get(ws_id, ws_id.replace('_', ' ').title())
    builder = _VOICE_CONTEXT_BUILDERS.get(ws_id)
    data = ""
    if builder:
        try:
            data = (builder() or "").strip()
        except Exception:
            data = ""
    parts = [
        f"[WORKSPACE CONTEXT — the user just opened the {label} workspace and started "
        f"a voice conversation with you.]",
    ]
    if data:
        parts.append(data)
    parts.append(
        f"Greet me in one short sentence, then give me a quick spoken rundown of what "
        f"matters in {label} right now — most important first — and ask what I'd like "
        f"to do. Keep it natural and brief; no markdown."
    )
    return label, "\n\n".join(parts)


# Primary path is /api/voice-context/<workspace> (collision-free); the spec's
# /api/<workspace>/voice-context is registered as an alias. The alias can be
# shadowed for a workspace that already owns an /api/<ws>/<id>-style route
# (e.g. messages), because Werkzeug ranks a static first segment above a dynamic
# one — so the UI calls the collision-free form.
@voice_context_bp.route('/api/voice-context/<workspace>')
@voice_context_bp.route('/api/<workspace>/voice-context')
def workspace_voice_context(workspace):
    """Assemble a spoken-ready opening turn for a workspace voice session.

    Returns {status, workspace, label, prompt}. The frontend queues `prompt`
    as the first user turn when the Live socket opens so the session starts
    grounded in the workspace's live data. Unknown workspace ids 404.
    """
    raw = (workspace or "").strip().lower()
    try:
        ws_id = _resolve_workspace(raw) or raw
    except Exception:
        ws_id = raw
    if ws_id not in WORKSPACE_VOICE_LABELS and ws_id not in _VOICE_CONTEXT_BUILDERS:
        # Still serve a generic context so a voice button never dead-ends, but
        # only for a plausibly real workspace token.
        if not re.fullmatch(r"[a-z0-9_-]{2,40}", ws_id):
            return jsonify({"status": "error", "message": "unknown workspace"}), 404
    label, prompt = _build_workspace_voice_prompt(ws_id)
    return jsonify({"status": "ok", "workspace": ws_id, "label": label, "prompt": prompt})


@voice_context_bp.route('/api/voice/start-my-day')
def voice_start_my_day():
    """Assemble the sequential morning voice briefing (calendar → email → news → tasks).

    Returns {status, label, prompt}. Reuses the same live-data gatherer the
    News "Generate Briefing" button uses so the spoken briefing reflects today,
    not a stale cached context.
    """
    try:
        ctx = _gather_live_briefing_context()
    except Exception as e:
        ctx = f"(could not load all live data: {e})"
    tasks = _vc_tasks()
    prompt = (
        "[START MY DAY — the user asked for their morning voice briefing.]\n\n"
        "Here is today's live data:\n\n"
        + (ctx or "(no live data available)")
        + (("\n\n" + tasks) if tasks else "")
        + "\n\nDeliver a spoken morning briefing in this exact order, conversationally "
          "and concisely: first what's on my calendar today (most important first), "
          "then any email that needs my attention, then the top news relevant to me, "
          "then my active tasks and commitments. End with one proactive suggestion for "
          "the day. Speak naturally in short sentences — no markdown — and pause between "
          "sections so I can interrupt."
    )
    return jsonify({"status": "ok", "label": "Start My Day", "prompt": prompt})
