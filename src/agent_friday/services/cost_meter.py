"""Cost metering for every model call (Part D of Self-Sufficient Friday).

Completes the in-memory ``CostTracker`` (model_router.py) into a durable,
queryable spend ledger:

  * **Per-direction pricing** — input and output tokens priced separately
    (output is ~5× input), sourced from a ``PRICING`` table with a fallback to
    ``provider_registry``'s blended ``cost_per_1k`` for unknown models.
  * **SQLite store** at ``~/.friday/costs.db`` — one ``cost_calls`` table with
    indexes on ts / workspace / provider, so range-bounded aggregations grouped
    by provider/workspace/model/kind are index scans, not full reads.
  * **Buffered writes** flushed off the hot path (every ~10s or 50 rows) so
    metering never adds latency to a model call.
  * **Attribution** — each row carries workspace, kind (chat | task | scheduled
    | compaction | briefing | voice), schedule_id, and run_id. The scheduler and
    the cost_attribution tool-hook feed attribution in so per-workspace and
    per-schedule breakdowns work.
  * **Budget alerts** — configurable daily/monthly USD thresholds; crossing 80%
    / 100% pushes a deduped notification.

Stdlib ``sqlite3`` only — no new dependency.
"""

import json
import sqlite3
import threading
import time as _time
from datetime import datetime, timezone

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR, _load_settings

DB_PATH = FRIDAY_DIR / "costs.db"

# ── Per-direction pricing (USD per 1K tokens) ────────────────────────────────
# Real pricing is input ≠ output. Unknown models fall back to the blended
# provider_registry rate (used for both directions) or 0 for local/on-device.
PRICING = {
    "claude-opus-4-8":            {"in": 0.015, "out": 0.075},
    "claude-sonnet-4-6":          {"in": 0.003, "out": 0.015},
    "claude-haiku-4-5-20251001":  {"in": 0.001, "out": 0.005},
    "gpt-4o":                     {"in": 0.0025, "out": 0.010},
    "gpt-4o-mini":                {"in": 0.00015, "out": 0.0006},
    "o3":                         {"in": 0.010, "out": 0.040},
    "gemini-2.5-pro":             {"in": 0.00125, "out": 0.010},
    "gemini-2.5-flash":           {"in": 0.0003, "out": 0.0025},
    "gemini-3.1-flash-live-preview": {"in": 0.0005, "out": 0.002},
}


def price_for(model):
    """Return {'in', 'out'} USD-per-1K for a model. Local models → 0."""
    if not model:
        return {"in": 0.0, "out": 0.0}
    if model in PRICING:
        return PRICING[model]
    try:
        from agent_friday.routing.model_router import provider_family
        if provider_family(model) == "local":
            return {"in": 0.0, "out": 0.0}
    except Exception:
        pass
    # Fallback: blended provider_registry rate applied to both directions.
    try:
        from services import provider_registry as _pr
        for prov in _pr.list_providers() if hasattr(_pr, "list_providers") else []:
            rate = (prov.get("cost_per_1k") or {}).get(model)
            if rate:
                return {"in": float(rate), "out": float(rate)}
    except Exception:
        pass
    return {"in": 0.0, "out": 0.0}


def cost_for(model, input_tokens, output_tokens):
    p = price_for(model)
    return round((input_tokens / 1000.0) * p["in"]
                 + (output_tokens / 1000.0) * p["out"], 6)


# ── Attribution context (thread-local + per-task map) ────────────────────────
_LOCAL = threading.local()
_TASK_ATTR: dict = {}
_TASK_ATTR_LOCK = threading.Lock()


def push_attribution(**fields):
    """Set attribution for the current thread (e.g. a scheduler builtin run)."""
    stack = getattr(_LOCAL, "stack", None)
    if stack is None:
        stack = _LOCAL.stack = []
    stack.append({k: v for k, v in fields.items() if v is not None})


def pop_attribution():
    stack = getattr(_LOCAL, "stack", None)
    if stack:
        stack.pop()


def _local_attr():
    stack = getattr(_LOCAL, "stack", None)
    return dict(stack[-1]) if stack else {}


