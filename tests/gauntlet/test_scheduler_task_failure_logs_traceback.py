"""Gauntlet finding F41: services/scheduler.py's dispatch()._body() exception
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
elsewhere (_log.warning/_log.error) and the codebase's broader
must-not-fail-silently convention (server.py's _fail_loud_and_exit, which
routes through logging for exactly this reason).

CORRECTION (2026-09-04, flagged by Stephen's independent cold
re-verification as a text pin): the original probe
(test_scheduler_task_failure_uses_logger.py) only checked dispatch()'s
SOURCE for "_log." and the absence of "traceback.print_exc" -- a text
pin, even though dispatch() is an ordinary, independently-callable
module-level function (not a closure nested in something untestable like
ws_live), so a real behavioral test is straightforward: dispatch a
genuinely failing builtin task exactly the way
tests/unit/test_scheduler.py::test_dispatch_builtin_failure_records_failed
already does, and assert on the ACTUAL log record emitted via pytest's
caplog, not on the source text of the handler that produces it. Kept the
original source-position probe alongside this one rather than deleting
it -- it still correctly proves traceback.print_exc() is gone, which
caplog alone would not directly show (a missing print_exc call has no
log-record footprint to assert on).
"""
from __future__ import annotations

import logging
import time

from agent_friday.services import scheduler as s


def _run_and_wait(rec, sid, timeout_s=3.0):
    s.dispatch(rec, manual=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if s.run_history(sid, limit=1):
            return
        time.sleep(0.05)


class TestSchedulerTaskFailureLogsTraceback:
    def test_a_failing_builtin_task_logs_the_exception_via_the_module_logger(
            self, friday_dir, caplog):
        if s.SCHEDULES_FILE.exists():
            s.SCHEDULES_FILE.unlink()
        if s.RUNS_FILE.exists():
            s.RUNS_FILE.unlink()
        s._RUNNING.clear()

        def _boom():
            raise RuntimeError("kaboom-f41")

        s.register_builtin_task("t_boom_f41", _boom, label="BoomF41",
                                default_trigger="daily", default_spec={"hour": 0})
        rec = s.register_schedule({
            "id": "sch_t_boom_f41", "name": "BoomF41", "trigger": "daily",
            "spec": {"hour": 0}, "task": {"kind": "builtin", "ref": "t_boom_f41"},
            "retry": {"max": 0, "backoff_seconds": 1},
        })

        with caplog.at_level(logging.ERROR, logger="friday.scheduler"):
            _run_and_wait(rec, "sch_t_boom_f41")

        hist = s.run_history("sch_t_boom_f41", limit=1)
        assert hist and hist[0]["status"] == "failed", (
            "the task didn't actually fail the way this test expects -- "
            "setup problem, not the thing F41 is about"
        )

        scheduler_records = [r for r in caplog.records if r.name == "friday.scheduler"]
        assert scheduler_records, (
            "a builtin task that raised an exception produced NO log record "
            "at all on the 'friday.scheduler' logger -- under the real "
            "packaged (pythonw, no console) runtime, friday.log is the only "
            "place a task failure's traceback could ever surface, and "
            "nothing reached it (findings.jsonl F41)"
        )
        assert any(r.exc_info for r in scheduler_records), (
            "the scheduler logged something about this failure, but none "
            "of the log records carry exc_info -- so even though a line "
            "reached friday.log, the actual traceback (which is what's "
            "needed to diagnose WHERE the task broke) still didn't"
        )
        assert any("kaboom-f41" in r.getMessage() or
                   (r.exc_text and "kaboom-f41" in r.exc_text)
                   for r in scheduler_records), (
            "no logged record's message or traceback mentions the real "
            "exception text -- the failure was logged, but not usefully"
        )
