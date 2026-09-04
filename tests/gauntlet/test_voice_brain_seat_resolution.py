"""Gauntlet finding: routes/voice.py resolved the local voice brain seat by
calling `local_seats.resolve("reasoning")` at two call sites (line 840,
inside `_local_brain_ready()`, and line ~1450, the live-voice reply's model
selection) -- but "reasoning" is not a valid role token. `local_seats.py`'s
`_ROLE_TO_CAPABILITY` maps ROLE names (`brain`, `judge`, `sidekick`,
`extractor`, `heavy`) to capability-routing keys, and "reasoning" is the
capability `brain` maps TO, not a role itself. Every other call site in the
codebase (judgment_gate.py, research/harness.py, sensitivity_classifier.py)
correctly passes a role token.

`_configured("reasoning")` does `_ROLE_TO_CAPABILITY.get("reasoning")` ->
None -> returns None immediately, so `capability_routing.reasoning.model`
(the user's actual configured orchestrator model -- the same setting
routes/chat.py reads correctly for text chat) is NEVER consulted for
voice. `resolve()` then falls through to its size-ordered fallback and
picks the SMALLEST installed local model, unconditionally -- so a live
voice session's answers can come from a materially weaker/different model
than the identical request would get through text chat, silently, with no
error and no log line naming the discrepancy as a bug.

Introduced in commit 12be4a7c (2026-08-21) -- ironically the fix for
"voice used no model / picked an unloaded seat" -- and never covered by a
seat-identity test.
"""
from __future__ import annotations

from agent_friday.routes import voice as v
from agent_friday.services import local_seats


class TestVoiceBrainSeatResolution:
    def test_local_brain_ready_asks_for_the_brain_role_not_reasoning(self, monkeypatch):
        seen_roles = []

        def _spy_resolve(role, configured=None):
            seen_roles.append(role)
            return "qwen3:14b"  # any truthy seat name

        monkeypatch.setattr(local_seats, "resolve", _spy_resolve)

        v._local_brain_ready()

        assert seen_roles == ["brain"], (
            "_local_brain_ready() called local_seats.resolve() with role=%r "
            "-- 'reasoning' is not a role token (it's the CAPABILITY 'brain' "
            "maps to), so _configured() returns None immediately and the "
            "user's actual configured orchestrator model is never consulted "
            "for voice, unlike text chat" % (seen_roles,)
        )

    def test_no_remaining_resolve_reasoning_call_sites_in_voice_py(self):
        """Static check for the second call site (inside the live-voice
        reply handler, not independently unit-testable in isolation) --
        this is a plain text/AST check, not a behavioral mock, so it is
        deliberately narrow: it only proves the literal wrong-role-token
        string is gone from the file, not that the surrounding logic is
        otherwise correct."""
        import inspect
        src = inspect.getsource(v)
        assert 'resolve("reasoning")' not in src and "resolve('reasoning')" not in src, (
            "routes/voice.py still calls local_seats.resolve() with the "
            "invalid role token 'reasoning' somewhere -- every valid role "
            "is one of brain/judge/sidekick/extractor/heavy"
        )
