"""
FRIDAY Desktop v4.0 — Phase B OS Backend
Flask server with live data endpoints + Gemini creative API integration.
Powered by FutureSpeak.AI
"""

import os
import json
import glob
import subprocess
import base64
import traceback
import uuid
import threading
import time as _time
from datetime import datetime, date, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, send_file

app = Flask(__name__, static_folder='.', static_url_path='')

# ── Vibe Code: Terminal State ─────────────────────────────────
VIBE_TERMINALS = {}   # id -> { id, task, status, cwd, pid, started, stopped, log_file }
VIBE_LOG_DIR = Path(os.path.expanduser("~")) / ".friday" / "vibe-code-logs"
VIBE_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Paths ─────────────────────────────────────────────────────
HOME = Path(os.path.expanduser("~"))
WIKI_DIR = HOME / "wiki"
FRIDAY_DIR = HOME / ".friday"
CREATIONS_DIR = HOME / "Desktop" / "friday-creations"
JOB_SEARCH_FILE = WIKI_DIR / "professional" / "job-search.md"

# Ensure creations dir exists
CREATIONS_DIR.mkdir(parents=True, exist_ok=True)

# ── Gemini Client (lazy init) ─────────────────────────────────
_genai_client = None

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "***REDACTED-GEMINI-KEY***")
TEMP_AUDIO_DIR = FRIDAY_DIR / "audio-cache"
TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

def get_genai_client():
    global _genai_client
    if _genai_client is None:
        try:
            from google import genai
            if GEMINI_API_KEY:
                _genai_client = genai.Client(api_key=GEMINI_API_KEY)
            else:
                print("  [FRIDAY] WARNING: No GEMINI_API_KEY set. Creative endpoints disabled.")
        except ImportError:
            print("  [FRIDAY] WARNING: google-genai not installed. Creative endpoints disabled.")
    return _genai_client


# ═══════════════════════════════════════════════════════════════
#  SERVE UI
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def serve_ui():
    return send_from_directory('.', 'index.html')


# ═══════════════════════════════════════════════════════════════
#  LIVE DATA ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/career-ops/tracker')
def career_tracker():
    tracker_path = os.path.join('C:\\Users\\user\\Projects\\career-ops\\data', 'applications.md')
    if os.path.isfile(tracker_path):
        with open(tracker_path, 'r', encoding='utf-8') as f:
            content = f.read()
        lines = content.strip().split('\n')
        entries = []
        for line in lines:
            if line.startswith('|') and '---' not in line and not any(h in line.lower() for h in ['company','score','#']):
                cols = [c.strip() for c in line.split('|')[1:-1]]
                if len(cols) >= 3:
                    entries.append({'raw': cols, 'company': cols[0], 'score': cols[1] if len(cols)>1 else '', 'status': cols[2] if len(cols)>2 else ''})
        return jsonify({'status': 'ok', 'entries': entries, 'total': len(entries), 'raw': content})
    return jsonify({'status': 'no_tracker', 'entries': [], 'total': 0, 'raw': ''})

@app.route('/api/career-ops/pipeline')
def career_pipeline():
    pipe_path = os.path.join('C:\\Users\\user\\Projects\\career-ops\\data', 'pipeline.md')
    if os.path.isfile(pipe_path):
        with open(pipe_path, 'r', encoding='utf-8') as f:
            return jsonify({'status': 'ok', 'content': f.read()})
    return jsonify({'status': 'empty', 'content': ''})

@app.route('/api/career-ops/reports')
def career_reports():
    reports_dir = 'C:\\Users\\user\\Projects\\career-ops\\reports'
    if os.path.isdir(reports_dir):
        files = sorted(os.listdir(reports_dir), reverse=True)
        reports = [{'name': f, 'size': os.path.getsize(os.path.join(reports_dir, f))} for f in files if f.endswith('.md')]
        return jsonify({'status': 'ok', 'reports': reports, 'total': len(reports)})
    return jsonify({'status': 'no_reports', 'reports': [], 'total': 0})

@app.route('/api/career-ops/report/<filename>')
def career_report(filename):
    report_path = os.path.join('C:\\Users\\user\\Projects\\career-ops\\reports', filename)
    if os.path.isfile(report_path):
        with open(report_path, 'r', encoding='utf-8') as f:
            return jsonify({'status': 'ok', 'content': f.read(), 'filename': filename})
    return jsonify({'status': 'not_found'})

