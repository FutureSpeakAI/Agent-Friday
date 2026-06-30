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

_log = logging.getLogger("friday.misc_engine")
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    FRIDAY_DIR,
    HOME,
    WIKI_PROFESSIONAL_DIR,
    _HAS_TRUST_GRAPHS,
    _POPEN_FLAGS,
    _log_context,
    get_people_graph,
)  # noqa: E501
from agent_friday.services.agent import (
    TASKS,
    TASKS_LOCK,
    _task_log,
    _task_set,
    _vault_read_text,
)  # noqa: E501
from agent_friday.services.model_router import (
    CAREER_OPS_DIR,
    _generate_text,
    _get_friday_system_prompt,
)  # noqa: E501



# ═══════════════════════════════════════════════════════════════
#  FINANCE WORKSPACE
# ═══════════════════════════════════════════════════════════════

FINANCE_DIR = FRIDAY_DIR / "finance"
FINANCE_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  HEALTH WORKSPACE
# ═══════════════════════════════════════════════════════════════

HEALTH_DIR = FRIDAY_DIR / "health"
HEALTH_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  AI TO-DO LIST
# ═══════════════════════════════════════════════════════════════

TODOS_FILE = FRIDAY_DIR / "todos.json"

def _load_todos():
    if TODOS_FILE.exists():
        try:
            return json.loads(TODOS_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []

def _save_todos(todos):
    TODOS_FILE.write_text(json.dumps(todos, indent=2), encoding='utf-8')


#  CLIPBOARD DRAFTING ENGINE
# ═══════════════════════════════════════════════════════════════

DRAFT_MODE_PROMPTS = {
    'linkedin_post': (
        "You are a LinkedIn ghostwriter for a senior AI/engineering leader. "
        "Write a professional but personable post — 1-3 paragraphs, strong opening hook, "
        "no hashtag spam (2-3 max at the end if any). Conversational authority, not corporate fluff. "
        "The voice should feel like a seasoned journalist who pivoted to AI."
    ),
    'email_reply': (
        "You are drafting a professional email reply. Match the formality of the original message. "
        "Be concise and clear. Include a specific call-to-action or next step. "
        "No filler phrases like 'I hope this email finds you well.'"
    ),
    'slack_message': (
        "You are drafting a Slack message. Keep it casual and brief — this is internal team chat. "
        "Emoji are fine where they feel natural. One short paragraph max. No sign-offs."
    ),
    'tweet': (
        "You are drafting a tweet. MUST be under 280 characters. Punchy, sharp, quotable. "
        "No hashtags unless they're genuinely clever. Think journalist, not influencer."
    ),
    'freeform': (
        "You are a versatile writing assistant. Follow the user's format instructions exactly. "
        "Write clearly and concisely unless told otherwise."
    ),
}

CONTENT_DRAFTS_DIR = FRIDAY_DIR / "wiki" / "content"


def _build_draft_html(draft_text, mode, prompt_text=''):
    """Build a styled HTML document for a draft, matching the daily briefing aesthetic."""
    mode_labels = {
        'linkedin_post': 'LinkedIn Post', 'email_reply': 'Email Reply',
        'slack_message': 'Slack Message', 'tweet': 'Tweet',
        'freeform': 'Freeform Draft',
    }
    mode_label = mode_labels.get(mode, mode)
    timestamp = datetime.now().strftime('%B %d, %Y · %H:%M')

    def _esc(s):
        return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    paras = [p.strip() for p in (draft_text or '').split('\n\n') if p.strip()]
    if not paras:
        paras = [(draft_text or '').strip() or '(empty)']

    _lead_style = ' style="font-size:18px;color:#e8e8f0"'
    _nl = chr(10)
    para_html = '\n    '.join(
        f'<p{_lead_style if i == 0 else ""}>{_esc(p).replace(_nl, "<br>")}</p>'
        for i, p in enumerate(paras)
    )
    prompt_block = (
        f'<div class="prompt-ctx">Prompt: {_esc(prompt_text[:200])}</div>'
        if prompt_text else ''
    )

    return (
        '<!DOCTYPE html><html lang="en">\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        f'<title>FRIDAY DRAFT — {_esc(mode_label)}</title>\n'
        '<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900'
        '&family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">\n'
        '<style>\n'
        '* { margin: 0; padding: 0; box-sizing: border-box; }\n'
        'body { background: #06060b; color: #e0e0e8; font-family: \'Inter\', sans-serif; line-height: 1.7; }\n'
        '.container { max-width: 780px; margin: 0 auto; padding: 40px 24px 80px; }\n'
        '.header { text-align: center; margin-bottom: 48px; padding-bottom: 32px; border-bottom: 1px solid rgba(0,212,255,0.15); }\n'
        '.header h1 { font-family: \'Orbitron\', monospace; font-size: 28px; font-weight: 900; '
        'background: linear-gradient(135deg, #00d4ff 0%, #7c3aed 50%, #ff0080 100%); '
        '-webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 8px; }\n'
        '.header .subtitle { font-size: 14px; color: #666; font-style: italic; }\n'
        '.header .date { font-family: \'JetBrains Mono\', monospace; font-size: 13px; color: #00d4ff; margin-top: 8px; letter-spacing: 0.05em; }\n'
        '.neon-line { height: 2px; background: linear-gradient(90deg, #00d4ff, #7c3aed, #ff0080); margin: 4px 0 0; opacity: 0.6; border-radius: 1px; }\n'
        '.mode-tag { display: inline-block; font-family: \'JetBrains Mono\', monospace; font-size: 11px; color: #7c3aed; border: 1px solid rgba(124,58,237,0.3); border-radius: 4px; padding: 2px 8px; margin-bottom: 24px; letter-spacing: 0.05em; }\n'
        '.prompt-ctx { font-size: 12px; color: #555; font-style: italic; margin-bottom: 32px; font-family: \'JetBrains Mono\', monospace; border-left: 2px solid rgba(0,212,255,0.2); padding-left: 12px; }\n'
        '.draft-body p { margin-bottom: 18px; font-size: 16px; line-height: 1.8; color: #d0d0d8; }\n'
        '.footer { text-align: center; margin-top: 60px; padding-top: 24px; border-top: 1px solid rgba(255,255,255,0.05); font-size: 12px; color: #444; font-family: \'JetBrains Mono\', monospace; }\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '<div class="container">\n'
        '  <div class="header">\n'
        '    <h1>FRIDAY DRAFT</h1>\n'
        '    <div class="neon-line"></div>\n'
        f'    <div class="subtitle">{_esc(mode_label)}</div>\n'
        f'    <div class="date">{timestamp}</div>\n'
        '  </div>\n'
        f'  {prompt_block}\n'
        f'  <div class="mode-tag">{_esc(mode_label.upper())}</div>\n'
        '  <div class="draft-body">\n'
        f'    {para_html}\n'
        '  </div>\n'
        '  <div class="footer">Generated by FRIDAY · FutureSpeak.AI</div>\n'
        '</div>\n'
        '</body>\n'
        '</html>'
    )


def _spawn_draft_task(mode, prompt_text, context=''):
    """Spawn a background draft-generation task. Returns (response_dict, http_status).

    Shared by /api/draft so drafts go through the same
    vault-context-aware pipeline.
    """
    if not (prompt_text or '').strip():
        return {"status": "error", "message": "No prompt provided"}, 400

    system = DRAFT_MODE_PROMPTS.get(mode, DRAFT_MODE_PROMPTS['freeform'])
    system += "\n\nOutput ONLY the draft text, no commentary or labels."

    user_parts = []
    if context:
        user_parts.append(f"CONTEXT (what the user is looking at / replying to):\n{context}")
    user_parts.append(f"USER INSTRUCTION:\n{prompt_text}")
    full_prompt = '\n\n'.join(user_parts)

    mode_labels = {
        'linkedin_post': 'LinkedIn Post', 'email_reply': 'Email Reply',
        'slack_message': 'Slack Message', 'tweet': 'Tweet',
        'freeform': 'Freeform Draft',
    }
    task_name = f"Quick Draft — {mode_labels.get(mode, mode)}"
    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            'task_id': task_id,
            'name': task_name,
            'description': prompt_text[:100],
            'status': 'queued',
            'created': _time.time(),
            'started': None,
            'ended': None,
            'log': [],
            'result': '',
            'draft_mode': mode,
        }

    # Capture loop variables for the thread closure
    _system = system
    _full_prompt = full_prompt
    _mode = mode

    def _draft_worker():
        _task_set(task_id, status='running', started=_time.time())
        _task_log(task_id, f'Generating {_mode} draft…')
        try:
            # Load full vault/wiki context — Friday MUST know the user's contacts,
            # his name, his boss, his family, etc. when writing on his behalf.
            _task_log(task_id, 'Loading vault context…')
            full_system = _get_friday_system_prompt(prompt_text, workspace='draft')
            full_system += f"\n\n== DRAFT WRITING INSTRUCTIONS ==\n{_system}"
            draft_text = _generate_text(
                [{"role": "user", "content": _full_prompt}],
                system=full_system,
                max_tokens=16384,
            )
            # Auto-save to content library
            try:
                CONTENT_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
                now_dt = datetime.now()
                slug = re.sub(r'[^a-z0-9]+', '-', prompt_text[:30].lower()).strip('-') or _mode
                fname = f"draft-{now_dt.strftime('%Y-%m-%d-%H%M')}-{slug}.html"
                html_content = _build_draft_html(draft_text, _mode, prompt_text)
                (CONTENT_DRAFTS_DIR / fname).write_text(html_content, encoding='utf-8')
                _task_log(task_id, f'Saved to library: {fname}')
            except Exception as _se:
                _task_log(task_id, f'Library save failed (non-fatal): {_se}')
            _task_set(task_id, status='complete', result=draft_text, ended=_time.time())
            _task_log(task_id, 'Draft ready.')
        except Exception as e:
            traceback.print_exc()
            _task_set(task_id, status='failed', result=f'[Error] {e}', ended=_time.time())
            _task_log(task_id, f'Error: {e}')

    threading.Thread(target=_draft_worker, daemon=True).start()
    _log_context("draft_spawn", {"task_id": task_id, "mode": mode, "prompt": prompt_text[:200]})

    return {
        "status": "queued",
        "task_id": task_id,
        "name": task_name,
    }, 200


