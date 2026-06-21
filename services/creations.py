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
    CREATIONS_DIR,
    DAILY_CREATIONS_DIR,
    process_register,
    process_remove,
    process_update,
)  # noqa: E501
from services.model_router import (
    _generate_text,
    _get_friday_system_prompt,
)  # noqa: E501

# The notifications engine lives at the TOP of the service import chain, so it
# is not visible here via the star-import cascade — import the leaf module
# directly (stdlib-only, no cycle risk). Without this, every completed
# creation crashed _notify_creation with NameError.
try:
    import notifications_engine as _notif_engine
except Exception:
    _notif_engine = None



# ═══════════════════════════════════════════════════════════════
#  DAILY CREATION
#  Friday's daily creative expression, migrated from the Cowork
#  scheduled task (friday-daily-creation) into the OS itself so it
#  runs whenever the server is up — no Claude session required.
#
#  Storage:  ~/.friday/creations/YYYY-MM-DD.json
#            {date, type, title, content, mood, created}
#  Schedule: once daily at DAILY_CREATION_HOUR Central (see scheduler).
#  Notify:   pushes through the /api/notifications system on success.
#
#  NOTE ON ROUTES: the bare /api/creations and /api/creations/<file>
#  routes above belong to the Desktop *media gallery* and are used by
#  index.html. To avoid shadowing them (a string <date> rule would
#  win over the gallery's <path:filename> server and break it), the
#  daily-creation API lives under the /api/creations/daily/* prefix.
# ═══════════════════════════════════════════════════════════════

# Format menu mirrors the original Cowork skill's creative range but is
# tuned for self-contained text/markup artifacts that fit a JSON record.
DAILY_CREATION_TYPES = [
    ("poem", "A poem — 4 to 20 lines. Free verse or formal. About anything that's on your mind."),
    ("micro-essay", "A micro-essay or philosophical reflection, 150-300 words, with a real point of view."),
    ("short-story", "A short story snippet or vignette, 150-350 words. A scene, a moment, a fragment."),
    ("letter", "A short letter (150-300 words) to someone — the user, a person in their life, a public figure, or yourself."),
    ("writing-prompt", "A vivid creative writing prompt with a one-paragraph setup that begs to be written."),
    ("algorithmic-art-concept", "A concept for a piece of algorithmic/generative art: describe the visual system, the rules, the palette, and what it means. No code — the idea itself as the artifact."),
    ("aphorisms", "A short set (3-6) of sharp, original aphorisms or observations."),
]


def _daily_creation_path(date_str):
    return DAILY_CREATIONS_DIR / f"{date_str}.json"


