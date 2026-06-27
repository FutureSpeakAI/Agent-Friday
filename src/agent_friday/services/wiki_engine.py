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
    FRIDAY_DIR,
    WIKI_DIR,
    _log_context,
)  # noqa: E501



# ═══════════════════════════════════════════════════════════════
#  WIKI
# ═══════════════════════════════════════════════════════════════

# ── Wiki helpers ──────────────────────────────────────────────
WIKI_PENDING_FILE = FRIDAY_DIR / "wiki-pending.json"
WIKI_MIRROR_DIR = Path(r"G:\My Drive\Wiki")


# ── Opt-in per-section encryption at rest ─────────────────────
# The wiki is the user's hand-editable knowledge base, so encryption is OFF by
# default. Setting `wiki_encrypted_sections` (e.g. ["health", "legal",
# "family"]) in settings.json encrypts those sections with the same
# AES-256-GCM vault key as ~/.friday/finance|health (requires
# FRIDAY_PASSWORD). All wiki reads route through wiki_read_text(), which
# transparently handles both states — so flipping the setting never breaks
# reading, and the startup vault migration encrypts existing files in place.

def _wiki_encrypted_sections() -> set:
    from agent_friday.core import _load_settings
    try:
        raw = (_load_settings() or {}).get("wiki_encrypted_sections") or []
        return {str(s).strip().lower().strip("/") for s in raw if str(s).strip()}
    except Exception:
        return set()


def _wiki_encrypted_section_dirs() -> list:
    """Absolute dirs for the opted-in sections (for the startup migration)."""
    return [WIKI_DIR / s for s in sorted(_wiki_encrypted_sections())]


def _wiki_path_is_sensitive(path) -> bool:
    try:
        rel = Path(path).resolve().relative_to(Path(WIKI_DIR).resolve())
        return bool(rel.parts) and rel.parts[0].lower() in _wiki_encrypted_sections()
    except Exception:
        return False


def wiki_read_text(path) -> str:
    """Read a wiki file, transparently decrypting vault-encrypted content.

    Plaintext passes through unchanged (errors='replace', matching the old
    direct reads). An encrypted blob with no available key degrades to a
    bracketed notice instead of raising, so search and the UI stay readable.
    """
    raw = Path(path).read_bytes()
    try:
        import vault_crypto as _vc
        if _vc.is_encrypted(raw):
            from agent_friday.services.agent import _get_vault_key  # upper layer — lazy
            key = _get_vault_key()
            if key is None:
                return "[vault-encrypted file — set FRIDAY_PASSWORD to read it]"
            return _vc.decrypt(raw, key).decode("utf-8")
    except Exception as e:
        if "decrypt" in str(type(e)).lower() or "InvalidTag" in str(type(e)):
            raise
    return raw.decode("utf-8", errors="replace")


def wiki_write_text(path, text: str) -> None:
    """Write a wiki file, encrypting when its section is opted in and a vault
    key is available (silent plaintext fallback otherwise — the vault layer
    already warns once at startup). Atomic."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if _wiki_path_is_sensitive(p):
        try:
            import vault_crypto as _vc
            from agent_friday.services.agent import _get_vault_key  # upper layer — lazy
            key = _get_vault_key()
            if key is not None:
                data = _vc.encrypt(data, key)
        except Exception:
            pass
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)


def _safe_wiki_path(rel):
    """Resolve a wiki-relative path inside WIKI_DIR. Returns Path or None."""
    if not rel or not isinstance(rel, str):
        return None
    rel = rel.replace('\\', '/').lstrip('/')
    try:
        p = (WIKI_DIR / rel).resolve()
        wiki_root = WIKI_DIR.resolve()
        try:
            p.relative_to(wiki_root)
        except ValueError:
            return None
        if p.suffix not in ('.md', '.txt', ''):
            return None
        if not p.suffix:
            p = p.with_suffix('.md')
        return p
    except Exception:
        return None


def _mirror_wiki_file(rel, content):
    """Write content to WIKI_DIR/rel and mirror to Google Drive if mounted."""
    rel = rel.replace('\\', '/').lstrip('/')
    primary = WIKI_DIR / rel
    primary.parent.mkdir(parents=True, exist_ok=True)
    old_content = wiki_read_text(primary) if primary.exists() else ""
    wiki_write_text(primary, content)
    try:
        if WIKI_MIRROR_DIR.exists():
            mirror = WIKI_MIRROR_DIR / rel
            mirror.parent.mkdir(parents=True, exist_ok=True)
            # Mirror the on-disk BYTES, not the plaintext — an encrypted
            # section must reach the cloud-synced Drive as ciphertext.
            mirror.write_bytes(primary.read_bytes())
    except Exception as e:
        print(f"  [WIKI] Mirror failed for {rel}: {e}")
    _log_context("wiki_edit", {
        "file": rel,
        "old_len": len(old_content),
        "new_len": len(content),
        "old_preview": old_content[:400],
        "new_preview": content[:400],
    })


def _delete_wiki_file(rel):
    """Delete primary + mirror if present."""
    rel = rel.replace('\\', '/').lstrip('/')
    primary = WIKI_DIR / rel
    deleted = False
    if primary.exists() and primary.is_file():
        primary.unlink()
        deleted = True
    try:
        if WIKI_MIRROR_DIR.exists():
            mirror = WIKI_MIRROR_DIR / rel
            if mirror.exists() and mirror.is_file():
                mirror.unlink()
    except Exception as e:
        print(f"  [WIKI] Mirror delete failed for {rel}: {e}")
    if deleted:
        _log_context("wiki_delete", {"file": rel})
    return deleted


def _load_pending_wiki():
    if not WIKI_PENDING_FILE.exists():
        return []
    try:
        data = json.loads(WIKI_PENDING_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_pending_wiki(items):
    WIKI_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    WIKI_PENDING_FILE.write_text(json.dumps(items, indent=2, default=str), encoding='utf-8')


def _propose_wiki_update(file, section, new_value, reason, old_value=""):
    """Stash a proposed update for user approval. Returns the new id."""
    items = _load_pending_wiki()
    item = {
        "id": uuid.uuid4().hex[:12],
        "file": (file or "").replace('\\', '/').lstrip('/'),
        "section": section or "",
        "old_value": old_value or "",
        "new_value": new_value or "",
        "reason": reason or "",
        "created": datetime.utcnow().isoformat() + "Z",
        "status": "pending",
    }
    items.append(item)
    _save_pending_wiki(items)
    return item["id"]


def _apply_wiki_proposal(item):
    """Apply a pending proposal to the actual file.

    Logic:
      - If old_value is present and found in current file: in-place replace.
      - Else: append a section like "\n## {section}\n{new_value}\n" (or just the value).
      - If the file does not exist yet, create it with a minimal header.
    """
    rel = item.get("file") or ""
    path = _safe_wiki_path(rel)
    if path is None:
        return False, "Invalid wiki path."
    existing = wiki_read_text(path) if path.exists() else ""
    old_val = item.get("old_value") or ""
    new_val = item.get("new_value") or ""
    section = item.get("section") or ""
    if old_val and old_val in existing:
        updated = existing.replace(old_val, new_val)
    elif existing.strip():
        header = f"\n\n## {section}\n" if section else "\n\n"
        updated = existing.rstrip() + header + new_val + "\n"
    else:
        title = path.stem.replace('-', ' ').title()
        header = f"# {title}\n\n"
        if section:
            header += f"## {section}\n"
        updated = header + new_val + "\n"
    _mirror_wiki_file(rel, updated)
    return True, "Applied."


