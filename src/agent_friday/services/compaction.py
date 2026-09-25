"""Transcript auto-compaction (Part C of Self-Sufficient Friday).

Long voice/chat sessions and long-running ``agent_prompt`` tasks can grow a
message list past the model's context window. This module compacts the *live
context window* — never the durable record — by summarizing the middle of the
transcript while preserving the head (system/task framing + opening exchange)
and the tail (recent turns) verbatim.

  head (keep_head)            ← never compacted
  middle                      → one synthetic "[Context Summary] …" message
  tail (keep_tail)            ← preserved verbatim

Triggering is by **estimated token count** of the assembled transcript (turn
count is a poor proxy — one big tool dump can blow the budget), firing at
``trigger_ratio`` × the context window the seat is actually served at, less
the room reserved for the reply.

The agent loops call this before the first round AND between tool rounds, so
a run of hundreds of rounds stays inside the window instead of only being
checked once at the start.

WHO WRITES THE SUMMARY IS A PRIVACY DECISION. The middle of a transcript holds
whatever its seat was allowed to see: vault reads, local-only conversation,
tool results that never left the machine. Summarising it on a different model
sends it wherever that model lives. So:

  * the loops pass their own summarizer — the OpenAI-format loop asks the
    SAME seat through its own transport (`seat_summarizer`), the Anthropic
    loop asks Claude, where that transcript is already going;
  * the default summarizer, for a caller that does not say, runs inside
    `local_only_guard.local_only`, so it can only ever reach a local model
    and otherwise degrades to no compaction.

Lossless where it matters: this operates on a *copy* assembled for the model
call. ``CHAT_HISTORY`` / ``chat_history.json`` keep the full transcript, and
every turn is independently embedded in ChromaDB, so the original turns stay
semantically retrievable even after the middle is summarized in-context.
"""

import json
import threading
import time

import agent_friday.core as core
from agent_friday.core import ANTHROPIC_MODEL_DEFAULT, _load_settings

_CHARS_PER_TOKEN = 4
_SUMMARY_PREFIX = "[Context Summary]"
_LEDGER_MARK = "[Task Ledger]"
# The summary is written in the task ledger's sections so it can be absorbed
# into the ledger (services/task_ledger.py).
from agent_friday.services.task_ledger import SUMMARY_SECTIONS as _SECTIONS  # noqa: E402
_TRIM_MARKER = "... [{n} characters of this tool result were trimmed to fit the context window]"

# Cumulative, per process — surfaced by GET /api/context/compression-stats.
_STATS_LOCK = threading.Lock()
STATS = {
    "compactions": 0,        # summaries inserted
    "tokens_before": 0,      # estimated tokens of transcripts that were compacted
    "tokens_after": 0,       # estimated tokens after compaction
    "tokens_saved": 0,
    "trimmed_results": 0,    # oversized tool results cut down in the tail
    "skipped_no_summary": 0, # over budget, but no permitted summarizer answered
    "by_seat": {},           # "local" | "cloud" -> compactions
    "last": None,            # {"at", "model", "window", "before", "after", "seat"}
}


def _record(**kw):
    with _STATS_LOCK:
        if kw.get("compacted"):
            STATS["compactions"] += 1
            STATS["tokens_before"] += kw["before"]
            STATS["tokens_after"] += kw["after"]
            STATS["tokens_saved"] += max(0, kw["before"] - kw["after"])
            seat = kw.get("seat") or "unknown"
            STATS["by_seat"][seat] = STATS["by_seat"].get(seat, 0) + 1
            STATS["last"] = {"at": time.time(), "model": kw.get("model"),
                             "window": kw.get("window"), "before": kw["before"],
                             "after": kw["after"], "seat": seat}
        STATS["trimmed_results"] += kw.get("trimmed", 0)
        STATS["skipped_no_summary"] += kw.get("skipped", 0)


def get_stats():
    with _STATS_LOCK:
        s = json.loads(json.dumps(STATS))
    tb = s["tokens_before"]
    s["compression_ratio"] = (s["tokens_saved"] / tb) if tb else 0.0
    return s


