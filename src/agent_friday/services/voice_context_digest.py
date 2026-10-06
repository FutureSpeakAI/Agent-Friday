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

#: Public workspace guidance shares this budget; it never enlarges the prompt.
PUBLIC_GUIDE_MAX_CHARS = 1600

#: Sections that describe text chat's tool surface and procedures. The voice
#: contract and the voice rules replace them.
DROPPED_SECTIONS = (
    "AVAILABLE TOOLS", "COMPUTER CONTROL", "SELF-IMPROVEMENT",
    "TASK DELEGATION", "PACKAGE INSTALLATION", "AUTONOMOUS OPERATION",
    "WHAT YOU CAN DO ON THIS COMPUTER", "SAY WHAT YOU ARE ABOUT TO DO",
)

#: Taken first, in this order, after the preamble.
PRIORITY_SECTIONS = ("CORE IDENTITY", "AGENT PERSONALITY", "USER MODEL",
                     "LEARNED HEURISTICS", "CAPABILITY DISCOVERY")

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


def _public_career_guide() -> str:
    """Read the compact public guide already shipped for voice, or omit it.

    Full SELF.md may be trimmed before its workspace section. Reserving this
    short product explanation keeps basic help available to the voice front
    without giving it the text-chat tool registry or private career records.
    """
    try:
        from agent_friday.core import _load_voice_demo
        document = _load_voice_demo()
        marker = "### Career workspace walkthrough"
        if marker not in document:
            return ""
        section = document.split(marker, 1)[1]
        section = re.split(r"^#{1,3} |^---\s*$", section, maxsplit=1, flags=re.M)[0].strip()
        if not section:
            return ""
        guide = "== CAREER WORKSPACE HELP ==\n" + section
        return guide if len(guide) <= PUBLIC_GUIDE_MAX_CHARS else ""
    except Exception:
        return ""


def build(settings=None) -> str:
    """The digest of Friday's stable local context (before the volatile
    marker; the volatile tail rides in each user turn)."""
    from agent_friday.services.model_router import _get_friday_system_prompt
    from agent_friday.services.prompt_cache import VOLATILE_MARKER
    guide = _public_career_guide()
    try:
        ctx = _get_friday_system_prompt(provider="local", vault_control=None)
    except Exception as e:   # public help remains available without private context
        ctx = f"(context unavailable: {type(e).__name__})"
    idx = ctx.find(VOLATILE_MARKER)
    if idx >= 0:
        ctx = ctx[:idx]
    if not guide:
        return digest(ctx)
    # Round the public block plus its separator up to whole budget units. The
    # existing digest still puts identity first and drops text-chat tool sections.
    reserved = (len(guide) + 2 + 3) // 4
    context = digest(ctx, budget_tokens=max(0, DIGEST_TOKEN_BUDGET - reserved))
    return "\n\n".join(part for part in (context, guide) if part)
