"""What Undo needs to put a calendar or Google Tasks change back, encrypted at rest (F2;
the release lead's call on #377, 2026-10-23).

An Undo row for a changed or deleted event or task needs the item's own words: an event's
title, place and description, a task's notes. Those are not written into the action ledger
(signed, but plaintext in its live segment). The receipt keeps only a random token and the
names of the fields; the values are sealed here exactly as A20 seals the full text of a send
(services/sent_text_store): with the vault key when a vault passphrase is set (and never more
weakly then), otherwise with Friday's key store. Never written readable: if nothing can seal
them, nothing is kept and the receipt says that change cannot be undone.

Off the record they stay in memory only, like the receipt itself. A kept entry is deleted when
its Undo has run, when the owner forgets a person it names (services/forget_registry), and after
RETENTION_S. A file planted in the folder is never opened as Undo data: only a known sealed
envelope is.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.undo_store")

#: How long Undo data is kept; past it the change can no longer be undone (the receipt stays).
RETENTION_S = 30 * 24 * 3600
MAX_ENTRY_BYTES = 256 * 1024
_TOKEN = re.compile(r"u[0-9a-f]{32}")
_LOCK = threading.Lock()
_MEMORY: Dict[str, Dict[str, Any]] = {}      # off the record: never on disk
_last_prune = 0.0


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "vault" / "undo"


def _path(token: str) -> Optional[Path]:
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return None
    from agent_friday.paths import contained
    try:
        return contained(_dir(), token + ".bin")
    except ValueError:
        return None


def _sts():
    from agent_friday.services import sent_text_store
    return sent_text_store


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return True                       # can't tell: nothing on disk (memory only)


def keep(fields: Dict[str, Any]) -> Optional[str]:
    """Seal `fields` and return its token, or None when it cannot be kept sealed (then the change
    has no Undo). Never raises."""
    try:
        raw = json.dumps({"ts": time.time(), "fields": fields}, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_ENTRY_BYTES:
            return None
        token = "u" + secrets.token_hex(16)
        if _off_record():
            with _LOCK:
                _MEMORY[token] = {"ts": time.time(), "fields": dict(fields)}
            return token
        blob = _sts()._seal(raw)
        if blob is None:
            return None                   # never kept readable
        p = _path(token)
        if p is None:
            return None
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            try:
                with open(tmp, "wb") as fh:
                    fh.write(blob)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, p)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()      # a failed write leaves nothing behind
                    except OSError:
                        pass
        _prune_sometimes()
        return token
    except Exception as e:  # noqa: BLE001
        _log.warning("undo data could not be kept: %s", type(e).__name__)
        return None


def read(token: str) -> Optional[Dict[str, Any]]:
    """The fields kept under `token`, or None (gone, expired, or it cannot be opened)."""
    _prune_sometimes()
    with _LOCK:
        mem = _MEMORY.get(token)
    if mem is not None:
        return dict(mem["fields"])
    p = _path(token)
    if p is None or not p.is_file():
        return None
    try:
        raw = _sts()._open(p.read_bytes())
        data = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        return None
    if not isinstance(data, dict) or time.time() - float(data.get("ts") or 0) > RETENTION_S:
        return None
    f = data.get("fields")
    return dict(f) if isinstance(f, dict) else None


def drop(token: str) -> None:
    """Delete one entry (its Undo has run)."""
    with _LOCK:
        _MEMORY.pop(token, None)
    p = _path(token)
    if p is not None:
        try:
            p.unlink()
        except OSError:
            pass


def prune() -> int:
    n, d = 0, _dir()
    if not d.is_dir():
        return 0
    cutoff = time.time() - RETENTION_S
    with _LOCK:
        for f in d.glob("u*.bin*"):        # a stale .tmp goes too
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    n += 1
            except OSError:
                continue
    return n


def _prune_sometimes() -> None:
    global _last_prune
    if time.time() - _last_prune > 3600:
        _last_prune = time.time()
        prune()


def _mentions(fields: Dict[str, Any], names, emails) -> bool:
    text = " ".join(str(v) for v in (fields or {}).values()).lower()
    return any(n and n in text for n in names) or any(e and e in text for e in emails)


def forget_find(names, emails) -> int:
    prune()
    return sum(1 for _ in _matching(names, emails))


def forget_purge(names, emails) -> int:
    """Forget means forget: every entry that names the person goes, and so does every entry that
    cannot be opened to check (the vault locked, a key gone): nothing that might name them is
    left. Those changes can then no longer be undone."""
    prune()
    n = 0
    for token in list(_matching(names, emails)):
        drop(token)
        n += 1
    return n


def _matching(names, emails):
    with _LOCK:
        mem = [(t, e["fields"]) for t, e in _MEMORY.items()]
    for t, f in mem:
        if _mentions(f, names, emails):
            yield t
    try:
        if _sts()._passphrase_set():
            _sts()._vault_key()           # forget may open the vault (a read never derives it)
    except Exception:
        pass
    d = _dir()
    for p in (d.glob("u*.bin") if d.is_dir() else []):
        token = p.stem
        if not _TOKEN.fullmatch(token):
            continue                      # not one of ours (and drop could not remove it)
        f = read(token)
        if f is None or _mentions(f, names, emails):
            yield token                   # unreadable counts as possibly naming them


def status() -> Dict[str, Any]:
    """For the privacy map: how many entries are kept, their size, and what seals new ones."""
    d = _dir()
    files = list(d.glob("u*.bin")) if d.is_dir() else []
    return {"kept": len(files), "bytes": sum(f.stat().st_size for f in files if f.is_file()),
            "sealed_with": _sts().sealed_with()}