def _content_text(msg):
    """Flatten a message's content to text for estimation/summarization.

    Both wire formats: Anthropic content blocks (text / tool_use /
    tool_result) and OpenAI-format assistant `tool_calls`."""
    c = msg.get("content")
    parts = []
    if isinstance(c, str):
        parts.append(c)
    elif isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif b.get("type") == "tool_result":
                    parts.append(str(b.get("content", ""))[:2000])
                elif b.get("type") == "tool_use":
                    parts.append(f"[tool_use {b.get('name', '')} "
                                 f"{json.dumps(b.get('input'), default=str)[:300]}]")
            else:
                parts.append(str(b))
    elif c is not None:
        parts.append(str(c))
    for tc in msg.get("tool_calls") or []:
        fn = (tc or {}).get("function") or {}
        parts.append(f"[tool_call {fn.get('name', '')} {str(fn.get('arguments', ''))[:300]}]")
    return "\n".join(parts)


def estimate_tokens(messages):
    """Cheap char-based token estimate for the assembled message list."""
    total = 0
    for m in messages or []:
        total += len(_content_text(m))
    return total // _CHARS_PER_TOKEN


def _cfg():
    try:
        return dict((_load_settings().get("compaction") or {}))
    except Exception:
        return {}


# ── what the model really counts ─────────────────────────────────────────────
# 4 chars/token is right for English prose and badly wrong for tool output:
# measured on JSON tool results, a BPE tokenizer counts ~1.55x the estimate,
# so a transcript "at 67% of budget" was already past a local seat's window.
# Every provider reports the prompt tokens it actually counted; the loops
# feed that back here and the estimate is scaled per model.
_CAL_LOCK = threading.Lock()
_CALIBRATION = {}            # model -> tokens-per-estimated-token (EMA)
_CAL_MIN, _CAL_MAX = 0.5, 4.0


def calibration(model):
    with _CAL_LOCK:
        return _CALIBRATION.get(model or "", 1.0)


def observe(model, estimated, actual):
    """Record that a request estimated at `estimated` tokens was counted as
    `actual` by the model. Leans toward the larger ratio, because
    under-counting overflows the seat and over-counting only compacts early."""
    try:
        estimated, actual = int(estimated or 0), int(actual or 0)
    except (TypeError, ValueError):
        return
    if estimated < 200 or actual <= 0:
        return
    ratio = max(_CAL_MIN, min(_CAL_MAX, actual / estimated))
    with _CAL_LOCK:
        old = _CALIBRATION.get(model or "")
        _CALIBRATION[model or ""] = ratio if old is None else (
            max(ratio, old * 0.7 + ratio * 0.3))


def is_context_overflow(err):
    """A provider's "this prompt is longer than the context" refusal."""
    t = str(err).lower()
    return any(k in t for k in ("exceed_context_size", "exceeds the available context",
                                "context length", "context_length_exceeded",
                                "prompt is too long", "maximum context", "too many tokens"))


def observe_overflow(model, estimated, err):
    """Calibrate from an overflow refusal: use the counts when the provider
    states them ("8519 > 8192"), else assume we under-counted by half."""
    import re
    m = re.search(r"(\d{3,})\s*>\s*(\d{3,})", str(err))
    if m:
        observe(model, estimated, int(m.group(1)))
    else:
        observe(model, estimated, int((estimated or 0) * calibration(model) * 1.5))


def schema_tokens(obj):
    """Estimated tokens of something sent alongside the transcript (tool
    schemas, a system prompt) -- same 4-chars basis as `estimate_tokens`."""
    if not obj:
        return 0
    try:
        return len(obj if isinstance(obj, str) else json.dumps(obj, default=str)) // _CHARS_PER_TOKEN
    except Exception:
        return 0


def ledger_view_chars(model=None, window=None):
    """How much of a seat's window the pinned task ledger may use: a quarter,
    in characters, at this model's calibrated token rate. The ledger rides in
    a message compaction cannot shrink, so it must fit with room to work."""
    window = window or resolve_context_window(model)
    return max(2000, int(window * 0.25 * _CHARS_PER_TOKEN / max(1.0, calibration(model))))


