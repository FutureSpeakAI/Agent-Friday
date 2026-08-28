"""Prompt-cache breakpoints and the hard pre-call ceiling.

The failure this file exists to prevent is not a crash — it is a SILENT one.
A breakpoint placed below volatile content, or a stable prefix that turns out
not to be byte-identical, costs nothing visible: the call succeeds and the bill
does not fall. So these tests assert the two properties a cache actually needs
(the marked prefix is stable across turns; the marks land where the payload is
append-only) rather than merely asserting that a key was added.
"""

import pytest

from agent_friday.services import prompt_cache as pc


def _sys(clock="10:00"):
    return (
        "== AGENT PERSONALITY ==\nstable persona text. " + "x" * 6000 + "\n"
        "== AUTHORITATIVE CLOCK ==\n"
        f"Current datetime: 2026-08-26 {clock} (Wednesday).\n"
        "== TODAY'S CONTEXT ==\nvolatile\n"
    )


def _tools(n=20):
    """Roughly the shape of the live registry: CLAUDE_TOOLS measured 14,041
    tokens on a real turn, well above every model's minimum cacheable prefix."""
    return [{"name": f"tool_{i}", "description": "d" * 900,
             "input_schema": {"type": "object", "properties": {}}}
            for i in range(n)]


class TestBreakpoints:
    def test_system_splits_at_the_clock_and_only_the_prefix_is_cached(self):
        out = pc.apply_anthropic_cache(
            {"model": "claude-sonnet-5", "system": _sys()})
        blocks = out["system"]
        assert isinstance(blocks, list) and len(blocks) == 2
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[1]
        assert blocks[1]["text"].startswith(pc.VOLATILE_MARKER)
        # Reassembly is lossless: caching must not change what the model reads.
        assert blocks[0]["text"] + blocks[1]["text"] == _sys()

    def test_the_cached_system_prefix_is_identical_across_turns(self):
        """The whole point. Two turns a minute apart must share bytes."""
        a = pc.apply_anthropic_cache({"model": "claude-sonnet-5",
                                      "system": _sys("10:00")})["system"]
        b = pc.apply_anthropic_cache({"model": "claude-sonnet-5",
                                      "system": _sys("10:01")})["system"]
        assert a[0]["text"] == b[0]["text"]
        assert a[1]["text"] != b[1]["text"]   # the clock moved, as it should

    def test_short_prefix_gets_no_breakpoint(self):
        """Below the model's minimum a breakpoint is accepted and ignored —
        and breakpoints are limited to four, so spending one is a real loss."""
        short = "== AUTHORITATIVE CLOCK ==\nnow\n"
        out = pc.apply_anthropic_cache({"model": "claude-sonnet-5",
                                        "system": short})
        assert out["system"] == short

    def test_last_tool_carries_the_tool_tier_breakpoint(self):
        out = pc.apply_anthropic_cache({"model": "claude-sonnet-5",
                                        "tools": _tools()})
        assert "cache_control" not in out["tools"][0]
        assert out["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        # Names and schemas untouched — the prefix must serialize identically.
        assert [t["name"] for t in out["tools"]] == [f"tool_{i}" for i in range(20)]

    def test_newest_message_carries_the_rolling_breakpoint(self):
        msgs = [{"role": "user", "content": "hello"},
                {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]
        out = pc.apply_anthropic_cache({"model": "claude-sonnet-5",
                                        "messages": msgs})
        assert out["messages"][0] == msgs[0]          # untouched
        assert out["messages"][1] == msgs[1]          # untouched
        assert out["messages"][-1]["content"][-1]["cache_control"] == {
            "type": "ephemeral"}

    def test_input_payload_is_never_mutated(self):
        """The gate hands us its sealed dict; mutating it would edit the
        payload other code still holds a reference to."""
        msgs = [{"role": "user", "content": "hello"}]
        tools = _tools(2)
        original = {"model": "claude-sonnet-5", "system": _sys(),
                    "messages": msgs, "tools": tools}
        pc.apply_anthropic_cache(original)
        assert original["messages"] is msgs
        assert msgs[0]["content"] == "hello"
        assert "cache_control" not in tools[-1]

    def test_disabled_by_setting_passes_the_payload_straight_through(self,
                                                                    monkeypatch):
        monkeypatch.setattr(pc, "_settings",
                            lambda: {"prompt_cache_enabled": False})
        p = {"model": "claude-sonnet-5", "system": _sys(), "tools": _tools()}
        assert pc.apply_anthropic_cache(p) is p

    def test_a_broken_payload_degrades_to_uncached_not_to_an_error(self):
        """A caching bug must cost a discount, never a turn."""
        p = {"model": "claude-sonnet-5", "messages": "not-a-list",
             "tools": "not-a-list", "system": 17}
        assert pc.apply_anthropic_cache(p) is p


class TestCeiling:
    def test_an_oversized_call_is_refused_before_it_is_billed(self,
                                                              monkeypatch):
        monkeypatch.setattr(pc, "_settings",
                            lambda: {"max_call_input_tokens": 1000})
        with pytest.raises(pc.CallTooLarge) as e:
            pc.check_call_size({"system": "x" * 40_000}, "anthropic")
        # The numbers must be IN the message: an overrun you cannot size is an
        # overrun you cannot act on.
        assert "10,000" in str(e.value) and "1,000" in str(e.value)

    def test_an_ordinary_call_passes(self, monkeypatch):
        monkeypatch.setattr(pc, "_settings", lambda: {})
        assert pc.check_call_size({"system": "x" * 4000}, "anthropic") == 1000

    def test_task_budget_stops_a_runaway_loop(self, monkeypatch):
        """`_call_claude_agent` defaults to max_iters=999 and nothing else
        bounds it; at the measured ~91k tokens/iteration that is 90M tokens."""
        monkeypatch.setattr(pc, "_settings", lambda: {})
        with pc.task_budget(limit=25_000, label="runaway") as b:
            for _ in range(2):
                pc.check_call_size({"system": "x" * 40_000}, "anthropic")
            assert b.spent == 20_000
            with pytest.raises(pc.TaskBudgetExceeded) as e:
                pc.check_call_size({"system": "x" * 40_000}, "anthropic")
        assert "runaway" in str(e.value)

    def test_a_nested_task_also_charges_its_parent(self, monkeypatch):
        """A fan-out must not launder its way past the outer ceiling."""
        monkeypatch.setattr(pc, "_settings", lambda: {})
        with pc.task_budget(limit=100_000, label="outer") as outer:
            with pc.task_budget(limit=100_000, label="inner") as inner:
                pc.check_call_size({"system": "x" * 40_000}, "anthropic")
                assert inner.spent == 10_000
            assert outer.spent == 10_000
        assert pc.current_budget() is None      # scope released

    def test_budget_scope_is_released_even_when_a_call_raises(self,
                                                              monkeypatch):
        monkeypatch.setattr(pc, "_settings", lambda: {})
        try:
            with pc.task_budget(limit=1, label="t"):
                pc.check_call_size({"system": "x" * 40_000}, "anthropic")
        except pc.TaskBudgetExceeded:
            pass
        assert pc.current_budget() is None


def _count_blocks(messages):
    """Content blocks, the unit Anthropic's lookback actually counts."""
    n = 0
    for m in messages:
        c = m.get("content")
        n += len(c) if isinstance(c, list) else 1
    return n


def _marked_positions(messages):
    """Block indexes (from the END, 0 = newest) carrying a cache breakpoint."""
    flat = []
    for m in messages:
        c = m.get("content")
        flat.extend(c if isinstance(c, list) else [{"type": "text", "text": c}])
    return [len(flat) - 1 - i for i, b in enumerate(flat)
            if isinstance(b, dict) and "cache_control" in b]


def _agent_loop_turn(tool_calls):
    """One agent-loop iteration that fans out `tool_calls` tools in parallel.

    This is the shape that breaks a single rolling breakpoint: Claude emits N
    tool_use blocks in one assistant message and we answer with N tool_result
    blocks in one user message, so a single iteration appends 2N content blocks.
    Eleven parallel tools is 22 — past the lookback in one step.
    """
    return [
        {"role": "assistant",
         "content": [{"type": "tool_use", "id": f"t{i}", "name": "f",
                      "input": {}} for i in range(tool_calls)]},
        {"role": "user",
         "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                      "content": "r" * 400} for i in range(tool_calls)]},
    ]


class TestLookbackWindow:
    """A breakpoint walks back at most 20 content blocks to find a prior entry.

    A single rolling breakpoint on the newest message is NOT enough when one
    turn appends more than 20 blocks, which is exactly what the agent loop does
    whenever Claude fans out more than ten tools at once. The previous
    iteration's entry falls out of the window and the next request silently
    misses — full price, no error, nothing in the logs.
    """

    def test_a_long_turn_gets_an_anchor_inside_the_window(self):
        msgs = [{"role": "user", "content": "start"}]
        msgs += _agent_loop_turn(11)          # +22 blocks in one iteration
        out = pc.apply_anthropic_cache(
            {"model": "claude-sonnet-5", "messages": msgs})
        assert _count_blocks(out["messages"]) > 20
        marks = _marked_positions(out["messages"])
        assert 0 in marks, "the rolling breakpoint on the newest block is gone"
        assert len(marks) >= 2, (
            "one breakpoint cannot span a turn longer than the 20-block "
            "lookback — an anchor is needed behind it")
        anchor = sorted(m for m in marks if m > 0)[0]
        assert anchor <= 20, (
            f"anchor sits {anchor} blocks back, outside the lookback window")

    def test_a_short_turn_is_left_alone(self):
        """Below the window the natural prefix match already works.

        Spending a second breakpoint here would buy nothing and burn one of the
        four the API allows.
        """
        msgs = [{"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "again"}]
        out = pc.apply_anthropic_cache(
            {"model": "claude-sonnet-5", "messages": msgs})
        assert _marked_positions(out["messages"]) == [0]

    def test_never_more_than_four_breakpoints(self):
        """Four is the hard API limit; a fifth is a 400, not a missed cache."""
        msgs = [{"role": "user", "content": "start"}]
        msgs += _agent_loop_turn(11)
        out = pc.apply_anthropic_cache({
            "model": "claude-sonnet-5", "system": _sys(),
            "tools": _tools(), "messages": msgs})
        total = (sum(1 for b in out["system"] if "cache_control" in b)
                 + sum(1 for t in out["tools"] if "cache_control" in t)
                 + len(_marked_positions(out["messages"])))
        assert total <= 4, f"{total} breakpoints — the API allows 4"


class TestMinimumCacheablePrefix:
    def test_canonical_haiku_id_carries_its_real_minimum(self):
        """Same dated-key bug as the pricing table, in a second file.

        `claude-haiku-4-5` needs 4096 tokens before a prefix caches at all.
        Keyed only on the dated alias, the canonical id fell to the 1024
        default, so we would mark a 2K-token prefix, Anthropic would ignore it,
        and one of four breakpoints was spent on nothing.
        """
        assert pc._min_cacheable("claude-haiku-4-5") == 4096
        assert (pc._min_cacheable("claude-haiku-4-5")
                == pc._min_cacheable("claude-haiku-4-5-20251001"))

    def test_fable_shares_the_opus_5_floor(self):
        """512, not 1024 — a conservative wrong number is still a wrong number.

        It costs every prefix between the two: marked, cacheable, and skipped.
        """
        assert pc._min_cacheable("claude-fable-5") == 512
        assert pc._min_cacheable("claude-opus-5") == 512