def register_task_attribution(task_id, fields):
    """Associate a spawned task id with attribution (used for agent_prompt
    scheduled tasks, whose model calls run on a separate worker thread)."""
    if not task_id:
        return
    with _TASK_ATTR_LOCK:
        _TASK_ATTR[task_id] = {k: v for k, v in (fields or {}).items() if v is not None}
        if len(_TASK_ATTR) > 2000:                       # bound the map
            for k in list(_TASK_ATTR)[:1000]:
                _TASK_ATTR.pop(k, None)


def lookup_task_attribution(task_id):
    with _TASK_ATTR_LOCK:
        return dict(_TASK_ATTR.get(task_id) or {})


def note_tool_attribution(ctx):
    """PostToolUse cost_attribution hook seam — make the active turn's workspace
    available on this thread so nested model calls inherit it. Never raises."""
    try:
        push_attribution(workspace=ctx.workspace or None, run_id=ctx.run_id,
                         schedule_id=ctx.schedule_id)
        pop_attribution()   # we only needed the side-effect-free resolution path
    except Exception:
        pass


def _resolve_attr(session_ctx, explicit):
    """Merge attribution from explicit args → session_ctx → task map → thread."""
    attr = dict(_local_attr())
    sc = session_ctx or {}
    tid = sc.get("task_id")
    if tid:
        attr.update(lookup_task_attribution(tid))
    for key in ("workspace", "kind", "schedule_id", "run_id"):
        if sc.get(key) is not None:
            attr[key] = sc.get(key)
    for key, val in (explicit or {}).items():
        if val is not None:
            attr[key] = val
    return attr


# ── SQLite store + buffered writer ───────────────────────────────────────────
_CONN = None
_CONN_LOCK = threading.Lock()
_BUFFER = []
_BUFFER_LOCK = threading.Lock()
_FLUSH_THREAD_STARTED = False
_LAST_ALERT = {}                       # dedupe key -> day/month string


def _conn():
    global _CONN
    with _CONN_LOCK:
        if _CONN is None:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            _CONN = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            _CONN.execute("""
                CREATE TABLE IF NOT EXISTS cost_calls (
                  id INTEGER PRIMARY KEY, ts REAL, provider TEXT, model TEXT,
                  input_tokens INT, output_tokens INT, cost_usd REAL,
                  duration_ms INT, workspace TEXT, kind TEXT,
                  schedule_id TEXT, run_id TEXT
                )""")
            for idx, col in (("idx_cost_ts", "ts"), ("idx_cost_ws", "workspace"),
                             ("idx_cost_prov", "provider")):
                _CONN.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON cost_calls({col})")
            _CONN.commit()
        return _CONN


def _maybe_start_flusher():
    global _FLUSH_THREAD_STARTED
    if _FLUSH_THREAD_STARTED or core._TESTING:
        return
    _FLUSH_THREAD_STARTED = True

    def _loop():
        while True:
            _time.sleep(10)
            try:
                flush()
            except Exception as e:
                print(f"  [cost-meter] flush failed: {e}")

    threading.Thread(target=_loop, daemon=True).start()


