"""The exact text of what was sent to the cloud, kept only if the owner turns
it on (north star §29.6, burn-down BD-15 slice P2).

By default the egress ledger keeps a FINGERPRINT of each send (a SHA-256 of
exactly what left), never the words: §29.6 says the ledger "does not store
the full sensitive payload by default". The owner can choose to keep the
exact text as well, so a receipt can show precisely what left. Then:

* it is written encrypted through Friday's key store
  (credential_store.protect), never readable on disk; if encryption is not
  available nothing is kept;
* nothing is kept while off the record;
* it is kept for `privacy_keep_sent_text_days` (default 30, 1 to 365) and
  pruned after that;
* turning the choice off deletes everything already kept (`set_keep`).

Each entry is stored under its fingerprint, so the egress row and the text
are tied by the hash of the exact bytes that left.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.sent_text")

SETTING = "privacy_keep_sent_text"
SETTING_DAYS = "privacy_keep_sent_text_days"
DEFAULT_DAYS = 30
MAX_DAYS = 365
#: The most one kept send may hold; a larger send keeps its fingerprint only.
MAX_KEEP_BYTES = 4 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
_LOCK = threading.Lock()
_last_prune = 0.0

EXPLANATION = (
    "By default Friday keeps only a fingerprint of each send to the cloud: a short "
    "code that proves exactly what was sent without keeping the words. If you turn "
    "this on, Friday also keeps the exact text of each send for the number of days "
    "you choose, encrypted on this PC, so a receipt can show you precisely what left. "
    "Nothing is kept while you're off the record. Turning it off deletes the text "
    "already kept.")


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "vault" / "sent-text"


def _settings() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def enabled() -> bool:
    return _settings().get(SETTING) is True


def days() -> int:
    try:
        d = int(_settings().get(SETTING_DAYS) or DEFAULT_DAYS)
    except (TypeError, ValueError):
        d = DEFAULT_DAYS
    return max(1, min(MAX_DAYS, d))


def _off_record() -> bool:
    try:
        from agent_friday.services import off_record
        return bool(off_record.active())
    except Exception:
        return True                       # can't tell: keep nothing


def _path(sha: str) -> Optional[Path]:
    if not isinstance(sha, str) or not _SHA.match(sha):
        return None
    return _dir() / (sha + ".bin")


def keep(sha: str, text: str, *, provider: str = "", model: str = "",
         purpose: str = "") -> bool:
    """Keep the exact text of one send under its fingerprint. Returns True
    only when it was written encrypted. Never raises."""
    try:
        if not enabled() or _off_record():
            return False
        p = _path(sha)
        if p is None:
            return False
        raw = json.dumps({"ts": time.time(), "provider": provider, "model": model,
                          "purpose": purpose, "text": text}, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_KEEP_BYTES:
            return False
        from agent_friday.services import credential_store as cs
        blob, method = cs.protect(raw)
        if method == "plaintext" or blob == raw:
            return False                  # never keep it readable
        with _LOCK:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            with open(tmp, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        _prune_sometimes()
        return True
    except Exception as e:
        _log.warning("sent text could not be kept: %s", type(e).__name__)
        return False


def read(sha: str) -> Optional[Dict[str, Any]]:
    p = _path(sha)
    if p is None or not p.is_file():
        return None
    try:
        from agent_friday.services import credential_store as cs
        data = json.loads(cs.unprotect(p.read_bytes()).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if time.time() - float(data.get("ts") or 0) > days() * 86400:
        return None                       # past its time; pruned on the next write
    return data


def has(sha: str) -> bool:
    p = _path(sha)
    return bool(p and p.is_file())


def prune(now: Optional[float] = None) -> int:
    now = now or time.time()
    cutoff = now - days() * 86400
    n = 0
    d = _dir()
    if not d.is_dir():
        return 0
    with _LOCK:
        for f in d.glob("*.bin"):
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


def purge_all() -> int:
    n = 0
    d = _dir()
    if not d.is_dir():
        return 0
    with _LOCK:
        for f in d.glob("*.bin*"):
            try:
                f.unlink()
                n += 1
            except OSError:
                continue
    return n


def set_keep(keep_text: bool, keep_days: Optional[int] = None) -> Dict[str, Any]:
    """The owner's choice. Off deletes everything already kept."""
    from agent_friday.core import _save_settings
    delta: Dict[str, Any] = {SETTING: bool(keep_text)}
    if keep_days is not None:
        delta[SETTING_DAYS] = max(1, min(MAX_DAYS, int(keep_days)))
    _save_settings(delta)
    deleted = 0 if keep_text else purge_all()
    return dict(status(), deleted=deleted)


def status() -> Dict[str, Any]:
    d = _dir()
    files = list(d.glob("*.bin")) if d.is_dir() else []
    return {"keep": enabled(), "days": days(), "kept": len(files),
            "bytes": sum(f.stat().st_size for f in files if f.is_file()),
            "explanation": EXPLANATION}