def effective_tokens(messages, model=None):
    return int(estimate_tokens(messages) * calibration(model))


# ── the window a seat is really served at ────────────────────────────────────

_SERVED_CACHE = {}          # base url -> (at, n_ctx or None)
_SERVED_TTL_S = 60.0


def served_context(model):
    """The per-request context a local llama-server seat is serving `model`
    at, read from its own `/props`. None when the model has no verified local
    endpoint or the server does not say.

    This is the number that decides whether a request fits: a seat started
    with `-c 49152 -np 2` serves 24,576 tokens per request, whatever the
    model card or the plan says the model supports.
    """
    if not model:
        return None
    try:
        from agent_friday.services.local_call import seat_endpoint
        base = seat_endpoint(model)
    except Exception:
        base = None
    if not base:
        return None
    now = time.time()
    hit = _SERVED_CACHE.get(base)
    if hit and now - hit[0] < _SERVED_TTL_S:
        return hit[1]
    n_ctx = None
    try:
        import urllib.request
        root = base[:-3] if base.rstrip("/").endswith("/v1") else base
        with urllib.request.urlopen(root.rstrip("/") + "/props", timeout=3) as r:
            props = json.loads(r.read().decode("utf-8"))
        n_ctx = int(((props or {}).get("default_generation_settings") or {}).get("n_ctx") or 0) or None
    except Exception:
        n_ctx = None
    _SERVED_CACHE[base] = (now, n_ctx)
    return n_ctx


def _declared_serving_window(model):
    """The window the arbiter would load a llama-server seat at: the model
    store's measured `serve_num_ctx`, under the arbiter's class ceiling.
    None for a model with no declaration (cloud models, Ollama tags)."""
    try:
        from agent_friday.services.residency_arbiter import LlamaServerBackend as _L
        if _L._declared_num_ctx(model):
            return int(_L.seat_cap(model))
    except Exception:
        pass
    return None


def resolve_context_window(model=None, cfg=None):
    """The context window to budget against, in tokens (decision D3).

    Precedence:
      0. what a local llama-server seat reports it is SERVING (`/props`),
      0b. when it is not serving: the window the arbiter would load it at
          (models.json `serve_num_ctx`, under the arbiter's ceiling),
      1. the residency plan's context for the seat,
      2. the model's REAL window from the catalog,
      3. the configured `compaction.context_window`,
      4. the 200_000 default.

    `model` was already threaded into should_compact/maybe_compact but was only
    ever passed to the summarizer — the window itself was a flat constant, so a
    4K local model and Claude Opus were budgeted identically. That is the bug
    this function exists to close.
    """
    cfg = cfg if cfg is not None else _cfg()
    if model:
        served = served_context(model)
        if served:
            return int(served)
        declared = _declared_serving_window(model)
        if declared:
            # Not serving right now, but the arbiter will load it at no more
            # than this -- the plan's rung and the descriptor's
            # context_window can both say more.
            return int(declared)
        try:
            # The residency PLAN next. It knows the context a seat is being
            # served at — `num_ctx` is chosen per-seat and applied on every
            # dispatch. model_catalog asks the Ollama daemon, which returns
            # None when the daemon is stopped, and compaction then budgeted
            # gemma4:12b against a 200,000-token default while the seat was
            # really serving 131,072. Budgeting more window than exists means
            # compacting too LATE, which overflows the seat.
            from agent_friday.services.residency_policy import num_ctx_for_model
            planned = num_ctx_for_model(model, default=0)
            if planned:
                return int(planned)
        except Exception:
            pass
        try:
            from agent_friday.services.model_catalog import context_window_for
            real = context_window_for(model)
            if real and real > 0:
                return int(real)
        except Exception:
            pass
    return int(cfg.get("context_window", 200000))


def _budget(window, cfg, reserve_tokens=0):
    """Tokens the transcript may use before compaction fires: the trigger
    ratio of the window, and never more than the window less the reply."""
    ratio = float(cfg.get("trigger_ratio", 0.70))
    budget = int(window * ratio)
    if reserve_tokens and reserve_tokens > 0:
        budget = min(budget, int(window - reserve_tokens) - 256)
    return max(512, budget)