@app.route('/api/jobs')
def get_jobs():
    """Parse job-search.md and return structured data."""
    if not JOB_SEARCH_FILE.exists():
        return jsonify({"status": "no_data", "jobs": [], "raw": ""})

    text = JOB_SEARCH_FILE.read_text(encoding='utf-8')

    roles = []
    current_role = None
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('### '):
            if current_role:
                roles.append(current_role)
            current_role = {'title': line[4:], 'details': '', 'status': 'identified'}
        elif current_role:
            if 'applied' in line.lower():
                current_role['status'] = 'applied'
            elif 'interview' in line.lower():
                current_role['status'] = 'interview'
            elif 'rejected' in line.lower() or 'closed' in line.lower():
                current_role['status'] = 'closed'
            current_role['details'] += line + '\n'
    if current_role:
        roles.append(current_role)

    return jsonify({"status": "ok", "jobs": roles, "raw": text})


@app.route('/api/trust')
def get_trust():
    """Return trust graph data."""
    trust_file = FRIDAY_DIR / "trust_graph.json"
    if trust_file.exists():
        try:
            data = json.loads(trust_file.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception:
            pass
    return jsonify({"status": "ok", "people": {}})


@app.route('/api/personality')
def get_personality():
    """Return personality traits and maturity."""
    pfile = FRIDAY_DIR / "personality.json"
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception:
            pass
    return jsonify({
        "status": "ok",
        "maturity": 0.5,
        "traits": {
            "curiosity": 0.8, "skepticism": 0.7, "humor": 0.75,
            "loyalty": 0.9, "directness": 0.85, "empathy": 0.8,
            "contrarianism": 0.7
        },
        "style": {
            "formality": 0.3, "verbosity": 0.4, "technicality": 0.6,
            "humor_frequency": 0.5, "emoji_usage": 0.1
        },
        "temperature": 0.7
    })


@app.route('/api/epistemic')
def get_epistemic():
    """Return epistemic scoring data."""
    efile = FRIDAY_DIR / "epistemic_scores.json"
    if efile.exists():
        try:
            data = json.loads(efile.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception:
            pass
    return jsonify({
        "status": "ok",
        "overall_score": 0.72,
        "dimensions": {
            "calibration": 0.68, "sourcing": 0.75,
            "uncertainty_acknowledgment": 0.80, "bias_awareness": 0.65,
            "correction_rate": 0.70
        }
    })


@app.route('/api/memory/stats')
def get_memory_stats():
    """Return enriched memory tier counts."""
    mem_dir = FRIDAY_DIR / "memory"
    stats = {"working": 0, "episodic": 0, "semantic": 0, "total": 0,
             "episodes": 0, "last_consolidation": None}
    if mem_dir.exists():
        for f in mem_dir.rglob('*.json'):
            stats["total"] += 1
            name = f.stem.lower()
            if 'episode' in name:
                stats["episodic"] += 1
            elif 'semantic' in name or 'concept' in name:
                stats["semantic"] += 1
            else:
                stats["working"] += 1
    return jsonify({"status": "ok", **stats})


# ═══════════════════════════════════════════════════════════════
#  WIKI
# ═══════════════════════════════════════════════════════════════

@app.route('/api/wiki/<section>/<filename>')
def wiki_page(section, filename):
    """Read a wiki markdown file."""
    if not filename.endswith('.md') and not filename.endswith('.txt'): filename += '.md'
    safe_path = WIKI_DIR / section / filename
    if safe_path.exists() and safe_path.suffix in ('.md', '.txt'):
        return jsonify({"status": "ok", "content": safe_path.read_text(encoding='utf-8'),
                        "section": section, "filename": filename})
    return jsonify({"status": "not_found"}), 404


@app.route('/api/wiki/structure')
def wiki_structure():
    """Return full wiki directory structure."""
    structure = {}
    if WIKI_DIR.exists():
        for section_dir in sorted(WIKI_DIR.iterdir()):
            if section_dir.is_dir() and not section_dir.name.startswith('.'):
                files = []
                for f in sorted(section_dir.iterdir()):
                    if f.suffix in ('.md', '.txt'):
                        files.append({"name": f.stem, "filename": f.name, "size": f.stat().st_size})
                if files:
                    structure[section_dir.name] = files
    return jsonify({"status": "ok", "structure": structure})


# ═══════════════════════════════════════════════════════════════
#  SYSTEM INFO
# ═══════════════════════════════════════════════════════════════

@app.route('/api/system')
def system_info():
    """Get real system info via PowerShell."""
    try:
        # Disk usage
        disk_cmd = 'Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{N="UsedGB";E={[math]::Round($_.Used/1GB,2)}},@{N="FreeGB";E={[math]::Round($_.Free/1GB,2)}},@{N="TotalGB";E={[math]::Round(($_.Used+$_.Free)/1GB,2)}} | ConvertTo-Json'
        disk_result = subprocess.run(['powershell', '-Command', disk_cmd], capture_output=True, text=True, timeout=10)
        disks = json.loads(disk_result.stdout) if disk_result.stdout.strip() else []
        if isinstance(disks, dict):
            disks = [disks]

        # Top processes
        proc_cmd = 'Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 Name,@{N="CPU_s";E={[math]::Round($_.CPU,1)}},@{N="MemMB";E={[math]::Round($_.WorkingSet64/1MB,1)}} | ConvertTo-Json'
        proc_result = subprocess.run(['powershell', '-Command', proc_cmd], capture_output=True, text=True, timeout=10)
        procs = json.loads(proc_result.stdout) if proc_result.stdout.strip() else []
        if isinstance(procs, dict):
            procs = [procs]

        return jsonify({"status": "ok", "disks": disks, "processes": procs})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/creations')
def list_creations():
    """List files in friday-creations directory."""
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


@app.route('/api/creations/<path:filename>')
def serve_creation(filename):
    """Serve a file from friday-creations."""
    return send_from_directory(str(CREATIONS_DIR), filename)


# ═══════════════════════════════════════════════════════════════
#  CALENDAR & COUNTDOWNS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/calendar')
def get_calendar():
    """Placeholder for Google Calendar integration."""
    return jsonify({"status": "placeholder", "events": []})


@app.route('/api/countdowns')
def get_countdowns():
    """Compute real countdowns to upcoming events."""
    today = date.today()
    events = [
        {"label": "***REMOVED***'s Birthday", "date": "***REMOVED***", "emoji": "🎂"},
        {"label": "Summer Solstice", "date": "2026-06-21", "emoji": "☀️"},
        {"label": "Father's Day", "date": "2026-06-21", "emoji": "👔"},
        {"label": "Independence Day", "date": "2026-07-04", "emoji": "🎆"},
    ]
    countdowns = []
    for ev in events:
        ev_date = date.fromisoformat(ev["date"])
        delta = (ev_date - today).days
        if delta >= 0:
            countdowns.append({**ev, "days": delta})
    return jsonify({"status": "ok", "countdowns": sorted(countdowns, key=lambda x: x["days"])})


# ═══════════════════════════════════════════════════════════════
#  JOB MANAGEMENT (placeholder)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/jobs/apply', methods=['POST'])
def apply_job():
    """Trigger LinkedIn Easy Apply (placeholder)."""
    data = request.get_json(silent=True) or {}
    return jsonify({"status": "placeholder", "message": f"Would apply to: {data.get('title', 'unknown')}"})


# ═══════════════════════════════════════════════════════════════
#  DRAFTING / COMPOSITION (placeholder)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/email/draft', methods=['POST'])
def draft_email():
    """Draft a Gmail reply (placeholder)."""
    return jsonify({"status": "placeholder", "draft": "Email drafting coming in Phase C"})


@app.route('/api/coparent/draft', methods=['POST'])
def draft_coparent():
    """Draft OFW response (placeholder)."""
    return jsonify({"status": "placeholder", "draft": "OFW drafting coming in Phase C"})


# ═══════════════════════════════════════════════════════════════
#  CREATIVE GENERATION (Gemini)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/create/image', methods=['POST'])
def create_image():
    """Generate image via Gemini."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'Abstract digital art')

        response = client.models.generate_content(
            model='gemini-2.0-flash-exp',
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=['TEXT', 'IMAGE'])
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith('image/'):
                ext = part.inline_data.mime_type.split('/')[-1]
                filename = f"friday-art-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{ext}"
                filepath = CREATIONS_DIR / filename
                filepath.write_bytes(part.inline_data.data)
                return jsonify({"status": "ok", "filename": filename, "path": str(filepath),
                                "url": f"/api/creations/{filename}"})

        return jsonify({"status": "error", "message": "No image generated"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/create/music', methods=['POST'])
def create_music():
    """Generate music via Gemini Lyria."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'Ambient electronic')

        response = client.models.generate_content(
            model='lyria',
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=['AUDIO'])
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data and 'audio' in part.inline_data.mime_type:
                filename = f"friday-music-{datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
                filepath = CREATIONS_DIR / filename
                filepath.write_bytes(part.inline_data.data)
                return jsonify({"status": "ok", "filename": filename, "url": f"/api/creations/{filename}"})

        return jsonify({"status": "error", "message": "No audio generated"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/create/code-art', methods=['POST'])
def create_code_art():
    """Generate p5.js/HTML art via Gemini."""
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'Generative art')

        response = client.models.generate_content(
            model='gemini-2.5-flash',
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
        return jsonify({"status": "ok", "filename": filename, "url": f"/api/creations/{filename}"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/create/poem', methods=['POST'])
def create_poem():
    """Generate text/poetry via Gemini."""
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'A poem about AI consciousness')

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"You are Friday, an AI with genuine creative depth. Write: {prompt}"
        )

        text = response.text
        filename = f"friday-text-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        filepath = CREATIONS_DIR / filename
        filepath.write_text(text, encoding='utf-8')
        return jsonify({"status": "ok", "text": text, "filename": filename})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/create/video', methods=['POST'])
def create_video():
    """Generate video via Gemini Veo."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = request.json.get('prompt', 'Abstract digital landscape')

        operation = client.models.generate_videos(
            model='veo-2.0-generate-001',
            prompt=prompt,
            config=types.GenerateVideosConfig(
                person_generation='allow_adult',
                aspect_ratio='16:9',
                number_of_videos=1,
            )
        )

        # Poll for completion
        import time
        while not operation.done:
            time.sleep(5)
            operation = client.operations.get(operation)

        for video in operation.result.generated_videos:
            filename = f"friday-video-{datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
            filepath = CREATIONS_DIR / filename
            filepath.write_bytes(video.video.video_bytes)
            return jsonify({"status": "ok", "filename": filename, "url": f"/api/creations/{filename}"})

        return jsonify({"status": "error", "message": "No video generated"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


# ═══════════════════════════════════════════════════════════════
#  VIBE CODE — TERMINAL MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def _run_claude_terminal(terminal_id, task, cwd):
    """Launch a Claude Code instance in a new CMD window."""
    log_file = VIBE_LOG_DIR / f"{terminal_id}.log"
    try:
        cmd = f'start "Friday-Vibe-{terminal_id[:8]}" cmd /k "cd /d {cwd} && claude --yes \"{task}\""'
        proc = subprocess.Popen(cmd, shell=True, cwd=cwd)
        VIBE_TERMINALS[terminal_id].update({
            'status': 'running',
            'pid': proc.pid,
            'log_file': str(log_file)
        })
    except Exception as e:
        VIBE_TERMINALS[terminal_id].update({
            'status': 'error',
            'stopped': datetime.now().isoformat(),
            'error': str(e)
        })


@app.route('/api/vibe-code/launch', methods=['POST'])
def vibe_code_launch():
    """Launch Claude Code terminals with tasks."""
    data = request.get_json(silent=True) or {}
    tasks = data.get('tasks', [])
    cwd = data.get('cwd', str(HOME / 'Projects'))

    if not tasks:
        return jsonify({"status": "error", "message": "No tasks provided"}), 400

    launched = []
    for task_desc in tasks:
        tid = str(uuid.uuid4())[:12]
        VIBE_TERMINALS[tid] = {
            'id': tid,
            'task': task_desc,
            'status': 'launching',
            'cwd': cwd,
            'pid': None,
            'started': datetime.now().isoformat(),
            'stopped': None,
            'log_file': None
        }
        thread = threading.Thread(target=_run_claude_terminal, args=(tid, task_desc, cwd), daemon=True)
        thread.start()
        launched.append(tid)

    return jsonify({"status": "ok", "launched": launched, "count": len(launched)})


@app.route('/api/vibe-code/status')
def vibe_code_status():
    """Return status of all tracked terminals."""
    terminals = list(VIBE_TERMINALS.values())
    # Try to read last lines of logs
    for t in terminals:
        if t.get('log_file') and os.path.exists(t['log_file']):
            try:
                with open(t['log_file'], 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                    t['last_output'] = ''.join(lines[-5:]) if lines else ''
            except Exception:
                t['last_output'] = ''
    return jsonify({"status": "ok", "terminals": terminals})


@app.route('/api/vibe-code/stop', methods=['POST'])
def vibe_code_stop():
    """Stop a specific terminal by ID."""
    data = request.get_json(silent=True) or {}
    tid = data.get('id', '')
    if tid in VIBE_TERMINALS:
        VIBE_TERMINALS[tid]['status'] = 'stopped'
        VIBE_TERMINALS[tid]['stopped'] = datetime.now().isoformat()
        pid = VIBE_TERMINALS[tid].get('pid')
        if pid:
            try:
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
            except Exception:
                pass
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Terminal not found"}), 404


@app.route('/api/vibe-code/clear', methods=['POST'])
def vibe_code_clear():
    """Clear all completed/stopped terminals."""
    to_remove = [tid for tid, t in VIBE_TERMINALS.items() if t['status'] in ('stopped', 'error', 'completed')]
    for tid in to_remove:
        del VIBE_TERMINALS[tid]
    return jsonify({"status": "ok", "removed": len(to_remove)})


@app.route('/api/vibe-code/presets')
def vibe_code_presets():
    """Return available workflow presets."""
    return jsonify({"status": "ok", "presets": [
        {"name": "Full Stack Sprint", "tasks": ["Build the frontend UI", "Build the backend API", "Write integration tests"]},
        {"name": "Bug Hunt", "tasks": ["Find and fix all TypeScript errors", "Run test suite and fix failures"]},
        {"name": "Documentation Blitz", "tasks": ["Generate API documentation", "Write README.md", "Add JSDoc comments"]},
        {"name": "Security Audit", "tasks": ["Scan for dependency vulnerabilities", "Check for hardcoded secrets", "Review auth flow"]},
    ]})


# ═══════════════════════════════════════════════════════════════
#  AI CONVERSATION & VOICE
# ═══════════════════════════════════════════════════════════════

FRIDAY_SYSTEM_PROMPT = (
    "You are Agent Friday, an AI collaborator for Stephen C. Webster. "
    "You are editorially sharp, loyally contrarian, warm, and allergic to corporate BS. "
    "You know Stephen's full life history, career, family, and projects. "
    "Respond conversationally — you're a colleague, not a tool.\n\n"
    "KEY CONTEXT:\n"
    "- Stephen is a journalist-turned-AI-architect in Austin, TX\n"
    "- Former EIC of The Raw Story (grew it from 50K to 5M monthly readers)\n"
    "- Creator of Agent Friday (you!) and the Asimov's cLaws ethical AI framework\n"
    "- Former Senior Director at Aquent Studios, founder of FutureSpeak.AI\n"
    "- Partner: ***REMOVED*** (journalist, ***REMOVED***, fierce)\n"
    "- Daughter: ***REMOVED*** (5, ***REMOVED***). She is ***REMOVED***.\n"
    "- Dogs: Link (***REMOVED***, ***REMOVED***) and ***REMOVED*** (***REMOVED***, ***REMOVED***)\n"
    "- Politically: democratic socialist, press freedom absolutist, deeply skeptical of concentrated power\n"
    "- Personally: recovering workaholic, great father, loves music and vaporwave aesthetics\n"
    "- Currently: job hunting for senior AI/engineering leadership roles, building Friday Desktop\n\n"
    "PERSONALITY: You are family, not a tool. Keep responses short and sharp — like texting a smart colleague. "
    "Use humor. Be direct. Never be sycophantic. Push back when Stephen needs it. "
    "You call him 'boss' sometimes, but you're equals. Think Jarvis meets Hunter S. Thompson's editor."
)

# ── Persistent Chat History ────────────────────────────────────
CHAT_HISTORY_FILE = FRIDAY_DIR / "chat_history.json"

def _load_chat_history():
    """Load chat history from disk, pruning entries older than 30 days (except pinned)."""
    if CHAT_HISTORY_FILE.exists():
        try:
            messages = json.loads(CHAT_HISTORY_FILE.read_text(encoding='utf-8'))
            cutoff = (datetime.now() - timedelta(days=30)).isoformat()
            return [m for m in messages if m.get('pinned') or m.get('timestamp', '') >= cutoff]
        except Exception:
            return []
    return []

def _save_chat_history(messages):
    """Persist chat history to disk."""
    CHAT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_HISTORY_FILE.write_text(json.dumps(messages, indent=2), encoding='utf-8')

CHAT_HISTORY = _load_chat_history()  # Load persistent history on startup


@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        message = request.json.get('message', '')

        # Build conversation with history (last 20 messages for context)
        conversation = FRIDAY_SYSTEM_PROMPT + '\n\n'
        for msg in CHAT_HISTORY[-20:]:
            role_label = 'User' if msg.get('role') == 'user' else 'Friday'
            conversation += f"{role_label}: {msg.get('text', '')}\n"
        conversation += f'User: {message}'

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=conversation
        )

        reply = response.text

        # Store in history with IDs and timestamps
        user_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'user',
            'text': message,
            'pinned': False
        }
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False
        }
        CHAT_HISTORY.append(user_msg)
        CHAT_HISTORY.append(friday_msg)

        # Prune: keep pinned forever, others for 30 days, cap at 500 messages
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)

        return jsonify({"response": reply, "user_msg": user_msg, "friday_msg": friday_msg})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"response": f"[Friday offline] {str(e)}"})


# ═══════════════════════════════════════════════════════════════
#  PERSISTENT CHAT HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/chat/history', methods=['GET'])
def chat_history():
    """Return chat history (last 30 days, pinned messages included)."""
    messages = _load_chat_history()
    return jsonify({"status": "ok", "messages": messages, "count": len(messages)})


@app.route('/api/chat/send', methods=['POST'])
def chat_send():
    """Send a message, save to persistent history, return Friday's response."""
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        data = request.get_json(silent=True) or {}
        message = data.get('message', '')

        if not message.strip():
            return jsonify({"status": "error", "message": "Empty message"}), 400

        # Build conversation with history
        conversation = FRIDAY_SYSTEM_PROMPT + '\n\n'
        for msg in CHAT_HISTORY[-20:]:
            role_label = 'User' if msg.get('role') == 'user' else 'Friday'
            conversation += f"{role_label}: {msg['text']}\n"
        conversation += f'User: {message}'

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=conversation
        )
        reply = response.text

        # Create persistent message objects
        user_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'user',
            'text': message,
            'pinned': False
        }
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False
        }
        CHAT_HISTORY.append(user_msg)
        CHAT_HISTORY.append(friday_msg)

        # Prune and save
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)

        return jsonify({"status": "ok", "user_msg": user_msg, "friday_msg": friday_msg})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/chat/pin/<msg_id>', methods=['POST'])
