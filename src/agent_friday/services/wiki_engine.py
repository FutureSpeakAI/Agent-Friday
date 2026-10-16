import os
import io
import json
import glob
import logging
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
from agent_friday.paths import contained



# ═══════════════════════════════════════════════════════════════
#  WIKI
# ═══════════════════════════════════════════════════════════════

# ── Wiki helpers ──────────────────────────────────────────────
WIKI_PENDING_FILE = FRIDAY_DIR / "wiki-pending.json"

#: One lock around every wiki page write (``_mirror_wiki_file``), every
#: read-modify-write of a page (an edit, a correction, a proposal apply) and
#: every read-modify-write of the pending store, so two writers never
#: interleave and two clicks on Approve write once. Re-entrant: a holder that
#: calls ``_mirror_wiki_file`` takes it again on the same thread. Nothing done
#: under it waits on another thread: the write's side effects are the context
#: log append and ``mark_wiki_dirty`` (a flag under its own short lock, and the
#: graph listener's ``put_nowait`` into subscriber queues).
_PENDING_LOCK = threading.RLock()


def wiki_path_hidden(path, root=None) -> bool:
    """True when ``path`` has a segment starting with a dot below ``root``
    (default WIKI_DIR): ``.git``, ``.obsidian``, ``.trash``, ``notes/.draft.md``.
    Such files are not pages -- ``_safe_wiki_path`` refuses them -- so no
    listing, search or correction reaches them either."""
    try:
        parts = Path(path).relative_to(Path(root if root is not None else WIKI_DIR)).parts
    except ValueError:
        parts = Path(path).parts
    return any(part.startswith('.') for part in parts)


# ── Opt-in wiki mirror ────────────────────────────────────────
# `wiki_mirror_dir` in settings.json names a folder that receives a copy of
# every wiki write and delete. It is empty (off) by default and there is no
# built-in location: the owner chooses the folder. If that folder is synced
# by a cloud client (OneDrive, Google Drive, Dropbox), every mirrored page
# leaves the machine through that client. Encrypted sections are copied as
# their on-disk ciphertext, never as plaintext.

def _wiki_mirror_dir():
    """The owner-chosen mirror folder, or None when mirroring is off.

    Returns None unless the setting names an absolute path to an existing
    directory; a mirror never creates its own root.
    """
    from agent_friday.core import _load_settings
    try:
        raw = str((_load_settings() or {}).get("wiki_mirror_dir") or "").strip()
    except Exception:
        return None
    if not raw:
        return None
    p = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute() or not p.is_dir():
        return None
    return p


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


# What an encrypted page reads as while the vault is locked. It is a
# placeholder, never the page: anything that would write it back (an edit
# made from what was on screen) must be refused, or the ciphertext is lost.
VAULT_LOCKED_PLACEHOLDER = "[vault-encrypted file — set FRIDAY_PASSWORD to read it]"


def wiki_read_text(path) -> str:
    """Read a wiki file, transparently decrypting vault-encrypted content.

    Plaintext passes through unchanged (errors='replace', matching the old
    direct reads). An encrypted blob with no available key degrades to a
    bracketed notice instead of raising, so search and the UI stay readable.
    """
    raw = Path(path).read_bytes()
    try:
        import agent_friday.privacy.vault_crypto as _vc
        if _vc.is_encrypted(raw):
            from agent_friday.services.agent import _get_vault_key  # upper layer — lazy
            key = _get_vault_key()
            if key is None:
                return VAULT_LOCKED_PLACEHOLDER
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
            import agent_friday.privacy.vault_crypto as _vc
            from agent_friday.services.agent import _get_vault_key  # upper layer — lazy
            key = _get_vault_key()
            if key is not None:
                data = _vc.encrypt(data, key)
        except Exception:
            pass
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    try:
        from agent_friday.services.knowledge_graph import mark_wiki_dirty
        mark_wiki_dirty(f"write:{p.name}")
    except Exception:
        pass


def _safe_wiki_path(rel):
    """Resolve a wiki-relative path inside WIKI_DIR. Returns Path or None."""
    if not rel or not isinstance(rel, str):
        return None
    rel = rel.replace('\\', '/').lstrip('/')
    # No hidden segment: ``.git``, ``.obsidian``, ``.trash`` and the like are
    # not pages, and a model- or request-supplied name never reaches them.
    if any(seg.startswith('.') for seg in rel.split('/') if seg):
        return None
    try:
        p = contained(WIKI_DIR, rel)
        if p.suffix not in ('.md', '.txt', ''):
            return None
        if not p.suffix:
            p = p.with_suffix('.md')
        # The same check on the RESOLVED path (after the suffix is added, so
        # "notes/DRAFT~1" + ".md" is checked as the file it names): on Windows
        # an 8.3 short name ("TRASH~1" for ".trash", "GIT~1" for ".git") has no
        # dot of its own but resolves to the hidden one; so does a link.
        root = os.path.realpath(os.fspath(WIKI_DIR))
        resolved = os.path.realpath(os.fspath(p))
        if wiki_path_hidden(resolved, root) or wiki_path_hidden(p, root):
            _wlog.info("wiki path %r refused: it resolves to a hidden path (%s)", rel, resolved)
            return None
        return p
    except Exception:
        return None


