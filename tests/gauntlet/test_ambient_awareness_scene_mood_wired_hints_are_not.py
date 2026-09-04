"""Gauntlet finding F60 (claim-corpus sweep, 2026-09-04): ambient_awareness's
module docstring claimed three adaptive behaviors -- shorter replies,
suppressed interruptions, and a holo-scene tint. Verified against the real
frontend (index.html) and call graph:

  * scene tint: REAL. index.html polls /api/ambient/state every 60s and
    feeds state.scene_mood into window._fridayAmbientMood, which reaches
    fridayVibe.setSystemMood().
  * suppressed interruptions / shorter replies: NOT real. get_ambient_state()
    computes them into `hints` every call, but index.html's poll handler
    only reads `state.scene_mood` -- the full state (including hints) is
    stashed on window._fridayAmbientState and never read back. And
    ambient_prompt_directive(), the function that would splice a directive
    into a system prompt, has zero callers anywhere in the codebase.

This probe pins the backend half of that finding: the hints ARE computed
(so a future frontend/prompt wiring has something real to consume), and
ambient_prompt_directive() still produces a real directive string when
called directly, proving the gap is "nothing calls this" rather than
"this doesn't work."
"""
from __future__ import annotations

import agent_friday.services.ambient_awareness as ambient_awareness


class TestHintsAreComputedButNotConsumed:
    def test_get_ambient_state_includes_hints_and_scene_mood(self):
        state = ambient_awareness.get_ambient_state()
        assert "hints" in state
        assert "scene_mood" in state
        assert "suppress_interruptions" in state["hints"]
        assert "response_length" in state["hints"]

    def test_ambient_prompt_directive_produces_real_text_for_a_low_energy_state(self, monkeypatch):
        monkeypatch.setattr(
            ambient_awareness, "get_ambient_state",
            lambda: {"hints": {"response_length": "short", "offer_breaks": True,
                                "suppress_interruptions": False, "tone": "calm"}},
        )
        directive = ambient_awareness.ambient_prompt_directive()
        assert "short" in directive.lower()
        assert directive != "", (
            "ambient_prompt_directive() returned nothing for a state with real "
            "hints -- if this ever gets wired into the chat pipeline it needs "
            "to actually produce text, which it still does (nothing calls it, "
            "which is the actual F60 gap, not this)"
        )

    def test_ambient_prompt_directive_is_blank_for_an_unremarkable_state(self, monkeypatch):
        monkeypatch.setattr(
            ambient_awareness, "get_ambient_state",
            lambda: {"hints": {"response_length": "normal", "offer_breaks": False,
                                "suppress_interruptions": False, "tone": None}},
        )
        assert ambient_awareness.ambient_prompt_directive() == ""
