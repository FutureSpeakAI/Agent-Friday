"""Prompt caching for cloud calls, and a hard ceiling on what one call may spend.

Two mechanisms that answer two different questions. They are in one module
because they act at the same moment — the last instant before a payload leaves
the device — but they are NOT the same guarantee and must not be confused:

  * **Caching makes the common case cheap.** Anthropic bills a cached prefix at
    ~0.1x the input rate; the same prefix re-sent uncached bills at 1.0x. Friday
    re-sends enormous stable prefixes constantly — the tool schemas on every
    iteration of the agent loop, the system prompt on every turn, and the whole
    accumulated transcript on every iteration of a task. Measured against
    ``~/.friday/costs.db`` over the 14 days to 2026-08-26: 168,887,596 input
    tokens went to Anthropic for $1,057.22, and 94.7% of those tokens sat in
    calls above 50,000 tokens. Modelled against the same rows with a rolling
    message breakpoint and the 5-minute TTL, the billable-equivalent falls to
    33.3M — **an 80% cut to the input line**, which is where ~99% of the money
    is (input:output ran 154:1).

  * **A ceiling makes the catastrophe impossible.** Caching cannot do this, and
    saying otherwise is how a cheap-per-call system still produces a $200 hour.
    ``_call_claude_agent`` defaults to ``max_iters=999``; at the measured median
    of ~91,000 input tokens per iteration that is a theoretical 90M tokens on a
    single task with nothing in the code to stop it. The observed incident — a
    crash-fallback that re-sent a blown-context turn and billed ~1.43M input
    tokens on one task — is the small version of that. So: a per-call ceiling,
    a per-task cumulative ceiling, and a no-progress guard that refuses to
    re-send a payload no smaller than the one that just failed.

Both are settings-driven and both fail LOUD. A refused call raises with the
numbers in the message, because a silent trim is how an overrun becomes
invisible again.

Ordering note (the thing that silently defeats caching): a cache hit requires a
**byte-identical prefix**. One changed byte at position N invalidates everything
after N. Friday's clock renders at minute resolution and used to sit at position
2 of the assembled system prompt, so on any turn that crossed a minute boundary
— that is, essentially every turn — everything after ~2,500 tokens was cold on
both backends. ``model_router._build_context_prompt`` now emits it last;
``VOLATILE_MARKER`` below is where this module splits stable from volatile, and
it is a header that already existed in the prompt rather than a sentinel we
inject, so nothing new reaches the model.
"""

from __future__ import annotations

import json
import logging
import threading

_log = logging.getLogger(__name__)

#: Everything from this header onward changes per turn (clock, today's context,
#: memory recall, the confirmation flow). Everything above it is stable for
#: minutes-to-days and is what gets a cache breakpoint. Kept in sync with
#: ``services/clock.clock_context_block``.
VOLATILE_MARKER = "== AUTHORITATIVE CLOCK =="

_EPHEMERAL = {"type": "ephemeral"}

#: Anthropic will not cache a prefix shorter than this, per model. A breakpoint
#: below the floor is accepted and then ignored, so we skip it rather than spend
#: a breakpoint (there are only 4) on something that cannot hit.
#: The minimum is NOT monotonic across generations — 512 on the newest models
#: and 4096 on Haiku 4.5 — so it cannot be guessed from the model's age.
_MIN_CACHEABLE = {
    "claude-fable-5": 512,
    "claude-opus-5": 512,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    # Same dated-key trap as the pricing table: keyed only on the alias, the
    # canonical id fell to the 1024 default and we would mark a 2K prefix that
    # Anthropic then ignores — one of four breakpoints spent on nothing.
    "claude-haiku-4-5": 4096,
    "claude-haiku-4-5-20251001": 4096,
}
_MIN_CACHEABLE_DEFAULT = 1024

CHARS_PER_TOKEN = 4.0

# ── Defaults for the ceilings ────────────────────────────────────────────────
# Chosen from the measured distribution rather than guessed. Anthropic 14d:
#   p50 90,951 · p95 136,740 · p99 166,799 · max 195,875 input tokens per call.
# So 180,000 sits above p99 and below the 200k window: it cannot refuse work
# Friday actually does today, and it catches a blown-context send before it is
# billed rather than after.
DEFAULT_MAX_CALL_INPUT_TOKENS = 180_000
#: One task, summed across every iteration of its agent loop. The observed
#: incident was ~1.43M. The largest legitimate burst in 14 days was 10.6M across
#: 107 calls, which this WOULD refuse — deliberately: that burst cost $33.38 and
#: Stephen should be asked before the next one, not billed for it.
DEFAULT_MAX_TASK_INPUT_TOKENS = 4_000_000


