"""
Job scanner — LinkedIn discovery, scoring, dedup, notification dispatch.

This module is intentionally side-effect-light: `scan()` is callable from
the Flask server, from the CLI, and from background scheduler threads.
Pass a `fetcher` callable to override the default LinkedIn fetch (useful
for tests).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.parse
from dataclasses import asdict
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

# Import the schema and notifications from the package — keep these as
# lazy imports so the module can be loaded without the full friday tree
# present (useful for unit tests).
try:
    from data.job_tracker_schema import JobListing, JobTracker
except ImportError:  # pragma: no cover - fallback for partial installs
    from job_tracker_schema import JobListing, JobTracker  # type: ignore

try:
    import notifications as _notify
except ImportError:  # pragma: no cover
    _notify = None  # type: ignore


log = logging.getLogger("friday.skills.job_scanner")


# ════════════════════════════════════════════════════════════════════════
#  Config loading
# ════════════════════════════════════════════════════════════════════════

HERE = Path(__file__).parent
DEFAULT_CONFIG_PATH = HERE / "config.yaml"
USER_OVERRIDE = Path.home() / ".friday" / "skills" / "job_scanner.local.yaml"


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load default config, merge user overrides on top."""
    if yaml is None:
        raise RuntimeError("PyYAML not installed — required for job_scanner config")
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with cfg_path.open(encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}
    if USER_OVERRIDE.exists():
        try:
            with USER_OVERRIDE.open(encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            base = _deep_merge(base, user)
        except Exception as e:
            log.warning("Could not merge user override at %s: %s", USER_OVERRIDE, e)
    return base


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ════════════════════════════════════════════════════════════════════════
#  Keyword rotation
# ════════════════════════════════════════════════════════════════════════

class KeywordRotator:
    """Round-robin through configured keyword sets so we vary our queries."""

    _state_path = Path.home() / ".friday" / "skills" / "job_scanner.state.json"

    def __init__(self, sets: Sequence[Sequence[str]]):
        self.sets: List[List[str]] = [list(s) for s in sets if s]
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    def next_set(self) -> List[str]:
        if not self.sets:
            return []
        idx = self._load_idx()
        chosen = self.sets[idx % len(self.sets)]
        self._save_idx(idx + 1)
        return chosen

    def _load_idx(self) -> int:
        if not self._state_path.exists():
            return 0
        try:
            return int(json.loads(self._state_path.read_text("utf-8")).get("idx", 0))
        except Exception:
            return 0

    def _save_idx(self, idx: int):
        try:
            self._state_path.write_text(
                json.dumps({"idx": idx, "updated_at": _now_iso()}), encoding="utf-8")
        except Exception as e:
            log.warning("Could not persist rotator state: %s", e)


# ════════════════════════════════════════════════════════════════════════
#  URL construction
# ════════════════════════════════════════════════════════════════════════

def build_linkedin_url(*, keywords: Sequence[str], location: str = "United States",
                       remote: bool = False, posted_within_hours: int = 24,
                       base_url: str = "https://www.linkedin.com/jobs/search/") -> str:
    """Construct a LinkedIn search URL from keywords + filters."""
    params: List[Tuple[str, str]] = []
    if keywords:
        params.append(("keywords", " OR ".join(f'"{k}"' for k in keywords)))
    if location:
        params.append(("location", location))
    if remote:
        # LinkedIn workplace types: 1=on-site, 2=remote, 3=hybrid
        params.append(("f_WT", "2"))
    if posted_within_hours and posted_within_hours > 0:
        # LinkedIn `f_TPR` accepts r<seconds>
        params.append(("f_TPR", f"r{posted_within_hours * 3600}"))
    params.append(("sortBy", "DD"))   # date descending
    return base_url + "?" + urllib.parse.urlencode(params, doseq=True)


# ════════════════════════════════════════════════════════════════════════
#  Scoring
# ════════════════════════════════════════════════════════════════════════

def score_listing(listing: JobListing, profile: Dict[str, Any],
                  salary_cfg: Dict[str, Any],
                  weights: Dict[str, float]) -> Tuple[float, Dict[str, float]]:
    """
    Returns (composite_score in [0,1], component_breakdown).

    Components:
      title_match     — does the title contain a target keyword?
      salary_match    — interpolated against floor/target/stretch
      remote_match    — remote=1, hybrid=0.6, onsite=0.0
      skills_overlap  — jaccard over target_skills
      seniority_match — matches configured seniority levels
      company_signal  — preferred(+) / blocked(−)
    """
    breakdown: Dict[str, float] = {}

    title = (listing.title or "").lower()
    targets = [k.lower() for k in profile.get("target_title_keywords", [])]
    excludes = [k.lower() for k in profile.get("exclude_title_keywords", [])]
    if any(x in title for x in excludes):
        breakdown["title_match"] = 0.0
    elif any(t in title for t in targets):
        breakdown["title_match"] = 1.0
    else:
        # partial credit for tokens that overlap
        title_tokens = set(re.findall(r"[a-z]+", title))
        match_tokens = set()
        for t in targets:
            match_tokens.update(re.findall(r"[a-z]+", t))
        if title_tokens & match_tokens:
            breakdown["title_match"] = 0.3
        else:
            breakdown["title_match"] = 0.0

    floor = salary_cfg.get("floor_usd", 0)
    target = salary_cfg.get("target_usd", floor or 1)
    salary_mid = listing.salary_max or listing.salary_min or 0
    if salary_mid <= 0:
        breakdown["salary_match"] = 0.5  # unknown — neutral
    elif salary_mid < floor:
        breakdown["salary_match"] = 0.0
    elif salary_mid >= target:
        breakdown["salary_match"] = 1.0
    else:
        breakdown["salary_match"] = (salary_mid - floor) / max(target - floor, 1)

    if listing.remote:
        breakdown["remote_match"] = 1.0
    elif listing.hybrid:
        breakdown["remote_match"] = 0.6
    else:
        breakdown["remote_match"] = 0.0

    targ_skills = {s.lower() for s in profile.get("target_skills", [])}
    have = {s.lower() for s in listing.skills_required}
    desc_tokens = re.findall(r"[a-z+#0-9.]+", (listing.description or "").lower())
    have.update(s for s in targ_skills if s.replace(" ", "") in "".join(desc_tokens))
    if targ_skills:
        overlap = len(have & targ_skills) / max(len(targ_skills), 1)
        breakdown["skills_overlap"] = round(min(overlap, 1.0), 3)
    else:
        breakdown["skills_overlap"] = 0.0

    sen_targets = [s.lower() for s in profile.get("seniority_targets", [])]
    seniority = (listing.seniority or "").lower()
    if seniority and any(s in seniority for s in sen_targets):
        breakdown["seniority_match"] = 1.0
    elif any(s in title for s in sen_targets):
        breakdown["seniority_match"] = 0.8
    else:
        breakdown["seniority_match"] = 0.0

    co_norm = (listing.company or "").strip().lower()
    preferred = {c.lower() for c in profile.get("preferred_companies", [])}
    blocked = {c.lower() for c in profile.get("blocked_companies", [])}
    if co_norm in blocked:
        breakdown["company_signal"] = 0.0
        breakdown["_blocked"] = 1.0
    elif co_norm in preferred:
        breakdown["company_signal"] = 1.0
    else:
        breakdown["company_signal"] = 0.4   # neutral default

    # Weighted sum, normalized by total weight
    total_weight = sum(weights.values()) or 1.0
    score = 0.0
    for key, w in weights.items():
        score += w * breakdown.get(key, 0.0)
    score = score / total_weight
    # If blocked, hard zero
    if breakdown.get("_blocked"):
        score = 0.0
    return round(max(0.0, min(1.0, score)), 4), breakdown


# ════════════════════════════════════════════════════════════════════════
#  Dedup
# ════════════════════════════════════════════════════════════════════════

def dedup_key(listing: JobListing, strategies: Sequence[str]) -> str:
    """Compute a stable hash for dedup. First matching strategy wins."""
    for strat in strategies:
        parts: List[str] = []
        for field_name in strat.split(":"):
            v = getattr(listing, field_name, "") or ""
            v = str(v).strip().lower()
            if not v:
                parts = []
                break
            parts.append(v)
        if parts:
            return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    # Fallback — hash the URL
    return hashlib.sha1((listing.source_url or listing.title).encode("utf-8")).hexdigest()[:16]


# ════════════════════════════════════════════════════════════════════════
#  Fetching (placeholder + extensible)
# ════════════════════════════════════════════════════════════════════════

Fetcher = Callable[[str], List[Dict[str, Any]]]


def default_fetcher(url: str) -> List[Dict[str, Any]]:
    """
    Default fetch — returns an empty list and logs the URL.

    Real scraping requires either:
      - the LinkedIn API (if Stephen has credentials), or
      - a browser-driven extractor (Selenium / playwright) which is too heavy
        to ship by default.

    Server.py wires this up with `browse_web` + an LLM extraction pass.
    """
    log.info("[stub] fetch %s", url)
    return []


def normalize_raw(raw: Dict[str, Any]) -> JobListing:
    """Turn a raw fetch payload into a JobListing dataclass."""
    listing = JobListing(
        source=raw.get("source", "linkedin"),
        source_url=raw.get("url") or raw.get("source_url", ""),
        external_id=str(raw.get("id") or raw.get("external_id") or ""),
        title=raw.get("title", "").strip(),
        company=raw.get("company", "").strip(),
        location=raw.get("location", "").strip(),
        remote=bool(raw.get("remote", False)),
        hybrid=bool(raw.get("hybrid", False)),
        onsite=bool(raw.get("onsite", False)),
        posted_at=raw.get("posted_at"),
        salary_min=raw.get("salary_min"),
        salary_max=raw.get("salary_max"),
        salary_currency=raw.get("salary_currency", "USD"),
        seniority=raw.get("seniority", ""),
        employment_type=raw.get("employment_type", "full_time"),
        skills_required=list(raw.get("skills") or raw.get("skills_required") or []),
        description=raw.get("description", ""),
        raw=raw,
    )
    return listing


# ════════════════════════════════════════════════════════════════════════
#  ScanResult
# ════════════════════════════════════════════════════════════════════════

def _empty_result() -> Dict[str, Any]:
    return {
        "scanned": 0,
        "deduped": 0,
        "new_listings": 0,
        "priority": 0,
        "notifications_sent": 0,
        "errors": [],
        "duration_ms": 0,
        "keyword_set": [],
        "url": "",
    }


# ════════════════════════════════════════════════════════════════════════
#  Scan
# ════════════════════════════════════════════════════════════════════════

def scan(*, config: Optional[Dict[str, Any]] = None,
         tracker: Optional[JobTracker] = None,
         fetcher: Optional[Fetcher] = None,
         notify: Optional[Callable[[Dict[str, Any]], None]] = None,
         keyword_set_override: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Run one scan cycle.

    Args:
        config:      full job_scanner config (loaded from yaml if not passed)
        tracker:     a JobTracker (will be loaded if not passed)
        fetcher:     callable(url) -> list of raw dicts. Defaults to a stub.
        notify:      callable(payload) -> None. Defaults to log.info.
        keyword_set_override: skip rotation, use this set instead.

    Returns ScanResult dict.
    """
    started = time.monotonic()
    config = config or load_config()
    tracker = tracker or JobTracker().load()
    fetcher = fetcher or default_fetcher
    notify = notify or _log_notify

    result = _empty_result()

    # Pick keyword set
    if keyword_set_override:
        keyword_set = keyword_set_override
    else:
        rotator = KeywordRotator(
            config.get("schedule", {}).get("keyword_rotation", {}).get("sets", [])
        )
        keyword_set = rotator.next_set()
    result["keyword_set"] = keyword_set

    if not keyword_set:
        result["errors"].append("no keyword set configured")
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return result

    # Build URL
    search_cfg = config.get("search", {})
    url = build_linkedin_url(
        keywords=keyword_set,
        location=search_cfg.get("location", "United States"),
        remote=search_cfg.get("remote", False),
        posted_within_hours=search_cfg.get("posted_within_hours", 24),
        base_url=search_cfg.get("base_url", "https://www.linkedin.com/jobs/search/"),
    )
    result["url"] = url

    # Fetch
    try:
        raw_results = fetcher(url) or []
    except Exception as e:
        log.exception("fetcher failed")
        result["errors"].append(f"fetcher error: {e}")
        raw_results = []

    # Cap
    limit = search_cfg.get("per_keyword_set_limit", 25)
    raw_results = list(raw_results)[:limit]
    result["scanned"] = len(raw_results)

    # Score, dedup, persist
    profile = config.get("profile", {})
    salary_cfg = config.get("salary", {})
    weights = config.get("scoring", {}).get("weights", {})
    priority_threshold = config.get("scoring", {}).get("priority_threshold", 0.80)
    dedup_keys = config.get("dedup", {}).get("keys", ["source:external_id"])

    daily_cap = config.get("notifications", {}).get("priority_alert_max_per_day", 6)
    sent_today = _priority_sent_today()

    new_priority: List[Dict[str, Any]] = []

    for raw in raw_results:
        try:
            listing = normalize_raw(raw)
            score, breakdown = score_listing(listing, profile, salary_cfg, weights)
            listing.relevance_score = score
            listing.score_breakdown = breakdown
            listing.keywords_matched = [k for k in keyword_set
                                        if k.lower() in (listing.title or "").lower()]
            listing.dedup_key = dedup_key(listing, dedup_keys)
            inserted = tracker.add_job(listing)
            if inserted is None:
                result["deduped"] += 1
                continue
            result["new_listings"] += 1
            if score >= priority_threshold:
                result["priority"] += 1
                if sent_today + len(new_priority) < daily_cap and _notify:
                    payload = _notify.priority_job_alert(listing.to_dict())
                    notify(payload)
                    new_priority.append(payload)
                else:
                    log.info("priority cap reached, not notifying for %s", listing.title)
        except Exception as e:
            log.exception("error processing raw posting")
            result["errors"].append(f"process error: {e}")

    result["notifications_sent"] = len(new_priority)
    _bump_priority_counter(len(new_priority))
    result["duration_ms"] = int((time.monotonic() - started) * 1000)

    # Send to SkillOpt for self-improvement tracking
    _record_to_skillopt(result, config)

    return result


# ════════════════════════════════════════════════════════════════════════
#  Helpers — counter, schedule, skillopt hook
# ════════════════════════════════════════════════════════════════════════

_COUNTER_PATH = Path.home() / ".friday" / "skills" / "job_scanner.daily.json"


def _priority_sent_today() -> int:
    if not _COUNTER_PATH.exists():
        return 0
    try:
        data = json.loads(_COUNTER_PATH.read_text("utf-8"))
        if data.get("date") != datetime.utcnow().date().isoformat():
            return 0
        return int(data.get("count", 0))
    except Exception:
        return 0


def _bump_priority_counter(n: int):
    if n <= 0:
        return
    try:
        _COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.utcnow().date().isoformat()
        cur = 0
        if _COUNTER_PATH.exists():
            data = json.loads(_COUNTER_PATH.read_text("utf-8"))
            if data.get("date") == today:
                cur = int(data.get("count", 0))
        _COUNTER_PATH.write_text(
            json.dumps({"date": today, "count": cur + n}), encoding="utf-8")
    except Exception as e:
        log.warning("could not bump priority counter: %s", e)


def _log_notify(payload: Dict[str, Any]):
    title = payload.get("title", "(notification)")
    log.info("[notify] %s", title)


def in_active_hours(now: Optional[datetime] = None,
                    cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Whether `now` falls within the configured active window."""
    now = now or datetime.now()
    cfg = cfg or load_config()
    sched = cfg.get("schedule", {}).get("active_hours", {})
    start = _parse_hhmm(sched.get("start", "07:00"))
    end = _parse_hhmm(sched.get("end", "22:00"))
    cur = now.time()
    return start <= cur <= end


def _parse_hhmm(s: str) -> dt_time:
    try:
        h, m = s.split(":")
        return dt_time(int(h), int(m))
    except Exception:
        return dt_time(0, 0)


def _record_to_skillopt(result: Dict[str, Any], config: Dict[str, Any]):
    """Push run metrics into SkillOpt for trend tracking."""
    try:
        from skillopt_engine import record_skill_run  # local import — keep optional
    except ImportError:
        return
    skillopt_cfg = config.get("skillopt", {})
    skill_name = skillopt_cfg.get("skill_name", "job_scanner")
    # Heuristic metrics: completeness = fraction of listings with full salary
    completeness = 0.0
    if result["new_listings"]:
        # We don't have direct access to inserted listings here; the tracker
        # will count this lazily. Approximate with priority ratio for now.
        completeness = result["priority"] / max(result["new_listings"], 1)
    try:
        record_skill_run(
            skill_name=skill_name,
            inputs={"keyword_set": result["keyword_set"], "url": result["url"]},
            outputs={"new_listings": result["new_listings"],
                     "priority": result["priority"],
                     "deduped": result["deduped"]},
            metrics={
                "accuracy": 1.0 if not result["errors"] else 0.5,
                "completeness": completeness,
                "user_satisfaction": 0.0,  # filled in later from explicit feedback
            },
            duration_ms=result["duration_ms"],
            cost_usd=0.0,
            error=("; ".join(result["errors"]) if result["errors"] else None),
        )
    except Exception as e:
        log.warning("skillopt record failed: %s", e)


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ════════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="job_scanner")
    parser.add_argument("--keywords", nargs="*", help="Override keyword set")
    parser.add_argument("--dry-run", action="store_true",
                        help="Just build URL and exit")
    args = parser.parse_args()

    cfg = load_config()
    if args.dry_run:
        rot = KeywordRotator(cfg.get("schedule", {}).get("keyword_rotation", {}).get("sets", []))
        ks = args.keywords or rot.next_set()
        print(build_linkedin_url(
            keywords=ks,
            location=cfg.get("search", {}).get("location", "United States"),
            remote=cfg.get("search", {}).get("remote", False),
            posted_within_hours=cfg.get("search", {}).get("posted_within_hours", 24),
        ))
    else:
        out = scan(config=cfg, keyword_set_override=args.keywords)
        print(json.dumps(out, indent=2))