# ═══════════════════════════════════════════════════════════════
#  DATA FLOW API — "Write once, live everywhere"
# ═══════════════════════════════════════════════════════════════

FLOW_QUEUE_DIR = FRIDAY_DIR / "flow-queue"
FLOW_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

BRIEFING_SUPPLEMENT_DIR = FRIDAY_DIR / "wiki" / "briefings"
BRIEFING_SUPPLEMENT_DIR.mkdir(parents=True, exist_ok=True)


def _flow_trust_graph(content, metadata):
    """Update a person's trust graph entry with new intelligence."""
    person_name = metadata.get('person_name', '').strip()
    if not person_name:
        return {'destination': 'trust_graph', 'ok': False, 'error': 'No person_name in metadata'}

    trust_file = FRIDAY_DIR / "trust_graph.json"
    try:
        tdata = {}
        if trust_file.exists():
            tdata = json.loads(trust_file.read_text(encoding='utf-8'))
        if 'people' not in tdata:
            tdata['people'] = {}

        key = person_name.lower().replace(' ', '_').replace('-', '_')

        if key not in tdata['people']:
            # Auto-create entry
            tdata['people'][key] = {
                "name": person_name,
                "aliases": [],
                "entity_type": "human",
                "scores": {"overall": 0.5, "reliability": 0.5, "information_quality": 0.5,
                           "emotional_trust": 0.5, "timeliness": 0.5, "domain_expertise": 0.5},
                "evidence": [],
                "domains": [],
                "last_interaction": datetime.now().isoformat(),
                "created": datetime.now().isoformat()
            }

        person = tdata['people'][key]
        if 'intelligence' not in person:
            person['intelligence'] = []
        person['intelligence'].append({
            "content": content[:2000],
            "timestamp": datetime.now().isoformat(),
            "source": "data_flow"
        })
        # Keep last 20 intel entries
        person['intelligence'] = person['intelligence'][-20:]
        person['last_interaction'] = datetime.now().isoformat()

        tdata['people'][key] = person
        trust_file.write_text(json.dumps(tdata, indent=2), encoding='utf-8')
        return {'destination': 'trust_graph', 'ok': True, 'person': key}
    except Exception as e:
        return {'destination': 'trust_graph', 'ok': False, 'error': str(e)}