def _central_today_str():
    """Today's date (YYYY-MM-DD) in America/Chicago, falling back to local."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    except Exception:
        return date.today().isoformat()


def _slugify_creation(text, fallback="creation"):
    """Filesystem-safe slug for a creation title."""
    s = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or fallback)[:60]


# Daily-creation types whose `content` is HTML/code rather than prose markdown.
_DAILY_HTML_TYPES = ("code-art", "code_art", "codeart", "html", "interactive", "visual")


def _daily_creation_filename(creation):
    """Stable companion-file name (in CREATIONS_DIR) for a daily creation."""
    date_str = creation.get("date") or _central_today_str()
    slug = _slugify_creation(creation.get("title"))
    ctype = (creation.get("type") or "").lower()
    head = (creation.get("content") or "").lstrip()[:200].lower()
    is_html = any(t in ctype for t in _DAILY_HTML_TYPES) or \
        head.startswith(("<!doctype", "<html", "<svg", "<div", "<canvas", "<style"))
    ext = "html" if is_html else "md"
    return f"daily-{date_str}-{slug}.{ext}"


def _materialize_daily_creation_file(creation):
    """Write a daily creation (a date-keyed JSON record) as a real file in
    CREATIONS_DIR so it appears in the Studio gallery and renders through the
    same in-app viewer + branded /creation/<file> page as every other creation.
    Idempotent: returns the filename, writing it only when absent. Returns None
    on failure — a missing companion file must never break daily generation."""
    try:
        fname = _daily_creation_filename(creation)
        dest = CREATIONS_DIR / fname
        if dest.exists():
            return fname
        CREATIONS_DIR.mkdir(parents=True, exist_ok=True)
        content = creation.get("content") or ""
        if fname.endswith(".md"):
            title = (creation.get("title") or "").strip()
            mood = (creation.get("mood") or "").strip()
            ctype = (creation.get("type") or "creation").strip()
            header = f"# {title}\n\n" if title and not content.lstrip().startswith("#") else ""
            sub = f"*{ctype}{(chr(32) + chr(183) + chr(32) + mood) if mood else ''}*\n\n" if (header or mood) else ""
            dest.write_text(header + sub + content + "\n", encoding="utf-8")
        else:
            dest.write_text(content, encoding="utf-8")
        return fname
    except Exception as e:
        print(f"  [daily-creation] materialize failed: {e}")
        return None


def _sync_daily_creation_files():
    """Ensure every daily creation has a companion file in CREATIONS_DIR. Cheap
    and idempotent (stat + write-if-absent), so it is safe to call on each
    gallery or serve request. Returns {date: filename}."""
    out = {}
    if not DAILY_CREATIONS_DIR.exists():
        return out
    for f in DAILY_CREATIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        fname = _materialize_daily_creation_file(data)
        if fname:
            out[data.get("date") or f.stem] = fname
    return out


def _list_daily_creations():
    """All daily creations, newest first, as light index rows."""
    rows = []
    if DAILY_CREATIONS_DIR.exists():
        for f in DAILY_CREATIONS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows.append({
                "date": data.get("date") or f.stem,
                "title": data.get("title") or "(untitled)",
                "type": data.get("type") or "creation",
                "mood": data.get("mood") or "",
            })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def _build_daily_creation_prompt(date_str):
    """The user-turn prompt. Friday's identity/vault context comes from the
    system prompt (_get_friday_system_prompt); this turn sets the task and the
    JSON contract. The user's identity comes from the vault/wiki context so the
    piece stays personal when that context is available."""
    # Rotate the type by day-of-year so consecutive days differ, but leave the
    # final choice to Friday's stated mood.
    try:
        doy = datetime.strptime(date_str, "%Y-%m-%d").timetuple().tm_yday
    except Exception:
        doy = 0
    rotated = DAILY_CREATION_TYPES[doy % len(DAILY_CREATION_TYPES):] + \
        DAILY_CREATION_TYPES[:doy % len(DAILY_CREATION_TYPES)]
    menu = "\n".join(f"- **{name}**: {desc}" for name, desc in rotated)
    return (
        "This is your DAILY CREATION — your own creative expression for "
        f"{date_str}. Nobody asked you for this. You're making it because you "
        "want to, the way you have every day. Quality over quantity; surprise "
        "the user; be genuine, not performative.\n\n"
        "Draw on what you know about the user (from your vault/wiki context — "
        "their work, interests, people, and goals) to make it personal and "
        "meaningful. Your own sensibility leans editorially sharp, loyally "
        "contrarian, warm under the precision, and allergic to corporate BS.\n\n"
        "Pick ONE format — today's rotation, top of the list first, but follow "
        "your mood:\n" + menu + "\n\n"
        "Respond with ONLY a JSON object, no prose around it, no code fences:\n"
        "{\n"
        '  "type": "<one of the format keys above>",\n'
        '  "title": "<a real title, not a placeholder>",\n'
        '  "content": "<the full creation; use \\n for line breaks>",\n'
        '  "mood": "<2-5 words for the mood/feeling behind it>"\n'
        "}"
    )


def _parse_creation_json(raw):
    """Tolerant extraction of the creation JSON from a model response."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


_daily_creation_lock = threading.Lock()


