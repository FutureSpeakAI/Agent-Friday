"""
Agent Friday -- filesystem probes that cannot stall a request.

`Path.exists()` on a UNC path (`\\\\wsl.localhost\\...`, `\\\\server\\share`)
blocks for the SMB timeout when the share is wedged. Measured on
2026-09-17 with the WSL share hung: `/api/residency/status` timed out at 25 s
and `/api/health` took 10.8 s, because `model_store.available()` and
`local_seats._friday_store()` both call `exists()` on every registered model
path, one of which is that share. A dead share became a slow Friday in
every tab, and the top-bar model pill reads exactly those endpoints.

This module answers "is this file there?" without ever letting a remote
path hold the caller for longer than `timeout_s`:

  * a local path is probed inline, as before;
  * a remote path is probed on a daemon thread with a short budget. If the
    thread answers in time, the answer is cached. If it does not, the caller
    gets `False` at once and the miss is cached with a reason, so the next
    caller does not pay again. The probe thread finishes in its own time and
    corrects the cache when it does.

A negative answer here means "not available right now", which is what every
caller wants to know. It does not mean the file was deleted; `probe_state()`
says which.

Deliberately imports nothing from `agent_friday.core`: `local_seats` avoids
that import on the CLI path (a ~4 s Flask bootstrap) and this module must be
usable there.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

#: How long a request-path caller waits on a remote probe before taking the
#: cached (or negative) answer. A healthy share answers a stat in
#: milliseconds; 1.5 s keeps the cold call under the 2 s the residency status
#: endpoint is held to (model-soup.md §12 step 1).
DEFAULT_TIMEOUT_S = 1.5
#: How long a positive answer about a remote path is trusted.
POSITIVE_TTL_S = 30.0
#: How long a negative answer (absent or timed out) is trusted. Longer than
#: the positive TTL because a wedged share stays wedged for minutes, and every
#: re-probe inside that window costs the caller the full timeout again.
NEGATIVE_TTL_S = 60.0

_lock = threading.Lock()
# path -> {"present": bool, "at": float, "reason": str, "inflight": Thread|None}
_cache: dict = {}
# share root (`\\host\share`) -> when a probe on it last timed out. A share
# that is not answering is not answering for every path on it, so a second
# file on the same share is a cached miss at once rather than another wait.
# Measured before this existed: three files on the wedged WSL share cost
# 6.1 s on the first `available()` call, 2 s each in sequence.
_share_down: dict = {}


def share_root(path) -> str:
    r"""`\\host\share` for a UNC path, or "" for anything else."""
    s = str(path or "").replace("/", "\\")
    if not s.startswith("\\\\"):
        return ""
    parts = [p for p in s[2:].split("\\") if p]
    return "\\\\" + "\\".join(parts[:2]).lower() if len(parts) >= 2 else ""


def is_remote(path) -> bool:
    """A path that goes over a network redirector rather than a local disk."""
    s = str(path or "")
    return s.startswith("\\\\") or s.startswith("//")


def _probe(path: str) -> None:
    """The blocking probe, run on its own thread for remote paths."""
    try:
        present = Path(path).exists()
        reason = "" if present else "absent"
    except Exception as e:  # pragma: no cover - OS specific
        present, reason = False, "error: %s" % e
    with _lock:
        _cache[path] = {"present": present, "at": time.time(),
                        "reason": reason, "inflight": None}
        if present:
            # The share answered: stop treating its other paths as down.
            _share_down.pop(share_root(path), None)


def exists(path, timeout_s: float | None = None) -> bool:
    """`Path(path).exists()` that returns within `timeout_s` for remote paths.

    Local paths are probed inline. Remote paths go through the cache and a
    bounded background probe; a probe that overruns its budget is recorded as
    a miss with reason `timed out` and the caller gets `False` at once.
    `timeout_s` defaults to the module's `DEFAULT_TIMEOUT_S`, read at call
    time so it can be tuned without a restart of every caller.
    """
    if not path:
        return False
    if timeout_s is None:
        timeout_s = DEFAULT_TIMEOUT_S
    s = str(path)
    if not is_remote(s):
        try:
            return Path(s).exists()
        except Exception:
            return False

    now = time.time()
    root = share_root(s)
    with _lock:
        row = _cache.get(s)
        t = None
        down_at = _share_down.get(root) if root else None
        if (down_at is not None and now - down_at < NEGATIVE_TTL_S
                and not (row and row.get("present"))):
            _cache[s] = {"present": False, "at": now,
                         "reason": "share not answering (timed out on "
                                   "another path)", "inflight":
                         (row or {}).get("inflight")}
            return False
        if row:
            ttl = POSITIVE_TTL_S if row["present"] else NEGATIVE_TTL_S
            # A settled answer, or a timed-out one, is honoured inside its
            # TTL. Only a probe that is still inside its first budget
            # ("probing") makes a second caller wait on it.
            if now - row["at"] < ttl and row.get("reason") != "probing":
                return bool(row["present"])
            t = row.get("inflight")
        if t is None or not t.is_alive():
            t = threading.Thread(target=_probe, args=(s,), daemon=True,
                                 name="path-probe")
            _cache[s] = {"present": False, "at": now,
                         "reason": "probing", "inflight": t}
            t.start()
    t.join(timeout_s)
    with _lock:
        row = _cache.get(s) or {}
        if row.get("inflight") is None:
            return bool(row.get("present"))
        # Still running: the share is not answering. Record the miss so the
        # next caller reads the cache instead of waiting again. The thread
        # keeps the `inflight` handle so a later `exists()` inside the TTL
        # joins it briefly rather than spawning a second probe.
        row["present"] = False
        row["at"] = time.time()
        row["reason"] = "timed out after %.1fs" % timeout_s
        if root:
            _share_down[root] = row["at"]
        return False


def probe_state(path) -> dict:
    """What the cache knows about `path`: present, when, and why not."""
    s = str(path or "")
    if not is_remote(s):
        return {"remote": False, "present": exists(s), "reason": ""}
    with _lock:
        row = dict(_cache.get(s) or {})
    row.pop("inflight", None)
    row.setdefault("present", False)
    row.setdefault("reason", "not probed")
    row["remote"] = True
    return row


def reset_for_tests() -> None:
    with _lock:
        _cache.clear()
        _share_down.clear()
