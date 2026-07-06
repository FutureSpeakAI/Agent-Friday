"""
Agent Friday — Content Pipeline data model & store (spec §3)
FutureSpeak.AI · Asimov's Mind

The persistence spine of the social-media pipeline: ContentPost / PlatformTarget
entities as plain dicts, the SQLite store, the §3.2 status machine, the
publish-log appender, and the one-way graduation path from the legacy kanban.

Public API (all return envelopes — {"ok": bool, ...} — and never raise)
----------------------------------------------------------------------
Constructors (plain dict builders, no I/O):
  new_post / new_asset_ref / new_platform_target / new_schedule_config
  new_variant / new_engagement_metrics

Posts:
  create_post, get_post, list_posts, update_post, delete_post (→ CANCELLED)
  schedule_post, publish_now, cancel_post, rearm_post, release_held,
  expire_stale_holds, graduate_idea

Targets:
  add_targets, get_target, update_target, set_target_status,
  claim_due_targets(now)  — mark-before-run PENDING→PREPARING under the lock
  defer_target            — budget deferral, attempt not burned
  recover_stale_claims    — crash recovery: orphaned PREPARING → PENDING

Analytics / learning:
  insert_engagement_snapshot, refresh_post_analytics, get_snapshots
  upsert_best_time, get_best_times
  award_psi (check-and-insert, idempotent per {target}:{metric}:{threshold})

Publish log:
  append_publish_log, read_publish_log   (~/.friday/content/publish_log.jsonl,
                                          rotated like schedule_runs.jsonl)

Storage: ~/.friday/content_pipeline.db (SQLite, WAL, no ORM).
Instants are ISO-8601 UTC strings ("%Y-%m-%dT%H:%M:%SZ"); `not_before` is a
REAL epoch (scheduler backoff convention).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR

# ── Storage ──────────────────────────────────────────────────────────────────
DB_PATH = FRIDAY_DIR / "content_pipeline.db"
CONTENT_DIR = FRIDAY_DIR / "content"
PUBLISH_LOG = CONTENT_DIR / "publish_log.jsonl"
PUBLISH_LOG_KEEP = 1000               # cap the publish log to the last N lines

_LOCK = threading.RLock()
_LOG_LOCK = threading.Lock()

# ── Status machine (§3.2) ────────────────────────────────────────────────────
POST_STATUSES = ("DRAFT", "SCHEDULED", "PUBLISHING", "PUBLISHED", "PARTIAL",
                 "HELD", "FAILED", "CANCELLED")
TARGET_STATUSES = ("PENDING", "PREPARING", "SENT", "CONFIRMED", "HELD",
                   "FAILED", "SKIPPED")

# Post states with no outgoing edges. PARTIAL/FAILED are *soft*-terminal — they
# re-arm back to SCHEDULED (never reopening CONFIRMED targets).
POST_TERMINAL = ("PUBLISHED", "CANCELLED")
TARGET_TERMINAL = ("CONFIRMED",)      # a confirmed target is immutable (§7.2)

HOLD_EXPIRY_DAYS = 7                  # HELD unattended → CANCELLED (§3.2)

_POST_TRANSITIONS = {
    "DRAFT":      {"SCHEDULED", "PUBLISHING", "CANCELLED"},
    "SCHEDULED":  {"DRAFT", "SCHEDULED", "PUBLISHING", "CANCELLED"},
    "PUBLISHING": {"PUBLISHING", "PUBLISHED", "PARTIAL", "FAILED", "HELD"},
    "HELD":       {"PUBLISHING", "HELD", "CANCELLED"},
    "PARTIAL":    {"SCHEDULED", "CANCELLED"},
    "FAILED":     {"SCHEDULED", "CANCELLED"},
    "PUBLISHED":  set(),
    "CANCELLED":  set(),
}

_TARGET_TRANSITIONS = {
    "PENDING":   {"PREPARING", "HELD", "SKIPPED"},
    "PREPARING": {"PENDING", "SENT", "CONFIRMED", "HELD", "FAILED", "SKIPPED"},
    "SENT":      {"CONFIRMED", "FAILED", "PENDING"},  # PENDING only after the
                                                      # §7.2 verify-before-retry
                                                      # probe finds no post
    "HELD":      {"PENDING", "SKIPPED"},
    "FAILED":    {"PENDING", "SKIPPED"},
    "SKIPPED":   {"PENDING"},          # re-arm may reopen a skipped target
    "CONFIRMED": set(),                # never reopened — double-post guarantee
}

# Target states that count as "settled" for the post rollup.
_TARGET_SETTLED = {"CONFIRMED", "FAILED", "SKIPPED"}
_TARGET_IN_FLIGHT = {"PENDING", "PREPARING", "SENT"}

PLATFORMS = ("linkedin", "twitter", "instagram", "youtube", "bluesky",
             "mastodon", "reddit", "substack", "medium", "tiktok",
             "federation", "discord", "telegram")
FORMATS = ("post", "thread", "story", "reel", "short", "article",
           "video_upload", "image_post", "link_post", "listing", "announce")

# Legacy kanban channel → pipeline platform (§3.4). Unknown channels graduate
# without a pre-selected target (the composer picks later).
_CHANNEL_TO_PLATFORM = {
    "linkedin": "linkedin", "twitter": "twitter", "x": "twitter",
    "instagram": "instagram", "youtube": "youtube", "bluesky": "bluesky",
    "mastodon": "mastodon", "reddit": "reddit", "substack": "substack",
    "medium": "medium", "tiktok": "tiktok", "federation": "federation",
}

# ── Schema (§3.3, verbatim) ──────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id            TEXT PRIMARY KEY,
    title         TEXT, body TEXT,
    status        TEXT NOT NULL DEFAULT 'DRAFT',
    schedule_json TEXT,
    assets_json   TEXT,
    variants_json TEXT,
    source_json   TEXT, license_json TEXT, tags_json TEXT,
    provenance_hash TEXT,
    analytics_json  TEXT,
    created_at TEXT, updated_at TEXT, published_at TEXT
);
CREATE TABLE IF NOT EXISTS targets (
    id            TEXT PRIMARY KEY,
    post_id       TEXT NOT NULL REFERENCES posts(id),
    platform      TEXT NOT NULL, format TEXT, variant_id TEXT,
    payload_json  TEXT,
    status        TEXT NOT NULL DEFAULT 'PENDING',
    attempt       INTEGER DEFAULT 0, not_before REAL DEFAULT 0,
    publish_at    TEXT,
    post_url TEXT, platform_post_id TEXT, error TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS engagement_snapshots (
    id         TEXT PRIMARY KEY,
    target_id  TEXT NOT NULL REFERENCES targets(id),
    post_id    TEXT NOT NULL, platform TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    raw_json     TEXT,
    collected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS best_times (
    platform  TEXT NOT NULL, weekday INTEGER NOT NULL, hour INTEGER NOT NULL,
    score REAL DEFAULT 0, samples INTEGER DEFAULT 0, updated_at TEXT,
    PRIMARY KEY (platform, weekday, hour)
);
CREATE TABLE IF NOT EXISTS psi_awards (
    key TEXT PRIMARY KEY,
    amount_mpsi INTEGER, awarded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_targets_due    ON targets(status, publish_at);
CREATE INDEX IF NOT EXISTS idx_targets_post   ON targets(post_id);
CREATE INDEX IF NOT EXISTS idx_snaps_target   ON engagement_snapshots(target_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_posts_status   ON posts(status, updated_at);
"""