def should_compact(messages, model=None, cfg=None, *, window=None, reserve_tokens=0):
    cfg = cfg if cfg is not None else _cfg()
    if not cfg or cfg.get("enabled") is False:
        return False
    keep_head = int(cfg.get("keep_head", 3))
    keep_tail = int(cfg.get("keep_tail", 10))
    # Need at least one message in the middle to be worth compacting.
    if len(messages or []) <= keep_head + keep_tail + 1:
        return False
    window = window or resolve_context_window(model, cfg)
    return effective_tokens(messages, model) > _budget(window, cfg, reserve_tokens)


def _merge_adjacent(messages):
    """Merge consecutive same-role messages that both carry string content, so
    inserting the summary can't create an illegal two-in-a-row-role sequence for
    providers that require strict alternation."""
    out = []
    for m in messages:
        if (out and out[-1].get("role") == m.get("role")
                and isinstance(out[-1].get("content"), str)
                and isinstance(m.get("content"), str)
                and not out[-1].get("tool_calls") and not m.get("tool_calls")):
            out[-1] = {"role": m["role"],
                       "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append(dict(m))
    return out


# ── tool-call pairs must not be split ────────────────────────────────────────

def _is_tool_reply(msg):
    """A message that answers a tool call made in the message before it."""
    if msg.get("role") == "tool":
        return True
    c = msg.get("content")
    return (msg.get("role") == "user" and isinstance(c, list)
            and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c))


def _calls_tools(msg):
    if msg.get("role") != "assistant":
        return False
    if msg.get("tool_calls"):
        return True
    c = msg.get("content")
    return isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in c)


def _safe_cuts(messages, keep_head, keep_tail):
    """(head_end, tail_start) moved so neither cut separates a tool call from
    its results. A provider rejects a tool result whose call is gone, and a
    call whose results are gone."""
    n = len(messages)
    head_end = min(keep_head, n)
    # The head may not end on a call whose results would fall in the middle.
    while head_end > 0 and _calls_tools(messages[head_end - 1]):
        head_end -= 1
    tail_start = max(head_end, n - keep_tail) if keep_tail else n
    # The tail may not open on a result whose call would fall in the middle.
    while tail_start < n and _is_tool_reply(messages[tail_start]):
        tail_start += 1
    return head_end, tail_start


# ── summarizers ──────────────────────────────────────────────────────────────

_SUMMARY_INSTRUCTION = (
    "Summarize the following conversation excerpt into a compact factual "
    "note that preserves decisions made, open questions, key entities, exact "
    "figures and identifiers, what each tool call found, and any state the "
    "assistant must remember to continue. Be terse; no preamble. Limit to "
    "about {n} tokens.\n" + _SECTIONS + "\n=== EXCERPT ===\n{text}\n=== END ===")


def _default_summarizer(text, max_tokens=400):
    """Summarize on a LOCAL model, for a caller that did not say where its
    transcript may go.

    Runs inside `local_only_guard.local_only`, so every cloud transport
    refuses; when no local model answers, compaction degrades to a no-op.
    The call is metered and tagged kind="compaction". Returns '' on failure.
    """
    try:
        from agent_friday.services.model_router import _generate_text
        from agent_friday.services import local_only_guard as _log
        prompt = _SUMMARY_INSTRUCTION.format(n=max_tokens, text=text)
        try:
            from agent_friday.services import cost_meter as _cm
            _cm.push_attribution(kind="compaction")
        except Exception:
            _cm = None
        try:
            with _log.local_only("context compaction"):
                out = _generate_text([{"role": "user", "content": prompt}],
                                     max_tokens=max_tokens,
                                     orb_label="🗜 Compacting context",
                                     workspace="system")
        finally:
            try:
                if _cm:
                    _cm.pop_attribution()
            except Exception:
                pass
        from agent_friday.services.model_router import is_refusal
        if is_refusal(out):
            # The router answered instead of a model (no local model may take
            # this, demo mode). That is no summary -- taking it as one replaced
            # whole conversations with the refusal.
            print("  [compaction] no local model wrote a summary: %s" % str(out)[:120])
            return ""
        return (out or "").strip()
    except Exception as e:
        print(f"  [compaction] summarizer failed: {e}")
        return ""


