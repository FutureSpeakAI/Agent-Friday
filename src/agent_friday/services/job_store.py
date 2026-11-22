"""The job store: one record per posting, with a status, for the Career workspace's panels.

Scanners (the bundled job_scanner today, the job-hunt skill after it) WRITE postings here with `upsert`; the
panel READS `list_recent` and `list_archive`. Neither side needs the other's internals.

* a posting has one stable key (`make_key`): its source's id when it has one, else company, title and location
  as words, else its URL; a repost or a second feed is the same record;
* a posting is open until its closing date passes or it has not been seen for STALE_DAYS; the sweep then moves
  it to the archive (status closed or expired) with the date it closed, and never deletes it;
* a posting that expired and is seen again is open again; one the owner closed, or whose closing date passed,
  stays closed; an applied posting is never swept;
* "recent" is open or applied postings seen within STALE_DAYS, newest sighting first (never by score);
* the file is one JSON document under the Friday home, written atomically; a damaged file is an error and is
  never replaced by an empty store.

tests/unit/test_job_store.py holds each rule.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

FILE_NAME = "job_store.json"
STALE_DAYS = 30
OPEN, CLOSED, EXPIRED, APPLIED = "open", "closed", "expired", "applied"
PUBLIC = ("key", "job_id", "title", "company", "location", "url", "source", "status", "first_seen", "last_seen", "posted_at",
          "closes_at", "closed_at", "close_reason", "score")
STALE = timedelta(days=STALE_DAYS)
DEFAULT_LIMIT = 50
_LOCK = threading.Lock()


class StoreUnreadable(OSError):
    """job_store.json exists but cannot be read as the store; it is never replaced."""


def path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / FILE_NAME


# -- keys -------------------------------------------------------------------------------------

def _words(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def make_key(listing: dict) -> str:
    """The stable key of a posting. Raises ValueError when the listing names nothing to key on."""
    source, external = _words(listing.get("source")), str(listing.get("external_id") or "").strip().lower()
    company, title, location = _words(listing.get("company")), _words(listing.get("title")), _words(listing.get("location"))
    url = str(listing.get("url") or listing.get("source_url") or "").strip().lower()
    if source and external:
        basis = "id|%s|%s" % (source, external)
    elif company and title:
        basis = "ct|%s|%s|%s" % (company, title, location)
    elif url:
        basis = "u|%s" % url
    else:
        raise ValueError("a listing needs a source id, a company and title, or a URL")
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


# -- time -------------------------------------------------------------------------------------

def _moment(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _iso(value) -> Optional[str]:
    moment = _moment(value)
    return moment.isoformat() if moment else None


# -- the file ---------------------------------------------------------------------------------

def _load() -> dict:
    try:
        raw = path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"v": 1, "jobs": {}}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise StoreUnreadable("job_store.json could not be read") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise StoreUnreadable("job_store.json is not a store")
    return data


def _save(data: dict) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".job-store-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(data, indent=1, ensure_ascii=False))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


# -- the rules ---------------------------------------------------------------------------------

def _sweep(data: dict, now: datetime) -> bool:
    """Close what is past its closing date and expire what has not been seen for STALE_DAYS. True when it changed."""
    changed = False
    for record in data["jobs"].values():
        if record["status"] != OPEN:
            continue
        closes, seen = _moment(record.get("closes_at")), _moment(record.get("last_seen"))
        if closes is not None and closes <= now:
            record.update(status=CLOSED, closed_at=closes.isoformat(), close_reason="closing date passed")
            changed = True
        elif seen is not None and now - seen > STALE:
            record.update(status=EXPIRED, closed_at=(seen + STALE).isoformat(),
                          close_reason="not seen for %d days" % STALE_DAYS)
            changed = True
    return changed


def _public(record: dict) -> dict:
    return {name: record.get(name) for name in PUBLIC}


def upsert(listing: dict, now: datetime) -> dict:
    """Record one sighting of a posting. A new posting is open; a known one is refreshed (and an expired one
    reopens); one that was closed stays closed. Returns the record."""
    key = make_key(listing)
    with _LOCK:
        data = _load()
        record = data["jobs"].get(key)
        seen = now.isoformat()
        if record is None:
            record = {"key": key, "status": OPEN, "first_seen": seen, "closed_at": None, "close_reason": None}
            data["jobs"][key] = record
        record["last_seen"] = seen
        for name, value in (("title", listing.get("title")), ("company", listing.get("company")),
                            ("location", listing.get("location")), ("source", listing.get("source")),
                            ("url", listing.get("url") or listing.get("source_url")),
                            ("job_id", listing.get("job_id")),
                            ("posted_at", _iso(listing.get("posted_at"))),
                            ("closes_at", _iso(listing.get("closes_at")))):
            if value:
                record[name] = value
        score = listing.get("score", listing.get("relevance_score"))
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            record["score"] = float(score)
        if record["status"] == EXPIRED:
            record.update(status=OPEN, closed_at=None, close_reason=None)
        _save(data)
        return _public(record)


def close(key: str, now: datetime, reason: str) -> None:
    """Close a posting on purpose: it stays closed when a feed still lists it."""
    with _LOCK:
        data = _load()
        record = data["jobs"].get(key)
        if record is None:
            raise KeyError(key)
        record.update(status=CLOSED, closed_at=now.isoformat(), close_reason=str(reason or "closed")[:200])
        _save(data)


def mark_applied(key: str, now: datetime) -> None:
    with _LOCK:
        data = _load()
        record = data["jobs"].get(key)
        if record is None:
            raise KeyError(key)
        record.update(status=APPLIED, applied_at=now.isoformat(), closed_at=None, close_reason=None)
        _save(data)


def _swept(now: datetime) -> dict:
    with _LOCK:
        data = _load()
        if _sweep(data, now):
            _save(data)
        return data


def _is_recent(record: dict, now: datetime) -> bool:
    seen = _moment(record.get("last_seen"))
    return record["status"] in (OPEN, APPLIED) and seen is not None and now - seen <= STALE


def list_recent(now: datetime, limit: int = DEFAULT_LIMIT) -> list:
    """Open or applied postings seen within STALE_DAYS, newest sighting first."""
    rows = [r for r in _swept(now)["jobs"].values() if _is_recent(r, now)]
    rows.sort(key=lambda r: (r.get("last_seen") or "", r.get("first_seen") or "", r.get("title") or ""), reverse=True)
    return [_public(r) for r in rows[:max(0, int(limit))]]


def list_archive(now: datetime, limit: int = DEFAULT_LIMIT) -> list:
    """Closed and expired postings (and applied ones no longer seen), newest closing first."""
    rows = [r for r in _swept(now)["jobs"].values() if not _is_recent(r, now)]
    rows.sort(key=lambda r: (r.get("closed_at") or r.get("last_seen") or ""), reverse=True)
    return [_public(r) for r in rows[:max(0, int(limit))]]


# -- the old tracker --------------------------------------------------------------------------------

def _tracker_rows() -> list:
    """The listings the bundled scanner's tracker holds (data only; its internals stay its own)."""
    from agent_friday.seed.data.job_tracker_schema import JobTracker
    return [job.to_dict() for job in JobTracker().load().list_jobs(limit=100000)]


def import_tracker(now: datetime) -> int:
    """Bring the old tracker's listings in once, judged by the same rules (a July posting nobody has seen since
    lands in the archive). Returns how many were imported; 0 on every later call."""
    with _LOCK:
        data = _load()
        if data.get("tracker_imported"):
            return 0
        added = 0
        for row in _tracker_rows():
            try:
                key = make_key(row)
            except ValueError:
                continue
            if key in data["jobs"]:
                continue
            seen = _iso(row.get("discovered_at")) or now.isoformat()
            data["jobs"][key] = {"key": key, "status": OPEN, "first_seen": seen, "last_seen": seen,
                                 "closed_at": None, "close_reason": None, "job_id": row.get("job_id"), "title": row.get("title"),
                                 "company": row.get("company"), "location": row.get("location"),
                                 "source": row.get("source"), "url": row.get("source_url") or row.get("url"),
                                 "posted_at": _iso(row.get("posted_at")), "closes_at": None,
                                 "score": row.get("relevance_score")}
            added += 1
        data["tracker_imported"] = True
        _save(data)
        return added
