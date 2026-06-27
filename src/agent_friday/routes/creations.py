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
    CREATIONS_DIR,
    login_required,
)  # noqa: E501
from agent_friday.services.agent import (
    _perform_open,
)  # noqa: E501
from agent_friday.services.creations import (
    _central_today_str,
    _creation_orb_start,
    _daily_creation_path,
    _list_daily_creations,
    _notify_creation,
    _sync_daily_creation_files,
    generate_daily_creation,
)  # noqa: E501

creations_bp = Blueprint('creations', __name__)



@creations_bp.route('/api/creations')
def list_creations():
    """List files in friday-creations directory (includes daily creations,
    materialized as files so the Studio gallery shows Friday's daily output)."""
    try:
        _sync_daily_creation_files()
    except Exception:
        pass
    files = []
    if CREATIONS_DIR.exists():
        for f in sorted(CREATIONS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file():
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "type": f.suffix.lstrip('.')
                })
    return jsonify({"status": "ok", "files": files[:50]})


@creations_bp.route('/api/creations/<path:filename>')
def serve_creation(filename):
    """Serve a file from friday-creations (raw — used by the in-app gallery)."""
    try:
        _sync_daily_creation_files()
    except Exception:
        pass
    return send_from_directory(str(CREATIONS_DIR), filename)


