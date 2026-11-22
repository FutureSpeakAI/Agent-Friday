"""The owner's edits to Friday's built-in scheduled jobs: versioned, logged, reversible, and kept across updates.

A built-in job (the morning edition, the afternoon briefing, the hourly sync ...) is seeded with a default timetable. The
owner may change its schedule, switch it on or off, and change its parameters (the hours, the interval, the idle wait).
Each such edit is a version in this file: what the fields were before, what they became, who asked (the owner, or a model
with the owner's yes), and when. The owner can step back one edit (``revert``) or put the job back as shipped
(``restore``). An app update never overwrites an edit: ``reapply`` puts the owner's fields back after any seeding or
migration, and ``adopt`` treats a built-in whose fields already differ from its shipped defaults as edited (an edit made
before this file existed is still the owner's).

A pure store: it takes the record's fields and the shipped defaults as arguments and returns the fields to write; the
scheduler does the writing (services/scheduler.py). Guarded by tests/unit/test_builtin_overrides.py.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: The fields an owner edit may change: the schedule, the enabled state, and the parameters.
EDITABLE = ("trigger", "spec", "enabled", "timeout_seconds", "retry", "notify")
#: What the job does: its prompt or action. A model's change here needs the owner's approval card.
ACTION_FIELDS = ("task",)

_LOCK = threading.Lock()
MAX_HISTORY = 50


def _file() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return Path(FRIDAY_DIR) / "builtin_schedule_overrides.json"


def fields_of(record: Dict[str, Any]) -> Dict[str, Any]:
    """The editable fields of a schedule record, copied."""
    return {k: copy.deepcopy(record[k]) for k in EDITABLE if k in record}


def _load(path: Optional[Path] = None) -> Dict[str, Any]:
    try:
        data = json.loads((path or _file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"jobs": {}}
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        return {"jobs": {}}
    return data


def _save(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = path or _file()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    tmp.replace(target)


def _job(data: Dict[str, Any], sid: str) -> Dict[str, Any]:
    return data["jobs"].setdefault(sid, {"history": [], "owner_fields": {}})


def _differs(fields: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """The fields that are not the shipped default."""
    return {k: copy.deepcopy(v) for k, v in fields.items() if defaults.get(k) != v}


def history(sid: str, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The versions of a job's edits, oldest first."""
    return copy.deepcopy(_load(path)["jobs"].get(sid, {}).get("history", []))


def owner_fields(sid: str, path: Optional[Path] = None) -> Dict[str, Any]:
    """The fields the owner has set on this job (those that differ from the shipped default)."""
    return copy.deepcopy(_load(path)["jobs"].get(sid, {}).get("owner_fields", {}))


def has_edits(sid: str, path: Optional[Path] = None) -> bool:
    return bool(owner_fields(sid, path))


def _append(job: Dict[str, Any], kind: str, by: str, before: Dict[str, Any], after: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    entry = {"version": (job["history"][-1]["version"] + 1) if job["history"] else 1, "at": time.time(), "kind": kind,
             "by": by, "before": before, "after": after, "note": note}
    job["history"].append(entry)
    del job["history"][:-MAX_HISTORY]
    return entry


def record_edit(sid: str, before: Dict[str, Any], after: Dict[str, Any], defaults: Dict[str, Any], by: str,
                path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Log one edit (before and after are the editable fields) as the next version. None when nothing changed."""
    before, after = fields_of(before), fields_of(after)
    if before == after:
        return None
    with _LOCK:
        data = _load(path)
        job = _job(data, sid)
        entry = _append(job, "edit", by, before, after)
        job["owner_fields"] = _differs(after, defaults)
        _save(data, path)
    return entry


def restore(sid: str, current: Dict[str, Any], defaults: Dict[str, Any], by: str,
            path: Optional[Path] = None) -> Dict[str, Any]:
    """Put the job back as shipped: logs a version and forgets the owner's fields. Returns the default fields to write."""
    with _LOCK:
        data = _load(path)
        job = _job(data, sid)
        shipped = fields_of(defaults)
        if fields_of(current) != shipped or job["owner_fields"]:
            _append(job, "restore_default", by, fields_of(current), shipped)
        job["owner_fields"] = {}
        _save(data, path)
    return shipped


def revert(sid: str, current: Dict[str, Any], defaults: Dict[str, Any], by: str,
           path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Step back the newest version that has not been stepped back (an edit, an adoption or a restore-to-default): the
    fields from before it, logged as a new version. None when there is nothing to step back to."""
    with _LOCK:
        data = _load(path)
        job = _job(data, sid)
        steps = [e for e in job["history"] if e["kind"] in ("edit", "adopted", "restore_default") and not e.get("undone")]
        if not steps:
            return None
        newest = steps[-1]
        newest["undone"] = True                  # each version is stepped back once; a revert is not itself stepped back
        target = copy.deepcopy(newest["before"])
        _append(job, "revert", by, fields_of(current), target, note="steps back version %d" % newest["version"])
        job["owner_fields"] = _differs(target, defaults)
        _save(data, path)
    return target


def adopt(sid: str, current: Dict[str, Any], defaults: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """A built-in that already differs from its shipped defaults and has no history was edited before this record existed:
    it is the owner's. Logged once as version 1 ("adopted")."""
    now = fields_of(current)
    if not _differs(now, defaults):
        return False
    with _LOCK:
        data = _load(path)
        job = _job(data, sid)
        if job["history"] or job["owner_fields"]:
            return False
        _append(job, "adopted", "owner", fields_of(defaults), now,
                note="an edit made before edits were versioned")
        job["owner_fields"] = _differs(now, defaults)
        _save(data, path)
    return True


def reapply(records: List[Dict[str, Any]], path: Optional[Path] = None) -> int:
    """Write the owner's fields back onto the built-in records (in place), whatever a seed or a migration just wrote.
    Returns how many records changed."""
    data = _load(path)
    changed = 0
    for record in records:
        if record.get("source") != "builtin":
            continue
        mine = (data["jobs"].get(record.get("id")) or {}).get("owner_fields") or {}
        touched = False
        for key, value in mine.items():
            if key in EDITABLE and record.get(key) != value:
                record[key] = copy.deepcopy(value)
                touched = True
        changed += touched
    return changed