def _wiki_rel(path) -> str:
    """The forward-slash path of a contained wiki file relative to WIKI_DIR."""
    root = contained(WIKI_DIR, "", allow_root=True)
    return str(Path(path).relative_to(root)).replace('\\', '/')


def _mirror_wiki_file(rel, content):
    """Write content to WIKI_DIR/rel, and to the opt-in mirror when one is set.

    Raises ValueError when ``rel`` does not stay inside WIKI_DIR; the mirror
    copy is contained in the mirror root the same way. Runs under
    ``_PENDING_LOCK`` (re-entrant), so it never interleaves with a proposal
    apply, a correction or another edit of the same wiki.
    """
    with _PENDING_LOCK:
        primary = contained(WIKI_DIR, rel.replace('\\', '/').lstrip('/'))
        rel = _wiki_rel(primary)
        primary.parent.mkdir(parents=True, exist_ok=True)
        old_content = wiki_read_text(primary) if primary.exists() else ""
        wiki_write_text(primary, content)
        try:
            mirror_root = _wiki_mirror_dir()
            if mirror_root is not None:
                mirror = contained(mirror_root, rel)
                mirror.parent.mkdir(parents=True, exist_ok=True)
                # Mirror the on-disk BYTES, not the plaintext — an encrypted
                # section reaches the mirror folder as ciphertext.
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
    """Delete primary, and the opt-in mirror's copy when a mirror is set.

    A ``rel`` that does not stay inside WIKI_DIR deletes nothing.
    """
    try:
        primary = contained(WIKI_DIR, (rel or "").replace('\\', '/').lstrip('/'))
    except ValueError:
        return False
    rel = _wiki_rel(primary)
    deleted = False
    if primary.exists() and primary.is_file():
        primary.unlink()
        deleted = True
    try:
        mirror_root = _wiki_mirror_dir()
        if mirror_root is not None:
            mirror = contained(mirror_root, rel)
            if mirror.exists() and mirror.is_file():
                mirror.unlink()
    except Exception as e:
        print(f"  [WIKI] Mirror delete failed for {rel}: {e}")
    if deleted:
        _log_context("wiki_delete", {"file": rel})
        try:
            from agent_friday.services.knowledge_graph import mark_wiki_dirty
            mark_wiki_dirty(f"delete:{rel}")
        except Exception:
            pass
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
    """Write the pending store atomically: a temp file in the same folder,
    flushed, then ``os.replace``. A write that fails (disk full, a crash
    mid-write) leaves the previous queue whole and removes its temp file."""
    target = WIKI_PENDING_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(items, indent=2, default=str)
    import tempfile as _tempfile
    fd, tmp = _tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(target))
    except BaseException as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        logging.getLogger("friday.wiki").warning(
            "wiki pending store not saved (the previous queue is kept): %s", e)
        raise


def wiki_replace_in_page(path, old_text, new_text):
    """Replace ``old_text`` with ``new_text`` in one wiki page, read and
    written under ``_PENDING_LOCK`` so no other wiki write lands in between.
    Returns the page's wiki-relative path when it changed, else ``None``."""
    with _PENDING_LOCK:
        text = wiki_read_text(path)
        if not old_text or old_text not in text or text == VAULT_LOCKED_PLACEHOLDER:
            return None
        try:
            rel = str(Path(path).relative_to(WIKI_DIR)).replace('\\', '/')
        except ValueError:
            rel = _wiki_rel(path)
        _mirror_wiki_file(rel, text.replace(old_text, new_text))
        return rel


_wlog = logging.getLogger("friday.wiki")

#: How a proposal changes its page. ``edit`` replaces ``old_value`` exactly
#: (once); ``append`` adds ``new_value`` (under ``section`` when given);
#: ``replace_file`` overwrites the whole page. A proposal without a mode is an
#: edit when it carries ``old_value`` and an append otherwise. A replacement is
#: never appended: an edit whose ``old_value`` is gone is ``stale``.
WIKI_PROPOSAL_MODES = ("edit", "append", "replace_file")


def _proposal_mode(item):
    mode = str((item or {}).get("mode") or "").strip().lower()
    if mode in WIKI_PROPOSAL_MODES:
        return mode
    return "edit" if (item or {}).get("old_value") else "append"


