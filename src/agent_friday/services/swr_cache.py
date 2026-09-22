"""
services/swr_cache.py — stale-while-revalidate cache for slow read-only data.

Some panels render from work that takes seconds to minutes (a git status
sweep over every repo, an RSS crawl, a Google Calendar round trip) and whose
answer rarely changes between two opens of the same panel. This cache keeps
the last answer per key:

  * younger than ``fresh_for`` seconds -> returned as is;
  * older -> still returned immediately, and ONE background thread
    recomputes it, so the next read is current;
  * absent (or older than ``max_age``) -> computed in the caller's thread.
    Concurrent cold callers share that one computation.

Every read returns ``(value, computed_at)`` so a response can state how old
its data is instead of passing cached data off as live.

``invalidate(prefix)`` drops matching keys. A refresh that was already
running when its key was invalidated does not write its (pre-invalidation)
result back, so a mutation is never followed by a read of the state from
before it.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from typing import Any, Callable, Optional

_lock = threading.Lock()
_entries: dict[str, tuple[float, Any]] = {}
_inflight: dict[str, Future] = {}
_generation: dict[str, int] = {}


def _run(key: str, compute: Callable[[], Any], fut: Future, gen: int) -> None:
    try:
        value = compute()
        ts = time.time()
        with _lock:
            if _generation.get(key, 0) == gen:
                _entries[key] = (ts, value)
        fut.set_result((value, ts))
    except BaseException as e:  # noqa: BLE001 - delivered to the waiters
        fut.set_exception(e)
    finally:
        with _lock:
            if _inflight.get(key) is fut:
                del _inflight[key]


def get(key: str, compute: Callable[[], Any], fresh_for: float,
        max_age: Optional[float] = None) -> tuple[Any, float]:
    """Return ``(value, computed_at)`` for ``key``; see the module docstring."""
    now = time.time()
    with _lock:
        hit = _entries.get(key)
        if hit is not None and (max_age is None or now - hit[0] <= max_age):
            if now - hit[0] > fresh_for and key not in _inflight:
                fut: Future = Future()
                _inflight[key] = fut
                threading.Thread(target=_run, name=f"swr:{key}", daemon=True,
                                 args=(key, compute, fut, _generation.get(key, 0))).start()
            return hit[1], hit[0]
        fut = _inflight.get(key)
        owner = fut is None
        if owner:
            fut = Future()
            _inflight[key] = fut
            gen = _generation.get(key, 0)
    if owner:
        _run(key, compute, fut, gen)
    return fut.result()


def peek(key: str) -> Optional[tuple[Any, float]]:
    """The cached ``(value, computed_at)`` without computing anything."""
    with _lock:
        hit = _entries.get(key)
    return (hit[1], hit[0]) if hit else None


def invalidate(prefix: str = "") -> int:
    """Drop every key starting with ``prefix``; returns how many were cached."""
    with _lock:
        keys = {k for k in _entries if k.startswith(prefix)}
        keys |= {k for k in _inflight if k.startswith(prefix)}
        for k in keys:
            _generation[k] = _generation.get(k, 0) + 1
            _inflight.pop(k, None)
        dropped = [k for k in keys if k in _entries]
        for k in dropped:
            del _entries[k]
    return len(dropped)
