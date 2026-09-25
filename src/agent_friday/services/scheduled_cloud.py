"""Whether the built-in scheduled jobs may use a cloud model, and what it costs.

The morning news, the evening front page, the afternoon briefing, daily
creation and the heartbeat are local-only by default
(`scheduler.LOCAL_ONLY_BY_DEFAULT`): on a PC with a local model serving they
run there at no cost, and that does not change here. On a PC with no local
model they are skipped, because running them in the cloud costs money and
nobody chose that.

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
"""
from __future__ import annotations

import logging
import math
from datetime import datetime

_log = logging.getLogger("friday.scheduled_cloud")

#: The jobs this choice covers, in the order they are shown, with plain names.
JOBS = (
    ("sch_news_morning", "Morning news"),
    ("sch_front_page_evening", "Evening front page"),
    ("sch_afternoon_briefing", "Afternoon briefing"),
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

ASSUMPTIONS = (
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
            raise ValueError("heartbeat_every_minutes must be a number of minutes")
        if every not in HEARTBEAT_CADENCES:
            raise ValueError("heartbeat_every_minutes must be one of %s"
                             % ", ".join(str(c) for c in HEARTBEAT_CADENCES))
        new["heartbeat_every_minutes"] = every
    for key in ("heartbeat_model", "job_model"):
        if key in patch:
            if not _known_model(patch[key]):
                raise ValueError("%s must be a priced model" % key)
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

def estimate(cfg: dict | None = None, records: list | None = None) -> dict:
    """Estimated monthly cost of running these jobs on their cloud models.

    For each job: runs per month (from its schedule, or the cloud cadence for
    the heartbeat) x the per-run token estimate (`per_run_tokens`, whose
    constants document where each number comes from) x the model's published
    price (cost_meter.PRICING). A job switched off is listed but not counted.
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
        toks = per_run_tokens(sid, rec)
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
        })
    return {"jobs": jobs, "total_usd_per_month": round(total, 2),
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
    return {"settings": cfg, "estimate": estimate(cfg),
            "local_model": local or "",
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
    body = ("This PC has no local model running, so the morning news, evening "
            "front page, afternoon briefing, daily creation and heartbeat are "
            "skipped instead of being sent to a paid cloud model. ")
    body += ("You chose to keep them off the cloud. " if cfg.get("answered") else "")
    body += "To let them use a cloud model, open Settings > Spending."
    try:
        ne.upsert_status(key=PAUSED_NOTICE_KEY,
                         title="Scheduled jobs are paused: no local model",
                         body=body, source="scheduler", kind="scheduled_status",
                         priority="low",
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