def flush():
    """Persist buffered rows. Cheap no-op when the buffer is empty."""
    with _BUFFER_LOCK:
        if not _BUFFER:
            return 0
        rows = _BUFFER[:]
        _BUFFER.clear()
    conn = _conn()
    with _CONN_LOCK:
        conn.executemany(
            "INSERT INTO cost_calls (ts, provider, model, input_tokens, "
            "output_tokens, cost_usd, duration_ms, workspace, kind, "
            "schedule_id, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    return len(rows)


def record(provider, model, input_tokens=0, output_tokens=0, *, duration_ms=0,
           session_ctx=None, workspace=None, kind=None, schedule_id=None,
           run_id=None):
    """Record one model call. Buffered; flushed off the hot path.

    Also mirrors into the legacy in-memory CostTracker so existing savings stats
    keep working. Never raises — metering must not break a model call.
    """
    try:
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        attr = _resolve_attr(session_ctx, {
            "workspace": workspace, "kind": kind,
            "schedule_id": schedule_id, "run_id": run_id})
        cost = cost_for(model, input_tokens, output_tokens)
        row = (_time.time(), provider or "", model or "", input_tokens,
               output_tokens, cost, int(duration_ms or 0),
               attr.get("workspace") or "", attr.get("kind") or "chat",
               attr.get("schedule_id"), attr.get("run_id"))
        with _BUFFER_LOCK:
            _BUFFER.append(row)
            over = len(_BUFFER) >= 50
        _maybe_start_flusher()
        if over or core._TESTING:
            flush()
        # Mirror into the legacy tracker (savings vs. all-cloud line).
        try:
            from agent_friday.routing.model_router import get_router
            get_router().cost_tracker.record(provider, model,
                                             prompt_tokens=input_tokens,
                                             completion_tokens=output_tokens)
        except Exception:
            pass
        # Budget tripwire (cheap; reads buffered+stored rolling spend).
        try:
            _check_budget_alerts()
        except Exception:
            pass
        return cost
    except Exception as e:  # noqa: BLE001
        print(f"  [cost-meter] record failed: {e}")
        return 0.0


def meter(provider, model, usage, *, duration_ms=0, session_ctx=None, kind=None):
    """Record from a provider ``usage`` object/dict. Maps both Anthropic
    (input_tokens/output_tokens) and OpenAI (prompt_tokens/completion_tokens)
    shapes onto the per-direction schema."""
    def _get(obj, *keys):
        for k in keys:
            if isinstance(obj, dict):
                if obj.get(k) is not None:
                    return obj[k]
            elif getattr(obj, k, None) is not None:
                return getattr(obj, k)
        return 0
    if usage is None:
        return 0.0
    in_tok = _get(usage, "input_tokens", "prompt_tokens")
    out_tok = _get(usage, "output_tokens", "completion_tokens")
    return record(provider, model, in_tok, out_tok, duration_ms=duration_ms,
                  session_ctx=session_ctx, kind=kind)


# ── Queries ──────────────────────────────────────────────────────────────────
def _range_bounds(rng, frm=None, to=None):
    now = _time.time()
    if rng == "today":
        start = datetime.now().replace(hour=0, minute=0, second=0,
                                       microsecond=0).timestamp()
        return start, now
    if rng == "7d":
        return now - 7 * 86400, now
    if rng == "month":
        start = datetime.now().replace(day=1, hour=0, minute=0, second=0,
                                       microsecond=0).timestamp()
        return start, now
    if rng == "custom" and frm and to:
        return float(frm), float(to)
    return now - 86400, now


def summary(rng="today", frm=None, to=None):
    flush()
    start, end = _range_bounds(rng, frm, to)
    conn = _conn()
    with _CONN_LOCK:
        cur = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), "
            "COALESCE(SUM(output_tokens),0), COALESCE(SUM(cost_usd),0) "
            "FROM cost_calls WHERE ts>=? AND ts<=?", (start, end))
        n, itok, otok, total = cur.fetchone()

        def _group(col):
            rows = conn.execute(
                f"SELECT COALESCE({col},''), COUNT(*), COALESCE(SUM(cost_usd),0) "
                f"FROM cost_calls WHERE ts>=? AND ts<=? GROUP BY {col}",
                (start, end)).fetchall()
            return {(r[0] or "unknown"): {"calls": r[1], "usd": round(r[2], 4)}
                    for r in rows}

        out = {
            "range": rng, "from": start, "to": end,
            "total_usd": round(total, 4), "total_calls": n,
            "input_tokens": itok, "output_tokens": otok,
            "by_provider": _group("provider"),
            "by_workspace": _group("workspace"),
            "by_model": _group("model"),
            "by_kind": _group("kind"),
        }
    return out


def timeseries(rng="month", bucket="day"):
    flush()
    start, end = _range_bounds(rng)
    conn = _conn()
    with _CONN_LOCK:
        rows = conn.execute(
            "SELECT ts, cost_usd FROM cost_calls WHERE ts>=? AND ts<=? ORDER BY ts",
            (start, end)).fetchall()
    buckets = {}
    for ts, cost in rows:
        key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        b = buckets.setdefault(key, {"date": key, "usd": 0.0, "calls": 0})
        b["usd"] += cost
        b["calls"] += 1
    return [{"date": k, "usd": round(v["usd"], 4), "calls": v["calls"]}
            for k, v in sorted(buckets.items())]