def _flow_calendar_notes(content, metadata):
    """Push content to a Google Calendar event description."""
    event_id = metadata.get('event_id', '').strip()
    if not event_id:
        return {'destination': 'calendar_notes', 'ok': False, 'error': 'No event_id in metadata'}
    try:
        result = _enrich_calendar_event(event_id, content)
        return {'destination': 'calendar_notes', **result}
    except Exception as e:
        return {'destination': 'calendar_notes', 'ok': False, 'error': str(e)}


def _flow_clipboard(content, _metadata):
    """Copy content to Windows clipboard via PowerShell."""
    try:
        subprocess.run(
            ['powershell', '-Command', 'Set-Clipboard', '-Value', content[:10000]],
            capture_output=True, text=True, timeout=10,
            creationflags=_POPEN_FLAGS,
        )
        return {'destination': 'clipboard', 'ok': True}
    except Exception as e:
        return {'destination': 'clipboard', 'ok': False, 'error': str(e)}


def _flow_gmail_draft(content, metadata):
    """Stage a Gmail draft in the flow queue for frontend pickup."""
    try:
        draft = {
            "id": str(uuid.uuid4()),
            "content": content[:10000],
            "thread_id": metadata.get('email_thread_id', ''),
            "person_name": metadata.get('person_name', ''),
            "created": datetime.now().isoformat(),
            "status": "pending"
        }
        draft_file = FLOW_QUEUE_DIR / f"gmail-draft-{draft['id']}.json"
        draft_file.write_text(json.dumps(draft, indent=2), encoding='utf-8')
        return {'destination': 'gmail_draft', 'ok': True, 'draft_id': draft['id']}
    except Exception as e:
        return {'destination': 'gmail_draft', 'ok': False, 'error': str(e)}


