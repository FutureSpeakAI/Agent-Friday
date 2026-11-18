"""How long each model really took to the first token on THIS PC, from the turns it actually answered.

The model slate's switch block says what a cold seat costs to load; this says what a model costs per answer
once running, and how much of the prompt the seat already held (``cache_share``: a first turn after a switch
reads none of the old conversation, so it is the slow one). Numbers are measured, never declared: the last
``_WINDOW`` calls per model, median and 90th percentile, with the sample count so a device can see how thin
the evidence is. services/reasoning_trace.model_call records the calls that report a first-token time (the local
OpenAI-compatible rounds inside an active trace: a cloud model, or a call made outside a trace, has no sample
and shows none, never a guess); services/model_slate reads.
Held by tests/unit/test_ttft_stats.py (record, summary, window, restart, damaged file, nonsense refused, and a
source check that model_call records).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

_log = logging.getLogger("friday.ttft_stats")
_LOCK = threading.Lock()
_CALLS: dict = {}
_LOADED = False
_WINDOW = 20
_MAX_MODELS = 64
_MAX_MS = 600_000


def _path():
    from agent_friday.paths import friday_home
    return friday_home() / "models" / "first_token_times.json"


def _load_once() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        for mid, rec in raw.items():
            calls = [c for c in rec.get("calls", []) if isinstance(c, list) and len(c) == 2
                     and isinstance(c[0], (int, float))]
            if calls:
                _CALLS[str(mid)] = {"at": float(rec.get("at") or 0), "calls": calls[-_WINDOW:]}
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError, TypeError) as e:
        _log.warning("saved first-token times unreadable, starting empty: %s", type(e).__name__)


def _save() -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_CALLS), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        _log.warning("first-token times not saved: %s", type(e).__name__)


def _cache_share(prefill, cache) -> Optional[float]:
    if isinstance(prefill, (int, float)) and isinstance(cache, (int, float)) and prefill >= 0 and cache >= 0:
        total = prefill + cache
        return round(cache / total, 3) if total else None
    return None


def record(model_id: str, ttft_ms, prefill_tokens=None, cache_tokens=None) -> bool:
    """Remember one answered call. Negative, zero, non-number and absurd waits are not measurements."""
    if isinstance(ttft_ms, bool) or not isinstance(ttft_ms, (int, float)) or not 0 < ttft_ms <= _MAX_MS:
        return False
    if not model_id:
        return False
    with _LOCK:
        _load_once()
        rec = _CALLS.setdefault(str(model_id), {"at": 0.0, "calls": []})
        rec["calls"].append([int(ttft_ms), _cache_share(prefill_tokens, cache_tokens)])
        del rec["calls"][:-_WINDOW]
        rec["at"] = time.time()
        while len(_CALLS) > _MAX_MODELS:
            _CALLS.pop(min(_CALLS, key=lambda k: _CALLS[k]["at"]))
        _save()
    return True


def _pick(sorted_ms: list, fraction: float) -> int:
    return sorted_ms[min(len(sorted_ms) - 1, int(fraction * len(sorted_ms)))]


def summary(model_id: str) -> Optional[dict]:
    """Median and p90 first-token wait over the last calls, or None when this PC has not timed the model."""
    with _LOCK:
        _load_once()
        rec = _CALLS.get(str(model_id))
        calls = list(rec["calls"]) if rec else []
    if not calls:
        return None
    waits = sorted(c[0] for c in calls)
    shares = [c[1] for c in calls if c[1] is not None]
    return {"samples": len(waits), "median_ms": _pick(waits, 0.5), "p90_ms": _pick(waits, 0.9),
            "cache_share": round(sum(shares) / len(shares), 3) if shares else None,
            "basis": "measured on this PC over its last %d answered call(s)" % len(waits)}


def _forget_memory_only() -> None:
    global _LOADED
    with _LOCK:
        _CALLS.clear()
        _LOADED = False