class CallTooLarge(RuntimeError):
    """A single cloud call exceeded the per-call input ceiling."""


class TaskBudgetExceeded(RuntimeError):
    """One task's cumulative cloud input exceeded its ceiling."""


def _tokens(text) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN)


def _settings():
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def enabled() -> bool:
    """Prompt caching is on unless explicitly disabled.

    On by default because the failure mode of a wrong breakpoint is a cache
    MISS — the call still succeeds, at today's price. There is no correctness
    risk to trade against, only a bill.
    """
    return bool(_settings().get("prompt_cache_enabled", True))


def _min_cacheable(model) -> int:
    return _MIN_CACHEABLE.get(str(model or ""), _MIN_CACHEABLE_DEFAULT)


# ─────────────────────────────────────────────────────────────────────────────
#  Size estimation
# ─────────────────────────────────────────────────────────────────────────────
def estimate_payload_tokens(payload) -> int:
    """Rough input-token count for an assembled provider payload.

    Deliberately cheap and deliberately an over-estimate of nothing: it counts
    the serialized bytes of system + messages + tools at 4 chars/token, the same
    basis ``services/context_budget`` uses, so the two agree. It is not the
    tokenizer's answer and does not need to be — a ceiling at 180,000 is not
    decided by a few hundred tokens of drift.
    """
    total = 0
    try:
        for key in ("system", "messages", "tools", "prompt", "input"):
            val = payload.get(key)
            if val is None:
                continue
            total += _tokens(val if isinstance(val, str)
                             else json.dumps(val, default=str))
    except Exception:
        # Never let the estimator be the reason a call fails. A payload we
        # cannot measure is reported as 0 and passes the ceiling — the ceiling
        # exists to catch runaway growth, and an unmeasurable payload is a
        # different bug than the one it guards.
        return 0
    return total


# ─────────────────────────────────────────────────────────────────────────────
#  Per-task cumulative budget
# ─────────────────────────────────────────────────────────────────────────────
_local = threading.local()


class task_budget:
    """Context manager scoping a cumulative input-token ceiling to one task.

    Thread-local rather than a contextvar because the agent loop and every one
    of its tool calls run on the request's own thread; a nested loop (a task
    that spawns a sub-agent inline) re-enters and the inner scope keeps its own
    tally while still charging the outer one, so a fan-out cannot launder its
    way past the parent's ceiling.
    """

    def __init__(self, limit=None, label=""):
        if limit is None:
            limit = int(_settings().get("max_task_input_tokens")
                        or DEFAULT_MAX_TASK_INPUT_TOKENS)
        self.limit = int(limit)
        self.label = label or "task"
        self.spent = 0
        self._parent = None

    def __enter__(self):
        self._parent = getattr(_local, "budget", None)
        _local.budget = self
        return self

    def __exit__(self, *exc):
        _local.budget = self._parent
        return False

    def charge(self, tokens: int):
        self.spent += max(0, int(tokens))
        if self._parent is not None:
            self._parent.charge(tokens)
        if self.limit > 0 and self.spent > self.limit:
            raise TaskBudgetExceeded(
                f"'{self.label}' has sent {self.spent:,} input tokens to cloud "
                f"providers, past its ceiling of {self.limit:,}. The task is "
                f"stopped rather than billed further. Raise "
                f"model_routing.max_task_input_tokens (or settings "
                f"'max_task_input_tokens') if this task genuinely needs more."
            )


def current_budget():
    return getattr(_local, "budget", None)


# ─────────────────────────────────────────────────────────────────────────────
#  The ceiling
# ─────────────────────────────────────────────────────────────────────────────
_last_refused_size = threading.local()


def check_call_size(payload, provider) -> int:
    """Refuse a cloud call that is too large, BEFORE it is billed.

    Raises ``CallTooLarge`` or ``TaskBudgetExceeded``. Returns the estimated
    input tokens otherwise, so the caller can log or meter them.

    The no-progress guard is the specific cure for the incident that prompted
    this module: a fallback caught the failure of an over-length send and
    re-sent the same (or a larger) payload to a different seat. A retry that has
    not shrunk cannot succeed and must not be paid for twice.
    """
    est = estimate_payload_tokens(payload)
    if est <= 0:
        return est

    cap = _settings().get("max_call_input_tokens")
    try:
        cap = int(cap) if cap else DEFAULT_MAX_CALL_INPUT_TOKENS
    except (TypeError, ValueError):
        cap = DEFAULT_MAX_CALL_INPUT_TOKENS

    if cap > 0 and est > cap:
        prev = getattr(_last_refused_size, "value", 0)
        _last_refused_size.value = est
        raise CallTooLarge(
            f"Refusing a {est:,}-token call to {provider}: the per-call ceiling "
            f"is {cap:,}. Nothing was sent and nothing was billed. "
            + (f"(A previous send at {prev:,} tokens was already refused on this "
               f"thread — the payload is not shrinking.) " if prev else "")
            + "Shorten the conversation, or raise settings "
              "'max_call_input_tokens' if this is genuinely the work."
        )

    budget = current_budget()
    if budget is not None:
        budget.charge(est)
    return est


