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
    VIBE_TERMINALS,
    _POPEN_FLAGS,
    get_genai_client,
)  # noqa: E501
from services.agent import (
    delete_workflow_chain,
    list_workflow_chains,
    load_workflow_chain,
    run_workflow_chain,
    save_workflow_chain,
)  # noqa: E501
from services.misc_engine import (
    CONTENT_DRAFTS_DIR,
    CONTENT_STAGES,
    CONTENT_TEMPLATES,
    _content_template,
    FLOW_HANDLERS,
    FLOW_QUEUE_DIR,
    ROUTINES_DIR,
    ROUTINE_REGISTRY,
    _career_ops_companies,
    _load_content_pipeline,
    _load_outreach_log,
    _load_routine_status,
    _load_trust_graph,
    _save_content_pipeline,
    _save_outreach_log,
    _save_routine_status,
    _spawn_draft_task,
)  # noqa: E501
from services.model_router import (
    _generate_text,
    _get_friday_system_prompt,
)  # noqa: E501

workflows_bp = Blueprint('workflows', __name__)



@workflows_bp.route('/api/draft', methods=['POST'])
def draft_generate():
    """Generate a draft via Claude — spawns as a background task, returns task_id immediately."""
    try:
        data = request.get_json(silent=True) or {}
        resp, code = _spawn_draft_task(
            mode=data.get('mode', 'freeform'),
            prompt_text=data.get('prompt', ''),
            context=data.get('context', ''),
        )
        return jsonify(resp), code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@workflows_bp.route('/api/draft/deploy', methods=['POST'])
def draft_deploy():
    """Deploy a draft to clipboard or other destination."""
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    destination = data.get('destination', 'clipboard')

    if not text:
        return jsonify({"status": "error", "message": "No text provided"}), 400

    if destination == 'clipboard':
        try:
            # Escape for PowerShell: replace double quotes and backticks
            escaped = text.replace('`', '``').replace('"', '`"').replace('$', '`$')
            subprocess.run(
                ['powershell', '-command', f'Set-Clipboard -Value "{escaped}"'],
                capture_output=True, text=True, timeout=10,
                creationflags=_POPEN_FLAGS,
            )
            return jsonify({"status": "ok", "destination": "clipboard", "char_count": len(text)})
        except subprocess.TimeoutExpired:
            return jsonify({"status": "error", "message": "Clipboard operation timed out"}), 500
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    elif destination == 'gmail_draft':
        # Frontend handles Gmail draft creation via MCP tools — return acknowledgment
        return jsonify({
            "status": "ok",
            "destination": "gmail_draft",
            "gmail_to": data.get('gmail_to', ''),
            "gmail_subject": data.get('gmail_subject', ''),
            "text": text,
            "message": "Gmail draft data ready — frontend will create via MCP"
        })

    return jsonify({"status": "error", "message": f"Unknown destination: {destination}"}), 400


