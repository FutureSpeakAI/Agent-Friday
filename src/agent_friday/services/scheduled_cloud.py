"""Whether the built-in scheduled jobs may use a cloud model, and what it costs.

Where a routine job runs is decided in ONE place,
`cloud_intent.background_route` (its docstring has the truth table); this
module holds the settings it reads. In short: when the owner CHOSE cloud_only
(Settings or setup; the factory value is not a choice), that choice is the
consent, and the News routines, podcast scripts and the heartbeat run on the
cloud model named here. Otherwise (and always for daily creation) daily
creation and the heartbeat run on the local model when one serves, at no cost;
with none serving they are skipped unless the owner answered yes below. The
News routines are not covered by that answer: they run locally and wait for a
local model.

This module holds the owner's explicit answer to "may they use a cloud model
instead?" -- the `scheduled_cloud` settings block -- and the monthly cost
estimate shown wherever that question is asked (the setup chat and
Settings -> Spending).

    answered   the owner has answered the question (Yes or No). Skipping it
               leaves this false, so the question can be asked again.
    allow      the jobs may run on the cloud models below when no local model
               is serving. False until the owner says yes.
    at         when it was answered (ISO time), or None.
    heartbeat_model / job_model
               the one cloud model each run is pinned to (see
               local_only_guard.cloud_pinned): no fallback leg may reach for a
               different, dearer one.
    heartbeat_every_minutes, heartbeat_from_hour, heartbeat_to_hour
               the heartbeat's cadence while it runs in the cloud: every few
               hours, daytime only. An hourly heartbeat around the clock on a
               frontier model is how one install ran up $438.76 over 2,357
               runs; the cloud cadence exists so that cannot recur by default.
    routine_follows_cloud_mode
               True (the default): under a cloud_only the owner chose, News,
               podcast scripts and the heartbeat run on the models above
               without this question being asked.
               False keeps them local-only even then.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from agent_friday.user_errors import UserFacingValueError

_log = logging.getLogger("friday.scheduled_cloud")

#: The jobs this choice covers, in the order they are shown, with plain names.
#: The jobs the owner may allow onto a cloud model in a local routing mode.
#: News is not among them: in a local mode every News path is local-only and
#: waits for the local seat (under a chosen cloud_only the mode is the consent).
JOBS = (
    ("sch_daily_creation", "Daily creation"),
    ("sch_heartbeat", "Heartbeat"),
)

#: Heartbeat cadences offered in Settings, in minutes.
HEARTBEAT_CADENCES = (60, 120, 240, 360, 480, 720, 1440)

PAUSED_NOTICE_KEY = "sched-cloud-paused"

#: Days per month used for the estimate (365 / 12).
DAYS_PER_MONTH = 30.4

# ── Per-run token estimate ────────────────────────────────────────────────────
#
# Every figure below is either measured in this codebase or derived from the
# payload the job actually sends; the ones that are assumptions say so.
#
# SYSTEM_PROMPT_TOKENS: the assembled Friday system prompt, measured on the
# reference machine at ~13,550 tokens (services/tool_budget.py). Every one of
# these jobs sends it.
SYSTEM_PROMPT_TOKENS = 13_550
#
# FRONT_PAGE_PROMPT_TOKENS: the Front Page editor's whole request, system
# prompt included, measured at 25,410 prompt tokens
# (news_engine._editorialize_front_page). Its reply is a JSON object of about
# 900 tokens at the top end; 2,000 allows for a longer edition.
FRONT_PAGE_PROMPT_TOKENS = 25_410
#
# ASSUMPTIONS, stated plainly:
#   * A heartbeat run takes 3 model rounds (check calendar, search mail,
#     answer) and each tool result adds ~1,000 tokens to the transcript that
#     the next round re-sends. Its reply is a line or "NO CHANGE".
#   * The afternoon briefing's live context (today's calendar, news, open
#     tasks) is ~4,000 tokens.
#   * Daily creation makes two calls: choosing the piece (reply capped at 600
#     tokens) and writing it (capped at 4,096; ~3,000 typical).
#   * Every input token is priced at the full input rate. Prompt caching
#     usually makes repeated rounds cheaper, so this errs high, never low.
HEARTBEAT_ROUNDS = 3
HEARTBEAT_TOOL_RESULT_TOKENS = 1_000
HEARTBEAT_OUTPUT_TOKENS = 600
HEARTBEAT_TOOLS_FALLBACK_TOKENS = 1_200
BRIEFING_CONTEXT_TOKENS = 4_000

#: A job with at least MEASURE_MIN_RUNS recorded runs in costs.db over the
#: last MEASURE_DAYS days is estimated from what those runs actually sent and
#: received; the constants above are only the fallback. The constants were off
#: by 2-4x on a real install (field install report, Part 2 #12).
MEASURE_DAYS = 30
MEASURE_MIN_RUNS = 3

ASSUMPTIONS = (
    "Where this PC has recorded at least 3 runs of a job in the last 30 days, "
    "its tokens per run are the average of those runs (costs.db); otherwise "
    "the figures below are used.",
    "Friday's system prompt is about 13,550 tokens and every job sends it.",
    "The Front Page editor's request was measured at 25,410 tokens.",
    "A heartbeat takes 3 model rounds; each tool result adds about 1,000 tokens.",
    "Every input token is priced at the full rate. Prompt caching usually "
    "makes it cheaper, so the estimate errs high.",
    "Daily jobs run once a day (daily creation only on days you step away).",
)


def _tokens(text) -> int:
    """Rough token count of a payload: ~4 characters per token."""
    return max(0, int(math.ceil(len(str(text or "")) / 4.0)))


def _heartbeat_tool_tokens(tool_names) -> int:
    """Tokens of the heartbeat's tool schemas, from the real registry."""
    try:
        import json
        from agent_friday.services.agent import CLAUDE_TOOLS
        names = set(tool_names or ())
        picked = [t for t in CLAUDE_TOOLS if t.get("name") in names]
        if picked:
            return _tokens(json.dumps(picked))
    except Exception as e:
        _log.debug("tool schema size unavailable: %s", e)
    return HEARTBEAT_TOOLS_FALLBACK_TOKENS


