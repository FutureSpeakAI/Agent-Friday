"""
Agent Friday — Content Pipeline API Routes (docs/CONTENT_PIPELINE_SPEC.md §11)
FutureSpeak.AI · Asimov's Mind

/api/content/* v2 — the social-media pipeline surface. The legacy
/api/content/{pipeline,idea,draft,templates,from-template,item,drafts} routes
in routes/workflows.py stay untouched (Ideas tab).

All responses are JSON envelopes ({ok|status, ...}) with errors in-body at
HTTP 200 (creations-route convention). The single exception is the OAuth
loopback callback, which is a browser redirect back into the app.

| Route                                        | Method            |
|----------------------------------------------|-------------------|
| /api/content/posts                           | GET / POST        |
| /api/content/posts/<id>                      | GET / PATCH / DELETE |
| /api/content/posts/<id>/compose              | POST              |
| /api/content/posts/<id>/schedule             | POST              |
| /api/content/posts/<id>/publish-now          | POST              |
| /api/content/posts/<id>/cancel               | POST              |
| /api/content/posts/<id>/release              | POST (ack gate)   |
| /api/content/preview                         | POST              |
| /api/content/repurpose                       | POST              |
| /api/content/queue                           | GET               |
| /api/content/calendar                        | GET               |
| /api/content/best-times                      | GET               |
| /api/content/analytics/summary               | GET               |
| /api/content/analytics/post/<id>             | GET               |
| /api/content/analytics/refresh/<target_id>   | POST              |
| /api/content/insights                        | GET               |
| /api/content/platforms                       | GET               |
| /api/content/platforms/<name>/connect        | POST              |
| /api/content/platforms/<name>/callback       | GET (redirect)    |
| /api/content/platforms/<name>                | DELETE            |
| /api/content/platforms/<name>/test           | POST              |
| /api/content/voice-cards/<platform>          | GET / POST        |
| /api/content/export                          | GET               |
"""
from __future__ import annotations

import threading
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, redirect, request

import agent_friday.core as core
from agent_friday.services import content_pipeline as store
from agent_friday.services import content_composer as composer
from agent_friday.services import platforms as platform_registry

content_pipeline_bp = Blueprint("content_pipeline", __name__)

# Where the OAuth callback drops the user afterwards — the Accounts tab of the
# Content workspace (useNavTarget deep-link params; the SPA resolves them).
_ACCOUNTS_DEEPLINK = "/?workspace=content&tab=accounts"

# ── OAuth state registry (§12.2: state bound + single-use, loopback only) ────
_OAUTH_STATES: Dict[str, Dict[str, Any]] = {}
_OAUTH_LOCK = threading.Lock()
_OAUTH_TTL_S = 900                     # 15 min, matching google_accounts.py


def _issue_state(platform: str) -> str:
    state = uuid.uuid4().hex
    with _OAUTH_LOCK:
        # prune stale entries
        cutoff = _time.time() - _OAUTH_TTL_S
        for s in [k for k, v in _OAUTH_STATES.items() if v.get("ts", 0) < cutoff]:
            _OAUTH_STATES.pop(s, None)
        _OAUTH_STATES[state] = {"platform": platform, "ts": _time.time()}
    return state


def _consume_state(state: Optional[str], platform: str) -> bool:
    """Single-use state check: pops the entry; True only when it existed,
    is fresh, and was issued for this platform."""
    if not state:
        return False
    with _OAUTH_LOCK:
        rec = _OAUTH_STATES.pop(state, None)
    if not rec:
        return False
    if rec.get("platform") != platform:
        return False
    return (_time.time() - rec.get("ts", 0)) <= _OAUTH_TTL_S


# ── small helpers ─────────────────────────────────────────────────────────────

def _json() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def _content_settings() -> Dict[str, Any]:
    try:
        c = (core._load_settings() or {}).get("content") or {}
    except Exception:
        c = {}
    return {
        "enabled": bool(c.get("enabled", True)),
        "conflict_window_hours": float(c.get("conflict_window_hours", 2)),
        "psi_daily_cap": int(c.get("psi_daily_cap", 200)),
        "staging_base_url": str(c.get("staging_base_url") or ""),
    }


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _kick_publisher() -> Dict[str, Any]:
    """Best-effort immediate dispatch after publish-now / release. The
    publisher tick (§6.2) is authoritative; kick() runs a one-shot background
    tick so the HTTP response never blocks on platform I/O (and stays inert
    under FRIDAY_TESTING — tests drive tick() directly)."""
    try:
        from agent_friday.services import publisher as _pub
    except Exception:
        return {"kicked": False, "reason": "publisher not available"}
    kick = getattr(_pub, "kick", None)
    if callable(kick):
        try:
            res = kick()
            return res if isinstance(res, dict) else {"kicked": bool(res)}
        except Exception as e:
            return {"kicked": False, "reason": str(e)}
    for fn_name in ("tick", "run_once", "dispatch_due"):
        fn = getattr(_pub, fn_name, None)
        if callable(fn):
            try:
                res = fn()
                return {"kicked": True, "via": fn_name,
                        "result": res if isinstance(res, dict) else None}
            except Exception as e:
                return {"kicked": False, "reason": str(e)}
    return {"kicked": False, "reason": "publisher has no tick entry point"}


