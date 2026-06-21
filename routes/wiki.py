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
    WIKI_DIR,
    get_anthropic_client,
)  # noqa: E501
from services.model_router import (
    _generate_text,
)  # noqa: E501
from services.wiki_engine import (
    _apply_wiki_proposal,
    _delete_wiki_file,
    _load_pending_wiki,
    _mirror_wiki_file,
    _propose_wiki_update,
    _safe_wiki_path,
    _save_pending_wiki,
    wiki_read_text,
    wiki_write_text,
)  # noqa: E501

wiki_bp = Blueprint('wiki', __name__)



@wiki_bp.route('/api/wiki/<section>/<filename>')
def wiki_page(section, filename):
    """Read a wiki markdown file."""
    if not filename.endswith('.md') and not filename.endswith('.txt'): filename += '.md'
    safe_path = WIKI_DIR / section / filename
    if safe_path.exists() and safe_path.suffix in ('.md', '.txt'):
        return jsonify({"status": "ok", "content": wiki_read_text(safe_path),
                        "section": section, "filename": filename})
    return jsonify({"status": "not_found"}), 404


@wiki_bp.route('/api/wiki/structure')
def wiki_structure():
    """Return full wiki directory structure, with modified times and recent list."""
    structure = {}
    all_files = []
    if WIKI_DIR.exists():
        for section_dir in sorted(WIKI_DIR.iterdir()):
            if section_dir.is_dir() and not section_dir.name.startswith('.'):
                files = []
                for f in sorted(section_dir.iterdir()):
                    if f.suffix in ('.md', '.txt'):
                        try:
                            mtime = f.stat().st_mtime
                            size = f.stat().st_size
                        except Exception:
                            mtime, size = 0, 0
                        entry = {
                            "name": f.stem,
                            "filename": f.name,
                            "size": size,
                            "modified": mtime,
                            "modified_iso": datetime.fromtimestamp(mtime).isoformat() if mtime else None,
                        }
                        files.append(entry)
                        all_files.append({**entry, "section": section_dir.name, "path": f"{section_dir.name}/{f.name}"})
                if files:
                    structure[section_dir.name] = files
    all_files.sort(key=lambda x: x.get("modified") or 0, reverse=True)
    recent = all_files[:5]
    pending_count = len([p for p in _load_pending_wiki() if p.get("status") == "pending"])
    return jsonify({"status": "ok", "structure": structure, "recent": recent, "pending_count": pending_count})


@wiki_bp.route('/api/wiki/update', methods=['POST'])
def wiki_update():
    """Agent or user proposes a wiki update. If auto=true, stored as pending; else applied immediately."""
    data = request.get_json(force=True, silent=True) or {}
    file = data.get("file", "")
    section = data.get("section", "")
    old_value = data.get("old_value", "")
    new_value = data.get("new_value", "")
    reason = data.get("reason", "")
    auto = bool(data.get("auto"))
    if not file or new_value is None:
        return jsonify({"status": "error", "message": "file and new_value required"}), 400
    if _safe_wiki_path(file) is None:
        return jsonify({"status": "error", "message": "invalid wiki path"}), 400
    if auto:
        pid = _propose_wiki_update(file, section, new_value, reason, old_value)
        return jsonify({"status": "ok", "queued": True, "id": pid})
    ok, msg = _apply_wiki_proposal({
        "file": file, "section": section, "old_value": old_value, "new_value": new_value,
    })
    if not ok:
        return jsonify({"status": "error", "message": msg}), 400
    return jsonify({"status": "ok", "applied": True})


@wiki_bp.route('/api/wiki/pending', methods=['GET'])
def wiki_pending():
    items = [p for p in _load_pending_wiki() if p.get("status") == "pending"]
    return jsonify({"status": "ok", "pending": items})