def per_run_tokens(sid: str, rec: dict | None = None) -> dict:
    """{'input', 'output'} tokens one run of `sid` is estimated to send/receive."""
    task = (rec or {}).get("task") or {}
    if sid == "sch_heartbeat":
        if not task:
            from agent_friday.services.scheduler import _DEFAULT_AGENT_SCHEDULES
            task = next((d["task"] for d in _DEFAULT_AGENT_SCHEDULES
                         if d["id"] == "sch_heartbeat"), {})
        base = (SYSTEM_PROMPT_TOKENS + _heartbeat_tool_tokens(task.get("tools"))
                + _tokens(task.get("prompt")))
        grown = sum(k * HEARTBEAT_TOOL_RESULT_TOKENS for k in range(HEARTBEAT_ROUNDS))
        return {"input": HEARTBEAT_ROUNDS * base + grown,
                "output": HEARTBEAT_OUTPUT_TOKENS}
    if sid in ("sch_news_morning", "sch_front_page_evening"):
        return {"input": FRONT_PAGE_PROMPT_TOKENS, "output": 2_000}
    if sid == "sch_afternoon_briefing":
        return {"input": SYSTEM_PROMPT_TOKENS + BRIEFING_CONTEXT_TOKENS + 100,
                "output": 1_200}
    if sid == "sch_daily_creation":
        return {"input": 2 * (SYSTEM_PROMPT_TOKENS + 1_000), "output": 600 + 3_000}
    return {"input": SYSTEM_PROMPT_TOKENS, "output": 1_000}


