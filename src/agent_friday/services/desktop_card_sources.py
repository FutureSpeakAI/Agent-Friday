"""Bounded local observations for Home; no source refresh starts work or I/O online."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path

from agent_friday.core import FRIDAY_DIR

MAX_RECORDS = 200
MAX_SOURCE_BYTES = 2 * 1024 * 1024
KINDS = {
    "task": ("Tasks", "tasks"), "calendar": ("Local calendar", "calendar"),
    "project": ("Projects", "projects"), "routine": ("Routines", "workflows"),
    "schedule": ("Schedules", "settings"), "activity": ("Local activity", "tasks"),
}


def _text(value, limit=120):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value.strip() if ord(c) >= 32 or c in "\n\t\r")[:limit]


def destination(kind):
    if kind in {"task", "activity", "project"}:
        return {"view": "projects" if kind == "project" else "activity"}
    return {"workspace": KINDS[kind][1]}


def _number(value, default=None):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else default


def _json(path, *, default):
    path = Path(path)
    if not path.exists():
        return default, None
    with path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds the bounded snapshot")
    return json.loads(raw), path.stat().st_mtime


def stable_id(prefix, kind, source_id):
    digest = hashlib.sha256((kind + "\0" + source_id).encode("utf-8")).hexdigest()[:24]
    return prefix + "-" + kind + "-" + digest


def automatic_id(kind, source_id, now):
    if kind == "activity":
        return "auto-activity-" + datetime.fromtimestamp(now).date().isoformat()
    return stable_id("auto", kind, source_id)


def _record(source_id, title, body, *, updated=None, priority=50, expires=None, **extra):
    if not isinstance(source_id, str) or not source_id or len(source_id) > 240 or any(ord(c) < 32 for c in source_id):
        raise ValueError("source record needs a bounded stable id")
    return {"id": source_id, "title": _text(title) or "Untitled item", "body": _text(body, 2000),
            "updated_at": _number(updated), "priority": priority, "expires_at": _number(expires), **extra}


def _tasks(now):
    from agent_friday.services.agent import _task_snapshot
    from agent_friday.services import task_journal
    rows = _task_snapshot()
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid task snapshot")
    rows = sorted(rows, key=lambda item: _number(item.get("created"), 0), reverse=True)[:MAX_RECORDS]
    records = []
    for row in rows:
        if row.get("off_record"):
            continue
        task_id = row.get("task_id")
        if not task_id:
            continue
        status = _text(row.get("status") or "unknown", 40)
        seen = _number(row.get("last_seen"))
        if status in {"running", "queued"} and seen is not None and now - seen > task_journal.STALLED_AFTER_S:
            status = "stalled"
        progress = row.get("progress")
        body = "Reported status: " + status + "."
        if _number(progress) is not None and progress <= 100:
            body += " Reported progress: " + str(round(progress)) + "%."
        ended = _number(row.get("ended"))
        records.append(_record(task_id, row.get("name") or "Task", body,
            updated=ended or seen or row.get("started") or row.get("created"),
            priority=90 if status in {"failed", "stalled", "interrupted"} else 80,
            expires=ended + 86400 if ended else None, state=status))
    return records, max((row["updated_at"] or 0 for row in records), default=None), "Reported task state from Friday's local registry; this is not an independent heartbeat check."


def _calendar(now):
    from agent_friday.services import swr_cache
    day = datetime.fromtimestamp(now).date()
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    rows, stamp = _json(Path(FRIDAY_DIR) / "calendar" / "local_events.json", default=[])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid local calendar")
    entries = [("local:", row, stamp, False) for row in rows[:MAX_RECORDS]]
    cached = swr_cache.peek("calendar.range:%s/%s" % (start.isoformat(), end.isoformat()))
    if cached:
        cached_rows, cached_at = cached
        if not isinstance(cached_rows, list) or any(not isinstance(row, dict) for row in cached_rows):
            raise ValueError("invalid calendar cache")
        entries.extend(("cached:", row, cached_at, now - cached_at > 300) for row in cached_rows[:MAX_RECORDS])
    records = []
    for prefix, row, updated, stale in entries:
        if row.get("off_record"):
            continue
        raw_start = row.get("start_time")
        if not raw_start or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("calendar record has no identity or start")
        try:
            at = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
            until = datetime.fromisoformat(str(row.get("end_time") or raw_start).replace("Z", "+00:00"))
            finish = until.timestamp()
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("calendar record has an invalid date") from exc
        body = ("All day: " if row.get("all_day") else "Starts: ") + _text(raw_start, 80)
        records.append(_record(prefix + str(row["id"]), row.get("title") or "Calendar event", body,
            updated=updated, priority=75, expires=finish if finish > at.timestamp() else at.timestamp() + 3600,
            starts_at=at.timestamp(), state="stale" if stale else "ready"))
    return records, max([value for value in [stamp, cached[1] if cached else None] if value is not None], default=None), (
        "Saved local events and an existing Calendar cache; Home never contacts a calendar provider."
        if cached else "Saved local events only. Open Calendar to refresh connected calendars.")


def _projects(now):
    root = Path(FRIDAY_DIR) / "projects"
    if not root.exists():
        return [], None, "No saved local projects are available."
    records = []
    total_bytes = 0
    # Refuse an oversized catalog instead of claiming an arbitrary partial
    # directory sample is the complete set of selectable projects.
    directories = list(islice(root.iterdir(), MAX_RECORDS + 1))
    if len(directories) > MAX_RECORDS:
        raise ValueError("project catalog exceeds the bounded snapshot")
    for directory in sorted(directories):
        if not directory.is_dir() or directory.name.startswith("_") or directory.is_symlink():
            continue
        metadata = directory / "project.json"
        if metadata.is_symlink():
            raise ValueError("linked project metadata is not a local snapshot")
        if metadata.exists():
            total_bytes += metadata.stat().st_size
            if total_bytes > 8 * MAX_SOURCE_BYTES:
                raise ValueError("project metadata exceeds the bounded snapshot")
        row, stamp = _json(metadata, default=None)
        if row is None:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("invalid project record")
        if row.get("archived") or row.get("off_record"):
            continue
        files, codebases = row.get("files") or [], row.get("codebases") or []
        if not isinstance(files, list) or not isinstance(codebases, list):
            raise ValueError("invalid project metadata")
        records.append(_record(str(row["id"]), row.get("name") or "Project",
            f"{len(files)} saved files · {len(codebases)} connected codebases. Open Projects to continue.",
            updated=row.get("updated_at") or stamp, priority=45))
    return records, max((r["updated_at"] or 0 for r in records), default=None), "Saved project metadata; no migration, repository scan or file content is read."


def _schedules(now):
    from agent_friday.services import scheduler
    rows, stamp = _json(scheduler.SCHEDULES_FILE, default=[])
    if isinstance(rows, dict):
        rows = rows.get("schedules")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid schedule store")
    records = []
    for row in rows[:MAX_RECORDS]:
        if row.get("off_record"):
            continue
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("schedule record has no identity")
        enabled = bool(row.get("enabled", True))
        state = _text(row.get("last_status") or "not run", 40)
        local_now = datetime.fromtimestamp(now, scheduler._now_central().tzinfo)
        next_run = _number(scheduler._next_run_ts(row, local_now))
        body = ("Enabled" if enabled else "Disabled") + "; last result: " + state + "."
        if next_run:
            body += " Next scheduled: " + datetime.fromtimestamp(next_run).astimezone().isoformat(timespec="minutes") + "."
        records.append(_record(str(row["id"]), row.get("name") or "Schedule", body,
            updated=row.get("last_run_ts") or row.get("updated") or stamp,
            priority=70 if state in {"failed", "error", "waiting", "missed"} else 40,
            state=state, next_run=next_run, enabled=enabled))
    return records, stamp, "Saved schedule and last-run state. Tracking never changes or starts a schedule."


def _routines(now):
    from agent_friday.services import misc_engine
    from agent_friday.routes.workflows import ROUTINE_TASKS
    statuses, stamp = _json(misc_engine.ROUTINE_STATUS_FILE, default={})
    if not isinstance(statuses, dict):
        raise ValueError("invalid routine status")
    records = []
    for item in misc_engine.ROUTINE_REGISTRY[:MAX_RECORDS]:
        row = statuses.get(item["id"], {})
        if not isinstance(row, dict):
            raise ValueError("invalid routine result")
        available = item["id"] in ROUTINE_TASKS
        state = _text(row.get("last_status") or "not run", 40) if available else "unimplemented"
        body = "Last recorded result: " + state + "." if available else "This routine has no executable handler in this build."
        last_run = row.get("last_run")
        if last_run:
            body += " Last run: " + _text(last_run, 80) + "."
        records.append(_record(item["id"], item.get("label"), body, updated=stamp,
            priority=65 if state in {"failed", "error"} else 35, state=state, available=available))
    return records, stamp, "The actual routine registry and recorded outcomes; templates alone do not prove a runnable routine."


def _activity(now):
    from agent_friday.services import activity_ledger
    path = Path(activity_ledger.LEDGER_FILE)
    if not path.exists():
        return [], None, "No local activity has been recorded."
    # Read only a bounded tail, discarding the first partial line. No prompt,
    # tool arguments, task descriptions or result bodies enter the card.
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - 256 * 1024))
        raw = stream.read(256 * 1024)
    lines = raw.splitlines()
    if size > len(raw):
        lines = lines[1:]
    day = datetime.fromtimestamp(now).date()
    since = datetime.combine(day, datetime.min.time()).timestamp()
    counts = {"model_invocation": 0, "tool_call": 0, "subagent_spawn": 0}
    latest = None
    for line in lines:
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("invalid activity record")
        stamp = _number(row.get("ts"))
        if row.get("off_record") or stamp is None or not since <= stamp <= now:
            continue
        if row.get("kind") in counts:
            counts[row["kind"]] += 1
            latest = max(latest or 0, stamp)
    detail = "Metadata from the latest 256 KiB of local activity; counts can be partial on busy days."
    if latest is None:
        return [], path.stat().st_mtime, detail
    body = f"{counts['tool_call']} tool calls · {counts['model_invocation']} model calls · {counts['subagent_spawn']} tasks started in today's recent local activity."
    expiry = (datetime.combine(day, datetime.min.time()) + timedelta(days=1)).timestamp()
    return [_record("today", "Today's activity", body, updated=latest, priority=30, expires=expiry)], latest, detail


_READERS = {"task": _tasks, "calendar": _calendar, "project": _projects,
            "routine": _routines, "schedule": _schedules, "activity": _activity}


def snapshot(now):
    """Return exact selectable local records and their honest source status."""
    result = {}
    for kind, (label, _workspace) in KINDS.items():
        info = {"kind": kind, "label": label, **destination(kind), "checked_at": now,
                "updated_at": None, "status": "unavailable", "detail": "This local source could not be read.",
                "options": [], "records": {}}
        try:
            records, stamp, detail = _READERS[kind](now)
            info.update(status="ready" if records else "empty", detail=detail, updated_at=stamp)
            for record in records[:MAX_RECORDS]:
                if record["id"] in info["records"]:
                    raise ValueError("duplicate source id")
                info["records"][record["id"]] = record
                info["options"].append({"id": record["id"], "label": record["title"],
                    "status": record.get("state", "ready"), "available": record.get("available", True)})
        except Exception:
            info.update(status="unavailable", detail="This local source could not be read. Existing tracking is retained.", options=[], records={})
        result[kind] = info
    return result


def card_for(kind, source_id, observations, *, prefix="auto", now):
    info = observations.get(kind)
    row = info and info["records"].get(source_id)
    if row is None:
        return None
    source = {key: info[key] for key in ("kind", "label", "checked_at", "updated_at", "detail")}
    target = destination(kind)
    source.update(target)
    source.update(id=source_id, status=row.get("state") if row.get("state") in {"stale", "unimplemented"} else "ready",
                  updated_at=row["updated_at"])
    return {"id": automatic_id(kind, source_id, now) if prefix == "auto" else stable_id(prefix, kind, source_id), "title": row["title"], "body": row["body"],
            "type": kind, "origin": "tracked" if prefix == "track" else "automatic", "priority": row["priority"],
            "actions": [{"label": "Open " + ("Activity" if kind in {"task", "activity"} else KINDS[kind][0]), **target}],
            "source": source, "created_at": row["updated_at"] or now, "updated_at": row["updated_at"],
            "expires_at": row["expires_at"]}


def automatic(observations, now):
    """Small, deterministic workday candidates derived from actual observations."""
    cards = []
    for kind, maximum in (("task", 3), ("calendar", 2), ("schedule", 2), ("routine", 1), ("project", 1), ("activity", 1)):
        rows = list(observations[kind]["records"].values())
        if kind == "calendar":
            rows = [r for r in rows if (r.get("expires_at") or 0) > now and r.get("starts_at", now) <= now + 86400]
            rows.sort(key=lambda r: r.get("starts_at", now))
        elif kind == "task":
            rows = [r for r in rows if r.get("state") in {"running", "queued", "failed", "stalled", "interrupted"}]
            rows.sort(key=lambda r: (-r["priority"], -(r["updated_at"] or 0), r["id"]))
        elif kind == "schedule":
            rows = [r for r in rows if r.get("enabled") and (r.get("state") in {"failed", "error", "waiting", "missed"}
                    or now <= (r.get("next_run") or 0) <= now + 86400)]
            rows.sort(key=lambda r: (-r["priority"], r.get("next_run") or float("inf"), r["id"]))
        elif kind == "routine":
            rows = [r for r in rows if r.get("available") and r.get("state") in {"failed", "error"}]
        elif kind == "project":
            rows = [r for r in rows if (r["updated_at"] or 0) >= now - 7 * 86400]
            rows.sort(key=lambda r: -(r["updated_at"] or 0))
        for row in rows[:maximum]:
            card = card_for(kind, row["id"], observations, now=now)
            cards.append(card)
    return cards