_RAW_CAP_BYTES = 8192                 # §8.6 — sanitized raw payload cap


# ─────────────────────────────────────────────────────────────────────────────
#  Connection / time helpers
# ─────────────────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    """Open the store, applying the schema (CREATE IF NOT EXISTS is cheap).

    DB_PATH is read at call time so tests can monkeypatch it to a tmp dir."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _ensure_schema() -> None:
    with _LOCK:
        _connect().close()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_dt().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_instant(value) -> Optional[datetime]:
    """Best-effort parse of an instant (datetime | ISO string | epoch) → aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    elif isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_utc_iso(value) -> Optional[str]:
    """Canonicalize any instant to the store format (lexicographically sortable)."""
    dt = _parse_instant(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _default_timezone() -> str:
    try:
        tz = (core._load_settings() or {}).get("timezone")
        if tz:
            return str(tz)
    except Exception:
        pass
    return "America/Chicago"


def _dumps(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def _loads(text, default):
    try:
        v = json.loads(text)
        return v if v is not None else default
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  Entity constructors (§3.1) — plain dict builders, no I/O, no raising
# ─────────────────────────────────────────────────────────────────────────────

def new_asset_ref(filename: str, content_hash: str = "", kind: str = "image",
                  alt_text: str = "", duration_s: Optional[float] = None) -> Dict[str, Any]:
    return {
        "filename": str(filename or ""),
        "content_hash": str(content_hash or ""),
        "kind": kind if kind in ("image", "video", "audio", "document") else "image",
        "alt_text": str(alt_text or ""),
        "duration_s": duration_s,
    }


def new_schedule_config(publish_at=None, tz: Optional[str] = None,
                        recurrence: str = "none",
                        recurrence_spec: Optional[dict] = None,
                        optimal_time: bool = False,
                        expires_at=None) -> Dict[str, Any]:
    return {
        "publish_at": _to_utc_iso(publish_at),
        "timezone": tz or _default_timezone(),
        "recurrence": recurrence if recurrence in ("none", "daily", "weekly", "custom_cron") else "none",
        "recurrence_spec": dict(recurrence_spec or {}),
        "optimal_time": bool(optimal_time),
        "expires_at": _to_utc_iso(expires_at),
    }


def new_variant(label: str, body: str, title: Optional[str] = None,
                weight: float = 0.5) -> Dict[str, Any]:
    return {
        "id": "var_" + uuid.uuid4().hex[:6],
        "label": str(label or ""),
        "body": str(body or ""),
        "title": title,
        "weight": float(weight),
    }


def new_engagement_metrics(**metrics) -> Dict[str, Any]:
    """Unified rolled-up shape (§3.1). Missing metrics stay absent — never 0."""
    out: Dict[str, Any] = {"per_platform": {}, "collected_at": None}
    for k in ("impressions", "reach", "likes", "comments", "shares", "saves",
              "clicks", "video_views", "watch_time_s", "follows_gained"):
        if k in metrics and metrics[k] is not None:
            try:
                out[k] = int(metrics[k])
            except Exception:
                pass
    if "engagement_rate" in metrics and metrics["engagement_rate"] is not None:
        try:
            out["engagement_rate"] = float(metrics["engagement_rate"])
        except Exception:
            pass
    if metrics.get("collected_at"):
        out["collected_at"] = _to_utc_iso(metrics["collected_at"])
    if isinstance(metrics.get("per_platform"), dict):
        out["per_platform"] = metrics["per_platform"]
    return out


def new_platform_target(platform: str, fmt: str = "post", **kw) -> Dict[str, Any]:
    return {
        "id": "tgt_" + uuid.uuid4().hex[:10],
        "platform": str(platform or ""),
        "format": fmt if fmt in FORMATS else "post",
        "variant_id": kw.get("variant_id"),
        "adapted_title": str(kw.get("adapted_title") or ""),
        "adapted_body": str(kw.get("adapted_body") or ""),
        "segments": list(kw.get("segments") or []),
        "adapted_assets": list(kw.get("adapted_assets") or []),
        "char_limit": int(kw.get("char_limit") or 0),
        "hashtags": list(kw.get("hashtags") or []),
        "mentions": list(kw.get("mentions") or []),
        "options": dict(kw.get("options") or {}),
        "publish_at_override": _to_utc_iso(kw.get("publish_at_override")),
        "status": "PENDING",
        "attempt": 0,
        "not_before": 0.0,
        "post_url": None,
        "platform_post_id": None,
        "error": None,
    }


def new_post(title: str = "", body: str = "", assets: Optional[list] = None,
             platforms: Optional[list] = None, schedule: Optional[dict] = None,
             variants: Optional[list] = None, source: Optional[dict] = None,
             provenance: Optional[dict] = None, license: Optional[dict] = None,
             tags: Optional[list] = None) -> Dict[str, Any]:
    """Build a ContentPost dict (status DRAFT). `platforms` accepts platform
    name strings or full target dicts from new_platform_target()."""
    targets: List[Dict[str, Any]] = []
    for p in (platforms or []):
        if isinstance(p, dict):
            targets.append(p)
        elif p:
            targets.append(new_platform_target(str(p)))
    now = _now_iso()
    return {
        "id": "post_" + uuid.uuid4().hex[:10],
        "title": str(title or ""),
        "body": str(body or ""),
        "assets": list(assets or []),
        "platforms": targets,
        "schedule": schedule or new_schedule_config(),
        "status": "DRAFT",
        "variants": list(variants or []),
        "source": dict(source or {}),
        "provenance": dict(provenance or {}),
        "license": dict(license or {}),
        "analytics": new_engagement_metrics(),
        "tags": list(tags or []),
        "created_at": now,
        "updated_at": now,
        "published_at": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Row ↔ dict hydration
# ─────────────────────────────────────────────────────────────────────────────

# Target payload fields folded into targets.payload_json (everything that is
# not a first-class column in the §3.3 schema).
_TARGET_PAYLOAD_KEYS = ("adapted_title", "adapted_body", "segments",
                        "adapted_assets", "char_limit", "hashtags", "mentions",
                        "options", "publish_at_override")


def _target_payload(target: Dict[str, Any]) -> str:
    return _dumps({k: target.get(k) for k in _TARGET_PAYLOAD_KEYS})


def _hydrate_target(row) -> Dict[str, Any]:
    d = dict(row)
    payload = _loads(d.pop("payload_json", None) or "{}", {})
    out = {
        "id": d.get("id"),
        "post_id": d.get("post_id"),
        "platform": d.get("platform"),
        "format": d.get("format"),
        "variant_id": d.get("variant_id"),
        "status": d.get("status"),
        "attempt": int(d.get("attempt") or 0),
        "not_before": float(d.get("not_before") or 0),
        "publish_at": d.get("publish_at"),
        "post_url": d.get("post_url"),
        "platform_post_id": d.get("platform_post_id"),
        "error": d.get("error"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }
    for k in _TARGET_PAYLOAD_KEYS:
        out[k] = payload.get(k)
    return out


def _hydrate_post(row, targets: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    d = dict(row)
    return {
        "id": d.get("id"),
        "title": d.get("title") or "",
        "body": d.get("body") or "",
        "status": d.get("status"),
        "schedule": _loads(d.get("schedule_json") or "{}", {}),
        "assets": _loads(d.get("assets_json") or "[]", []),
        "variants": _loads(d.get("variants_json") or "[]", []),
        "source": _loads(d.get("source_json") or "{}", {}),
        "license": _loads(d.get("license_json") or "{}", {}),
        "tags": _loads(d.get("tags_json") or "[]", []),
        "provenance_hash": d.get("provenance_hash") or "",
        "analytics": _loads(d.get("analytics_json") or "{}", {}),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
        "published_at": d.get("published_at"),
        "targets": targets if targets is not None else [],
    }


def _fetch_post_row(con, post_id: str):
    return con.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()


def _fetch_targets(con, post_id: str) -> List[Dict[str, Any]]:
    rows = con.execute(
        "SELECT * FROM targets WHERE post_id=? ORDER BY created_at, rowid",
        (post_id,)).fetchall()
    return [_hydrate_target(r) for r in rows]


def _get_post_locked(con, post_id: str) -> Optional[Dict[str, Any]]:
    row = _fetch_post_row(con, post_id)
    if not row:
        return None
    return _hydrate_post(row, _fetch_targets(con, post_id))


# ─────────────────────────────────────────────────────────────────────────────
#  Status machine internals
# ─────────────────────────────────────────────────────────────────────────────

def _set_post_status(con, post_id: str, current: str, new: str) -> bool:
    """Guarded transition. Returns False when the edge is not in the machine."""
    if new != current and new not in _POST_TRANSITIONS.get(current, set()):
        return False
    con.execute("UPDATE posts SET status=?, updated_at=? WHERE id=?",
                (new, _now_iso(), post_id))
    return True


def _rollup_post(con, post_id: str) -> Optional[str]:
    """Recompute a post's status from its target states (§3.2 / §7.1).

    Only posts in flight (PUBLISHING or HELD) roll up — a SCHEDULED post's
    PENDING targets must not flip it. Precedence: any HELD → HELD; anything
    unsettled → PUBLISHING; then all-confirmed → PUBLISHED, mixed → PARTIAL,
    none confirmed → FAILED (SKIPPED counts as dead — deliberate non-delivery
    is still non-delivery for the rollup).
    """
    row = _fetch_post_row(con, post_id)
    if not row:
        return None
    current = row["status"]
    if current not in ("PUBLISHING", "HELD"):
        return current
    statuses = [r["status"] for r in con.execute(
        "SELECT status FROM targets WHERE post_id=?", (post_id,)).fetchall()]
    if not statuses:
        return current
    if "HELD" in statuses:
        new = "HELD"
    elif any(s in _TARGET_IN_FLIGHT for s in statuses):
        new = "PUBLISHING"
    else:
        confirmed = sum(1 for s in statuses if s == "CONFIRMED")
        dead = sum(1 for s in statuses if s in ("FAILED", "SKIPPED"))
        if confirmed and not dead:
            new = "PUBLISHED"
        elif confirmed and dead:
            new = "PARTIAL"
        else:
            new = "FAILED"
    if new != current:
        _set_post_status(con, post_id, current, new)
    return new


# ─────────────────────────────────────────────────────────────────────────────
#  Post CRUD
# ─────────────────────────────────────────────────────────────────────────────

def _insert_target(con, post_id: str, target: Dict[str, Any],
                   publish_at: Optional[str]) -> None:
    now = _now_iso()
    con.execute(
        """INSERT INTO targets (id, post_id, platform, format, variant_id,
               payload_json, status, attempt, not_before, publish_at,
               post_url, platform_post_id, error, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (target["id"], post_id, target.get("platform") or "",
         target.get("format") or "post", target.get("variant_id"),
         _target_payload(target), target.get("status") or "PENDING",
         int(target.get("attempt") or 0), float(target.get("not_before") or 0),
         publish_at, target.get("post_url"), target.get("platform_post_id"),
         target.get("error"), now, now))