def _flow_briefing(content, metadata):
    """Append content to today's briefing supplementary file."""
    try:
        today_str = date.today().isoformat()
        supplement_file = BRIEFING_SUPPLEMENT_DIR / f"{today_str}-supplement.md"

        existing = ''
        if supplement_file.exists():
            existing = supplement_file.read_text(encoding='utf-8')

        person_name = metadata.get('person_name', '')
        header = f"\n\n---\n### {person_name or 'Research'} — {datetime.now().strftime('%H:%M')}\n" if existing else f"# Briefing Supplement — {today_str}\n\n### {person_name or 'Research'} — {datetime.now().strftime('%H:%M')}\n"

        supplement_file.write_text(existing + header + content[:5000] + '\n', encoding='utf-8')
        return {'destination': 'briefing', 'ok': True, 'file': str(supplement_file.name)}
    except Exception as e:
        return {'destination': 'briefing', 'ok': False, 'error': str(e)}


FLOW_HANDLERS = {
    'trust_graph': _flow_trust_graph,
    'calendar_notes': _flow_calendar_notes,
    'clipboard': _flow_clipboard,
    'gmail_draft': _flow_gmail_draft,
    'briefing': _flow_briefing,
}


# ═══════════════════════════════════════════════════════════════
#  CALENDAR ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def _enrich_calendar_event(event_id, research):
    """Read a calendar event, append Friday research, and update it.

    Uses the gcal MCP tools when available; falls back to storing
    the enrichment locally for later sync.
    """
    separator = "\n\n--- Friday Meeting Prep ---\n"
    enrichment = separator + research.strip() + "\n"

    # Try MCP-based Google Calendar update
    # The gcal tools are invoked at the agent/MCP layer, not directly here.
    # This endpoint stores the enrichment and exposes it for MCP tool orchestration.
    enrichment_file = FLOW_QUEUE_DIR / f"calendar-enrich-{event_id}.json"
    payload = {
        "event_id": event_id,
        "research": research.strip(),
        "enrichment_block": enrichment,
        "created": datetime.now().isoformat(),
        "status": "pending_sync"
    }
    enrichment_file.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return {"ok": True, "event_id": event_id, "status": "queued_for_sync",
            "message": "Enrichment stored. Will sync via gcal MCP on next calendar pass."}


