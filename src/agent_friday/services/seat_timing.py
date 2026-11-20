"""How long a cold local seat took to come up on THIS PC, as measured by the arbiter's own loads.

A device choosing a local model that is not resident wants to know "about how long until it answers". The
honest answer is the last measured load of that model here, or nothing when it has not loaded since timing
began (never a made-up number). services/residency_arbiter records every llama-server spawn (process start to /health answering; an Ollama
load, and the time spent evicting the seat being replaced, are not in it); services/model_slate reads it. Held by tests/unit/test_seat_timing.py (record, read back, restart, damaged file, nonsense refused,
and a source check that the arbiter records its spawns).
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from typing import Optional

_log = logging.getLogger("friday.seat_timing")
_LOCK = threading.Lock()
_TIMES: dict = {}
_LOADED = False
_MAX_ENTRIES = 64
_MAX_SECONDS = 3600.0


def _path():
    from agent_friday.paths import friday_home
    return friday_home() / "models" / "seat_load_times.json"


def _sane(value, low: float, high: float) -> Optional[float]:
    """``value`` as a finite float in (low, high], else None: a damaged file or a caller never gets a nonsense number."""
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) and low < f <= high else None


def _load_once() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        for mid, rec in raw.items():
            sec = _sane(rec.get("seconds"), 0.0, _MAX_SECONDS) if isinstance(rec, dict) else None
            if sec is not None:                  # one damaged record is dropped; the others are kept
                _TIMES[str(mid)] = {"seconds": sec, "at": _sane(rec.get("at"), 0.0, 1e12) or 0.0}
    except FileNotFoundError:
        pass
    except Exception as e:                       # a saved file of any shape starts empty, never breaks a load
        _log.warning("saved seat load times unreadable, starting empty: %s", type(e).__name__)


def _save() -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_TIMES), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        _log.warning("seat load times not saved: %s", type(e).__name__)


def record(model_id: str, seconds) -> bool:
    """Remember a real cold load. Zero (already seated), negatives, non-numbers and absurd values are not loads.
    Never raises: the arbiter calls this right after a seat came up, and bookkeeping must not fail that load."""
    sec = _sane(seconds, 0.0, _MAX_SECONDS)
    if not model_id or sec is None:
        return False
    try:
        with _LOCK:
            _load_once()
            _TIMES[str(model_id)] = {"seconds": round(sec, 1), "at": time.time()}
            while len(_TIMES) > _MAX_ENTRIES:
                _TIMES.pop(min(_TIMES, key=lambda k: _TIMES[k]["at"]))
            _save()
    except Exception as e:
        _log.warning("seat load time not recorded: %s", type(e).__name__)
        return False
    return True


def last_load_seconds(model_id: str) -> Optional[float]:
    with _LOCK:
        _load_once()
        rec = _TIMES.get(str(model_id))
        return rec["seconds"] if rec else None


def _forget_memory_only() -> None:
    global _LOADED
    with _LOCK:
        _TIMES.clear()
        _LOADED = False