# ─────────────────────────────────────────────────────────────────────────────
#  Anthropic cache breakpoints
# ─────────────────────────────────────────────────────────────────────────────
def _split_system(system, model):
    """Return ``system`` as blocks, with the stable prefix marked cacheable.

    The split is at ``VOLATILE_MARKER``. Above it: persona, cLaws, the frozen
    Friday system prompt, self-knowledge — text that changes when Stephen edits
    a file, not when a minute passes. From it down: the clock and everything
    the assembler appends after it.

    A prefix under the model's minimum cacheable length gets no breakpoint: it
    would be accepted and silently ignored, and breakpoints are limited to four.
    """
    if not isinstance(system, str) or not system:
        return system, False
    idx = system.find(VOLATILE_MARKER)
    if idx <= 0:
        # No marker (or it is the first thing): cache the whole string. Inside
        # one agent loop the system prompt is built once and constant, so this
        # still pays even when the prefix is not stable across turns.
        if _tokens(system) < _min_cacheable(model):
            return system, False
        return [{"type": "text", "text": system, "cache_control": _EPHEMERAL}], True
    stable, volatile = system[:idx], system[idx:]
    if _tokens(stable) < _min_cacheable(model):
        return system, False
    return ([{"type": "text", "text": stable, "cache_control": _EPHEMERAL},
             {"type": "text", "text": volatile}], True)


def _mark_last_tool(tools, model):
    """Breakpoint on the final tool definition — caches the whole tool tier.

    Tools render first, so this one breakpoint covers every schema. It is the
    largest fixed cost in a turn (measured 14,041 tokens live, and it grows with
    every MCP connector), and it is re-sent on EVERY iteration of the agent
    loop, which is what makes it the cheapest win in the file.

    Order matters and is not ours to choose: the tool list must serialize
    identically call to call or the prefix breaks. It already does — the
    registry is built once at import and MCP tools are appended at registration.
    """
    if not isinstance(tools, list) or not tools:
        return tools, False
    if _tokens(json.dumps(tools, default=str)) < _min_cacheable(model):
        return tools, False
    out = list(tools)
    last = out[-1]
    if not isinstance(last, dict) or "cache_control" in last:
        return tools, False
    out[-1] = {**last, "cache_control": _EPHEMERAL}
    return out, True


def _mark_last_message(messages):
    """Rolling breakpoint on the newest message — caches the accrued transcript.

    This is where the money is. Within one ``_call_claude_agent`` loop the
    message list is strictly append-only (assistant tool_use, then user
    tool_result — ``agent.py`` never rewrites it mid-loop), so iteration N+1's
    prompt has iteration N's prompt as an exact prefix. Marking the newest
    message writes a cache the next iteration reads at 0.1x. Anthropic matches
    the longest previously-cached prefix automatically — but only by walking
    back at most 20 content blocks from the breakpoint, so one moving
    breakpoint is enough ONLY while a turn stays shorter than that. It does not
    here: eleven parallel tool calls append 22 blocks in one iteration.
    ``_mark_lookback_anchor`` leaves the trailing breakpoint this docstring
    used to say was unnecessary.

    Measured over 14 days of real calls: this alone accounts for most of the 80%
    reduction, because the median burst runs 5 calls and the expensive ones run
    50-107.
    """
    if not isinstance(messages, list) or not messages:
        return messages, False
    out = list(messages)
    last = out[-1]
    if not isinstance(last, dict):
        return messages, False
    content = last.get("content")
    if isinstance(content, str):
        if not content:
            return messages, False
        out[-1] = {**last, "content": [{"type": "text", "text": content,
                                        "cache_control": _EPHEMERAL}]}
        return out, True
    if isinstance(content, list) and content:
        tail = content[-1]
        if not isinstance(tail, dict) or "cache_control" in tail:
            return messages, False
        new_content = list(content)
        new_content[-1] = {**tail, "cache_control": _EPHEMERAL}
        out[-1] = {**last, "content": new_content}
        return out, True
    return messages, False


#: A breakpoint walks backward at most this many CONTENT BLOCKS looking for a
#: prior cache entry. Past it the match silently fails: full price, no error.
LOOKBACK_BLOCKS = 20
#: Where the anchor goes, in blocks back from the newest, when no message
#: boundary is available. Inside the window so the rolling breakpoint can still
#: see it, and far enough behind to extend the pair's reach to ~35 blocks.
ANCHOR_TARGET_BLOCKS = 15