def _propose_wiki_update(file, section, new_value, reason, old_value="", mode=""):
    """Stash a proposed update for user approval. Returns the id.

    One change, one decision: an identical proposal already waiting returns
    the id of that one instead of queuing a second.
    """
    file = (file or "").replace('\\', '/').lstrip('/')
    _p = _safe_wiki_path(file)
    if _p is not None:
        file = _wiki_rel(_p)   # the page actually written: "notes/x" is notes/x.md
    mode = str(mode or "").strip().lower()
    mode = mode if mode in WIKI_PROPOSAL_MODES else ""
    with _PENDING_LOCK:
        items = _load_pending_wiki()
        for it in items:
            if (it.get("status") == "pending" and it.get("file") == file
                    and (it.get("section") or "") == (section or "")
                    and (it.get("old_value") or "") == (old_value or "")
                    and (it.get("new_value") or "") == (new_value or "")
                    and (it.get("mode") or "") == mode):
                _wlog.info("wiki proposal %s already waiting for %s; not queued twice",
                           it.get("id"), file)
                return it.get("id")
        item = {
            "id": uuid.uuid4().hex[:12],
            "file": file,
            "section": section or "",
            "old_value": old_value or "",
            "new_value": new_value or "",
            "reason": reason or "",
            "created": datetime.utcnow().isoformat() + "Z",
            "status": "pending",
        }
        if mode:
            item["mode"] = mode
        items.append(item)
        _save_pending_wiki(items)
    return item["id"]


def _record_wiki_decision(file, section, new_value, reason, old_value, mode, result, decided_by):
    """Keep a resolved record of a proposal the owner already decided on a card,
    so Knowledge shows its history without it ever waiting there. Best-effort."""
    try:
        _p = _safe_wiki_path(file)
        with _PENDING_LOCK:
            items = _load_pending_wiki()
            now = datetime.utcnow().isoformat() + "Z"
            items.append({
                "id": uuid.uuid4().hex[:12],
                "file": _wiki_rel(_p) if _p is not None else (file or "").replace('\\', '/').lstrip('/'),
                "section": section or "", "old_value": old_value or "",
                "new_value": new_value or "", "reason": reason or "",
                "mode": _proposal_mode({"mode": mode, "old_value": old_value}),
                "created": now, "resolved": now,
                "status": result.get("status"), "decided_by": str(decided_by or ""),
                "message": result.get("message", ""),
            })
            _save_pending_wiki(items)
            return items[-1]["id"]
    except Exception as e:  # noqa: BLE001 - the write already happened; the record is history
        _wlog.warning("wiki decision record not saved for %s: %s", file, e)
        return ""


def _attach_cloud_note(proposal_id, cloud_note):
    """Internal, for the work-order executor only (not a tool): attach a cloud model's
    labelled, fenced note to a pending proposal, apart from its reason. False if not found."""
    with _PENDING_LOCK:
        items = _load_pending_wiki()
        for it in items:
            if it.get("id") == proposal_id and it.get("status") == "pending":
                it["cloud_note"] = str(cloud_note)[:600]
                _save_pending_wiki(items)
                return True
    return False


def _contains_block(text, block):
    """True when every line of ``block`` appears, in order and consecutively,
    as whole lines of ``text`` (trailing spaces ignored). A one-word value
    is not "already there" just because the word occurs inside a sentence."""
    want = [ln.rstrip() for ln in (block or "").strip("\n").split("\n")]
    if not want or not any(want):
        return False
    have = [ln.rstrip() for ln in (text or "").split("\n")]
    n = len(want)
    return any(have[i:i + n] == want for i in range(len(have) - n + 1))


#: An ATX heading line: up to 3 spaces, 1-6 ``#``, a space, the text, and an
#: optional closing run of ``#`` (only after a space, so "C#" keeps its ``#``).
_HEADING_LINE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]([^\n]*))?$")


def _strip_closing_hashes(text):
    """"Notes ##" -> "Notes"; "C#" keeps its ``#`` (a closing run needs a space
    before it). String operations, not a backtracking pattern."""
    s = str(text or "").rstrip()
    j = len(s.rstrip("#"))
    if j < len(s) and (j == 0 or s[j - 1] in " \t"):
        s = s[:j].rstrip()
    return s


class _Heading:
    __slots__ = ("_g",)

    def __init__(self, hashes, text):
        self._g = (None, hashes, text)

    def group(self, i):
        return self._g[i]


class _HeadingMatcher:
    """``_HEADING_RE.match(line)``: ``group(1)`` the ``#`` run, ``group(2)`` the
    text without a closing ``#`` run, or None for a line that is no heading."""

    @staticmethod
    def match(line):
        m = _HEADING_LINE.match(line or "")
        if not m:
            return None
        text = m.group(2)
        return _Heading(m.group(1), None if text is None else _strip_closing_hashes(text))