def push_to_calendar(event_id, research_text):
    """Helper for briefing tasks to push research into calendar events.

    Call this from daily briefing generation when attendee research is ready.
    It routes through the flow API to update both the calendar and trust graph.
    """
    results = {}

    # Push to calendar
    results['calendar'] = _enrich_calendar_event(event_id, research_text)

    # Also push to briefing supplement
    results['briefing'] = _flow_briefing(research_text, {'person_name': 'Meeting Prep'})

    return results


# ═══════════════════════════════════════════════════════════════
#  CONTACTS / CRM
# ═══════════════════════════════════════════════════════════════

def _load_trust_graph():
    """Load the people graph with consistent shape. Delegates to PeopleGraph
    (canonical ~/.friday/people_graph.json, mirrored to trust_graph.json), with
    a direct-file fallback if the module is unavailable."""
    if _HAS_TRUST_GRAPHS:
        try:
            return get_people_graph(friday_dir=FRIDAY_DIR).load()
        except Exception:
            pass
    tfile = FRIDAY_DIR / "trust_graph.json"
    if not tfile.exists():
        return {"people": {}}
    try:
        return json.loads(tfile.read_text(encoding='utf-8'))
    except Exception:
        return {"people": {}}


def _contacts_list():
    """Merge trust graph people into a flat contacts list."""
    graph = _load_trust_graph()
    raw = graph.get('people') or {}
    items = raw.values() if isinstance(raw, dict) else raw
    contacts = []
    for p in items:
        if not isinstance(p, dict):
            continue
        scores = p.get('scores') or {}
        overall = scores.get('overall')
        if not isinstance(overall, (int, float)):
            overall = 0.5
        contacts.append({
            "name": p.get('name') or 'Unknown',
            "aliases": p.get('aliases') or [],
            "domains": p.get('domains') or [],
            "overall": overall,
            "last_interaction": p.get('last_interaction'),
            "evidence_count": len(p.get('evidence') or []),
        })
    contacts.sort(key=lambda c: c.get('overall') or 0, reverse=True)
    return contacts


def _contacts_research_dir():
    d = FRIDAY_DIR / "contacts-research"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ═══════════════════════════════════════════════════════════════
#  ROUTINES
# ═══════════════════════════════════════════════════════════════

ROUTINES_DIR = FRIDAY_DIR / "routines"
ROUTINES_DIR.mkdir(parents=True, exist_ok=True)
ROUTINE_STATUS_FILE = FRIDAY_DIR / "routine_status.json"

