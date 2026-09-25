"""The standing prompt has a declared ceiling, and something checks it.

WHY THIS EXISTS. A warm chat turn on the local seat once took forty
to fifty seconds, and forty-three of those were one line in the seat's own log:

    prompt eval time = 52,962 ms / 26,284 tokens

Twenty-six thousand tokens of standing prompt, re-read at about 500 tokens a
second, before the model had seen a word the user typed. Several separate
fixes brought that down, but nothing in the tree would have noticed it growing
back. The number had never been declared, so it could not regress - it could
only drift, which is how it got to 26,000 in the first place.

The ceilings below are deliberately a little above what is measured today. A
budget set exactly at the current value fails on the next honest sentence
added to a prompt and gets raised without thought, which teaches everyone to
raise it. Set with room, it stays quiet until something real happens.

THIS IS A COST, NOT A BUG. Every token here buys something - Friday's
identity, her standing instructions, the tools she can actually call. The test
does not say the prompt is too big. It says the size is a decision, and a
decision that moves should move on purpose.
"""
from __future__ import annotations

import pytest

from agent_friday.services import tool_budget as tb

#: Measured on the reference machine: ~4,250 tokens.
#: Headroom to 6,000 - about forty per cent - because the system prompt
#: legitimately grows with capabilities, and because part of it is assembled
#: from the user's own wiki and vault, which are not fixed.
MAX_SYSTEM_PROMPT_TOKENS = 6_000

#: Every tool's full schema: ~17,959 tokens for 100 tools. This is what a turn
#: pays only when the tool index is switched off (FRIDAY_TOOL_CATALOGUE=0), so
#: it is the fallback's cost, and it still grows with every tool added.
MAX_TOOL_CATALOGUE_TOKENS = 20_000

#: What a turn actually sends by default: the tool index (name and one line
#: per tool, services/tool_catalogue.py) plus the few resident tools. ~3,266
#: tokens for 100 tools. This is the number that decides prompt-eval time.
MAX_TOOL_OPENING_TOKENS = 4_500

#: What the seat reads before the conversation starts. Measured ~17,071.
#: At the 500 tokens/second this machine sustains, 22,000 is about 44 seconds
#: on a cache miss - already slow, and the point past which no amount of
#: caching hides it.
MAX_STANDING_PROMPT_TOKENS = 22_000


def _system_prompt_tokens() -> int:
    from agent_friday.services.model_router import _build_context_prompt
    text, _sources = _build_context_prompt("hello", workspace="chat",
                                           provider="local")
    return len(text) // 4


def _tool_catalogue_tokens() -> int:
    from agent_friday.services.agent import CLAUDE_TOOLS
    return tb._tokens(CLAUDE_TOOLS)


def test_the_system_prompt_stays_within_its_budget():
    got = _system_prompt_tokens()
    assert got <= MAX_SYSTEM_PROMPT_TOKENS, (
        "the assembled system prompt is ~%d tokens against a declared ceiling "
        "of %d. On the local seat that is ~%.0f seconds of prompt evaluation "
        "on every cache miss. If the growth is deliberate, raise the ceiling "
        "in this file and say why in the commit."
        % (got, MAX_SYSTEM_PROMPT_TOKENS, got / 500.0))


def test_what_a_turn_sends_about_tools_stays_within_its_budget():
    from agent_friday.services.agent import CLAUDE_TOOLS
    from agent_friday.services import tool_catalogue as tc
    assert tc.enabled(), "the tool index is on by default; this budget assumes it"
    got = tb._tokens(tc.opening_set(CLAUDE_TOOLS))
    assert got <= MAX_TOOL_OPENING_TOKENS, (
        "the tool index plus resident tools is ~%d tokens against a ceiling of "
        "%d. It is sent on every turn by every seat." % (got, MAX_TOOL_OPENING_TOKENS))


def test_the_tool_catalogue_stays_within_its_budget():
    got = _tool_catalogue_tokens()
    assert got <= MAX_TOOL_CATALOGUE_TOKENS, (
        "the tool catalogue is ~%d tokens against a declared ceiling of %d. "
        "Tool schemas render before everything else in the prompt, so this is "
        "paid on every turn by every seat. A new tool is not free."
        % (got, MAX_TOOL_CATALOGUE_TOKENS))


def test_the_standing_prompt_stays_within_its_budget():
    """The one that matters: what the seat reads before the user's first word."""
    got = _system_prompt_tokens() + _tool_catalogue_tokens()
    assert got <= MAX_STANDING_PROMPT_TOKENS, (
        "the standing prompt is ~%d tokens against a declared ceiling of %d - "
        "about %.0f seconds of prompt evaluation on a 500 tok/s local seat, "
        "before the model reads anything the user typed."
        % (got, MAX_STANDING_PROMPT_TOKENS, got / 500.0))


def test_the_budget_is_measured_and_not_asserted_into_existence():
    """A ceiling nothing can reach is not a budget.

    If the measurement ever returns zero - an import that fails and is
    swallowed, a prompt builder that returns empty on a fixture - the three
    tests above pass for a reason that has nothing to do with the prompt. That
    is the same failure as a test whose fixture cannot exercise the behaviour,
    and it is worth one assertion to rule out.
    """
    sys_tokens = _system_prompt_tokens()
    tool_tokens = _tool_catalogue_tokens()
    assert sys_tokens > 500, \
        "the system prompt measured %d tokens; the measurement is broken, " \
        "not the prompt" % sys_tokens
    assert tool_tokens > 500, \
        "the tool catalogue measured %d tokens; the measurement is broken, " \
        "not the catalogue" % tool_tokens


def test_the_ceilings_leave_room_but_not_a_field():
    """A budget set far above reality never fires; one set at reality always
    does. Both get ignored. This keeps the declared numbers honest about
    being close to what was measured."""
    standing = _system_prompt_tokens() + _tool_catalogue_tokens()
    slack = MAX_STANDING_PROMPT_TOKENS / max(standing, 1)
    assert 1.0 < slack < 2.0, (
        "the standing-prompt ceiling is %.2fx the measured value. Under 1.0 it "
        "is already breached; over 2.0 it will never fire and is decoration."
        % slack)


if __name__ == "__main__":  # a quick way to see the numbers, not a test
    print("system prompt  : %6d tokens (ceiling %d)"
          % (_system_prompt_tokens(), MAX_SYSTEM_PROMPT_TOKENS))
    print("tool catalogue : %6d tokens (ceiling %d)"
          % (_tool_catalogue_tokens(), MAX_TOOL_CATALOGUE_TOKENS))
    print("standing total : %6d tokens (ceiling %d)"
          % (_system_prompt_tokens() + _tool_catalogue_tokens(),
             MAX_STANDING_PROMPT_TOKENS))
    assert pytest  # keep the import honest when run directly