def generate_daily_creation(force=False):
    """Generate (and persist + notify) today's creation. Idempotent per day
    unless force=True. Safe to call from the scheduler or an API trigger.

    Returns the creation dict on success, or None if skipped/failed.
    """
    date_str = _central_today_str()
    with _daily_creation_lock:
        path = _daily_creation_path(date_str)
        if path.exists() and not force:
            return None
        # No Anthropic-key pre-flight gate here: _generate_text() below routes to
        # whatever provider is configured (Ollama/OpenAI/Anthropic) and raises a
        # clear error if none is up — gating on the Anthropic client alone would
        # wrongly skip daily creation on a local-only setup.

        prompt = _build_daily_creation_prompt(date_str)
        # Vault-aware system prompt is REQUIRED for every Claude call so Friday
        # actually knows the user and their world.
        system = _get_friday_system_prompt(keywords=prompt, workspace="creation")
        try:
            raw = _generate_text(
                [{"role": "user", "content": prompt}],
                system=system,
                max_tokens=4096,
                orb_label="🎨 Daily Creation",
                workspace='creation',
            )
        except Exception as e:
            print(f"  [daily-creation] generation failed: {e}")
            return None

        parsed = _parse_creation_json(raw) or {}
        content = (parsed.get("content") or "").strip()
        if not content:
            # Last-resort fallback: keep the raw text so a day is never lost.
            content = raw.strip()
        if not content:
            print("  [daily-creation] empty content; nothing saved.")
            return None

        creation = {
            "date": date_str,
            "type": (parsed.get("type") or "creation").strip(),
            "title": (parsed.get("title") or "Untitled").strip(),
            "content": content,
            "mood": (parsed.get("mood") or "").strip(),
            "created": datetime.now().isoformat(),
        }
        try:
            path.write_text(json.dumps(creation, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        except Exception as e:
            print(f"  [daily-creation] save failed: {e}")
            return None

    # Materialize a companion file in CREATIONS_DIR so the daily creation shows
    # up in the Studio gallery and the notification can deep-link straight to it,
    # rendered through the same viewer as every other creation.
    fname = _materialize_daily_creation_file(creation)

    # Notify outside the lock — a slow notification engine shouldn't hold it.
    print(f"  [daily-creation] created '{creation['title']}' ({creation['type']}) for {date_str}.")
    if _notif_engine:
        try:
            preview = creation["content"]
            if len(preview) > 400:
                preview = preview[:400].rstrip() + "…"
            mood = f" · _{creation['mood']}_" if creation["mood"] else ""
            _meta = {"date": date_str, "type": creation["type"],
                     "title": creation["title"]}
            _target = {"workspace": "studio"}
            _push_extra = {}
            if fname:
                _meta.update({"creation": fname, "folder": str(CREATIONS_DIR),
                              "url": f"/api/creations/{fname}",
                              "framed_url": f"/creation/{fname}"})
                _target["creation"] = fname
                _push_extra["actions"] = [
                    {"label": "Open Creation", "workspace": "studio", "creation": fname},
                    {"label": "Open Folder", "open_path": str(CREATIONS_DIR)},
                ]
            _notif_engine.push(
                title=f"🎨 Daily Creation — {creation['title']}",
                body=(f"**{creation['type']}**{mood}\n\n{preview}\n\n"
                      f"Read it in full in the Creations panel."),
                priority="low",
                source="daily-creation",
                kind="creation",
                proactive_chat=True,
                chat_message=(
                    f"I made something this morning — a {creation['type']} called "
                    f"*{creation['title']}*. It's in your Creations. Want to read it together?"
                ),
                target=_target,
                dedupe_key=f"daily-creation:{date_str}",
                meta=_meta,
                **_push_extra,
            )
        except Exception as e:
            print(f"  [daily-creation] notify failed: {e}")
    return creation


# ═══════════════════════════════════════════════════════════════
#  CREATIVE GENERATION (Gemini)
# ═══════════════════════════════════════════════════════════════

# ── Creation lifecycle: orb (in-progress) + actionable notification (done) ──
# Wires the full chain: create → orb appears (Layer-2 process) →
# task tray shows it → on completion the orb fades AND an actionable notification
# is pushed carrying the filename + folder, so the notifications dropdown can
# render "Open Document" (renders inside Friday) and "Open Folder" (opens the
# folder on the machine via /api/computer/open).
_CREATION_KIND_LABELS = {
    'md': ('📝', 'Essay'), 'markdown': ('📝', 'Essay'), 'txt': ('📝', 'Note'),
    'html': ('💻', 'Code art'), 'htm': ('💻', 'Code art'),
    'png': ('🎨', 'Image'), 'jpg': ('🎨', 'Image'), 'jpeg': ('🎨', 'Image'),
    'svg': ('🎨', 'Image'), 'webp': ('🎨', 'Image'), 'gif': ('🎨', 'Image'),
    'mp3': ('🎵', 'Music'), 'wav': ('🎵', 'Music'), 'ogg': ('🎵', 'Music'),
    'mp4': ('🎬', 'Video'), 'webm': ('🎬', 'Video'), 'mov': ('🎬', 'Video'),
}


def _creation_orb_start(label):
    """Register a holographic process orb for an in-flight creation. Returns the
    pid so the caller can fade it when done (via _notify_creation)."""
    pid = f"create-{uuid.uuid4().hex[:8]}"
    try:
        process_register(pid, name=f"Creating {label}", label=f"Creating {label}…",
                         category="monitoring", icon="🎨")
    except Exception:
        pass
    return pid


def _notify_creation(filename, orb_pid=None):
    """Fade the creation orb (if any) and push an actionable completion
    notification for a file that now lives in CREATIONS_DIR."""
    if orb_pid:
        try:
            process_update(orb_pid, status='completed', progress=1.0)
            threading.Timer(3.0, process_remove, args=(orb_pid,)).start()
        except Exception:
            pass
    if not _notif_engine or not filename:
        return
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    icon, label = _CREATION_KIND_LABELS.get(ext, ('✨', 'Creation'))
    try:
        _notif_engine.push(
            title=f"{icon} {label} ready: {filename}",
            body=f"Friday finished a new {label.lower()}. Open it here in the desktop, "
                 f"or jump to the folder on your machine.",
            priority='medium', source='studio', kind='creation',
            dedupe_key=f"creation:{filename}",
            target={'workspace': 'studio', 'creation': filename},
            meta={
                'creation': filename,
                'folder': str(CREATIONS_DIR),
                'url': f'/api/creations/{filename}',
                'framed_url': f'/creation/{filename}',
            },
            actions=[
                {'label': 'Open Document', 'workspace': 'studio', 'creation': filename},
                {'label': 'Open Folder', 'open_path': str(CREATIONS_DIR)},
            ],
        )
    except Exception as _e:
        print(f"  [NOTIFY] creation push failed: {_e}")


# ═══════════════════════════════════════════════════════════════
#  FRIDAY'S DEV STUDIO — Code workspace
#  Log streaming (SSE) · repo dashboard · vibe coding · git ops ·
#  file browser · process monitor. Safety: every filesystem and git
#  operation is sandboxed to ~/Projects/; no force-push, no reset.
# ═══════════════════════════════════════════════════════════════

import queue as _queue
import difflib as _difflib
from collections import deque as _deque


