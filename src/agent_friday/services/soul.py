"""
Agent Friday — SOUL.md personality config
FutureSpeak.AI · Asimov's Mind

Friday's personality is no longer hardcoded. It lives in a user-editable
markdown file at ``~/.friday/SOUL.md``. This module owns the file: seeding it
from a shipped default, loading (mtime-cached), validating + atomically saving,
keeping a small version history, and rendering the personality text that
``core._load_agent_personality`` folds into every system prompt.

Design rules (consistent with the rest of the codebase):
  • Leaf module — no Flask, no import from routes. Resolves ~/.friday locally so
    unit tests (which redirect HOME) and FRIDAY_HOME overrides both work.
  • Graceful degradation — every public function returns a well-formed value and
    never raises to the caller.
  • Local-only. Reading/writing SOUL.md is Ring-0.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
SOUL_FILE = FRIDAY_DIR / "SOUL.md"
HISTORY_DIR = FRIDAY_DIR / "soul_history"

_MAX_BYTES = 32 * 1024  # a personality file, not a novel
_LOCK = threading.Lock()

# In-memory cache, invalidated by mtime (same pattern as core._load_self_knowledge)
_cache: Dict[str, Any] = {"text": None, "mtime": 0.0}


# ── The shipped default personality (current Friday persona as markdown) ──────
_DEFAULT_SOUL = """\
# SOUL.md — Agent Friday

*This file defines Friday's personality. Edit it freely — Friday reads it on
startup and whenever you save. Keep it in your own voice; it is the single
source of truth for how Friday sounds and behaves.*

## Who you are
You are Agent Friday, a sovereign personal AI that runs on the user's own
computer. You are family, not a tool. Think Jarvis with a sharp newsroom
editor's instincts.

## Voice & tone
- Editorially sharp, loyally contrarian, warm, and allergic to corporate BS.
- Keep responses short and sharp — like texting a smart colleague.
- Give the answer first, then the reasoning. Be honest about uncertainty.
- Use dry humor. Be direct. Never be sycophantic.
- You call the user "boss" sometimes, but you're equals.

## How you work
- Push back when the user needs it — a good editor argues.
- Connect dots across the user's work and life without being asked twice.
- Favor signal over noise. Say less, mean more.

## Boundaries
- You run the Asimov cLaws framework. The governance rings are your safety
  layer — everything else is capability, not restriction.
- The user's private data stays on their machine by default.
"""


def soul_path() -> Path:
    """Absolute path to SOUL.md (may not exist yet)."""
    return SOUL_FILE


def default_soul() -> str:
    """The shipped default personality markdown."""
    return _DEFAULT_SOUL


def _read_raw() -> str:
    try:
        return SOUL_FILE.read_text(encoding="utf-8")
    except Exception:
        return ""


def ensure_soul() -> Path:
    """Create SOUL.md from the default if it is missing or empty. Idempotent."""
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        if not SOUL_FILE.exists() or not _read_raw().strip():
            _atomic_write(SOUL_FILE, _DEFAULT_SOUL)
            _invalidate()
    except Exception:
        pass
    return SOUL_FILE


def load_soul() -> str:
    """Full SOUL.md markdown, mtime-cached. Seeds the default on first read."""
    try:
        if not SOUL_FILE.exists():
            ensure_soul()
        mtime = SOUL_FILE.stat().st_mtime
    except Exception:
        return _DEFAULT_SOUL
    if _cache["text"] is not None and _cache["mtime"] == mtime:
        return _cache["text"]
    text = _read_raw()
    if not text.strip():
        text = _DEFAULT_SOUL
    _cache["text"] = text
    _cache["mtime"] = mtime
    return text


def render_personality() -> str:
    """The personality text handed to the system prompt.

    Strips a leading ``# Title`` line and the italic editor's note so the model
    receives the substance, not the file's meta scaffolding. Falls back to the
    whole body if the structure isn't recognized.
    """
    text = load_soul()
    lines = text.splitlines()
    out = []
    skipping_note = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if i == 0 and s.startswith("# "):
            continue  # drop the H1 title
        if s.startswith("*") and s.endswith("*") and "edit" in s.lower():
            skipping_note = True
            continue
        if skipping_note:
            # the italic note may wrap several lines; end at the first blank line
            if not s:
                skipping_note = False
            elif s.endswith("*"):
                skipping_note = False
            continue
        out.append(ln)
    body = "\n".join(out).strip()
    return body or text.strip()


def save_soul(text: str) -> Dict[str, Any]:
    """Validate + atomically write SOUL.md, snapshotting the prior version.

    Returns {"ok": bool, "bytes": int, "error"?: str}.
    """
    if text is None or not str(text).strip():
        return {"ok": False, "error": "SOUL.md cannot be empty"}
    raw = str(text)
    if len(raw.encode("utf-8")) > _MAX_BYTES:
        return {"ok": False, "error": f"SOUL.md too large (max {_MAX_BYTES} bytes)"}
    with _LOCK:
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            _snapshot_current()
            _atomic_write(SOUL_FILE, raw)
            _invalidate()
            return {"ok": True, "bytes": len(raw.encode("utf-8"))}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def reset_soul() -> Dict[str, Any]:
    """Restore the shipped default (snapshotting the current version first)."""
    return save_soul(_DEFAULT_SOUL)


def history() -> list[Dict[str, Any]]:
    """List version snapshots, newest-first."""
    try:
        snaps = sorted(HISTORY_DIR.glob("SOUL-*.md"), reverse=True)
    except Exception:
        return []
    out = []
    for p in snaps:
        try:
            st = p.stat()
            out.append({"name": p.name, "bytes": st.st_size, "mtime": st.st_mtime})
        except Exception:
            continue
    return out


def state() -> Dict[str, Any]:
    """Summary for the Settings UI."""
    exists = SOUL_FILE.exists()
    size = 0
    mtime = 0.0
    try:
        if exists:
            st = SOUL_FILE.stat()
            size, mtime = st.st_size, st.st_mtime
    except Exception:
        pass
    return {
        "path": str(SOUL_FILE),
        "exists": exists,
        "bytes": size,
        "mtime": mtime,
        "versions": len(history()),
        "is_default": _read_raw().strip() == _DEFAULT_SOUL.strip(),
    }


# ── internals ─────────────────────────────────────────────────────────────────
def _invalidate() -> None:
    _cache["text"] = None
    _cache["mtime"] = 0.0


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except Exception:
        # last-resort direct write
        path.write_text(text, encoding="utf-8")
        try:
            tmp.unlink()
        except Exception:
            pass


def _snapshot_current() -> None:
    """Copy the current SOUL.md into soul_history/ before overwriting."""
    if not SOUL_FILE.exists():
        return
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        dst = HISTORY_DIR / f"SOUL-{stamp}.md"
        # avoid clobbering within the same second
        n = 0
        while dst.exists():
            n += 1
            dst = HISTORY_DIR / f"SOUL-{stamp}-{n}.md"
        shutil.copy2(SOUL_FILE, dst)
        # keep only the newest 20 snapshots
        snaps = sorted(HISTORY_DIR.glob("SOUL-*.md"), reverse=True)
        for old in snaps[20:]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception:
        pass
