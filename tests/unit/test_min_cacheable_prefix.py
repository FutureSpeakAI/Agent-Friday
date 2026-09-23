"""The minimum cacheable prefix is per-model, published, and not guessable.

Anthropic ignores a `cache_control` breakpoint on a prefix shorter than the
model's minimum: the request succeeds, nothing is cached, and no error comes
back. There are only four breakpoints, so getting this number wrong costs in one
of two directions:

  * too LOW  -- a breakpoint is spent on a prefix that can never hit;
  * too HIGH -- a prefix that would have cached gets no breakpoint at all.

`_MIN_CACHEABLE_DEFAULT` is 1024, which is the second (safe) direction, so an
absent model is a missed saving rather than a wrong bill. But Claude Opus 5.5
and Claude Fable 5.1 are both 512 and were both absent, so every prefix they had
in the 512-1023 band went uncached.

Figures read off the prompt-caching page on 2026-09-23, which names them:

    512 tokens for Claude Fable 5.1, Claude Mythos 5.1, Claude Opus 5.5,
    Claude Opus 5, Claude Fable 5, and Claude Mythos 5
    1,024 tokens for Claude Opus 4.8, Claude Sonnet 5, Claude Sonnet 4.6,
    Claude Sonnet 4.5, ...
    4,096 tokens for Claude Haiku 4.5

The minimum is not monotonic across generations -- 512 on the newest models,
4,096 on Haiku 4.5 -- so it cannot be inferred from a model's age and has to be
looked up per model.
"""

import pytest

from agent_friday.services import prompt_cache as pc

#: Exactly what the published list says, for every id Friday can dispatch to.
PUBLISHED_MINIMUM = {
    "claude-fable-5-1": 512,
    "claude-fable-5": 512,
    "claude-opus-5-5": 512,
    "claude-opus-5": 512,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-haiku-4-5": 4096,
    "claude-haiku-4-5-20251001": 4096,
}


@pytest.mark.parametrize("model,minimum", sorted(PUBLISHED_MINIMUM.items()))
def test_every_model_carries_its_published_minimum(model, minimum):
    assert pc._min_cacheable(model) == minimum


def test_the_newest_opus_is_512_not_the_conservative_default():
    """The row this test exists for. Opus 5.5 fell to 1024 and lost the band."""
    assert pc._min_cacheable("claude-opus-5-5") == 512
    assert pc._min_cacheable("claude-opus-5-5") != pc._MIN_CACHEABLE_DEFAULT


def test_fable_5_1_is_512_too():
    """Named in the same sentence, already selectable through discovery, and
    already priced -- so leaving it at the default would have been fixing only
    the line I happened to be looking at."""
    assert pc._min_cacheable("claude-fable-5-1") == 512


def test_an_unknown_model_falls_back_conservatively():
    """Unknown must err HIGH. Guessing low spends one of four breakpoints on a
    prefix that cannot hit; guessing high only forgoes one."""
    assert pc._min_cacheable("acme-whatever-1") == pc._MIN_CACHEABLE_DEFAULT
    assert pc._min_cacheable("") == pc._MIN_CACHEABLE_DEFAULT
    assert pc._min_cacheable(None) == pc._MIN_CACHEABLE_DEFAULT
    assert pc._MIN_CACHEABLE_DEFAULT >= 1024


def test_the_minimum_is_not_monotonic_so_it_cannot_be_guessed():
    """Pinned because the tempting shortcut -- "newer means smaller" -- is
    wrong: Haiku 4.5 is newer than Fable 5 and needs 8x the prefix."""
    assert pc._min_cacheable("claude-haiku-4-5") > pc._min_cacheable("claude-opus-5-5")


# ── the consequence, at the seam that actually decides ──────────────────────

def _system_of_tokens(n):
    """A system prompt long enough to estimate as `n` tokens."""
    return "x" * int(n * pc.CHARS_PER_TOKEN)


@pytest.mark.parametrize("model,expect_cached", [
    ("claude-opus-5-5", True),    # 512 minimum -> a 700-token prefix caches
    ("claude-fable-5-1", True),
    ("claude-sonnet-5", False),   # 1024 minimum -> 700 is below it
    ("acme-unknown-1", False),    # unknown -> conservative default
])
def test_a_700_token_prefix_gets_a_breakpoint_only_where_the_floor_allows(
        model, expect_cached):
    """The real consequence: `_split_system` is what marks the breakpoint, and
    it asks `_min_cacheable`. 700 tokens sits inside the 512-1023 band, so this
    is exactly the range Opus 5.5 was losing."""
    _out, cached = pc._split_system(_system_of_tokens(700), model)
    assert cached is expect_cached


def test_a_400_token_prefix_is_never_cached_on_any_model():
    """Below every published floor, so no model may mark it."""
    for model in PUBLISHED_MINIMUM:
        _out, cached = pc._split_system(_system_of_tokens(400), model)
        assert cached is False, model