_HEADING_RE = _HeadingMatcher()
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _heading_key(text):
    """How a section name is compared with a heading: any leading ``#`` run,
    surrounding and repeated whitespace, a closing ``#`` run and one trailing
    colon are ignored, and case does not matter. "## Preferences ",
    "preferences" and "### PREFERENCES:" name the same section."""
    t = str(text or "").strip()
    t = re.sub(r"^#{1,6}(?=\s|$)", "", t).strip()
    t = _strip_closing_hashes(t).strip()
    t = re.sub(r"\s+", " ", t)
    if t.endswith(":"):
        t = t[:-1].rstrip()
    return t.casefold()


def _section_name(section):
    """``(text, level)`` of a requested section: "## Notes" is ("Notes", 2);
    a bare "Notes" has no level of its own (None)."""
    s = str(section or "").strip()
    m = re.match(r"^(#{1,6})(?=\s|$)", s)
    level = len(m.group(1)) if m else None
    text = s[m.end():].strip() if m else s
    text = _strip_closing_hashes(text).strip()
    return text, level


def _page_headings(lines):
    """``[(index, level, text)]`` for the ATX headings of ``lines``, skipping
    fenced code blocks (a ``# comment`` in a shell sample is not a heading)."""
    out, fence = [], None
    for i, ln in enumerate(lines):
        f = _FENCE_RE.match(ln)
        if fence is not None:
            if f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence) \
                    and not ln.strip()[len(f.group(1)):].strip():
                fence = None
            continue
        if f:
            fence = f.group(1)
            continue
        m = _HEADING_RE.match(ln)
        if m and (m.group(2) or "").strip():
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _lift_leading_heading(new_val, key=None):
    """When ``new_val`` opens with a heading (first non-blank line) and the rest
    of it has no heading at that level or above, return ``(heading_text,
    level, body)``; else None. With ``key`` the heading must name that section.
    A model often sends "## Preferences\\n- tea" for the section Preferences;
    the heading is the section, not a second copy of it."""
    lines = (new_val or "").strip("\n").split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return None
    m = _HEADING_RE.match(lines[i])
    if not m or not (m.group(2) or "").strip():
        return None
    level, text = len(m.group(1)), m.group(2).strip()
    if key is not None and _heading_key(text) != key:
        return None
    rest = lines[i + 1:]
    if any(lv <= level for _, lv, _ in _page_headings(rest)):
        return None
    while rest and not rest[0].strip():
        rest = rest[1:]
    body = "\n".join(rest).rstrip()
    return (text, level, body) if body.strip() else None


def _find_section(lines, section):
    """``(index, level)`` of the heading that names ``section`` in ``lines``,
    or None. A heading at the requested level wins ("## Notes" over an
    earlier "### Notes"); otherwise the first match at any level."""
    text, want = _section_name(section)
    key = _heading_key(text)
    if not key:
        return None
    hits = [(i, lv) for i, lv, t in _page_headings(lines) if _heading_key(t) == key]
    if not hits:
        return None
    if want is not None:
        for i, lv in hits:
            if lv == want:
                return i, lv
    return hits[0]


def _append_under_section(existing, section, new_val):
    """Add ``new_val`` at the END of the existing section named ``section``
    (any heading level, with or without its ``#``, case and spacing aside):
    after its last line and its subsections, before the next heading of the
    same or a higher level. Only when the page has no such heading is one
    added, at the end of the page. Never a second copy of the heading."""
    return _section_append(existing, section, new_val)[0]


def _section_append(existing, section, new_val):
    """``(after, matched)``: the page with ``new_val`` appended under
    ``section`` (see ``_append_under_section``) and the existing heading line
    it went under, or None when a new heading was added."""
    lines = existing.rstrip("\n").split("\n")
    text, want = _section_name(section)
    lifted = _lift_leading_heading(new_val, _heading_key(text))
    if lifted is not None:
        new_val = lifted[2]
    found = _find_section(lines, section)
    if found is None:
        heading = "#" * (want or 2) + " " + text
        return existing.rstrip() + f"\n\n{heading}\n" + new_val.rstrip("\n") + "\n", None
    start, level = found
    end = len(lines)
    for j, lv, _t in _page_headings(lines[start + 1:]):
        if lv <= level:
            end = start + 1 + j
            break
    body_end = end
    while body_end > start + 1 and not lines[body_end - 1].strip():
        body_end -= 1
    out = lines[:body_end] + new_val.strip("\n").split("\n")
    if end < len(lines):
        out += [""] + lines[end:]
    return "\n".join(out) + "\n", lines[start].strip()


