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
    HOME,
    VIBE_TERMINALS,
    _POPEN_FLAGS,
)  # noqa: E501
from agent_friday.services.code_engine import (
    CODE_PLANS_DIR,
    CODE_PROCESSES,
    PROJECTS_DIR,
    _CODE_LOG_BUF,
    _CODE_LOG_LOCK,
    _CODE_LOG_SUBS,
    _LANG_BY_EXT,
    _SKIP_DIRS,
    _code_log,
    _dev_git,
    _git_repo_summary,
    _git_result,
    _projects_root,
    _repo_path,
    _repo_tree,
    _run_claude_terminal,
    _safe_project_path,
)  # noqa: E501
from agent_friday.services.model_router import (
    _generate_text,
    _get_friday_system_prompt,
)  # noqa: E501

code_bp = Blueprint('code', __name__)



@code_bp.route('/api/vibe-code/launch', methods=['POST'])
def vibe_code_launch():
    """Launch Claude Code terminals with tasks."""
    data = request.get_json(silent=True) or {}
    tasks = data.get('tasks', [])
    cwd = os.path.normpath(os.path.expanduser(data.get('cwd', str(HOME / 'Projects'))))

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


@code_bp.route('/api/vibe-code/status')
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


@code_bp.route('/api/vibe-code/stop', methods=['POST'])
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
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, creationflags=_POPEN_FLAGS)
            except Exception:
                pass
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Terminal not found"}), 404


@code_bp.route('/api/vibe-code/clear', methods=['POST'])
def vibe_code_clear():
    """Clear all completed/stopped terminals."""
    to_remove = [tid for tid, t in VIBE_TERMINALS.items() if t['status'] in ('stopped', 'error', 'completed')]
    for tid in to_remove:
        del VIBE_TERMINALS[tid]
    return jsonify({"status": "ok", "removed": len(to_remove)})


@code_bp.route('/api/vibe-code/presets')
def vibe_code_presets():
    """Return available workflow presets."""
    return jsonify({"status": "ok", "presets": [
        {"name": "Full Stack Sprint", "tasks": ["Build the frontend UI", "Build the backend API", "Write integration tests"]},
        {"name": "Bug Hunt", "tasks": ["Find and fix all TypeScript errors", "Run test suite and fix failures"]},
        {"name": "Documentation Blitz", "tasks": ["Generate API documentation", "Write README.md", "Add JSDoc comments"]},
        {"name": "Security Audit", "tasks": ["Scan for dependency vulnerabilities", "Check for hardcoded secrets", "Review auth flow"]},
    ]})


# ── LOGS: live streaming ───────────────────────────────────────
@code_bp.route('/api/logs/recent')
def code_logs_recent():
    """Return the recent log ring buffer (for initial paint / SSE fallback)."""
    try:
        limit = int(request.args.get('limit', 200))
    except Exception:
        limit = 200
    with _CODE_LOG_LOCK:
        items = list(_CODE_LOG_BUF)[-limit:]
    return jsonify({"status": "ok", "events": items, "count": len(items)})


@code_bp.route('/api/logs/stream')
def code_logs_stream():
    """Server-Sent Events stream of all Dev Studio log activity."""
    def gen():
        q = _queue.Queue(maxsize=500)
        with _CODE_LOG_LOCK:
            backlog = list(_CODE_LOG_BUF)[-50:]
            _CODE_LOG_SUBS.append(q)
        try:
            yield "retry: 3000\n\n"
            for evt in backlog:
                yield f"data: {json.dumps(evt)}\n\n"
            while True:
                try:
                    evt = q.get(timeout=20)
                    yield f"data: {json.dumps(evt)}\n\n"
                except _queue.Empty:
                    # Heartbeat keeps the connection (and any proxy) alive.
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            with _CODE_LOG_LOCK:
                try:
                    _CODE_LOG_SUBS.remove(q)
                except ValueError:
                    pass
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(stream_with_context(gen()), headers=headers)


