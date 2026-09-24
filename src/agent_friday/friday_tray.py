"""FRIDAY Desktop — system tray app.

Spawns the Flask server (server.py) as a child process and exposes a Windows
system-tray icon with controls for opening the UI, restarting the server,
viewing the voice debug log, and quitting cleanly.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import pystray
from PIL import Image

from agent_friday.paths import clear_server_port, friday_home, server_port

# No console windows from anything this process spawns. The tray outlives the
# server — it is what starts Friday and what keeps running after Friday is
# closed — so console popups seen "when Friday is closed" come from
# here. Installed before anything can shell out. See services/no_console.py.
try:
    from agent_friday.services.no_console import install as _install_no_console
    _install_no_console()
except Exception:
    pass

# Same reasoning, one layer up. The tray is the ROOT of Friday's process tree,
# so setting HF_HUB_DISABLE_XET here is inherited by the server and by every
# child the server spawns - which matters because hf_xet aborts happen in
# child processes, not in the server itself. Arming faulthandler here
# additionally covers the tray, which outlives the server and would otherwise
# be the one process that could die with no record at all.
try:
    from agent_friday.services import crash_forensics as _crash
    _crash.disable_hf_xet()
    _crash.install()
except Exception:
    pass

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
VENV_PYTHON = PROJECT_DIR / "venv" / "Scripts" / "python.exe"
SERVER_SCRIPT = PROJECT_DIR / "server.py"
ICON_PATH = PROJECT_DIR / "assets" / "icons" / "futurespeak.png"
log = logging.getLogger(__name__)

VOICE_LOG = friday_home() / "voice_debug.log"
SERVER_STDERR_LOG = friday_home() / "server_stderr.log"


# The server's port comes from the same source the server uses (FRIDAY_PORT,
# else 3000), overridden by the port the running server recorded after
# binding, which differs when the requested port was busy. Resolved on every
# use, because the server may publish a new port after the tray starts.
def _port() -> int:
    return server_port()


def _server_url() -> str:
    return "http://localhost:%d" % _port()


def _health_url() -> str:
    return _server_url() + "/api/health"


def _meetings_status_url() -> str:
    return _server_url() + "/api/meetings/status"


TRAY_TITLE = "Agent Friday by FutureSpeak.AI"


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
            with urllib.request.urlopen(_health_url(), timeout=3.0) as r:
                if r.status < 500:
                    return True, "healthy"
        except Exception:
            time.sleep(0.5)
    if proc is not None and proc.poll() is not None:
        return False, (f"FAILED TO START (exit {proc.returncode}) - "
                       f"see {SERVER_STDERR_LOG.name}")
    return False, f"NOT RESPONDING after {timeout:.0f}s (process still alive)"


# ── Push-to-transcribe ──────────────────────────────────────────────────────
# The tray is where a system-wide hotkey belongs: it is the one Friday process
# that is always running and has a desktop session to send keystrokes into.
# The server owns the ear, so the tray records and asks it for the words.

class PushToTranscribe:
    """Holds the hotkey service and keeps it in step with the settings."""

    def __init__(self, server_url=None):
        # None follows the server's current port on every request.
        self._fixed_url = server_url
        self.service = None
        self.indicator = None
        self.detail = "not started"

    @property
    def server_url(self) -> str:
        return self._fixed_url or _server_url()

    # -- settings ---------------------------------------------------------
    def _settings(self) -> dict:
        try:
            import json
            import urllib.request
            with urllib.request.urlopen(self.server_url + "/api/settings",
                                        timeout=5) as r:
                body = json.load(r)
            return body.get("settings", body) or {}
        except Exception as e:
            log.info("push-to-transcribe could not read settings: %s", e)
            return {}

    # -- the ear lives in the server --------------------------------------
    def _transcribe(self, pcm: bytes) -> str:
        import json
        import urllib.request
        req = urllib.request.Request(
            self.server_url + "/api/voice/transcribe", data=pcm,
            method="POST",
            headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                detail = json.load(e).get("error") or e.reason
            except Exception:
                detail = str(e.reason)
            raise RuntimeError(detail) from None
        except Exception:
            raise RuntimeError(
                "Friday's server is not answering. Is it still starting up?"
            ) from None
        return body.get("text") or ""

    # -- lifecycle --------------------------------------------------------
    def apply(self) -> None:
        """Start, stop or rebind to match the current settings."""
        s = self._settings()
        want = bool(s.get("push_to_transcribe", True))
        hotkey = str(s.get("push_to_transcribe_hotkey") or "alt+t")
        hold_ms = int(s.get("push_to_transcribe_hold_ms") or 150)

        if not want:
            self.stop()
            self.detail = "off"
            return

        try:
            from agent_friday.services import push_to_talk as ptt
        except Exception as e:
            self.detail = "unavailable (%s)" % e
            log.info("push-to-transcribe unavailable: %s", e)
            return

        try:
            ptt.parse_hotkey(hotkey)
        except ptt.HotkeyError as e:
            # Refuse rather than bind something the user did not ask for.
            self.detail = "bad hotkey: %s" % e
            log.warning("push-to-transcribe %s", self.detail)
            self.stop()
            return

        if self.service is not None:
            try:
                self.service.set_hotkey(hotkey)
                self.service.hold_ms = hold_ms
                self.detail = ptt.describe_hotkey(hotkey)
            except Exception as e:
                self.detail = "could not rebind: %s" % e
            return

        if self.indicator is None:
            try:
                from agent_friday.services.ptt_indicator import Indicator
                self.indicator = Indicator()
            except Exception as e:
                log.info("push-to-transcribe indicator unavailable: %s", e)

        self.service = ptt.PushToTalk(
            hotkey=hotkey, hold_ms=hold_ms, transcribe=self._transcribe,
            indicator=self.indicator)
        if self.service.start():
            self.detail = ptt.describe_hotkey(hotkey)
            log.info("push-to-transcribe active: hold %s anywhere", self.detail)
        else:
            self.service = None
            self.detail = "could not install the keyboard hook"

    def stop(self) -> None:
        svc, self.service = self.service, None
        if svc:
            try:
                svc.stop()
            except Exception:
                pass

    def label(self) -> str:
        if self.service is not None:
            return "Push-to-Transcribe: hold %s" % self.detail
        return "Push-to-Transcribe: %s" % self.detail


class FridayTray:
    def __init__(self) -> None:
        self.server_proc: subprocess.Popen | None = None
        self._child_err = None
        self._last_failure: str | None = None
        self.running = False
        self.icon: pystray.Icon | None = None
        self._lock = threading.Lock()
        # Debounce guard. start_server()'s existing self._lock already
        # serializes in-process restarts — two concurrent restart_server()
        # calls collapse to one spawn. Two server processes born the same
        # second come from a SEPARATE, externally-launched process (a second
        # start.bat / manual `python server.py`), which no in-tray lock can
        # see — that is what server.py's own single-instance lock
        # (_acquire_single_instance_lock) guards against directly. This
        # debounce is defense-in-depth for a double-click regardless: cheap,
        # removes any doubt, costs nothing when idle.
        self._restart_in_flight = threading.Lock()
        self.ptt = PushToTranscribe()

    # ── Server lifecycle ──────────────────────────────────────────────
    def start_server(self) -> None:
        with self._lock:
            if self.server_proc and self.server_proc.poll() is None:
                return
            if _port_in_use(_port()):
                # Server already running externally — treat as healthy.
                self.running = True
                self._last_failure = None
                return
            # A port recorded by an earlier server no longer applies; the
            # child records the port it binds as soon as it has chosen one.
            clear_server_port()
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
        # Friday's own address (https://agent.<name>, services/local_address)
        # when it is proven to reach the same Friday as the loopback URL; the
        # loopback URL otherwise. Checked on a thread so a slow answer never
        # freezes the menu.
        def go():
            base = _server_url()
            url = base
            try:
                from agent_friday.services.local_address import open_url
                url = open_url(base)
            except Exception:
                pass
            webbrowser.open(url)
        threading.Thread(target=go, name="friday-open-ui", daemon=True).start()

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
            pystray.MenuItem(lambda _i: self.ptt.label(), None, enabled=False),
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
        """Poll every 5s and react to the server dying.

        Detecting a crash within the 5s cadence is not enough if the only
        reaction is relabeling the tray menu: nobody is looking at that
        menu, and the server can stay down for a long time before anyone
        notices. Hence the notification below.
        `self.running == True and alive == False` is specifically the
        crash shape: a deliberate stop (Quit / Restart) already sets
        `self.running = False` synchronously in `stop_server()` before this
        loop's next poll, so this branch does not fire for those. No
        auto-restart here on purpose — resurrecting a crashed process on a
        loop can mask a repeating fault, and whether Friday restarts
        herself is the user's call, not this watchdog's. A notification is
        not that call; it's just telling them.
        """
        while True:
            time.sleep(5)
            self._update_meeting_title()
            proc = self.server_proc
            alive = (proc is not None and proc.poll() is None) or _port_in_use(_port())
            if alive != self.running:
                crashed = self.running and not alive
                self.running = alive
                self._refresh_menu()
                if crashed and self.icon is not None:
                    try:
                        self.icon.notify(
                            "Friday's server stopped unexpectedly. "
                            "It has NOT been restarted automatically — "
                            "open the tray menu to restart it, or check "
                            "%s for what happened." % SERVER_STDERR_LOG,
                            "Friday Desktop",
                        )
                    except Exception:
                        pass

    def _meeting_status(self) -> dict:
        try:
            import json
            with urllib.request.urlopen(_meetings_status_url(), timeout=2.0) as r:
                return json.load(r) or {}
        except Exception:
            return {}

    def _update_meeting_title(self) -> None:
        """While a meeting is recording, the tray tooltip says so, with the time.

        The UI shows the same thing as a red dot; this is the one place that
        stays visible when every Friday window is closed.
        """
        if self.icon is None:
            return
        try:
            from agent_friday.services.meeting_capture import tray_tooltip
            tip = tray_tooltip(self._meeting_status()) if self.running else None
        except Exception:
            tip = None
        title = tip or TRAY_TITLE
        try:
            if self.icon.title != title:
                self.icon.title = title
        except Exception:
            pass

    def run(self) -> None:
        image = Image.open(ICON_PATH)
        self.icon = pystray.Icon(
            "friday_desktop",
            image,
            TRAY_TITLE,
            menu=self._build_menu(),
        )

        threading.Thread(target=self.start_server, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        threading.Thread(target=self._start_push_to_transcribe,
                         daemon=True).start()

        self.icon.run()

    def _start_push_to_transcribe(self) -> None:
        """Install the dictation hotkey once the ear is reachable.

        It waits for health rather than racing it: the hook would install
        fine, but the first hold would reach a server that is not listening
        yet, and "it did nothing the first time" is how a feature gets
        written off.
        """
        healthy, detail = _wait_for_health(timeout=120.0)
        if not healthy:
            self.ptt.detail = "the server never came up"
            log.info("push-to-transcribe: not installing the hotkey (%s)",
                     detail)
            return
        try:
            self.ptt.apply()
        except Exception as e:
            log.warning("push-to-transcribe failed to start: %s", e)
        self._refresh_menu()


#: Held for the life of the process. A module global rather than a local,
#: because a mutex handle that falls out of scope is a mutex that is released,
#: and a guard released at the end of main() guards nothing.
_INSTANCE_HANDLES: list = []


def _claim_windows_mutex() -> bool:
    """False only when another tray demonstrably holds the named mutex.

    Split out from `_acquire_single_instance` so tests can stub the one part
    of the guard that reaches out to the real operating system. Everything
    else - the socket, the bind, the failure path - stays exercised, which is
    what the existing tray tests assert on.

    True on any platform without this mechanism, and true on any error:
    failing to claim is not the same as finding it taken, and a guard that
    cannot run must not be the reason Friday will not start.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        ERROR_ALREADY_EXISTS = 183
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _k32.CreateMutexW.restype = wintypes.HANDLE
        _k32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL,
                                      wintypes.LPCWSTR]
        # Local\ scopes the name to this logon session, which is the boundary
        # we actually want: one tray per signed-in user.
        handle = _k32.CreateMutexW(None, True, r"Local\AgentFridayTray")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            return False
        if handle:
            _INSTANCE_HANDLES.append(handle)
    except Exception:
        pass
    return True


