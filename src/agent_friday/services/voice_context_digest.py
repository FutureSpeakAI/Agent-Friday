"""The voice front's context digest: who Friday and the owner are, in a fixed
budget (local voice spec §10 P1).

The brain's standing prompt is ~26K tokens; a small front model prefills
whatever it is given on every cold start, so it gets a digest of the SAME
gated context instead of the whole of it. The digest is cut by rule, not by a
model: sections are taken in priority order (the identity and personality
preamble, core identity, then the wiki indexes, then everything else in
prompt order) until ``DIGEST_TOKEN_BUDGET`` is spent, and the sections that
describe text chat's own tools and procedures are left out (the front's tools
come from the voice contract, and naming another tool list would make it
promise tools it does not hold). Deep context stays one ``ask_friday`` away.

The context is the LOCAL provider's view: the front is a local model on this
machine, so it is gated as local (``vault_control=None``), like the brain.
"""
from __future__ import annotations

import re

#: ~3K tokens at four characters a token (the budget the spec sets).
DIGEST_TOKEN_BUDGET = 3000

#: Sections that describe text chat's tool surface and procedures. The voice
#: contract and the voice rules replace them.
DROPPED_SECTIONS = (
    "AVAILABLE TOOLS", "COMPUTER CONTROL", "SELF-IMPROVEMENT",
    "TASK DELEGATION", "PACKAGE INSTALLATION", "AUTONOMOUS OPERATION",
    "WHAT YOU CAN DO ON THIS COMPUTER", "SAY WHAT YOU ARE ABOUT TO DO",
)

#: Taken first, in this order, after the preamble.
PRIORITY_SECTIONS = ("CORE IDENTITY",)

_HEADER = re.compile(r"^== (.+?) ==\s*$", re.M)


def split_sections(text: str) -> list:
    """``[(title, body)]`` in prompt order; the text before the first header
    is the preamble, titled ""."""
    out = []
    marks = list(_HEADER.finditer(text or ""))
    head = (text or "")[:marks[0].start()] if marks else (text or "")
    if head.strip():
        out.append(("", head.strip()))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(1).strip(), text[m.start():end].strip()))
    return out


def digest(context: str, budget_tokens: int = DIGEST_TOKEN_BUDGET) -> str:
    """The digest of `context` within `budget_tokens`. Pure."""
    budget = int(budget_tokens) * 4
    secs = [(t, b) for t, b in split_sections(context)
            if not any(t.upper().startswith(d) for d in DROPPED_SECTIONS)]

    def rank(item):
        i, (title, _b) = item
        if title == "":
            return (0, i)
        up = title.upper()
        if up in PRIORITY_SECTIONS:
            return (1, PRIORITY_SECTIONS.index(up))
        if up.endswith(" INDEX"):
            return (2, i)
        return (3, i)
    chosen, used = [], 0
    for _i, (title, body) in sorted(enumerate(secs), key=rank):
        if used >= budget:
            break
        room = budget - used
        piece = body if len(body) <= room else body[:max(0, room - 1)].rstrip() + "…"
        chosen.append((_i, piece))
        used += len(piece) + 2
    # Back in prompt order, so the digest reads like the prompt it came from.
    return "\n\n".join(p for _i, p in sorted(chosen))


def build(settings=None) -> str:
    """The digest of Friday's stable local context (before the volatile
    marker; the volatile tail rides in each user turn)."""
    from agent_friday.services.model_router import _get_friday_system_prompt
    from agent_friday.services.prompt_cache import VOLATILE_MARKER
    try:
        ctx = _get_friday_system_prompt(provider="local", vault_control=None)
    except Exception as e:   # the front still answers, with less context
        return f"(context unavailable: {type(e).__name__})"
    idx = ctx.find(VOLATILE_MARKER)
    if idx >= 0:
        ctx = ctx[:idx]
    return digest(ctx)
