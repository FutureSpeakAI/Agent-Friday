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

# Google Workspace APIs (Calendar, Gmail, Drive — services/calendar_engine.py,
# calendar_write.py, google_accounts.py, routes/calendar.py) are deliberately
# NOT metered here. At realistic single-user personal-account volume these sit
# under Google's free/quota-based tier — the real risk is quota throttling or
# an error, not a bill — so there is nothing to record. Stated explicitly so
# a future reader can tell "verified free, intentionally unmetered" apart
# from "someone forgot" (docs/history/audits/gauntlet-2026-09-03/findings.jsonl).
#
# ── Per-direction pricing (USD per 1K tokens) ────────────────────────────────
# Real pricing is input ≠ output. Unknown models fall back to the blended
# provider_registry rate (used for both directions) or 0 for local/on-device.
PRICING = {
    # Published USD per 1M, divided by 1000. Checked against Anthropic's price
    # page 2026-08-28. Opus 5 sat at 0.015/0.075 (a 3x overcharge) and Fable 5
    # at 0.003/0.015 (a 3.3x undercharge) for the whole 5.6.x line, so the most
    # expensive model in the lineup was billed as the cheapest and the default
    # model was billed at triple. Every spend figure downstream inherited both.
    "claude-fable-5":             {"in": 0.010, "out": 0.050},   # $10 / $50
    "claude-opus-5":              {"in": 0.005, "out": 0.025},   # $5  / $25
    "claude-sonnet-5":            {"in": 0.003, "out": 0.015},   # $3  / $15
    # `claude-haiku-4-5` is the model id. The dated form is a legacy alias, and
    # keying ONLY on it meant a canonical-id call missed the table, fell through
    # the registry fallback (Haiku has no cost_per_1k there) and metered $0 --
    # which reads as "local, on-device, free" for a cloud call.
    "claude-haiku-4-5":           {"in": 0.001, "out": 0.005},   # $1  / $5
    "claude-haiku-4-5-20251001":  {"in": 0.001, "out": 0.005},
    "gpt-4o":                     {"in": 0.0025, "out": 0.010},
    "gpt-4o-mini":                {"in": 0.00015, "out": 0.0006},
    "o3":                         {"in": 0.010, "out": 0.040},
    "gemini-2.5-pro":             {"in": 0.00125, "out": 0.010},
    "gemini-2.5-flash":           {"in": 0.0003, "out": 0.0025},
    "gemini-3.1-flash-live-preview": {"in": 0.0005, "out": 0.002},
    # Native-audio Live models bill at the same live-session rate.
    "gemini-2.5-flash-native-audio-latest":          {"in": 0.0005, "out": 0.002},
    "gemini-2.5-flash-native-audio-preview-09-2025": {"in": 0.0005, "out": 0.002},
    "gemini-2.5-flash-native-audio-preview-12-2025": {"in": 0.0005, "out": 0.002},
    # Gemini 3.x lineup (ai.google.dev/gemini-api/docs/pricing, 2026-07).
    # 3.1 Pro uses the <=200k-token tier ($2/$12 per 1M).
    "gemini-3.5-flash":           {"in": 0.0015, "out": 0.009},
    "gemini-3.1-pro-preview":     {"in": 0.002, "out": 0.012},
    "gemini-3.1-flash-lite":      {"in": 0.00025, "out": 0.0015},
    # Omni Flash bills video output at $17.50/1M tokens (5,792 tok/s of
    # 720p ≈ $0.10/s); text output is $9/1M. Use the video rate — video
    # is the product. Both the friendly id and the wire id are metered.
    "gemini-omni-flash":          {"in": 0.0015, "out": 0.0175},
    "gemini-omni-flash-preview":  {"in": 0.0015, "out": 0.0175},
    # Gemini TTS (voice_engine.py _synthesize_tts_wav_gemini) — the exact model
    # id that function calls. ai.google.dev/gemini-api/docs/pricing, checked
    # 2026-09-04: $0.50/1M input (text) tokens, $10/1M output (audio) tokens.
    # Was completely unmetered before this fix despite the entries above for
    # the Live models already existing (docs/audits/gauntlet-2026-09-03/
    # findings.jsonl Q6c) — the TTS model id itself was also missing here.
    "gemini-2.5-flash-preview-tts": {"in": 0.0005, "out": 0.010},
    # Gemini native image models ("Nano Banana" family — creative_engine.py).
    # Google bills image OUTPUT as a fixed token count per image (not a flat
    # per-image charge at the API level, though it nets out to one); INPUT
    # here is the text prompt, priced at each model's ordinary text rate.
    # gemini-2.5-flash-image (Nano Banana): 1290 output tokens/image @ $30/1M
    # -> $0.039/image standard tier.
    # gemini-3-pro-image (Nano Banana Pro): $120/1M output at 1K/2K
    # resolution ($0.134/image); 4K resolution costs more per Google's own
    # tiering, which this flat per-token rate does not capture.
    # gemini-3.1-flash-image (Nano Banana 2): $0.50/1M input (text/image),
    # $60/1M output (1K/2K resolution).
    # gemini-3.1-flash-lite-image (Nano Banana 2 Lite): $0.25/1M input,
    # $30/1M output.
    # VERIFIED directly against ai.google.dev/gemini-api/docs/pricing,
    # 2026-09-04 (2026-09-04 gauntlet-audit correction: the two 3.1 rows were
    # previously CONSERVATIVE PLACEHOLDERS interpolated from the 2.5/3-pro
    # rows rather than looked up -- both were wrong, each understating the
    # real output rate by exactly 2x. Findings.jsonl F50 logs the lapse:
    # shipping an interpolated guess in a field a cost panel treats as fact,
    # right after recording the "don't fabricate rates" decision elsewhere
    # in the same pass.)
    "gemini-2.5-flash-image":      {"in": 0.0003, "out": 0.030},
    "gemini-3-pro-image":           {"in": 0.002, "out": 0.120},
    "gemini-3.1-flash-image":      {"in": 0.0005, "out": 0.060},
    "gemini-3.1-flash-lite-image": {"in": 0.00025, "out": 0.030},

    # ── ElevenLabs TTS (services/elevenlabs_tools.py _tool_speak_text) ──────
    # ElevenLabs bills per CHARACTER, not per token. Reusing this table's
    # "in"-per-1K slot as "USD per 1K CHARACTERS" lets the existing cost_for()
    # arithmetic work unchanged: the call site passes len(text) as
    # input_tokens and 0 as output_tokens. "out" is always 0 here — it is
    # never read for these rows, kept only for the table's shape.
    # VERIFIED directly against elevenlabs.io/pricing/api, 2026-09-04:
    # "v2 Multilingual & v3" models bill at $0.10/1K chars; "Flash/Turbo"
    # models bill at $0.05/1K chars.
    "eleven_multilingual_v2":    {"in": 0.10, "out": 0.0},   # $0.10 / 1K chars
    "eleven_multilingual_v3":    {"in": 0.10, "out": 0.0},
    "eleven_turbo_v2_5":         {"in": 0.05, "out": 0.0},   # flash/turbo tier
    "eleven_flash_v2_5":         {"in": 0.05, "out": 0.0},

    # ── Opt-in provider catalogs (routing/provider_descriptors.py
    #    BUILTIN_EXTRA_PROVIDERS) that previously metered as exactly $0 for
    #    every call (docs/history/audits/gauntlet-2026-09-03/findings.jsonl Q11b) --
    #    the _call_openai -> cost_meter.meter() code path already worked,
    #    this table was just empty for these five.
    #
    # Mistral declares its model ids explicitly in BUILTIN_EXTRA_PROVIDERS
    # (models=(...)), so this key is GUARANTEED to match what a real call
    # actually sends as `model`. VERIFIED directly against mistral.ai/pricing,
    # 2026-09-04: "$0.5/M tokens in and $1.5/M tokens out."
    "mistral-large-latest":  {"in": 0.0005, "out": 0.0015},
}

