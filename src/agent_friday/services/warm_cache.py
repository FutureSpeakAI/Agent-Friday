"""Answer instantly from the last known good value, refresh behind the user.

THE COMPLAINT, 2026-09-22: "it can't read the catalogue and times out,
preventing me from switching... We need to populate this list once, upon
startup (running in the background so it doesn't stop the UI from launching),
and keep that list available for quick retrieval. I think that goes for all of
our workspace data so that the user doesn't try to load something and then sit
there for a minute or longer."

Measured before building anything: build_catalog() takes 18.9 s and produces
598 models. GET /api/models calls it synchronously, so the model picker spends
nineteen seconds doing nothing visible, and under load it exceeds the client's
timeout entirely - which is the failure he hit.

THREE PROPERTIES, and the third is the one usually skipped.

1. NEVER BLOCK. get() returns whatever is in hand and says how old it is. A
   caller that wants to wait can ask; nothing waits by default.

2. SURVIVE RESTART. The last good value is written to
   ~/.friday/cache/<name>.json and read back at startup, so a restart costs
   milliseconds instead of nineteen seconds. Warming on boot alone would still
   leave a cold window at every restart, which is exactly when someone is most
   likely to be poking at the UI.

3. SAY WHEN IT IS STALE, AND WHEN IT FAILED. Each entry reports age, whether a
   refresh is in flight, and the last error. A cache that silently serves
   month-old data is a worse bug than a slow endpoint, because the slow
   endpoint is at least honest. `/api/models` already has UI for exactly this
   ("catalog stale, showing cached") - this makes it true of everything.

A refresh that raises NEVER clobbers a good value. Stale-while-revalidate: the
old answer keeps being served and the error is recorded alongside it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger("friday.warm_cache")

#: Stagger between warms at startup. Twelve providers all probing at once on a
#: machine already loading a 27B model is how a "background" warm becomes a
#: foreground stall.
WARM_STAGGER_S = 1.5


@dataclass
class Entry:
    name: str
    fn: Callable[[], Any]
    ttl_s: float
    persist: bool = True
    value: Any = None
    fetched_at: float = 0.0
    last_error: Optional[str] = None
    refreshing: bool = False
    #: An input changed after this value was computed (see `invalidate`).
    dirty: bool = False
    #: Bumped by every invalidate(), so a refresh that was already running
    #: when an input changed does not clear the flag for a value it did not see.
    generation: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def age_s(self) -> float:
        return (time.time() - self.fetched_at) if self.fetched_at else float("inf")

    @property
    def is_stale(self) -> bool:
        return self.dirty or self.age_s > self.ttl_s

    @property
    def has_value(self) -> bool:
        return self.fetched_at > 0


_ENTRIES: Dict[str, Entry] = {}
_REG_LOCK = threading.Lock()
_STARTED = False


def _cache_dir():
    from agent_friday.core import FRIDAY_DIR
    d = FRIDAY_DIR / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _disk_path(name: str):
    safe = "".join(c for c in name if c.isalnum() or c in "-_")[:64] or "unnamed"
    return _cache_dir() / f"{safe}.json"


def register(name: str, fn: Callable[[], Any], ttl_s: float = 900.0,
             persist: bool = True) -> None:
    """Declare something worth keeping warm. Idempotent by name."""
    with _REG_LOCK:
        if name in _ENTRIES:
            _ENTRIES[name].fn = fn
            _ENTRIES[name].ttl_s = ttl_s
            return
        e = Entry(name=name, fn=fn, ttl_s=ttl_s, persist=persist)
        _ENTRIES[name] = e
    if persist:
        _load_from_disk(e)


def _load_from_disk(e: Entry) -> None:
    """Seed from the last run. This is what makes a restart instant."""
    try:
        p = _disk_path(e.name)
        if not p.exists():
            return
        blob = json.loads(p.read_text(encoding="utf-8"))
        e.value = blob.get("value")
        e.fetched_at = float(blob.get("fetched_at") or 0)
        _log.debug("warm_cache %s seeded from disk (age %.0fs)", e.name, e.age_s)
    except Exception as ex:
        _log.warning("warm_cache %s could not read its disk copy: %s", e.name, ex)


def _save_to_disk(e: Entry) -> None:
    if not e.persist:
        return
    try:
        p = _disk_path(e.name)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"value": e.value, "fetched_at": e.fetched_at},
                                  ensure_ascii=False, default=str),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception as ex:
        _log.warning("warm_cache %s could not persist: %s", e.name, ex)


def refresh(name: str, blocking: bool = False) -> bool:
    """Recompute one entry. Returns True if a fresh value was stored.

    A failure keeps the previous value and records the error - the whole point
    of stale-while-revalidate is that a bad refresh is less bad than no answer.
    """
    e = _ENTRIES.get(name)
    if e is None:
        return False
    if e.refreshing and not blocking:
        return False
    with e.lock:
        e.refreshing = True
        try:
            gen = e.generation
            v = e.fn()
            e.value = v
            e.fetched_at = time.time()
            e.last_error = None
            if e.generation == gen:
                e.dirty = False
            _save_to_disk(e)
            return True
        except Exception as ex:
            e.last_error = f"{type(ex).__name__}: {ex}"
            _log.warning("warm_cache %s refresh failed: %s", name, e.last_error)
            return False
        finally:
            e.refreshing = False


def get(name: str, compute_if_cold: bool = False) -> dict:
    """The value plus its provenance. Never raises, never blocks by default.

    Returns {value, ready, age_s, stale, refreshing, error}. `ready` is False
    only when there has never been a value - the caller then decides whether to
    show a spinner or wait, rather than having the wait imposed on it.
    """
    e = _ENTRIES.get(name)
    if e is None:
        return {"value": None, "ready": False, "age_s": None, "stale": True,
                "refreshing": False, "error": f"no such cache: {name}"}
    if (not e.has_value or e.dirty) and compute_if_cold:
        # Cold, or known to predate a change someone just made (a catalog
        # refresh they asked for): serving the old value would show them
        # their own action as not having happened.
        refresh(name, blocking=True)
    elif e.is_stale and not e.refreshing:
        # Serve now, refresh behind them.
        threading.Thread(target=refresh, args=(name,),
                         name=f"warm-{name}", daemon=True).start()
    return {"value": e.value, "ready": e.has_value,
            "age_s": round(e.age_s, 1) if e.has_value else None,
            "stale": e.is_stale, "refreshing": e.refreshing,
            "error": e.last_error}


def invalidate(name: Optional[str] = None) -> None:
    """Mark one entry (or every entry) out of date because an input changed.

    The value is kept, so a caller that never waits still gets an answer and
    a background refresh; a caller passing ``compute_if_cold=True`` gets a
    recomputed value. Unknown names are ignored: the writer of an input does
    not need to know whether anything has cached a view of it yet.
    """
    with _REG_LOCK:
        entries = list(_ENTRIES.values()) if name is None else \
            [e for e in (_ENTRIES.get(name),) if e is not None]
    for e in entries:
        e.generation += 1
        e.dirty = True


def start_warming() -> None:
    """Warm everything registered, on one background thread. Idempotent.

    One thread, staggered, rather than a thread per entry: these are mostly
    I/O-bound probes against the same few subsystems, and a stampede at boot
    is the thing being fixed, not a cheaper way to cause it.
    """
    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    def _loop():
        for name in list(_ENTRIES):
            e = _ENTRIES.get(name)
            if e is None:
                continue
            # Something seeded from disk and still inside its TTL needs nothing.
            if e.has_value and not e.is_stale:
                continue
            refresh(name)
            time.sleep(WARM_STAGGER_S)

    threading.Thread(target=_loop, name="warm-cache-boot", daemon=True).start()


def status() -> dict:
    """What is warm, how old, and what failed - for /api/health and the UI."""
    out = {}
    for name, e in _ENTRIES.items():
        out[name] = {"ready": e.has_value,
                     "age_s": round(e.age_s, 1) if e.has_value else None,
                     "ttl_s": e.ttl_s, "stale": e.is_stale,
                     "refreshing": e.refreshing, "error": e.last_error,
                     "persisted": e.persist}
    return out


def registered() -> list:
    return sorted(_ENTRIES)