def _coerce_assets(raw: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in (raw or []):
        if isinstance(a, dict):
            out.append(store.new_asset_ref(
                a.get("filename") or "", a.get("content_hash") or "",
                a.get("kind") or "image", a.get("alt_text") or "",
                a.get("duration_s")))
        elif a:
            out.append(store.new_asset_ref(str(a)))
    return out


def _coerce_schedule(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    return store.new_schedule_config(
        publish_at=raw.get("publish_at"),
        tz=raw.get("timezone"),
        recurrence=raw.get("recurrence") or "none",
        recurrence_spec=raw.get("recurrence_spec"),
        optimal_time=bool(raw.get("optimal_time")),
        expires_at=raw.get("expires_at"))


# ── Optimal-time seed tables (§6.4 seed layer; learned layer outranks) ───────
# (weekday, hour) in the POST's timezone; weekday 0=Monday.
_SEED_BEST_TIMES: Dict[str, List[tuple]] = {
    "linkedin":  [(wd, h) for wd in (1, 2, 3) for h in (8, 9, 10, 11)],
    "twitter":   [(wd, h) for wd in range(5) for h in (9, 10, 11, 12)],
    "instagram": [(wd, h) for wd in range(7) for h in (11, 12, 13, 19, 20, 21)],
    "youtube":   ([(3, h) for h in (14, 15, 16)] + [(4, h) for h in (14, 15, 16)]
                  + [(5, 9), (5, 10), (6, 9), (6, 10)]),
    "tiktok":    [(wd, h) for wd in range(7) for h in (18, 19, 20, 21, 22)],
    "reddit":    [(wd, h) for wd in range(5) for h in (7, 8, 9, 10)],
    "mastodon":  [(wd, h) for wd in range(7) for h in (18, 19, 20, 21, 22)],
    "bluesky":   [(wd, h) for wd in range(7) for h in (18, 19, 20, 21, 22)],
    "substack":  [(wd, h) for wd in (1, 2, 3) for h in (9, 10)],
    "medium":    [(wd, h) for wd in (1, 2, 3) for h in (9, 10)],
    "federation": [(wd, 9) for wd in range(7)],
}
_LEARNED_MIN_SAMPLES = 5               # a learned cell outranks seed at n≥5


def _learned_cells(platform: str) -> List[tuple]:
    """Learned (weekday, hour) cells with enough samples, best-first."""
    res = store.get_best_times(platform=platform,
                               min_samples=_LEARNED_MIN_SAMPLES)
    cells = []
    for row in (res.get("best_times") or []):
        try:
            cells.append((int(row["weekday"]), int(row["hour"])))
        except Exception:
            continue
    return cells


def _tzinfo(tz_name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _good_cells(platforms: List[str]) -> set:
    cells: set = set()
    for plat in platforms or ["linkedin"]:
        learned = _learned_cells(plat)
        cells.update(learned or _SEED_BEST_TIMES.get(plat, []))
    return cells


def _scheduled_instants(platforms: List[str]) -> List[Any]:
    """Existing armed target instants on the given platforms (conflict fuel)."""
    out = []
    for st in ("SCHEDULED", "PUBLISHING"):
        res = store.list_posts(status=st, limit=500)
        for p in (res.get("posts") or []):
            for t in (p.get("targets") or []):
                if t.get("platform") not in platforms:
                    continue
                dt = store._parse_instant(t.get("publish_at"))
                if dt:
                    out.append(dt)
    return out


def _resolve_optimal(platforms: List[str], tz_name: str,
                     window_hours: float) -> Optional[str]:
    """§6.4 resolver: next slot ≥15 min ahead whose (weekday, hour) — in the
    post's timezone — is a learned (n≥5) or seed cell, and that clears the
    same-platform conflict window. Falls back to now+1h."""
    tz = _tzinfo(tz_name or "UTC")
    good = _good_cells(platforms)
    taken = _scheduled_instants(platforms)
    window = timedelta(hours=max(0.0, float(window_hours)))
    start = _now_dt() + timedelta(minutes=15)
    cand = start.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    if cand < start.astimezone(tz):
        cand += timedelta(hours=1)
    for _ in range(24 * 14):           # scan two weeks, hour by hour
        if ((cand.weekday(), cand.hour) in good
                and not any(abs(other - cand) <= window for other in taken)):
            return cand.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cand += timedelta(hours=1)
    return (_now_dt() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_conflicts(platforms: List[str], publish_at_iso: Optional[str],
                    window_hours: float,
                    exclude_post: Optional[str]) -> List[Dict[str, Any]]:
    """§6.8 conflict detection: another target on the SAME platform within the
    window. Warnings only — the caller never blocks on them."""
    when = store._parse_instant(publish_at_iso)
    if not when or not platforms:
        return []
    window = timedelta(hours=max(0.0, float(window_hours)))
    conflicts: List[Dict[str, Any]] = []
    for st in ("SCHEDULED", "PUBLISHING"):
        res = store.list_posts(status=st, limit=500)
        for p in (res.get("posts") or []):
            if exclude_post and p.get("id") == exclude_post:
                continue
            for t in (p.get("targets") or []):
                if t.get("platform") not in platforms:
                    continue
                other = store._parse_instant(t.get("publish_at"))
                if not other:
                    continue
                if abs(other - when) <= window:
                    conflicts.append({
                        "platform": t.get("platform"),
                        "post_id": p.get("id"),
                        "target_id": t.get("id"),
                        "publish_at": t.get("publish_at"),
                        "title": p.get("title") or "",
                    })
    return conflicts


# ═════════════════════════════════════════════════════════════════════════════
#  Posts CRUD
# ═════════════════════════════════════════════════════════════════════════════

@content_pipeline_bp.route("/api/content/posts", methods=["GET"])
def content_posts_list():
    args = request.args
    res = store.list_posts(
        status=args.get("status") or None,
        platform=args.get("platform") or None,
        since=args.get("from") or None,
        until=args.get("to") or None,
        source_kind=args.get("source") or None,
        limit=int(args.get("limit") or 50),
        offset=int(args.get("offset") or 0))
    return jsonify(res)


@content_pipeline_bp.route("/api/content/posts", methods=["POST"])
def content_posts_create():
    data = _json()
    res = store.create_post(
        title=str(data.get("title") or ""),
        body=str(data.get("body") or ""),
        assets=_coerce_assets(data.get("assets")),
        platforms=data.get("platforms") or [],
        schedule=_coerce_schedule(data.get("schedule")),
        variants=data.get("variants") or [],
        source=data.get("source") or {},
        provenance=data.get("provenance") or {},
        license=data.get("license") or {},
        tags=data.get("tags") or [])
    return jsonify(res)


@content_pipeline_bp.route("/api/content/posts/<post_id>", methods=["GET"])
def content_post_get(post_id):
    return jsonify(store.get_post(post_id))


@content_pipeline_bp.route("/api/content/posts/<post_id>", methods=["PATCH"])
def content_post_patch(post_id):
    return jsonify(store.update_post(post_id, _json()))


@content_pipeline_bp.route("/api/content/posts/<post_id>", methods=["DELETE"])
def content_post_delete(post_id):
    """Delete = CANCELLED (+ optional best-effort platform takedowns of
    already-CONFIRMED targets via ?takedown=1, §12.6 erase). The local
    delete runs FIRST — irreversible platform-side takedowns must never fire
    for a post the store then refuses to erase (the old order could delete
    the remote copies and still answer ok:false with status=PUBLISHED)."""
    # Snapshot the confirmed targets before the erase sweeps their statuses.
    got = store.get_post(post_id)
    confirmed = [t for t in ((got.get("post") or {}).get("targets") or [])
                 if t.get("status") == "CONFIRMED" and t.get("platform_post_id")]
    out = store.delete_post(post_id)
    if not out.get("ok"):
        return jsonify(out)
    takedowns: List[Dict[str, Any]] = []
    if request.args.get("takedown") in ("1", "true", "yes"):
        for t in confirmed:
            adapter = platform_registry.get_adapter(t.get("platform") or "")
            if adapter is None:
                takedowns.append({"target_id": t["id"], "ok": False,
                                  "error": "adapter unavailable"})
                continue
            try:
                res = adapter.delete(t["platform_post_id"])
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            res = res if isinstance(res, dict) else {"ok": False}
            takedowns.append({"target_id": t["id"], **res})
            store.append_publish_log({
                "event": "takedown", "post_id": post_id,
                "target_id": t["id"], "platform": t.get("platform"),
                "ok": bool(res.get("ok"))})
    if takedowns:
        out["takedowns"] = takedowns
    return jsonify(out)


# ═════════════════════════════════════════════════════════════════════════════
#  Compose / schedule / lifecycle
# ═════════════════════════════════════════════════════════════════════════════

@content_pipeline_bp.route("/api/content/posts/<post_id>/compose", methods=["POST"])
def content_post_compose(post_id):
    data = _json()
    got = store.get_post(post_id)
    if not got.get("ok"):
        return jsonify(got)
    res = composer.adapt(
        got["post"],
        platforms=data.get("platforms") or None,
        formats=data.get("formats") or None,
        regenerate=bool(data.get("regenerate")))
    if res.get("ok"):
        refreshed = store.get_post(post_id)
        if refreshed.get("ok"):
            res["post"] = refreshed["post"]
    return jsonify(res)


@content_pipeline_bp.route("/api/content/posts/<post_id>/schedule", methods=["POST"])
def content_post_schedule(post_id):
    """Set/replace the ScheduleConfig (§11): resolves optimal_time through the
    best-times engine and returns same-platform conflicts as warnings."""
    data = _json()
    got = store.get_post(post_id)
    if not got.get("ok"):
        return jsonify(got)
    post = got["post"]
    platforms = [t.get("platform") for t in (post.get("targets") or [])
                 if t.get("platform")]
    sched = _coerce_schedule(data) or store.new_schedule_config()
    cs = _content_settings()
    resolved_optimal = False
    if sched.get("optimal_time") and not sched.get("publish_at"):
        sched["publish_at"] = _resolve_optimal(
            platforms, sched.get("timezone") or "", cs["conflict_window_hours"])
        resolved_optimal = sched["publish_at"] is not None
    res = store.schedule_post(post_id, sched)
    if not res.get("ok"):
        return jsonify(res)
    res["conflicts"] = _find_conflicts(
        platforms, sched.get("publish_at"), cs["conflict_window_hours"],
        exclude_post=post_id)
    res["resolved_optimal"] = resolved_optimal
    return jsonify(res)


@content_pipeline_bp.route("/api/content/posts/<post_id>/publish-now", methods=["POST"])
def content_post_publish_now(post_id):
    """Immediate dispatch — still fully gated: the publisher runs the H1-H4
    scan and the egress classifier on every claimed target (§7.1)."""
    res = store.publish_now(post_id)
    if res.get("ok"):
        res["dispatch"] = _kick_publisher()
    return jsonify(res)


@content_pipeline_bp.route("/api/content/posts/<post_id>/cancel", methods=["POST"])
def content_post_cancel(post_id):
    data = _json()
    return jsonify(store.cancel_post(post_id, target_id=data.get("target_id")))


@content_pipeline_bp.route("/api/content/posts/<post_id>/release", methods=["POST"])
def content_post_release(post_id):
    """Release a HELD target after human review (§7.3). Requires an explicit
    `ack: true` — 'publish anyway, this is intentional' is a deliberate act."""
    data = _json()
    if data.get("ack") is not True:
        return jsonify({"ok": False,
                        "error": "release requires ack: true — review the "
                                 "flagged content before publishing"})
    res = store.release_held(post_id, target_id=data.get("target_id"))
    if res.get("ok"):
        res["dispatch"] = _kick_publisher()
    return jsonify(res)


# ═════════════════════════════════════════════════════════════════════════════
#  Preview / repurpose
# ═════════════════════════════════════════════════════════════════════════════

@content_pipeline_bp.route("/api/content/preview", methods=["POST"])
def content_preview():
    data = _json()
    return jsonify(composer.preview(
        str(data.get("body") or ""),
        _coerce_assets(data.get("assets")),
        str(data.get("platform") or ""),
        str(data.get("format") or "")))


# §9.2 default spread per dominant asset kind (each entry = one target).
_DEFAULT_SPREADS: Dict[str, List[str]] = {
    "text":  ["linkedin", "twitter", "bluesky", "mastodon", "substack",
              "medium", "federation"],
    "image": ["instagram", "twitter", "linkedin", "bluesky", "mastodon",
              "federation"],
    "video": ["youtube", "instagram", "tiktok", "twitter", "bluesky",
              "mastodon", "federation"],
    "audio": ["federation"],
}


@content_pipeline_bp.route("/api/content/repurpose", methods=["POST"])
def content_repurpose():
    """Source → spread of drafts (§9). Body: {source: {kind, ref}, spread?,
    body?, title?, assets?}. When source.kind == "post" the body/title/assets
    come from the referenced ContentPost; otherwise the caller supplies them."""
    data = _json()
    source = data.get("source") or {}
    body = str(data.get("body") or "")
    title = str(data.get("title") or "")
    assets = _coerce_assets(data.get("assets"))
    tags = list(data.get("tags") or [])
    if not body and source.get("kind") == "post" and source.get("ref"):
        got = store.get_post(str(source["ref"]))
        if not got.get("ok"):
            return jsonify(got)
        src_post = got["post"]
        body = src_post.get("body") or ""
        title = title or src_post.get("title") or ""
        assets = assets or list(src_post.get("assets") or [])
        tags = tags or list(src_post.get("tags") or [])
    if not body.strip():
        return jsonify({"ok": False,
                        "error": "repurpose needs content — pass body, or "
                                 "source: {kind: 'post', ref: <post_id>}"})
    spread = data.get("spread")
    if not spread or spread == "default":
        kinds = [a.get("kind") for a in assets]
        dominant = ("video" if "video" in kinds else
                    "image" if "image" in kinds else
                    "audio" if "audio" in kinds else "text")
        spread = _DEFAULT_SPREADS[dominant]
    spread = [str(p).strip().lower() for p in spread if p]
    created = store.create_post(
        title=title, body=body, assets=assets, platforms=spread,
        source={"kind": "repurpose",
                "ref": str(source.get("ref") or ""),
                "src_kind": str(source.get("kind") or "")},
        tags=tags)
    if not created.get("ok"):
        return jsonify(created)
    post = created["post"]
    adapted = composer.adapt(post, platforms=spread)
    refreshed = store.get_post(post["id"])
    return jsonify({
        "ok": True,
        "post": refreshed.get("post") if refreshed.get("ok") else post,
        "spread": spread,
        "warnings": (created.get("warnings") or []) + (adapted.get("warnings") or []),
    })


# ═════════════════════════════════════════════════════════════════════════════
#  Queue / calendar / best-times
# ═════════════════════════════════════════════════════════════════════════════

def _target_row(post: Dict[str, Any], t: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "target_id": t.get("id"),
        "post_id": post.get("id"),
        "title": post.get("title") or "",
        "platform": t.get("platform"),
        "format": t.get("format"),
        "variant_id": t.get("variant_id"),
        "status": t.get("status"),
        "publish_at": t.get("publish_at"),
        "attempt": t.get("attempt"),
        "error": t.get("error"),
        "post_url": t.get("post_url"),
        "timezone": (post.get("schedule") or {}).get("timezone"),
    }


@content_pipeline_bp.route("/api/content/queue", methods=["GET"])
def content_queue():
    """§6.6: upcoming targets + held (pinned) + recent publish history."""
    upcoming: List[Dict[str, Any]] = []
    held: List[Dict[str, Any]] = []
    for st in ("SCHEDULED", "PUBLISHING", "HELD"):
        res = store.list_posts(status=st, limit=500)
        for p in (res.get("posts") or []):
            for t in (p.get("targets") or []):
                if t.get("status") == "HELD":
                    held.append(_target_row(p, t))
                elif t.get("status") in ("PENDING", "PREPARING", "SENT"):
                    upcoming.append(_target_row(p, t))
    upcoming.sort(key=lambda r: (r.get("publish_at") or "9999", r["target_id"]))
    history = store.read_publish_log(limit=50).get("entries") or []
    return jsonify({
        "ok": True,
        "upcoming": upcoming,
        "held": held,
        "history": history,
        "pause_all": platform_registry.publishing_paused(),
    })


@content_pipeline_bp.route("/api/content/calendar", methods=["GET"])
def content_calendar():
    """§10.1 Calendar: ?from&to → target pills + optimal-slot suggestions."""
    frm = store._parse_instant(request.args.get("from")) or _now_dt()
    to = store._parse_instant(request.args.get("to")) or (frm + timedelta(days=31))
    frm_iso = frm.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_iso = to.strftime("%Y-%m-%dT%H:%M:%SZ")
    pills: List[Dict[str, Any]] = []
    for st in ("SCHEDULED", "PUBLISHING", "HELD", "PUBLISHED", "PARTIAL"):
        res = store.list_posts(status=st, limit=500)
        for p in (res.get("posts") or []):
            recurrence = (p.get("schedule") or {}).get("recurrence") or "none"
            for t in (p.get("targets") or []):
                when = t.get("publish_at")
                if not when or not (frm_iso <= when <= to_iso):
                    continue
                row = _target_row(p, t)
                row["post_status"] = p.get("status")
                row["recurrence"] = recurrence
                pills.append(row)
    pills.sort(key=lambda r: r.get("publish_at") or "")
    # Suggested optimal slots (§6.4): learned cells labeled as such, else seed.
    # Seed and learned (weekday, hour) cells are defined in the user's LOCAL
    # timezone (same convention as _resolve_optimal) — candidates are UTC
    # instants, so convert before matching or every suggestion is wrong by
    # the UTC offset (and drifts across DST).
    suggestions: List[Dict[str, Any]] = []
    tz = _tzinfo(request.args.get("timezone")
                 or store._default_timezone() or "UTC")
    platform = request.args.get("platform")
    plats = ([platform] if platform
             else ["linkedin", "twitter", "instagram", "bluesky", "mastodon"])
    for plat in plats:
        learned = _learned_cells(plat)
        cells = learned or _SEED_BEST_TIMES.get(plat, [])
        label = "learned" if learned else "general"
        cand = frm
        seen = 0
        while cand <= to and seen < 4:
            local = cand.astimezone(tz)
            if (local.weekday(), local.hour) in cells and cand > _now_dt():
                suggestions.append({
                    "platform": plat,
                    "at": cand.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "label": label,
                })
                seen += 1
                cand += timedelta(days=1)
                cand = cand.replace(minute=0, second=0, microsecond=0)
                continue
            cand += timedelta(hours=1)
    return jsonify({"ok": True, "from": frm_iso, "to": to_iso,
                    "pills": pills, "suggestions": suggestions})


@content_pipeline_bp.route("/api/content/best-times", methods=["GET"])
def content_best_times():
    """§6.4: the static seed table + the learned histogram (?platform=)."""
    platform = request.args.get("platform") or None
    seed = ({platform: _SEED_BEST_TIMES.get(platform, [])} if platform
            else {k: v for k, v in _SEED_BEST_TIMES.items()})
    learned = store.get_best_times(platform=platform)
    return jsonify({
        "ok": True,
        "seed": {k: [{"weekday": wd, "hour": h} for wd, h in v]
                 for k, v in seed.items()},
        "learned": learned.get("best_times") or [],
        "learned_min_samples": _LEARNED_MIN_SAMPLES,
    })


# ═════════════════════════════════════════════════════════════════════════════
#  Analytics
# ═════════════════════════════════════════════════════════════════════════════

@content_pipeline_bp.route("/api/content/analytics/summary", methods=["GET"])
def content_analytics_summary():
    """Dashboard rollup (§10.1 Analytics header cards). ?days=30."""
    try:
        days = max(1, int(request.args.get("days") or 30))
    except ValueError:
        days = 30
    cutoff = (_now_dt() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    totals: Dict[str, int] = {}
    best_post = None
    best_rate = -1.0
    published = 0
    for st in ("PUBLISHED", "PARTIAL"):
        res = store.list_posts(status=st, limit=1000)
        for p in (res.get("posts") or []):
            if (p.get("published_at") or "") < cutoff:
                continue
            published += 1
            a = p.get("analytics") or {}
            for k in ("impressions", "reach", "likes", "comments", "shares",
                      "saves", "clicks", "video_views", "watch_time_s",
                      "follows_gained"):
                if isinstance(a.get(k), (int, float)):
                    totals[k] = totals.get(k, 0) + int(a[k])
            rate = a.get("engagement_rate")
            if isinstance(rate, (int, float)) and rate > best_rate:
                best_rate = float(rate)
                best_post = {"post_id": p.get("id"), "title": p.get("title"),
                             "engagement_rate": float(rate),
                             "published_at": p.get("published_at")}
    numerator = sum(totals.get(k, 0) for k in
                    ("likes", "comments", "shares", "saves", "clicks"))
    denominator = totals.get("impressions") or totals.get("video_views") or 1
    psi_mpsi = 0
    for row in (store.list_psi_awards().get("awards") or []):
        if (row.get("awarded_at") or "") >= cutoff:
            psi_mpsi += int(row.get("amount_mpsi") or 0)
    return jsonify({
        "ok": True, "days": days,
        "posts_published": published,
        "totals": totals,
        "engagement_rate": round(numerator / max(denominator, 1), 6),
        "best_post": best_post,
        "psi_earned_mpsi": psi_mpsi,
    })


@content_pipeline_bp.route("/api/content/analytics/post/<post_id>", methods=["GET"])
def content_analytics_post(post_id):
    got = store.get_post(post_id)
    if not got.get("ok"):
        return jsonify(got)
    snaps = store.get_snapshots(post_id=post_id, limit=500)
    return jsonify({
        "ok": True,
        "post_id": post_id,
        "analytics": got["post"].get("analytics") or {},
        "targets": got["post"].get("targets") or [],
        "snapshots": snaps.get("snapshots") or [],
    })


@content_pipeline_bp.route("/api/content/analytics/refresh/<target_id>", methods=["POST"])
def content_analytics_refresh(target_id):
    """Manual metric poll for one target, budget-aware (§8.1)."""
    got = store.get_target(target_id)
    if not got.get("ok"):
        return jsonify(got)
    t = got["target"]
    if t.get("status") != "CONFIRMED" or not t.get("platform_post_id"):
        return jsonify({"ok": False,
                        "error": "target has no confirmed platform post to poll"})
    adapter = platform_registry.get_adapter(t.get("platform") or "")
    if adapter is None:
        return jsonify({"ok": False,
                        "error": f"adapter unavailable: {t.get('platform')}"})
    try:
        metrics = adapter.fetch_metrics(t["platform_post_id"])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    if not metrics:
        return jsonify({"ok": False,
                        "error": "platform reported no metrics for this post"})
    res = store.insert_engagement_snapshot(target_id, metrics,
                                           raw={"metrics": metrics})
    if res.get("ok"):
        res["metrics"] = metrics
    return jsonify(res)


@content_pipeline_bp.route("/api/content/insights", methods=["GET"])
def content_insights():
    """Current insight cards (§8.4). Served by analytics_collector's weekly
    job; degrades to an empty deck while that module lands."""
    try:
        from agent_friday.services import analytics_collector as _ac
    except Exception:
        return jsonify({"ok": True, "insights": [],
                        "note": "analytics collector not available yet"})
    for fn_name in ("get_insights", "insights", "current_insights"):
        fn = getattr(_ac, fn_name, None)
        if callable(fn):
            try:
                res = fn()
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)})
            if isinstance(res, dict):
                return jsonify(res)
            return jsonify({"ok": True, "insights": list(res or [])})
    return jsonify({"ok": True, "insights": []})


# ═════════════════════════════════════════════════════════════════════════════
#  Platform accounts (§10.1 Accounts tab)
# ═════════════════════════════════════════════════════════════════════════════

@content_pipeline_bp.route("/api/content/platforms", methods=["GET"])
def content_platforms():
    st = platform_registry.status()
    return jsonify({"ok": True, **st})


@content_pipeline_bp.route("/api/content/platforms/<name>/connect", methods=["POST"])
def content_platform_connect(name):
    """Begin auth (§4.1): OAuth adapters return a connect_url + bound state;
    token/app-password adapters accept a pasted secret; manual adapters just
    acknowledge. Never echoes secret material back."""
    data = _json()
    adapter = platform_registry.get_adapter(name)
    if adapter is None:
        return jsonify({"ok": False,
                        "error": platform_registry.import_error(name)
                        or f"unknown platform: {name}"})
    options = dict(data.get("options")) if isinstance(data.get("options"), dict) else {}
    # The Accounts tab posts the non-secret identity fields at the TOP level
    # ({identifier, secret} for Bluesky, {instance, secret} for Mastodon) —
    # fold them into the platform options, otherwise the adapter never learns
    # its handle/instance and the connection can never actually work.
    for k in ("identifier", "handle", "instance", "base_url", "pds", "tier",
              "client_id", "redirect_uri"):
        v = data.get(k)
        if v is not None and k not in options:
            options[k] = v
    pasted = data.get("secret") or data.get("token")   # pragma: allowlist secret
    if options or pasted:
        cfg = platform_registry.configure_platform(
            name, options, secret_value=pasted or None)
        if not cfg.get("ok"):
            return jsonify(cfg)
        if pasted:
            # A pasted secret must yield a LIVE, verified session at connect
            # time (§4.1) — storing it alone leaves publish()/fetch_metrics()
            # answering not_connected (Bluesky) or silently broken later (bad
            # Mastodon token) until something incidentally re-logs in.
            verified: Optional[bool] = None
            if adapter.auth_mode == "app_password":
                # Bluesky-style: createSession via the token-paste callback.
                try:
                    cb = adapter.handle_callback(dict(options))
                    verified = bool(isinstance(cb, dict) and cb.get("connected"))
                except Exception:
                    verified = False
            else:
                # Token-paste (Mastodon etc.): probe the token live where the
                # adapter can (verify_credentials-backed account metrics).
                probe = getattr(adapter, "fetch_account_metrics", None)
                if callable(probe):
                    try:
                        verified = probe() is not None
                    except Exception:
                        verified = False
            st = adapter.status()
            out = {"ok": True, "mode": "token", "status": st}
            if verified is not None:
                out["verified"] = verified
            if verified is False:
                out["ok"] = False
                out["error"] = (st.get("last_error")
                                or "the pasted credential did not produce a "
                                   "live session — check it and try again")
            return jsonify(out)
    state = _issue_state(adapter.name)
    try:
        url = adapter.connect_url(state)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    if url:
        return jsonify({"ok": True, "mode": "oauth", "connect_url": url,
                        "state": state})
    # Tokenless / manual adapters: no browser hop needed.
    with _OAUTH_LOCK:
        _OAUTH_STATES.pop(state, None)
    if adapter.auth_mode in ("token", "app_password"):
        return jsonify({"ok": True, "mode": adapter.auth_mode,
                        "needs": "secret",
                        "message": f"{adapter.label} uses a pasted "
                                   f"{adapter.auth_mode.replace('_', ' ')} — "
                                   "POST it as {secret: ...}",
                        "status": adapter.status()})
    return jsonify({"ok": True, "mode": adapter.auth_mode,
                    "status": adapter.status()})


@content_pipeline_bp.route("/api/content/platforms/<name>/callback", methods=["GET"])
def content_platform_callback(name):
    """OAuth loopback redirect target (§4.1). Verifies the single-use state,
    delegates the code→token exchange to the adapter, and drops the user on
    the Accounts tab. This is a browser navigation, not a JSON API."""
    adapter = platform_registry.get_adapter(name)
    if adapter is None:
        return redirect(f"{_ACCOUNTS_DEEPLINK}&connect=error&reason=unknown_platform")
    state = request.args.get("state")
    if not _consume_state(state, adapter.name):
        # CSRF backstop: never hand an unbound code to the adapter.
        return redirect(f"{_ACCOUNTS_DEEPLINK}&connect=error&reason=bad_state")
    if request.args.get("error"):
        return redirect(f"{_ACCOUNTS_DEEPLINK}&connect=denied&platform={adapter.name}")
    try:
        st = adapter.handle_callback(request.args.to_dict())
    except Exception:
        st = {"connected": False}
    if isinstance(st, dict) and st.get("connected"):
        return redirect(f"{_ACCOUNTS_DEEPLINK}&connected={adapter.name}")
    return redirect(f"{_ACCOUNTS_DEEPLINK}&connect=error&platform={adapter.name}")


@content_pipeline_bp.route("/api/content/platforms/<name>", methods=["DELETE"])
def content_platform_disconnect(name):
    """Disconnect (§12.6): platform-side revoke where supported + credential
    purge, plus optional local analytics purge (?purge_analytics=1)."""
    adapter = platform_registry.get_adapter(name)
    if adapter is None:
        return jsonify({"ok": False,
                        "error": platform_registry.import_error(name)
                        or f"unknown platform: {name}"})
    try:
        revoked = bool(adapter.revoke())
    except Exception:
        revoked = False
    # Belt-and-suspenders: revoke() purges locally, but a platform-side revoke
    # failure must never leave tokens on disk.
    try:
        adapter.clear_credentials()
    except Exception:
        pass
    purged = False
    if request.args.get("purge_analytics") in ("1", "true", "yes"):
        platform_id = platform_registry.ADAPTER_MODULES.get(
            platform_registry._norm(name), name)
        try:
            with store._LOCK, store._connect() as con:
                con.execute("DELETE FROM engagement_snapshots WHERE platform=?",
                            (platform_id,))
                con.execute("DELETE FROM best_times WHERE platform=?",
                            (platform_id,))
            purged = True
        except Exception:
            purged = False
    return jsonify({"ok": True, "revoked": revoked,
                    "purged_analytics": purged,
                    "status": adapter.status()})


@content_pipeline_bp.route("/api/content/platforms/<name>/test", methods=["POST"])
def content_platform_test(name):
    """Connection test (§10.1): a private/self test post where the adapter
    supports one, otherwise a refresh + status probe."""
    adapter = platform_registry.get_adapter(name)
    if adapter is None:
        return jsonify({"ok": False,
                        "error": platform_registry.import_error(name)
                        or f"unknown platform: {name}"})
    tester = getattr(adapter, "test_post", None) or getattr(adapter, "test", None)
    if callable(tester):
        try:
            res = tester()
            if isinstance(res, dict):
                return jsonify(res)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})
    try:
        usable = bool(adapter.refresh())
    except Exception:
        usable = False
    st = adapter.status()
    return jsonify({"ok": bool(usable and st.get("connected")),
                    "connected": bool(st.get("connected")),
                    "status": st})