@wiki_bp.route('/api/wiki/pending/<pid>/approve', methods=['POST'])
def wiki_pending_approve(pid):
    items = _load_pending_wiki()
    target = None
    for it in items:
        if it.get("id") == pid:
            target = it
            break
    if target is None:
        return jsonify({"status": "not_found"}), 404
    ok, msg = _apply_wiki_proposal(target)
    if not ok:
        return jsonify({"status": "error", "message": msg}), 400
    target["status"] = "approved"
    target["resolved"] = datetime.utcnow().isoformat() + "Z"
    _save_pending_wiki(items)
    return jsonify({"status": "ok", "approved": pid})


@wiki_bp.route('/api/wiki/pending/<pid>/reject', methods=['POST'])
def wiki_pending_reject(pid):
    items = _load_pending_wiki()
    found = False
    for it in items:
        if it.get("id") == pid:
            it["status"] = "rejected"
            it["resolved"] = datetime.utcnow().isoformat() + "Z"
            found = True
            break
    if not found:
        return jsonify({"status": "not_found"}), 404
    _save_pending_wiki(items)
    return jsonify({"status": "ok", "rejected": pid})


@wiki_bp.route('/api/wiki/edit', methods=['PUT'])
def wiki_edit():
    """Direct inline edit from the UI: full file content replacement."""
    data = request.get_json(force=True, silent=True) or {}
    file = data.get("file", "")
    content = data.get("content")
    if not file or content is None:
        return jsonify({"status": "error", "message": "file and content required"}), 400
    path = _safe_wiki_path(file)
    if path is None:
        return jsonify({"status": "error", "message": "invalid wiki path"}), 400
    _mirror_wiki_file(file, content)
    return jsonify({"status": "ok", "saved": file, "bytes": len(content)})


@wiki_bp.route('/api/wiki/file', methods=['DELETE'])
def wiki_delete():
    """Delete a wiki file. Requires confirm == 'DELETE'."""
    data = request.get_json(force=True, silent=True) or {}
    file = data.get("file", "")
    confirm = data.get("confirm", "")
    if confirm != "DELETE":
        return jsonify({"status": "error", "message": "confirmation token required"}), 400
    path = _safe_wiki_path(file)
    if path is None:
        return jsonify({"status": "error", "message": "invalid wiki path"}), 400
    deleted = _delete_wiki_file(file)
    return jsonify({"status": "ok" if deleted else "not_found", "deleted": deleted, "file": file})


@wiki_bp.route('/api/wiki/search', methods=['POST'])
def wiki_search():
    """Full-text search across wiki files. Returns matching files + line snippets."""
    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    results = []
    if not query:
        return jsonify({"status": "ok", "query": "", "results": []})
    q_lower = query.lower()
    if WIKI_DIR.exists():
        for f in WIKI_DIR.rglob('*'):
            if not f.is_file() or f.suffix not in ('.md', '.txt'):
                continue
            try:
                text = wiki_read_text(f)
            except Exception:
                continue
            if q_lower not in text.lower():
                continue
            snippets = []
            for i, line in enumerate(text.splitlines(), start=1):
                if q_lower in line.lower():
                    snippets.append({"line": i, "text": line.strip()[:220]})
                    if len(snippets) >= 3:
                        break
            try:
                rel = str(f.relative_to(WIKI_DIR)).replace('\\', '/')
            except Exception:
                rel = f.name
            results.append({"path": rel, "matches": len(snippets), "snippets": snippets})
            if len(results) >= 50:
                break
    return jsonify({"status": "ok", "query": query, "results": results})


