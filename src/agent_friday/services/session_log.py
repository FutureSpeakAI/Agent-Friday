"""One line per start of Friday, saying how the run before it ended.

The crash-free session rate (north star §33.6, NS-33.6-1) needs to know, for
each run, whether it ended because the owner closed or restarted Friday, or
whether it simply stopped (a crash, a forced stop, a power loss). The task
journal already decides that once per start, from the clean-exit marker a
deliberate quit writes (services/task_journal.reconcile_on_boot); this keeps
the answer instead of dropping it.

Counts only: a time, the process id, and closed / unexpected for the run
before. Kept under <home>/logs/sessions.jsonl, on this PC; nothing reads it
but services/reliability_metrics.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

#: Lines kept; a few starts a day is years of history.
MAX_LINES = 4000
_LOCK = threading.Lock()


def path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "logs" / "sessions.jsonl"


def _read(p: Path) -> List[dict]:
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and isinstance(row.get("t"), (int, float)):
            out.append(row)
    return out


def note_start(previous_kind: Optional[str], *, now: Optional[float] = None) -> Optional[dict]:
    """Record this start and how the run before ended ("closed" or
    "unexpected"). The very first start on this PC has no run before it, so
    its `previous` is "none" whatever the marker said. Never raises."""
    try:
        p = path()
        with _LOCK:
            rows = _read(p)
            prev = str(previous_kind or "") if rows else "none"
            if prev not in ("closed", "unexpected", "none"):
                prev = "unexpected"
            row = {"t": float(now if now is not None else time.time()),
                   "pid": os.getpid(), "previous": prev}
            rows.append(row)
            if len(rows) > MAX_LINES:
                rows = rows[-MAX_LINES:]
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
                           encoding="utf-8", newline="\n")
            os.replace(tmp, p)
            return row
    except Exception:
        return None


def starts(since: float) -> List[dict]:
    """Every recorded start at or after `since`, oldest first."""
    with _LOCK:
        rows = _read(path())
    return [r for r in rows if r["t"] >= since]