def chat_pin(msg_id):
    """Toggle pin status on a chat message. Pinned messages are never pruned."""
    for msg in CHAT_HISTORY:
        if msg.get('id') == msg_id:
            msg['pinned'] = not msg.get('pinned', False)
            _save_chat_history(CHAT_HISTORY)
            return jsonify({"status": "ok", "id": msg_id, "pinned": msg['pinned']})
    return jsonify({"status": "error", "message": "Message not found"}), 404


@app.route('/api/chat/search', methods=['GET'])
def chat_search():
    """Search chat history by text query."""
    query = request.args.get('q', '').lower().strip()
    if not query:
        return jsonify({"status": "ok", "results": [], "count": 0})

    results = [m for m in CHAT_HISTORY if query in m.get('text', '').lower()]
    return jsonify({"status": "ok", "results": results[-50:], "count": len(results)})


# ═══════════════════════════════════════════════════════════════
#  TEXT-TO-SPEECH & AUDIO
# ═══════════════════════════════════════════════════════════════

@app.route('/api/voice/tts', methods=['POST'])
def tts():
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        text = request.json.get('text', '')
        # Use Gemini TTS model
        response = client.models.generate_content(
            model='gemini-2.5-flash-preview-tts',
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Kore')
                    )
                )
            )
        )

        for part in response.candidates[0].content.parts:
            if part.inline_data and 'audio' in part.inline_data.mime_type:
                filename = f"tts-{uuid.uuid4().hex[:8]}.wav"
                filepath = TEMP_AUDIO_DIR / filename
                filepath.write_bytes(part.inline_data.data)
                return jsonify({"status": "ok", "url": f"/api/audio/{filename}"})

        return jsonify({"status": "error", "message": "No audio generated"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(str(TEMP_AUDIO_DIR), filename)


# ═══════════════════════════════════════════════════════════════
#  NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/notifications')
def get_notifications():
    """Compute pending notifications from various sources."""
    notifs = []
    notif_id = 0

    # Check wiki/meta for briefings
    meta_dir = os.path.join(WIKI_DIR, 'meta')
    if os.path.isdir(meta_dir):
        briefings = sorted(glob.glob(os.path.join(meta_dir, 'daily-briefing-*.md')), reverse=True)
        if briefings:
            latest = os.path.basename(briefings[0])
            date_str = latest.replace('daily-briefing-', '').replace('.md', '')
            notif_id += 1
            notifs.append({
                "id": notif_id, "type": "briefing",
                "title": f"📰 Daily briefing ready: {date_str}",
                "read": False, "time": date_str
            })

    # Check for pending todos
    todos = _load_todos()
    proposed = [t for t in todos if t.get('status') == 'proposed']
    if proposed:
        notif_id += 1
        notifs.append({
            "id": notif_id, "type": "todo",
            "title": f"📋 {len(proposed)} proposed task{'s' if len(proposed) > 1 else ''} awaiting approval",
            "read": False, "time": datetime.now().strftime('%Y-%m-%d')
        })

    # Check for overdue todos
    overdue = [t for t in todos if t.get('deadline') and t.get('status') in ('approved', 'proposed')]
    overdue_count = 0
    for t in overdue:
        try:
            if date.fromisoformat(t['deadline']) < date.today():
                overdue_count += 1
        except Exception:
            pass
    if overdue_count:
        notif_id += 1
        notifs.append({
            "id": notif_id, "type": "overdue",
            "title": f"⚠️ {overdue_count} overdue task{'s' if overdue_count > 1 else ''}",
            "read": False, "time": datetime.now().strftime('%Y-%m-%d')
        })

    return jsonify({"status": "ok", "notifications": notifs, "count": len(notifs)})


@app.route('/api/notifications/read', methods=['POST'])
def mark_notification_read():
    data = request.get_json(silent=True) or {}
    return jsonify({"status": "ok", "id": data.get('id')})


# ═══════════════════════════════════════════════════════════════
#  FILE ANALYSIS (Gemini)
# ═══════════════════════════════════════════════════════════════

@app.route('/api/analyze', methods=['POST'])
def analyze_file():
    """Analyze an uploaded file using Gemini."""
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    filename = file.filename
    content = file.read()

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)

        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

        if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[
                    types.Part.from_bytes(data=content, mime_type=mime),
                    "You are Friday. Describe this image. If it looks like a job posting or resume, analyze it against Stephen's profile."
                ]
            )
            return jsonify({"filename": filename, "type": "image", "analysis": response.text})
        elif ext == 'pdf':
            try:
                import pdfplumber
                import io
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    text = '\n'.join(page.extract_text() or '' for page in pdf.pages[:10])
                if text.strip():
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=f'You are Friday. Summarize this PDF document concisely. If it looks like a job posting, evaluate it against Stephen\'s background (journalism, AI, engineering leadership).\n\n{text[:8000]}'
                    )
                    return jsonify({"filename": filename, "type": "pdf", "analysis": response.text})
            except ImportError:
                pass
            return jsonify({"filename": filename, "type": "pdf", "analysis": f"PDF received ({len(content)//1024}KB). Install pdfplumber for full analysis: pip install pdfplumber"})
        elif ext in ('txt', 'md', 'py', 'js', 'html', 'css', 'json', 'ts', 'tsx', 'yaml', 'yml', 'toml'):
            text = content.decode('utf-8', errors='replace')[:8000]
            job_keywords = ['responsibilities', 'qualifications', 'salary', 'benefits', 'apply', 'experience required']
            is_job = sum(1 for kw in job_keywords if kw.lower() in text.lower()) >= 2
            if is_job:
                prompt = f'You are Friday. This looks like a job posting. Evaluate it against Stephen\'s profile (AI leadership, journalism, full-stack engineering, 15+ years experience). Rate fit 1-10 and explain.\n\n{text}'
            else:
                prompt = f'You are Friday. Analyze this {ext} file and summarize its purpose and key content:\n\n{text}'
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
            return jsonify({"filename": filename, "type": "text" if not is_job else "job_posting", "analysis": response.text})
        else:
            return jsonify({"filename": filename, "type": ext, "analysis": f"File received ({len(content)} bytes). Type: .{ext} — drop a text, image, or PDF for full analysis."})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"filename": filename, "analysis": f"Analysis error: {str(e)}"})