# ── Deliberately NOT priced (2026-09-04) ─────────────────────────────────────
# mistral-small-latest, deepseek-chat, deepseek-reasoner, and the well-known
# Groq/xAI/Cohere model ids all had rows here as of an earlier draft of this
# fix -- every one of them sourced from public pricing-aggregator pages
# (cloudzero.com, aipricing.guru, etc.), not the provider's own page, despite
# a real attempt to fetch each provider's own pricing page directly (Mistral's
# own page shows Mistral Large's rate but not Mistral Small's; DeepSeek's own
# docs page did not render a pricing table for this fetch and its listed
# current model ids -- deepseek-v4-flash/-pro -- don't even match
# deepseek-chat/deepseek-reasoner, suggesting those ids may be stale; Groq/
# xAI/Cohere's own pages were not confirmed either). Per this session's own
# "don't fabricate rates" decision -- violated once already for the Gemini
# 3.1 image rows above (findings.jsonl F50) -- these are recorded as UNPRICED
# rather than guessed at a second time. record()/price_for() below return
# None for a model in this set: the call is still logged (tokens, provider,
# model, timestamp), with cost_usd stored as NULL, not 0.0 -- an honest gap a
# UI can render as "not priced," not a number that looks like a fact.
# Revisit once a real per-model rate can be confirmed directly from each
# provider's own current pricing page. See findings.jsonl F50.
UNPRICED_MODELS = frozenset({
    "mistral-small-latest",
    "deepseek-chat", "deepseek-reasoner",
    "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768",  # Groq
    "grok-4", "grok-4-fast",  # xAI
    "command-r-plus", "command-r", "command-a",  # Cohere
})