def seat_summarizer(send_fn):
    """A summarizer that asks the SAME seat, through the loop's own transport.

    `send_fn(convo, tools)` is the OpenAI-format loop's round sender; it
    already knows the seat's endpoint, credentials and served model, so the
    summary is written wherever the transcript already lives. Streamed text
    is kept out of the user's reply.
    """
    def _summarize(text, max_tokens=400):
        try:
            from agent_friday.services import model_router as _mr
            token = _mr.DELTA_SINK.set(None)
            try:
                resp = send_fn([
                    {"role": "system", "content": "You compress transcripts for an assistant that must keep working. Output only the note."},
                    {"role": "user", "content": _SUMMARY_INSTRUCTION.format(n=max_tokens, text=text)},
                ], None)
            finally:
                _mr.DELTA_SINK.reset(token)
            choices = (resp or {}).get("choices") or []
            msg = (choices[0].get("message") if choices else {}) or {}
            out = msg.get("content")
            return out.strip() if isinstance(out, str) else ""
        except Exception as e:
            print(f"  [compaction] seat summarizer failed: {e}")
            return ""
    return _summarize


def claude_summarizer(client, model, *, session_ctx=None, on_cost=None):
    """A summarizer for the Anthropic loop: the transcript is already bound
    for Anthropic, so the summary is written there too, with no tools.

    The request is a cloud call like any round: it goes through
    `model_router._seal_or_block` (spending cap, size ceiling, egress seal)
    and is metered. A send the chokepoint refuses is no summary -- compaction
    then falls back to trimming, and nothing is sent. `on_cost(usd)` lets the
    loop charge the call to its task budget.
    """
    def _summarize(text, max_tokens=400):
        try:
            from agent_friday.services import model_router as _mr
            kwargs = _mr._seal_or_block({
                "model": model, "max_tokens": max(1024, int(max_tokens) * 4),
                "messages": [{"role": "user", "content": _SUMMARY_INSTRUCTION.format(
                    n=max_tokens, text=text)}]}, "anthropic")
        except Exception as e:
            print(f"  [compaction] summary not sent (egress chokepoint): {e}")
            return ""
        try:
            t0 = time.time()
            resp = client.messages.create(**kwargs)
        except Exception as e:
            print(f"  [compaction] claude summarizer failed: {e}")
            return ""
        try:
            from agent_friday.services import cost_meter as _cm
            usd = _cm.meter("anthropic", kwargs.get("model") or model,
                            getattr(resp, "usage", None),
                            duration_ms=int((time.time() - t0) * 1000),
                            session_ctx=session_ctx, kind="compaction")
            if on_cost is not None:
                on_cost(usd)
        except Exception as e:
            print(f"  [compaction] metering the summary failed: {e}")
        return "".join(getattr(b, "text", "") or "" for b in resp.content
                       if getattr(b, "type", None) == "text").strip()
    return _summarize


# ── trimming what summarising cannot reach ───────────────────────────────────

def _trim_tail_results(messages, budget, keep_chars=4000, factor=1.0):
    """Cut oversized tool results in the tail (newest untouched last) until
    the transcript fits. Summarising cannot shrink the tail, and one large
    tool result there is enough to overflow a local seat. Returns
    (messages, trimmed_count)."""
    out = [dict(m) for m in messages]
    trimmed = 0
    order = sorted(range(len(out)), key=lambda i: -len(_content_text(out[i])))
    for i in order:
        if estimate_tokens(out) * factor <= budget:
            break
        if i == len(out) - 1 and len(out) > 1 and not _is_tool_reply(out[i]):
            continue                          # the user's newest words stay whole
        m = out[i]
        c = m.get("content")
        if m.get("role") == "tool" and isinstance(c, str) and len(c) > keep_chars:
            cut = len(c) - keep_chars
            m["content"] = c[:keep_chars] + "\n" + _TRIM_MARKER.format(n=cut)
            trimmed += 1
        elif isinstance(c, list):
            changed = False
            new = []
            for b in c:
                if (isinstance(b, dict) and b.get("type") == "tool_result"
                        and isinstance(b.get("content"), str) and len(b["content"]) > keep_chars):
                    cut = len(b["content"]) - keep_chars
                    b = dict(b, content=b["content"][:keep_chars] + "\n" + _TRIM_MARKER.format(n=cut))
                    changed = True
                new.append(b)
            if changed:
                m["content"] = new
                trimmed += 1
    return out, trimmed