def _first_h1(text):
    """The text of ``text``'s first non-blank line when that line is an H1."""
    for ln in (text or "").split("\n"):
        if not ln.strip():
            continue
        m = _HEADING_RE.match(ln)
        if m and len(m.group(1)) == 1 and (m.group(2) or "").strip():
            return m.group(2).strip()
        return None
    return None


def _new_page_text(path, rel, section, new_val):
    """The text of a page an append creates. Its title is the page name
    ("notes/tea-list.md" is "# Tea List") unless ``new_val`` already opens
    with an H1: then that H1 is the title and no second one is added. When
    the two differ the content's own H1 wins (it was written on purpose; the
    file name is a slug) and that is logged. A ``section`` is added as a
    heading only when ``new_val`` has no heading of that name already."""
    title = path.stem.replace('-', ' ').replace('_', ' ').title()
    sec_text, sec_level = _section_name(section)
    sec_key = _heading_key(sec_text)
    h1 = _first_h1(new_val)
    if h1 is None:
        lifted = _lift_leading_heading(new_val, sec_key) if sec_key else None
        if lifted is not None:
            new_val = lifted[2]
        header = f"# {title}\n\n"
        if sec_text:
            header += "#" * (sec_level or 2) + f" {sec_text}\n"
        return header + new_val.strip("\n") + "\n"
    if _heading_key(h1) != _heading_key(title):
        _wlog.info("new wiki page %s: the content's own title %r is kept instead of %r "
                   "from the page name", rel, h1, title)
    body_lines = new_val.strip("\n").split("\n")
    heads = _page_headings(body_lines)
    names = {_heading_key(t) for _, _, t in heads}
    # A section heading goes under the H1 only when the content has no other
    # heading: a page with its own structure is written as it was given.
    if sec_key and sec_key not in names and len(heads) == 1:
        i = next(k for k, ln in enumerate(body_lines) if ln.strip())
        rest = body_lines[i + 1:]
        while rest and not rest[0].strip():
            rest = rest[1:]
        body_lines = (body_lines[:i + 1] + [""] + ["#" * (sec_level or 2) + f" {sec_text}"]
                      + rest)
    return "\n".join(body_lines).rstrip("\n") + "\n"


def plan_wiki_proposal(item):
    """What applying ``item`` would do, without writing anything.

    Returns a dict with ``status`` one of:
      ready      -- ``after`` is the page to write
      unchanged  -- the page already says this; nothing to write
      stale      -- an edit whose ``old_value`` is no longer in the page
      ambiguous  -- an edit whose ``old_value`` occurs more than once
      failed   -- cannot be applied (bad path, locked vault page, no old_value)
    plus ``mode``, ``file``, ``exists``, ``before``, ``after`` and ``message``.
    """
    item = item or {}
    rel = (item.get("file") or "").replace('\\', '/').lstrip('/')
    mode = _proposal_mode(item)
    old_val = item.get("old_value") or ""
    new_val = item.get("new_value") or ""
    section = (item.get("section") or "").strip()
    plan = {"mode": mode, "file": rel, "exists": False, "before": "", "after": "",
            "status": "failed", "message": ""}
    path = _safe_wiki_path(rel)
    if path is None:
        plan["message"] = "Invalid wiki path."
        return plan
    # The page that is written, read back and reported is the contained,
    # suffixed one: "notes/x" is notes/x.md, never a bare "x" beside it.
    rel = plan["file"] = _wiki_rel(path)
    plan["exists"] = path.exists()
    existing = wiki_read_text(path) if path.exists() else ""
    plan["before"] = existing
    if existing == VAULT_LOCKED_PLACEHOLDER:
        plan["message"] = (f"{rel} is encrypted and the vault is locked; nothing was written. "
                           "Unlock it (FRIDAY_PASSWORD) and approve again.")
        return plan
    if mode == "edit":
        if not old_val:
            plan["message"] = "An edit needs the exact text it replaces (old_value)."
            return plan
        hits = existing.count(old_val)
        if hits and old_val in new_val and new_val in existing:
            # An edit that extends its own text ("a" -> "a, b") is already done
            # when the page holds the whole result; replacing again would stack it.
            plan.update(status="unchanged", after=existing,
                        message=f"{rel} already has this change; nothing was written.")
            return plan
        if hits > 1:
            plan.update(status="ambiguous", message=(
                f"The text this edit replaces occurs {hits} times in {rel}, so which one "
                "to change is not clear; nothing was written. Propose again with a longer "
                "old_value that occurs once."))
            return plan
        if hits == 1:
            plan["after"] = existing.replace(old_val, new_val, 1)
        elif _contains_block(existing, new_val):
            # Already applied only when the WHOLE new block is there as whole
            # lines -- not because one of its words occurs somewhere.
            plan.update(status="unchanged", after=existing,
                        message=f"{rel} already has this change; nothing was written.")
            return plan
        else:
            plan.update(status="stale", message=(
                f"The text this edit replaces is no longer in {rel} (the page changed "
                "since it was proposed); nothing was written. Read the page and propose again."))
            return plan
    elif mode == "replace_file":
        plan["after"] = new_val if new_val.endswith("\n") else new_val + "\n"
    else:  # append
        if existing.strip() and _contains_block(existing, new_val):
            plan.update(status="unchanged", after=existing,
                        message=f"{rel} already contains this text; nothing was appended.")
            return plan
        if existing.strip():
            if not _heading_key(section):
                # No section named, but the text opens with a heading the page
                # already has ("## Preferences\n- tea"): that is the section.
                lifted = _lift_leading_heading(new_val)
                if lifted is not None and _find_section(
                        existing.rstrip("\n").split("\n"), "#" * lifted[1] + " " + lifted[0]):
                    section = "#" * lifted[1] + " " + lifted[0]
            if _heading_key(section):
                lifted = _lift_leading_heading(new_val, _heading_key(_section_name(section)[0]))
                if lifted is not None and _contains_block(existing, lifted[2]):
                    plan.update(status="unchanged", after=existing,
                                message=f"{rel} already contains this text; nothing was appended.")
                    return plan
                plan["after"], matched = _section_append(existing, section, new_val)
                plan["section_heading"] = matched
                plan["section_new"] = matched is None
            else:
                plan["after"] = existing.rstrip() + "\n\n" + new_val.rstrip("\n") + "\n"
        else:
            plan["after"] = _new_page_text(path, rel, section, new_val)
    if plan["after"] == existing:
        plan.update(status="unchanged", message=f"{rel} already reads this way; nothing was written.")
        return plan
    plan["status"] = "ready"
    return plan


