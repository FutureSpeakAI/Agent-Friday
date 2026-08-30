"""
Job tracker schema — dataclass models for Agent Friday's career pipeline.

Pipeline:
    discovered → triaged → applied → screening → interview → offer → closed
                                                                   ↘ rejected
                                                                   ↘ withdrawn

Everything serializes to JSON. The tracker file lives at
~/.friday/job_tracker.json by default; the JobTracker class handles
read/write atomically.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


# ── Constants ────────────────────────────────────────────────────────────

PIPELINE_STAGES = [
    "discovered",
    "triaged",
    "applied",
    "screening",
    "interview",
    "offer",
    "closed",
    "rejected",
    "withdrawn",
]

ACTIVE_STAGES = {"discovered", "triaged", "applied", "screening", "interview", "offer"}
TERMINAL_STAGES = {"closed", "rejected", "withdrawn"}

DEFAULT_TRACKER_PATH = Path.home() / ".friday" / "job_tracker.json"


# ── Helpers ──────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _new_id(prefix: str = "job") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ── JobListing ───────────────────────────────────────────────────────────

@dataclass
class JobListing:
    """A single discovered opportunity. The atom of the pipeline."""
    job_id: str = field(default_factory=lambda: _new_id("job"))
    source: str = "linkedin"             # linkedin | ycombinator | greenhouse | manual | ...
    source_url: str = ""
    external_id: str = ""                # platform-native ID (e.g. LinkedIn job ID)
    title: str = ""
    company: str = ""
    location: str = ""
    remote: bool = False                 # fully remote
    hybrid: bool = False
    onsite: bool = False
    posted_at: Optional[str] = None
    discovered_at: str = field(default_factory=_now_iso)
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: str = "USD"
    seniority: str = ""                  # IC | manager | director | vp | c-suite
    employment_type: str = "full_time"   # full_time | contract | part_time
    skills_required: List[str] = field(default_factory=list)
    keywords_matched: List[str] = field(default_factory=list)
    description: str = ""                # raw scraped/extracted text
    raw: Dict[str, Any] = field(default_factory=dict)  # full source payload for replay
    relevance_score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    dedup_key: str = ""                  # hash used to deduplicate
    notes: str = ""

    # ── Convenience ──
    @property
    def is_priority(self) -> bool:
        return self.relevance_score >= 0.80

    @property
    def headline(self) -> str:
        company = self.company or "Unknown company"
        loc = self.location or ("Remote" if self.remote else "—")
        salary = ""
        if self.salary_min or self.salary_max:
            lo = f"${self.salary_min // 1000}K" if self.salary_min else "?"
            hi = f"${self.salary_max // 1000}K" if self.salary_max else "?"
            salary = f"  ·  {lo}–{hi}"
        return f"{self.title} @ {company}  ·  {loc}{salary}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobListing":
        # Tolerant of missing keys from older records
        kept = {k: data.get(k, _default_for(cls, k)) for k in cls.__dataclass_fields__}
        return cls(**kept)


# ── ApplicationRecord ────────────────────────────────────────────────────

@dataclass
class ApplicationRecord:
    """One application attempt against a JobListing."""
    application_id: str = field(default_factory=lambda: _new_id("app"))
    job_id: str = ""
    submitted_at: str = field(default_factory=_now_iso)
    stage: str = "applied"               # one of PIPELINE_STAGES
    ats_platform: str = ""               # greenhouse | lever | workable | smartrecruiters | other
    resume_variant: str = ""             # which resume variant was used
    cover_letter_variant: str = ""
    cover_letter_word_count: int = 0
    quality_gates_passed: List[str] = field(default_factory=list)
    quality_gates_failed: List[str] = field(default_factory=list)
    confirmation_received: bool = False
    confirmation_at: Optional[str] = None
    response_at: Optional[str] = None
    response_kind: str = ""              # rejection | screening | interview | offer | ghost
    interview_rounds: List[Dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    history: List[Dict[str, str]] = field(default_factory=list)
    # history entries: {"at": iso, "stage": str, "note": str}

    def advance(self, new_stage: str, note: str = ""):
        if new_stage not in PIPELINE_STAGES:
            raise ValueError(f"unknown pipeline stage: {new_stage}")
        self.history.append({"at": _now_iso(), "stage": new_stage, "note": note})
        self.stage = new_stage
        if new_stage in {"screening", "interview", "offer"} and not self.response_at:
            self.response_at = _now_iso()
            self.response_kind = new_stage

    @property
    def is_active(self) -> bool:
        return self.stage in ACTIVE_STAGES

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApplicationRecord":
        kept = {k: data.get(k, _default_for(cls, k)) for k in cls.__dataclass_fields__}
        return cls(**kept)


# ── JobTracker ───────────────────────────────────────────────────────────

class JobTracker:
    """
    File-backed pipeline manager.

    Atomic JSON writes — never half-written files. Thread-safe via internal lock.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_TRACKER_PATH
        self._lock = threading.Lock()
        self._jobs: Dict[str, JobListing] = {}
        self._apps: Dict[str, ApplicationRecord] = {}
        self._dedup_index: Dict[str, str] = {}    # dedup_key -> job_id
        self._loaded = False

    # ── persistence ──
    def load(self):
        with self._lock:
            self._load_unlocked()
        return self

    def _load_unlocked(self):
        if not self.path.exists():
            self._loaded = True
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except Exception:
            data = {}
        self._jobs = {
            jid: JobListing.from_dict(d) for jid, d in data.get("jobs", {}).items()
        }
        self._apps = {
            aid: ApplicationRecord.from_dict(d) for aid, d in data.get("applications", {}).items()
        }
        self._dedup_index = {j.dedup_key: jid for jid, j in self._jobs.items() if j.dedup_key}
        self._loaded = True

    def save(self):
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": _now_iso(),
            "jobs": {jid: j.to_dict() for jid, j in self._jobs.items()},
            "applications": {aid: a.to_dict() for aid, a in self._apps.items()},
        }
        # Atomic write — tmp file + rename
        tmp = tempfile.NamedTemporaryFile(
            "w", delete=False, dir=self.path.parent,
            prefix=".tracker.", suffix=".tmp", encoding="utf-8"
        )
        try:
            json.dump(payload, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, self.path)
        except Exception:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise

    # ── job CRUD ──
    def add_job(self, listing: JobListing) -> Optional[JobListing]:
        """
        Add a job. If dedup_key collides with an existing entry, returns None
        and updates the existing entry's relevance_score if the new one is higher.
        """
        if not self._loaded:
            self.load()
        with self._lock:
            if listing.dedup_key and listing.dedup_key in self._dedup_index:
                existing_id = self._dedup_index[listing.dedup_key]
                existing = self._jobs.get(existing_id)
                if existing and listing.relevance_score > existing.relevance_score:
                    existing.relevance_score = listing.relevance_score
                    existing.score_breakdown = listing.score_breakdown
                    existing.keywords_matched = listing.keywords_matched
                    self._save_unlocked()
                return None
            self._jobs[listing.job_id] = listing
            if listing.dedup_key:
                self._dedup_index[listing.dedup_key] = listing.job_id
            self._save_unlocked()
            return listing

    def get_job(self, job_id: str) -> Optional[JobListing]:
        if not self._loaded:
            self.load()
        return self._jobs.get(job_id)

    def list_jobs(self, *, since: Optional[str] = None,
                  min_score: float = 0.0,
                  limit: Optional[int] = None) -> List[JobListing]:
        if not self._loaded:
            self.load()
        out = [j for j in self._jobs.values() if j.relevance_score >= min_score]
        if since:
            out = [j for j in out if (j.discovered_at or "") >= since]
        out.sort(key=lambda j: (j.relevance_score, j.discovered_at), reverse=True)
        if limit:
            out = out[:limit]
        return out

    def priority_jobs(self, limit: int = 5) -> List[JobListing]:
        return [j for j in self.list_jobs(min_score=0.80, limit=limit) if j.is_priority]

    def remove_job(self, job_id: str) -> bool:
        if not self._loaded:
            self.load()
        with self._lock:
            j = self._jobs.pop(job_id, None)
            if not j:
                return False
            if j.dedup_key and self._dedup_index.get(j.dedup_key) == job_id:
                self._dedup_index.pop(j.dedup_key, None)
            self._save_unlocked()
            return True

    # ── application CRUD ──
    def log_application(self, record: ApplicationRecord) -> ApplicationRecord:
        if not self._loaded:
            self.load()
        with self._lock:
            self._apps[record.application_id] = record
            self._save_unlocked()
            return record

    def get_application(self, app_id: str) -> Optional[ApplicationRecord]:
        if not self._loaded:
            self.load()
        return self._apps.get(app_id)

    def applications_for(self, job_id: str) -> List[ApplicationRecord]:
        if not self._loaded:
            self.load()
        return [a for a in self._apps.values() if a.job_id == job_id]

    def advance_application(self, app_id: str, new_stage: str, note: str = "") -> Optional[ApplicationRecord]:
        if not self._loaded:
            self.load()
        with self._lock:
            app = self._apps.get(app_id)
            if not app:
                return None
            app.advance(new_stage, note=note)
            self._save_unlocked()
            return app

    def list_applications(self, *, stage: Optional[str] = None,
                          active_only: bool = False) -> List[ApplicationRecord]:
        if not self._loaded:
            self.load()
        out = list(self._apps.values())
        if stage:
            out = [a for a in out if a.stage == stage]
        if active_only:
            out = [a for a in out if a.is_active]
        out.sort(key=lambda a: a.submitted_at, reverse=True)
        return out

    # ── analytics ──
    def response_rate(self, days: int = 30) -> Dict[str, Any]:
        """Compute response rates over the last N days."""
        if not self._loaded:
            self.load()
        cutoff = (datetime.utcnow() - _days(days)).isoformat() + "Z"
        applied = [a for a in self._apps.values() if a.submitted_at >= cutoff]
        if not applied:
            return {
                "window_days": days,
                "applied": 0,
                "responded": 0,
                "screening": 0,
                "interview": 0,
                "offer": 0,
                "rejection": 0,
                "ghost_rate": 0.0,
                "response_rate": 0.0,
                "interview_rate": 0.0,
                "offer_rate": 0.0,
            }
        counts = {
            "screening": 0, "interview": 0, "offer": 0,
            "rejection": 0, "ghost": 0,
        }
        for a in applied:
            kind = a.response_kind or ("ghost" if not a.response_at else "")
            for k in counts:
                if a.stage == k or a.response_kind == k:
                    counts[k] += 1
                    break
        total = len(applied)
        responded = sum(counts[k] for k in ("screening", "interview", "offer", "rejection"))
        ghosted = total - responded
        return {
            "window_days": days,
            "applied": total,
            "responded": responded,
            "screening": counts["screening"],
            "interview": counts["interview"],
            "offer": counts["offer"],
            "rejection": counts["rejection"],
            "ghost_rate": round(ghosted / total, 3),
            "response_rate": round(responded / total, 3),
            "interview_rate": round(counts["interview"] / total, 3),
            "offer_rate": round(counts["offer"] / total, 3),
        }

    def pipeline_summary(self) -> Dict[str, int]:
        if not self._loaded:
            self.load()
        summary = {s: 0 for s in PIPELINE_STAGES}
        for a in self._apps.values():
            summary[a.stage] = summary.get(a.stage, 0) + 1
        summary["_total_jobs_discovered"] = len(self._jobs)
        summary["_priority_jobs_open"] = sum(
            1 for j in self._jobs.values() if j.is_priority
            and not any(app.is_active for app in self.applications_for(j.job_id))
        )
        return summary

    def to_dict(self) -> Dict[str, Any]:
        """Full serialization for export."""
        if not self._loaded:
            self.load()
        return {
            "version": 1,
            "updated_at": _now_iso(),
            "jobs": {jid: j.to_dict() for jid, j in self._jobs.items()},
            "applications": {aid: a.to_dict() for aid, a in self._apps.items()},
            "summary": self.pipeline_summary(),
            "response_rate_30d": self.response_rate(30),
        }