@code_bp.route('/api/logs/emit', methods=['POST'])
def code_logs_emit():
    """Manually push a log line (used by clients / external tasks)."""
    data = request.get_json(silent=True) or {}
    msg = (data.get('message') or '').strip()
    if not msg:
        return jsonify({"status": "error", "message": "message required"}), 400
    evt = _code_log(msg, source=data.get('source', 'client'), level=data.get('level', 'info'))
    return jsonify({"status": "ok", "event": evt})


# ── REPOS: dashboard ───────────────────────────────────────────
@code_bp.route('/api/repos/scan')
def repos_scan():
    """Scan ~/Projects/ for git repos and return status cards."""
    root = PROJECTS_DIR
    repos = []
    if not root.exists():
        return jsonify({"status": "ok", "repos": [], "root": str(root),
                        "message": "~/Projects does not exist yet."})
    try:
        children = sorted([d for d in root.iterdir() if d.is_dir()], key=lambda p: p.name.lower())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    for d in children:
        if (d / ".git").exists():
            rp = _repo_path(d.name)
            if rp:
                repos.append(_git_repo_summary(rp))
    return jsonify({"status": "ok", "repos": repos, "root": str(root), "count": len(repos)})


@code_bp.route('/api/repos/<name>/status')
def repos_status(name):
    rp = _repo_path(name)
    if not rp:
        return jsonify({"status": "error", "message": "repo not found in ~/Projects"}), 404
    card = _git_repo_summary(rp)
    # Attach the porcelain file list for the detail view.
    files = []
    try:
        st = _dev_git(rp, "status", "--porcelain", timeout=15)
        for line in st.stdout.splitlines():
            if len(line) >= 3:
                files.append({"code": line[:2].strip() or "?", "file": line[3:]})
    except Exception:
        pass
    card["files"] = files
    return jsonify({"status": "ok", "repo": card})


