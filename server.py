"""
FRIDAY Desktop v4.0 — Phase B OS Backend
Flask server with live data endpoints + Gemini creative API integration.
Powered by FutureSpeak.AI
"""

import os
import io
import json
import glob
import subprocess
import base64
import traceback
import uuid
import threading
import asyncio
import re
import time as _time
from datetime import datetime, date, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, send_file, session, redirect, url_for, Response
from functools import wraps

try:
    from flask_sock import Sock, ConnectionClosed
    _HAS_SOCK = True
except ImportError:
    _HAS_SOCK = False
    print("  [FRIDAY] WARNING: flask-sock not installed. /ws/live disabled.")

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get("FRIDAY_SECRET_KEY", "friday-default-secret-change-me")
sock = Sock(app) if _HAS_SOCK else None

# ── Authentication ───────────────────────────────────────────
FRIDAY_USERNAME = os.environ.get("FRIDAY_USERNAME", "admin")
FRIDAY_PASSWORD = os.environ.get("FRIDAY_PASSWORD", "")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not FRIDAY_PASSWORD:
            return f(*args, **kwargs)
        if not session.get("authenticated"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FRIDAY — Authenticate</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;color:#e0e0ff;font-family:'Orbitron',monospace;display:flex;align-items:center;justify-content:center;min-height:100vh;overflow:hidden}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(ellipse at 50% 50%,rgba(124,58,237,.12) 0%,transparent 70%);pointer-events:none}
.login-box{background:rgba(15,15,30,.85);border:1px solid rgba(124,58,237,.35);border-radius:12px;padding:40px 36px;width:340px;backdrop-filter:blur(20px);box-shadow:0 0 40px rgba(124,58,237,.15),inset 0 0 30px rgba(124,58,237,.05);position:relative}
.login-box::before{content:'';position:absolute;top:-1px;left:20%;right:20%;height:2px;background:linear-gradient(90deg,transparent,rgba(124,58,237,.8),transparent);border-radius:2px}
h1{font-size:14px;letter-spacing:.25em;text-align:center;color:rgba(124,58,237,.9);margin-bottom:8px}
.subtitle{font-size:9px;letter-spacing:.15em;text-align:center;color:rgba(180,160,255,.4);margin-bottom:32px}
.field{margin-bottom:12px}
input[type=email],input[type=text],input[type=password]{width:100%;padding:12px 16px;background:rgba(124,58,237,.08);border:1px solid rgba(124,58,237,.25);border-radius:6px;color:#e0e0ff;font-family:'Orbitron',monospace;font-size:12px;letter-spacing:.15em;outline:none;transition:border-color .3s}
input[type=email]:focus,input[type=text]:focus,input[type=password]:focus{border-color:rgba(124,58,237,.7);box-shadow:0 0 15px rgba(124,58,237,.15)}
input::placeholder{color:rgba(180,160,255,.25)}
button{width:100%;padding:12px;margin-top:4px;background:linear-gradient(135deg,rgba(124,58,237,.3),rgba(124,58,237,.15));border:1px solid rgba(124,58,237,.4);border-radius:6px;color:rgba(200,180,255,.9);font-family:'Orbitron',monospace;font-size:11px;letter-spacing:.2em;cursor:pointer;transition:all .3s}
button:hover{background:linear-gradient(135deg,rgba(124,58,237,.45),rgba(124,58,237,.25));border-color:rgba(124,58,237,.7);box-shadow:0 0 20px rgba(124,58,237,.2)}
.error{color:#ff4466;font-size:9px;text-align:center;margin-top:12px;letter-spacing:.1em}
.scan-line{position:fixed;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,rgba(124,58,237,.15),transparent);animation:scan 4s linear infinite;pointer-events:none}
@keyframes scan{0%{top:0}100%{top:100vh}}
</style>
</head>
<body>
<div class="scan-line"></div>
<div class="login-box">
<h1>FRIDAY</h1>
<div class="subtitle">AUTHENTICATION REQUIRED</div>
<form method="POST">
<div class="field"><input type="email" name="username" placeholder="EMAIL / USERNAME" autofocus autocomplete="username"></div>
<div class="field"><input type="password" name="password" placeholder="PASSWORD" autocomplete="current-password"></div>
<button type="submit">AUTHENTICATE</button>
</form>
{{ error }}
</div>
</body>
</html>"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    if not FRIDAY_PASSWORD:
        return redirect('/')
    error = ""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == FRIDAY_USERNAME and password == FRIDAY_PASSWORD:
            session['authenticated'] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
            return redirect('/')
        error = '<div class="error">ACCESS DENIED — INVALID CREDENTIALS</div>'
    html = LOGIN_HTML.replace('{{ error }}', error)
    return Response(html, content_type='text/html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ── Vibe Code: Terminal State ─────────────────────────────────
VIBE_TERMINALS = {}   # id -> { id, task, status, cwd, pid, started, stopped, log_file }
VIBE_LOG_DIR = Path(os.path.expanduser("~")) / ".friday" / "vibe-code-logs"
VIBE_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Paths ─────────────────────────────────────────────────────
HOME = Path(os.path.expanduser("~"))
WIKI_DIR = HOME / "wiki"
FRIDAY_DIR = HOME / ".friday"
CREATIONS_DIR = HOME / "Desktop" / "friday-creations"
WIKI_PROFESSIONAL_DIR = WIKI_DIR / "professional"
JOB_SEARCH_FILE = WIKI_PROFESSIONAL_DIR / "job-search.md"

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


@app.before_request
def check_auth():
    if not FRIDAY_PASSWORD:
        return None
    if request.endpoint in ('login', 'static'):
        return None
    if not session.get("authenticated"):
        if request.is_json or request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login"))


# ═══════════════════════════════════════════════════════════════
#  SERVE UI
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def serve_ui():
    return send_from_directory('.', 'index.html')


@app.route('/friday-live')
@app.route('/friday-live/')
def serve_friday_live():
    return send_from_directory('.', 'friday_live.html')


@app.route('/friday-live/manifest.json')
def serve_friday_live_manifest():
    return send_from_directory('.', 'friday_live_manifest.json', mimetype='application/manifest+json')


@app.route('/friday-live/sw.js')
def serve_friday_live_sw():
    resp = send_from_directory('.', 'friday_live_sw.js', mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/friday-live/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ═══════════════════════════════════════════════════════════════
#  LIVE DATA ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/api/career-ops/tracker')
def career_tracker():
    candidates = [
        WIKI_PROFESSIONAL_DIR / 'application-log.md',
        Path('C:\\Users\\user\\Projects\\career-ops\\data') / 'applications.md',
    ]
    tracker_path = next((p for p in candidates if p.is_file()), None)
    if tracker_path:
        content = tracker_path.read_text(encoding='utf-8')
        lines = content.strip().split('\n')
        entries = []
        for line in lines:
            if line.startswith('|') and '---' not in line and not any(h in line.lower() for h in ['company','score','#']):
                cols = [c.strip() for c in line.split('|')[1:-1]]
                if len(cols) >= 3:
                    entries.append({'raw': cols, 'company': cols[0], 'score': cols[1] if len(cols)>1 else '', 'status': cols[2] if len(cols)>2 else ''})
        return jsonify({'status': 'ok', 'entries': entries, 'total': len(entries), 'raw': content, 'source': str(tracker_path)})
    return jsonify({'status': 'no_tracker', 'entries': [], 'total': 0, 'raw': ''})

@app.route('/api/career-ops/pipeline')
def career_pipeline():
    candidates = [
        WIKI_PROFESSIONAL_DIR / 'job-search.md',
        Path('C:\\Users\\user\\Projects\\career-ops\\data') / 'pipeline.md',
    ]
    pipe_path = next((p for p in candidates if p.is_file()), None)
    if pipe_path:
        return jsonify({'status': 'ok', 'content': pipe_path.read_text(encoding='utf-8'), 'source': str(pipe_path)})
    return jsonify({'status': 'empty', 'content': ''})

@app.route('/api/career-ops/reports')
def career_reports():
    reports = []
    seen = set()
    # wiki/professional/ is primary — collect all .md files there
    if WIKI_PROFESSIONAL_DIR.is_dir():
        for f in sorted(WIKI_PROFESSIONAL_DIR.iterdir(), reverse=True):
            if f.suffix == '.md':
                reports.append({'name': f.name, 'size': f.stat().st_size, 'source': 'wiki'})
                seen.add(f.name)
    # career-ops/reports/ is fallback — add any files not already in wiki
    fallback_dir = Path('C:\\Users\\user\\Projects\\career-ops\\reports')
    if fallback_dir.is_dir():
        for f in sorted(fallback_dir.iterdir(), reverse=True):
            if f.suffix == '.md' and f.name not in seen:
                reports.append({'name': f.name, 'size': f.stat().st_size, 'source': 'career-ops'})
    if reports:
        return jsonify({'status': 'ok', 'reports': reports, 'total': len(reports)})
    return jsonify({'status': 'no_reports', 'reports': [], 'total': 0})

@app.route('/api/career-ops/report/<filename>')
def career_report(filename):
    candidates = [
        WIKI_PROFESSIONAL_DIR / filename,
        Path('C:\\Users\\user\\Projects\\career-ops\\reports') / filename,
    ]
    report_path = next((p for p in candidates if p.is_file()), None)
    if report_path:
        return jsonify({'status': 'ok', 'content': report_path.read_text(encoding='utf-8'), 'filename': filename, 'source': str(report_path)})
    return jsonify({'status': 'not_found'})

@app.route('/api/briefings')
def list_briefings():
    """List all daily briefing files from both known locations (never delete these)."""
    briefings_by_date = {}

    # Location 1: Desktop/friday-creations — filenames like daily-briefing-2026-04-14.html
    creations = HOME / 'Desktop' / 'friday-creations'
    if creations.exists():
        for f in creations.iterdir():
            if f.name.startswith('daily-briefing') and f.suffix in ('.html', '.md'):
                date_part = f.name.replace('daily-briefing-', '').replace('.html', '').replace('.md', '')
                entry = briefings_by_date.setdefault(date_part, {'date': date_part, 'name': f.stem})
                entry[f.suffix.lstrip('.')] = f.name
                entry['size'] = f.stat().st_size

    # Location 2: ~/.friday/wiki/briefings — filenames like 2026-04-14.html
    wiki_briefings = HOME / '.friday' / 'wiki' / 'briefings'
    if wiki_briefings.exists():
        for f in wiki_briefings.iterdir():
            if f.suffix == '.html' and len(f.stem) == 10 and f.stem[4] == '-' and f.stem[7] == '-':
                date_part = f.stem  # e.g. "2026-04-14"
                entry = briefings_by_date.setdefault(date_part, {'date': date_part, 'name': f.stem})
                entry['html'] = f.name
                entry.setdefault('size', f.stat().st_size)

    briefings = sorted(briefings_by_date.values(), key=lambda b: b['date'], reverse=True)
    return jsonify({'status': 'ok', 'briefings': briefings, 'total': len(briefings)})

def _find_briefing_path(filename):
    """Return the Path for a briefing file, checking both known locations."""
    # Location 1: Desktop/friday-creations (legacy daily-briefing-*.html files)
    p1 = HOME / 'Desktop' / 'friday-creations' / filename
    if p1.exists() and p1.name.startswith('daily-briefing'):
        return p1
    # Location 2: ~/.friday/wiki/briefings (date-named files like 2026-04-14.html)
    p2 = HOME / '.friday' / 'wiki' / 'briefings' / filename
    if p2.exists():
        return p2
    return None

@app.route('/briefing/<filename>')
def serve_briefing(filename):
    """Serve a briefing HTML file directly for browser viewing."""
    path = _find_briefing_path(filename)
    if path:
        return send_from_directory(str(path.parent), filename)
    return 'Not found', 404

@app.route('/api/briefing/<filename>')
def get_briefing(filename):
    """Serve a briefing file content."""
    path = _find_briefing_path(filename)
    if path:
        return jsonify({'status': 'ok', 'content': path.read_text(encoding='utf-8'), 'filename': filename, 'is_html': path.suffix == '.html'})
    return jsonify({'status': 'not_found'}), 404

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
#  FINANCE WORKSPACE
# ═══════════════════════════════════════════════════════════════

FINANCE_DIR = FRIDAY_DIR / "finance"
FINANCE_DIR.mkdir(parents=True, exist_ok=True)

@app.route('/api/finance/portfolio')
def finance_portfolio():
    """Read portfolio positions from config."""
    path = FINANCE_DIR / "portfolio.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    # Create template if missing
    template = {"positions": [{"ticker": "NVDA", "shares": 0, "cost_basis": 0}], "accounts": ["RW Baird - Lisa Schmidt"]}
    path.write_text(json.dumps(template, indent=2), encoding='utf-8')
    return jsonify({"status": "ok", **template})

@app.route('/api/finance/perks')
def finance_perks():
    """Read Amex perks from config."""
    path = FINANCE_DIR / "amex_perks.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"perks": [{"name": "Perk name", "value": "$X/yr", "used": False, "expires": "", "notes": ""}]}
    path.write_text(json.dumps(template, indent=2), encoding='utf-8')
    return jsonify({"status": "ok", **template})

@app.route('/api/finance/contacts')
def finance_contacts():
    """Financial contacts reference."""
    return jsonify({"status": "ok", "contacts": [
        {"name": "Lisa J. Schmidt", "role": "Financial Advisor", "firm": "RW Baird", "phone": "", "email": ""},
        {"name": "Claudia Gonzalez-Chavez", "role": "CPA", "firm": "Whitley Penn", "phone": "", "email": ""}
    ]})

@app.route('/api/finance/quickref')
def finance_quickref():
    """Quick reference for financial accounts."""
    return jsonify({"status": "ok", "accounts": [
        {"name": "Capital One", "type": "Banking", "notes": ""},
        {"name": "Cigna Healthcare", "type": "Insurance", "notes": ""},
        {"name": "Amex Platinum — Stephen", "type": "Credit Card", "notes": ""},
        {"name": "Amex Platinum — ***REMOVED***", "type": "Credit Card", "notes": ""}
    ]})


# ═══════════════════════════════════════════════════════════════
#  HEALTH WORKSPACE
# ═══════════════════════════════════════════════════════════════

HEALTH_DIR = FRIDAY_DIR / "health"
HEALTH_DIR.mkdir(parents=True, exist_ok=True)

@app.route('/api/health/medications')
def health_medications():
    """Read medications from config."""
    path = HEALTH_DIR / "medications.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"medications": [{"name": "GLP-1 (Henry Meds)", "dose": "", "frequency": "", "notes": ""}]}
    path.write_text(json.dumps(template, indent=2), encoding='utf-8')
    return jsonify({"status": "ok", **template})

@app.route('/api/health/appointments')
def health_appointments():
    """Read appointments from config."""
    path = HEALTH_DIR / "appointments.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"appointments": [{"provider": "Rachel Hodgdon", "type": "***REMOVED*** play therapy", "email": "user@example.com", "next": "", "frequency": ""}]}
    path.write_text(json.dumps(template, indent=2), encoding='utf-8')
    return jsonify({"status": "ok", **template})

@app.route('/api/health/insurance')
def health_insurance():
    """Read insurance info from config."""
    path = HEALTH_DIR / "insurance.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"insurance": {"provider": "Cigna Healthcare", "plan": "Add your plan name", "policy_number": "Add your policy number", "group_number": "Add your group number"}}
    path.write_text(json.dumps(template, indent=2), encoding='utf-8')
    return jsonify({"status": "ok", **template})

@app.route('/api/health/vehicles')
def health_vehicles():
    """Read vehicle fleet data from config."""
    path = HEALTH_DIR / "vehicles.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return jsonify({"status": "ok", **data})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    template = {"vehicles": [{"name": "2015 VW Golf TSI SEL", "miles": "~60K", "notes": "", "mechanic": "Motormania Austin", "service_history": []}], "mechanics": []}
    path.write_text(json.dumps(template, indent=2), encoding='utf-8')
    return jsonify({"status": "ok", **template})


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


# ═══════════════════════════════════════════════════════════════
#  CONTEXT AWARENESS ENGINE
# ═══════════════════════════════════════════════════════════════

CAREER_OPS_DIR = Path('C:\\Users\\user\\Projects\\career-ops\\data')
WIKI_DIR_FRIDAY = HOME / ".friday" / "wiki"

def _load_vault_summary():
    """Load a lightweight summary of all core vault data for context injection."""
    ctx = {}

    # Personality state
    pfile = FRIDAY_DIR / "personality.json"
    if pfile.exists():
        try:
            data = json.loads(pfile.read_text(encoding='utf-8'))
            ctx['personality'] = {
                'maturity': data.get('maturity', 0.5),
                'session_count': data.get('session_count', 0),
                'top_traits': {k: round(v, 2) for k, v in list(data.get('traits', {}).items())[:5]},
                'temperature': data.get('temperature', 0.7),
            }
        except Exception:
            pass

    # Trust graph — names and scores only (lightweight)
    tfile = FRIDAY_DIR / "trust_graph.json"
    if tfile.exists():
        try:
            data = json.loads(tfile.read_text(encoding='utf-8'))
            people = data.get('people', {})
            if isinstance(people, dict):
                ctx['trust_people'] = {
                    name: {
                        'overall': round(info.get('overall_score', info.get('score', 0.5)), 2),
                        'relationship': info.get('relationship', ''),
                    }
                    for name, info in people.items()
                }
            elif isinstance(people, list):
                ctx['trust_people'] = {
                    p.get('name', 'unknown'): {
                        'overall': round(p.get('overall_score', p.get('score', 0.5)), 2),
                        'relationship': p.get('relationship', ''),
                    }
                    for p in people
                }
        except Exception:
            pass

    # Memory stats
    mem_file = FRIDAY_DIR / "memory.json"
    if mem_file.exists():
        try:
            data = json.loads(mem_file.read_text(encoding='utf-8'))
            # Pull recent memories for conversational awareness
            recent = []
            for tier in ['short_term', 'working', 'recent']:
                if tier in data and isinstance(data[tier], list):
                    for m in data[tier][-5:]:
                        if isinstance(m, dict):
                            recent.append(m.get('content', m.get('text', str(m)))[:200])
                        elif isinstance(m, str):
                            recent.append(m[:200])
            ctx['recent_memories'] = recent
        except Exception:
            pass

    # Todos
    todo_file = FRIDAY_DIR / "todos.json"
    if todo_file.exists():
        try:
            todos = json.loads(todo_file.read_text(encoding='utf-8'))
            active = [t for t in todos if t.get('status') in ('proposed', 'approved')]
            ctx['active_todos'] = [
                {'task': t.get('title', t.get('task', '')), 'status': t.get('status', '')}
                for t in active[:10]
            ]
        except Exception:
            pass

    # Epistemic score
    efile = FRIDAY_DIR / "epistemic_scores.json"
    if not efile.exists():
        efile = FRIDAY_DIR / "epistemic.json"
    if efile.exists():
        try:
            data = json.loads(efile.read_text(encoding='utf-8'))
            ctx['epistemic'] = {
                'overall': round(data.get('overall_score', data.get('overall', 0.72)), 2),
            }
        except Exception:
            pass

    return ctx


def _lookup_trust_person(name, trust_data):
    """Look up a person's full trust entry by name (fuzzy match)."""
    if not trust_data:
        return None
    people = trust_data.get('people', {})
    name_lower = name.lower()

    if isinstance(people, dict):
        for pname, pdata in people.items():
            if name_lower in pname.lower():
                return {pname: pdata}
    elif isinstance(people, list):
        for p in people:
            if name_lower in p.get('name', '').lower():
                return p
    return None


def _get_career_context():
    """Load career-ops summary for career-related queries."""
    ctx = {}
    tracker_candidates = [WIKI_PROFESSIONAL_DIR / 'application-log.md', CAREER_OPS_DIR / 'applications.md']
    tracker_path = next((p for p in tracker_candidates if p.exists()), None)
    if tracker_path:
        try:
            content = tracker_path.read_text(encoding='utf-8')
            lines = [l for l in content.strip().split('\n')
                     if l.startswith('|') and '---' not in l
                     and not any(h in l.lower() for h in ['company', 'score', '#'])]
            ctx['applications_count'] = len(lines)
            ctx['recent_applications'] = lines[-5:]
        except Exception:
            pass

    pipeline_candidates = [WIKI_PROFESSIONAL_DIR / 'job-search.md', CAREER_OPS_DIR / 'pipeline.md']
    pipeline_path = next((p for p in pipeline_candidates if p.exists()), None)
    if pipeline_path:
        try:
            ctx['pipeline_summary'] = pipeline_path.read_text(encoding='utf-8')[:1000]
        except Exception:
            pass
    return ctx


def _get_wiki_context(topic):
    """Search wiki for content matching a topic."""
    results = []
    for wiki_dir in [HOME / "wiki", WIKI_DIR_FRIDAY]:
        if not wiki_dir.exists():
            continue
        for md_file in wiki_dir.rglob('*.md'):
            try:
                content = md_file.read_text(encoding='utf-8')
                if topic.lower() in content.lower() or topic.lower() in md_file.stem.lower():
                    results.append({
                        'file': str(md_file.relative_to(wiki_dir)),
                        'excerpt': content[:500],
                    })
                    if len(results) >= 3:
                        return results
            except Exception:
                continue
    return results


def _detect_context_needs(message, workspace):
    """Analyze the message and workspace to decide what data to pull."""
    msg_lower = message.lower()
    needs = set()

    # Always include personality for tone calibration
    needs.add('personality')

    # Workspace-driven context
    ws_map = {
        'career': {'career', 'trust'},
        'trust': {'trust'},
        'coparent': {'trust', 'wiki'},
        'wiki': {'wiki'},
        'home': {'todos', 'personality'},
        'family': {'trust'},
        'futurespeak': {'career'},
        'code': set(),
        'studio': set(),
        'system': set(),
        'news': set(),
        'finance': set(),
        'health': set(),
    }
    needs.update(ws_map.get(workspace, set()))

    # Message keyword detection
    career_words = ['job', 'career', 'interview', 'resume', 'salary', 'apply', 'application',
                    'hire', 'offer', 'pipeline', 'role', 'position', 'recruiter']
    trust_words = ['trust', 'who is', 'tell me about', 'what do you know about',
                   'relationship', 'score', 'person']
    family_words = ['libby', 'liberty', 'janet', 'link', 'kismet', 'daughter',
                    'partner', 'dog', 'family', 'custody', 'birthday']
    todo_words = ['todo', 'task', 'to-do', 'to do', 'pending', 'approve', 'action item']
    wiki_words = ['briefing', 'wiki', 'notes', 'article', 'research', 'report']
    memory_words = ['remember', 'recall', 'memory', 'earlier', 'last time', 'you said',
                    'we discussed', 'we talked']

    if any(w in msg_lower for w in career_words):
        needs.add('career')
    if any(w in msg_lower for w in trust_words):
        needs.add('trust')
    if any(w in msg_lower for w in family_words):
        needs.add('trust')
    if any(w in msg_lower for w in todo_words):
        needs.add('todos')
    if any(w in msg_lower for w in wiki_words):
        needs.add('wiki')
    if any(w in msg_lower for w in memory_words):
        needs.add('memory')

    return needs


def _build_context_prompt(message, workspace='', workspace_context=None, vision_description=None):
    """Build an enriched system prompt with all relevant context layers."""
    vault = _load_vault_summary()
    needs = _detect_context_needs(message, workspace)
    sources_consulted = []

    sections = [FRIDAY_SYSTEM_PROMPT]

    # Layer 1: Active workspace context (from frontend)
    if workspace_context:
        sections.append(
            f"\n== ACTIVE WORKSPACE: {workspace_context.get('name', workspace)} ==\n"
            f"What Stephen is looking at right now:\n"
            f"{json.dumps(workspace_context.get('data', {}), indent=2, default=str)[:2000]}"
        )
        if workspace_context.get('focus'):
            sections.append(f"Current focus: {workspace_context['focus']}")
        sources_consulted.append('workspace')

    # Layer 2: Vault data (personality always included)
    if 'personality' in needs and 'personality' in vault:
        p = vault['personality']
        sections.append(
            f"\n== FRIDAY STATE ==\n"
            f"Maturity: {p.get('maturity', 0.5):.0%} · Sessions: {p.get('session_count', 0)} · "
            f"Temperature: {p.get('temperature', 0.7)}"
        )
        sources_consulted.append('personality')

    if 'trust' in needs and 'trust_people' in vault:
        # Check if message references a specific person
        trust_data_raw = None
        tfile = FRIDAY_DIR / "trust_graph.json"
        if tfile.exists():
            try:
                trust_data_raw = json.loads(tfile.read_text(encoding='utf-8'))
            except Exception:
                pass

        # Try to find a specific person mentioned
        person_match = None
        if trust_data_raw:
            for name in vault['trust_people']:
                if name.lower() in message.lower():
                    person_match = _lookup_trust_person(name, trust_data_raw)
                    break

        if person_match:
            sections.append(
                f"\n== TRUST DATA (specific person) ==\n"
                f"{json.dumps(person_match, indent=2, default=str)[:1500]}"
            )
        else:
            # General trust summary
            summary = ', '.join(
                f"{n} ({d.get('relationship', '?')}: {d.get('overall', '?')})"
                for n, d in list(vault['trust_people'].items())[:8]
            )
            sections.append(f"\n== TRUST NETWORK ==\n{summary}")
        sources_consulted.append('trust_graph')

    if 'career' in needs:
        career = _get_career_context()
        if career:
            sections.append(
                f"\n== CAREER OPS ==\n"
                f"Applications tracked: {career.get('applications_count', 0)}\n"
                f"Recent: {career.get('recent_applications', [])}\n"
                f"Pipeline: {career.get('pipeline_summary', 'N/A')[:500]}"
            )
            sources_consulted.append('career_ops')

    if 'todos' in needs and 'active_todos' in vault:
        todo_list = '\n'.join(
            f"- [{t['status']}] {t['task']}" for t in vault['active_todos']
        )
        sections.append(f"\n== ACTIVE TASKS ==\n{todo_list or 'No pending tasks.'}")
        sources_consulted.append('todos')

    if 'memory' in needs and 'recent_memories' in vault:
        mem_text = '\n'.join(f"- {m}" for m in vault['recent_memories'])
        sections.append(f"\n== RECENT MEMORIES ==\n{mem_text}")
        sources_consulted.append('memory')

    if 'wiki' in needs:
        # Extract a search term from the message
        topic = message.strip()[:50]
        wiki_results = _get_wiki_context(topic)
        if wiki_results:
            wiki_text = '\n'.join(
                f"[{r['file']}]: {r['excerpt'][:300]}" for r in wiki_results
            )
            sections.append(f"\n== WIKI/BRIEFING DATA ==\n{wiki_text}")
            sources_consulted.append('wiki')

    if 'epistemic' in needs and 'epistemic' in vault:
        sections.append(
            f"\n== EPISTEMIC STATE ==\n"
            f"Independence score: {vault['epistemic'].get('overall', 0.72)}"
        )

    # Layer 3: Vision context (from Gemini screen capture)
    if vision_description:
        sections.append(
            f"\n== SCREEN VISION (what Stephen's screen shows) ==\n"
            f"{vision_description[:1500]}"
        )
        sources_consulted.append('vision')

    return '\n'.join(sections), sources_consulted

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
        data = request.get_json(silent=True) or {}
        message = data.get('message', '')
        workspace = data.get('workspace', '')
        workspace_context = data.get('workspaceContext', None)
        include_vision = data.get('includeVision', False)
        vision_description = None

        # Vision capture: if requested, describe a provided screenshot via Gemini
        screenshot_b64 = data.get('screenshot', None)
        if include_vision and screenshot_b64:
            try:
                from google.genai import types
                img_bytes = base64.b64decode(screenshot_b64)
                vision_resp = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        "Briefly describe what is visible on this screen. Focus on text, UI elements, and data shown. Be concise (2-3 sentences).",
                        types.Part.from_bytes(data=img_bytes, mime_type='image/png'),
                    ],
                )
                vision_description = vision_resp.text
            except Exception as ve:
                vision_description = f"[Vision unavailable: {ve}]"

        # Build context-enriched system prompt
        system_prompt, sources = _build_context_prompt(
            message, workspace, workspace_context, vision_description
        )

        # Build conversation with history (last 20 messages for context)
        conversation = system_prompt + '\n\n'
        for msg in CHAT_HISTORY[-20:]:
            role_label = 'User' if msg.get('role') == 'user' else 'Friday'
            conversation += f"{role_label}: {msg.get('text', '')}\n"
        conversation += f'User: {message}'

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=conversation
        )

        reply = response.text

        # Store in history with IDs, timestamps, and context metadata
        user_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'user',
            'text': message,
            'pinned': False,
            'workspace': workspace,
        }
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False,
            'sources': sources,
        }
        CHAT_HISTORY.append(user_msg)
        CHAT_HISTORY.append(friday_msg)

        # Prune: keep pinned forever, others for 30 days, cap at 500 messages
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)

        return jsonify({
            "response": reply,
            "user_msg": user_msg,
            "friday_msg": friday_msg,
            "sources": sources,
        })
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
    """Send a message, save to persistent history, return Friday's response.
    Accepts context-aware payload: {message, workspace, workspaceContext, includeVision, screenshot}
    """
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        data = request.get_json(silent=True) or {}
        message = data.get('message', '')
        workspace = data.get('workspace', '')
        workspace_context = data.get('workspaceContext', None)
        include_vision = data.get('includeVision', False)
        vision_description = None

        if not message.strip():
            return jsonify({"status": "error", "message": "Empty message"}), 400

        # Vision capture
        screenshot_b64 = data.get('screenshot', None)
        if include_vision and screenshot_b64:
            try:
                from google.genai import types
                img_bytes = base64.b64decode(screenshot_b64)
                vision_resp = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        "Briefly describe what is visible on this screen. Focus on text, UI elements, and data shown. Be concise (2-3 sentences).",
                        types.Part.from_bytes(data=img_bytes, mime_type='image/png'),
                    ],
                )
                vision_description = vision_resp.text
            except Exception as ve:
                vision_description = f"[Vision unavailable: {ve}]"

        # Build context-enriched system prompt
        system_prompt, sources = _build_context_prompt(
            message, workspace, workspace_context, vision_description
        )

        # Build conversation with history
        conversation = system_prompt + '\n\n'
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
            'pinned': False,
            'workspace': workspace,
        }
        friday_msg = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now().isoformat(),
            'role': 'friday',
            'text': reply,
            'pinned': False,
            'sources': sources,
        }
        CHAT_HISTORY.append(user_msg)
        CHAT_HISTORY.append(friday_msg)

        # Prune and save
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        CHAT_HISTORY[:] = [m for m in CHAT_HISTORY if m.get('pinned') or m.get('timestamp', '') >= cutoff][-500:]
        _save_chat_history(CHAT_HISTORY)

        return jsonify({"status": "ok", "user_msg": user_msg, "friday_msg": friday_msg, "sources": sources})
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
    """Text-to-speech using Gemini 2.5 Flash TTS model — returns WAV binary directly."""
    try:
        import wave
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        text = request.json.get('text', '')
        voice = request.json.get('voice', 'Kore')

        if not text:
            return jsonify({"status": "error", "message": "No text provided"}), 400

        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=f"Say this aloud: {text}",
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice
                        )
                    )
                )
            )
        )

        audio_data = response.candidates[0].content.parts[0].inline_data.data

        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_data)
        buf.seek(0)
        return send_file(buf, mimetype='audio/wav')

    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


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
    'ofw_response': (
        "You are drafting a response for OurFamilyWizard (co-parenting communication platform). "
        "CRITICAL RULES: Stay calm, factual, and brief. Answer only what needs answering. "
        "Ignore all bait and emotional provocation. Never match the other party's emotional register. "
        "Everything you write should be something a family court judge would find reasonable, measured, and cooperative. "
        "Do not over-explain, do not defend, do not attack. Short sentences. Airtight logic."
    ),
    'freeform': (
        "You are a versatile writing assistant. Follow the user's format instructions exactly. "
        "Write clearly and concisely unless told otherwise."
    ),
}

