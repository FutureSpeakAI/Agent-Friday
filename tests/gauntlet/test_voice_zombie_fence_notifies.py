"""Gauntlet finding: /ws/live's reconnect loop ends a call for several
reasons the browser didn't initiate -- local-only turned on mid-call, the
handle-exhausted-after-3-tries giveup, a hard connect failure -- and every
one of those paths sends a status/error frame to the browser BEFORE
`done.set()`, because `_safe_send()` itself no-ops once `done` is set (so
the ordering is load-bearing, not stylistic).

The "zombie fence" branch -- the exact path a second concurrent /ws/live
connection (e.g. a second browser tab) triggers, via the single global
`_LIVE_CONN_GEN` slot ("single-user app: one global slot, last-writer-
wins") -- broke that idiom: it called `done.set()` with no `_safe_send`
at all. Consequence: opening voice mode in a second tab silently killed
the first tab's in-progress call with zero notification -- the tab's UI
was left showing a "live" call that had actually gone dead, with nothing
telling the user why.

This probe must be RED before the fix (the zombie-fence branch has no
_safe_send call, or calls it after done.set()) and GREEN after.
"""
from __future__ import annotations

import inspect

import agent_friday.routes.voice as vr


def _ws_live_reconnect_loop_body() -> str:
    src = inspect.getsource(vr)
    i_ws_live_def = src.index("def ws_live(ws):")
    body = src[i_ws_live_def:]
    i_next_def = body.index("\n    def ", 1) if "\n    def " in body[1:] else len(body)
    body = body[:i_next_def]
    i_reconnect_loop = body.index("Reconnect loop")
    return body[i_reconnect_loop:]


class TestVoiceZombieFenceNotifies:
    def test_zombie_fence_sends_status_before_ending_the_call(self):
        renewal_body = _ws_live_reconnect_loop_body()

        i_zombie_comment = renewal_body.index("Zombie fence")
        i_zombie_check = renewal_body.index("_live_conn_current(_conn_gen)", i_zombie_comment)
        # Scope the search to just this branch's body, not the whole rest
        # of the (much longer) reconnect loop -- the next `if` after the
        # zombie check starts the following branch.
        branch_end = renewal_body.index("\n                        _use_handle", i_zombie_check)
        branch = renewal_body[i_zombie_check:branch_end]

        assert "_safe_send" in branch, (
            "the zombie-fence branch (fires when a second /ws/live "
            "connection supersedes this one, e.g. a second browser tab) "
            "never notifies the browser before ending the call -- every "
            "other reason this loop ends a call unprompted sends a "
            "status/error frame first"
        )
        i_send = branch.index("_safe_send")
        i_done_set = branch.index("done.set()")
        assert i_send < i_done_set, (
            "_safe_send is called in the zombie-fence branch, but AFTER "
            "done.set() -- _safe_send() no-ops once done is set (see its "
            "own guard), so the notification must be sent first"
        )

    def test_other_unprompted_endings_still_notify_first(self):
        """No-op-shaped sanity check: confirms the sibling local-only-mid-
        call branch (the one this fix's ordering was modeled on) still
        notifies before ending, so this probe's own methodology is sound
        against a KNOWN-correct branch, not just the one being fixed."""
        renewal_body = _ws_live_reconnect_loop_body()
        i_local_only = renewal_body.index("_renewal_local_only")
        i_send = renewal_body.index("_safe_send", i_local_only)
        i_done_set = renewal_body.index("done.set()", i_local_only)
        assert i_send < i_done_set, (
            "the local-only-mid-call branch no longer notifies before "
            "ending the call -- this probe's methodology assumes that "
            "branch stays correct as the reference point"
        )
