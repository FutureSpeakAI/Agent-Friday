"""The local embedding model (all-MiniLM-L6-v2): is it on disk, and fetching it.

Friday's embedders (the sensitivity classifier's semantic layer, conversation
memory, the context pruner, the tool selector) all use this one model through
sentence-transformers, which downloads it from Hugging Face the first time it
is constructed if it is not cached. That download must never happen silently
at startup:

  * At boot, the model is loaded only when it is already cached (`is_cached`).
  * The first feature that needs it calls `ensure_available(feature)` before
    constructing it. If it is missing, that call records a "downloading"
    status, tells the owner through a notification and the log what is being
    fetched and from where, downloads it, and records the outcome.
  * The Windows installer's memory tier fetches it ahead of time
    (services/prewarm.py), so on an installed PC none of this runs.

Import-light: no sentence-transformers or torch import here.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

MODEL = "all-MiniLM-L6-v2"
REPO = "sentence-transformers/all-MiniLM-L6-v2"
APPROX_MB = 90

_log = logging.getLogger("friday.embedder")
_LOCK = threading.Lock()
STATUS: dict = {"state": "unknown", "detail": "", "feature": "", "at": None}


def _cache_dir():
    d = os.environ.get("SENTENCE_TRANSFORMERS_HOME")
    return d or None


def is_cached() -> bool:
    """True when the model's files are in the local Hugging Face cache.
    Never touches the network."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except Exception:
        return False
    for fname in ("modules.json", "config.json"):
        try:
            hit = try_to_load_from_cache(REPO, fname, cache_dir=_cache_dir())
        except Exception:
            hit = None
        if not isinstance(hit, str) or not Path(hit).is_file():
            return False
    return True


def _set(state: str, detail: str = "", feature: str = "") -> None:
    STATUS.update(state=state, detail=detail, feature=feature or STATUS.get("feature", ""),
                  at=time.time())


def _notify(title: str, body: str, kind: str = "info") -> None:
    try:
        from agent_friday.services.notifications import _notif_engine
        if _notif_engine:
            _notif_engine.push(title=title, body=body, priority="low",
                               source="memory-model", kind=kind,
                               dedupe_key="embedder-download:" + STATUS.get("state", ""))
    except Exception:
        pass


def ensure_available(feature: str = "") -> bool:
    """Make sure the model is on disk before a feature constructs it.

    Returns True when it is cached (already, or after this download). A
    download is announced before it starts and its result afterwards.
    """
    if is_cached():
        if STATUS["state"] in ("unknown", "not_downloaded"):
            _set("cached")
        return True
    with _LOCK:
        if is_cached():
            _set("cached")
            return True
        what = feature or "a feature that needs it"
        msg = (f"Downloading the local memory model {MODEL} (~{APPROX_MB} MB) "
               f"from huggingface.co, needed by {what}. It is stored on this PC "
               f"and not downloaded again.")
        _set("downloading", msg, feature)
        _log.warning(msg)
        print("  [memory-model] " + msg)
        _notify("Downloading the memory model", msg)
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(REPO, cache_dir=_cache_dir())
        except Exception as e:
            detail = f"download failed ({type(e).__name__}: {e})"
            _set("failed", detail, feature)
            _log.warning("memory model %s", detail)
            _notify("The memory model could not be downloaded",
                    f"{MODEL}: {detail}. Features that use it run without it.",
                    kind="warning")
            return False
        ok = is_cached()
        _set("cached" if ok else "failed",
             "downloaded" if ok else "download finished but the files are not in the cache",
             feature)
        if ok:
            _notify("Memory model ready", f"{MODEL} is downloaded and stored on this PC.")
        return ok


def boot_status() -> str:
    """What startup may do: load it from disk, or leave it for first use."""
    if is_cached():
        _set("cached")
        return "cached"
    _set("not_downloaded",
         f"{MODEL} is not on this PC yet; it is downloaded, with a notice, the "
         f"first time a feature needs it.")
    return "not_downloaded"