def apply_wiki_proposal_result(item, before_sha256=None, require_preview=False):
    """Apply a proposal and say exactly what happened.

    Returns ``{"ok", "status", "message", "file", "mode", "lines_added",
    "lines_removed"}`` with status ``applied`` (written and read back),
    ``unchanged`` (already so, nothing written), ``stale``, ``ambiguous`` or
    ``failed``. With ``before_sha256`` (from a preview) a page that no longer
    hashes to it is ``stale`` and nothing is written. With
    ``require_preview`` an overwrite or append of a page that exists is also
    ``stale`` when no ``before_sha256`` is given (as on a Knowledge approve).

    Plan and write happen in one hold of ``_PENDING_LOCK``, so the
    owner-decided tool path, ``/api/wiki/update`` and a Knowledge approve
    never interleave on a page.
    """
    with _PENDING_LOCK:
        plan = plan_wiki_proposal(item)
        refused = _hash_refusal(plan, before_sha256, require=bool(require_preview))
        if refused is not None:
            return refused
        return _apply_wiki_proposal_locked(item, plan=plan)


def apply_decided_wiki_proposal(item):
    """The owner-decided tool path (an approved card, a grant, a chat yes):
    plan and apply in ONE hold of ``_PENDING_LOCK``.

    A ``replace_file`` on a page that exists -- when planned, or by the time
    of the write -- is not applied: the result is ``needs_review`` (``ok``
    False, with the plan's line counts) and the caller queues it for
    Knowledge, where the overwrite and its diff are shown. A card shows the
    call, not what the page loses.
    """
    with _PENDING_LOCK:
        plan = plan_wiki_proposal(item)
        path = _safe_wiki_path(plan["file"])
        exists_now = path is not None and path.exists()
        if plan["mode"] == "replace_file" and plan["status"] == "ready" and (
                plan["exists"] or exists_now):
            if exists_now and not plan["exists"]:
                before = wiki_read_text(path)
            else:
                before = plan["before"]
            added, removed = _diff_counts(before, plan["after"])
            _wlog.info("decided replace_file on existing %s not applied; it goes to Knowledge",
                       plan["file"])
            return {"ok": False, "status": "needs_review", "file": plan["file"],
                    "mode": plan["mode"], "lines_added": added, "lines_removed": removed,
                    "message": (f"{plan['file']} exists; an overwrite waits for approval in "
                                "Knowledge, where what the page loses is shown.")}
        return _apply_wiki_proposal_locked(item, plan=plan)


