"""Memory census: where the server's Python memory is growing, by code location.

Three loopback-only routes for diagnosing growth in a running server:

  * ``POST /api/debug/memtrace/start?frames=1&minutes=15`` starts
    ``tracemalloc`` if it is not already tracing and stores a baseline
    snapshot. It traces one frame unless ``frames`` asks for more (at most
    25), and stops by itself after ``minutes`` (default 15, at most 60).
  * ``GET /api/debug/memtrace/top?limit=30`` compares a fresh snapshot with the
    baseline, grouped by traceback, and adds the 30 most numerous object types
    and the process's private bytes.
  * ``POST /api/debug/memtrace/stop`` stops tracing and drops the baseline.

Invariants:

  * Every route answers only a request from this machine (never a tunnel or a
    proxy) that carries the page's X-Friday-Token header.
  * Nothing returned is memory CONTENT. The census reports sizes, counts, type
    names and code locations (file and line) only; file paths are shortened to
    the package-relative part so no home directory appears.
  * Tracing costs memory and CPU while it runs, and slows every allocation in
    the server: it is off unless started here, and every run has a deadline
    after which a timer stops it whether or not anyone calls stop.
"""
from __future__ import annotations

import collections
import gc
import os
import threading
import tracemalloc

from flask import Blueprint, jsonify, request

import agent_friday.core as core
from agent_friday.core import login_required

memtrace_bp = Blueprint("memtrace", __name__)

TRACE_FRAMES = 1
MAX_TRACE_FRAMES = 25
AUTO_STOP_MINUTES = 15
MAX_AUTO_STOP_MINUTES = 60
GC_TYPE_LIMIT = 30
MAX_LIMIT = 200

_Timer = threading.Timer
_lock = threading.Lock()
_state: dict = {"baseline": None, "timer": None, "run": 0}


def _reset_for_tests() -> None:
    with _lock:
        _disarm_locked()
        _state["baseline"] = None


def _disarm_locked() -> None:
    timer = _state.get("timer")
    _state["timer"] = None
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass


def _stop_locked() -> bool:
    _disarm_locked()
    was = tracemalloc.is_tracing()
    if was:
        tracemalloc.stop()
    _state["baseline"] = None
    _state["run"] += 1
    return was


def _auto_stop(run: int) -> None:
    """The deadline of run ``run``; a later run is never stopped by it."""
    with _lock:
        if _state["run"] == run:
            _stop_locked()


def _int_arg(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _refusal() -> str:
    if not core._is_local_request():
        return "Only a request from this PC can read the memory census."
    if not core._api_token_valid(request.headers.get("X-Friday-Token")):
        return "This request did not come from Friday's page."
    return ""


def _refused(msg: str):
    return jsonify({"ok": False, "message": msg}), 403


_EXCLUDE = (
    tracemalloc.Filter(False, tracemalloc.__file__),
    tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
    tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
    tracemalloc.Filter(False, "<unknown>"),
)


def _snapshot() -> tracemalloc.Snapshot:
    return tracemalloc.take_snapshot().filter_traces(_EXCLUDE)


def _short(filename: str) -> str:
    """The package-relative part of a path: never a home directory."""
    p = (filename or "").replace("\\", "/")
    if p.startswith("<"):
        return p
    for anchor in ("/site-packages/", "/Lib/", "/lib/"):
        if anchor in p:
            return p.rsplit(anchor, 1)[1]
    parts = p.split("/")
    if "agent_friday" in parts:
        return "/".join(parts[len(parts) - 1 - parts[::-1].index("agent_friday"):])
    return "/".join(parts[-3:])


def _is_ours(filename: str) -> bool:
    return "/agent_friday/" in (filename or "").replace("\\", "/")


def _private_bytes():
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mi = proc.memory_info()
        private = getattr(mi, "private", None)
        if private is None:
            private = getattr(proc.memory_full_info(), "uss", None)
        return {"private_bytes": private, "rss_bytes": getattr(mi, "rss", None)}
    except Exception:
        return {"private_bytes": None, "rss_bytes": None}


def _gc_types(limit: int = GC_TYPE_LIMIT) -> list:
    counts = collections.Counter()
    for o in gc.get_objects():
        t = type(o)
        counts["%s.%s" % (t.__module__, t.__qualname__)] += 1
    return [{"type": name, "count": n} for name, n in counts.most_common(limit)]


@memtrace_bp.route("/api/debug/memtrace/start", methods=["POST"])
@login_required
def memtrace_start():
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    frames = _int_arg("frames", TRACE_FRAMES, 1, MAX_TRACE_FRAMES)
    minutes = _int_arg("minutes", AUTO_STOP_MINUTES, 1, MAX_AUTO_STOP_MINUTES)
    seconds = minutes * 60
    with _lock:
        already = tracemalloc.is_tracing()
        if not already:
            tracemalloc.start(frames)
        _state["baseline"] = _snapshot()
        _disarm_locked()
        _state["run"] += 1
        timer = _Timer(seconds, _auto_stop, args=(_state["run"],))
        timer.daemon = True
        _state["timer"] = timer
        timer.start()
    return jsonify({"ok": True, "tracing": True, "already_running": already,
                    "frames": tracemalloc.get_traceback_limit(),
                    "stops_in_seconds": seconds})


@memtrace_bp.route("/api/debug/memtrace/top", methods=["GET"])
@login_required
def memtrace_top():
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    try:
        limit = int(request.args.get("limit", 30))
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, MAX_LIMIT))
    with _lock:
        baseline = _state["baseline"]
        if baseline is None or not tracemalloc.is_tracing():
            return jsonify({"ok": False,
                            "message": "The census is not running; start it first."}), 409
        current = _snapshot()
    diffs = current.compare_to(baseline, "traceback")
    diffs.sort(key=lambda d: d.size_diff, reverse=True)
    top = []
    for d in diffs[:limit]:
        # tracemalloc orders a traceback oldest frame first.
        frames = [{"file": _short(f.filename), "line": f.lineno}
                  for f in reversed(d.traceback)]
        ours = next(({"file": _short(f.filename), "line": f.lineno}
                     for f in reversed(d.traceback) if _is_ours(f.filename)), None)
        top.append({
            "size_diff": d.size_diff, "size": d.size,
            "count_diff": d.count_diff, "count": d.count,
            "top_frame": frames[0] if frames else None,
            "agent_friday_frame": ours,
            "frames": frames,
        })
    traced, peak = tracemalloc.get_traced_memory()
    return jsonify({
        "ok": True,
        "top": top,
        "traced_bytes": traced,
        "traced_peak_bytes": peak,
        "gc_types": _gc_types(),
        "process": _private_bytes(),
    })


@memtrace_bp.route("/api/debug/memtrace/stop", methods=["POST"])
@login_required
def memtrace_stop():
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    with _lock:
        was = _stop_locked()
    return jsonify({"ok": True, "tracing": False, "was_running": was})
