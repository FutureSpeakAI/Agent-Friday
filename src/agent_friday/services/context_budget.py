"""
Agent Friday — how much of a context window is spent before the user types.

The residency policy has to choose a `num_ctx` for every seat (rule R7). Until
2026-08-15 it chose 32768, and the arithmetic behind that number was wrong in a
specific, checkable way: it was derived from the **tool registry alone**.

    52 tools, 34138 chars  ->  ~8534 tokens   x4 headroom  ->  32768

That ignored the larger of the two fixed costs. Friday's system prompt measured
46726 chars, ~11681 tokens — half again as much as the tools. The real fixed
overhead is ~20216 tokens, so a 32768 seat had ~12552 tokens (38%) left for the
actual conversation. Nearly two thirds of every window was gone before the user
said anything.

This module measures the overhead instead of assuming it, because both halves
grow: tools get added, and the system prompt is assembled at runtime from the
vault, self-knowledge and persona. A constant would be stale the first time
someone registers a tool. The measured figures are kept as a fallback for when
the live sources cannot be read (tests, a partially-imported process), never as
the primary source.

Token counting here is deliberately crude — chars/4 — and that is not laziness.
The consumer is a power-of-two ladder rung; a 15% error in the estimate does not
move which rung is chosen, while importing a real tokenizer would make a pure
planning path depend on a model being loaded.
"""
from __future__ import annotations

import json
import time

# Measured on the reference instance, 2026-08-15, against the live 52-tool
# registry and an assembled system prompt. Used only when the live sources
# cannot be read; a stale constant is a much smaller error than assuming zero.
MEASURED_TOOL_TOKENS = 8534
MEASURED_SYSTEM_PROMPT_TOKENS = 11681

# Injected memory and source context: recalled conversation, wiki spans, search
# results and anything else assembled INTO the turn before the user's words.
#
# This was missing entirely, and its absence is why seats came out too small.
# Measured 2026-08-18: a real turn against a 32,768 seat totalled 47,448 tokens
# -- roughly 13k system prompt, 9.7k tools, and ~25k of injected context that
# nothing budgeted against the seat at all. A seat sized from tools plus system
# prompt was therefore sized for 22k while the turn demanded 47k, and the
# overflow is silent: the window fills and the oldest spans fall out the front,
# so the model answers from a truncated view and nothing reports it.
#
# It belongs in the overhead figure because seat sizing asks "how big must this
# window be to hold one turn?", and injected context is unambiguously part of
# one turn. It varies where the other two are fixed, which is why it carries
# its own basis rather than being folded into either.
MEASURED_INJECTED_TOKENS = 25000

MEASURED_OVERHEAD_TOKENS = (MEASURED_TOOL_TOKENS
                            + MEASURED_SYSTEM_PROMPT_TOKENS
                            + MEASURED_INJECTED_TOKENS)

CHARS_PER_TOKEN = 4.0

_MEMO: dict = {}
_MEMO_TTL_S = 300.0


def _tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN)


def tool_tokens() -> tuple[int, str]:
    """Size of the tool definitions every tool-using turn carries.

    Returns (tokens, basis). `basis` is "measured" when the live registry was
    read and "recorded" when the constant was used, so a caller can tell the
    difference between a fresh number and a remembered one.
    """
    try:
        from agent_friday.services.agent import CLAUDE_TOOLS
        return _tokens(json.dumps(CLAUDE_TOOLS)), "measured"
    except Exception:
        return MEASURED_TOOL_TOKENS, "recorded"


def system_prompt_tokens() -> tuple[int, str]:
    """Size of Friday's assembled system prompt.

    Assembled, not static: it pulls in vault context, self-knowledge and
    persona, so it is measured by asking for it rather than by reading a file.
    """
    try:
        from agent_friday.services.agent import _get_friday_system_prompt
        return _tokens(_get_friday_system_prompt()), "measured"
    except Exception:
        return MEASURED_SYSTEM_PROMPT_TOKENS, "recorded"


def overhead(*, force: bool = False) -> dict:
    """Everything spent before the conversation starts.

    Memoised for 5 minutes: assembling the system prompt reads the vault and
    several files, and this is called on a planning path that must not become
    an I/O cost per routing decision.
    """
    hit = _MEMO.get("overhead")
    if hit and not force and (time.time() - hit[0]) < _MEMO_TTL_S:
        return hit[1]
    tools, tool_basis = tool_tokens()
    sysp, sys_basis = system_prompt_tokens()
    inj, inj_basis = injected_tokens()
    out = {
        "tool_tokens": tools,
        "system_prompt_tokens": sysp,
        "injected_tokens": inj,
        "total_tokens": tools + sysp + inj,
        "basis": ("measured" if tool_basis == sys_basis == "measured"
                  else "partial" if "measured" in (tool_basis, sys_basis)
                  else "recorded"),
        "tool_basis": tool_basis,
        "system_prompt_basis": sys_basis,
        "injected_basis": inj_basis,
    }
    _MEMO["overhead"] = (time.time(), out)
    return out


def injected_tokens() -> tuple[int, str]:
    """Memory and source context assembled into the turn, and where it came from.

    Prefers a figure recorded from a real turn, because this is the one
    component that genuinely varies and a constant will always be wrong for
    somebody. Falls back to the measured reference figure rather than zero: a
    stale number is a far smaller error than pretending 25k of context does not
    exist, which is the mistake this function was written to end.
    """
    try:
        from agent_friday.core import runtime_dir
        p = runtime_dir() / "residency" / "turn_demand.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        v = int(d.get("injected_tokens") or 0)
        if v > 0:
            return v, "recorded"
    except Exception:
        pass
    return MEASURED_INJECTED_TOKENS, "reference"


def record_turn_demand(*, injected_tokens: int, total_tokens: int | None = None,
                       note: str = "") -> None:
    """Persist what a REAL turn actually demanded, so seats size against it.

    Called from the turn path rather than guessed at here. The whole defect this
    addresses is that seat sizing was reasoning about a turn it had never
    measured.
    """
    try:
        from agent_friday.core import runtime_dir
        p = runtime_dir() / "residency" / "turn_demand.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "injected_tokens": int(injected_tokens),
            "total_tokens": total_tokens,
            "note": note,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, indent=2), encoding="utf-8")
        _MEMO.clear()
    except Exception:
        pass


def overhead_tokens() -> int:
    """The single number the policy needs."""
    return overhead()["total_tokens"]


def working_room(num_ctx: int) -> dict:
    """What is actually left for the conversation at a given window size."""
    ov = overhead_tokens()
    room = num_ctx - ov
    return {
        "num_ctx": num_ctx,
        "overhead_tokens": ov,
        "room_tokens": room,
        "room_pct": round(100.0 * room / num_ctx, 1) if num_ctx else 0.0,
        "sufficient": room >= 0,
    }


def reset_cache() -> None:
    _MEMO.clear()