def wiki_before_sha256(text):
    """The hash a preview carries of the page it was computed against
    (``plan["before"]``; the empty string for a page that does not exist).

    Definition (platform-independent): SHA-256 of the page's decoded text as
    UTF-8, with line endings normalised to ``\\n`` (CRLF and lone CR become LF).
    That is the page as the preview shows it -- the diff is line-based -- so a
    page saved with Windows line endings hashes the same as on Linux/macOS, and
    a client can recompute it from the text it displays. A change of line
    endings alone is not a change the owner could have seen, so it is not stale.
    """
    norm = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return _hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _hash_refusal(plan, before_sha256, require):
    """A ``stale`` result when the page is not the one the owner saw, else None.

    ``before_sha256`` given: it must equal the page's hash now (every mode).
    Not given and ``require``: an overwrite or append on a page that exists is
    refused, because nothing shows the owner saw what it lands on; an edit
    keeps working on its own ``old_value`` check, and a new page loses nothing.
    """
    given = str(before_sha256 or "").strip().lower()
    cur = wiki_before_sha256(plan["before"])
    if given and given != cur:
        msg = (f"{plan['file']} changed since this preview was shown; nothing was written. "
               "Reopen the preview to see the page as it is now, then approve again.")
    elif not given and require and plan["mode"] in ("replace_file", "append") and plan["exists"]:
        what = "An overwrite" if plan["mode"] == "replace_file" else "An append"
        msg = (f"{what} of the existing page {plan['file']} is applied only from the preview "
               "the owner saw (before_sha256); none was sent, so nothing was written. "
               "Reopen the preview and approve again.")
    else:
        return None
    _wlog.info("wiki proposal for %s refused as stale (preview hash): %s", plan["file"], msg)
    return {"ok": False, "status": "stale", "message": msg, "file": plan["file"],
            "mode": plan["mode"], "lines_added": 0, "lines_removed": 0,
            "before_sha256": cur, "preview_stale": True}


def _apply_wiki_proposal_locked(item, plan=None):
    """``apply_wiki_proposal_result`` for a caller that holds ``_PENDING_LOCK``.

    ``plan`` is the plan the caller already made under the same lock (it is
    made here otherwise). Immediately before the write the page is read again:
    if it is not exactly what was planned against (created, changed or removed
    by a writer outside this process), nothing is written and the result is
    ``stale``.
    """
    plan = plan if plan is not None else plan_wiki_proposal(item)
    res = {"ok": False, "status": plan["status"], "message": plan["message"],
           "file": plan["file"], "mode": plan["mode"], "lines_added": 0, "lines_removed": 0}
    if plan["status"] == "unchanged":
        res["ok"] = True
        return res
    if plan["status"] != "ready":
        _wlog.info("wiki proposal for %s not applied (%s): %s",
                   plan["file"], plan["status"], plan["message"])
        return res
    added, removed = _diff_counts(plan["before"], plan["after"])
    try:
        path = _safe_wiki_path(plan["file"])
        now_exists = path is not None and path.exists()
        now = wiki_read_text(path) if now_exists else ""
        if now_exists != plan["exists"] or now != plan["before"]:
            _wlog.info("wiki proposal for %s not applied: the page changed between plan and write",
                       plan["file"])
            res.update(status="stale", message=(
                f"{plan['file']} changed while this change was being applied; nothing was "
                "written. Read the page and propose again."))
            return res
        _mirror_wiki_file(plan["file"], plan["after"])
        path = _safe_wiki_path(plan["file"])
        back = wiki_read_text(path) if path is not None and path.exists() else None
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        _wlog.warning("wiki proposal for %s failed to write: %s", plan["file"], e)
        res.update(status="failed", message=f"Writing {plan['file']} failed: {e}")
        return res
    if back != plan["after"]:
        _wlog.warning("wiki proposal for %s: re-read after write did not match", plan["file"])
        res.update(status="failed", message=(
            f"{plan['file']} was written but reading it back did not match; check the page."))
        return res
    res.update(ok=True, status="applied", lines_added=added, lines_removed=removed,
               message=f"Applied to {plan['file']} (+{added} -{removed} lines).")
    return res


def _diff_counts(before, after):
    added = removed = 0
    for ln in _difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(),
                                    lineterm="", n=0):
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("+"):
            added += 1
        elif ln.startswith("-"):
            removed += 1
    return added, removed


def _apply_wiki_proposal(item):
    """Apply a proposal; ``(ok, message)``. See ``apply_wiki_proposal_result``."""
    res = apply_wiki_proposal_result(item)
    return res["ok"], res["message"] or "Applied."


#: Proposal states that are final. ``approved`` is the pre-1.1 word for applied.
_DONE_STATES = {"applied": "applied", "approved": "applied", "rejected": "rejected",
                "denied": "rejected", "stale": "stale"}