def measured_per_run(sid: str, days: int = MEASURE_DAYS,
                     min_runs: int = MEASURE_MIN_RUNS) -> dict | None:
    """What one run of `sid` actually sent and received, averaged over its
    recorded runs in costs.db, or None with fewer than `min_runs` runs.

    {'input', 'output', 'runs', 'usd'}: input counts cached reads and cache
    writes too (they are prompt tokens the job sent), and `usd` is what those
    runs were billed on average, whatever model they ran on. Runs on a local
    seat count for size; they are the same job sending the same prompt.
    Never raises.
    """
    import time as _t
    try:
        from agent_friday.services import cost_meter
        cost_meter.flush()
        conn = cost_meter._conn()
        since = _t.time() - max(1, int(days)) * 86400
        with cost_meter._CONN_LOCK:
            rows = conn.execute(
                "SELECT run_id, "
                "COALESCE(SUM(input_tokens),0) + COALESCE(SUM(cache_read_tokens),0) "
                "+ COALESCE(SUM(cache_write_tokens),0), "
                "COALESCE(SUM(output_tokens),0), COALESCE(SUM(cost_usd),0) "
                "FROM cost_calls WHERE schedule_id=? AND ts>=? "
                "AND run_id IS NOT NULL AND run_id<>'' GROUP BY run_id",
                (sid, since)).fetchall()
    except Exception as e:
        _log.debug("measured tokens for %s unavailable: %s", sid, e)
        return None
    rows = [r for r in rows if (r[1] or 0) > 0]
    if len(rows) < max(1, int(min_runs)):
        return None
    n = len(rows)
    return {"input": int(round(sum(r[1] for r in rows) / n)),
            "output": int(round(sum(r[2] for r in rows) / n)),
            "runs": n,
            "usd": round(sum(r[3] for r in rows) / n, 6)}


# ── Settings ──────────────────────────────────────────────────────────────────

def defaults() -> dict:
    import agent_friday.core as core
    return dict(core.DEFAULT_SETTINGS.get("scheduled_cloud") or {})


def settings() -> dict:
    """The `scheduled_cloud` block with every key present and sane."""
    import agent_friday.core as core
    out = defaults()
    try:
        blk = (core._load_settings() or {}).get("scheduled_cloud") or {}
    except Exception:
        blk = {}
    if isinstance(blk, dict):
        out.update({k: v for k, v in blk.items() if k in out})
    out["answered"] = bool(out.get("answered"))
    out["allow"] = bool(out.get("allow")) and out["answered"]
    out["routine_follows_cloud_mode"] = bool(out.get("routine_follows_cloud_mode", True))
    try:
        out["heartbeat_every_minutes"] = max(60, int(out.get("heartbeat_every_minutes") or 240))
    except (TypeError, ValueError):
        out["heartbeat_every_minutes"] = 240
    for k, d in (("heartbeat_from_hour", 8), ("heartbeat_to_hour", 20)):
        try:
            out[k] = min(24, max(0, int(out.get(k, d))))
        except (TypeError, ValueError):
            out[k] = d
    return out


def _known_model(model) -> bool:
    try:
        from agent_friday.services import cost_meter
        return str(model or "") in cost_meter.PRICING
    except Exception:
        return False