#: Fast mode runs the SAME model at up to 2.5x output tokens/sec and bills at
#: its own rate -- it is a different price, not a faster same price. Opus 5 and
#: Opus 4.8 only; every other model ignores the request, so asking for it
#: elsewhere must not change the bill. Nothing in Friday sets speed='fast'
#: today: this is the meter being right in advance rather than a bug being
#: fixed, so that the first caller to want it cannot silently under-bill.
FAST_PRICING = {
    "claude-opus-5": {"in": 0.010, "out": 0.050},   # $10 / $50
}


def price_for(model, speed=None):
    """Return {'in', 'out'} USD-per-1K for a model. Local models → 0.

    ``speed='fast'`` selects the fast-mode rate where one exists. An unrecognised
    speed is not a licence to invent a rate: it falls back to standard, because
    a made-up number is worse than a known-conservative one.
    """
    if not model:
        return {"in": 0.0, "out": 0.0}
    if model in UNPRICED_MODELS:
        # Deliberately unpriced (see UNPRICED_MODELS above, findings.jsonl
        # F50) -- None is the "genuinely unknown, do not compute a number"
        # signal cost_for()/record() propagate through to a NULL cost_usd
        # row, distinct from a confirmed $0.0 (a real local model).
        return None
    if speed == "fast" and model in FAST_PRICING:
        return FAST_PRICING[model]
    if model in PRICING:
        return PRICING[model]
    try:
        from agent_friday.routing.model_router import provider_family
        if provider_family(model) == "local":
            return {"in": 0.0, "out": 0.0}
    except Exception:
        pass
    # Fallback: blended provider_registry rate applied to both directions.
    # (list_providers is a method on the registry object, not the module —
    # the old module-level hasattr check made this loop dead code and every
    # unknown cloud model metered at $0.)
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        for prov in get_provider_registry().list_providers():
            rate = (prov.get("cost_per_1k") or {}).get(model)
            if rate:
                return {"in": float(rate), "out": float(rate)}
    except Exception:
        pass
    return {"in": 0.0, "out": 0.0}


#: Anthropic prompt-cache multipliers on the INPUT rate. A cache read bills at
#: a tenth of the input price; writing a cache entry costs a quarter more than
#: sending the tokens plain, which is why the break-even is two requests and why
#: the 5-minute TTL is the right default for interactive turns.
CACHE_READ_MULT = 0.1
CACHE_WRITE_MULT = 1.25