def decide_wiki_proposal(pid, decision, before_sha256=None):
    """Approve or reject a proposal once. Returns ``(http_code, payload)``.

    Idempotent: approving an applied proposal answers 200 ``{status: applied,
    already: true}`` and writes nothing; a rejected or stale one answers its
    state with a non-2xx code, never success. A proposal whose write failed
    stays pending so it can be approved again.

    ``before_sha256`` is the hash the preview the owner approved from was
    computed against. When the page no longer hashes to it the answer is 409
    ``stale`` and nothing is written (any mode). Without it, an edit still
    applies on its own ``old_value`` check, but an overwrite or append of a
    page that exists is refused 409 ``stale`` (reopen the preview). Either
    way the proposal stays pending: it is not wrong, it was not seen.
    """
    decision = "approve" if decision == "approve" else "reject"
    with _PENDING_LOCK:
        items = _load_pending_wiki()
        target = next((it for it in items if it.get("id") == pid), None)
        if target is None:
            return 404, {"status": "not_found", "ok": False, "id": pid,
                         "message": "No such proposal."}
        done = _DONE_STATES.get(str(target.get("status") or ""))
        if done:
            same = (decision == "approve" and done == "applied") or (
                decision == "reject" and done == "rejected")
            return (200 if same else 409), {
                "status": done, "ok": same, "already": True, "id": pid,
                "file": target.get("file", ""),
                "message": target.get("message") or f"This proposal is already {done}."}
        now = datetime.utcnow().isoformat() + "Z"
        if decision == "reject":
            target.update(status="rejected", resolved=now)
            _save_pending_wiki(items)
            _wlog.info("wiki proposal %s rejected", pid)
            return 200, {"status": "rejected", "ok": True, "rejected": pid, "id": pid,
                         "file": target.get("file", "")}
        plan = plan_wiki_proposal(target)              # already under _PENDING_LOCK
        refused = _hash_refusal(plan, before_sha256, require=True)
        if refused is not None:
            target["last_error"] = refused["message"]
            _save_pending_wiki(items)
            return 409, dict(refused, id=pid)
        res = _apply_wiki_proposal_locked(target, plan=plan)
        payload = dict(res, id=pid)
        if res["status"] in ("applied", "unchanged"):
            target.update(status="applied", resolved=now, message=res["message"])
            payload.update(status="applied", approved=pid, already=res["status"] == "unchanged")
            code = 200
        elif res["status"] == "stale":
            target.update(status="stale", resolved=now, message=res["message"])
            code = 409
        else:
            target["last_error"] = res["message"]
            code = 422
        _save_pending_wiki(items)
        _wlog.info("wiki proposal %s approve -> %s", pid, payload["status"])
        return code, payload


def preview_wiki_proposal(pid):
    """A plain-English view of one proposal: headline, badge, unified diff and
    line counts, and its state. ``None`` when there is no such id."""
    item = next((it for it in _load_pending_wiki() if it.get("id") == pid), None)
    if item is None:
        return None
    return wiki_proposal_preview(item)


def wiki_proposal_preview(item):
    """The preview of one proposal dict (see ``preview_wiki_proposal``). The
    Knowledge pending list carries this per row, so an overwrite is shown as
    an overwrite -- never as an append -- before anyone approves it."""
    pid = item.get("id") or ""
    state = _DONE_STATES.get(str(item.get("status") or ""), str(item.get("status") or "pending"))
    plan = plan_wiki_proposal(item)
    rel, mode = plan["file"], plan["mode"]
    if not plan["exists"]:
        badge = "new"
    else:
        badge = {"edit": "edit", "append": "append", "replace_file": "overwrite"}[mode]
    after = plan["after"] if plan["status"] in ("ready", "unchanged") else plan["before"]
    diff = list(_difflib.unified_diff(plan["before"].splitlines(), after.splitlines(),
                                      fromfile=rel, tofile=rel, lineterm=""))
    added, removed = _diff_counts(plan["before"], after)
    sec = f" under '{item.get('section')}'" if item.get("section") else ""
    if plan.get("section_heading"):
        sec = f" at the end of its existing section '{plan['section_heading']}'"
    elif plan.get("section_new"):
        sec = f" under a new heading '{_section_name(item.get('section'))[0]}'"
    headline = {
        "new": f"Create the page {rel}",
        "edit": f"Change text in {rel}",
        "append": f"Add to {rel}{sec}",
        "overwrite": f"Replaces the whole page {rel}",
    }[badge]
    if plan["status"] in ("stale", "failed", "unchanged", "ambiguous") and state == "pending":
        headline += f" ({plan['status']}: {plan['message']})"
    return {"status": "ok", "id": pid, "state": state, "would": plan["status"],
            "headline": headline + f" (+{added} -{removed} lines)", "badge": badge,
            "file": rel, "mode": mode, "diff": "\n".join(diff),
            "lines_added": added, "lines_removed": removed, "message": plan["message"],
            # Sent back on approve: the page must still be the one shown here.
            "before_sha256": wiki_before_sha256(plan["before"])}