def by_schedule(rng="month"):
    flush()
    start, end = _range_bounds(rng)
    conn = _conn()
    with _CONN_LOCK:
        rows = conn.execute(
            "SELECT schedule_id, COUNT(*), COALESCE(SUM(cost_usd),0) "
            "FROM cost_calls WHERE ts>=? AND ts<=? AND schedule_id IS NOT NULL "
            "AND schedule_id<>'' GROUP BY schedule_id ORDER BY 3 DESC",
            (start, end)).fetchall()
    return [{"schedule_id": r[0], "calls": r[1], "usd": round(r[2], 4)} for r in rows]


def _rolling_spend():
    """(today_usd, month_usd) including buffered-but-unflushed rows."""
    flush()
    conn = _conn()
    today_start = datetime.now().replace(hour=0, minute=0, second=0,
                                         microsecond=0).timestamp()
    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0,
                                         microsecond=0).timestamp()
    with _CONN_LOCK:
        today = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM cost_calls "
                             "WHERE ts>=?", (today_start,)).fetchone()[0]
        month = conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM cost_calls "
                             "WHERE ts>=?", (month_start,)).fetchone()[0]
    return round(today, 4), round(month, 4)


# ── Budget alerts ────────────────────────────────────────────────────────────
def get_budget():
    cfg = (_load_settings().get("cost_budget") or {})
    return {"daily": cfg.get("daily", 0), "monthly": cfg.get("monthly", 0),
            "daily_enabled": cfg.get("daily_enabled", False),
            "monthly_enabled": cfg.get("monthly_enabled", False)}


def set_budget(patch):
    from agent_friday.core import _load_settings_raw, _save_settings
    cfg = dict((_load_settings_raw().get("cost_budget") or {}))
    for k in ("daily", "monthly", "daily_enabled", "monthly_enabled"):
        if k in (patch or {}):
            cfg[k] = patch[k]
    _save_settings({"cost_budget": cfg})
    return get_budget()


def _push_budget_alert(period, pct, spend, limit):
    try:
        from agent_friday.services.voice_engine import _notif_engine as _ne
    except Exception:
        _ne = None
    if not _ne:
        return
    stamp = datetime.now().strftime("%Y-%m-%d" if period == "daily" else "%Y-%m")
    dk = f"budget:{period}:{'100' if pct >= 100 else '80'}:{stamp}"
    if _LAST_ALERT.get(dk):
        return
    _LAST_ALERT[dk] = stamp
    _ne.push(
        title=("🛑 " if pct >= 100 else "⚠️ ")
              + f"{period.capitalize()} cost budget {'exceeded' if pct >= 100 else 'at ' + str(int(pct)) + '%'}",
        body=f"${spend:.2f} of ${limit:.2f} {period} budget used.",
        priority="high" if pct >= 100 else "medium",
        source="cost-meter", kind="budget_alert", dedupe_key=dk,
        target={"workspace": "system", "tab": "costs"})


def _check_budget_alerts():
    b = get_budget()
    if not (b["daily_enabled"] or b["monthly_enabled"]):
        return
    today, month = _rolling_spend()
    if b["daily_enabled"] and b["daily"] > 0:
        pct = today / b["daily"] * 100
        if pct >= 80:
            _push_budget_alert("daily", pct, today, b["daily"])
    if b["monthly_enabled"] and b["monthly"] > 0:
        pct = month / b["monthly"] * 100
        if pct >= 80:
            _push_budget_alert("monthly", pct, month, b["monthly"])


def reset_for_tests():
    """Drop the in-memory connection + buffer (tests use a fresh temp-home DB)."""
    global _CONN
    with _BUFFER_LOCK:
        _BUFFER.clear()
    with _CONN_LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
        _CONN = None
    _LAST_ALERT.clear()
    with _TASK_ATTR_LOCK:
        _TASK_ATTR.clear()