@workflows_bp.route('/api/content/drafts')
def list_content_drafts():
    """List saved draft HTML files from ~/.friday/wiki/content/."""
    CONTENT_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    drafts = []
    for f in sorted(CONTENT_DRAFTS_DIR.glob('*.html'), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            drafts.append({
                'filename': f.name,
                'size': f.stat().st_size,
                'modified': datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
        except Exception:
            pass
    return jsonify({'status': 'ok', 'drafts': drafts, 'total': len(drafts)})


@workflows_bp.route('/api/content/drafts/<filename>')
def serve_content_draft(filename):
    """Serve a saved draft HTML file for browser viewing."""
    safe_name = Path(filename).name
    filepath = CONTENT_DRAFTS_DIR / safe_name
    if not filepath.exists() or not filepath.is_file():
        return jsonify({'status': 'not_found'}), 404
    CONTENT_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    return send_from_directory(str(CONTENT_DRAFTS_DIR), safe_name)


@workflows_bp.route('/api/flow', methods=['POST'])
def data_flow():
    """Central data flow endpoint — routes content to multiple destinations.

    POST JSON:
    {
      "data_type": "contact_research|meeting_prep|draft|briefing_excerpt|job_research",
      "content": "the content to distribute",
      "metadata": {"person_name": "", "event_id": "", "email_thread_id": ""},
      "destinations": ["trust_graph", "calendar_notes", "briefing", "clipboard", "gmail_draft"]
    }
    """
    data = request.get_json(silent=True) or {}
    content = data.get('content', '').strip()
    if not content:
        return jsonify({"status": "error", "message": "No content provided"}), 400

    destinations = data.get('destinations', [])
    if not destinations:
        return jsonify({"status": "error", "message": "No destinations specified"}), 400

    metadata = data.get('metadata', {})
    data_type = data.get('data_type', 'general')
    receipt = {"status": "ok", "data_type": data_type, "results": []}

    for dest in destinations:
        handler = FLOW_HANDLERS.get(dest)
        if handler:
            result = handler(content, metadata)
            receipt["results"].append(result)
        else:
            receipt["results"].append({"destination": dest, "ok": False, "error": f"Unknown destination: {dest}"})

    succeeded = sum(1 for r in receipt["results"] if r.get('ok'))
    failed = len(receipt["results"]) - succeeded
    receipt["summary"] = f"{succeeded} succeeded, {failed} failed"
    return jsonify(receipt)


@workflows_bp.route('/api/flow/queue', methods=['GET'])
def flow_queue():
    """List pending items in the flow queue (gmail drafts, etc)."""
    items = []
    if FLOW_QUEUE_DIR.exists():
        for f in sorted(FLOW_QUEUE_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.suffix == '.json':
                try:
                    items.append(json.loads(f.read_text(encoding='utf-8')))
                except Exception:
                    pass
    return jsonify({"status": "ok", "items": items[:50], "count": len(items)})


@workflows_bp.route('/api/flow/draft/confirm', methods=['POST'])
def confirm_draft():
    """Mark a queued gmail draft as deployed/sent."""
    data = request.get_json(silent=True) or {}
    draft_id = data.get('draft_id', '').strip()
    if not draft_id:
        return jsonify({"status": "error", "message": "No draft_id provided"}), 400

    draft_file = FLOW_QUEUE_DIR / f"gmail-draft-{draft_id}.json"
    if not draft_file.exists():
        return jsonify({"status": "error", "message": "Draft not found"}), 404

    try:
        draft = json.loads(draft_file.read_text(encoding='utf-8'))
        draft['status'] = 'deployed'
        draft['deployed_at'] = datetime.now().isoformat()
        draft_file.write_text(json.dumps(draft, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "draft_id": draft_id})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@workflows_bp.route('/api/routines')
def list_routines():
    """Return the routine registry plus last-run status for each."""
    status = _load_routine_status()
    out = []
    for r in ROUTINE_REGISTRY:
        s = status.get(r['id'], {}) or {}
        template_exists = (ROUTINES_DIR / f"{r['id']}.md").exists()
        out.append({
            **r,
            "last_run": s.get('last_run'),
            "last_status": s.get('last_status'),
            "last_task_id": s.get('last_task_id'),
            "template_exists": template_exists,
        })
    return jsonify({"status": "ok", "routines": out})


@workflows_bp.route('/api/routines/<routine_id>/run', methods=['POST'])
def run_routine(routine_id):
    """Trigger a routine on demand. Launches a background Vibe-Code task and records status."""
    reg = next((r for r in ROUTINE_REGISTRY if r['id'] == routine_id), None)
    if not reg:
        return jsonify({"status": "error", "message": "Unknown routine"}), 404

    template = ROUTINES_DIR / f"{routine_id}.md"
    task_desc = f"Run routine: {reg['label']}"
    if template.exists():
        task_desc += f" (see {template.name})"

    stamp = datetime.now().isoformat()
    tid = str(uuid.uuid4())[:8]
    try:
        VIBE_TERMINALS[tid] = {
            "id": tid, "task": task_desc,
            "status": "pending", "cwd": str(Path.cwd()),
            "started": stamp, "log_file": None
        }
    except Exception:
        pass

    status = _load_routine_status()
    status[routine_id] = {
        "last_run": stamp,
        "last_status": "launched",
        "last_task_id": tid,
    }
    _save_routine_status(status)

    return jsonify({
        "status": "ok",
        "routine": routine_id,
        "task_id": tid,
        "started_at": stamp,
        "message": f"{reg['label']} launched",
    })


@workflows_bp.route('/api/outreach/suggestions')
def outreach_suggestions():
    """Warm leads pulled from trust graph + career-ops tracker."""
    graph = _load_trust_graph()
    people_raw = graph.get('people') or {}
    people_items = people_raw.values() if isinstance(people_raw, dict) else people_raw

    log = _load_outreach_log()
    recent_targets = {
        (e.get('contact') or '').strip().lower()
        for e in log.get('entries', [])
        if e.get('contact')
    }

    suggestions = []
    for p in people_items:
        if not isinstance(p, dict):
            continue
        scores = p.get('scores') or {}
        overall = scores.get('overall')
        if not isinstance(overall, (int, float)):
            overall = 0.5
        if overall < 0.55:
            continue
        name = p.get('name') or 'Unknown'
        last = p.get('last_interaction') or ''
        suggestions.append({
            "type": "warm_contact",
            "contact": name,
            "score": round(overall, 2),
            "domains": p.get('domains') or [],
            "last_interaction": last,
            "reason": f"Trust {int(overall*100)}%" + (f" · last contact {last[:10]}" if last else " · no recent touch"),
            "already_contacted": name.lower() in recent_targets,
        })
    suggestions.sort(key=lambda s: s['score'], reverse=True)

    companies = _career_ops_companies()
    company_suggestions = []
    for c in companies[:10]:
        status = (c.get('status') or '').lower()
        if any(t in status for t in ('applied', 'interview', 'evaluated')):
            company_suggestions.append({
                "type": "career_target",
                "company": c.get('company'),
                "status": c.get('status'),
                "score": c.get('score'),
                "reason": f"Career-ops: {c.get('status') or 'tracked'}",
            })

    return jsonify({
        "status": "ok",
        "warm_contacts": suggestions[:20],
        "career_targets": company_suggestions,
        "total": len(suggestions) + len(company_suggestions),
    })


@workflows_bp.route('/api/outreach/draft', methods=['POST'])
def outreach_draft():
    """Draft outreach message. Uses Gemini if available, else templated fallback."""
    data = request.get_json(silent=True) or {}
    contact = (data.get('contact') or data.get('name') or '').strip()
    company = (data.get('company') or '').strip()
    angle = (data.get('angle') or 'reconnect').strip()
    channel = (data.get('channel') or 'email').strip()
    context_notes = (data.get('context') or '').strip()

    if not contact and not company:
        return jsonify({"status": "error", "message": "contact or company required"}), 400

    target_label = contact or company
    prompt = (
        f"Draft a {channel} outreach to {target_label}. "
        f"Angle: {angle}. "
        f"Tone: warm, concise, specific. "
        f"Keep under 150 words. End with a single clear ask. "
        f"Context: {context_notes}"
    )

    draft_text = None
    try:
        client = get_genai_client()
        if client:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            draft_text = (getattr(resp, 'text', None) or '').strip()
    except Exception as e:
        print(f"  [FRIDAY] outreach draft Gemini error: {e}")

    if not draft_text:
        subject = f"Quick hello — {angle.title()}"
        body = (
            f"Hi {contact or 'there'},\n\n"
            f"Wanted to reach out — {angle}. "
            f"Specifically: {context_notes or 'would love to catch up when you have a few minutes.'}\n\n"
            f"Does next week work for a short call?\n\n"
            f"Best,"
        )
        draft_text = f"Subject: {subject}\n\n{body}"

    return jsonify({
        "status": "ok",
        "contact": contact,
        "company": company,
        "channel": channel,
        "angle": angle,
        "draft": draft_text,
    })


@workflows_bp.route('/api/outreach/log', methods=['POST'])
def outreach_log():
    """Append an outreach event to the log."""
    data = request.get_json(silent=True) or {}
    contact = (data.get('contact') or '').strip()
    company = (data.get('company') or '').strip()
    channel = (data.get('channel') or 'email').strip()
    angle = (data.get('angle') or '').strip()
    message = (data.get('message') or '').strip()
    status = (data.get('status') or 'sent').strip()

    if not contact and not company:
        return jsonify({"status": "error", "message": "contact or company required"}), 400

    log = _load_outreach_log()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "contact": contact,
        "company": company,
        "channel": channel,
        "angle": angle,
        "status": status,
        "message": message[:2000],
        "timestamp": datetime.now().isoformat(),
    }
    log.setdefault('entries', []).append(entry)
    _save_outreach_log(log)
    return jsonify({"status": "ok", "entry": entry, "total": len(log['entries'])})


@workflows_bp.route('/api/outreach/pipeline')
def outreach_pipeline():
    """Pipeline view: counts by channel/angle/status plus recent entries."""
    log = _load_outreach_log()
    entries = list(reversed(log.get('entries', [])))

    by_status, by_channel, by_angle = {}, {}, {}
    for e in entries:
        by_status[e.get('status', 'unknown')] = by_status.get(e.get('status', 'unknown'), 0) + 1
        by_channel[e.get('channel', 'unknown')] = by_channel.get(e.get('channel', 'unknown'), 0) + 1
        if e.get('angle'):
            by_angle[e['angle']] = by_angle.get(e['angle'], 0) + 1

    return jsonify({
        "status": "ok",
        "total": len(entries),
        "by_status": by_status,
        "by_channel": by_channel,
        "by_angle": by_angle,
        "recent": entries[:25],
    })


@workflows_bp.route('/api/content/pipeline')
def content_pipeline():
    """Return content pipeline grouped by stage for kanban view."""
    pipe = _load_content_pipeline()
    items = pipe.get('items', [])
    by_stage = {s: [] for s in CONTENT_STAGES}
    for it in items:
        stage = it.get('stage') or 'idea'
        if stage not in by_stage:
            by_stage.setdefault(stage, [])
        by_stage[stage].append(it)
    return jsonify({
        "status": "ok",
        "stages": CONTENT_STAGES,
        "by_stage": by_stage,
        "total": len(items),
    })


@workflows_bp.route('/api/content/idea', methods=['POST'])
def content_idea():
    """Add a new content idea to the pipeline."""
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({"status": "error", "message": "title required"}), 400

    stage = (data.get('stage') or 'idea').strip()
    if stage not in CONTENT_STAGES:
        stage = 'idea'

    pipe = _load_content_pipeline()
    stamp = datetime.now().isoformat()
    item = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "type": (data.get('type') or 'post').strip(),
        "stage": stage,
        "channel": (data.get('channel') or 'linkedin').strip(),
        "notes": (data.get('notes') or '').strip(),
        "tags": data.get('tags') or [],
        "created": stamp,
        "updated": stamp,
    }
    pipe.setdefault('items', []).append(item)
    _save_content_pipeline(pipe)
    return jsonify({"status": "ok", "item": item, "total": len(pipe['items'])})


@workflows_bp.route('/api/content/draft', methods=['POST'])
def content_draft():
    """Draft content from a pipeline item (or ad-hoc title). Optionally advances stage."""
    data = request.get_json(silent=True) or {}
    item_id = (data.get('id') or '').strip()
    title = (data.get('title') or '').strip()
    channel = (data.get('channel') or 'linkedin').strip()
    notes = (data.get('notes') or '').strip()
    advance = bool(data.get('advance_stage'))

    pipe = _load_content_pipeline()
    item = None
    if item_id:
        for it in pipe.get('items', []):
            if it.get('id') == item_id:
                item = it
                break
        if not item:
            return jsonify({"status": "error", "message": "item not found"}), 404
        title = title or item.get('title', '')
        channel = item.get('channel') or channel
        notes = notes or item.get('notes', '')

    if not title:
        return jsonify({"status": "error", "message": "title or id required"}), 400

    prompt = (
        f"Draft a {channel} {item.get('type') if item else 'post'} titled: {title}. "
        f"Write in the user's voice. "
        f"Tone: sharp, specific, credible. "
        f"Structure: hook, 2-3 body beats, ask/CTA. "
        f"Length: 180-260 words for LinkedIn, longer for article. "
        f"Context / notes: {notes}"
    )

    draft_text = None
    try:
        # Route through the user's configured provider (Ollama / OpenAI / cloud)
        # like the chat path, not a hard-coded Gemini/Anthropic call.
        system = _get_friday_system_prompt(keywords=title, workspace='content')
        draft_text = _generate_text(
            [{"role": "user", "content": prompt}],
            system=system, max_tokens=1600,
            orb_label="📝 Content Draft", workspace='content',
        )
    except Exception as e:
        print(f"  [FRIDAY] content draft generation error: {e}")

    if not draft_text:
        draft_text = (
            f"[{channel.upper()} DRAFT — {title}]\n\n"
            f"Hook: (one-line opener)\n\n"
            f"Body:\n- Point 1\n- Point 2\n- Point 3\n\n"
            f"Notes: {notes or '(no notes)'}\n\n"
            f"CTA: (single ask)"
        )

    if item is not None:
        item['draft'] = draft_text
        item['updated'] = datetime.now().isoformat()
        if advance and item.get('stage') in CONTENT_STAGES:
            idx = CONTENT_STAGES.index(item['stage'])
            if idx < len(CONTENT_STAGES) - 1:
                item['stage'] = CONTENT_STAGES[idx + 1]
        _save_content_pipeline(pipe)

    return jsonify({
        "status": "ok",
        "id": item_id or None,
        "title": title,
        "channel": channel,
        "draft": draft_text,
        "stage": (item or {}).get('stage'),
    })


@workflows_bp.route('/api/content/templates')
def content_templates():
    """Curated starter templates for the content studio (one-click scaffolds)."""
    return jsonify({"status": "ok", "templates": CONTENT_TEMPLATES})


@workflows_bp.route('/api/content/from-template', methods=['POST'])
def content_from_template():
    """Create a pipeline idea pre-filled from a template."""
    data = request.get_json(silent=True) or {}
    tpl = _content_template((data.get('template_id') or '').strip())
    if not tpl:
        return jsonify({"status": "error", "message": "unknown template"}), 404
    pipe = _load_content_pipeline()
    stamp = datetime.now().isoformat()
    title = (data.get('title') or '').strip() or f"New {tpl['label']}"
    item = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "type": tpl["type"],
        "stage": "idea",
        "channel": tpl["channel"],
        "notes": tpl["scaffold"],
        "tags": [tpl["id"]],
        "template": tpl["id"],
        "created": stamp,
        "updated": stamp,
    }
    pipe.setdefault('items', []).append(item)
    _save_content_pipeline(pipe)
    return jsonify({"status": "ok", "item": item})


@workflows_bp.route('/api/content/item/<item_id>', methods=['POST'])
def content_item_update(item_id):
    """Update a pipeline item in place: draft, title, notes, stage, tags,
    scheduled_for. Used for inline editing, manual stage moves, scheduling."""
    data = request.get_json(silent=True) or {}
    pipe = _load_content_pipeline()
    item = next((it for it in pipe.get('items', []) if it.get('id') == item_id), None)
    if not item:
        return jsonify({"status": "error", "message": "item not found"}), 404
    for field in ('title', 'notes', 'draft', 'channel', 'type', 'scheduled_for'):
        if field in data and data[field] is not None:
            item[field] = data[field]
    if 'stage' in data and data['stage'] in CONTENT_STAGES:
        item['stage'] = data['stage']
    if 'tags' in data and isinstance(data['tags'], list):
        item['tags'] = data['tags']
    item['updated'] = datetime.now().isoformat()
    _save_content_pipeline(pipe)
    return jsonify({"status": "ok", "item": item})


@workflows_bp.route('/api/content/item/<item_id>', methods=['DELETE'])
def content_item_delete(item_id):
    """Remove a pipeline item."""
    pipe = _load_content_pipeline()
    before = len(pipe.get('items', []))
    pipe['items'] = [it for it in pipe.get('items', []) if it.get('id') != item_id]
    if len(pipe['items']) == before:
        return jsonify({"status": "error", "message": "item not found"}), 404
    _save_content_pipeline(pipe)
    return jsonify({"status": "ok", "total": len(pipe['items'])})


@workflows_bp.route('/api/content/item/<item_id>/export', methods=['POST'])
def content_item_export(item_id):
    """Materialize an item's draft as a saved HTML doc so it shows in Saved
    Drafts and can be opened / published."""
    pipe = _load_content_pipeline()
    item = next((it for it in pipe.get('items', []) if it.get('id') == item_id), None)
    if not item:
        return jsonify({"status": "error", "message": "item not found"}), 404
    body = (item.get('draft') or '').strip()
    if not body:
        return jsonify({"status": "error", "message": "nothing to export — draft first"}), 400
    CONTENT_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^a-z0-9]+', '-', (item.get('title') or 'draft').lower()).strip('-')[:50] or 'draft'
    fname = f"{datetime.now():%Y%m%d-%H%M}-{safe}.html"
    esc = html.escape(body)
    doc = (
        f"<!doctype html><meta charset='utf-8'><title>{html.escape(item.get('title') or 'Draft')}</title>"
        f"<style>body{{font:16px/1.6 -apple-system,Segoe UI,Inter,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;color:#1a1a2e}}"
        f"h1{{font-size:22px}}.meta{{color:#888;font-size:13px;margin-bottom:20px}}pre{{white-space:pre-wrap;font-family:inherit}}</style>"
        f"<h1>{html.escape(item.get('title') or 'Draft')}</h1>"
        f"<div class='meta'>{html.escape(item.get('type') or '')} · {html.escape(item.get('channel') or '')} · "
        f"{datetime.now():%Y-%m-%d %H:%M}</div><pre>{esc}</pre>"
    )
    (CONTENT_DRAFTS_DIR / fname).write_text(doc, encoding='utf-8')
    return jsonify({"status": "ok", "filename": fname})