def _resolve_publish_at(schedule: Dict[str, Any], target: Dict[str, Any]) -> Optional[str]:
    """Target instant = per-target override, else the post's schedule instant."""
    return (_to_utc_iso(target.get("publish_at_override"))
            or _to_utc_iso((schedule or {}).get("publish_at")))


def create_post(title: str = "", body: str = "", assets: Optional[list] = None,
                platforms: Optional[list] = None, schedule: Optional[dict] = None,
                variants: Optional[list] = None, source: Optional[dict] = None,
                provenance: Optional[dict] = None, license: Optional[dict] = None,
                tags: Optional[list] = None) -> Dict[str, Any]:
    """Create a DRAFT ContentPost (+ its targets). Returns {ok, post, warnings}."""
    try:
        post = new_post(title=title, body=body, assets=assets,
                        platforms=platforms, schedule=schedule,
                        variants=variants, source=source, provenance=provenance,
                        license=license, tags=tags)
        warnings = [f"unknown platform: {t['platform']}"
                    for t in post["platforms"] if t["platform"] not in PLATFORMS]
        prov_hash = str((post["provenance"] or {}).get("content_hash") or "")
        if not prov_hash and post["assets"]:
            prov_hash = str(post["assets"][0].get("content_hash") or "")
        with _LOCK, _connect() as con:
            con.execute(
                """INSERT INTO posts (id, title, body, status, schedule_json,
                       assets_json, variants_json, source_json, license_json,
                       tags_json, provenance_hash, analytics_json,
                       created_at, updated_at, published_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (post["id"], post["title"], post["body"], post["status"],
                 _dumps(post["schedule"]), _dumps(post["assets"]),
                 _dumps(post["variants"]), _dumps(post["source"]),
                 _dumps(post["license"]), _dumps(post["tags"]), prov_hash,
                 _dumps(post["analytics"]), post["created_at"],
                 post["updated_at"], None))
            for t in post["platforms"]:
                _insert_target(con, post["id"], t,
                               _resolve_publish_at(post["schedule"], t))
            stored = _get_post_locked(con, post["id"])
        return {"ok": True, "post": stored, "warnings": warnings}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_post(post_id: str) -> Dict[str, Any]:
    try:
        with _LOCK, _connect() as con:
            post = _get_post_locked(con, post_id)
        if not post:
            return {"ok": False, "error": "post not found"}
        return {"ok": True, "post": post}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_posts(status: Optional[str] = None, platform: Optional[str] = None,
               since: Optional[str] = None, until: Optional[str] = None,
               source_kind: Optional[str] = None,
               limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """List posts newest-first. Filters: status, platform (any target),
    created_at window (since/until), source.kind."""
    try:
        clauses, params = [], []
        if status:
            clauses.append("p.status=?"); params.append(status)
        if platform:
            clauses.append(
                "EXISTS (SELECT 1 FROM targets t WHERE t.post_id=p.id AND t.platform=?)")
            params.append(platform)
        if since:
            clauses.append("p.created_at>=?"); params.append(_to_utc_iso(since) or since)
        if until:
            clauses.append("p.created_at<=?"); params.append(_to_utc_iso(until) or until)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _LOCK, _connect() as con:
            rows = con.execute(
                f"SELECT * FROM posts p {where} ORDER BY p.created_at DESC, p.id "
                f"LIMIT ? OFFSET ?", params + [int(limit), int(offset)]).fetchall()
            posts = [_hydrate_post(r, _fetch_targets(con, r["id"])) for r in rows]
        if source_kind:
            posts = [p for p in posts
                     if (p.get("source") or {}).get("kind") == source_kind]
        return {"ok": True, "posts": posts, "count": len(posts)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


_POST_EDIT_KEYS = ("title", "body", "assets", "variants", "tags", "source",
                   "license", "schedule")
# Content edits on a SCHEDULED post pull it back to DRAFT (§3.2 edit edge).
_POST_CONTENT_KEYS = {"title", "body", "assets", "variants"}


def update_post(post_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Edit a post's content/metadata. Editing a SCHEDULED post's content
    returns it to DRAFT. Terminal and in-flight posts are not editable.

    A 'schedule' patch behaves like schedule_post: the stored ScheduleConfig
    is merged + canonicalized AND every non-terminal target's publish_at is
    re-resolved — the publisher scans targets.publish_at, so rewriting only
    schedule_json would leave the post dispatching at the old instant."""
    try:
        patch = {k: v for k, v in (patch or {}).items() if k in _POST_EDIT_KEYS}
        with _LOCK, _connect() as con:
            row = _fetch_post_row(con, post_id)
            if not row:
                return {"ok": False, "error": "post not found"}
            status = row["status"]
            if status in POST_TERMINAL or status in ("PUBLISHING", "PARTIAL", "FAILED"):
                return {"ok": False, "error": f"post not editable in {status}"}
            sched: Optional[Dict[str, Any]] = None
            if "schedule" in patch:
                sched = dict(_loads(row["schedule_json"] or "{}", {}))
                if isinstance(patch["schedule"], dict):
                    sched.update(patch["schedule"])
                sched["publish_at"] = _to_utc_iso(sched.get("publish_at"))
                sched["expires_at"] = _to_utc_iso(sched.get("expires_at"))
                patch["schedule"] = sched
            sets, params = [], []
            for k in ("title", "body"):
                if k in patch:
                    sets.append(f"{k}=?"); params.append(str(patch[k] or ""))
            for k, col in (("assets", "assets_json"), ("variants", "variants_json"),
                           ("tags", "tags_json"), ("source", "source_json"),
                           ("license", "license_json"), ("schedule", "schedule_json")):
                if k in patch:
                    sets.append(f"{col}=?"); params.append(_dumps(patch[k]))
            new_status = status
            if status == "SCHEDULED" and (_POST_CONTENT_KEYS & set(patch)):
                new_status = "DRAFT"
            if sets or new_status != status:
                sets.append("status=?"); params.append(new_status)
                sets.append("updated_at=?"); params.append(_now_iso())
                params.append(post_id)
                con.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id=?", params)
            if sched is not None:
                # Keep targets.publish_at in lockstep with the stored schedule
                # (override wins, CONFIRMED targets untouched — §7.2).
                for t in _fetch_targets(con, post_id):
                    if t["status"] in TARGET_TERMINAL:
                        continue
                    con.execute(
                        "UPDATE targets SET publish_at=?, updated_at=? WHERE id=?",
                        (_resolve_publish_at(sched, t), _now_iso(), t["id"]))
            post = _get_post_locked(con, post_id)
        return {"ok": True, "post": post}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_post(post_id: str) -> Dict[str, Any]:
    """Soft delete → CANCELLED (rows are kept for the audit story).

    Unlike cancel_post (a lifecycle action), delete is the §12.6 erase
    contract — it also applies to a PUBLISHED post (the route pairs it with
    optional platform takedowns), so the terminal guard is deliberately
    bypassed for that one edge. A PUBLISHING post still cannot be deleted
    (targets may be mid-flight); deleting a CANCELLED post is idempotent."""
    res = cancel_post(post_id)
    if res.get("ok"):
        return res
    try:
        with _LOCK, _connect() as con:
            row = _fetch_post_row(con, post_id)
            if not row:
                return {"ok": False, "error": "post not found"}
            status = row["status"]
            if status == "CANCELLED":               # idempotent erase
                return {"ok": True, "post": _get_post_locked(con, post_id)}
            if status != "PUBLISHED":
                return res                          # keep cancel_post's verdict
            # §12.6 erase edge: PUBLISHED → CANCELLED, non-terminal targets
            # swept to SKIPPED. CONFIRMED targets keep their history rows.
            con.execute(
                "UPDATE targets SET status='SKIPPED', updated_at=? "
                "WHERE post_id=? AND status IN ('PENDING','HELD','FAILED')",
                (_now_iso(), post_id))
            con.execute("UPDATE posts SET status='CANCELLED', updated_at=? WHERE id=?",
                        (_now_iso(), post_id))
            post = _get_post_locked(con, post_id)
        return {"ok": True, "post": post, "erased": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def graduate_idea(item: Dict[str, Any]) -> Dict[str, Any]:
    """§3.4 one-way graduation: a legacy kanban item dict → DRAFT ContentPost.

    body = item.draft (falls back to notes), platform pre-selected from
    item.channel where it maps. The legacy JSON store is never touched here —
    the route advances the item's stage itself."""
    try:
        if not isinstance(item, dict) or not item.get("id"):
            return {"ok": False, "error": "invalid kanban item (id required)"}
        body = str(item.get("draft") or item.get("notes") or "")
        channel = str(item.get("channel") or "").strip().lower()
        platform = _CHANNEL_TO_PLATFORM.get(channel)
        schedule = None
        if item.get("scheduled_for"):
            iso = _to_utc_iso(item.get("scheduled_for"))
            if iso:
                schedule = new_schedule_config(publish_at=iso)
        res = create_post(
            title=str(item.get("title") or ""), body=body,
            platforms=[platform] if platform else [],
            schedule=schedule,
            source={"kind": "idea", "ref": str(item["id"]), "channel": channel,
                    "type": item.get("type") or ""},
            tags=list(item.get("tags") or []))
        if res.get("ok") and not platform and channel:
            res.setdefault("warnings", []).append(
                f"no platform mapping for legacy channel '{channel}'")
        return res
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Scheduling / lifecycle transitions
# ─────────────────────────────────────────────────────────────────────────────

def schedule_post(post_id: str, schedule: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """DRAFT|SCHEDULED → SCHEDULED. Stores the ScheduleConfig and resolves
    publish_at onto every non-terminal target (override wins)."""
    try:
        with _LOCK, _connect() as con:
            row = _fetch_post_row(con, post_id)
            if not row:
                return {"ok": False, "error": "post not found"}
            status = row["status"]
            if status not in ("DRAFT", "SCHEDULED"):
                return {"ok": False, "error": f"cannot schedule from {status}"}
            sched = dict(_loads(row["schedule_json"] or "{}", {}))
            if schedule:
                sched.update(schedule)
                sched["publish_at"] = _to_utc_iso(sched.get("publish_at"))
                sched["expires_at"] = _to_utc_iso(sched.get("expires_at"))
            if not sched.get("publish_at") and not sched.get("optimal_time"):
                return {"ok": False,
                        "error": "schedule needs publish_at or optimal_time"}
            con.execute("UPDATE posts SET schedule_json=?, updated_at=? WHERE id=?",
                        (_dumps(sched), _now_iso(), post_id))
            for t in _fetch_targets(con, post_id):
                if t["status"] in TARGET_TERMINAL:
                    continue
                con.execute("UPDATE targets SET publish_at=?, updated_at=? WHERE id=?",
                            (_resolve_publish_at(sched, t), _now_iso(), t["id"]))
            _set_post_status(con, post_id, status, "SCHEDULED")
            post = _get_post_locked(con, post_id)
        return {"ok": True, "post": post}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def publish_now(post_id: str) -> Dict[str, Any]:
    """DRAFT|SCHEDULED → PUBLISHING with every pending target due immediately.
    The publisher tick claims them on its next pass (still fully gated)."""
    try:
        now = _now_iso()
        with _LOCK, _connect() as con:
            row = _fetch_post_row(con, post_id)
            if not row:
                return {"ok": False, "error": "post not found"}
            status = row["status"]
            if status not in ("DRAFT", "SCHEDULED"):
                return {"ok": False, "error": f"cannot publish-now from {status}"}
            targets = _fetch_targets(con, post_id)
            if not any(t["status"] == "PENDING" for t in targets):
                return {"ok": False, "error": "no pending targets to publish"}
            con.execute(
                "UPDATE targets SET publish_at=?, not_before=0, updated_at=? "
                "WHERE post_id=? AND status='PENDING'", (now, now, post_id))
            _set_post_status(con, post_id, status, "PUBLISHING")
            post = _get_post_locked(con, post_id)
        return {"ok": True, "post": post}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cancel_post(post_id: str, target_id: Optional[str] = None) -> Dict[str, Any]:
    """Cancel a post (→ CANCELLED) or one target (→ SKIPPED, then rollup).
    A PUBLISHING post cannot be blanket-cancelled (targets may be in flight);
    PUBLISHED/CANCELLED are terminal."""
    try:
        with _LOCK, _connect() as con:
            row = _fetch_post_row(con, post_id)
            if not row:
                return {"ok": False, "error": "post not found"}
            status = row["status"]
            if target_id:
                t = con.execute("SELECT * FROM targets WHERE id=? AND post_id=?",
                                (target_id, post_id)).fetchone()
                if not t:
                    return {"ok": False, "error": "target not found"}
                if t["status"] not in ("PENDING", "HELD", "FAILED"):
                    return {"ok": False,
                            "error": f"cannot cancel target in {t['status']}"}
                con.execute("UPDATE targets SET status='SKIPPED', updated_at=? WHERE id=?",
                            (_now_iso(), target_id))
                new_status = _rollup_post(con, post_id)
                post = _get_post_locked(con, post_id)
                return {"ok": True, "post": post, "post_status": new_status}
            if status in POST_TERMINAL:
                return {"ok": False, "error": f"post is {status}"}
            if status == "PUBLISHING":
                return {"ok": False,
                        "error": "post is publishing — cancel individual targets"}
            con.execute(
                "UPDATE targets SET status='SKIPPED', updated_at=? "
                "WHERE post_id=? AND status IN ('PENDING','HELD','FAILED')",
                (_now_iso(), post_id))
            _set_post_status(con, post_id, status, "CANCELLED")
            post = _get_post_locked(con, post_id)
        return {"ok": True, "post": post}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def rearm_post(post_id: str, publish_at=None) -> Dict[str, Any]:
    """PARTIAL|FAILED → SCHEDULED, reopening only non-CONFIRMED targets
    (FAILED/SKIPPED/HELD → PENDING, error cleared, backoff reset). A CONFIRMED
    target is never touched — the double-post guarantee (§3.2, §7.2)."""
    try:
        with _LOCK, _connect() as con:
            row = _fetch_post_row(con, post_id)
            if not row:
                return {"ok": False, "error": "post not found"}
            status = row["status"]
            if status not in ("PARTIAL", "FAILED"):
                return {"ok": False, "error": f"cannot re-arm from {status}"}
            sched = _loads(row["schedule_json"] or "{}", {})
            when = _to_utc_iso(publish_at) or _to_utc_iso(sched.get("publish_at")) or _now_iso()
            if publish_at:
                sched["publish_at"] = when
                con.execute("UPDATE posts SET schedule_json=? WHERE id=?",
                            (_dumps(sched), post_id))
            reopened = []
            for t in _fetch_targets(con, post_id):
                if t["status"] == "CONFIRMED":
                    continue        # immutable — never reopened
                if t["status"] not in ("FAILED", "SKIPPED", "HELD"):
                    continue
                con.execute(
                    "UPDATE targets SET status='PENDING', error=NULL, not_before=0, "
                    "publish_at=?, updated_at=? WHERE id=?",
                    (t.get("publish_at_override") or when, _now_iso(), t["id"]))
                reopened.append(t["id"])
            if not reopened:
                return {"ok": False, "error": "no re-armable targets"}
            _set_post_status(con, post_id, status, "SCHEDULED")
            post = _get_post_locked(con, post_id)
        return {"ok": True, "post": post, "reopened": reopened}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def release_held(post_id: str, target_id: Optional[str] = None) -> Dict[str, Any]:
    """Human release of a HELD post (§3.2: HELD ──user release──▶ PUBLISHING).
    Releases one target or all HELD targets back to PENDING, due immediately.

    A released target carries options.released=True: claim_due_targets honors
    that marker even while the post itself stays HELD for its remaining holds,
    so releasing one of several held targets ('publish anyway') actually
    publishes it instead of deadlocking behind the siblings. Sticky-safety is
    unchanged for everything the user did NOT release."""
    try:
        now = _now_iso()
        with _LOCK, _connect() as con:
            row = _fetch_post_row(con, post_id)
            if not row:
                return {"ok": False, "error": "post not found"}
            if row["status"] != "HELD":
                return {"ok": False, "error": f"post is {row['status']}, not HELD"}
            to_release = [t for t in _fetch_targets(con, post_id)
                          if t["status"] == "HELD"
                          and (target_id is None or t["id"] == target_id)]
            if not to_release:
                return {"ok": False, "error": "no HELD target to release"}
            for t in to_release:
                payload = {k: t.get(k) for k in _TARGET_PAYLOAD_KEYS}
                opts = dict(payload.get("options") or {})
                opts["released"] = True          # explicit human release (§7.3)
                payload["options"] = opts
                con.execute(
                    "UPDATE targets SET status='PENDING', error=NULL, not_before=0, "
                    "publish_at=?, payload_json=?, updated_at=? WHERE id=?",
                    (now, _dumps(payload), now, t["id"]))
            remaining_held = con.execute(
                "SELECT COUNT(*) FROM targets WHERE post_id=? AND status='HELD'",
                (post_id,)).fetchone()[0]
            if not remaining_held:
                _set_post_status(con, post_id, "HELD", "PUBLISHING")
            else:
                # Partial release: the post stays HELD for its siblings, but a
                # human just acted on it — restart the HOLD_EXPIRY_DAYS clock
                # (expire_stale_holds keys off posts.updated_at) so the acked
                # released target is not swept to SKIPPED on the ORIGINAL
                # hold's schedule.
                con.execute("UPDATE posts SET updated_at=? WHERE id=?",
                            (now, post_id))
            post = _get_post_locked(con, post_id)
        return {"ok": True, "post": post}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def expire_stale_holds(now=None) -> Dict[str, Any]:
    """§3.2: HELD posts unattended for HOLD_EXPIRY_DAYS → CANCELLED (their
    non-terminal targets → SKIPPED). Returns {ok, expired: [post ids]}."""
    try:
        now_dt = _parse_instant(now) or _now_dt()
        cutoff = (now_dt - timedelta(days=HOLD_EXPIRY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        expired = []
        with _LOCK, _connect() as con:
            rows = con.execute(
                "SELECT id FROM posts WHERE status='HELD' AND updated_at<=?",
                (cutoff,)).fetchall()
            for r in rows:
                con.execute(
                    "UPDATE targets SET status='SKIPPED', updated_at=? "
                    "WHERE post_id=? AND status IN ('PENDING','HELD','FAILED')",
                    (_now_iso(), r["id"]))
                _set_post_status(con, r["id"], "HELD", "CANCELLED")
                expired.append(r["id"])
        return {"ok": True, "expired": expired, "count": len(expired)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Targets
# ─────────────────────────────────────────────────────────────────────────────

def add_targets(post_id: str, targets: List[Any]) -> Dict[str, Any]:
    """Append targets (dicts from new_platform_target, or platform names) to a
    non-terminal post. publish_at resolves from the stored schedule."""
    try:
        with _LOCK, _connect() as con:
            row = _fetch_post_row(con, post_id)
            if not row:
                return {"ok": False, "error": "post not found"}
            if row["status"] in POST_TERMINAL:
                return {"ok": False, "error": f"post is {row['status']}"}
            sched = _loads(row["schedule_json"] or "{}", {})
            added = []
            for t in (targets or []):
                if not isinstance(t, dict):
                    t = new_platform_target(str(t))
                _insert_target(con, post_id, t, _resolve_publish_at(sched, t))
                added.append(t["id"])
            con.execute("UPDATE posts SET updated_at=? WHERE id=?",
                        (_now_iso(), post_id))
            post = _get_post_locked(con, post_id)
        return {"ok": True, "post": post, "added": added}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_target(target_id: str) -> Dict[str, Any]:
    try:
        with _LOCK, _connect() as con:
            row = con.execute("SELECT * FROM targets WHERE id=?",
                              (target_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "target not found"}
        return {"ok": True, "target": _hydrate_target(row)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def update_target(target_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Patch a target's payload (composer output) and/or format/variant.
    CONFIRMED targets are immutable."""
    try:
        patch = dict(patch or {})
        with _LOCK, _connect() as con:
            row = con.execute("SELECT * FROM targets WHERE id=?",
                              (target_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "target not found"}
            if row["status"] in TARGET_TERMINAL:
                return {"ok": False, "error": "target is CONFIRMED (immutable)"}
            t = _hydrate_target(row)
            payload = {k: t.get(k) for k in _TARGET_PAYLOAD_KEYS}
            for k in _TARGET_PAYLOAD_KEYS:
                if k in patch:
                    payload[k] = patch[k]
            sets = ["payload_json=?", "updated_at=?"]
            params: List[Any] = [_dumps(payload), _now_iso()]
            if "format" in patch:
                sets.append("format=?"); params.append(patch["format"])
            if "variant_id" in patch:
                sets.append("variant_id=?"); params.append(patch["variant_id"])
            if "publish_at_override" in patch:
                sched = _loads(_fetch_post_row(con, t["post_id"])["schedule_json"] or "{}", {})
                payload["publish_at_override"] = _to_utc_iso(patch["publish_at_override"])
                params[0] = _dumps(payload)
                sets.append("publish_at=?")
                params.append(_resolve_publish_at(sched, payload))
            params.append(target_id)
            con.execute(f"UPDATE targets SET {', '.join(sets)} WHERE id=?", params)
            row = con.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
        return {"ok": True, "target": _hydrate_target(row)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_target_status(target_id: str, status: str, *,
                      error: Optional[str] = None,
                      post_url: Optional[str] = None,
                      platform_post_id: Optional[str] = None,
                      not_before: Optional[float] = None) -> Dict[str, Any]:
    """Guarded target transition + post rollup. CONFIRMED sets the post's
    published_at on first confirmation. Returns {ok, target, post_status}."""
    try:
        if status not in TARGET_STATUSES:
            return {"ok": False, "error": f"unknown target status: {status}"}
        now = _now_iso()
        with _LOCK, _connect() as con:
            row = con.execute("SELECT * FROM targets WHERE id=?",
                              (target_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "target not found"}
            current = row["status"]
            if status != current and status not in _TARGET_TRANSITIONS.get(current, set()):
                return {"ok": False,
                        "error": f"illegal target transition {current} → {status}"}
            sets = ["status=?", "updated_at=?"]
            params: List[Any] = [status, now]
            if error is not None:
                sets.append("error=?"); params.append(str(error))
            if post_url is not None:
                sets.append("post_url=?"); params.append(str(post_url))
            if platform_post_id is not None:
                sets.append("platform_post_id=?"); params.append(str(platform_post_id))
            if not_before is not None:
                sets.append("not_before=?"); params.append(float(not_before))
            params.append(target_id)
            con.execute(f"UPDATE targets SET {', '.join(sets)} WHERE id=?", params)
            post_id = row["post_id"]
            if status == "CONFIRMED":
                con.execute(
                    "UPDATE posts SET published_at=COALESCE(published_at, ?), "
                    "updated_at=? WHERE id=?", (now, now, post_id))
            post_status = _rollup_post(con, post_id)
            t_row = con.execute("SELECT * FROM targets WHERE id=?",
                                (target_id,)).fetchone()
        return {"ok": True, "target": _hydrate_target(t_row),
                "post_status": post_status}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def claim_due_targets(now=None, limit: int = 50) -> Dict[str, Any]:
    """Publisher tick claim (§6.2/§7.1): PENDING targets whose publish_at and
    not_before have both passed, on posts that are actually armed (SCHEDULED
    or PUBLISHING — a HELD post's remaining targets stay put: sticky-safe).
    The one exception on a HELD post is a target the user explicitly released
    (options.released, set by release_held): 'publish anyway' must publish
    even while sibling holds keep the post itself HELD.

    Mark-before-run: each claimed target flips to PREPARING (attempt+1) under
    the store lock before it is returned, so a second concurrent tick can
    never claim it again. Parent posts flip SCHEDULED → PUBLISHING.
    """
    try:
        now_dt = _parse_instant(now) or _now_dt()
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        now_epoch = now_dt.timestamp()
        claimed: List[Dict[str, Any]] = []
        with _LOCK, _connect() as con:
            rows = con.execute(
                """SELECT t.id, t.post_id, t.payload_json, p.status AS post_status
                   FROM targets t
                   JOIN posts p ON p.id = t.post_id
                   WHERE t.status='PENDING' AND t.publish_at IS NOT NULL
                     AND t.publish_at<=? AND t.not_before<=?
                     AND p.status IN ('SCHEDULED','PUBLISHING','HELD')
                   ORDER BY t.publish_at, t.rowid LIMIT ?""",
                (now_iso, now_epoch, int(limit))).fetchall()
            post_ids = set()
            for r in rows:
                if r["post_status"] == "HELD":
                    opts = (_loads(r["payload_json"] or "{}", {}) or {}).get("options") or {}
                    if not opts.get("released"):
                        continue            # sticky-safe: unreleased stays put
                cur = con.execute(
                    "UPDATE targets SET status='PREPARING', attempt=attempt+1, "
                    "updated_at=? WHERE id=? AND status='PENDING'",
                    (now_iso, r["id"]))
                if cur.rowcount:            # mark-before-run won the row
                    claimed.append(r["id"])
                    post_ids.add(r["post_id"])
            for pid in post_ids:
                con.execute(
                    "UPDATE posts SET status='PUBLISHING', updated_at=? "
                    "WHERE id=? AND status='SCHEDULED'", (now_iso, pid))
            out = []
            for tid in claimed:
                t_row = con.execute("SELECT * FROM targets WHERE id=?",
                                    (tid,)).fetchone()
                if t_row:
                    out.append(_hydrate_target(t_row))
        return {"ok": True, "targets": out, "count": len(out)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def find_recurrence_clone(parent_id: str, publish_at: str) -> Dict[str, Any]:
    """§6.7 idempotency probe: does a recurrence clone of `parent_id` armed
    for `publish_at` already exist? SQL-side match over ALL posts — the
    list_posts pagination (SQL LIMIT before the Python source_kind filter)
    must never blind this check; a missed clone forks the series into
    permanent double-posting."""
    try:
        with _LOCK, _connect() as con:
            rows = con.execute(
                "SELECT id, source_json, schedule_json FROM posts "
                "WHERE source_json LIKE ?",
                (f'%{parent_id}%',)).fetchall()
        for r in rows:
            try:
                src = json.loads(r["source_json"] or "{}")
                sch = json.loads(r["schedule_json"] or "{}")
            except Exception:
                continue
            if (src.get("kind") == "recurrence"
                    and src.get("ref") == parent_id
                    and sch.get("publish_at") == publish_at):
                return {"ok": True, "found": True, "post_id": r["id"]}
        return {"ok": True, "found": False}
    except Exception as e:
        return {"ok": False, "error": str(e), "found": False}


def defer_target(target_id: str, not_before: float) -> Dict[str, Any]:
    """Budget deferral (§7.1 step 5): PREPARING → PENDING with a backoff gate,
    and the claim's attempt is refunded — a deferral burns no attempt."""
    try:
        with _LOCK, _connect() as con:
            row = con.execute("SELECT * FROM targets WHERE id=?",
                              (target_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "target not found"}
            if row["status"] != "PREPARING":
                return {"ok": False,
                        "error": f"can only defer a PREPARING target (is {row['status']})"}
            con.execute(
                "UPDATE targets SET status='PENDING', not_before=?, "
                "attempt=MAX(attempt-1, 0), updated_at=? WHERE id=?",
                (float(not_before), _now_iso(), target_id))
            t_row = con.execute("SELECT * FROM targets WHERE id=?",
                                (target_id,)).fetchone()
        return {"ok": True, "target": _hydrate_target(t_row)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


STALE_CLAIM_S = 15 * 60               # a PREPARING claim older than this with
                                      # no live worker is a crashed run


def recover_stale_claims(now=None, stale_after_s: float = STALE_CLAIM_S,
                         exclude=None) -> Dict[str, Any]:
    """Mark-before-run crash recovery (§7.1/§7.2): a target claimed PREPARING
    normally only leaves via the worker that claimed it. If the process died
    between claim and publish, the target — and its post, stuck PUBLISHING —
    would be unpublishable, uncancellable and un-re-armable forever. Flip
    stale claims back to PENDING (the burned attempt stands, so the retry cap
    still bounds redispatch); `exclude` is the publisher's live running set so
    genuinely in-flight work is never yanked. Returns {ok, recovered, count}."""
    try:
        now_dt = _parse_instant(now) or _now_dt()
        cutoff = (now_dt - timedelta(seconds=max(60.0, float(stale_after_s)))
                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
        excl = {str(x) for x in (exclude or [])}
        recovered: List[str] = []
        with _LOCK, _connect() as con:
            rows = con.execute(
                "SELECT id FROM targets WHERE status='PREPARING' AND updated_at<=?",
                (cutoff,)).fetchall()
            for r in rows:
                if r["id"] in excl:
                    continue
                cur = con.execute(
                    "UPDATE targets SET status='PENDING', updated_at=?, error=? "
                    "WHERE id=? AND status='PREPARING'",
                    (now_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "recovered from stale PREPARING claim (interrupted run)",
                     r["id"]))
                if cur.rowcount:
                    recovered.append(r["id"])
        return {"ok": True, "recovered": recovered, "count": len(recovered)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_stale_sent_targets(now=None, stale_after_s: float = STALE_CLAIM_S,
                            exclude=None) -> Dict[str, Any]:
    """SENT targets whose claim looks abandoned — the process died between
    the adapter call and confirm/fail, so the publish may or may not have
    landed platform-side (§7.2 restart-time verify). READ-ONLY by design:
    a SENT target is never blind-flipped back to PENDING here (that would
    reopen the double-post window); the publisher resolves each one through
    the adapter's verify probe. `exclude` = the publisher's live running
    set. Returns {ok, targets, count}."""
    try:
        now_dt = _parse_instant(now) or _now_dt()
        cutoff = (now_dt - timedelta(seconds=max(60.0, float(stale_after_s)))
                  ).strftime("%Y-%m-%dT%H:%M:%SZ")
        excl = {str(x) for x in (exclude or [])}
        with _LOCK, _connect() as con:
            rows = con.execute(
                "SELECT * FROM targets WHERE status='SENT' AND updated_at<=?",
                (cutoff,)).fetchall()
        targets = [_hydrate_target(r) for r in rows if r["id"] not in excl]
        return {"ok": True, "targets": targets, "count": len(targets)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Engagement snapshots + rollup (§8.3)
# ─────────────────────────────────────────────────────────────────────────────

_METRIC_KEYS = ("impressions", "reach", "likes", "comments", "shares", "saves",
                "clicks", "video_views", "watch_time_s", "follows_gained")


def _sanitize_metrics(metrics: Dict[str, Any]) -> Dict[str, int]:
    """Type-coerce the unified keys; drop everything else. Missing ≠ zero —
    absent keys stay absent (§8.2 honesty rule)."""
    out: Dict[str, int] = {}
    for k in _METRIC_KEYS:
        if k in (metrics or {}) and metrics[k] is not None:
            try:
                out[k] = int(metrics[k])
            except Exception:
                continue
    return out


def _sanitize_raw(raw) -> Optional[str]:
    """Untrusted-input discipline (§8.6): JSON only, size-capped."""
    if raw is None:
        return None
    try:
        text = _dumps(raw)
    except Exception:
        return None
    if len(text.encode("utf-8", errors="replace")) > _RAW_CAP_BYTES:
        return _dumps({"_truncated": True, "_bytes": len(text)})
    return text


def insert_engagement_snapshot(target_id: str, metrics: Dict[str, Any],
                               raw: Optional[dict] = None,
                               collected_at=None) -> Dict[str, Any]:
    """Append one normalized snapshot row for a target and refresh the post's
    denormalized analytics rollup. Returns {ok, snapshot_id, analytics}."""
    try:
        norm = _sanitize_metrics(metrics)
        when = _to_utc_iso(collected_at) or _now_iso()
        with _LOCK, _connect() as con:
            row = con.execute("SELECT * FROM targets WHERE id=?",
                              (target_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "target not found"}
            snap_id = "snap_" + uuid.uuid4().hex[:10]
            con.execute(
                """INSERT INTO engagement_snapshots
                       (id, target_id, post_id, platform, metrics_json,
                        raw_json, collected_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (snap_id, target_id, row["post_id"], row["platform"],
                 _dumps(norm), _sanitize_raw(raw), when))
            analytics = _refresh_analytics_locked(con, row["post_id"])
        return {"ok": True, "snapshot_id": snap_id, "analytics": analytics}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_snapshots(target_id: Optional[str] = None, post_id: Optional[str] = None,
                  limit: int = 100) -> Dict[str, Any]:
    try:
        clauses, params = [], []
        if target_id:
            clauses.append("target_id=?"); params.append(target_id)
        if post_id:
            clauses.append("post_id=?"); params.append(post_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _LOCK, _connect() as con:
            rows = con.execute(
                f"SELECT * FROM engagement_snapshots {where} "
                f"ORDER BY collected_at DESC LIMIT ?",
                params + [int(limit)]).fetchall()
        snaps = []
        for r in rows:
            d = dict(r)
            d["metrics"] = _loads(d.pop("metrics_json", None) or "{}", {})
            d["raw"] = _loads(d.pop("raw_json", None) or "null", None)
            snaps.append(d)
        return {"ok": True, "snapshots": snaps, "count": len(snaps)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _refresh_analytics_locked(con, post_id: str) -> Dict[str, Any]:
    """Rebuild posts.analytics_json from each target's *latest* snapshot:
    per-platform sums + unified totals + honest engagement_rate (§8.2)."""
    rows = con.execute(
        """SELECT s.* FROM engagement_snapshots s
           JOIN (SELECT target_id, MAX(collected_at) AS mc
                 FROM engagement_snapshots WHERE post_id=?
                 GROUP BY target_id) latest
             ON latest.target_id = s.target_id AND latest.mc = s.collected_at
           WHERE s.post_id=?""", (post_id, post_id)).fetchall()
    totals: Dict[str, int] = {}
    per_platform: Dict[str, Dict[str, Any]] = {}
    latest_at = None
    seen_targets = set()
    for r in rows:
        if r["target_id"] in seen_targets:   # two snaps at the same instant
            continue
        seen_targets.add(r["target_id"])
        m = _loads(r["metrics_json"] or "{}", {})
        for k, v in m.items():
            if k in _METRIC_KEYS:
                totals[k] = totals.get(k, 0) + int(v)
        plat = r["platform"]
        bucket = per_platform.setdefault(plat, {"metrics": {}, "collected_at": None})
        for k, v in m.items():
            if k in _METRIC_KEYS:
                bucket["metrics"][k] = bucket["metrics"].get(k, 0) + int(v)
        if not bucket["collected_at"] or r["collected_at"] > bucket["collected_at"]:
            bucket["collected_at"] = r["collected_at"]
        if not latest_at or r["collected_at"] > latest_at:
            latest_at = r["collected_at"]
    numerator = sum(totals.get(k, 0) for k in
                    ("likes", "comments", "shares", "saves", "clicks"))
    denominator = totals.get("impressions") or totals.get("video_views") or 1
    analytics = dict(totals)
    analytics["engagement_rate"] = round(numerator / max(denominator, 1), 6)
    analytics["collected_at"] = latest_at
    analytics["per_platform"] = per_platform
    con.execute("UPDATE posts SET analytics_json=?, updated_at=? WHERE id=?",
                (_dumps(analytics), _now_iso(), post_id))
    return analytics


def refresh_post_analytics(post_id: str) -> Dict[str, Any]:
    try:
        with _LOCK, _connect() as con:
            if not _fetch_post_row(con, post_id):
                return {"ok": False, "error": "post not found"}
            analytics = _refresh_analytics_locked(con, post_id)
        return {"ok": True, "analytics": analytics}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Best-times histogram (§6.4 learned layer)
# ─────────────────────────────────────────────────────────────────────────────

def upsert_best_time(platform: str, weekday: int, hour: int,
                     score: float, samples: int) -> Dict[str, Any]:
    try:
        weekday, hour = int(weekday), int(hour)
        if not (0 <= weekday <= 6 and 0 <= hour <= 23):
            return {"ok": False, "error": "weekday 0-6 / hour 0-23 required"}
        with _LOCK, _connect() as con:
            con.execute(
                """INSERT INTO best_times (platform, weekday, hour, score,
                                           samples, updated_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(platform, weekday, hour)
                   DO UPDATE SET score=excluded.score, samples=excluded.samples,
                                 updated_at=excluded.updated_at""",
                (str(platform), weekday, hour, float(score), int(samples),
                 _now_iso()))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_best_times(platform: Optional[str] = None,
                   min_samples: int = 0) -> Dict[str, Any]:
    try:
        clauses, params = [], []
        if platform:
            clauses.append("platform=?"); params.append(platform)
        if min_samples:
            clauses.append("samples>=?"); params.append(int(min_samples))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _LOCK, _connect() as con:
            rows = con.execute(
                f"SELECT * FROM best_times {where} ORDER BY score DESC",
                params).fetchall()
        return {"ok": True, "best_times": [dict(r) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  ψ awards (§8.7) — idempotent per {target}:{metric}:{threshold}
# ─────────────────────────────────────────────────────────────────────────────

def award_psi(target_id: str, metric: str, threshold: int,
              amount_mpsi: int) -> Dict[str, Any]:
    """Check-and-insert an engagement→ψ award key. `awarded` is True only for
    the first crossing — a re-poll storm can never double-mint. The caller
    (analytics collector) calls economy.earn() only when awarded is True."""
    try:
        award_key = f"{target_id}:{metric}:{int(threshold)}"
        with _LOCK, _connect() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO psi_awards (key, amount_mpsi, awarded_at) "
                "VALUES (?,?,?)", (award_key, int(amount_mpsi), _now_iso()))
            awarded = bool(cur.rowcount)
        return {"ok": True, "awarded": awarded, "key": award_key}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_psi_awards(target_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        with _LOCK, _connect() as con:
            if target_id:
                rows = con.execute(
                    "SELECT * FROM psi_awards WHERE key LIKE ? ORDER BY awarded_at",
                    (f"{target_id}:%",)).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM psi_awards ORDER BY awarded_at").fetchall()
        return {"ok": True, "awards": [dict(r) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Publish log (~/.friday/content/publish_log.jsonl, §3.3)
# ─────────────────────────────────────────────────────────────────────────────

def append_publish_log(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Append one publish-attempt line; rotate like schedule_runs.jsonl."""
    try:
        rec = dict(entry or {})
        rec.setdefault("ts", _now_iso())
        with _LOG_LOCK:
            PUBLISH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(PUBLISH_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            # Rotate: keep only the last PUBLISH_LOG_KEEP lines.
            lines = PUBLISH_LOG.read_text(encoding="utf-8").splitlines()
            if len(lines) > PUBLISH_LOG_KEEP:
                PUBLISH_LOG.write_text(
                    "\n".join(lines[-PUBLISH_LOG_KEEP:]) + "\n",
                    encoding="utf-8")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def read_publish_log(limit: int = 50, target_id: Optional[str] = None,
                     post_id: Optional[str] = None) -> Dict[str, Any]:
    """Recent publish-log entries, newest first, optionally filtered."""
    try:
        try:
            lines = PUBLISH_LOG.read_text(encoding="utf-8").splitlines()
        except Exception:
            return {"ok": True, "entries": []}
        out = []
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if target_id and rec.get("target_id") != target_id:
                continue
            if post_id and rec.get("post_id") != post_id:
                continue
            out.append(rec)
            if len(out) >= int(limit):
                break
        return {"ok": True, "entries": out}
    except Exception as e:
        return {"ok": False, "error": str(e)}
