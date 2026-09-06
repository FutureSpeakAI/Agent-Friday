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

CORRECTION (2026-09-04, caught by an independent cold re-verification of
this fix): the original version of this probe searched ws_live's body for
the bare substring "local_only", and the ledger's evidence claimed that
substring had "zero" occurrences pre-fix. Both were wrong in a way that
happened to still work: ws_live's body already contained exactly one
`local_only` match pre-fix, from an unrelated call, `_vault_local_only()`
(a vault-scoped setting, nothing to do with model_routing.mode) --
sitting well after this function's `resolve_gemini_key` call. The probe's
first assertion (`"local_only" in body`) therefore already passed
pre-fix; only the SECOND assertion (ordering relative to
`resolve_gemini_key`) actually forced a real pre-fix failure, and only by
coincidence of where that unrelated call happened to sit in the function.
Rewritten to pin the fix's own variable name, `_ws_local_only` -- which
grep-confirmed did not exist anywhere in this file before the fix commit
(cdeed3d) -- so a match can only ever be the real model-routing gate, not
an incidental, differently-scoped `local_only` string elsewhere in the
same function.
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

        # Pin the fix's own variable name, not the bare substring
        # "local_only" -- ws_live's body also contains an unrelated
        # `_vault_local_only()` call (a different, vault-scoped setting)
        # that already matched that bare substring before this fix existed,
        # which made an earlier version of this assertion pass vacuously.
        # `_ws_local_only` is unique to the real model-routing gate.
        assert "_ws_local_only" in body, (
            "ws_live's body never computes a model-routing local-only "
            "flag at all -- the Gemini Live websocket dispatch point has "
            "no local-only gate, unlike _resolve_voice_engine and "
            "_synthesize_tts_wav (F16). (Note: the body DOES contain an "
            "unrelated 'local_only' substring via _vault_local_only(), a "
            "vault setting -- that is not this gate and must not satisfy "
            "this check.)"
        )
        i_local_only_check = body.index("_ws_local_only")
        i_key_resolution = body.index("resolve_gemini_key")
        assert i_local_only_check < i_key_resolution, (
            "ws_live computes _ws_local_only somewhere, but not before it "
            "starts resolving/using a Gemini API key -- the gate must run "
            "first so a local-only connection is refused before any "
            "Gemini work happens, matching the fail-closed pattern F16 "
            "established"
        )
        i_refusal_return = body.index("local-only mode is on")
        assert i_local_only_check < i_refusal_return < i_key_resolution, (
            "the gate must actually refuse (send the error and return) "
            "before Gemini key work starts, not merely compute the flag "
            "and continue on"
        )

    def test_ws_live_rechecks_local_only_before_every_renewal_leg(self):
        """The connect-time gate above only covers the FIRST Gemini
        connection. /ws/live's own comments say a single Gemini Live
        connection is capped (~10 min) and the reconnect loop is what makes
        an hours-long call possible by re-dialing Gemini for each new leg --
        so turning local-only on mid-call must stop the NEXT renewal, not
        just future new connections. Pins that the reconnect loop
        ('Reconnect loop' section, guarded by `while not done.is_set():`)
        itself re-checks local_only, not only the code that runs once before
        the loop starts."""
        src = inspect.getsource(vr)
        i_ws_live_def = src.index("def ws_live(ws):")
        body = src[i_ws_live_def:]
        i_next_def = body.index("\n    def ", 1) if "\n    def " in body[1:] else len(body)
        body = body[:i_next_def]

        i_reconnect_loop = body.index("Reconnect loop")
        renewal_body = body[i_reconnect_loop:]

        # Pin the fix's own variable name, `_renewal_local_only`, not the
        # bare substring "local_only" -- same precision fix as the
        # connect-time test above (grep-confirmed this section has no
        # unrelated 'local_only' match today, but pinning the specific
        # name rather than a generic substring removes that risk for good).
        assert "_renewal_local_only" in renewal_body, (
            "the reconnect/renewal loop never computes a local-only "
            "recheck at all -- the connect-time gate is checked exactly "
            "once, so turning local-only on mid-call has no effect on a "
            "call already in progress: audio keeps streaming to Gemini "
            "for every subsequent renewal leg of what the code's own "
            "comments say can be an hours-long call"
        )
        i_renewal_check = renewal_body.index("_renewal_local_only")
        i_first_connect_attempt = renewal_body.index("active_client.aio.live.connect")
        assert i_renewal_check < i_first_connect_attempt, (
            "_renewal_local_only is computed somewhere in the renewal loop, "
            "but not before the first leg-connect attempt inside it -- the "
            "recheck must run before Gemini is redialed for a new leg, not "
            "after"
        )