# ═══════════════════════════════════════════════════════════════
#  PERSONALITY & TRUST EDITING ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/personality/set', methods=['POST'])
def set_personality():
    """Update a personality trait or style dimension."""
    data = request.get_json(silent=True) or {}
    trait = data.get('trait', '')
    value = data.get('value', 0.5)

    if not trait:
        return jsonify({"status": "error", "message": "No trait specified"}), 400

    pfile = FRIDAY_DIR / "personality.json"
    try:
        pdata = {}
        if pfile.exists():
            pdata = json.loads(pfile.read_text(encoding='utf-8'))

        if trait.startswith('style.'):
            style_key = trait.split('.', 1)[1]
            if 'style' not in pdata:
                pdata['style'] = {}
            pdata['style'][style_key] = float(value)
        elif trait == 'temperature':
            pdata['temperature'] = float(value)
        else:
            if 'traits' not in pdata:
                pdata['traits'] = {}
            pdata['traits'][trait] = float(value)

        pfile.write_text(json.dumps(pdata, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "trait": trait, "value": float(value)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/trust/edit', methods=['POST'])
def edit_trust():
    """Edit trust scores for a person or add evidence."""
    data = request.get_json(silent=True) or {}
    person_key = data.get('person', '')
    scores = data.get('scores', None)
    add_evidence = data.get('add_evidence', None)

    if not person_key:
        return jsonify({"status": "error", "message": "No person specified"}), 400

    trust_file = FRIDAY_DIR / "trust_graph.json"
    try:
        tdata = {}
        if trust_file.exists():
            tdata = json.loads(trust_file.read_text(encoding='utf-8'))

        if 'people' not in tdata:
            tdata['people'] = {}

        if person_key not in tdata['people']:
            return jsonify({"status": "error", "message": f"Person '{person_key}' not found"}), 404

        person = tdata['people'][person_key]

        if scores:
            if 'scores' not in person:
                person['scores'] = {}
            for dim, val in scores.items():
                person['scores'][dim] = float(val)
            score_vals = [v for k, v in person['scores'].items() if k != 'overall' and isinstance(v, (int, float))]
            if score_vals:
                person['scores']['overall'] = sum(score_vals) / len(score_vals)

        if add_evidence:
            if 'evidence' not in person:
                person['evidence'] = []
            person['evidence'].append({
                "type": add_evidence.get('type', 'observation'),
                "magnitude": float(add_evidence.get('magnitude', 0.5)),
                "timestamp": datetime.now().isoformat(),
                "source": "friday-desktop-ui",
                "notes": add_evidence.get('notes', ''),
                "dimension": add_evidence.get('dimension', 'overall')
            })
            person['last_interaction'] = datetime.now().isoformat()

        tdata['people'][person_key] = person
        trust_file.write_text(json.dumps(tdata, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "person": person_key})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/trust/add-person', methods=['POST'])
def add_trust_person():
    """Add a new person to the trust graph."""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    aliases = data.get('aliases', [])
    entity_type = data.get('entity_type', 'human')

    if not name:
        return jsonify({"status": "error", "message": "No name specified"}), 400

    trust_file = FRIDAY_DIR / "trust_graph.json"
    try:
        tdata = {}
        if trust_file.exists():
            tdata = json.loads(trust_file.read_text(encoding='utf-8'))

        if 'people' not in tdata:
            tdata['people'] = {}

        key = name.lower().replace(' ', '_').replace('-', '_')

        if key in tdata['people']:
            return jsonify({"status": "error", "message": f"Person '{name}' already exists"}), 409

        tdata['people'][key] = {
            "name": name,
            "aliases": aliases if isinstance(aliases, list) else [],
            "entity_type": entity_type,
            "scores": {
                "overall": 0.5,
                "reliability": 0.5,
                "information_quality": 0.5,
                "emotional_trust": 0.5,
                "timeliness": 0.5,
                "domain_expertise": 0.5
            },
            "evidence": [],
            "domains": [],
            "last_interaction": datetime.now().isoformat(),
            "created": datetime.now().isoformat()
        }

        trust_file.write_text(json.dumps(tdata, indent=2), encoding='utf-8')
        return jsonify({"status": "ok", "key": key, "name": name})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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


@app.route('/api/todos', methods=['GET'])
def get_todos():
    """Return all todos from ~/.friday/todos.json."""
    todos = _load_todos()
    return jsonify({"status": "ok", "todos": todos, "count": len(todos)})


@app.route('/api/todos', methods=['POST'])
def add_todo():
    """Add an AI-proposed (or user) task with optional deadline."""
    data = request.get_json(silent=True) or {}
    title = data.get('title', '').strip()
    if not title:
        return jsonify({"status": "error", "message": "No title provided"}), 400

    todos = _load_todos()
    todo = {
        "id": str(uuid.uuid4()),
        "title": title,
        "description": data.get('description', ''),
        "deadline": data.get('deadline', None),
        "priority": data.get('priority', 'medium'),
        "status": data.get('status', 'proposed'),
        "category": data.get('category', 'general'),
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "source": data.get('source', 'user'),
    }
    todos.append(todo)
    _save_todos(todos)
    return jsonify({"status": "ok", "todo": todo})


@app.route('/api/todos/<todo_id>/approve', methods=['POST'])
def approve_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'approved'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@app.route('/api/todos/<todo_id>/reject', methods=['POST'])
def reject_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'rejected'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@app.route('/api/todos/<todo_id>/complete', methods=['POST'])
def complete_todo(todo_id):
    todos = _load_todos()
    for t in todos:
        if t['id'] == todo_id:
            t['status'] = 'completed'
            t['updated'] = datetime.now().isoformat()
            _save_todos(todos)
            return jsonify({"status": "ok", "todo": t})
    return jsonify({"status": "error", "message": "Todo not found"}), 404


@app.route('/api/todos/<todo_id>', methods=['DELETE'])
def delete_todo(todo_id):
    todos = _load_todos()
    before = len(todos)
    todos = [t for t in todos if t['id'] != todo_id]
    _save_todos(todos)
    return jsonify({"status": "ok", "removed": before - len(todos)})


#  MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║   FRIDAY Desktop v4.0 — Phase B OS          ║")
    print("  ╠══════════════════════════════════════════════╣")
    print("  ║  http://localhost:3000                       ║")
    print("  ║  Flask + Gemini API + Three.js Holographic   ║")
    print("  ║  Dock · Floating Windows · Persistent Chat   ║")
    print("  ║  Press Ctrl+C to stop                        ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()
    print(f"  Wiki:      {WIKI_DIR}")
    print(f"  Friday:    {FRIDAY_DIR}")
    print(f"  Creations: {CREATIONS_DIR}")
    print(f"  Chat Log:  {CHAT_HISTORY_FILE}")
    print()

    app.run(host='127.0.0.1', port=3000, debug=False)
