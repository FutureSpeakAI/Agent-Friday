"""Unit tests for services/hang_watchdog.py (docs: toolcall-integrity-v5,
follow-up after the 2026-08-12/08-13 silent-hang incidents).

Uses check_once()/pet() directly rather than real threads sleeping through
real thresholds, so a simulated stall is deterministic and fast — no test
waits through an actual 90s window.
"""
from __future__ import annotations

import faulthandler
import time

from agent_friday.services.hang_watchdog import HangWatchdog


class TestNoStallNoDump:
    def test_fresh_watchdog_does_not_dump(self, tmp_path):
        wd = HangWatchdog(stall_threshold_s=60, logs_dir=tmp_path)
        assert wd.check_once() is None
        assert list(tmp_path.glob("hang-dump-primary-*.log")) == []

    def test_a_recent_pet_prevents_a_dump(self, tmp_path):
        wd = HangWatchdog(stall_threshold_s=0.1, logs_dir=tmp_path)
        wd.pet()
        assert wd.check_once() is None


class TestSimulatedStall:
    def test_stale_heartbeat_triggers_a_dump(self, tmp_path):
        wd = HangWatchdog(stall_threshold_s=0.1, logs_dir=tmp_path)
        # Simulate a stall: force the last heartbeat far enough into the
        # past to exceed the threshold, without sleeping for real.
        wd._last_beat = time.time() - 10
        path = wd.check_once()
        assert path is not None
        assert path.exists()

    def test_dump_file_contains_reason_and_timestamp(self, tmp_path):
        wd = HangWatchdog(stall_threshold_s=0.1, logs_dir=tmp_path)
        wd._last_beat = time.time() - 10
        path = wd.check_once()
        content = path.read_text(encoding="utf-8")
        assert "reason:" in content
        assert "heartbeat stalled for" in content
        assert "timestamp:" in content
        assert "stall_threshold_s: 0.1" in content

    def test_dump_file_contains_real_thread_stacks(self, tmp_path):
        wd = HangWatchdog(stall_threshold_s=0.1, logs_dir=tmp_path)
        wd._last_beat = time.time() - 10
        path = wd.check_once()
        content = path.read_text(encoding="utf-8")
        # faulthandler.dump_traceback's own header line for the current thread.
        assert "Thread" in content or "Current thread" in content

    def test_only_dumps_once_per_stall_episode(self, tmp_path):
        wd = HangWatchdog(stall_threshold_s=0.1, logs_dir=tmp_path)
        wd._last_beat = time.time() - 10
        first = wd.check_once()
        second = wd.check_once()  # still stale, but already dumped this episode
        assert first is not None
        assert second is None

    def test_a_pet_after_a_dump_rearms_for_the_next_stall(self, tmp_path):
        wd = HangWatchdog(stall_threshold_s=0.1, logs_dir=tmp_path)
        wd._last_beat = time.time() - 10
        assert wd.check_once() is not None
        wd.pet()
        wd._last_beat = time.time() - 10  # a second, later stall
        assert wd.check_once() is not None


class TestOnDumpCallback:
    def test_on_dump_fires_with_the_dump_path(self, tmp_path):
        seen = []
        wd = HangWatchdog(stall_threshold_s=0.1, logs_dir=tmp_path,
                          on_dump=lambda p: seen.append(p))
        wd._last_beat = time.time() - 10
        wd.check_once()
        assert len(seen) == 1
        assert seen[0] is not None and seen[0].exists()

    def test_on_dump_never_prevents_the_watchdog_from_completing(self, tmp_path):
        # A broken callback must not raise out of check_once() — the
        # watchdog's own reliability can't depend on caller code.
        def _broken(path):
            raise RuntimeError("boom")
        wd = HangWatchdog(stall_threshold_s=0.1, logs_dir=tmp_path, on_dump=_broken)
        wd._last_beat = time.time() - 10
        path = wd.check_once()  # must not raise
        assert path is not None


class TestStartIsIdempotentAndBackgrounded:
    def test_start_launches_daemon_threads_and_is_safe_to_call_twice(self, tmp_path):
        wd = HangWatchdog(heartbeat_interval_s=0.05, stall_threshold_s=0.5,
                          logs_dir=tmp_path)
        try:
            wd.start()
            wd.start()  # idempotent — must not double-launch threads
            time.sleep(0.15)
            # A healthy, ticking watchdog must have petted at least once —
            # never fires a false-positive dump while genuinely alive.
            assert list(tmp_path.glob("hang-dump-primary-*.log")) == []
            # The faulthandler backstop file is created at arm time.
            assert list(tmp_path.glob("hang-dump-backstop-*.log"))
        finally:
            wd._stop = True
            try:
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