def _acquire_single_instance() -> bool:
    """True when this process is the only tray. False when another holds it.

    A socket-bind guard does not hold: two trays can start in the same second,
    each starting its own server.py - so Friday runs twice, and both copies'
    schedulers and health probes hit one single-slot llama-server. Four-token
    requests taking seven to twenty seconds in the seat log is what queueing
    behind another Friday looks like from the inside.

    A bare bind() is not a reliable mutex on Windows. Without
    SO_EXCLUSIVEADDRUSE the OS will let a second socket take the same loopback
    address under conditions that are easy to hit and hard to reproduce on
    purpose, and a guard that fails open is worse than none: it reads as
    protection in the source while the duplicate it was meant to stop runs
    anyway. A named kernel mutex has no such ambiguity - CreateMutexW either
    creates it or tells you it already existed.

    A FUNCTION RATHER THAN A BLOCK INSIDE main(), and that is not tidying.
    Inline, this made two OS-mode tests depend on whether a tray happened to
    be running on the machine executing them: they called main(), the real
    mutex was already held by the real tray, and main() exited before reaching
    what they were testing. A test that passes or fails on the state of the
    developer's desktop is worse than one that fails, because it will
    eventually pass for the wrong reason. Named and separate, it can be
    stubbed by the tests that are not about it.
    """
    if not _claim_windows_mutex():
        return False

    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        try:
            # SO_EXCLUSIVEADDRUSE (~SO_REUSEADDR as a signed int) is the
            # option that makes a Windows bind actually exclusive.
            guard.setsockopt(socket.SOL_SOCKET, ~socket.SO_REUSEADDR, 1)
        except Exception:
            pass
    try:
        guard.bind(("127.0.0.1", 51847))
    except OSError:
        return False
    _INSTANCE_HANDLES.append(guard)
    return True


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

    if not _acquire_single_instance():
        return

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
