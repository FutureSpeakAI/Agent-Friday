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
