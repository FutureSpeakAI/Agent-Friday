"""
Agent Friday — Analytics Collector (docs/CONTENT_PIPELINE_SPEC.md §8)
FutureSpeak.AI · Asimov's Mind

The monitor/learn half of the pipeline: two scheduler builtins registered by
``start()`` (called from publisher.start() at boot):

  content_analytics (interval, 30 min, silent) → ``collect()``
      Walks the decaying per-target poll plan (+1 h, +6 h, +24 h, +3 d, +7 d,
      +30 d after publish — §8.1), pulls ``adapter.fetch_metrics`` for each
      due CONFIRMED target, stores a normalized snapshot row (the store
      sanitizes: unified keys only, raw capped — §8.6 untrusted-input
      discipline), and mints ψ on engagement thresholds (idempotent per
      {target}:{metric}:{threshold}, daily-capped — §8.7).

  content_insights (weekly) → ``weekly_insights()``
      Aggregates snapshots locally into best-time histograms (platform ×
      LOCAL weekday × hour → best_times rows, the §6.4 learned layer),
      attribute-lift insight cards with honest sample sizes (§8.4), and
      ``learning_loop.observe(task_type="content_publish")`` records.

The poll plan is STATELESS by design: the number of stored snapshots for a
target indexes into POLL_PLAN_S relative to the post's published_at — no
side-car plan file to corrupt, and a manual refresh simply advances the plan.
Budget-tight adapters slide their poll to the next tick (§8.1).

No platform-returned text ever reaches an LLM prompt; insights are computed
numerically and phrased from local templates only (§8.6).

House rules: import-safe under FRIDAY_TESTING (no threads/network/registration
at import), envelopes out of every public helper, heavy deps lazy-imported.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from agent_friday.services import content_pipeline as store
from agent_friday.services import platforms as platform_registry

_log = logging.getLogger("friday.analytics_collector")

# §8.1 decaying poll plan — seconds after published_at, one snapshot per step.
POLL_PLAN_S = (3600, 6 * 3600, 24 * 3600, 3 * 86400, 7 * 86400, 30 * 86400)

# §8.7 engagement→ψ thresholds: every 10 likes, every 5 shares.
_PSI_STEPS = (("likes", 10), ("shares", 5))
_INSIGHT_MIN_SAMPLES = 3               # attribute-lift honesty floor (§8.4)
_LEARNED_MIN_SAMPLES = 5               # §6.4: learned outranks seed at n≥5


def _insights_path():
    # Resolved at call time so tests that monkeypatch store.CONTENT_DIR win.
    return store.CONTENT_DIR / "insights.json"


def _observed_path():
    # §8.4 once-ever observation ledger: learning_loop.observe() writes a
    # fresh uuid row per call, so the weekly job must remember which targets
    # it already observed or n inflates week over week.
    return store.CONTENT_DIR / "observed_targets.json"


def _load_observed() -> set:
    try:
        return set(json.loads(_observed_path().read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_observed(seen: set) -> None:
    try:
        path = _observed_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(seen)[-20000:]), encoding="utf-8")
    except Exception:
        pass


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
#  Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def start() -> Dict[str, Any]:
    """Register the §6.2 analytics builtins with the scheduler. Never raises."""
    out: Dict[str, Any] = {"ok": True, "registered": []}
    try:
        from agent_friday.services.scheduler import register_builtin_task
        register_builtin_task(
            "content_analytics", collect, label="Content analytics poll",
            default_trigger="interval", default_spec={"every_minutes": 30},
            notify="silent")
        out["registered"].append("content_analytics")
        register_builtin_task(
            "content_insights", weekly_insights, label="Content insights",
            default_trigger="weekly", default_spec={"weekday": 0, "hour": 9},
            notify="silent")
        out["registered"].append("content_insights")
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Poll plan (§8.1)
# ─────────────────────────────────────────────────────────────────────────────

def _published_ref(post: Dict[str, Any], target: Dict[str, Any]) -> Optional[datetime]:
    return (store._parse_instant(post.get("published_at"))
            or store._parse_instant(target.get("updated_at")))


def _confirmed_targets() -> List[Dict[str, Any]]:
    """(post, target) pairs for every CONFIRMED target with a platform id."""
    pairs: List[Dict[str, Any]] = []
    for status in ("PUBLISHED", "PARTIAL", "PUBLISHING", "HELD"):
        res = store.list_posts(status=status, limit=1000)
        for p in (res.get("posts") or []):
            for t in (p.get("targets") or []):
                if t.get("status") == "CONFIRMED" and t.get("platform_post_id"):
                    pairs.append({"post": p, "target": t})
    return pairs


def poll_due(now=None) -> List[Dict[str, Any]]:
    """Targets whose next §8.1 plan step is due: snapshot COUNT indexes into
    POLL_PLAN_S relative to published_at; plan exhausted (≥6 snaps) → done
    (the manual refresh route still works past that)."""
    now_dt = store._parse_instant(now) or _now_dt()
    due: List[Dict[str, Any]] = []
    for pair in _confirmed_targets():
        t = pair["target"]
        ref = _published_ref(pair["post"], t)
        if ref is None:
            continue
        n = int(store.get_snapshots(target_id=t["id"], limit=1000).get("count") or 0)
        if n >= len(POLL_PLAN_S):
            continue
        if (now_dt - ref).total_seconds() >= POLL_PLAN_S[n]:
            due.append(pair)
    return due


def get_poll_plan(target_id: str, now=None) -> Dict[str, Any]:
    """The derived §8.1 plan for one target: six steps at published_at +
    POLL_PLAN_S offsets, `done` = already snapshotted (count-indexed)."""
    try:
        res = store.get_target(target_id)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error") or "target not found"}
        t = res.get("target") or {}
        post = (store.get_post(str(t.get("post_id"))).get("post")) or {}
        ref = _published_ref(post, t)
        if ref is None:
            return {"ok": False, "error": "target has no publish instant"}
        n = int(store.get_snapshots(target_id=target_id,
                                    limit=1000).get("count") or 0)
        now_dt = store._parse_instant(now) or _now_dt()
        steps = []
        for i, off in enumerate(POLL_PLAN_S):
            due = ref + timedelta(seconds=off)
            steps.append({
                "step": i, "offset_s": off,
                "due_at": due.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "done": i < n,
                "due": i == n and now_dt >= due,
            })
        return {"ok": True, "target_id": target_id, "completed": n,
                "exhausted": n >= len(POLL_PLAN_S), "steps": steps}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def create_poll_plan(target_id: str) -> Dict[str, Any]:
    """Called at target confirmation (§8.1). The plan is *derived* from
    published_at + POLL_PLAN_S (no side-car state to corrupt), so creation is
    validation: it exists the instant the target is confirmed, and calling
    this twice — or replaying a confirmation — is idempotent by construction."""
    return get_poll_plan(target_id)


# ─────────────────────────────────────────────────────────────────────────────
#  §8.2 unified metric normalization — missing ≠ zero, strings coerced,
#  everything else dropped (untrusted-input discipline, §8.6)
# ─────────────────────────────────────────────────────────────────────────────

# unified_key → tuple of platform-payload source keys (present sources SUM —
# e.g. X shares = retweets + quotes). Unified keys already present in the
# payload win (adapters may pre-normalize).
_PLATFORM_MAPS: Dict[str, Dict[str, tuple]] = {
    "linkedin": {"likes": ("reactions",), "comments": ("comments",),
                 "shares": ("reposts",), "video_views": ("videoViews",)},
    "twitter": {"impressions": ("impression_count",),
                "likes": ("like_count",), "comments": ("reply_count",),
                "shares": ("retweet_count", "quote_count"),
                "saves": ("bookmark_count",),
                "clicks": ("url_link_clicks",)},
    "instagram": {"impressions": ("views",), "likes": ("likes",),
                  "comments": ("comments",), "shares": ("shares",),
                  "saves": ("saved",), "video_views": ("plays",),
                  "follows_gained": ("follows",)},
    "youtube": {"impressions": ("views",), "video_views": ("views",),
                "likes": ("likes",), "comments": ("comments",),
                "shares": ("shares",),
                "follows_gained": ("subscribersGained",)},
    "bluesky": {"likes": ("like_count",), "comments": ("reply_count",),
                "shares": ("repost_count", "quote_count",)},
    "mastodon": {"likes": ("favourites",), "comments": ("replies",),
                 "shares": ("reblogs",), "saves": ("bookmarks",)},
    "reddit": {"likes": ("score",), "comments": ("num_comments",),
               "shares": ("crossposts",), "saves": ("saves",)},
    "tiktok": {"impressions": ("view_count",), "video_views": ("view_count",),
               "likes": ("like_count",), "comments": ("comment_count",),
               "shares": ("share_count",)},
    "federation": {"impressions": ("listing_views",),
                   "likes": ("tips_count",), "comments": ("peer_messages",),
                   "shares": ("peer_relays",), "clicks": ("fetches",),
                   "follows_gained": ("new_peers",)},
}
_PLATFORM_ALIASES = {"x": "twitter", "x_twitter": "twitter"}


def _coerce_num(value) -> Optional[int]:
    """Untrusted number coercion: ints/floats/clean numeric strings only."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except (ValueError, TypeError):
            return None
    return None