def _take_prior_summary(head):
    """Split an earlier compaction's summary out of the head.

    A summary is merged into the head's last user message (strict-alternation
    templates reject two user messages in a row), so without this every
    compaction would stack another summary into the head and a run of hundreds
    of rounds would grow its head without bound. The prior summary is folded
    into the next one instead."""
    prior = ""
    out = []
    for m in head:
        c = m.get("content")
        marks = [c.index(k) for k in (_SUMMARY_PREFIX, _LEDGER_MARK)
                 if isinstance(c, str) and k in c]
        if marks:
            # A continuation leg's first message carries the ledger too; it
            # is folded in like an earlier summary, not left as a stale copy.
            i = min(marks)
            prior = c[i:]
            rest = c[:i].rstrip()
            if not rest:
                continue
            m = dict(m, content=rest)
        out.append(m)
    return out, prior


def _rolling_summary(summarize, prior, transcript, max_tokens, window):
    """Summarize `transcript` in chunks that fit the summarizing seat, carrying
    the running summary forward, so nothing is dropped for being too long to
    read in one request. Returns '' if any chunk fails (never a partial
    summary that silently lost the rest)."""
    cap_chars = max(4000, (window - max_tokens * 3 - 1024) * _CHARS_PER_TOKEN)
    running = prior
    for start in range(0, len(transcript), cap_chars):
        chunk = transcript[start:start + cap_chars]
        text = (("PRIOR SUMMARY (fold everything in it into the new note):\n"
                 + running + "\n\n") if running else "") + chunk
        running = summarize(text, max_tokens)
        if not running:
            return ""
    return running


def compress_new_output(messages, start, model=None, seat=None):
    """Run Headroom over what a round just added (messages[start:]).

    Headroom rewrites bulky tool output (JSON, logs, tables) into a denser
    form in-process -- no model call, nothing leaves the machine -- keeping
    roles and tool-call ids. The agent loops call this between rounds, before
    `maybe_compact`, so summarising is needed later and less often. A no-op
    when compression is off or Headroom cannot compress."""
    try:
        cfg = (_load_settings() or {}).get("context_compression") or {}
        if cfg.get("enabled", True) is False:
            return messages
        from agent_friday.services.model_router import _get_context_compressor
        return _get_context_compressor(cfg).compress_new(
            messages, start, model=model or ANTHROPIC_MODEL_DEFAULT, seat=seat)
    except Exception as e:
        print(f"  [compaction] headroom skipped: {e}")
        return messages


