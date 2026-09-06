"""Gauntlet finding F57 (claim-corpus sweep, 2026-09-04):
research/harness.py's _pseudo_toolcall_check() called
tool_integrity.find_pseudo_toolcalls(text) with only one argument, but that
function requires a second, REQUIRED tool_names argument. Every call raised
TypeError, silently caught by `except Exception: return True` ("cannot
check -> do not fabricate a failure") -- so this integrity check never
actually ran, for any research draft, ever, since the function was
written. Every draft passed it unconditionally, not because the draft was
clean, but because the check itself was broken.

This probe proves the check now genuinely fires: a draft whose text
narrates a real tool name in prose form is caught, and a clean draft still
passes.
"""
from __future__ import annotations

from agent_friday.services.research.harness import _pseudo_toolcall_check


class TestPseudoToolcallCheckActuallyRuns:
    def test_a_draft_narrating_a_real_tool_call_is_rejected(self):
        draft = {"sections": [
            {"body": "I then ran [search_web(query=\"more sources\")] and found this."},
        ]}
        assert _pseudo_toolcall_check(draft) is False, (
            "a draft narrating a pseudo tool call in prose was NOT caught -- "
            "the check is still not actually running (F57 regressed)"
        )

    def test_a_clean_draft_still_passes(self):
        draft = {"sections": [
            {"body": "The research shows three independent sources agree on this point."},
        ]}
        assert _pseudo_toolcall_check(draft) is True

    def test_does_not_raise_on_malformed_input(self):
        """Falsifiability / safety check: the fail-open except clause must
        still protect a genuinely malformed draft from crashing the caller."""
        assert _pseudo_toolcall_check({}) is True
        assert _pseudo_toolcall_check({"sections": None}) is True