def cost_for(model, input_tokens, output_tokens,
             cache_read_tokens=0, cache_write_tokens=0, speed=None):
    """USD for this call, or None when the model is deliberately unpriced
    (UNPRICED_MODELS) -- propagated through, not coerced to 0.0."""
    p = price_for(model, speed=speed)
    if p is None:
        return None
    return round((input_tokens / 1000.0) * p["in"]
                 + (cache_read_tokens / 1000.0) * p["in"] * CACHE_READ_MULT
                 + (cache_write_tokens / 1000.0) * p["in"] * CACHE_WRITE_MULT
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
            # ── Prompt-cache columns (2026-08-26). ──
            # Anthropic reports cached input SEPARATELY from `input_tokens`:
            # a cache hit shows up as `cache_read_input_tokens` and the row's
            # `input_tokens` counts only the uncached remainder. Without these
            # columns the ledger would show the bill falling and be unable to
            # say why — and a caching change that cannot be measured is a
            # hypothesis, not a saving. Added by migration so an existing
            # costs.db keeps its history.
            _cols = {r[1] for r in _CONN.execute("PRAGMA table_info(cost_calls)")}
            for _c in ("cache_read_tokens", "cache_write_tokens"):
                if _c not in _cols:
                    _CONN.execute(f"ALTER TABLE cost_calls ADD COLUMN {_c} INT DEFAULT 0")
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
            "schedule_id, run_id, cache_read_tokens, cache_write_tokens) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    return len(rows)


def record(provider, model, input_tokens=0, output_tokens=0, *, duration_ms=0,
           session_ctx=None, workspace=None, kind=None, schedule_id=None,
           run_id=None, cost_usd=None, cache_read_tokens=0,
           cache_write_tokens=0, speed=None):
    """Record one model call. Buffered; flushed off the hot path.

    ``cost_usd`` overrides the locally computed price — used when the provider
    reports an authoritative figure (OpenRouter usage accounting returns the
    exact billed cost per response, which beats local price math).

    Also mirrors into the legacy in-memory CostTracker so existing savings stats
    keep working. Never raises — metering must not break a model call.
    """
    try:
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        cache_read_tokens = int(cache_read_tokens or 0)
        cache_write_tokens = int(cache_write_tokens or 0)
        attr = _resolve_attr(session_ctx, {
            "workspace": workspace, "kind": kind,
            "schedule_id": schedule_id, "run_id": run_id})
        if cost_usd is not None:
            cost = round(float(cost_usd), 6)
        else:
            cost = cost_for(model, input_tokens, output_tokens,
                            cache_read_tokens, cache_write_tokens, speed=speed)
            if (cost == 0.0 or cost is None) and provider:
                # Enrichment tier: the pricing service knows discovery-cache and
                # descriptor prices for models the static PRICING table doesn't.
                # Tried even when cost_for() already said "unknown" (None) --
                # this is a genuinely separate, more current data source (live
                # discovery), not a second guess at the same guess. If it
                # ALSO comes back None, `cost` stays None and is stored as
                # SQL NULL below -- an honest "we don't know", never silently
                # coerced back to a $0.0 that would look like a verified free
                # call (findings.jsonl F50).
                try:
                    from agent_friday.services import pricing as _pricing
                    live = _pricing.cost_usd(provider, model,
                                             input_tokens, output_tokens)
                    if live is not None:
                        cost = live
                except Exception:
                    pass
        row = (_time.time(), provider or "", model or "", input_tokens,
               output_tokens, cost, int(duration_ms or 0),
               attr.get("workspace") or "", attr.get("kind") or "chat",
               attr.get("schedule_id"), attr.get("run_id"),
               cache_read_tokens, cache_write_tokens)
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
    shapes onto the per-direction schema. An OpenRouter-style ``usage.cost``
    (exact billed USD, present when usage accounting is requested) is honored
    as the authoritative cost for the row."""
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
    # Anthropic prompt caching: `input_tokens` excludes both of these, so a
    # row without them understates the call and hides whether caching worked.
    cache_read = _get(usage, "cache_read_input_tokens")
    cache_write = _get(usage, "cache_creation_input_tokens")
    reported = _get(usage, "cost")  # OpenRouter usage accounting (USD)
    try:
        cost_usd = float(reported) if reported not in (0, None, "") else None
    except (TypeError, ValueError):
        cost_usd = None
    return record(provider, model, in_tok, out_tok, duration_ms=duration_ms,
                  session_ctx=session_ctx, kind=kind, cost_usd=cost_usd,
                  cache_read_tokens=cache_read, cache_write_tokens=cache_write)


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
            "COALESCE(SUM(output_tokens),0), COALESCE(SUM(cost_usd),0), "
            "COALESCE(SUM(cache_read_tokens),0), "
            "COALESCE(SUM(cache_write_tokens),0) "
            "FROM cost_calls WHERE ts>=? AND ts<=?", (start, end))
        n, itok, otok, total, crtok, cwtok = cur.fetchone()
        # Calls with cost_usd IS NULL: a model this build could not verify a
        # real rate for (cost_meter.UNPRICED_MODELS), logged honestly rather
        # than guessed at (findings.jsonl F50). Surfaced so the cost panel
        # can show "N calls not priced" instead of folding them silently
        # into total_usd as if they cost nothing.
        unpriced = conn.execute(
            "SELECT COUNT(*) FROM cost_calls WHERE ts>=? AND ts<=? "
            "AND cost_usd IS NULL", (start, end)).fetchone()[0]

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
            "unpriced_calls": unpriced,
            "input_tokens": itok, "output_tokens": otok,
            # The prompt-cache receipt. `cache_hit_rate` is the share of all
            # input tokens that were served from cache at 0.1x — the one number
            # that says whether the breakpoints are landing. A rate near zero
            # with caching enabled means the prefix is being broken upstream,
            # not that caching is unavailable.
            "cache_read_tokens": crtok, "cache_write_tokens": cwtok,
            "cache_hit_rate": (round(crtok / (itok + crtok + cwtok), 4)
                               if (itok + crtok + cwtok) else 0.0),
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
        # cost is NULL for a call whose model this build could not verify a
        # real rate for (UNPRICED_MODELS, findings.jsonl F50/F64) -- summary()
        # already excludes NULL from its SQL-level SUM via COALESCE; this
        # loop sums in Python, so it must skip None explicitly or `+=` raises
        # TypeError the first time an unpriced call lands in the range
        # (found via a real crash: F64's investigation of an order-dependent
        # test failure traced to exactly this -- not hypothetical).
        if cost is not None:
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
BUDGET_KEYS = ("daily", "monthly", "daily_enabled", "monthly_enabled",
               # the hard stop (services/spend_guard) -- a second, separate cap
               "hard_stop_daily", "hard_stop_monthly",
               "hard_stop_daily_enabled", "hard_stop_monthly_enabled")


def get_budget():
    cfg = (_load_settings().get("cost_budget") or {})
    return {"daily": cfg.get("daily", 0), "monthly": cfg.get("monthly", 0),
            "daily_enabled": cfg.get("daily_enabled", False),
            "monthly_enabled": cfg.get("monthly_enabled", False),
            "hard_stop_daily": cfg.get("hard_stop_daily", 0),
            "hard_stop_monthly": cfg.get("hard_stop_monthly", 0),
            "hard_stop_daily_enabled": cfg.get("hard_stop_daily_enabled", False),
            "hard_stop_monthly_enabled": cfg.get("hard_stop_monthly_enabled", False)}


def set_budget(patch):
    from agent_friday.core import _load_settings_raw, _save_settings
    cfg = dict((_load_settings_raw().get("cost_budget") or {}))
    for k in BUDGET_KEYS:
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
    # The hard stop announces itself at the crossing, not at the next call.
    try:
        from agent_friday.services import spend_guard as _sg
        _sg.notify_if_tripped()
    except Exception:
        pass
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
