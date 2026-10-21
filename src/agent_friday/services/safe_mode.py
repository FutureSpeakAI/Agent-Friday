"""Safe mode: a start with extensions and the cloud off, so Friday can be
repaired (north star §28.5, §33.5-3; burn-down BD-03).

In safe mode Friday starts a minimal local interface:

* no extensions: MCP servers are not started or reloaded, user and learned
  skills are not loaded (the skills shipped with the app still are), tool
  hooks do not run, connectors are not polled and connected-service reads are
  held, remote channels, the phone line and published-page hosting stay off;
* no cloud: every cloud model call is refused (even with a per-turn intent),
  routing is local only for this run, web search and web fetch are refused,
  nothing is downloaded at start and no update check is made;
* schedules paused, outward actions held (the action gate's outward_hold);
* still there: chat with a local model if one is installed, read-only data,
  the Activity ledger, the Doctor report, backup (the Friday Bundle) and the
  owner's way out.

How it is entered, in order of precedence:

* ``FRIDAY_SAFE_START=1`` in the environment of the process (``friday safe``
  and the tray's "Restart in safe mode" set it);
* ``<home>/safe_mode/ON``, written by "Start in safe mode next time" (CLI
  ``friday safe-mode on`` or the Doctor page); it stays until the owner leaves
  safe mode, so a start that keeps failing cannot slip out of it;
* automatically, when the last three starts failed (services/boot_guard keeps
  the count): safe mode must stay reachable after a failed migration or a
  runtime that will not load (§28.5-2).

The mode is decided once per process, at the first question, and does not
change while it runs: leaving takes a restart, like entering.

It also implies services/boot_guard's self-modification lock (FRIDAY_SAFE_MODE),
which is a narrower, older switch with a similar name.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

#: What safe mode turns off. Each name is asked about where that part starts.
DISABLED = (
    "mcp", "skills", "tool_hooks", "connectors", "connector_reads", "channels",
    "phone", "publish_hosting", "cloud", "web", "first_run_models",
    "update_check", "scheduler", "reconcile_resume", "publisher", "call_watch",
    "notifications_loop", "news", "podcasts", "media_index", "library_runtime",
    "network_monitor", "predictive_prewarm", "credential_sweep", "model_seats",
    "voice_warm", "local_address", "machine_monitor",
)

FAILED_STARTS = 3
_LOCK = threading.Lock()
_DECIDED: dict = {}


def marker_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "safe_mode" / "ON"


def _failed_starts() -> int:
    try:
        from agent_friday.services import boot_guard
        return int(boot_guard._read_attempt().get("consecutive_failures") or 0)
    except Exception:
        return 0


def _decide() -> dict:
    if os.environ.get("FRIDAY_SAFE_START", "").strip().lower() in ("1", "true", "yes", "on"):
        return {"active": True, "why": "started in safe mode", "how": "start"}
    try:
        if marker_path().exists():
            return {"active": True, "why": "safe mode was chosen for the next start",
                    "how": "marker"}
    except OSError:
        pass
    n = _failed_starts()
    if n >= FAILED_STARTS:
        return {"active": True, "why": "the last %d starts failed" % n, "how": "failed_starts"}
    return {"active": False, "why": "", "how": ""}


def state() -> dict:
    """{active, why, how, since}: decided once per process."""
    with _LOCK:
        if not _DECIDED:
            _DECIDED.update(_decide(), since=time.time())
        return dict(_DECIDED)


def active() -> bool:
    return bool(state().get("active"))


def allows(part: str) -> bool:
    """False when safe mode keeps `part` (one of DISABLED) off."""
    return not (part in DISABLED and active())


def refusal(part: str) -> Optional[str]:
    """The sentence a refused part returns, or None when it may run."""
    if allows(part):
        return None
    return ("Friday is in safe mode (%s): extensions and the cloud are off until "
            "you leave safe mode and restart." % state().get("why"))


def set_next_start(on: bool) -> dict:
    """Write or remove the marker for the next start. Returns the new plan.
    The running process keeps its mode until it restarts."""
    p = marker_path()
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"chosen_at": time.time()}), encoding="utf-8")
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    return plan()


def plan() -> dict:
    """What is true now and what the next start will do."""
    now = state()
    try:
        nxt = marker_path().exists() or _failed_starts() >= FAILED_STARTS
    except OSError:
        nxt = False
    return {"active": now["active"], "why": now.get("why"), "how": now.get("how"),
            "next_start_safe": bool(nxt), "disabled": list(DISABLED) if now["active"] else []}


def reset_for_tests() -> None:
    with _LOCK:
        _DECIDED.clear()