# ═══ TASK-CHAINING WORKFLOWS ══════════════════════════════════
# A chain is an ordered list of background-task steps where each step's output
# feeds the next. Definitions live in ~/.friday/workflows/ as JSON.
@workflows_bp.route('/api/workflows/chains', methods=['GET'])
def workflow_chains_list():
    return jsonify({"status": "ok", "chains": list_workflow_chains()})


@workflows_bp.route('/api/workflows/chains', methods=['POST'])
def workflow_chains_create():
    """Create/update a chain. Body: {name, description?, steps:[{name?, prompt,
    with_context?}, ...]}."""
    data = request.get_json(silent=True) or {}
    try:
        stored = save_workflow_chain(data)
        return jsonify({"status": "ok", "chain": stored})
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@workflows_bp.route('/api/workflows/chains/<name>', methods=['GET'])
def workflow_chain_get(name):
    chain = load_workflow_chain(name)
    if not chain:
        return jsonify({"status": "not_found"}), 404
    return jsonify({"status": "ok", "chain": chain})


@workflows_bp.route('/api/workflows/chains/<name>', methods=['DELETE'])
def workflow_chain_delete(name):
    ok = delete_workflow_chain(name)
    return jsonify({"status": "ok" if ok else "not_found"}), (200 if ok else 404)


@workflows_bp.route('/api/workflows/chains/<name>/run', methods=['POST'])
def workflow_chain_run(name):
    """Kick off a chain at step 0. Each step auto-advances on completion."""
    chain = load_workflow_chain(name)
    if not chain:
        return jsonify({"status": "error", "message": "Unknown chain"}), 404
    tid = run_workflow_chain(name)
    if not tid:
        return jsonify({"status": "error", "message": "Chain has no runnable steps"}), 400
    return jsonify({
        "status": "ok",
        "chain": chain.get('name'),
        "task_id": tid,
        "steps": len(chain.get('steps') or []),
        "message": f"Chain '{chain.get('name')}' started — watch the Task Tray.",
    })
