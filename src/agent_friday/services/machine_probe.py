"""Reading the machine must never make a menu wait.

Two menus were timing out, and measurement on 2026-09-23 showed neither was
slow for the reason anyone expected. Neither was model discovery, a VRAM probe,
or the residency arbiter:

  * **The model picker** (`/api/models` -> `build_catalog`) spent **18s**, of
    which `_tts_engines()` was 14.3s and `_voice_engines()` 3.4s. Every
    `_model_entries_for(provider)` was 0.00s. `_tts_engines` calls
    `kokoro_health()`, which deliberately IMPORTS kokoro to find out whether the
    package actually loads — and that pulls in torch. Measured alone:
    **28.9s on first call, 0.00s after.** So the first person to open the model
    picker after a restart waited for a torch import.

  * **Settings > Intelligence** (`/api/health` -> `inference_health`) ran seven
    provider probes **serially**, 44s in total, and `_PROBE_TTL_S` is 60 — so it
    went slow again every minute. Four of the seven were failures that each
    burned 4-12s on a connection timeout (ollama-local 12.1s with no daemon,
    fridayweaver-seat 8.0s with nothing on :8095), and two were REAL billable
    inference calls (openrouter 6.3s, "generated in 6322ms").

`services/swr_cache` already keeps the last answer per key and refreshes it in
the background, which is most of the fix. What it cannot do is the cold case:
on a miss it computes in the CALLER's thread, so the first open still pays the
full 29s. That is exactly the symptom being fixed, so this module adds the
missing half — a hard budget on the cold path — and leaves `swr_cache` alone,
since another session is actively working in it.

The contract every caller gets:

  * a value younger than ``fresh_for`` — returned as is, ``state="fresh"``;
  * an older value — returned immediately with ``state="stale"`` and a
    background refresh kicked, so the menu shows the last known answer rather
    than a spinner;
  * nothing cached and the probe does not finish inside ``budget`` — the
    caller's ``default`` with ``state="unknown"``, and the probe keeps running
    so the next open is fast.

``state`` is the point. A UI can say "GPU voice: couldn't read yet" or
"as of 2 minutes ago" instead of showing a spinner that ends in a timeout, and
nothing here ever presents a stale or missing reading as a live one.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from agent_friday.services import swr_cache

_log = logging.getLogger("friday.machine_probe")

#: How long a cold caller may wait before it gives up and renders a placeholder.
#: Deliberately short: a menu is an interactive surface, and every probe behind
#: one is either a network round trip to something that may be absent or an
#: import of a multi-hundred-megabyte library.
DEFAULT_BUDGET_S = 1.5

#: Per-probe ceiling for the parallel provider sweep. A probe that has not
#: answered in this long is reported as unreachable rather than waited on: the
#: measured failures took 4-12s each, and the answer they eventually gave was
#: "down" anyway.
DEFAULT_PROBE_TIMEOUT_S = 2.0

STATE_FRESH = "fresh"
STATE_STALE = "stale"
STATE_UNKNOWN = "unknown"


def snapshot(key: str, compute: Callable[[], Any], *, fresh_for: float,
             budget: float = DEFAULT_BUDGET_S,
             default: Any = None) -> tuple[Any, float, str]:
    """``(value, computed_at, state)`` for ``key``, never blocking past ``budget``.

    ``computed_at`` is 0.0 exactly when ``state`` is ``"unknown"``, so a caller
    cannot accidentally render a placeholder with a plausible-looking age.
    """
    hit = swr_cache.peek(key)
    if hit is not None:
        value, ts = hit
        age = time.time() - ts
        if age <= fresh_for:
            return value, ts, STATE_FRESH
        # Stale: hand back the last answer now, refresh behind the request.
        _kick(key, compute, fresh_for)   # fire and forget
        return value, ts, STATE_STALE

    # Cold. Start it in the background and spend the budget only if WE started
    # it. A caller that merely arrives while a probe is already in flight waits
    # zero: the budget exists to let a FAST probe answer the first caller, not
    # to make every later caller pay it again. Without this, a 29-second import
    # made every menu open cost the full budget for 29 seconds.
    done, started_here = _kick(key, compute, fresh_for)
    if started_here and done is not None and done.wait(max(0.0, budget)):
        got = swr_cache.peek(key)
        if got is not None:
            return got[0], got[1], STATE_FRESH
    return default, 0.0, STATE_UNKNOWN


_kicks: dict[str, threading.Event] = {}
_kicks_lock = threading.Lock()


def _kick(key: str, compute: Callable[[], Any],
          fresh_for: float) -> tuple[threading.Event | None, bool]:
    """Start (or join) one background computation for ``key``.

    Returns ``(event, started_here)``. The event fires when this round finishes;
    ``started_here`` is False for a caller that found one already running, which
    is what lets `snapshot` charge the budget to the first caller only.

    One thread per key: a menu polled in a loop must not spawn a probe per poll,
    which is how a failing probe becomes a thread leak.
    """
    with _kicks_lock:
        existing = _kicks.get(key)
        if existing is not None:
            return existing, False
        ev = threading.Event()
        _kicks[key] = ev

    def _run():
        try:
            # fresh_for=0 forces this call to actually recompute; swr_cache
            # still de-duplicates concurrent work on the same key.
            swr_cache.get(key, compute, fresh_for=0.0)
        except Exception as e:  # noqa: BLE001 - a probe failure is data
            _log.debug("machine_probe: %s failed: %s", key, e)
        finally:
            with _kicks_lock:
                _kicks.pop(key, None)
            ev.set()

    threading.Thread(target=_run, name="probe:%s" % key, daemon=True).start()
    return ev, True


def probe_all(probes: dict[str, Callable[[], Any]], *,
              timeout: float = DEFAULT_PROBE_TIMEOUT_S) -> dict[str, dict]:
    """Run every probe in PARALLEL with a hard per-probe ceiling.

    Returns ``{name: {"ok": bool, "value": …, "error": str|None,
    "timed_out": bool, "seconds": float}}``.

    Serial was the whole problem: seven probes at 4-12s each is 44 seconds, and
    the four slowest were failures whose verdict was known the moment the
    connection was refused. In parallel with a 2s ceiling the same sweep costs
    about 2 seconds, and a probe that overruns is reported as unreachable
    instead of being waited on.

    A timed-out probe's thread is left running rather than killed -- Python
    cannot safely interrupt a blocking socket read -- but it is a daemon thread
    and nothing waits on its result, so it cannot hold up a response.
    """
    from concurrent.futures import ThreadPoolExecutor

    out: dict[str, dict] = {}
    if not probes:
        return out
    started: dict[str, float] = {}
    ex = ThreadPoolExecutor(max_workers=max(1, min(len(probes), 12)),
                            thread_name_prefix="probe_all")
    try:
        futures = {}
        for name, fn in probes.items():
            started[name] = time.time()
            futures[name] = ex.submit(fn)
        deadline = time.time() + max(0.0, timeout)
        for name, fut in futures.items():
            remaining = max(0.0, deadline - time.time())
            try:
                value = fut.result(timeout=remaining)
                out[name] = {"ok": True, "value": value, "error": None,
                             "timed_out": False,
                             "seconds": round(time.time() - started[name], 3)}
            except TimeoutError:
                out[name] = {"ok": False, "value": None, "timed_out": True,
                             "error": "did not answer within %.1fs" % timeout,
                             "seconds": round(time.time() - started[name], 3)}
            except Exception as e:  # noqa: BLE001 - a failure is a verdict
                out[name] = {"ok": False, "value": None, "timed_out": False,
                             "error": "%s: %s" % (type(e).__name__, str(e)[:120]),
                             "seconds": round(time.time() - started[name], 3)}
    finally:
        # Do not join: a probe blocked on a refused connection would otherwise
        # make shutdown as slow as the thing we are avoiding.
        ex.shutdown(wait=False)
    return out


def age_note(computed_at: float, state: str) -> str | None:
    """A short human phrase for how old a reading is, or None when it is fresh.

    Returned rather than formatted into the value so each surface can place it
    where it belongs; the point is that a stale reading always CAN say so.
    """
    if state == STATE_FRESH:
        return None
    if state == STATE_UNKNOWN or not computed_at:
        return "not read yet"
    secs = max(0, int(time.time() - computed_at))
    if secs < 90:
        return "as of %ds ago" % secs
    if secs < 5400:
        return "as of %dm ago" % (secs // 60)
    return "as of %dh ago" % (secs // 3600)


# ── boot warming ────────────────────────────────────────────────────────────

#: Registered by the modules that own each slow reading, so `warm_all()` does
#: not need to import them (and pull torch in) just to know they exist.
_WARMERS: dict[str, Callable[[], Any]] = {}


def register_warmer(name: str, fn: Callable[[], Any]) -> None:
    _WARMERS[name] = fn


def warm_all() -> list:
    """Kick every registered warmer on background threads. Returns their names.

    Called once at boot so the first person to open a menu finds a populated
    cache. Never waits: boot must not be slower because a probe is slow, which
    would move the same stall to startup instead of removing it.
    """
    names = []
    for name, fn in list(_WARMERS.items()):
        names.append(name)
        threading.Thread(target=_safe, args=(name, fn),
                         name="warm:%s" % name, daemon=True).start()
    if names:
        _log.info("machine_probe: warming %d reading(s) in the background: %s",
                  len(names), ", ".join(names))
    return names


def _safe(name, fn):
    t0 = time.time()
    try:
        fn()
        _log.info("machine_probe: %s warmed in %.1fs", name, time.time() - t0)
    except Exception as e:  # noqa: BLE001
        _log.warning("machine_probe: %s could not be warmed (%s)", name, e)