# ── Internal utils ───────────────────────────────────────────────────────

def _default_for(cls, field_name: str) -> Any:
    f = cls.__dataclass_fields__.get(field_name)
    if f is None:
        return None
    if f.default is not dataclass_MISSING():
        return f.default
    if f.default_factory is not dataclass_MISSING():  # type: ignore[misc]
        try:
            return f.default_factory()  # type: ignore[misc]
        except Exception:
            return None
    return None


def dataclass_MISSING():
    """dataclasses.MISSING is a sentinel — wrap it so type checkers don't complain."""
    import dataclasses as _dc
    return _dc.MISSING


def _days(n: int):
    from datetime import timedelta
    return timedelta(days=n)


# ── CLI for quick inspection ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="job_tracker_schema")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("summary", help="Show pipeline summary")
    sub.add_parser("priority", help="List priority jobs")
    p_resp = sub.add_parser("response", help="Show response rate")
    p_resp.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    t = JobTracker().load()
    if args.cmd == "summary" or args.cmd is None:
        print(json.dumps(t.pipeline_summary(), indent=2))
    elif args.cmd == "priority":
        for j in t.priority_jobs(limit=10):
            print(f"  [{j.relevance_score:.2f}]  {j.headline}")
            print(f"          {j.source_url}")
    elif args.cmd == "response":
        print(json.dumps(t.response_rate(days=args.days), indent=2))