def normalize_metrics(platform: str, payload: Any) -> Dict[str, int]:
    """Platform payload → the §8.2 unified shape. A metric the platform did
    not report stays ABSENT (never 0 — the UI renders '—'); unknown keys are
    dropped; numbers are coerced; nothing here ever reaches a prompt."""
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, int] = {}
    for key in store._METRIC_KEYS:               # pre-normalized keys win
        v = _coerce_num(payload.get(key))
        if v is not None:
            out[key] = v
    plat = _PLATFORM_ALIASES.get(str(platform or "").lower(),
                                 str(platform or "").lower())
    for unified, sources in (_PLATFORM_MAPS.get(plat) or {}).items():
        if unified in out:
            continue
        vals = [n for n in (_coerce_num(payload.get(s)) for s in sources)
                if n is not None]
        if vals:
            out[unified] = sum(vals)
    if plat == "youtube" and "watch_time_s" not in out:
        mins = _coerce_num(payload.get("estimatedMinutesWatched"))
        if mins is not None:
            out["watch_time_s"] = mins * 60
    return out


def engagement_rate(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """§8.2 uniform rate with an HONEST denominator: impressions, else
    video_views, else 1 — and the basis is reported so the UI can footnote
    which denominator each platform used."""
    m = metrics or {}
    numerator = sum(v for v in (_coerce_num(m.get(k)) for k in
                                ("likes", "comments", "shares", "saves",
                                 "clicks")) if v is not None)
    impressions = _coerce_num(m.get("impressions"))
    video_views = _coerce_num(m.get("video_views"))
    if impressions:
        basis, denom = "impressions", impressions
    elif video_views:
        basis, denom = "video_views", video_views
    else:
        basis, denom = "none", 1
    return {"rate": round(numerator / max(denom, 1), 6),
            "denominator": max(denom, 1), "basis": basis}


def collect(now=None) -> Dict[str, Any]:
    """The content_analytics tick: poll every due target, store snapshots,
    mint threshold ψ. Budget-aware — a tight adapter slides to the next tick."""
    try:
        polled = slid = skipped = 0
        for pair in poll_due(now):
            t = pair["target"]
            platform = str(t.get("platform") or "")
            adapter = platform_registry.get_adapter(platform)
            if adapter is None:
                skipped += 1
                continue
            try:
                if adapter.budget_would_exceed():
                    slid += 1                      # §8.1: slide, don't skip
                    continue
            except Exception:
                pass
            try:
                payload = adapter.fetch_metrics(t["platform_post_id"])
            except Exception:
                payload = None
            metrics = normalize_metrics(platform, payload)
            if not metrics:
                skipped += 1                       # can't report ≠ zero (§8.2)
                continue
            res = store.insert_engagement_snapshot(
                t["id"], metrics, raw=payload, collected_at=now)
            if res.get("ok"):
                polled += 1
                _award_engagement_psi(t, metrics)
        return {"ok": True, "polled": polled, "slid": slid, "skipped": skipped}
    except Exception as e:
        _log.debug("collect failed", exc_info=True)
        return {"ok": False, "error": str(e)}


tick = collect                         # the scheduler-facing name (§6.2/§8.1)


# ─────────────────────────────────────────────────────────────────────────────
#  Engagement → ψ (§8.7): idempotent, bounded, local
# ─────────────────────────────────────────────────────────────────────────────

def _psi_daily_cap_mpsi() -> int:
    try:
        import agent_friday.core as core
        c = (core._load_settings() or {}).get("content") or {}
        return max(0, int(c.get("psi_daily_cap", 200))) * 1000
    except Exception:
        return 200_000


def _minted_today_mpsi(today_prefix: str) -> int:
    total = 0
    for row in (store.list_psi_awards().get("awards") or []):
        if str(row.get("awarded_at") or "").startswith(today_prefix):
            total += int(row.get("amount_mpsi") or 0)
    return total


def _self_agent_id() -> str:
    try:
        from agent_friday.services.federation import get_identity
        return str((get_identity() or {}).get("agent_id") or "self")
    except Exception:
        return "self"


def _award_engagement_psi(target: Dict[str, Any],
                          metrics: Dict[str, Any]) -> int:
    """Crossed thresholds mint PSI_LIKE each, once ever per key — the award
    table guarantees a re-poll storm never double-mints; the daily cap keeps
    a viral day from distorting the wallet. Returns mψ minted this call."""
    try:
        from agent_friday.services.economy import PSI_LIKE, earn
    except Exception:
        return 0
    cap = _psi_daily_cap_mpsi()
    today = store._now_iso()[:10]
    minted_today = _minted_today_mpsi(today)
    minted = 0
    agent = _self_agent_id()
    for metric, step in _PSI_STEPS:
        try:
            value = int(metrics.get(metric) or 0)
        except (TypeError, ValueError):
            continue
        for threshold in range(step, value + 1, step):
            if cap and minted_today + PSI_LIKE > cap:
                # Daily cap reached: stop WITHOUT writing award rows, so the
                # uncrossed thresholds mint on a later day's poll instead of
                # being silently lost. Bounded and fair (§8.7).
                return minted
            res = store.award_psi(str(target.get("id")), metric, threshold,
                                  PSI_LIKE)
            if not (res.get("ok") and res.get("awarded")):
                continue
            try:
                earn(agent, PSI_LIKE,
                     reason=f"content:engagement:{target.get('platform')}")
            except Exception:
                pass
            minted += PSI_LIKE
            minted_today += PSI_LIKE
    return minted


# ─────────────────────────────────────────────────────────────────────────────
#  Weekly insights (§8.4) — best times, attribute lift, learning-loop feed
# ─────────────────────────────────────────────────────────────────────────────

def _tzinfo(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _target_rate(target_id: str) -> Optional[float]:
    """Engagement rate from the target's LATEST snapshot (§8.2 formula)."""
    snaps = store.get_snapshots(target_id=target_id, limit=1).get("snapshots") or []
    if not snaps:
        return None
    return float(engagement_rate(snaps[0].get("metrics") or {})["rate"])


def _wilson_lower(successes: int, n: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound — the learning_loop scoring approach
    (§8.4): keeps small-n flukes quiet without hiding real, replicated lift."""
    if n <= 0:
        return 0.0
    p = min(max(successes / n, 0.0), 1.0)
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _post_kind(post: Dict[str, Any]) -> str:
    kinds = {str((a or {}).get("kind") or "") for a in (post.get("assets") or [])}
    if "video" in kinds:
        return "video"
    if "image" in kinds:
        return "image"
    if "audio" in kinds:
        return "audio"
    return "text"


def weekly_insights(now=None) -> Dict[str, Any]:
    """The content_insights job: local aggregation only — no cloud, no
    platform text in prompts (§8.6). Returns {ok, insights, best_time_cells}."""
    try:
        cells: Dict[tuple, List[float]] = {}
        by_kind: Dict[str, List[float]] = {}
        observations = 0
        rates: List[float] = []
        rows: List[Dict[str, Any]] = []
        for pair in _confirmed_targets():
            post, t = pair["post"], pair["target"]
            rate = _target_rate(t["id"])
            if rate is None:
                continue
            ref = _published_ref(post, t)
            if ref is None:
                continue
            tz = _tzinfo(str((post.get("schedule") or {}).get("timezone")
                             or "UTC"))
            local = ref.astimezone(tz)
            cells.setdefault((str(t.get("platform")), local.weekday(),
                              local.hour), []).append(rate)
            kind = _post_kind(post)
            by_kind.setdefault(kind, []).append(rate)
            rates.append(rate)
            rows.append({"target_id": str(t.get("id")),
                         "platform": t.get("platform"), "kind": kind,
                         "hour": local.hour, "rate": rate})

        # Best-time histogram → the §6.4 learned layer. Cells are stored in
        # the post's LOCAL (weekday, hour) — the same convention the seed
        # table and the calendar/optimal resolvers read.
        for (platform, weekday, hour), vals in cells.items():
            store.upsert_best_time(platform, weekday, hour,
                                   round(sum(vals) / len(vals), 6), len(vals))

        # Attribute lift with honest sample sizes (§8.4).
        insights: List[Dict[str, Any]] = []
        baseline = (sum(rates) / len(rates)) if rates else 0.0
        eligible = {k: v for k, v in by_kind.items()
                    if len(v) >= _INSIGHT_MIN_SAMPLES}
        if len(eligible) >= 2:
            ranked = sorted(eligible.items(),
                            key=lambda kv: sum(kv[1]) / len(kv[1]),
                            reverse=True)
            (top_kind, top_vals), (low_kind, low_vals) = ranked[0], ranked[-1]
            top_avg = sum(top_vals) / len(top_vals)
            low_avg = sum(low_vals) / len(low_vals)
            # Wilson lower bound on "top-kind posts beat the account baseline"
            # — n=3 of 3 wins gives lb≈0.44 (silent); n=5 of 5 gives ≈0.57
            # (speaks). Small-n flukes never make it onto a card (§8.4).
            wins = sum(1 for v in top_vals if v > baseline)
            lb = _wilson_lower(wins, len(top_vals))
            if low_avg > 0 and top_avg > low_avg and lb > 0.5:
                insights.append({
                    "kind": "attribute_lift",
                    "text": (f"{top_kind} posts get {top_avg / low_avg:.1f}× "
                             f"the engagement of {low_kind} posts "
                             f"(n={len(top_vals)} vs n={len(low_vals)})"),
                    "samples": len(top_vals) + len(low_vals),
                    "wilson_lb": round(lb, 4),
                })
        learned = [
            {"platform": platform, "weekday": weekday, "hour": hour,
             "score": round(sum(vals) / len(vals), 6), "samples": len(vals)}
            for (platform, weekday, hour), vals in cells.items()
            if len(vals) >= _LEARNED_MIN_SAMPLES]
        for cell in sorted(learned, key=lambda c: -c["score"])[:3]:
            insights.append({
                "kind": "best_time",
                "text": (f"{cell['platform']}: your audience is most active "
                         f"weekday {cell['weekday']} around {cell['hour']:02d}:00 "
                         f"(learned, n={cell['samples']})"),
                "platform": cell["platform"], "weekday": cell["weekday"],
                "hour": cell["hour"], "samples": cell["samples"],
            })

        # learning_loop observations (§8.4) — numeric-only approach strings.
        # Once-ever per target via the observed ledger: without it every
        # weekly run re-observed ALL confirmed targets, inflating n and
        # poisoning the Wilson-scored heuristics.
        try:
            from agent_friday.services import learning_loop
            seen = _load_observed()
            fresh = [r for r in rows if r["target_id"] not in seen][:200]
            for r in fresh:
                learning_loop.observe(
                    task_type="content_publish", prompt="",
                    approach=f"{r['platform']}:{r['kind']}:h{r['hour']:02d}",
                    success=bool(r["rate"] > baseline), workspace="content")
                observations += 1
                seen.add(r["target_id"])
            if fresh:
                _save_observed(seen)
        except Exception:
            pass

        payload = {"generated_at": store._now_iso(), "insights": insights,
                   "baseline_rate": round(baseline, 6),
                   "best_time_cells": len(cells)}
        try:
            path = _insights_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass
        return {"ok": True, "observations": observations, **payload}
    except Exception as e:
        _log.debug("weekly_insights failed", exc_info=True)
        return {"ok": False, "error": str(e)}


def get_insights() -> Dict[str, Any]:
    """Current insight cards for GET /api/content/insights — empty deck when
    the weekly job hasn't produced any yet."""
    try:
        path = _insights_path()
        if not path.exists():
            return {"ok": True, "insights": [], "generated_at": None}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"ok": True,
                "insights": list((data or {}).get("insights") or []),
                "generated_at": (data or {}).get("generated_at")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