def save(patch: dict) -> dict:
    """Apply the owner's change and return the stored block.

    Setting `allow` records an answer (answered=True, at=now). The models must
    be priced models, so the estimate can never show a cloud model at $0.
    """
    import agent_friday.core as core
    patch = dict(patch or {})
    cur = settings()
    new = dict(cur)
    if "allow" in patch:
        new["allow"] = bool(patch["allow"])
        new["answered"] = True
        new["at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    if "heartbeat_every_minutes" in patch:
        try:
            every = int(patch["heartbeat_every_minutes"])
        except (TypeError, ValueError):
            raise UserFacingValueError("heartbeat_every_minutes must be a number of minutes")
        if every not in HEARTBEAT_CADENCES:
            raise UserFacingValueError("heartbeat_every_minutes must be one of %s"
                             % ", ".join(str(c) for c in HEARTBEAT_CADENCES))
        new["heartbeat_every_minutes"] = every
    for key in ("heartbeat_model", "job_model"):
        if key in patch:
            if not _known_model(patch[key]):
                raise UserFacingValueError("%s must be a priced model" % key)
            new[key] = str(patch[key])
    core._save_settings({"scheduled_cloud": new})
    if new.get("allow"):
        clear_paused_notice()
    return settings()


def model_for(sid: str, cfg: dict | None = None) -> str:
    cfg = cfg or settings()
    return cfg["heartbeat_model"] if sid == "sch_heartbeat" else cfg["job_model"]


def model_label(model: str) -> str:
    """The catalogue's display name for `model` (e.g. "Claude Haiku 4.5")."""
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        for prov in get_provider_registry().list_providers():
            meta = (prov.get("model_meta") or {}).get(model)
            if meta and meta.get("label"):
                return str(meta["label"])
    except Exception:
        pass
    return str(model or "")


# ── Cadence ───────────────────────────────────────────────────────────────────

def heartbeat_runs_per_day(cfg: dict) -> int:
    window = max(0, cfg["heartbeat_to_hour"] - cfg["heartbeat_from_hour"]) * 60
    return int(math.ceil(window / float(cfg["heartbeat_every_minutes"]))) if window else 0


def heartbeat_slot_open(rec: dict, now, cfg: dict | None = None) -> bool:
    """May the heartbeat run now, at its CLOUD cadence?"""
    import time as _t
    cfg = cfg or settings()
    if not (cfg["heartbeat_from_hour"] <= now.hour < cfg["heartbeat_to_hour"]):
        return False
    last = float(rec.get("last_run_ts") or 0)
    now_ts = now.timestamp() if hasattr(now, "timestamp") else _t.time()
    return (now_ts - last) >= cfg["heartbeat_every_minutes"] * 60


def runs_per_month(sid: str, rec: dict | None, cfg: dict) -> float:
    if sid == "sch_heartbeat":
        return heartbeat_runs_per_day(cfg) * DAYS_PER_MONTH
    trig = (rec or {}).get("trigger") or "daily"
    spec = (rec or {}).get("spec") or {}
    if trig == "weekly":
        return DAYS_PER_MONTH / 7.0
    if trig == "interval":
        every = max(1, int(spec.get("every_minutes", 60)))
        return 1440.0 / every * DAYS_PER_MONTH
    if trig == "once":
        return 0.0
    return DAYS_PER_MONTH            # daily, idle_daily


# ── The estimate ──────────────────────────────────────────────────────────────

def estimate(cfg: dict | None = None, records: list | None = None,
             measured: dict | None = None) -> dict:
    """Estimated monthly cost of running these jobs on their cloud models.

    For each job: runs per month (from its schedule, or the cloud cadence for
    the heartbeat) x tokens per run x the model's published price
    (cost_meter.PRICING). Tokens per run come from this PC's recorded runs
    (`measured_per_run`, basis "measured") when there are enough of them, else
    from `per_run_tokens`, whose constants document where each number comes
    from (basis "assumed"). Either way it is labelled an estimate. A job
    switched off is listed but not counted. `measured` ({sid: measurement or
    None}) replaces the costs.db lookup, for callers that already have it.
    """
    from agent_friday.services import cost_meter
    cfg = cfg or settings()
    if records is None:
        try:
            from agent_friday.services import scheduler
            records = scheduler.list_schedules()
        except Exception:
            records = []
    by_id = {r.get("id"): r for r in records or []}
    jobs, total = [], 0.0
    for sid, name in JOBS:
        rec = by_id.get(sid)
        model = model_for(sid, cfg)
        price = cost_meter.price_for(model) or {"in": 0.0, "out": 0.0}
        meas = (measured or {}).get(sid) if measured is not None else measured_per_run(sid)
        if meas:
            toks, basis = {"input": meas["input"], "output": meas["output"]}, "measured"
        else:
            toks, basis = per_run_tokens(sid, rec), "assumed"
        per_run = toks["input"] / 1000.0 * price["in"] + toks["output"] / 1000.0 * price["out"]
        runs = runs_per_month(sid, rec, cfg)
        enabled = bool((rec or {}).get("enabled", True))
        monthly = per_run * runs
        if enabled:
            total += monthly
        jobs.append({
            "id": sid, "name": name, "enabled": enabled,
            "model": model, "model_label": model_label(model),
            "runs_per_month": round(runs, 1),
            "input_tokens_per_run": toks["input"],
            "output_tokens_per_run": toks["output"],
            "usd_per_run": round(per_run, 4),
            "usd_per_month": round(monthly, 2),
            # Where the token figures came from: this PC's recorded runs, or
            # the documented constants. Both are estimates of a future bill.
            "basis": basis,
            "runs_measured": (meas or {}).get("runs", 0),
        })
    return {"jobs": jobs, "total_usd_per_month": round(total, 2),
            "is_estimate": True,
            "heartbeat_every_minutes": cfg["heartbeat_every_minutes"],
            "heartbeat_window": [cfg["heartbeat_from_hour"], cfg["heartbeat_to_hour"]],
            "heartbeat_runs_per_day": heartbeat_runs_per_day(cfg),
            "assumptions": list(ASSUMPTIONS)}


def view() -> dict:
    """The answer, the models and the estimate, for the Spending panel."""
    cfg = settings()
    local = None
    try:
        from agent_friday.services import setup_reader
        local = setup_reader.local_model()
    except Exception:
        local = None
    # Whether the routing mode itself sends the routine jobs to the cloud, so
    # the panel says what actually happens (cloud_intent.background_route).
    try:
        from agent_friday.services import cloud_intent
        route = cloud_intent.background_route("sch_heartbeat", local_serving=True)
        by_mode = route.route == "cloud"
    except Exception:
        by_mode = False
    return {"settings": cfg, "estimate": estimate(cfg),
            "local_model": local or "",
            "cloud_by_mode": by_mode,
            "cadences": list(HEARTBEAT_CADENCES)}


# ── The paused notice ─────────────────────────────────────────────────────────

def notify_paused(cfg: dict | None = None) -> None:
    """One self-updating status entry saying the jobs are paused and why.

    A status entry (notifications_engine.upsert_status) is updated in place,
    so an hourly skip never adds a second row or bumps the unread badge.
    """
    cfg = cfg or settings()
    try:
        import agent_friday.notifications_engine as ne
    except Exception:
        return
    body = ("This PC has no local model running, so the daily creation and "
            "heartbeat are skipped instead of being sent to a paid cloud model, "
            "and the News front pages and briefings wait for it. ")
    body += ("You chose to keep them off the cloud. " if cfg.get("answered") else "")
    body += "To let them use a cloud model, open Settings > Spending."
    try:
        ne.upsert_status(key=PAUSED_NOTICE_KEY,
                         title="Scheduled jobs are paused: no local model",
                         body=body, source="scheduler", kind="scheduled_status",
                         priority="low", emitter="E10",
                         target={"workspace": "settings", "tab": "costs"})
    except Exception as e:
        _log.debug("paused notice failed: %s", e)


def clear_paused_notice() -> None:
    try:
        import agent_friday.notifications_engine as ne
        for n in ne.list_notifications(limit=500):
            if n.get("dedupe_key") == PAUSED_NOTICE_KEY:
                ne.dismiss(n.get("id"))
    except Exception as e:
        _log.debug("could not clear the paused notice: %s", e)
