"""Hang watchdog (docs: toolcall-integrity-v5). A silent hang — friday.log
going completely dark for hours while the process stays alive (0% CPU, no
Traceback/ERROR, no exit) — leaves nothing to explain why. This detects the
process becoming unresponsive while it's still running and dumps every
thread's stack trace, so a hang writes its own case file instead of leaving
zero forensic trace.

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


def _pool_stats():
    try:
        from agent_friday.services import pooled_server
        return pooled_server.stats()
    except Exception:
        return None


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

    def status(self) -> dict:
        """Queryable stall state, so callers can READ the hang signal instead
        of reinventing it with a stopwatch.

        The chat UI's fifteen-minute release was the reinvention: a wall-clock
        number standing in for this. A long agent turn and a wedged interpreter
        look identical to a clock and completely different here.

        Note the honest limit: if the interpreter really is frozen, an HTTP
        caller never gets this answer at all — and that silence is itself the
        signal. ``stalled`` catches the case where the monitor thread still
        runs and the heartbeat does not.
        """
        now = time.time()
        stale_for = now - self._last_beat
        return {
            "armed": self._started,
            "last_beat": self._last_beat,
            "stale_for_s": round(stale_for, 1),
            "threshold_s": self.stall_threshold_s,
            "stalled": bool(self._started and stale_for > self.stall_threshold_s),
            "dumped_this_stall": self._dumped_this_stall,
            "os_threads": os_thread_count(),
            "python_threads": threading.active_count(),
            "pool": _pool_stats(),
        }

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


def status() -> dict:
    """Module-level convenience. An unarmed watchdog reports itself as such
    rather than as healthy: "we are not looking" is not "nothing is wrong"."""
    wd = _default
    if wd is None:
        return {"armed": False, "stalled": False, "reason": "watchdog not armed"}
    return wd.status()


# ── Accept-path probe ───────────────────────────────────────────────────────
# The heartbeat above proves the interpreter is scheduling threads. It says
# nothing about whether the server still ANSWERS: a listener can stay in LISTEN
# with its accept loop wedged while every other thread runs happily. The only
# honest test is to be a client: connect to our own port and complete a tiny
# HTTP request. The probe thread is started once at arm time and only ever
# sleeps, connects and reads, so it does not depend on creating a thread at
# the moment the server is unwell.

EXIT_WEDGED = 75  # the tray restarts a server that leaves with this code

PROBE_INTERVAL_S = 10.0
PROBE_TIMEOUT_S = 5.0
PROBE_FAILURES_TO_HEAL = 6
MAX_HEALS_PER_HOUR = 3


def probe_once(host: str, port: int, timeout: float, tls: bool = False,
               path: str = "/api/health") -> tuple[bool, str]:
    """True when a complete HTTP status line came back within `timeout`.
    Any status counts (a 503 is a server that is answering)."""
    import socket
    deadline = time.monotonic() + timeout
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return False, f"connect failed: {e}"
    try:
        s = raw
        if tls:
            import ssl
            ctx = ssl.create_default_context()
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            s = ctx.wrap_socket(raw, server_hostname=host)
        s.settimeout(max(0.1, deadline - time.monotonic()))
        s.sendall(f"GET {path} HTTP/1.0\r\nHost: {host}\r\n"
                  f"Connection: close\r\n\r\n".encode())
        head = b""
        while b"\r\n" not in head and len(head) < 512:
            chunk = s.recv(128)
            if not chunk:
                break
            head += chunk
        if head.startswith(b"HTTP/"):
            return True, head.split(b"\r\n", 1)[0].decode("latin-1")
        return False, "no HTTP status line"
    except OSError as e:
        return False, f"no answer: {e}"
    finally:
        try:
            raw.close()
        except Exception:
            pass


def capture_forensics(logs_dir: Path, port: int, reason: str) -> Path | None:
    """Everything worth knowing about a process that cannot serve: native and
    Python stacks (py-spy, when installed), OS thread count, and the port
    table. Never raises."""
    import os
    import shutil
    import subprocess
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / f"wedge-forensics-{ts}.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"reason: {reason}\ntimestamp: {datetime.now().isoformat()}\n"
                     f"pid: {os.getpid()}\n\n")
            fh.write(f"os_threads: {os_thread_count()}\n")
            fh.write(f"python_threads: {threading.active_count()}\n\n")
            try:
                import psutil
                fh.write("== port table ==\n")
                for c in psutil.net_connections(kind="tcp"):
                    if c.laddr and c.laddr.port == port:
                        fh.write(f"{c.laddr} -> {c.raddr or '-'} {c.status} pid={c.pid}\n")
            except Exception as e:
                fh.write(f"port table unavailable: {e}\n")
            try:
                from agent_friday.services import pooled_server
                fh.write(f"\n== pool ==\n{pooled_server.stats()}\n")
            except Exception:
                pass
            fh.write("\n== python stacks ==\n")
            fh.flush()
            faulthandler.dump_traceback(file=fh, all_threads=True)
            spy = shutil.which("py-spy")
            fh.write("\n== py-spy dump --native ==\n")
            fh.flush()
            if spy:
                try:
                    out = subprocess.run(
                        [spy, "dump", "--native", "--pid", str(os.getpid())],
                        capture_output=True, text=True, timeout=25)
                    fh.write(out.stdout + out.stderr)
                except Exception as e:
                    fh.write(f"py-spy failed: {e}\n")
            else:
                fh.write("py-spy is not installed; install it to capture native stacks\n")
        return path
    except Exception as e:
        _log.error("wedge forensics failed: %s", e)
        return None


def os_thread_count() -> int | None:
    """OS threads in this process, which is not threading.active_count():
    native pools (torch, onnx, ctranslate2) create threads Python never sees."""
    try:
        import psutil
        return psutil.Process().num_threads()
    except Exception:
        return None


class AcceptProbe:
    """Detects 'listening but not answering' and heals it, at most a bounded
    number of times per hour, with a receipt each time.

    A miss needs BOTH: the probe gets no answer, AND the server completed no
    request since the previous miss (`progress_fn`). A busy server that is
    slow but working is not restarted."""

    def __init__(self, port: int, *, host: str = "127.0.0.1", tls: bool = False,
                 interval_s: float = PROBE_INTERVAL_S,
                 timeout_s: float = PROBE_TIMEOUT_S,
                 failures_to_heal: int = PROBE_FAILURES_TO_HEAL,
                 progress_fn=None, heal=None, notify=None,
                 logs_dir: Path | None = None, probe=None,
                 forensics=None, max_heals_per_hour: int = MAX_HEALS_PER_HOUR):
        self.port, self.host, self.tls = port, host, tls
        self.interval_s, self.timeout_s = interval_s, timeout_s
        self.failures_to_heal = failures_to_heal
        self.progress_fn = progress_fn
        self.logs_dir = logs_dir or LOGS_DIR
        self.notify = notify
        self._probe = probe or (lambda: probe_once(host, port, timeout_s, tls))
        self._forensics = forensics or capture_forensics
        self._heal = heal or self._exit_for_restart
        self.max_heals_per_hour = max_heals_per_hour
        self.failures = 0
        self.heals = 0
        self._heal_times: list[float] = []
        self._last_progress = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _exit_for_restart(_receipt):
        import os
        os._exit(EXIT_WEDGED)

    def check_once(self) -> bool:
        """One probe. Returns True when a heal was triggered."""
        ok, detail = self._probe()
        progress = self.progress_fn() if self.progress_fn else None
        if ok:
            self.failures = 0
            self._last_progress = progress
            return False
        if progress is not None and self._last_progress is not None \
                and progress != self._last_progress:
            # Requests are still completing: slow, not wedged.
            self.failures = 0
            self._last_progress = progress
            return False
        self._last_progress = progress
        self.failures += 1
        _log.warning("accept probe failed (%d/%d): %s", self.failures,
                     self.failures_to_heal, detail)
        if self.failures < self.failures_to_heal:
            return False
        return self._trigger(detail)

    def _trigger(self, detail: str) -> bool:
        import json
        now = time.time()
        self._heal_times = [t for t in self._heal_times if now - t < 3600]
        if len(self._heal_times) >= self.max_heals_per_hour:
            _log.critical("accept probe: server wedged again but %d heals in the "
                          "last hour already; leaving it for the owner",
                          len(self._heal_times))
            self.failures = 0
            return False
        self._heal_times.append(now)
        self.heals += 1
        reason = (f"port {self.port} did not answer {self.failures} consecutive "
                  f"probes over ~{self.failures * self.interval_s:.0f}s ({detail})")
        forensics = self._forensics(self.logs_dir, self.port, reason)
        receipt = {
            "kind": "wedged-server-heal",
            "at": datetime.now().isoformat(),
            "port": self.port,
            "reason": reason,
            "forensics": str(forensics) if forensics else None,
            "action": f"server exits with code {EXIT_WEDGED}; the tray restarts it",
            "llama_server": "left running; the restarted server adopts it",
        }
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            rp = self.logs_dir / f"wedge-heal-{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
            rp.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            receipt["receipt_path"] = str(rp)
        except Exception as e:
            _log.error("could not write heal receipt: %s", e)
        _log.critical("SERVER WEDGED: %s. Restarting through the tray.", reason)
        if self.notify:
            try:
                self.notify("Friday stopped answering, so she is restarting. "
                            "Nothing was lost; the details are in the logs folder.")
            except Exception:
                pass
        self.failures = 0
        try:
            self._heal(receipt)
        except Exception as e:
            _log.error("heal action failed: %s", e)
        return True

    def _loop(self):
        while not self._stop.wait(self.interval_s):
            try:
                self.check_once()
            except Exception as e:
                _log.error("accept probe error: %s", e)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="accept-probe",
                                        daemon=True)
        self._thread.start()
        _log.info("accept probe armed (port %s, every %ss, heal after %d misses)",
                  self.port, self.interval_s, self.failures_to_heal)

    def stop(self) -> None:
        self._stop.set()
