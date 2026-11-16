"""How long a cold local seat took to come up on THIS PC, as measured by the arbiter's own loads.

A device choosing a local model that is not resident wants to know "about how long until it answers". The
honest answer is the last measured load of that model here, or nothing when it has not loaded since timing
began (never a made-up number). services/residency_arbiter records every real spawn; services/model_slate
reads it. Held by tests/unit/test_seat_timing.py (record, read back, restart, damaged file, nonsense refused,
and a source check that the arbiter records its spawns).
"""
from __future__ import annotations

import json
import logging
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


def _load_once() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        for mid, rec in raw.items():
            if isinstance(rec, dict) and isinstance(rec.get("seconds"), (int, float)):
                _TIMES[str(mid)] = {"seconds": float(rec["seconds"]), "at": float(rec.get("at") or 0)}
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError) as e:
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
    """Remember a real cold load. Zero (already seated), negatives, non-numbers and absurd values are not loads."""
    try:
        sec = float(seconds)
    except (TypeError, ValueError):
        return False
    if not model_id or not 0.0 < sec <= _MAX_SECONDS:
        return False
    with _LOCK:
        _load_once()
        _TIMES[str(model_id)] = {"seconds": round(sec, 1), "at": time.time()}
        while len(_TIMES) > _MAX_ENTRIES:
            _TIMES.pop(min(_TIMES, key=lambda k: _TIMES[k]["at"]))
        _save()
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
