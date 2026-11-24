"""The job store: one record per posting, with a status, for the Career workspace's panels.

Scanners (the bundled job_scanner today, the job-hunt skill after it) WRITE sightings here with `upsert_many`;
the panel READS `list_recent` and `list_archive`. Neither side needs the other's internals.

* a posting is one record however it arrives: `make_keys` gives every key a listing can be known by (its feed's
  id, its company, title and location as words, its URL without tracking parameters or fragment); a sighting that
  shares its feed id or its company, title and location with a record is that record, and one that shares only the
  URL is that record unless it names another company, title or place (_contradicts: a careers page lists several
  postings on one URL); a second feed with its own id, the same feed with and without an id, and a URL with utm_
  parameters all land on one record;
* a posting is open until its closing date passes (a date without a time closes at the end of that day) or it has
  not been seen for STALE_DAYS; the sweep then moves it to the archive (status closed or expired) with the date it
  closed, and never deletes it; an expired posting that is seen again is open again, a closed one stays closed;
* "applied" is not stored: a posting whose tracker job (job_tracker_schema.JobTracker) has an active
  ApplicationRecord shows as applied, sits in recent, and is never swept while that application is active;
* "recent" is applied postings and open postings seen within STALE_DAYS, newest sighting first (never by score);
* the file is one JSON document under the Friday home, written atomically once per `upsert_many` call; a damaged
  file is an error and is never replaced by an empty store.

tests/unit/test_job_store.py holds each rule; tests/api/test_career_jobs_route.py holds them through a real scan.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

FILE_NAME = "job_store.json"
STALE_DAYS = 30
STALE = timedelta(days=STALE_DAYS)
DEFAULT_LIMIT = 50
PUBLIC = ("key", "job_id", "title", "company", "location", "remote", "url", "source", "status", "first_seen",
          "last_seen", "posted_at", "closes_at", "closed_at", "close_reason", "score")
TRACKING_PARAMS = frozenset({"fbclid", "gclid", "msclkid", "ref", "ref_src", "referrer", "trk", "trackingid"})
_LOCK = threading.Lock()


class Status(str, Enum):
    """OPEN, CLOSED and EXPIRED are stored; APPLIED is derived from the tracker's applications."""
    OPEN = "open"
    CLOSED = "closed"
    EXPIRED = "expired"
    APPLIED = "applied"


class StoreUnreadable(OSError):
    """job_store.json exists but cannot be read as the store; it is never replaced."""


def path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / FILE_NAME


# -- keys -------------------------------------------------------------------------------------

