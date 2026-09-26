"""One lock for importing the ML stack, shared by every subsystem that does it.

transformers 5.x builds its namespace lazily, and during boot several threads
reach for it at once: the privacy classifier's embedder (Layer 3 of the egress
classifier), Kokoro's voice probe, Laya's approval model, the context pruner,
the tool selector and the prewarm. A thread that arrives while another is
mid-import can see a half-built module and fail with "cannot import name
'AlbertModel'" or KeyError 'transformers.utils.import_utils'.

Laya had its own lock and its own cure: evict half-built modules from
sys.modules and retry. But its lock was private, and a module that is
"half-built" by that test (``__spec__._initializing``) is also, exactly, a
module another thread is importing right now. So Laya's retry could pull a
module out from under the privacy classifier mid-import, and the classifier
then cached the failure for the life of the process: egress classification
without semantic matching until the next restart.

The rules now:
- Every ML import runs under ML_IMPORT_LOCK (``guarded``).
- Half-built modules are evicted only while holding that lock, so nothing can
  be mid-import when they are (``purge_partial_imports``).
- A failed guarded import is purged and tried once more at once; callers
  that cache results cache success forever and retry failure after a cooldown.
- ``preload()`` imports the shared stack once, early, on one thread, so the
  first import of the day is not a race at all.
"""
from __future__ import annotations

import importlib
import logging
import sys
import threading
import time

_log = logging.getLogger("friday.ml_imports")

#: Held for the whole of any ML-stack import. Reentrant: an importer may call
#: another guarded helper while holding it.
ML_IMPORT_LOCK = threading.RLock()

#: Module name prefixes that make up the shared ML stack.
ML_ROOTS = ("transformers", "huggingface_hub", "tokenizers",
            "sentence_transformers", "laya", "kokoro", "misaki")

_state = {"preloaded": False, "error": "", "at": 0.0, "seconds": 0.0}


def purge_partial_imports() -> list:
    """Drop half-built ML modules from sys.modules. Returns the names dropped.

    Takes ML_IMPORT_LOCK, so it waits for any guarded import in progress
    instead of evicting the module that import is building.
    """
    with ML_IMPORT_LOCK:
        doomed = []
        for name, mod in list(sys.modules.items()):
            if not name.startswith(ML_ROOTS):
                continue
            spec = getattr(mod, "__spec__", None)
            if mod is None or (spec is not None and getattr(spec, "_initializing", False)):
                doomed.append(name)
        for name in doomed:
            sys.modules.pop(name, None)
        if doomed:
            _log.info("evicted %d half-built ML module(s): %s",
                      len(doomed), ", ".join(sorted(doomed)[:4]))
        return doomed


def guarded(fn, *args, **kwargs):
    """Run an ML import (or a load that imports) under the shared lock.

    On an import-shaped failure the half-built modules are purged and ``fn``
    is tried once more before the exception is allowed out.
    """
    with ML_IMPORT_LOCK:
        try:
            return fn(*args, **kwargs)
        except (ImportError, KeyError, AttributeError) as first:
            dropped = purge_partial_imports()
            if not dropped:
                raise
            _log.info("ML import failed (%s: %s); retrying after purge",
                      type(first).__name__, first)
            return fn(*args, **kwargs)


def import_module(name: str):
    """``importlib.import_module`` under the shared lock, with one purge-retry."""
    return guarded(importlib.import_module, name)


#: Imported by preload(), in order. Optional names are skipped when absent.
_PRELOAD = (
    ("huggingface_hub", None),
    ("transformers", "AutoTokenizer"),
    ("transformers", "AlbertModel"),
    ("sentence_transformers", "SentenceTransformer"),
)


def preload() -> dict:
    """Import the shared ML stack once, on this thread, before anything races it.

    Resolves the lazily-built names the other subsystems use, so their later
    imports find finished modules. Never raises; the outcome is in status().
    """
    import importlib.util
    t0 = time.monotonic()
    err = ""
    for mod, attr in _PRELOAD:
        try:
            if importlib.util.find_spec(mod) is None:
                continue
        except Exception:
            continue
        try:
            m = import_module(mod)
            if attr:
                guarded(getattr, m, attr)
        except Exception as e:  # noqa: BLE001
            err = "%s.%s: %s: %s" % (mod, attr or "", type(e).__name__, str(e)[:160])
            _log.warning("ML preload: %s", err)
            break
    _state.update(preloaded=not err, error=err, at=time.time(),
                  seconds=round(time.monotonic() - t0, 2))
    if not err:
        _log.info("ML stack preloaded in %.1fs", _state["seconds"])
    return status()


def status() -> dict:
    return dict(_state)
