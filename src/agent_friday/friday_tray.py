"""FRIDAY Desktop — system tray app.

Spawns the Flask server (server.py) as a child process and exposes a Windows
system-tray icon with controls for opening the UI, restarting the server,
viewing the voice debug log, and quitting cleanly.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import pystray
from PIL import Image

from agent_friday.paths import friday_home

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
VENV_PYTHON = PROJECT_DIR / "venv" / "Scripts" / "python.exe"
SERVER_SCRIPT = PROJECT_DIR / "server.py"
ICON_PATH = PROJECT_DIR / "assets" / "icons" / "futurespeak.png"
VOICE_LOG = friday_home() / "voice_debug.log"
SERVER_STDERR_LOG = friday_home() / "server_stderr.log"
SERVER_URL = "http://localhost:3000"
HEALTH_URL = f"{SERVER_URL}/api/health"
PORT = 3000
# Real cold start measured at ~143s (wiki merge, model discovery, embedding
# load, judgment probe battery). The previous 30s budget was structurally
# guaranteed to expire before a HEALTHY server finished booting, so the tray
# reported failure on every successful start and only recovered when the
# watchdog later noticed the port. 300s is headroom, not a guess.
SERVER_START_TIMEOUT_S = 300.0

CREATE_NO_WINDOW = 0x08000000  # Windows: suppress child console

_OS_MODE_TRUTHY = {"1", "true", "yes", "on"}


def _os_mode_active() -> bool:
    """True when FRIDAY_OS_MODE is on.

    Deliberately duplicated from (not imported from) agent_friday.core.
    os_mode.is_os_mode(): that module lives inside the `agent_friday.core`
    PACKAGE, and importing any name from a submodule of a package forces
    Python to execute that package's __init__.py first. For
    agent_friday.core that means the ~2600-line Flask app bootstrap and a
    legacy `~/wiki` migration that touches the REAL home directory
    regardless of FRIDAY_HOME (see agent_friday/paths.py's module docstring
    for the full history of this hazard, from PR-1 of this OS-mode
    sequence). This tray entry point exists specifically to decide, cheaply
    and before anything heavy runs, whether to run anything at all — which
    is impossible if making that decision first requires running the heavy
    thing. Every other consumer of is_os_mode() in this PR already imports
    (or already forces the import of) agent_friday.core for unrelated
    reasons, so this three-line duplication is confined to the one call site
    that cannot afford the shared import.
    """
    return os.environ.get("FRIDAY_OS_MODE", "").strip().lower() in _OS_MODE_TRUTHY


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_health(timeout: float = SERVER_START_TIMEOUT_S,
                     proc: subprocess.Popen | None = None) -> tuple[bool, str]:
    """Wait for the server to answer /api/health.

    Returns (healthy, detail). The distinction that matters: a server that
    DIED and a server that is merely slow both used to look like one silent
    timeout. Polling proc.poll() separates them - a dead child is reported
    immediately with its exit code instead of burning the full budget.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False, (f"FAILED TO START (exit {proc.returncode}) - "
                           f"see {SERVER_STDERR_LOG.name}")
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3.0) as r:
                if r.status < 500:
                    return True, "healthy"
        except Exception:
            time.sleep(0.5)
    if proc is not None and proc.poll() is not None:
        return False, (f"FAILED TO START (exit {proc.returncode}) - "
                       f"see {SERVER_STDERR_LOG.name}")
    return False, f"NOT RESPONDING after {timeout:.0f}s (process still alive)"