# ═════════════════════════════════════════════════════════════════════════════
#  Voice cards / export
# ═════════════════════════════════════════════════════════════════════════════

@content_pipeline_bp.route("/api/content/voice-cards/<platform>", methods=["GET"])
def content_voice_card_get(platform):
    return jsonify(composer.get_voice_card(platform))


@content_pipeline_bp.route("/api/content/voice-cards/<platform>", methods=["POST"])
def content_voice_card_set(platform):
    data = _json()
    return jsonify(composer.set_voice_card(platform, str(data.get("text") or "")))


@content_pipeline_bp.route("/api/content/export", methods=["GET"])
def content_export():
    """§12.6 full local data export: posts (with targets), snapshots,
    best-times, ψ awards, and the publish log — one JSON bundle."""
    posts = store.list_posts(limit=100000)
    snaps = store.get_snapshots(limit=100000)
    best = store.get_best_times()
    awards = store.list_psi_awards()
    log = store.read_publish_log(limit=store.PUBLISH_LOG_KEEP)
    return jsonify({
        "ok": True,
        "exported_at": _now_dt().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": 1,
        "posts": posts.get("posts") or [],
        "snapshots": snaps.get("snapshots") or [],
        "best_times": best.get("best_times") or [],
        "psi_awards": awards.get("awards") or [],
        "publish_log": log.get("entries") or [],
    })
