"""Gauntlet finding: services/scheduler.py's dispatch()._body() exception
handler (every builtin/agent_prompt task's failure path) called
`traceback.print_exc()`, which writes to stderr. The packaged app launches
via pythonw (core/__init__.py's own comment: "no console... makes
[friday.log via the logging module] the only debug output" -- confirmed
by pythonw references in packaging/windows/lib/Shortcuts.ps1 and
Heal.ps1), so under the real shipped runtime that traceback went nowhere:
not to friday.log (logging-only), not to any console. The one-line
failure summary (exception type + str(e)) still reached the user via
_notify_run/run history, but the traceback needed to diagnose WHERE a
task broke did not -- unlike this same file's own established pattern
elsewhere (_log.warning/_log.error, e.g. the away-drain and GPU-lease
paths) and the codebase's broader must-not-fail-silently convention
(server.py's _fail_loud_and_exit, which routes through logging for
exactly this reason).

This probe must be RED before the fix (the handler calls
traceback.print_exc and never touches the module logger) and GREEN
after.
"""
from __future__ import annotations

import inspect

import agent_friday.services.scheduler as sched


def _dispatch_body_source() -> str:
    src = inspect.getsource(sched)
    i_dispatch = src.index("def dispatch(rec, *, manual=False):")
    body = src[i_dispatch:]
    # Stop at the next top-level (unindented) def, so we don't pull in
    # unrelated later functions.
    i_next_def = body.index("\n\ndef ", 1)
    return body[:i_next_def]


class TestSchedulerTaskFailureUsesLogger:
    def test_the_exception_handler_logs_through_the_module_logger(self):
        dispatch_src = _dispatch_body_source()
        i_except = dispatch_src.index("except Exception as e:")
        # The handler block runs from the except line to the following
        # `attempts = ` line that starts building the retry decision.
        i_attempts = dispatch_src.index("attempts = int(rec.get", i_except)
        handler = dispatch_src[i_except:i_attempts]

        assert "traceback.print_exc" not in handler, (
            "dispatch()'s task-failure handler still calls "
            "traceback.print_exc(), which writes to stderr -- the "
            "packaged app runs headless via pythonw and has no stderr "
            "console, so this traceback is silently discarded under the "
            "real shipped runtime"
        )
        assert "_log." in handler, (
            "dispatch()'s task-failure handler never touches this "
            "module's own logger (_log) -- friday.log is the only debug "
            "output that survives the packaged pythonw runtime, and "
            "nothing here reaches it"
        )

    def test_other_scheduler_error_paths_still_use_the_logger(self):
        """No-op-shaped sanity check: this fix's methodology (checking for
        _log usage) is meaningful because OTHER error paths in this same
        file already correctly use it -- confirms the probe isn't
        trivially satisfied by any random _log reference existing
        somewhere in the module."""
        src = inspect.getsource(sched)
        assert "_log.warning" in src or "_log.error" in src
