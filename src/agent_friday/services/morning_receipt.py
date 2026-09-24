"""The morning receipt: what Friday did in a window of time, from the record.

Read-only. Two sources, both written as the work happened:

  * the governance checkpoint's signed receipts (``decision-bom.jsonl``, one
    line per decision, written by ``governance.action_gate``), each checked
    against its HMAC here and reported as verified or not -- a line that was
    edited, or that carries no signature, says so;
  * the task journal's index (``tasks/index.jsonl``) for background tasks
    that finished in the window.

Nothing here writes, and nothing is summarised by a model: the page shows the
record, so it cannot describe work that did not happen.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

#: Most receipts one response carries. The file holds every decision,
#: including every read; a day can be long.
MAX_ENTRIES = 1000


def _ts(value) -> Optional[float]:
    """Epoch seconds for a receipt timestamp (ISO-8601, UTC, trailing Z)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1]
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _task_index() -> dict:
    try:
        from agent_friday.services import task_journal as tj
        return tj.index_read() or {}
    except Exception:
        return {}


def build(since: float, until: Optional[float] = None) -> dict[str, Any]:
    """Every checkpoint decision and every task completion in [since, until]."""
    from agent_friday.governance import action_gate

    until = float(until if until is not None else time.time())
    since = float(since)
    try:
        key = action_gate._governance_key()
        key_ok = True
    except Exception:
        key, key_ok = None, False

    tasks = _task_index()
    actions: list[dict] = []
    unreadable = 0
    counts = {"allow": 0, "confirm": 0, "card": 0, "deny": 0}
    verified_n = unverified_n = 0
    path = action_gate.receipts_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    except Exception:
        lines = []
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            unreadable += 1
            continue
        if not isinstance(entry, dict):
            unreadable += 1
            continue
        at = _ts(entry.get("timestamp"))
        if at is None or at < since or at > until:
            continue
        ok = bool(key_ok and action_gate.verify_receipt(entry, key))
        if ok:
            verified_n += 1
        else:
            unverified_n += 1
        decision = str(entry.get("decision") or "")
        if decision in counts:
            counts[decision] += 1
        tid = entry.get("task_id")
        trow = tasks.get(tid) if tid else None
        actions.append({
            "line": n,
            "at": at,
            "tool": entry.get("tool"),
            "decision": decision,
            "class": entry.get("class"),
            "surface": entry.get("surface"),
            "reason": entry.get("reason"),
            "grant": entry.get("grant"),
            "approval": entry.get("approval"),
            "task_id": tid,
            "task_name": (trow or {}).get("name"),
            "verified": ok,
        })
    actions.sort(key=lambda a: a["at"], reverse=True)
    truncated = len(actions) > MAX_ENTRIES

    finished = []
    for tid, row in tasks.items():
        ended = row.get("ended")
        try:
            ended = float(ended) if ended is not None else None
        except (TypeError, ValueError):
            ended = None
        if ended is None or ended < since or ended > until:
            continue
        if str(row.get("status") or "") == "deleted":
            continue
        finished.append({"task_id": tid, "name": row.get("name"),
                         "status": row.get("status"), "ended": ended,
                         "created": row.get("created")})
    finished.sort(key=lambda t: t["ended"], reverse=True)

    return {
        "since": since,
        "until": until,
        "actions": actions[:MAX_ENTRIES],
        "truncated": truncated,
        "counts": counts,
        "verified": verified_n,
        "not_verified": unverified_n,
        "unreadable_lines": unreadable,
        "key_available": key_ok,
        "tasks": finished,
    }
