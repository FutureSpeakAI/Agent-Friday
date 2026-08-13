"""Hang watchdog (docs: toolcall-integrity-v5, follow-up after the
2026-08-12/08-13 silent-hang incidents — friday.log went completely dark for
hours on both occasions while the process stayed alive (0% CPU, no
Traceback/ERROR, no exit), and nothing was captured to explain why. This
detects the process becoming unresponsive while it's still running and
dumps every thread's stack trace, so the next hang writes its own case file
instead of leaving zero forensic trace.

Two independent firing mechanisms, layered for robustness:
  1. A lightweight heartbeat thread, monitored by a second thread that
     checks it's still ticking on schedule. Catches the common case — a
     blocked I/O call or a stuck lock — where at least these two threads
     stay schedulable (a thread blocked in a native/syscall wait releases
     the GIL, so sibling threads keep running normally).
  2. faulthandler.dump_traceback_later as a backstop, re-armed on every
     heartbeat. Its firing does not depend on any Python thread of ours
     being scheduled, so it can still dump even in the rarer case where
     every Python thread — including our own heartbeat/monitor — is wedged
     (a true GIL-level freeze).
"""
from __future__ import annotations

import faulthandler
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from agent_friday.core import FRIDAY_DIR

_log = logging.getLogger("friday.hang_watchdog")

DEFAULT_HEARTBEAT_INTERVAL_S = 15
DEFAULT_STALL_THRESHOLD_S = 90

LOGS_DIR = FRIDAY_DIR / "logs"


class HangWatchdog:
    def __init__(self, heartbeat_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
                 stall_threshold_s: float = DEFAULT_STALL_THRESHOLD_S,
                 on_dump=None, logs_dir: Path | None = None):
        """on_dump: optional callback(dump_path) fired after a dump is
        written — e.g. the tray's opt-in auto-restart-after-dump setting."""
        self.heartbeat_interval_s = heartbeat_interval_s
        self.stall_threshold_s = stall_threshold_s
        self.on_dump = on_dump
        self.logs_dir = logs_dir or LOGS_DIR
        self._last_beat = time.time()
        self._dumped_this_stall = False
        self._started = False
        self._stop = False

    def _sidecar_path(self, label: str) -> Path:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        return self.logs_dir / f"hang-dump-{label}-{ts}.log"

    def _dump(self, reason: str) -> Path | None:
        """Write the reason+timestamp header plus every thread's stack to a
        sidecar file AND to friday.log (via the friday.hang_watchdog
        logger). Never raises — a dump failure must not take down whatever
        is left of the process."""
        path = self._sidecar_path("primary")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("=== FRIDAY HANG WATCHDOG ===\n")
                fh.write(f"reason: {reason}\n")
                fh.write(f"timestamp: {datetime.now().isoformat()}\n")
                fh.write(f"stall_threshold_s: {self.stall_threshold_s}\n\n")
                fh.flush()
                faulthandler.dump_traceback(file=fh, all_threads=True)
        except Exception as e:
            _log.error("hang watchdog: failed to write dump sidecar file: %s", e)
            path = None
        _log.critical("HANG WATCHDOG FIRED: %s (dump: %s)", reason, path)
        if self.on_dump:
            try:
                self.on_dump(path)
            except Exception as e:
                _log.error("hang watchdog: on_dump callback failed: %s", e)
        return path

    def check_once(self) -> Path | None:
        """One monitor tick: dump if the heartbeat has gone stale since the
        last successful pet. Exposed directly (not just via the loop) so
        tests can simulate a stall deterministically without sleeping
        through a real threshold."""
        stale_for = time.time() - self._last_beat
        if stale_for > self.stall_threshold_s and not self._dumped_this_stall:
            self._dumped_this_stall = True
            return self._dump(f"heartbeat stalled for {stale_for:.0f}s "
                              f"(threshold {self.stall_threshold_s}s)")
        return None

    def pet(self) -> None:
        """Record a successful heartbeat — resets staleness tracking."""
        self._last_beat = time.time()
        self._dumped_this_stall = False

    def _heartbeat_loop(self):
        backstop_fh = open(self._sidecar_path("backstop"), "w", encoding="utf-8")
        backstop_fh.write("=== FRIDAY HANG WATCHDOG (faulthandler backstop) ===\n")
        backstop_fh.write(f"armed: {datetime.now().isoformat()}\n")
        backstop_fh.write(f"stall_threshold_s: {self.stall_threshold_s}\n")
        backstop_fh.write(
            "Silent unless the heartbeat/monitor threads themselves stop "
            "running (a true interpreter-level freeze) — in the normal case "
            "the hang-dump-primary-*.log file is what fires, with a real "
            "reason and timestamp. This file exists so a dump still happens "
            "even if that path is itself wedged.\n\n")
        backstop_fh.flush()
        try:
            faulthandler.dump_traceback_later(
                self.stall_threshold_s, repeat=True, exit=False, file=backstop_fh)
            while not self._stop:
                time.sleep(self.heartbeat_interval_s)
                self.pet()
                faulthandler.cancel_dump_traceback_later()
                faulthandler.dump_traceback_later(
                    self.stall_threshold_s, repeat=True, exit=False, file=backstop_fh)
        finally:
            try:
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
            try:
                backstop_fh.close()
            except Exception:
                pass

    def _monitor_loop(self):
        while not self._stop:
            time.sleep(self.heartbeat_interval_s)
            self.check_once()

    def start(self) -> None:
        """Idempotent — a second call is a no-op."""
        if self._started:
            return
        self._started = True
        try:
            faulthandler.enable()
        except Exception:
            pass
        threading.Thread(target=self._heartbeat_loop, name="hang-watchdog-heartbeat",
                         daemon=True).start()
        threading.Thread(target=self._monitor_loop, name="hang-watchdog-monitor",
                         daemon=True).start()
        _log.info("hang watchdog armed (heartbeat=%ss, stall_threshold=%ss)",
                  self.heartbeat_interval_s, self.stall_threshold_s)


_default: HangWatchdog | None = None


def start(**kwargs) -> HangWatchdog:
    """Module-level convenience — the singleton server.py arms at boot."""
    global _default
    if _default is None:
        _default = HangWatchdog(**kwargs)
        _default.start()
    return _default


def get() -> HangWatchdog | None:
    return _default