class FridayTray:
    def __init__(self) -> None:
        self.server_proc: subprocess.Popen | None = None
        self._child_err = None
        self._last_failure: str | None = None
        self.running = False
        self.icon: pystray.Icon | None = None
        self._lock = threading.Lock()
        # Debounce guard (toolcall-integrity-v5, 2026-08-13 double-launch
        # incident audit): read through start_server()'s existing self._lock
        # and could not find an in-process race it fails to serialize — two
        # concurrent restart_server() calls should already collapse to one
        # spawn. The most likely explanation for two server processes born
        # the same second is a SEPARATE, externally-launched process (a
        # second start.bat / manual `python server.py`), which no in-tray
        # lock can see — that's what server.py's own single-instance lock
        # (_acquire_single_instance_lock) now guards against directly. This
        # debounce is defense-in-depth for a double-click regardless: cheap,
        # removes any doubt, costs nothing when idle.
        self._restart_in_flight = threading.Lock()

    # ── Server lifecycle ──────────────────────────────────────────────
    def start_server(self) -> None:
        with self._lock:
            if self.server_proc and self.server_proc.poll() is None:
                return
            if _port_in_use(PORT):
                # Server already running externally — treat as healthy.
                self.running = True
                self._last_failure = None
                return
            python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
            # Child stdout+stderr are appended to a file, never discarded: a
            # server that dies during import (before its own file logging is
            # up) has nowhere else to leave a traceback. DEVNULL here cost us
            # seven invisible failures.
            err_path = SERVER_STDERR_LOG
            err_path.parent.mkdir(parents=True, exist_ok=True)
            self._child_err = open(err_path, "ab", buffering=0)
            self._child_err.write(
                b"\n===== server start "
                + time.strftime("%Y-%m-%dT%H:%M:%S").encode()
                + b" =====\n"
            )
            self.server_proc = subprocess.Popen(
                [python_exe, str(SERVER_SCRIPT)],
                cwd=str(PROJECT_DIR),
                creationflags=CREATE_NO_WINDOW,
                stdout=self._child_err,
                stderr=subprocess.STDOUT,
            )
        healthy, detail = _wait_for_health(proc=self.server_proc)
        self.running = healthy
        self._last_failure = None if healthy else detail
        self._refresh_menu()

    def stop_server(self) -> None:
        with self._lock:
            proc = self.server_proc
            self.server_proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        if self._child_err is not None:
            try:
                self._child_err.close()
            except Exception:
                pass
            self._child_err = None
        self.running = False

    def restart_server(self) -> None:
        if not self._restart_in_flight.acquire(blocking=False):
            # A restart is already running (e.g. a double-click) — the
            # in-flight one will finish the job; don't run a second
            # stop→sleep→start sequence concurrently with it.
            return
        try:
            self.stop_server()
            # Give the OS a moment to release the port.
            time.sleep(0.5)
            self.start_server()
        finally:
            self._restart_in_flight.release()

    # ── Menu actions ──────────────────────────────────────────────────
    def _open_ui(self, _icon, _item) -> None:
        webbrowser.open(SERVER_URL)

    def _restart(self, _icon, _item) -> None:
        threading.Thread(target=self.restart_server, daemon=True).start()

    def _open_voice_log(self, _icon, _item) -> None:
        if VOICE_LOG.exists():
            os.startfile(str(VOICE_LOG))  # type: ignore[attr-defined]
        else:
            os.startfile(str(VOICE_LOG.parent))  # type: ignore[attr-defined]

    def _quit(self, _icon, _item) -> None:
        self.stop_server()
        if self.icon:
            self.icon.stop()

    # ── Menu / icon ───────────────────────────────────────────────────
    def _status_label(self, _item=None) -> str:
        """A tray label that cannot lie about which of three states we are in.

        Running / explicitly failed / merely stopped were previously collapsed
        into two, so a crashed server was indistinguishable from a quit one.
        """
        if self.running:
            return "Server Status: Running"
        if self._last_failure:
            detail = self._last_failure
            if len(detail) > 60:
                detail = detail[:57] + "..."
            return f"Server Status: {detail}"
        return "Server Status: Stopped"

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Open Friday Desktop", self._open_ui, default=True),
            pystray.MenuItem("Restart Server", self._restart),
            pystray.MenuItem("Voice Debug Log", self._open_voice_log),
            pystray.MenuItem(self._status_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    def _refresh_menu(self) -> None:
        if self.icon:
            self.icon.menu = self._build_menu()
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def _watchdog(self) -> None:
        while True:
            time.sleep(5)
            proc = self.server_proc
            alive = (proc is not None and proc.poll() is None) or _port_in_use(PORT)
            if alive != self.running:
                self.running = alive
                self._refresh_menu()

    def run(self) -> None:
        image = Image.open(ICON_PATH)
        self.icon = pystray.Icon(
            "friday_desktop",
            image,
            "Agent Friday by FutureSpeak.AI — Running on port 3000",
            menu=self._build_menu(),
        )

        threading.Thread(target=self.start_server, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

        self.icon.run()


def main() -> None:
    # Kiosk image (FRIDAY_OS_MODE=1): there is no desktop to put a tray icon
    # on — see core/os_mode.py. The sealed Linux image starts server.py
    # directly (systemd unit / `friday` CLI), never this tray, so skipping
    # here rather than in a caller is the one place that actually gates every
    # way this entry point could be invoked.
    if _os_mode_active():
        print("[FRIDAY] FRIDAY_OS_MODE is on — skipping the system tray "
              "(no desktop to put it on in kiosk mode).")
        return

    # Single-instance guard: bind a loopback port to ensure only one tray runs.
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1", 51847))
    except OSError:
        # Another tray instance is already running.
        sys.exit(0)

    def _on_signal(_sig, _frm):
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    except Exception:
        pass

    FridayTray().run()


if __name__ == "__main__":
    main()
