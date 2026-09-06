"""Hard spending cap — the stop that actually stops.

The maintainer's ruling: "spending cap alerts. we do want a stopping
cap available to the user though." Two caps, not one:

  * the ALERT cap (`cost_budget.daily/monthly`) is the default and is
    unchanged -- crossing 80% warns, 100% alerts, and work continues. It is
    the cap that let a $1,189 month happen against a $50 limit, and that is
    by design: it never blocks.
  * the HARD STOP (`cost_budget.hard_stop_daily/monthly`, off by default)
    halts CLOUD spend when reached. Local models keep working -- the cap is
    about money leaving the machine, and Ollama costs nothing.

Neither is imposed; the user chooses, the same shape as local/cloud.

WHERE IT STOPS. check() sits at the choke points every paid call already
passes through: model_router._seal_or_block (every text/tool cloud call),
creative_engine.generate_image/generate_video (Gemini/Veo/Higgsfield/kie),
the Gemini Live session open (routes/voice.py), and firecrawl._post. The
trip is DETECTED in cost_meter.record(), the moment a recorded call pushes
the rolling spend over the limit -- so the notification lands at crossing,
not at the next attempt.

WHAT "STOP" MEANS AT THE BOUNDARY (decided here, not deferred):
  1. A request already in flight finishes. It has been sent and will be
     billed either way; discarding its answer would waste what was spent.
     Its cost is recorded, and that record is what trips the cap.
  2. A multi-step task / agentic loop halts at its NEXT cloud call. The
     tool loop's existing failure path marks the task failed with a
     "[Halted by hard spending cap]" result, the task log keeps every step
     that ran, files already written stay written, and the halt ledger
     names the task so it can be re-run once the cap is raised. Nothing is
     rolled back and nothing is retried against the cap.
  3. A scheduled job that trips the cap at 3am fails fast at the guard on
     that run and every later run until the cap is lifted -- each attempt
     costs nothing, so the scheduler cannot hammer money out. It gets its
     own notification (deduped per schedule per period) so the halt is
     visible in the morning, not buried under the first one.
  4. A live voice session already open is not cut mid-sentence; the NEXT
     session refuses to open.
  5. Local providers are never touched. If a local model can do the job,
     Friday keeps working on it.

NEVER SILENT. Every halt writes a row to ~/.friday/spend_halts.jsonl (what
stopped, why, spend vs limit, attribution) and pushes a HIGH-priority
notification saying exactly that plus what to do: raise the cap, switch the
hard stop off, or use a local model. The trip itself is one notification
per period; each distinct halted thing gets one more.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from agent_friday.core import FRIDAY_DIR

_log = logging.getLogger("friday.spend_guard")

HALT_LOG = FRIDAY_DIR / "spend_halts.jsonl"
_LOCK = threading.Lock()
_NOTIFIED: Dict[str, str] = {}      # dedupe key -> stamp

PERIODS = ("daily", "monthly")


class SpendCapReached(RuntimeError):
    """Raised at a cloud choke point when the hard stop has tripped.

    A RuntimeError on purpose: model_router's callers already convert a
    raising gate into a blocked send, so this rides the same rail.
    """

    def __init__(self, period: str, spend: float, limit: float, what: str):
        self.period, self.spend, self.limit, self.what = period, spend, limit, what
        super().__init__(
            f"Hard spending cap reached: ${spend:.2f} of ${limit:.2f} "
            f"{period} cap already spent, so {what} was not sent to the cloud. "
            f"Local models still work. To resume cloud work: raise the cap or "
            f"switch the hard stop off in Settings > Cost & Usage."
        )


# ── configuration ────────────────────────────────────────────────────────────

def get_hard_stop() -> Dict[str, Any]:
    from agent_friday.services import cost_meter as _cm
    b = _cm.get_budget()
    return {p: float(b.get(f"hard_stop_{p}") or 0) for p in PERIODS} | {
        f"{p}_enabled": bool(b.get(f"hard_stop_{p}_enabled")) for p in PERIODS}


def enabled() -> bool:
    hs = get_hard_stop()
    return any(hs[f"{p}_enabled"] and hs[p] > 0 for p in PERIODS)


# ── evaluation ───────────────────────────────────────────────────────────────

def tripped() -> Optional[Dict[str, Any]]:
    """{period, spend, limit} for the first tripped period, else None.

    Reads REAL recorded spend (cost_meter's rolling totals, buffered rows
    flushed) -- never a cached flag that could say 'stopped' while money
    keeps moving, or 'fine' after it has already gone.
    """
    hs = get_hard_stop()
    if not any(hs[f"{p}_enabled"] and hs[p] > 0 for p in PERIODS):
        return None
    from agent_friday.services import cost_meter as _cm
    today, month = _cm._rolling_spend()
    spend = {"daily": today, "monthly": month}
    for p in PERIODS:
        if hs[f"{p}_enabled"] and hs[p] > 0 and spend[p] >= hs[p]:
            return {"period": p, "spend": round(spend[p], 4), "limit": hs[p]}
    return None


def _is_cloud(provider: str) -> bool:
    try:
        from agent_friday.services.egress_gate import _is_cloud
        return bool(_is_cloud(provider or ""))
    except Exception:
        return True   # uncertain = paid, same default as the egress gate


def _attribution() -> Dict[str, Any]:
    try:
        from agent_friday.services import cost_meter as _cm
        return {k: v for k, v in _cm._local_attr().items() if v}
    except Exception:
        return {}


def _describe(what: Optional[str], provider: str, attr: Dict[str, Any]) -> str:
    bits = [what or f"a {provider} call"]
    if attr.get("schedule_id"):
        bits.append(f"scheduled job '{attr['schedule_id']}'")
    if attr.get("workspace"):
        bits.append(f"workspace '{attr['workspace']}'")
    if attr.get("task_id"):
        bits.append(f"task {attr['task_id']}")
    return " · ".join(bits)


def check(provider: str, *, what: Optional[str] = None) -> None:
    """Refuse a cloud call when the hard stop has tripped. Never raises for
    local providers, never raises when the hard stop is off, and never
    raises for any reason other than the cap (a guard that fails must not
    become a guard that halts everything -- the alert cap is the fallback,
    and the failure is logged loudly)."""
    try:
        if not _is_cloud(provider):
            return
        t = tripped()
    except SpendCapReached:
        raise
    except Exception as e:
        _log.error("spend guard could not evaluate the hard stop (%s) -- "
                   "call allowed, alert cap still active", e)
        return
    if not t:
        return
    attr = _attribution()
    desc = _describe(what, provider, attr)
    _record_halt(t, provider, desc, attr)
    raise SpendCapReached(t["period"], t["spend"], t["limit"], desc)


# ── the loud part ────────────────────────────────────────────────────────────

def _stamp(period: str) -> str:
    return datetime.now().strftime("%Y-%m-%d" if period == "daily" else "%Y-%m")


def _push(title: str, body: str, dedupe_key: str, priority: str = "high") -> None:
    with _LOCK:
        if _NOTIFIED.get(dedupe_key):
            return
        _NOTIFIED[dedupe_key] = dedupe_key
    try:
        import agent_friday.notifications_engine as _ne
        _ne.push(title=title, body=body, priority=priority, source="spend-guard",
                 kind="spend_halt", dedupe_key=dedupe_key,
                 target={"workspace": "system", "tab": "costs"})
    except Exception as e:
        _log.warning("spend halt notification failed: %s", e)


def _resume_hint() -> str:
    return ("Local models keep working. To resume cloud work: raise the cap, "
            "or switch the hard stop off, in Settings > Cost & Usage.")


def notify_if_tripped() -> Optional[Dict[str, Any]]:
    """Called from cost_meter.record() after every recorded call: the moment
    a call pushes spend over the hard stop, say so -- once per period."""
    try:
        t = tripped()
    except Exception:
        return None
    if not t:
        return None
    p = t["period"]
    _push(
        f"🛑 Hard spending cap reached — cloud work stopped",
        f"${t['spend']:.2f} of your ${t['limit']:.2f} {p} cap is spent. Friday "
        f"will refuse every further cloud call this {'day' if p == 'daily' else 'month'}; "
        f"anything already in flight finishes. {_resume_hint()}",
        dedupe_key=f"spend_halt:trip:{p}:{_stamp(p)}",
    )
    return t


def _record_halt(t: Dict[str, Any], provider: str, desc: str, attr: Dict[str, Any]) -> None:
    row = {"ts": time.time(), "period": t["period"], "spend": t["spend"],
           "limit": t["limit"], "provider": provider, "what": desc, **attr}
    try:
        HALT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with HALT_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning("could not write spend halt ledger: %s", e)
    p = t["period"]
    # One notification per distinct halted THING per period, so a 3am
    # scheduled job's halt is its own line in the morning, not lost behind
    # the trip notification.
    key_bits = attr.get("schedule_id") or attr.get("task_id") or desc
    _push(
        f"🛑 Stopped by the hard spending cap: {desc[:80]}",
        f"Not sent to {provider}: {desc}. ${t['spend']:.2f} of ${t['limit']:.2f} "
        f"{p} cap already spent. Nothing was rolled back -- work done so far is "
        f"kept and this can be re-run once the cap is lifted. {_resume_hint()}",
        dedupe_key=f"spend_halt:{p}:{_stamp(p)}:{key_bits}",
    )
    _log.warning("HARD SPENDING CAP: refused %s (%s) -- $%.2f of $%.2f %s",
                 desc, provider, t["spend"], t["limit"], p)


# ── status for the UI / API ──────────────────────────────────────────────────

def recent_halts(limit: int = 20) -> list:
    try:
        if not HALT_LOG.exists():
            return []
        lines = HALT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        for ln in reversed(lines[-limit:]):
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    except Exception:
        return []


def status() -> Dict[str, Any]:
    hs = get_hard_stop()
    t = None
    try:
        t = tripped()
    except Exception as e:
        _log.warning("spend guard status: %s", e)
    return {"hard_stop": hs, "enabled": enabled(), "tripped": t,
            "recent_halts": recent_halts(), "resume": _resume_hint()}


def reset_for_tests() -> None:
    with _LOCK:
        _NOTIFIED.clear()