@code_bp.route('/api/git/diff')
def git_diff():
    rp = _repo_path(request.args.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    target = request.args.get('file')
    try:
        args = ["diff", "--no-color"]
        if request.args.get('staged') in ('1', 'true', 'yes'):
            args.append("--cached")
        if target:
            safe = _safe_project_path(os.path.join(rp, target))
            if not safe:
                return jsonify({"status": "error", "message": "bad path"}), 400
            args += ["--", target]
        cp = _dev_git(rp, *args, timeout=20)
        diff = cp.stdout or ""
        if not diff.strip():
            # Include untracked content as a synthetic add-diff so the UI shows new files too.
            unt = _dev_git(rp, "ls-files", "--others", "--exclude-standard", timeout=15)
            files = [f for f in unt.stdout.splitlines() if f.strip()]
            if files:
                diff = "Untracked files:\n" + "\n".join("  + " + f for f in files)
        return jsonify({"status": "ok", "diff": diff})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/git/branches')
def git_branches():
    rp = _repo_path(request.args.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    try:
        cur = _dev_git(rp, "rev-parse", "--abbrev-ref", "HEAD", timeout=10).stdout.strip()
        cp = _dev_git(rp, "branch", "--format=%(refname:short)", timeout=10)
        branches = [b.strip() for b in cp.stdout.splitlines() if b.strip()]
        return jsonify({"status": "ok", "branches": branches, "current": cur})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/git/pull', methods=['POST'])
def git_pull():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    try:
        cp = _dev_git(rp, "pull", "--ff-only", timeout=120)
        return jsonify(_git_result(rp, cp, "pull"))
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "pull timed out"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/git/push', methods=['POST'])
def git_push():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    # SAFETY: never allow force pushes, no matter what the client sends.
    try:
        branch = _dev_git(rp, "rev-parse", "--abbrev-ref", "HEAD", timeout=10).stdout.strip()
        has_up = _dev_git(rp, "rev-parse", "--abbrev-ref", "@{u}", timeout=10).returncode == 0
        if has_up:
            cp = _dev_git(rp, "push", timeout=120)
        else:
            cp = _dev_git(rp, "push", "--set-upstream", "origin", branch, timeout=120)
        return jsonify(_git_result(rp, cp, "push"))
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "push timed out"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/git/checkout', methods=['POST'])
def git_checkout():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    branch = (data.get('branch') or '').strip()
    if not branch or not re.match(r'^[\w./\-]+$', branch):
        return jsonify({"status": "error", "message": "invalid branch name"}), 400
    create = bool(data.get('create'))
    try:
        args = ["checkout", "-b", branch] if create else ["checkout", branch]
        cp = _dev_git(rp, *args, timeout=30)
        return jsonify(_git_result(rp, cp, "checkout " + branch))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/git/branch', methods=['POST'])
def git_branch_create():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    name = (data.get('name') or '').strip()
    if not name or not re.match(r'^[\w./\-]+$', name):
        return jsonify({"status": "error", "message": "invalid branch name"}), 400
    try:
        cp = _dev_git(rp, "checkout", "-b", name, timeout=30)
        return jsonify(_git_result(rp, cp, "branch " + name))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/git/commit', methods=['POST'])
def git_commit():
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    msg = (data.get('message') or '').strip()
    if not msg:
        return jsonify({"status": "error", "message": "commit message required"}), 400
    try:
        if data.get('add_all', True):
            _dev_git(rp, "add", "-A", timeout=30)
        cp = _dev_git(rp, "commit", "-m", msg, timeout=30)
        res = _git_result(rp, cp, "commit")
        if not res["ok"] and "nothing to commit" in (res["stdout"] + res["stderr"]).lower():
            res["message"] = "Nothing to commit — working tree clean."
        return jsonify(res)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/git/pr', methods=['POST'])
def git_pr():
    """Open a PR via the GitHub CLI (gh). Pushes the branch first."""
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found"}), 404
    title = (data.get('title') or '').strip()
    body = (data.get('body') or '').strip()
    if not title:
        return jsonify({"status": "error", "message": "PR title required"}), 400
    try:
        branch = _dev_git(rp, "rev-parse", "--abbrev-ref", "HEAD", timeout=10).stdout.strip()
        # Ensure the branch is on origin first (non-force).
        if _dev_git(rp, "rev-parse", "--abbrev-ref", "@{u}", timeout=10).returncode != 0:
            _dev_git(rp, "push", "--set-upstream", "origin", branch, timeout=120)
        cp = subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body or title],
            cwd=rp, capture_output=True, text=True, timeout=60, creationflags=_POPEN_FLAGS,
        )
        ok = cp.returncode == 0
        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()
        _code_log(f"gh pr create -> {'ok' if ok else 'FAILED'} :: {(out or err)[:300]}",
                  source=f"git:{os.path.basename(rp)}", level="info" if ok else "error")
        url = ""
        m = re.search(r'https?://\S+', out)
        if m:
            url = m.group(0)
        msg = err if not ok else out
        if not ok and ("not found" in err.lower() or "is not recognized" in err.lower()):
            msg = "GitHub CLI (gh) not installed or not on PATH. Install from https://cli.github.com/"
        return jsonify({"status": "ok" if ok else "error", "ok": ok, "url": url,
                        "stdout": out, "stderr": err, "message": msg})
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "GitHub CLI (gh) not installed. https://cli.github.com/"}), 200
    except subprocess.TimeoutExpired:
        return jsonify({"status": "error", "message": "gh pr create timed out"}), 504
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/files/list')
def files_list():
    """List a directory inside ~/Projects/ (dirs first, then files)."""
    rel = request.args.get('path', '')
    target = _safe_project_path(rel) if rel else _projects_root()
    if not target or not os.path.isdir(target):
        return jsonify({"status": "error", "message": "path not found in ~/Projects"}), 404
    entries = []
    try:
        for entry in sorted(os.scandir(target), key=lambda e: (not e.is_dir(), e.name.lower())):
            if entry.name in _SKIP_DIRS:
                continue
            is_dir = entry.is_dir()
            try:
                size = entry.stat().st_size if not is_dir else 0
            except Exception:
                size = 0
            entries.append({
                "name": entry.name,
                "path": os.path.relpath(entry.path, _projects_root()).replace("\\", "/"),
                "dir": is_dir,
                "size": size,
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    parent = None
    if os.path.realpath(target) != _projects_root():
        parent = os.path.relpath(os.path.dirname(target), _projects_root()).replace("\\", "/")
        if parent == ".":
            parent = ""
    return jsonify({"status": "ok", "path": os.path.relpath(target, _projects_root()).replace("\\", "/"),
                    "parent": parent, "entries": entries})


@code_bp.route('/api/files/read')
def files_read():
    """Read a file inside ~/Projects/ with detected language for highlighting."""
    rel = request.args.get('path', '')
    target = _safe_project_path(rel)
    if not target or not os.path.isfile(target):
        return jsonify({"status": "error", "message": "file not found in ~/Projects"}), 404
    try:
        size = os.path.getsize(target)
        if size > 1024 * 1024:
            return jsonify({"status": "error", "message": "file too large to preview (>1 MB)"}), 413
        with open(target, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    ext = os.path.splitext(target)[1].lstrip('.').lower()
    return jsonify({"status": "ok", "content": content, "lang": _LANG_BY_EXT.get(ext, 'plaintext'),
                    "ext": ext, "size": size, "lines": content.count("\n") + 1,
                    "name": os.path.basename(target)})


@code_bp.route('/api/code/plan', methods=['POST'])
def code_plan():
    """Natural language -> Claude generates a structured code plan with file changes."""
    data = request.get_json(silent=True) or {}
    rp = _repo_path(data.get('repo', ''))
    if not rp:
        return jsonify({"status": "error", "message": "repo not found in ~/Projects"}), 404
    instruction = (data.get('instruction') or '').strip()
    if not instruction:
        return jsonify({"status": "error", "message": "instruction required"}), 400
    # No Anthropic-key pre-flight gate: _generate_text() below routes to whatever
    # provider is configured (Ollama/OpenAI/Anthropic) and surfaces a clear error
    # if none is reachable, so code planning works on a local-only setup too.

    _code_log(f"planning: {instruction[:120]}", source="vibe", level="info")
    tree = _repo_tree(rp)
    # Pull in the contents of any files the instruction names, plus README for grounding.
    ctx_files = []
    for rel in tree:
        base = os.path.basename(rel).lower()
        if base in ('readme.md', 'package.json', 'requirements.txt'):
            ctx_files.append(rel)
    for rel in tree:
        stem = os.path.splitext(os.path.basename(rel))[0].lower()
        if stem and stem in instruction.lower() and rel not in ctx_files:
            ctx_files.append(rel)
    ctx_blocks = []
    for rel in ctx_files[:6]:
        try:
            fp = os.path.join(rp, rel)
            if os.path.getsize(fp) <= 40000:
                with open(fp, 'r', encoding='utf-8', errors='replace') as f:
                    ctx_blocks.append(f"### {rel}\n```\n{f.read()}\n```")
        except Exception:
            pass

    user_prompt = (
        f"You are working in the git repo `{os.path.basename(rp)}` at `{rp}`.\n\n"
        f"Repository files (truncated):\n" + "\n".join(tree[:200]) + "\n\n"
        + ("Relevant file contents:\n" + "\n\n".join(ctx_blocks) + "\n\n" if ctx_blocks else "")
        + f"TASK: {instruction}\n\n"
        "Produce a concrete implementation plan. Respond with ONLY a JSON object, no prose, "
        "no markdown fences, in exactly this shape:\n"
        "{\n"
        '  "summary": "one-paragraph description of the change",\n'
        '  "steps": ["short step 1", "short step 2"],\n'
        '  "files": [\n'
        '    {"path": "relative/path.ext", "action": "create|modify", '
        '"rationale": "why", "new_content": "FULL new file contents"}\n'
        "  ]\n"
        "}\n"
        "Rules: paths are RELATIVE to the repo root and must stay inside it. "
        "`new_content` must be the COMPLETE file, not a diff or fragment. "
        "Keep changes minimal and focused on the task."
    )
    try:
        system = _get_friday_system_prompt(keywords=instruction, workspace='code')
    except Exception:
        system = None
    try:
        raw = _generate_text([{"role": "user", "content": user_prompt}], system=system, max_tokens=16384, workspace='code')
    except Exception as e:
        _code_log(f"plan failed: {e}", source="vibe", level="error")
        return jsonify({"status": "error", "message": str(e)}), 500

    # Extract the JSON object from the response (tolerate stray fences/prose).
    plan_obj = None
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r'^```[a-zA-Z]*\n', '', txt)
        txt = re.sub(r'\n```\s*$', '', txt)
    try:
        plan_obj = json.loads(txt)
    except Exception:
        m = re.search(r'\{.*\}', txt, re.DOTALL)
        if m:
            try:
                plan_obj = json.loads(m.group(0))
            except Exception:
                plan_obj = None
    if not isinstance(plan_obj, dict) or "files" not in plan_obj:
        _code_log("plan: model returned unparseable JSON", source="vibe", level="error")
        return jsonify({"status": "error", "message": "Could not parse a plan from the model.",
                        "raw": raw[:2000]}), 502

    # Compute a unified diff per file (current vs proposed).
    files_out = []
    for f in plan_obj.get("files", []):
        if not isinstance(f, dict):
            continue
        rel = (f.get("path") or "").strip().replace("\\", "/")
        safe = _safe_project_path(os.path.join(rp, rel)) if rel else None
        if not safe:
            continue
        new_content = f.get("new_content") or ""
        old_content = ""
        exists = os.path.isfile(safe)
        if exists:
            try:
                with open(safe, 'r', encoding='utf-8', errors='replace') as fh:
                    old_content = fh.read()
            except Exception:
                old_content = ""
        diff = "".join(_difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}",
        ))
        files_out.append({
            "path": rel,
            "action": "modify" if exists else "create",
            "rationale": f.get("rationale", ""),
            "new_content": new_content,
            "diff": diff or ("(new file)\n" + new_content[:4000]),
        })

    plan_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:6]
    record = {
        "id": plan_id,
        "created": datetime.now().isoformat(),
        "repo": os.path.basename(rp),
        "repo_path": rp,
        "instruction": instruction,
        "summary": plan_obj.get("summary", ""),
        "steps": plan_obj.get("steps", []),
        "files": files_out,
        "applied": False,
    }
    try:
        (CODE_PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception as e:
        _code_log(f"plan save failed: {e}", source="vibe", level="error")
    _code_log(f"plan ready: {len(files_out)} file change(s)", source="vibe", level="info")
    return jsonify({"status": "ok", "plan": record})


@code_bp.route('/api/code/plans')
def code_plans_list():
    plans = []
    try:
        for p in sorted(CODE_PLANS_DIR.glob("*.json"), reverse=True)[:50]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                plans.append({"id": d.get("id"), "created": d.get("created"),
                              "repo": d.get("repo"), "instruction": d.get("instruction", "")[:200],
                              "summary": d.get("summary", "")[:300],
                              "file_count": len(d.get("files", [])), "applied": d.get("applied", False)})
            except Exception:
                continue
    except Exception:
        pass
    return jsonify({"status": "ok", "plans": plans})


@code_bp.route('/api/code/plan/<plan_id>')
def code_plan_get(plan_id):
    pid = re.sub(r'[^\w\-]', '', plan_id)
    p = CODE_PLANS_DIR / f"{pid}.json"
    if not p.exists():
        return jsonify({"status": "error", "message": "plan not found"}), 404
    try:
        return jsonify({"status": "ok", "plan": json.loads(p.read_text(encoding="utf-8"))})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@code_bp.route('/api/code/apply', methods=['POST'])
def code_apply():
    """Write the file changes from a saved plan onto disk (inside ~/Projects/)."""
    data = request.get_json(silent=True) or {}
    pid = re.sub(r'[^\w\-]', '', data.get('plan_id', ''))
    p = CODE_PLANS_DIR / f"{pid}.json"
    if not p.exists():
        return jsonify({"status": "error", "message": "plan not found"}), 404
    try:
        record = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    rp = _repo_path(record.get("repo_path") or record.get("repo") or "")
    if not rp:
        return jsonify({"status": "error", "message": "repo no longer found"}), 404

    applied, failed = [], []
    for f in record.get("files", []):
        rel = (f.get("path") or "").replace("\\", "/")
        safe = _safe_project_path(os.path.join(rp, rel))
        if not safe:
            failed.append({"path": rel, "error": "escapes sandbox"})
            continue
        try:
            os.makedirs(os.path.dirname(safe), exist_ok=True)
            with open(safe, 'w', encoding='utf-8', newline='') as fh:
                fh.write(f.get("new_content") or "")
            applied.append(rel)
            _code_log(f"applied {f.get('action','write')}: {rel}", source="vibe", level="info")
        except Exception as e:
            failed.append({"path": rel, "error": str(e)})
            _code_log(f"apply failed {rel}: {e}", source="vibe", level="error")

    record["applied"] = True
    record["applied_at"] = datetime.now().isoformat()
    try:
        p.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception:
        pass
    return jsonify({"status": "ok", "applied": applied, "failed": failed, "count": len(applied)})


# ── PROCESS MONITOR ────────────────────────────────────────────
@code_bp.route('/api/code/processes')
def code_processes():
    """List Friday-spawned background processes (vibe terminals + tracked jobs)."""
    procs = []
    for t in VIBE_TERMINALS.values():
        alive = t.get("status") == "running"
        procs.append({
            "id": t.get("id"), "kind": "terminal", "label": (t.get("task") or "")[:80],
            "status": t.get("status"), "pid": t.get("pid"), "cwd": t.get("cwd"),
            "started": t.get("started"), "killable": bool(alive and t.get("pid")),
        })
    for pid, m in list(CODE_PROCESSES.items()):
        procs.append({**m, "id": pid, "killable": True})
    running = sum(1 for p in procs if p.get("status") == "running")
    return jsonify({"status": "ok", "processes": procs, "running": running, "total": len(procs)})


@code_bp.route('/api/code/kill', methods=['POST'])
def code_kill():
    """Kill a tracked background process by its id."""
    data = request.get_json(silent=True) or {}
    pid_or_id = str(data.get('id', '')).strip()
    if not pid_or_id:
        return jsonify({"status": "error", "message": "id required"}), 400
    # Only kill processes Friday tracks — never arbitrary system PIDs.
    target = VIBE_TERMINALS.get(pid_or_id) or CODE_PROCESSES.get(pid_or_id)
    if not target:
        return jsonify({"status": "error", "message": "unknown process id"}), 404
    os_pid = target.get("pid")
    if not os_pid:
        return jsonify({"status": "error", "message": "no OS pid for this process"}), 400
    try:
        subprocess.run(["taskkill", "/PID", str(os_pid), "/T", "/F"],
                       capture_output=True, creationflags=_POPEN_FLAGS)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    if pid_or_id in VIBE_TERMINALS:
        VIBE_TERMINALS[pid_or_id]["status"] = "stopped"
        VIBE_TERMINALS[pid_or_id]["stopped"] = datetime.now().isoformat()
    CODE_PROCESSES.pop(pid_or_id, None)
    _code_log(f"killed process {pid_or_id} (pid {os_pid})", source="monitor", level="warn")
    return jsonify({"status": "ok", "killed": pid_or_id})