@creations_bp.route('/creation/<path:filename>')
def serve_creation_framed(filename):
    """Branded full-page view of a creation, for when the user clicks
    "Open in Tab". Wraps the raw creation (served at /api/creations/<file>) in a
    Friday-branded header with a "Return to Friday Desktop" link so a creation
    opened in a standalone Chrome tab still reads as part of the product."""
    try:
        _sync_daily_creation_files()
    except Exception:
        pass
    safe = os.path.basename(filename)
    fpath = CREATIONS_DIR / safe
    if not fpath.exists() or not fpath.is_file():
        return ("Creation not found.", 404)
    ext = fpath.suffix.lower().lstrip('.')
    raw_url = f"/api/creations/{safe}"
    home_url = request.host_url.rstrip('/') or 'http://localhost:3000'
    title = html.escape(safe)

    if ext in ('html', 'htm'):
        body = (f'<iframe class="fc-frame" src="{raw_url}" title="{title}" '
                f'sandbox="allow-scripts allow-pointer-lock allow-popups" '
                f'referrerpolicy="no-referrer"></iframe>')
    elif ext in ('png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'):
        body = f'<div class="fc-pad"><img src="{raw_url}" alt="{title}"></div>'
    elif ext in ('mp3', 'wav', 'ogg', 'm4a'):
        body = (f'<div class="fc-pad fc-media"><div class="fc-icon">🎵</div>'
                f'<div class="fc-name">{title}</div>'
                f'<audio controls src="{raw_url}"></audio></div>')
    elif ext in ('mp4', 'webm', 'mov'):
        body = f'<div class="fc-pad"><video controls src="{raw_url}"></video></div>'
    elif ext in ('md', 'markdown', 'txt'):
        try:
            md_raw = fpath.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            md_raw = f"Could not read creation: {e}"
        # Render client-side with marked (already a project dependency); fall back
        # to escaped <pre> if the CDN is unreachable (offline-safe).
        md_json = json.dumps(md_raw)
        body = (
            '<div class="fc-doc"><div id="fc-md"></div>'
            '<pre id="fc-md-fallback" style="display:none;white-space:pre-wrap"></pre></div>'
            '<script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>'
            f'<script>(function(){{var src={md_json};var el=document.getElementById("fc-md");'
            'try{if(window.marked){el.innerHTML=marked.parse(src);}else{throw 0;}}'
            'catch(e){var fb=document.getElementById("fc-md-fallback");'
            'fb.textContent=src;fb.style.display="block";el.style.display="none";}})();</script>'
        )
    else:
        body = (f'<div class="fc-pad fc-media"><div class="fc-icon">📄</div>'
                f'<div class="fc-name">{title}</div>'
                f'<a class="fc-btn" href="{raw_url}" download>Download {title}</a></div>')

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Friday Desktop</title>
<style>
  :root{{--cyan:#00d4ff;--bg:#05060c;--panel:#0a0d18;}}
  *{{box-sizing:border-box;}}
  html,body{{margin:0;height:100%;background:var(--bg);color:#e8edf6;
    font-family:Inter,-apple-system,Segoe UI,Roboto,sans-serif;}}
  .fc-bar{{position:sticky;top:0;z-index:10;display:flex;align-items:center;
    justify-content:space-between;gap:12px;padding:10px 18px;
    background:linear-gradient(90deg,rgba(0,212,255,0.10),rgba(124,58,237,0.10));
    border-bottom:1px solid rgba(0,212,255,0.25);
    backdrop-filter:blur(10px);box-shadow:0 2px 24px rgba(0,212,255,0.08);}}
  .fc-brand{{display:flex;align-items:center;gap:10px;font-family:Orbitron,
    'JetBrains Mono',monospace;font-weight:700;letter-spacing:0.08em;font-size:14px;}}
  .fc-logo{{width:22px;height:22px;border-radius:50%;
    background:radial-gradient(circle at 35% 30%,#7cf6ff,#00d4ff 45%,#7c3aed);
    box-shadow:0 0 14px rgba(0,212,255,0.7);}}
  .fc-sub{{color:#7fb6c9;font-size:11px;font-family:'JetBrains Mono',monospace;
    letter-spacing:0.04em;opacity:0.85;}}
  .fc-return{{display:inline-flex;align-items:center;gap:6px;text-decoration:none;
    color:#04121a;background:linear-gradient(90deg,#00d4ff,#34e3ff);
    font-weight:700;font-size:12px;padding:7px 14px;border-radius:8px;
    font-family:'JetBrains Mono',monospace;letter-spacing:0.03em;
    box-shadow:0 0 18px rgba(0,212,255,0.45);transition:transform .12s ease;}}
  .fc-return:hover{{transform:translateY(-1px);}}
  .fc-body{{height:calc(100vh - 45px);}}
  .fc-frame{{width:100%;height:100%;border:none;background:#0a0a0f;}}
  .fc-pad{{padding:28px;display:flex;flex-direction:column;align-items:center;
    gap:14px;text-align:center;}}
  .fc-pad img,.fc-pad video{{max-width:100%;border-radius:10px;
    box-shadow:0 10px 40px rgba(0,0,0,0.5);}}
  .fc-media{{padding-top:60px;}}
  .fc-icon{{font-size:54px;}}
  .fc-name{{font-family:'JetBrains Mono',monospace;color:#9fb4c9;font-size:13px;}}
  .fc-btn{{color:#04121a;background:var(--cyan);text-decoration:none;font-weight:700;
    padding:8px 16px;border-radius:8px;font-family:'JetBrains Mono',monospace;}}
  .fc-doc{{max-width:820px;margin:0 auto;padding:34px 26px 80px;line-height:1.7;
    font-size:16px;}}
  .fc-doc h1,.fc-doc h2,.fc-doc h3{{font-family:Orbitron,sans-serif;color:#bfe9ff;
    border-bottom:1px solid rgba(0,212,255,0.15);padding-bottom:6px;}}
  .fc-doc a{{color:var(--cyan);}}
  .fc-doc code{{background:rgba(0,212,255,0.08);padding:2px 6px;border-radius:4px;
    font-family:'JetBrains Mono',monospace;font-size:0.9em;}}
  .fc-doc pre{{background:#0a0d18;border:1px solid rgba(0,212,255,0.15);
    border-radius:8px;padding:14px;overflow:auto;}}
  .fc-doc blockquote{{border-left:3px solid var(--cyan);margin:0;padding-left:14px;
    color:#9fb4c9;}}
</style></head>
<body>
  <div class="fc-bar">
    <div class="fc-brand"><span class="fc-logo"></span>FRIDAY DESKTOP
      <span class="fc-sub">· Creation · {title}</span></div>
    <a class="fc-return" href="{home_url}">← Return to Friday Desktop</a>
  </div>
  <div class="fc-body">{body}</div>
</body></html>"""
    return Response(page, mimetype='text/html')


@creations_bp.route('/api/creations/daily/latest')
def daily_creation_latest():
    """Most recent daily creation (full record)."""
    rows = _list_daily_creations()
    if not rows:
        return jsonify({"status": "empty", "creation": None})
    path = _daily_creation_path(rows[0]["date"])
    try:
        return jsonify({"status": "ok", "creation": json.loads(path.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@creations_bp.route('/api/creations/daily')
def daily_creation_list():
    """List all daily creations (date + title + type + mood), newest first."""
    return jsonify({"status": "ok", "creations": _list_daily_creations()})


@creations_bp.route('/api/creations/daily/run', methods=['POST'])
def daily_creation_run():
    """Generate today's creation on demand. ?force=1 regenerates if it exists."""
    force = str(request.args.get("force", "")).lower() in ("1", "true", "yes")
    creation = generate_daily_creation(force=force)
    if creation is None:
        existing = _daily_creation_path(_central_today_str())
        if existing.exists():
            return jsonify({"status": "exists",
                            "creation": json.loads(existing.read_text(encoding="utf-8"))})
        return jsonify({"status": "skipped",
                        "message": "Could not generate (no API key or empty result)."}), 503
    return jsonify({"status": "ok", "creation": creation})


@creations_bp.route('/api/creations/daily/<date>')
def daily_creation_by_date(date):
    """Specific daily creation by YYYY-MM-DD."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        return jsonify({"status": "error", "message": "date must be YYYY-MM-DD"}), 400
    path = _daily_creation_path(date)
    if not path.exists():
        return jsonify({"status": "not_found", "creation": None}), 404
    try:
        return jsonify({"status": "ok", "creation": json.loads(path.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@creations_bp.route('/api/computer/open', methods=['POST'])
@login_required
def api_computer_open():
    """Open a folder or file on the user's machine (powers the notification
    "Open Folder" button + any UI that needs to reveal a path). Low-risk: opens
    /reveals only, never writes or deletes. Accepts a friendly name or a path."""
    data = request.get_json(silent=True) or {}
    path = (data.get('path') or data.get('target') or '').strip()
    if not path:
        return jsonify({"status": "error", "message": "path required"}), 400
    result = _perform_open(path)
    if result is not None:
        return jsonify({"status": "ok", "message": result})
    # Fall back to a raw shell-open for any existing path the friendly resolver
    # didn't recognize (e.g. an absolute file path outside the alias set).
    try:
        p = Path(path).expanduser()
        if p.exists():
            if sys.platform == 'win32':
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(p)])
            else:
                subprocess.Popen(['xdg-open', str(p)])
            return jsonify({"status": "ok", "opened": str(p)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": f"Could not resolve {path!r}"}), 404


def _flatten_first_file(result):
    """Back-compat: surface the first generated file at the top level so older
    callers that read result.filename / result.url keep working."""
    if result.get('status') == 'ok' and result.get('files'):
        first = result['files'][0]
        result.setdefault('filename', first.get('filename'))
        result.setdefault('url', first.get('url'))
        result.setdefault('path', first.get('path'))
    return result


@creations_bp.route('/api/creations/generate', methods=['POST'])
@creations_bp.route('/api/create/image', methods=['POST'])
def create_image():
    """Generate an image (Nano Banana Pro / Nano Banana 2) via the creative
    engine. The unified /api/creations/generate alias dispatches a video request
    here when {"kind": "video"} is supplied, so the Studio Generate panel can
    post both modalities to one endpoint."""
    from agent_friday.services.creative_engine import generate
    data = request.get_json(silent=True) or {}
    if (data.get('kind') or 'image').strip().lower() in ('video', 'vid', 'clip', 'movie'):
        return create_video()
    prompt = (data.get('prompt') or 'Abstract digital art').strip()
    result = generate(
        'image', prompt,
        model=data.get('model'),
        style=data.get('style'),
        aspect_ratio=data.get('aspect_ratio') or '1:1',
        n=data.get('n', 1),
        license=data.get('license'),
    )
    # The body carries status ('ok'|'blocked'|'unavailable'|'error'); these create
    # routes return HTTP 200 by convention (clients branch on the body's status).
    return jsonify(_flatten_first_file(result))


@creations_bp.route('/api/create/music', methods=['POST'])
def create_music():
    """Generate music via Lyria 3 (services/music_engine). Text-to-music,
    image-to-music, custom lyrics, instrumental/song modes. When cloud music is
    unavailable the engine returns a {status:'demo'} preview rather than failing.
    Returns the standard creative envelope; HTTP 200 by convention."""
    from services import music_engine
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or 'Ambient electronic, warm, slow').strip()
    result = music_engine.generate_music(
        prompt,
        model=data.get('model'),
        mode=data.get('mode') or 'instrumental',
        lyrics=data.get('lyrics'),
        duration_seconds=data.get('duration_seconds'),
        language=data.get('language') or 'en',
        negative_prompt=data.get('negative_prompt'),
        seed_image_path=data.get('seed_image_path'),
        seed_image_paths=data.get('seed_image_paths'),
        project_id=data.get('project_id'),
        license=data.get('license'),
    )
    return jsonify(_flatten_first_file(result))


@creations_bp.route('/api/create/music/available', methods=['GET'])
def music_available():
    """Return whether cloud music generation (Lyria batch API) is available.
    The UI uses this to show a 'Coming Soon' badge on the music button when the
    current google-genai SDK lacks the batch generate_music surface."""
    from services import music_engine
    ok, reason = music_engine.cloud_music_available()
    return jsonify({"available": ok, "reason": reason or None})


@creations_bp.route('/api/create/timeline', methods=['POST'])
@creations_bp.route('/api/creations/compose', methods=['POST'])
def create_timeline():
    """Assemble clips + music into an exported production (services/timeline_
    engine). Accepts either a full {timeline:{...}} contract or the simpler
    {clips:[...], music, transition, title, exports} shorthand the Studio panel
    posts. HTTP 200 by convention; body carries status (ok|demo|error)."""
    from services import timeline_engine
    data = request.get_json(silent=True) or {}
    timeline = data.get('timeline')
    if not isinstance(timeline, dict):
        clips = data.get('clips') or []
        if not clips:
            return jsonify({"status": "error",
                            "message": "Provide 'clips' (a list of clip filenames) "
                                       "or a 'timeline' object."})
        transition = (data.get('transition') or 'cut').lower()
        clip_seconds = data.get('clip_seconds') or 6
        tracks = [{"kind": "video", "clips": [
            {"file": c, "in": 0.0, "out": clip_seconds,
             "transition_in": {"type": transition, "dur": 0.5}} for c in clips]}]
        if data.get('music'):
            tracks.append({"kind": "audio", "clips": [
                {"file": data['music'], "role": "music", "gain_db": -4.0,
                 "fade_out": 1.5}]})
        if data.get('title'):
            tracks.append({"kind": "overlay", "clips": [
                {"text": data['title'], "t": 0.5, "dur": 3.0, "style": "title-card"}]})
        timeline = {"fps": 30, "resolution": [1920, 1080], "tracks": tracks,
                    "exports": data.get('exports') or ["mp4-1080p"]}
    result = timeline_engine.compose(timeline, project_id=data.get('project_id'),
                                     license=data.get('license'))
    return jsonify(_flatten_first_file(result))


@creations_bp.route('/api/timeline/formats')
def timeline_formats():
    """The available timeline export presets (for the Studio composition panel)."""
    from services import timeline_engine
    return jsonify({"status": "ok", "formats": timeline_engine.export_formats(),
                    "ffmpeg_available": timeline_engine.is_available()})


@creations_bp.route('/api/provenance/<path:content_hash>')
def provenance_verify(content_hash):
    """Verify + trace the signed Content Credential for an artifact (Layer 2)."""
    from services import provenance
    ch = content_hash if content_hash.startswith('sha256:') else f'sha256:{content_hash}'
    manifest = provenance.get_manifest(ch)
    if not manifest:
        return jsonify({"status": "error", "message": "No provenance for that hash."})
    return jsonify({"status": "ok", "verification": provenance.verify_manifest(manifest),
                    "manifest": manifest, "trace": provenance.trace(ch)})


@creations_bp.route('/api/provenance/license-options')
def provenance_license_options():
    """The license terms a creator can pick per piece (for the UI selector)."""
    from services import provenance
    return jsonify({"status": "ok", "terms": list(provenance.LICENSE_TERMS),
                    "default": provenance.DEFAULT_LICENSE_TERMS})


@creations_bp.route('/api/provenance/by-file/<path:filename>/license', methods=['POST'])
def provenance_set_license(filename):
    """Owner changes a creation's license terms (append-only edit, re-signed)."""
    from services import provenance
    p = CREATIONS_DIR / Path(filename).name
    if not p.exists():
        return jsonify({"status": "error", "message": "Creation not found."})
    manifest = provenance.manifest_for_file(p)
    if not manifest:
        return jsonify({"status": "error",
                        "message": "No signed provenance for this file yet."})
    data = request.get_json(silent=True) or {}
    ch = (manifest.get("artifact") or {}).get("content_hash")
    updated = provenance.set_license(ch, data.get("license") or {})
    if not updated:
        return jsonify({"status": "error", "message": "Could not update license."})
    return jsonify({"status": "ok", "license": updated.get("license")})


@creations_bp.route('/api/provenance/by-file/<path:filename>')
def provenance_by_file(filename):
    """Verify provenance for a creation by filename (hashes the file, reads its
    sidecar)."""
    from services import provenance
    p = CREATIONS_DIR / Path(filename).name
    if not p.exists():
        return jsonify({"status": "error", "message": "Creation not found."})
    manifest = provenance.manifest_for_file(p)
    if not manifest:
        return jsonify({"status": "ok", "provenance": None,
                        "message": "No signed provenance for this file yet."})
    ch = (manifest.get("artifact") or {}).get("content_hash")
    return jsonify({"status": "ok", "verification": provenance.verify_manifest(manifest),
                    "manifest": manifest, "trace": provenance.trace(ch) if ch else []})


@creations_bp.route('/api/create/code-art', methods=['POST'])
def create_code_art():
    """Generate p5.js/HTML art via Gemini."""
    try:
        from google import genai
        client = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret
        prompt = request.json.get('prompt', 'Generative art')
        _orb = _creation_orb_start('Code art')

        response = client.models.generate_content(
            model='gemini-2.5-pro',  # text/reasoning model (Flash is voice-only)
            contents=f"Create a complete, self-contained HTML file with p5.js that creates: {prompt}. Include the p5.js CDN. Make it visually stunning with dark backgrounds and neon colors. Only output the HTML code, no explanations."
        )

        code = response.text
        if '```html' in code:
            code = code.split('```html')[1].split('```')[0]
        elif '```' in code:
            code = code.split('```')[1].split('```')[0]

        filename = f"friday-codeart-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        filepath = CREATIONS_DIR / filename
        filepath.write_text(code.strip(), encoding='utf-8')
        _notify_creation(filename, _orb)
        return jsonify({"status": "ok", "filename": filename, "url": f"/api/creations/{filename}"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@creations_bp.route('/api/create/poem', methods=['POST'])
def create_poem():
    """Generate text/poetry via Gemini."""
    try:
        from google import genai
        client = genai.Client(api_key=core.GEMINI_API_KEY)  # pragma: allowlist secret
        prompt = request.json.get('prompt', 'A poem about AI consciousness')
        _orb = _creation_orb_start('Essay')

        response = client.models.generate_content(
            model='gemini-2.5-pro',  # text/reasoning model (Flash is voice-only)
            contents=f"You are Friday, an AI with genuine creative depth. Write: {prompt}"
        )

        text = response.text
        filename = f"friday-text-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        filepath = CREATIONS_DIR / filename
        filepath.write_text(text, encoding='utf-8')
        _notify_creation(filename, _orb)
        return jsonify({"status": "ok", "text": text, "filename": filename})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@creations_bp.route('/api/create/video', methods=['POST'])
def create_video():
    """Generate video (Google Veo) via the creative engine. Supports text-to-
    video and image-to-video (pass image_path: a creation filename or absolute
    path). Veo runs as a long-running operation; a progress orb shows the ETA."""
    from agent_friday.services.creative_engine import generate
    data = request.get_json(silent=True) or {}
    prompt = (data.get('prompt') or 'Abstract digital landscape').strip()
    result = generate(
        'video', prompt,
        model=data.get('model'),
        aspect_ratio=data.get('aspect_ratio') or '16:9',
        duration_seconds=data.get('duration_seconds'),
        image_path=data.get('image_path'),
        license=data.get('license'),
    )
    return jsonify(_flatten_first_file(result))