def maybe_compact(messages, model=None, summarizer=None, *, window=None,
                  reserve_tokens=0, seat=None, force=False, ledger=None, task_id=None,
                  taint_key=None):
    """Return a (possibly) compacted copy of ``messages``.

    No-op (returns the original list) when compaction is disabled or the
    transcript is below budget. Over budget with nothing a permitted
    summarizer can write, it still trims oversized tool results rather than
    let the request overflow. Idempotent: a prior "[Context Summary]" message
    in the middle is folded into the new summary rather than re-summarized on
    top of itself.

    `window` overrides the resolved context window; `reserve_tokens` is the
    room the reply needs (a reasoning seat's thinking counts). `seat` labels
    the stats ("local" / "cloud"). `force` compacts to well under the budget
    even when the estimate says it fits -- the seat has just refused it.
    `ledger` (with `task_id`) is the task's working ledger: the summary is
    absorbed into it, it is saved, and the pinned block shows the ledger.
    """
    if not messages:
        return messages
    cfg = _cfg()
    if not cfg or cfg.get("enabled") is False:
        return messages
    window = window or resolve_context_window(model, cfg)
    budget = _budget(window, cfg, reserve_tokens)
    factor = calibration(model)
    before = int(estimate_tokens(messages) * factor)
    if force:
        budget = min(budget, int(before * 0.6))
    elif before <= budget:
        return messages

    keep_head = int(cfg.get("keep_head", 3))
    keep_tail = int(cfg.get("keep_tail", 10))
    max_tokens = int(cfg.get("summary_max_tokens", 400))

    # The verbatim tail may use at most a quarter of the budget. On a small
    # seat a fixed "last 10 messages" of tool output is larger than the window
    # itself, which leaves nothing for summarising to shrink; and a tail near
    # the budget means compacting again on the very next round.
    while keep_tail > 2:
        _h, _t = _safe_cuts(messages, keep_head, keep_tail)
        if estimate_tokens(messages[_t:]) * factor <= budget // 4:
            break
        keep_tail -= 1
    head_end, tail_start = _safe_cuts(messages, keep_head, keep_tail)
    head, prior = _take_prior_summary(messages[:head_end])
    if ledger is not None:
        # The ledger is the authoritative record of what the task has learned.
        # The summarizer always starts from ALL of it -- whatever the head
        # happens to hold -- because its FACTS replace the ledger's: a summary
        # written without them would erase every fact it was not shown.
        from agent_friday.services import task_ledger as _tl
        prior = _tl.render(ledger)
    middle = messages[head_end:tail_start]
    tail = messages[tail_start:]

    compacted = None
    if middle:
        transcript_lines = []
        for m in middle:
            role = m.get("role", "?")
            text = _content_text(m)
            if not text.strip():
                continue
            transcript_lines.append(f"{role.upper()}: {text}")
        transcript = "\n\n".join(transcript_lines)
        summary = ""
        if transcript.strip():
            summary = _rolling_summary(summarizer or _default_summarizer, prior,
                                       transcript, max_tokens, window)
        if summary:
            body = summary
            if ledger is not None:
                from agent_friday.services import task_ledger as _tl
                _tl.absorb_summary(ledger, summary)
                _tl.save(task_id, ledger)
                body = _tl.render(ledger, ledger_view_chars(model, window))
            # The summary (or the ledger it updated) is written from what was
            # read: register it as outside content, or a value an email planted
            # in it would come back as the model's own (services/taint.py).
            _key = taint_key or ("task:%s" % task_id if task_id else None)
            if _key:
                try:
                    from agent_friday.services import taint as _taint
                    if ledger is not None:
                        from agent_friday.services import task_ledger as _tl
                        _taint.note_carried(_key, "ledger", _tl.carried_text(ledger))
                    else:
                        _taint.note_carried(_key, "summary", summary)
                except Exception as e:
                    print(f"  [compaction] could not record the summary's provenance: {e}")
            summary_msg = {
                "role": "user",
                "content": f"{_SUMMARY_PREFIX} Earlier in this session ("
                           f"{len(middle)} messages condensed): {body}",
            }
            compacted = _merge_adjacent(list(head) + [summary_msg] + list(tail))
        else:
            _record(skipped=1)

    result = compacted if compacted is not None else list(messages)
    trimmed = 0
    for keep_chars in (4000, 2000, 1000, 500):
        if estimate_tokens(result) * factor <= budget:
            break
        result, n = _trim_tail_results(result, budget, keep_chars=keep_chars, factor=factor)
        trimmed += n
    if compacted is None and not trimmed:
        return messages          # nothing permitted could shrink it
    after = int(estimate_tokens(result) * factor)
    _record(compacted=compacted is not None, before=before, after=after,
            model=model, window=window, seat=seat, trimmed=trimmed)
    try:
        from agent_friday.services import reasoning_trace as _rt
        _rt.note("Context compacted for %s: ~%d → ~%d tokens (window %d)%s"
                 % (model or "model", before, after, window,
                    ", %d oversized tool result(s) trimmed" % trimmed if trimmed else ""))
    except Exception:
        pass
    return result
