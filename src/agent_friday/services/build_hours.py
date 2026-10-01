"""Build hours: the window in which an external daemon owns the brain seat.

During build hours a daemon outside this process parks the brain (stops the
llama-server on its port, keeping the command line), re-parks it after every
restart, and restores it when the window ends. While the window is open the
Arbiter must not relaunch a pinned GPU language seat: every relaunch is killed
within seconds by the daemon, and the ping-pong leaves `endpoints.json`
pointing at a port nothing serves. The "previous seat" a heavy job restores
during build hours is therefore the PARKED state, and the verification of
that restore is that nothing is listening on the seat's port.

The window is a flag file the daemon writes, with optional `start:` and
`end:` lines (ISO minutes). No `end:` means the next 07:00 after the file's
modification time, which is the daemon's own rule. The path is configurable
with `FRIDAY_BUILD_HOURS_FLAG`; the default is `BUILD_HOURS` in Friday's home,
where the build-hours daemon mirrors its flag while the window is open.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

RESTORE_HOUR = 7


def flag_path() -> Path:
    override = os.environ.get("FRIDAY_BUILD_HOURS_FLAG")
    if override:
        return Path(override)
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "BUILD_HOURS"


def _next_seven(after: dt.datetime) -> dt.datetime:
    candidate = after.replace(hour=RESTORE_HOUR, minute=0, second=0,
                              microsecond=0)
    if candidate <= after:
        candidate += dt.timedelta(days=1)
    return candidate


def window(path: Path | None = None) -> tuple[dt.datetime, dt.datetime] | None:
    """(start, end) of the window the flag declares, or None without a flag."""
    p = path or flag_path()
    try:
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8", errors="replace")
        mtime = dt.datetime.fromtimestamp(p.stat().st_mtime)
    except Exception:
        return None
    start, end = mtime, None
    for line in text.splitlines():
        key, _, val = line.partition(":")
        val = val.strip()
        try:
            if key.strip() == "start" and val:
                start = dt.datetime.fromisoformat(val)
            elif key.strip() == "end" and val:
                end = dt.datetime.fromisoformat(val)
        except ValueError:
            continue
    return start, (end or _next_seven(mtime))


def is_active(now: dt.datetime | None = None, path: Path | None = None) -> bool:
    """True while the daemon owns the brain seat."""
    w = window(path)
    if w is None:
        return False
    start, end = w
    now = now or dt.datetime.now()
    return start <= now < end


def describe(path: Path | None = None) -> str:
    w = window(path)
    if w is None:
        return "build hours are not active"
    return "build hours until %s (the brain seat is parked by the build-hours daemon)" % \
        w[1].strftime("%Y-%m-%d %H:%M")
