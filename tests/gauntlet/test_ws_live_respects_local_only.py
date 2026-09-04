"""Gauntlet finding: /ws/live (the Gemini Live websocket bridge,
routes/voice.py's `ws_live`) never checked `model_routing.mode` at all,
even after F16 fixed local-only gating for `_resolve_voice_engine` and
`_synthesize_tts_wav`. F16's fix only touched the ADVISORY recommendation
returned by /api/voice/session-info -- it never reached the actual
dispatch point. A stale browser tab that fetched session-info before
local-only was turned on (or any client that connects to /ws/live
directly, skipping the recommendation) could still stream mic audio and
conversation text to Gemini regardless of the setting.

`ws_live` is a closure nested inside a Flask-Sock route registration
function, not an independently callable module-level function, so this
codebase's established pattern for pinning behavior inside it is a
source-level check (see tests/unit/test_voice_live_tuning.py's
TestToolChoreography for precedent) rather than a full behavioral
websocket test. This probe follows that precedent: it asserts the refusal
check exists AND runs before any Gemini client/key work, not just that
the string exists somewhere in the file.
"""
from __future__ import annotations

import inspect

import agent_friday.routes.voice as vr


class TestWsLiveRespectsLocalOnly:
    def test_ws_live_checks_local_only_before_resolving_a_gemini_key(self):
        src = inspect.getsource(vr)
        i_ws_live_def = src.index("def ws_live(ws):")
        # Only look inside ws_live's body, not the rest of the file (e.g.
        # ws_voice_local, which is local-only-safe by construction since it
        # never touches Gemini at all).
        body = src[i_ws_live_def:]
        i_next_def = body.index("\n    def ", 1) if "\n    def " in body[1:] else len(body)
        body = body[:i_next_def]

        assert "local_only" in body, (
            "ws_live's body never mentions local_only at all -- the "
            "Gemini Live websocket dispatch point has no local-only gate, "
            "unlike _resolve_voice_engine and _synthesize_tts_wav (F16)"
        )
        i_local_only_check = body.index("local_only")
        i_key_resolution = body.index("resolve_gemini_key")
        assert i_local_only_check < i_key_resolution, (
            "ws_live checks local_only somewhere, but not before it starts "
            "resolving/using a Gemini API key -- the gate must run first "
            "so a local-only connection is refused before any Gemini work "
            "happens, matching the fail-closed pattern F16 established"
        )
