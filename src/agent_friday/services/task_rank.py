"""Which open tasks the daily rundown puts first, decided in code.

The owner marks a task they must not lose by starting its title with ``PRIORITY``. Such a task outranks every other
open task and climbs the longer it stays open, so it cannot sit at the bottom of a list for weeks. The rule is here and
not in a prompt: ``rank`` orders the tasks, ``section`` writes the briefing's Tasks section from that order, and the
model only phrases what it is given.

Google Tasks does not say when a task was made, so the first day this module saw a task open is kept
(``first_seen.json`` under the data folder) and the age is counted from the earlier of that day and the task's own
``updated`` time. Each rule has its test: tests/unit/test_task_rank.py.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: ``PRIORITY: x``, ``priority - x``, ``[PRIORITY] x``, ``(Priority) x``, ``PRIORITY! x``: the word first, then a
#: separator or a closing bracket. ``Priority review of ...`` is a title, not a label.
_LABEL = re.compile(r"^\s*[\[(]?\s*priority\s*[\])]?\s*(?::|!|-|–|—|\]|\))\s*", re.IGNORECASE)

#: Every priority task scores above this; every other task scores below it.
PRIORITY_FLOOR = 1000.0
#: An overdue ordinary task gains a point a day up to this many days, so it never reaches the floor.
OVERDUE_CAP_DAYS = 365

_LOCK = threading.Lock()


def is_priority(title: Any) -> bool:
    """The owner's label: the title starts with PRIORITY and a separator."""
    return bool(_LABEL.match(str(title or "")))


def clean_title(title: Any) -> str:
    """The title without the label, for the line the briefing shows."""
    text = str(title or "")
    return _LABEL.sub("", text, count=1).strip() or text.strip()


def _parse(stamp: Any) -> Optional[datetime]:
    text = str(stamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_days(task: Dict[str, Any], first_seen: Dict[str, str], now: datetime) -> int:
    """Whole days the task has been open: from the earlier of the day it was first seen and its ``updated`` time."""
    starts = [d for d in (_parse(first_seen.get(str(task.get("id") or ""))), _parse(task.get("updated"))) if d]
    if not starts:
        return 0
    return max(0, (now - min(starts)).days)


def overdue_days(task: Dict[str, Any], now: datetime) -> int:
    due = _parse(task.get("due"))
    return max(0, (now - due).days) if due else 0


def score(task: Dict[str, Any], first_seen: Dict[str, str], now: datetime) -> float:
    """A priority task scores ``PRIORITY_FLOOR`` plus its days open (always above any other task, and higher each day);
    any other task scores by how long it is overdue, below the floor."""
    if is_priority(task.get("title")):
        return PRIORITY_FLOOR + age_days(task, first_seen, now)
    return float(min(overdue_days(task, now), OVERDUE_CAP_DAYS))


def rank(tasks: Iterable[Dict[str, Any]], first_seen: Dict[str, str], now: datetime) -> List[Dict[str, Any]]:
    """The open tasks, highest first; equal scores keep a stable, readable order (earliest due, then title)."""
    rows = [t for t in tasks if isinstance(t, dict) and t.get("title") is not None and not t.get("more_available")]
    return sorted(rows, key=lambda t: (-score(t, first_seen, now), str(t.get("due") or "9999"), str(t.get("title"))))


def section(tasks: Iterable[Dict[str, Any]], first_seen: Dict[str, str], now: datetime, *, limit: int = 15) -> str:
    """The briefing's Tasks section, ranked. Priority tasks say how many days they have been open."""
    ordered = rank(tasks, first_seen, now)
    if not ordered:
        return "## Open Tasks\n(No open tasks.)"
    lines = ["## Open Tasks (ranked: the owner's PRIORITY tasks first, the longest-open first)"]
    for task in ordered[:limit]:
        if is_priority(task.get("title")):
            days = age_days(task, first_seen, now)
            lines.append("- PRIORITY (open %d day%s): %s" % (days, "" if days == 1 else "s", clean_title(task.get("title"))))
        else:
            due = str(task.get("due") or "")[:10]
            lines.append("- %s%s" % (str(task.get("title")), (" (due %s)" % due) if due else ""))
    if len(ordered) > limit:
        lines.append("(%d more open tasks not shown.)" % (len(ordered) - limit))
    return "\n".join(lines)


# ── the first-seen record ────────────────────────────────────────────────────

def _file() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "task_first_seen.json"


def _read(path: Path) -> Dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def note_seen(tasks: Iterable[Dict[str, Any]], now: datetime, path: Optional[Path] = None, *,
              complete: bool = True) -> Dict[str, str]:
    """Record today as the first day each open task was seen and return the record. When the read was ``complete``,
    tasks no longer open are forgotten; a partial read (an account that failed, more tasks than were read) forgets
    nothing, so a task's age is never reset by a bad fetch. Never raises: a record that cannot be written leaves
    ages to the tasks' own ``updated`` times."""
    target = path or _file()
    ids = [str(t.get("id")) for t in tasks if isinstance(t, dict) and t.get("id")]
    with _LOCK:
        known = _read(target)
        stamp = now.astimezone(timezone.utc).isoformat()
        kept = {i: known.get(i, stamp) for i in ids} if complete else {**known, **{i: known.get(i, stamp) for i in ids}}
        if kept != known:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".tmp")
                tmp.write_text(json.dumps(kept, indent=1), encoding="utf-8")
                tmp.replace(target)
            except OSError:
                pass
        return kept
