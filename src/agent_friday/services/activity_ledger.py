"""Global activity ledger (B4 — Seats & Transparency).

Append-only JSONL record of *metadata-only* activity events: which model ran,
on which seat, for how long, with which orb/task correlation ids — plus tool
call outcomes and subagent spawns. This is the single global answer to
"what has Friday actually been doing?" across chat, voice, background tasks
and the orchestrator.

PRIVACY BY CONSTRUCTION: the schema whitelists metadata fields per kind and
silently drops everything else. Prompt text, tool arguments and tool results
can never enter the ledger — there are no such fields.

Every write is best-effort: a ledger failure must never break a turn.
"""

import json
import threading
import time
from pathlib import Path

from agent_friday.core import FRIDAY_DIR

LEDGER_FILE = FRIDAY_DIR / "activity_ledger.jsonl"
_MAX_BYTES = 20 * 1024 * 1024   # rotate at 20 MB → activity_ledger.jsonl.1
_LOCK = threading.Lock()

# Whitelisted metadata fields per event kind. Anything not listed here is
# DROPPED on write — this is the egress-tier guarantee: names, ids, counts and
# durations only; never prompt text, never tool args/results.
_ALLOWED_FIELDS = {
    "model_invocation": {
        "model", "provider", "seat", "duration_ms",
        "tokens_in", "tokens_out", "orb_id", "task_id", "workspace",
    },
    "tool_call": {"tool", "ok", "duration_ms", "orb_id", "task_id"},
    "subagent_spawn": {"task_id", "description", "model"},
}

# Free-text fields still get a hard length cap so a caller can't smuggle a
# prompt through e.g. `description`.
_TEXT_CAP = 200


def _rotated_path():
    return Path(str(LEDGER_FILE) + ".1")


def _maybe_rotate_locked():
    """Rotate the ledger when it exceeds _MAX_BYTES. Caller holds _LOCK."""
    try:
        if LEDGER_FILE.exists() and LEDGER_FILE.stat().st_size >= _MAX_BYTES:
            rot = _rotated_path()
            if rot.exists():
                rot.unlink()
            LEDGER_FILE.rename(rot)
    except Exception:
        pass


def record(kind, **fields):
    """Append one activity event. Returns True on success, False otherwise.

    Unknown kinds and non-whitelisted fields are dropped. Never raises.
    """
    try:
        allowed = _ALLOWED_FIELDS.get(kind)
        if allowed is None:
            return False
        rec = {"kind": kind, "ts": time.time()}
        for key, val in fields.items():
            if key not in allowed or val is None:
                continue
            if isinstance(val, str) and len(val) > _TEXT_CAP:
                val = val[:_TEXT_CAP]
            rec[key] = val
        line = json.dumps(rec, default=str)
        with _LOCK:
            LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
            _maybe_rotate_locked()
            with open(LEDGER_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:
        return False


def read(limit=200, kind=None, model=None, since=None, until=None, task_id=None):
    """Return recent events, NEWEST FIRST, after filtering.

    since/until are unix timestamps (inclusive). Reads the live file plus the
    rotated `.1` file when needed to satisfy `limit`. Never raises.
    """
    lines = []
    try:
        with _LOCK:
            for path in (_rotated_path(), LEDGER_FILE):
                try:
                    if path.exists():
                        with open(path, encoding="utf-8") as f:
                            lines.extend(f.readlines())
                except Exception:
                    continue
    except Exception:
        return []

    out = []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if kind is not None and rec.get("kind") != kind:
            continue
        if model is not None and rec.get("model") != model:
            continue
        if task_id is not None and rec.get("task_id") != task_id:
            continue
        ts = rec.get("ts")
        if since is not None and (ts is None or ts < since):
            continue
        if until is not None and (ts is None or ts > until):
            continue
        out.append(rec)
        if len(out) >= max(1, int(limit)):
            break
    return out