@wiki_bp.route('/api/wiki/correct', methods=['POST'])
def wiki_correct():
    """Replace old_text with new_text across every wiki file and ~/.friday JSONs."""
    data = request.get_json(force=True, silent=True) or {}
    old_text = data.get("old_text") or ""
    new_text = data.get("new_text") or ""
    if not old_text:
        return jsonify({"status": "error", "message": "old_text required"}), 400
    modified = []
    if WIKI_DIR.exists():
        for f in WIKI_DIR.rglob('*'):
            if not f.is_file() or f.suffix not in ('.md', '.txt'):
                continue
            try:
                text = wiki_read_text(f)
            except Exception:
                continue
            if old_text in text:
                new_content = text.replace(old_text, new_text)
                try:
                    rel = str(f.relative_to(WIKI_DIR)).replace('\\', '/')
                    _mirror_wiki_file(rel, new_content)
                    modified.append({"scope": "wiki", "path": rel})
                except Exception as e:
                    print(f"  [WIKI] Correct failed for {f}: {e}")
    if FRIDAY_DIR.exists():
        for f in FRIDAY_DIR.glob('*.json'):
            try:
                text = wiki_read_text(f)
            except Exception:
                continue
            if old_text in text:
                try:
                    wiki_write_text(f, text.replace(old_text, new_text))
                    modified.append({"scope": "friday", "path": f.name})
                except Exception as e:
                    print(f"  [WIKI] Correct failed for {f}: {e}")
    return jsonify({"status": "ok", "modified": modified, "count": len(modified)})


@wiki_bp.route('/api/wiki/setup-research', methods=['POST'])
def wiki_setup_research():
    """Build draft wiki files for a new user. Stores all as PENDING (auto=true).

    If Anthropic is available, drafts the content via Claude; otherwise creates
    minimal template files from profile fields.
    """
    data = request.get_json(force=True, silent=True) or {}
    full_name = (data.get("full_name") or "").strip()
    birthdate = (data.get("birthdate") or "").strip()
    location = (data.get("location") or "").strip()

    drafts = []
    client = get_anthropic_client()
    base_context = (
        f"Name: {full_name or '[unknown]'}\n"
        f"Birthdate: {birthdate or '[unknown]'}\n"
        f"Location: {location or '[unknown]'}\n"
    )
    targets = [
        ("identity/core-profile.md", "Core profile",
         "A factual, third-person profile: full name, date of birth, current location, "
         "short bio (3-5 sentences), and a 'Known facts' bullet list."),
        ("identity/career-timeline.md", "Career timeline",
         "A reverse-chronological career timeline. Each entry has bold company + role "
         "and a one-line date range. If unknown, leave a [needs research] placeholder."),
        ("identity/education.md", "Education",
         "Schools attended, degrees, dates, and notable accomplishments. Mark unknowns "
         "as [needs research]."),
    ]
    for rel, section, instr in targets:
        try:
            if client and full_name:
                prompt = (
                    f"Draft the following wiki file for the user described below. "
                    f"Markdown. Concise. Mark anything you don't actually know as "
                    f"`[needs research]` — do NOT invent facts.\n\n"
                    f"User:\n{base_context}\n\n"
                    f"Section: {section}\nInstructions: {instr}"
                )
                content = _generate_text(
                    messages=[{"role": "user", "content": prompt}],
                    system="You build draft personal-wiki entries. Be honest about gaps; never fabricate biographical details.",
                    max_tokens=16384,
                    temperature=0.2,
                )
            else:
                title = rel.split('/')[-1].replace('.md', '').replace('-', ' ').title()
                content = (
                    f"# {title}\n\n"
                    f"- **Name:** {full_name or '[needs research]'}\n"
                    f"- **Birthdate:** {birthdate or '[needs research]'}\n"
                    f"- **Location:** {location or '[needs research]'}\n\n"
                    f"_This file was auto-created from profile setup. Fill in details as you learn them._\n"
                )
        except Exception as e:
            content = f"# Draft\n\n[Draft generation failed: {e}]\n\n{base_context}"
        pid = _propose_wiki_update(
            file=rel, section=section, new_value=content,
            reason=f"New-user setup research for {full_name or 'unknown user'}",
            old_value="",
        )
        drafts.append({"id": pid, "file": rel, "section": section, "preview": content[:400]})

    return jsonify({"status": "ok", "drafts": drafts, "count": len(drafts),
                    "message": "Drafts created as pending. Approve each in the Wiki workspace."})