# Registered routine catalog. Defines display + default schedule when a template is missing.
ROUTINE_REGISTRY = [
    {"id": "morning-briefing",   "label": "Morning Briefing",    "ico": "🌅", "category": "briefing",    "schedule": "Daily · 7:00 AM"},
    {"id": "afternoon-briefing", "label": "Afternoon Briefing",  "ico": "☀️", "category": "briefing",    "schedule": "Daily · 2:00 PM"},
    {"id": "weekly-legal-prep",  "label": "Weekly Legal Prep",   "ico": "⚖️", "category": "legal",       "schedule": "Sundays · 6:00 PM"},
    {"id": "family-weekend-prep", "label": "Family Weekend Prep",  "ico": "👧", "category": "family",      "schedule": "Thursdays · 6:00 PM"},
    {"id": "portfolio-snapshot", "label": "Portfolio Snapshot",  "ico": "💰", "category": "finance",     "schedule": "Daily · 5:00 PM"},
    {"id": "content-pipeline",   "label": "Content Pipeline",    "ico": "✍️", "category": "content",     "schedule": "Daily · 10:00 AM"},
    {"id": "daily-creation",     "label": "Daily Creation",      "ico": "🎨", "category": "studio",      "schedule": "Daily · 2:00 PM"},
    {"id": "job-intelligence",   "label": "Job Intelligence",    "ico": "💼", "category": "career",      "schedule": "Daily · 8:00 AM"},
    {"id": "repo-sync",          "label": "Repo Sync",           "ico": "🔄", "category": "engineering", "schedule": "Daily · 11:00 PM"},
]