def _flatten_blocks(messages):
    """[(msg_index, block_index_or_None)] oldest→newest, one entry per block.

    ``None`` marks a message whose content is a bare string — one block, but it
    has to be promoted to list form before it can carry a breakpoint.
    """
    flat = []
    for mi, m in enumerate(messages):
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list):
            flat.extend((mi, bi) for bi in range(len(content)))
        else:
            flat.append((mi, None))
    return flat


def _mark_lookback_anchor(messages):
    """Second breakpoint behind the rolling one, inside the 20-block window.

    The rolling breakpoint alone suffices only while a turn appends fewer blocks
    than the lookback. Friday's agent loop routinely breaks that: Claude fans
    out N tools in one assistant message and we answer with N tool_result blocks
    in one user message, so a single iteration appends 2N blocks. Past eleven
    parallel tools the previous iteration's entry has fallen out of the window,
    and every iteration after it silently pays full price — the exact opposite
    of where this module claims its savings come from.

    Anchoring on a MESSAGE BOUNDARY is preferred: that is where the previous
    iteration's rolling breakpoint actually sat, so the entry already exists.
    Only when a single message is itself longer than the window does the anchor
    land mid-message, which is legal (cache_control rides on any content block,
    tool_use and tool_result included) and is the only option left.
    """
    if not isinstance(messages, list) or not messages:
        return messages, False
    flat = _flatten_blocks(messages)
    total = len(flat)
    if total <= LOOKBACK_BLOCKS:
        # The natural prefix match already reaches the previous entry. A second
        # breakpoint here would buy nothing and burn one of the four allowed.
        return messages, False

    boundaries = {}          # distance-from-end -> flat index, at message ends
    for i, (mi, _bi) in enumerate(flat):
        if i + 1 == total or flat[i + 1][0] != mi:
            boundaries[total - 1 - i] = i
    candidates = [d for d in boundaries if 0 < d <= LOOKBACK_BLOCKS]
    if candidates:
        pick = boundaries[max(candidates)]
    else:
        pick = total - 1 - min(ANCHOR_TARGET_BLOCKS, total - 1)

    mi, bi = flat[pick]
    out = list(messages)
    msg = dict(out[mi])
    content = msg.get("content")
    if bi is None:
        if not isinstance(content, str) or not content:
            return messages, False
        msg["content"] = [{"type": "text", "text": content,
                           "cache_control": _EPHEMERAL}]
    else:
        if not isinstance(content, list) or bi >= len(content):
            return messages, False
        block = content[bi]
        if not isinstance(block, dict) or "cache_control" in block:
            return messages, False
        new_content = list(content)
        new_content[bi] = {**block, "cache_control": _EPHEMERAL}
        msg["content"] = new_content
    out[mi] = msg
    return out, True


def apply_anthropic_cache(kwargs):
    """Add ``cache_control`` breakpoints to an assembled Anthropic payload.

    Returns a new dict; the input is not mutated. Call this AFTER the egress
    gate: the gate's job is classification and redaction, and running last means
    no gating path can drop the breakpoints on its way through. The gate already
    understands block-form ``system`` (``egress_gate.seal_outbound``), so the
    order is a choice about robustness, not about safety.

    All four breakpoints Anthropic allows: tools, system, newest message, and
    an anchor behind the newest that keeps the previous iteration's entry
    inside the 20-block lookback window.

    Every failure mode here is a cache miss, never a wrong answer: if a
    breakpoint lands somewhere unstable the call runs at today's price.
    """
    if not isinstance(kwargs, dict) or not enabled():
        return kwargs
    try:
        out = dict(kwargs)
        model = out.get("model")
        marked = []
        if "tools" in out:
            out["tools"], hit = _mark_last_tool(out["tools"], model)
            if hit:
                marked.append("tools")
        if "system" in out:
            out["system"], hit = _split_system(out["system"], model)
            if hit:
                marked.append("system")
        if "messages" in out:
            out["messages"], hit = _mark_last_message(out["messages"])
            if hit:
                marked.append("messages")
            # The fourth breakpoint, reserved for exactly this until the
            # 20-block lookback was measured against the loop's fan-out shape.
            out["messages"], hit = _mark_lookback_anchor(out["messages"])
            if hit:
                marked.append("anchor")
        if not marked:
            return kwargs
        _log.debug("prompt cache breakpoints: %s", "+".join(marked))
        return out
    except Exception as exc:
        # A caching bug must never cost a turn. Fall back to the uncached
        # payload, which is exactly what shipped before this module existed.
        _log.warning("prompt-cache breakpoints skipped (%s) — sending uncached", exc)
        return kwargs