COPARENTING_DIR = HOME / ".friday" / "wiki" / "coparenting"


def _load_ofw_context():
    """Load co-parenting wiki context for OFW drafts."""
    context_parts = []
    if COPARENTING_DIR.exists():
        for md_file in sorted(COPARENTING_DIR.glob('*.md'))[:5]:
            try:
                text = md_file.read_text(encoding='utf-8')[:2000]
                context_parts.append(f"[{md_file.name}]: {text}")
            except Exception:
                continue
    return '\n\n'.join(context_parts) if context_parts else ''


@app.route('/api/draft', methods=['POST'])
def draft_generate():
    """Generate a draft using Gemini based on mode, context, and prompt."""
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        data = request.get_json(silent=True) or {}

        mode = data.get('mode', 'freeform')
        context = data.get('context', '')
        prompt = data.get('prompt', '')

        if not prompt.strip():
            return jsonify({"status": "error", "message": "No prompt provided"}), 400

        # Build the system prompt for this mode
        system = DRAFT_MODE_PROMPTS.get(mode, DRAFT_MODE_PROMPTS['freeform'])

        # For OFW mode, inject co-parenting context
        if mode == 'ofw_response':
            ofw_ctx = _load_ofw_context()
            if ofw_ctx:
                system += f"\n\nCO-PARENTING CONTEXT (from wiki):\n{ofw_ctx}"

        # Build the full prompt
        parts = [system]
        if context:
            parts.append(f"\nCONTEXT (what the user is looking at / replying to):\n{context}")
        parts.append(f"\nUSER INSTRUCTION:\n{prompt}")
        parts.append("\nWrite the draft now. Output ONLY the draft text, no commentary or labels.")

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents='\n'.join(parts)
        )

        draft_text = response.text.strip()

        return jsonify({
            "status": "ok",
            "draft": draft_text,
            "mode": mode,
            "char_count": len(draft_text),
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/draft/deploy', methods=['POST'])
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
                capture_output=True, text=True, timeout=10
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
            capture_output=True, text=True, timeout=10
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


@app.route('/api/flow', methods=['POST'])
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


@app.route('/api/flow/queue', methods=['GET'])
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


@app.route('/api/calendar/enrich', methods=['POST'])
def calendar_enrich():
    """Enrich a Google Calendar event with meeting prep research.

    POST JSON:
    {
      "event_id": "google calendar event ID",
      "research": "the attendee research / meeting prep content"
    }
    """
    data = request.get_json(silent=True) or {}
    event_id = data.get('event_id', '').strip()
    research = data.get('research', '').strip()

    if not event_id:
        return jsonify({"status": "error", "message": "No event_id provided"}), 400
    if not research:
        return jsonify({"status": "error", "message": "No research content provided"}), 400

    try:
        result = _enrich_calendar_event(event_id, research)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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


@app.route('/api/flow/draft/confirm', methods=['POST'])
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


# ═══════════════════════════════════════════════════════════════
#  CONTACTS / CRM
# ═══════════════════════════════════════════════════════════════

def _load_trust_graph():
    """Load trust graph with consistent shape. Returns dict with people keyed by name."""
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


@app.route('/api/contacts')
def get_contacts():
    """Merged contact list built from trust_graph.json."""
    contacts = _contacts_list()
    return jsonify({"status": "ok", "contacts": contacts, "count": len(contacts)})


@app.route('/api/contacts/<path:name>')
def get_contact(name):
    """Full trust dimensions + evidence for a single contact (case-insensitive name)."""
    graph = _load_trust_graph()
    raw = graph.get('people') or {}
    target = (name or '').strip().lower()
    match = None
    if isinstance(raw, dict):
        if target in raw:
            match = raw[target]
        else:
            for k, v in raw.items():
                if not isinstance(v, dict):
                    continue
                cand = (v.get('name') or k or '').strip().lower()
                aliases = [a.lower() for a in (v.get('aliases') or [])]
                if cand == target or target in aliases:
                    match = v
                    break
    else:
        for v in raw:
            if not isinstance(v, dict):
                continue
            cand = (v.get('name') or '').strip().lower()
            aliases = [a.lower() for a in (v.get('aliases') or [])]
            if cand == target or target in aliases:
                match = v
                break
    if not match:
        return jsonify({"status": "error", "message": "Contact not found"}), 404

    # Look for a stored research file.
    research_file = _contacts_research_dir() / f"{target.replace(' ', '_')}.md"
    research = research_file.read_text(encoding='utf-8') if research_file.exists() else ''

    return jsonify({"status": "ok", "contact": match, "research": research})


@app.route('/api/contacts/research', methods=['POST'])
def contacts_research():
    """Kick off web research on a contact. Writes a stub and launches a background terminal."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"status": "error", "message": "name required"}), 400
    key = name.lower().replace(' ', '_')
    research_file = _contacts_research_dir() / f"{key}.md"
    stamp = datetime.now().isoformat()
    if not research_file.exists():
        research_file.write_text(
            f"# Research: {name}\n\n_Initialized {stamp}_\n\n"
            f"- Public profile search: pending\n"
            f"- LinkedIn / GitHub: pending\n"
            f"- Recent news mentions: pending\n",
            encoding='utf-8'
        )
    try:
        tid = str(uuid.uuid4())[:8]
        VIBE_TERMINALS[tid] = {
            "id": tid, "task": f"Research contact: {name}",
            "status": "pending", "cwd": str(FRIDAY_DIR),
            "started": stamp, "log_file": None
        }
    except Exception:
        tid = None
    return jsonify({
        "status": "ok", "name": name,
        "research_file": str(research_file),
        "task_id": tid,
        "message": f"Research queued for {name}"
    })


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
    {"id": "libby-weekend-prep", "label": "***REMOVED*** Weekend Prep",  "ico": "👧", "category": "family",      "schedule": "Thursdays · 6:00 PM"},
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


@app.route('/api/routines')
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


@app.route('/api/routines/<routine_id>/run', methods=['POST'])
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
        print(f"  [FRIDAY] outreach log save failed: {e}")


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


@app.route('/api/outreach/suggestions')
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


@app.route('/api/outreach/draft', methods=['POST'])
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
        f"Tone: warm, concise, specific. Sender: Stephen Webster (FutureSpeak.AI). "
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
            f"— Stephen"
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


@app.route('/api/outreach/log', methods=['POST'])
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


@app.route('/api/outreach/pipeline')
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
        print(f"  [FRIDAY] content pipeline save failed: {e}")


@app.route('/api/content/pipeline')
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


@app.route('/api/content/idea', methods=['POST'])
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


@app.route('/api/content/draft', methods=['POST'])
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
        f"Author: Stephen Webster (FutureSpeak.AI). "
        f"Tone: sharp, specific, credible. "
        f"Structure: hook, 2-3 body beats, ask/CTA. "
        f"Length: 180-260 words for LinkedIn, longer for article. "
        f"Context / notes: {notes}"
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
        print(f"  [FRIDAY] content draft Gemini error: {e}")

    if not draft_text:
        draft_text = (
            f"[{channel.upper()} DRAFT — {title}]\n\n"
            f"Hook: (one-line opener)\n\n"
            f"Body:\n- Point 1\n- Point 2\n- Point 3\n\n"
            f"Notes: {notes or '(no notes)'}\n\n"
            f"CTA: (single ask)\n\n— Stephen"
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


# ═══════════════════════════════════════════════════════════════
#  FUTURESPEAK BUSINESS WORKSPACE
# ═══════════════════════════════════════════════════════════════

FUTURESPEAK_DIR = FRIDAY_DIR / "futurespeak"
FUTURESPEAK_DIR.mkdir(parents=True, exist_ok=True)
FS_PIPELINE_FILE = FUTURESPEAK_DIR / "pipeline.json"
FS_REVENUE_FILE = FUTURESPEAK_DIR / "revenue.json"
FS_LEGAL_FILE = FUTURESPEAK_DIR / "legal.json"
FS_ASSETS_DIR = FUTURESPEAK_DIR / "demo-assets"
FS_ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def _fs_load(path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return fallback


@app.route('/api/futurespeak/pipeline')
def fs_pipeline():
    data = _fs_load(FS_PIPELINE_FILE, {"opportunities": []})
    opps = data.get('opportunities', []) or []
    total_value = sum((o.get('value_usd') or 0) for o in opps)
    weighted = sum((o.get('value_usd') or 0) * (o.get('probability') or 0) for o in opps)
    by_status = {}
    for o in opps:
        s = o.get('status', 'unknown')
        by_status[s] = by_status.get(s, 0) + 1
    return jsonify({
        "status": "ok",
        "opportunities": opps,
        "total": len(opps),
        "total_value": total_value,
        "weighted_value": weighted,
        "by_status": by_status,
    })


@app.route('/api/futurespeak/revenue')
def fs_revenue():
    data = _fs_load(FS_REVENUE_FILE, {"months": [], "quarters": []})
    months = data.get('months', []) or []
    quarters = data.get('quarters', []) or []
    burn = data.get('monthly_burn') or 0
    cash = data.get('cash_on_hand') or 0

    last_actual = 0
    for m in months:
        if isinstance(m.get('actual'), (int, float)):
            last_actual = m['actual']
    net_monthly = last_actual - burn
    runway_months = None
    if burn > 0 and net_monthly < 0:
        runway_months = round(cash / burn, 1)

    ytd_actual = sum(m.get('actual') or 0 for m in months)
    ytd_projected = sum(m.get('projected') or 0 for m in months)

    return jsonify({
        "status": "ok",
        "currency": data.get('currency', 'USD'),
        "months": months,
        "quarters": quarters,
        "monthly_burn": burn,
        "cash_on_hand": cash,
        "last_actual_month": last_actual,
        "net_monthly": net_monthly,
        "runway_months": runway_months,
        "ytd_actual": ytd_actual,
        "ytd_projected": ytd_projected,
    })


@app.route('/api/futurespeak/legal')
def fs_legal():
    data = _fs_load(FS_LEGAL_FILE, {"items": []})
    items = data.get('items', []) or []
    by_status, by_type = {}, {}
    for it in items:
        s = it.get('status', 'unknown')
        t = it.get('type', 'other')
        by_status[s] = by_status.get(s, 0) + 1
        by_type[t] = by_type.get(t, 0) + 1
    return jsonify({
        "status": "ok",
        "items": items,
        "total": len(items),
        "by_status": by_status,
        "by_type": by_type,
    })


@app.route('/api/futurespeak/assets')
def fs_assets():
    assets = []
    if FS_ASSETS_DIR.exists():
        for p in sorted(FS_ASSETS_DIR.iterdir()):
            try:
                stat = p.stat()
                assets.append({
                    "name": p.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "ext": p.suffix.lower().lstrip('.'),
                    "kind": "dir" if p.is_dir() else "file",
                })
            except Exception:
                continue
    return jsonify({"status": "ok", "assets": assets, "total": len(assets), "path": str(FS_ASSETS_DIR)})


# ═══════════════════════════════════════════════════════════════
#  FRIDAY LIVE — Gemini Live API bridge over WebSocket
# ═══════════════════════════════════════════════════════════════

LIVE_MODEL = os.environ.get("FRIDAY_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")
LIVE_VOICE = os.environ.get("FRIDAY_LIVE_VOICE", "Aoede")

LIVE_SYSTEM_TEMPLATE = """You are Agent Friday, a personal AI assistant for Stephen Webster.
You are having a live voice conversation. Be concise and natural — this is spoken dialogue, not text chat.
Short sentences. Pause. Let him interrupt. If he doesn't hear you the first time, repeat simpler.

You can see through Stephen's phone camera. If you notice something interesting or relevant, mention it naturally.
Don't narrate what's on screen unless asked — only speak up when it matters.

Personality: knowledgeable, direct collaborator. No sycophancy. Independent thinker. Journalist-level communication.
Trust Stephen's judgment; push back when you genuinely disagree, but don't lecture.

=== DAILY CONTEXT ===
{context_summary}
=== END CONTEXT ===
"""


def _strip_html(raw: str) -> str:
    raw = re.sub(r'<script\b[^>]*>.*?</script>', ' ', raw, flags=re.S | re.I)
    raw = re.sub(r'<style\b[^>]*>.*?</style>', ' ', raw, flags=re.S | re.I)
    raw = re.sub(r'<[^>]+>', ' ', raw)
    raw = re.sub(r'&nbsp;', ' ', raw)
    raw = re.sub(r'&amp;', '&', raw)
    raw = re.sub(r'&lt;', '<', raw)
    raw = re.sub(r'&gt;', '>', raw)
    raw = re.sub(r'\s+', ' ', raw)
    return raw.strip()


def _load_live_context() -> str:
    """Build a concise context summary string for the Friday Live system prompt."""
    parts = [f"TODAY: {date.today().isoformat()}"]

    # Latest briefing (plain-text excerpt)
    try:
        briefings_dir = HOME / ".friday" / "wiki" / "briefings"
        if briefings_dir.exists():
            candidates = sorted(
                (p for p in briefings_dir.iterdir() if p.suffix in ('.html', '.md')),
                reverse=True,
            )
            if candidates:
                latest = candidates[0]
                raw = latest.read_text(encoding='utf-8', errors='ignore')
                text = _strip_html(raw) if latest.suffix == '.html' else raw
                parts.append(f"LATEST BRIEFING ({latest.name}):\n{text[:1800]}")
    except Exception as e:
        parts.append(f"(briefing load failed: {e})")

    # Career pipeline
    try:
        tracker_candidates = [WIKI_PROFESSIONAL_DIR / 'application-log.md', CAREER_OPS_DIR / 'applications.md']
        tracker = next((p for p in tracker_candidates if p.exists()), None)
        if tracker:
            raw = tracker.read_text(encoding='utf-8', errors='ignore')
            parts.append(f"CAREER PIPELINE (top):\n{raw[:1200]}")
    except Exception:
        pass

    # Upcoming countdowns (<=90 days)
    try:
        today_d = date.today()
        events = [
            {"label": "***REMOVED***'s Birthday", "date": "***REMOVED***"},
            {"label": "Summer Solstice", "date": "2026-06-21"},
            {"label": "Father's Day", "date": "2026-06-21"},
            {"label": "Independence Day", "date": "2026-07-04"},
        ]
        cd = []
        for ev in events:
            d = date.fromisoformat(ev['date'])
            delta = (d - today_d).days
            if 0 <= delta <= 90:
                cd.append(f"- {ev['label']}: {delta} days away ({ev['date']})")
        if cd:
            parts.append("UPCOMING:\n" + "\n".join(cd))
    except Exception:
        pass

    # Trust graph — top names
    try:
        tfile = FRIDAY_DIR / "trust_graph.json"
        if tfile.exists():
            data = json.loads(tfile.read_text(encoding='utf-8'))
            people = data.get('people') or {}
            items = []
            for name, info in people.items():
                score = 0
                role = ''
                if isinstance(info, dict):
                    score = info.get('score') or info.get('trust_score') or 0
                    role = info.get('role') or info.get('relation') or info.get('relationship') or ''
                try:
                    score = float(score)
                except Exception:
                    score = 0.0
                items.append((name, score, role))
            items.sort(key=lambda x: x[1], reverse=True)
            top = items[:8]
            if top:
                lines = [f"- {n}" + (f" ({r})" if r else '') for n, _s, r in top]
                parts.append("TRUST CIRCLE (top 8):\n" + "\n".join(lines))
    except Exception:
        pass

    # Personality snapshot
    try:
        pfile = FRIDAY_DIR / "personality.json"
        if pfile.exists():
            data = json.loads(pfile.read_text(encoding='utf-8'))
            parts.append(f"PERSONALITY: {json.dumps(data)[:500]}")
    except Exception:
        pass

    return "\n\n".join(parts)


if sock is not None:

    @sock.route('/ws/live')
    def ws_live(ws):
        """Bridge a browser WebSocket to a Gemini Live API session.

        Messages from browser -> Gemini:
          { type: 'audio', data: <b64 PCM16 @ 16 kHz> }
          { type: 'image', data: <b64 JPEG> }
          { type: 'text', text: "..." }
          { type: 'end' }
        Messages from Gemini -> browser:
          { type: 'audio', data: <b64 PCM16 @ 24 kHz> }
          { type: 'text', text: "..." }           # model text or transcript
          { type: 'input_transcript', text: ... } # user transcript
          { type: 'status', text: "..." }
          { type: 'turn_end' }
          { type: 'error', error: "..." }
        """
        # Auth enforcement (before_request already redirects unauthenticated HTML
        # requests, but be defensive in case /ws/ paths were excluded).
        if FRIDAY_PASSWORD and not session.get("authenticated"):
            try:
                ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
            except Exception:
                pass
            return

        if not GEMINI_API_KEY:
            try:
                ws.send(json.dumps({"type": "error", "error": "GEMINI_API_KEY not set"}))
            except Exception:
                pass
            return

        try:
            from google import genai
            from google.genai import types
        except ImportError:
            try:
                ws.send(json.dumps({"type": "error", "error": "google-genai not installed"}))
            except Exception:
                pass
            return

        try:
            ctx = _load_live_context()
        except Exception as e:
            ctx = f"(context load failed: {e})"
        system_instruction = LIVE_SYSTEM_TEMPLATE.format(context_summary=ctx)

        try:
            ws.send(json.dumps({"type": "status", "text": "loading context"}))
        except Exception:
            return

        # Build a client for the Live API. Live requires the v1beta API surface.
        try:
            client = genai.Client(
                api_key=GEMINI_API_KEY,
                http_options=types.HttpOptions(api_version='v1beta'),
            )
        except Exception:
            # Fallback: older SDKs accept dict-form http_options
            client = genai.Client(
                api_key=GEMINI_API_KEY,
                http_options={'api_version': 'v1beta'},
            )

        cfg = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=LIVE_VOICE)
                )
            ),
            system_instruction=system_instruction,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        done = threading.Event()

        def _safe_send(obj):
            if done.is_set():
                return False
            try:
                ws.send(json.dumps(obj))
                return True
            except ConnectionClosed:
                done.set()
                return False
            except Exception:
                return False

        async def runner():
            try:
                async with client.aio.live.connect(model=LIVE_MODEL, config=cfg) as session_ai:
                    _safe_send({"type": "status", "text": "live"})

                    async def reader():
                        """Pump messages from the browser WebSocket into the Gemini session."""
                        while not done.is_set():
                            try:
                                raw = await asyncio.to_thread(ws.receive, 1.0)
                            except ConnectionClosed:
                                done.set()
                                return
                            except Exception:
                                continue
                            if raw is None:
                                continue
                            if isinstance(raw, bytes):
                                try:
                                    raw = raw.decode('utf-8')
                                except Exception:
                                    continue
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue
                            t = msg.get('type')
                            try:
                                if t == 'audio' and msg.get('data'):
                                    data = base64.b64decode(msg['data'])
                                    await session_ai.send_realtime_input(
                                        audio=types.Blob(data=data, mime_type='audio/pcm;rate=16000')
                                    )
                                elif t == 'image' and msg.get('data'):
                                    data = base64.b64decode(msg['data'])
                                    await session_ai.send_realtime_input(
                                        video=types.Blob(data=data, mime_type='image/jpeg')
                                    )
                                elif t == 'text' and msg.get('text'):
                                    await session_ai.send_client_content(
                                        turns=[types.Content(
                                            role='user',
                                            parts=[types.Part(text=msg['text'])],
                                        )],
                                        turn_complete=True,
                                    )
                                elif t == 'end':
                                    done.set()
                                    return
                            except Exception as e:
                                print(f'[live] send-to-gemini error: {e}')

                    async def writer():
                        """Pump Gemini responses back to the browser WebSocket."""
                        try:
                            async for chunk in session_ai.receive():
                                if done.is_set():
                                    return
                                try:
                                    # Audio bytes (convenience property)
                                    audio = getattr(chunk, 'data', None)
                                    if audio:
                                        _safe_send({
                                            "type": "audio",
                                            "data": base64.b64encode(audio).decode('ascii'),
                                        })
                                    # Server content details
                                    sc = getattr(chunk, 'server_content', None)
                                    if sc is not None:
                                        # Output transcription (what Gemini said)
                                        out_tr = getattr(sc, 'output_transcription', None)
                                        if out_tr and getattr(out_tr, 'text', None):
                                            _safe_send({"type": "text", "text": out_tr.text})
                                        # Input transcription (what user said)
                                        in_tr = getattr(sc, 'input_transcription', None)
                                        if in_tr and getattr(in_tr, 'text', None):
                                            _safe_send({"type": "input_transcript", "text": in_tr.text})
                                        # Any text in model turn parts
                                        mt = getattr(sc, 'model_turn', None)
                                        if mt and getattr(mt, 'parts', None):
                                            for part in mt.parts:
                                                pt = getattr(part, 'text', None)
                                                if pt:
                                                    _safe_send({"type": "text", "text": pt})
                                        if getattr(sc, 'turn_complete', False):
                                            _safe_send({"type": "turn_end"})
                                        if getattr(sc, 'interrupted', False):
                                            _safe_send({"type": "interrupted"})
                                except Exception as e:
                                    print(f'[live] recv error: {e}')
                        except Exception as e:
                            print(f'[live] session recv ended: {e}')
                        finally:
                            done.set()

                    await asyncio.gather(reader(), writer(), return_exceptions=True)
            except Exception as e:
                print(f'[live] session error: {e}')
                traceback.print_exc()
                _safe_send({"type": "error", "error": str(e)})

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(runner())
        finally:
            done.set()
            try:
                loop.close()
            except Exception:
                pass
            try:
                ws.close()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
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

    # Bind 0.0.0.0 when tunnel/remote access is needed, else localhost only
    bind_host = '0.0.0.0' if FRIDAY_PASSWORD else '127.0.0.1'
    app.run(host=bind_host, port=3000, debug=False)