def _load_routine_status():
    if ROUTINE_STATUS_FILE.exists():
        try:
            return json.loads(ROUTINE_STATUS_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _save_routine_status(status):
    try:
        ROUTINE_STATUS_FILE.write_text(json.dumps(status, indent=2), encoding='utf-8')
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  OUTREACH PIPELINE
# ═══════════════════════════════════════════════════════════════

OUTREACH_DIR = FRIDAY_DIR / "outreach"
OUTREACH_DIR.mkdir(parents=True, exist_ok=True)
OUTREACH_LOG_FILE = OUTREACH_DIR / "outreach-log.json"


def _load_outreach_log():
    if not OUTREACH_LOG_FILE.exists():
        return {"version": 1, "entries": []}
    try:
        return json.loads(OUTREACH_LOG_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {"version": 1, "entries": []}


def _save_outreach_log(log):
    log["updated"] = datetime.now().isoformat()
    try:
        OUTREACH_LOG_FILE.write_text(json.dumps(log, indent=2), encoding='utf-8')
    except Exception as e:
        _log.warning("outreach log save failed: %s", e)


def _career_ops_companies():
    """Return list of companies currently in the career-ops tracker (applied/interviewing)."""
    candidates = [WIKI_PROFESSIONAL_DIR / 'application-log.md', CAREER_OPS_DIR / 'applications.md']
    tracker_path = next((p for p in candidates if p.is_file()), None)
    if not tracker_path:
        return []
    try:
        content = tracker_path.read_text(encoding='utf-8')
    except Exception:
        return []
    companies = []
    for line in content.strip().split('\n'):
        if line.startswith('|') and '---' not in line and 'company' not in line.lower():
            cols = [c.strip() for c in line.split('|')[1:-1]]
            if len(cols) >= 3 and cols[0]:
                companies.append({"company": cols[0], "score": cols[1] if len(cols) > 1 else '', "status": cols[2] if len(cols) > 2 else ''})
    return companies


# ═══════════════════════════════════════════════════════════════
#  CONTENT PIPELINE
# ═══════════════════════════════════════════════════════════════

CONTENT_DIR = FRIDAY_DIR / "content"
CONTENT_DIR.mkdir(parents=True, exist_ok=True)
CONTENT_PIPELINE_FILE = CONTENT_DIR / "pipeline.json"
CONTENT_STAGES = ["idea", "drafting", "review", "scheduled", "published"]


def _load_content_pipeline():
    if not CONTENT_PIPELINE_FILE.exists():
        return {"version": 1, "items": []}
    try:
        return json.loads(CONTENT_PIPELINE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {"version": 1, "items": []}


def _save_content_pipeline(pipe):
    pipe["updated"] = datetime.now().isoformat()
    try:
        CONTENT_PIPELINE_FILE.write_text(json.dumps(pipe, indent=2), encoding='utf-8')
    except Exception as e:
        _log.warning("content pipeline save failed: %s", e)


# Curated starter templates so the Content studio is useful on day one. Each is
# a one-click scaffold: it seeds a pipeline idea pre-filled with type/channel and
# a structure prompt Friday expands when you hit Draft.
CONTENT_TEMPLATES = [
    {
        "id": "linkedin-thought",
        "label": "LinkedIn thought-leadership post",
        "icon": "in",
        "type": "post", "channel": "linkedin",
        "blurb": "Hook → contrarian insight → 3 beats → CTA. ~200 words.",
        "scaffold": ("Audience: my professional network. Goal: spark discussion and "
                     "establish credibility.\nStructure:\n- Scroll-stopping first line (no preamble)\n"
                     "- One contrarian or non-obvious insight\n- 2-3 short proof beats / examples\n"
                     "- A question or single CTA to drive comments\nVoice: sharp, specific, no buzzwords."),
    },
    {
        "id": "blog-howto",
        "label": "Blog / article (how-to)",
        "icon": "📄",
        "type": "article", "channel": "blog",
        "blurb": "SEO-aware long-form: problem, steps, takeaways.",
        "scaffold": ("Format: 800-1200 word how-to article.\nStructure:\n- Title + 1-sentence promise\n"
                     "- Why this matters (the problem)\n- Numbered steps with concrete detail\n"
                     "- Common pitfalls\n- Key takeaways + next step\nInclude a suggested meta description."),
    },
    {
        "id": "newsletter",
        "label": "Newsletter issue",
        "icon": "✉️",
        "type": "newsletter", "channel": "substack",
        "blurb": "Personal intro, 3 curated items, one deeper take.",
        "scaffold": ("Format: email newsletter.\nStructure:\n- Warm personal intro (2-3 lines)\n"
                     "- 3 curated items, each a bolded title + 2-sentence why-it-matters\n"
                     "- One deeper 'my take' section\n- Sign-off + CTA\nTone: like writing to a smart friend."),
    },
    {
        "id": "twitter-thread",
        "label": "X / Twitter thread",
        "icon": "𝕏",
        "type": "thread", "channel": "twitter",
        "blurb": "Hook tweet + 5-8 numbered beats + recap.",
        "scaffold": ("Format: a thread of 6-9 tweets.\nStructure:\n- Tweet 1: bold hook/claim\n"
                     "- Tweets 2-8: one idea each, <280 chars, concrete\n- Final tweet: recap + follow CTA\n"
                     "Number each tweet. No hashtags spam."),
    },
    {
        "id": "outreach-email",
        "label": "Outreach / cold email",
        "icon": "📧",
        "type": "email", "channel": "email",
        "blurb": "Personalized, short, one clear ask.",
        "scaffold": ("Format: cold outreach email.\nStructure:\n- Subject line (curiosity, <8 words)\n"
                     "- 1-line personalized opener (reference the recipient)\n- 2 sentences of relevance/value\n"
                     "- One specific, low-friction ask\n- Brief sign-off\nKeep under 120 words. No corporate filler."),
    },
    {
        "id": "presentation",
        "label": "Presentation outline",
        "icon": "📊",
        "type": "presentation", "channel": "deck",
        "blurb": "Slide-by-slide narrative arc with talk-track.",
        "scaffold": ("Format: presentation outline.\nStructure: 8-12 slides, each with a title, 2-4 "
                     "bullet points, and a one-line speaker note.\nArc: hook → problem → stakes → "
                     "solution → proof → ask.\nEnd with a clear call to action slide."),
    },
    {
        "id": "announcement",
        "label": "Launch / announcement post",
        "icon": "🚀",
        "type": "post", "channel": "linkedin",
        "blurb": "What shipped, why it matters, what's next.",
        "scaffold": ("Format: announcement post.\nStructure:\n- The news in one punchy line\n"
                     "- What it is / what changed\n- Why it matters to the reader\n- A concrete proof point\n"
                     "- What's next + where to learn more.\nTone: confident, not hypey."),
    },
]


def _content_template(template_id):
    return next((t for t in CONTENT_TEMPLATES if t["id"] == template_id), None)