def _words(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _is_tracking(param: str) -> bool:
    name = param.lower()
    return name.startswith("utm_") or name in TRACKING_PARAMS


def normalize_url(url) -> str:
    """The URL without tracking parameters, fragment or trailing slash; scheme and host lower-cased."""
    text = str(url or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    query = sorted((name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
                   if not _is_tracking(name))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def _hash(basis: str) -> str:
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def make_keys(listing: dict) -> List[str]:
    """Every key the posting can be known by, strongest first: feed id, company+title+location, URL.
    Raises ValueError when the listing names nothing to key on."""
    source, external = _words(listing.get("source")), str(listing.get("external_id") or "").strip().lower()
    company, title, location = _words(listing.get("company")), _words(listing.get("title")), _words(listing.get("location"))
    url = normalize_url(listing.get("url") or listing.get("source_url"))
    bases = []
    if source and external:
        bases.append("id|%s|%s" % (source, external))
    if company and title:
        bases.append("ct|%s|%s|%s" % (company, title, location))
    if url:
        bases.append("u|%s" % url)
    if not bases:
        raise ValueError("a listing needs a source id, a company and title, or a URL")
    return [_hash(basis) for basis in bases]


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


def _closing_iso(value) -> Optional[str]:
    """A closing date; one given without a time closes at the end of that local day."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time(23, 59, 59)).isoformat()
    if isinstance(value, str) and len(value.strip()) == 10:
        try:
            return datetime.combine(date.fromisoformat(value.strip()), time(23, 59, 59)).isoformat()
        except ValueError:
            return None
    return _iso(value)


# -- the file ---------------------------------------------------------------------------------

def _load() -> dict:
    try:
        raw = path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"v": 1, "jobs": {}, "aliases": {}}
    except (OSError, UnicodeDecodeError) as exc:       # locked, not permitted, or not text: unreadable, never empty
        raise StoreUnreadable("job_store.json could not be read") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise StoreUnreadable("job_store.json could not be read") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict) \
            or not isinstance(data.setdefault("aliases", {}), dict):
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


#: Words that say how a posting is worked, not where: "Remote - US" is placed in the US, "Remote" alone nowhere.
_WORK_MODES = frozenset({"remote", "hybrid", "onsite", "anywhere"})


def _place(location) -> Tuple[str, ...]:
    """The place a location names first, as words: the first comma, slash, bracket or spaced dash separated part
    that is more than a work mode. "New York, NY" and "New York, New York, United States" are ("new", "york");
    "Austin, TX" is ("austin",); "Remote (US)" and "US Remote" are ("us",); "Remote" is ()."""
    for part in re.split(r"[,;|/()]|\s[-\u2013\u2014]\s", str(location or "").lower()):
        words = tuple(w for w in _words(part).split() if w not in _WORK_MODES)
        if words:
            return words
    return ()


def _contradicts(record: dict, listing: dict) -> bool:
    """True when both name a company and a title and they differ, or both name a place (_place) and neither place
    begins the other: then a shared URL is a careers page that lists several postings, not one posting. Austin, TX
    and Dallas, TX are two postings, and so are New York and York, PA; "New York, NY", "New York City" and "New
    York, New York, United States" are feeds spelling one place, and stay one posting. Known limits: a place spelled
    two ways that neither begins ("NYC", "New York") is two postings, and two towns of one name in different states
    ("Portland, OR", "Portland, ME") are one. A sighting that names no place contradicts nothing, so on a careers
    page it joins the first record that page's URL was seen with."""
    mine = (_words(record.get("company")), _words(record.get("title")))
    theirs = (_words(listing.get("company")), _words(listing.get("title")))
    if all(mine) and all(theirs) and mine != theirs:
        return True
    here, there = _place(record.get("location")), _place(listing.get("location"))
    shorter = min(len(here), len(there))
    return bool(shorter) and here[:shorter] != there[:shorter]


def _find(data: dict, keys: List[str], listing: dict) -> Optional[dict]:
    """The record the listing is a sighting of. A feed id or company+title+location match is enough; a URL match
    only when the company, title and location do not contradict the record's (_contradicts)."""
    url = normalize_url(listing.get("url") or listing.get("source_url"))
    url_key = _hash("u|%s" % url) if url else None
    for key in keys:
        primary = data["aliases"].get(key)
        if primary not in data["jobs"]:
            continue
        record = data["jobs"][primary]
        if key == url_key and _contradicts(record, listing):
            continue
        return record
    return None


def _register(data: dict, keys: List[str], record: dict) -> None:
    for key in keys:
        data["aliases"].setdefault(key, record["key"])


def _new_record(data: dict, keys: List[str], seen: str) -> dict:
    record = {"key": keys[0], "status": Status.OPEN.value, "first_seen": seen, "last_seen": seen,
              "closed_at": None, "close_reason": None, "job_ids": []}
    data["jobs"][record["key"]] = record
    return record


def _note_job_id(record: dict, job_id) -> None:
    if job_id:
        record["job_id"] = job_id
        known = record.setdefault("job_ids", [])
        if job_id not in known:
            known.append(job_id)


# -- the rules ---------------------------------------------------------------------------------

#: The application stages that mean the owner has applied and it is still going. An application only prepared by
#: Friday ("discovered", "triaged") is not one: the owner submits it themselves, so the job is still just open.
APPLIED_STAGES = frozenset({"applied", "screening", "interview", "offer"})


def _applied_job_ids() -> frozenset:
    """Tracker job ids with an application at APPLIED_STAGES."""
    from agent_friday.seed.data.job_tracker_schema import JobTracker
    return frozenset(app.job_id for app in JobTracker().load().list_applications(active_only=True)
                     if app.stage in APPLIED_STAGES)


def _is_applied(record: dict, applied: frozenset) -> bool:
    return any(job_id in applied for job_id in record.get("job_ids") or [record.get("job_id")])


def _sweep(data: dict, now: datetime, applied: frozenset) -> bool:
    """Close what is past its closing date and expire what has not been seen for STALE_DAYS, leaving postings with
    an active application alone. True when it changed."""
    changed = False
    for record in data["jobs"].values():
        if record["status"] != Status.OPEN.value or _is_applied(record, applied):
            continue
        closes, seen = _moment(record.get("closes_at")), _moment(record.get("last_seen"))
        if closes is not None and closes <= now:
            record.update(status=Status.CLOSED.value, closed_at=closes.isoformat(), close_reason="closing date passed")
            changed = True
        elif seen is not None and now - seen > STALE:
            record.update(status=Status.EXPIRED.value, closed_at=(seen + STALE).isoformat(),
                          close_reason="not seen for %d days" % STALE_DAYS)
            changed = True
    return changed


def _public(record: dict, applied: frozenset = frozenset()) -> dict:
    row = {name: record.get(name) for name in PUBLIC}
    if _is_applied(record, applied):
        row["status"] = Status.APPLIED.value
    return row


def _apply_sighting(record: dict, listing: dict, seen: str) -> None:
    record["last_seen"] = seen
    for name, value in (("title", listing.get("title")), ("company", listing.get("company")),
                        ("location", listing.get("location")), ("source", listing.get("source")),
                        ("url", listing.get("url") or listing.get("source_url")),
                        ("posted_at", _iso(listing.get("posted_at"))),
                        ("closes_at", _closing_iso(listing.get("closes_at")))):
        if value:
            record[name] = value
    if "remote" in listing:
        record["remote"] = bool(listing["remote"])
    _note_job_id(record, listing.get("job_id"))
    score = listing.get("score", listing.get("relevance_score"))
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        record["score"] = float(score)
    if record["status"] == Status.EXPIRED.value:
        record.update(status=Status.OPEN.value, closed_at=None, close_reason=None)


def upsert_many(listings: Iterable[dict], now: datetime) -> List[dict]:
    """Record one sighting of each posting with one read and one write of the file. A new posting is open; a known
    one is refreshed (an expired one reopens, a closed one stays closed). Listings that name nothing to key on are
    skipped. Returns the records of the listings recorded."""
    seen = now.isoformat()
    with _LOCK:
        data = _load()
        recorded = []
        for listing in listings:
            try:
                keys = make_keys(listing)
            except ValueError:
                continue
            record = _find(data, keys, listing) or _new_record(data, keys, seen)
            _register(data, keys, record)
            _apply_sighting(record, listing, seen)
            recorded.append(_public(record))
        _save(data)
        return recorded


def upsert(listing: dict, now: datetime) -> dict:
    """One sighting (see upsert_many). Raises ValueError when the listing names nothing to key on."""
    make_keys(listing)
    return upsert_many([listing], now)[0]


def _swept(now: datetime) -> Tuple[dict, frozenset]:
    applied = _applied_job_ids()
    with _LOCK:
        data = _load()
        if _sweep(data, now, applied):
            _save(data)
        return data, applied


def _is_recent(record: dict, now: datetime, applied: frozenset) -> bool:
    if _is_applied(record, applied):
        return True
    seen = _moment(record.get("last_seen"))
    return record["status"] == Status.OPEN.value and seen is not None and now - seen <= STALE


def list_recent(now: datetime, limit: int = DEFAULT_LIMIT) -> list:
    """Applied postings and open postings seen within STALE_DAYS, newest sighting first."""
    data, applied = _swept(now)
    rows = [r for r in data["jobs"].values() if _is_recent(r, now, applied)]
    rows.sort(key=lambda r: (r.get("last_seen") or "", r.get("first_seen") or "", r.get("title") or ""), reverse=True)
    return [_public(r, applied) for r in rows[:max(0, int(limit))]]


def list_archive(now: datetime, limit: int = DEFAULT_LIMIT) -> list:
    """Closed and expired postings without an active application, newest closing first."""
    data, applied = _swept(now)
    rows = [r for r in data["jobs"].values() if not _is_recent(r, now, applied)]
    rows.sort(key=lambda r: (r.get("closed_at") or r.get("last_seen") or ""), reverse=True)
    return [_public(r, applied) for r in rows[:max(0, int(limit))]]


# -- the old tracker --------------------------------------------------------------------------------

def _tracker_rows() -> list:
    """The listings the bundled scanner's tracker holds (data only; its internals stay its own)."""
    from agent_friday.seed.data.job_tracker_schema import JobTracker
    return [job.to_dict() for job in JobTracker().load().list_jobs(limit=100000)]


def import_tracker(now: datetime) -> int:
    """Bring the old tracker's listings in once, judged by the same rules (a July posting nobody has seen since
    lands in the archive unless it has an active application). Returns how many records were added; 0 on every
    later call."""
    with _LOCK:
        data = _load()
        if data.get("tracker_imported"):
            return 0
        added = 0
        for row in _tracker_rows():
            try:
                keys = make_keys(row)
            except ValueError:
                continue
            record = _find(data, keys, row)
            if record is None:
                seen = _iso(row.get("discovered_at")) or now.isoformat()
                record = _new_record(data, keys, seen)
                _apply_sighting(record, row, seen)
                added += 1
            else:
                _note_job_id(record, row.get("job_id"))
            _register(data, keys, record)
        data["tracker_imported"] = True
        _save(data)
        return added
