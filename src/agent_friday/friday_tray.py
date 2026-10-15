"""FRIDAY Desktop — system tray app.

Spawns the Flask server (server.py) as a child process and exposes a Windows
system-tray icon with controls for opening the UI, restarting the server,
viewing the voice debug log, and quitting cleanly.
"""

from __future__ import annotations

import json
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

from agent_friday import brand
from agent_friday.services import notice_registry as _notice_registry
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
# server_stderr.log is capped: past this size it rotates into numbered
# backups (services/capped_log), checked at every server start and on every
# watchdog tick while the server runs.
SERVER_STDERR_LOG_MAX_BYTES = 50 * 1024 * 1024
SERVER_STDERR_LOG_BACKUPS = 3


def _rotate_server_log() -> None:
    try:
        from agent_friday.services import capped_log
        capped_log.rotate_if_over(SERVER_STDERR_LOG, SERVER_STDERR_LOG_MAX_BYTES,
                                  SERVER_STDERR_LOG_BACKUPS)
    except Exception as e:
        log.info("server log rotation skipped: %s", e)


# The server's port comes from the same source the server uses (FRIDAY_PORT,
# else 3000), overridden by the port the running server recorded after
# binding, which differs when the requested port was busy. Resolved on every
# use, because the server may publish a new port after the tray starts.
# Exit code services/hang_watchdog.EXIT_WEDGED: the server could not answer its
# own port and left so the tray can restart it.
WEDGE_EXIT_CODE = 75
WEDGE_HEALS_PER_HOUR = 3


def _is_wedge_exit(returncode) -> bool:
    return returncode == WEDGE_EXIT_CODE


def _port() -> int:
    return server_port()


def _server_url() -> str:
    return "http://localhost:%d" % _port()


def _health_url() -> str:
    return _server_url() + "/api/health"


def _meetings_status_url() -> str:
    return _server_url() + "/api/meetings/status"


def _local_queue_url() -> str:
    return _server_url() + "/api/local-queue"


TRAY_TITLE = brand.PRODUCT_LOCKUP


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


# ── --open: what the Desktop / Start menu icon runs ─────────────────────────
# `Agent Friday (tray).cmd` starts this module with --open. The sign-in start
# (`Agent Friday (background).cmd`) does not pass it and behaves as before.

#: Under the "friday" logger tree so the lines land in friday.log next to the
#: server's own. The tray has no other file log (pythonw has no console).
_open_log = logging.getLogger("friday.tray.open")

#: Windows: a new console for the child (subprocess.CREATE_NEW_CONSOLE).
CREATE_NEW_CONSOLE = 0x00000010


class _AppendAndCloseHandler(logging.FileHandler):
    """Append one record and let go of the file.

    The server rotates friday.log by renaming it, and Windows refuses to
    rename a file another process holds open. The tray outlives the server, so
    a handle kept open here would stop the rotation for the life of the tray.
    """

    def emit(self, record):
        try:
            super().emit(record)
        finally:
            self.close()


def _attach_friday_log() -> None:
    root = logging.getLogger("friday.tray")
    if any(isinstance(h, _AppendAndCloseHandler) for h in root.handlers):
        return
    try:
        home = friday_home()
        home.mkdir(parents=True, exist_ok=True)
        fh = _AppendAndCloseHandler(home / "friday.log", encoding="utf-8",
                                    delay=True)
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S"))
        root.addHandler(fh)
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
    except Exception:
        pass  # a log that cannot be opened must not stop Friday starting


def _ui_url() -> str:
    """Friday's own address (https://agent.<name>, services/local_address)
    when it is proven to reach the same Friday as the loopback URL; the
    loopback URL otherwise."""
    base = _server_url()
    try:
        from agent_friday.services.local_address import open_url
        return open_url(base)
    except Exception:
        return base


def _first_run_pending() -> bool:
    """True when setup has not been completed on this computer.

    The console launcher's own test (cli._is_existing_user), so the two
    launchers can never disagree about what counts as set up. If that cannot
    be loaded, the setup marker alone decides - erring towards the console,
    which is never harmful: it starts Friday and opens the browser too.
    """
    try:
        from agent_friday.cli import _is_existing_user
        return not _is_existing_user()
    except Exception as e:
        _open_log.warning("tray --open: could not load the setup check from "
                          "the console launcher (%s); using the setup marker "
                          "alone", e)
        return not (friday_home() / ".setup_complete").exists()


def _setup_already_open() -> bool:
    """True when a console setup is open now (agent_friday.setup_lock)."""
    try:
        from agent_friday import setup_lock
        return setup_lock.is_held()
    except Exception as e:
        _open_log.warning("tray --open: could not read the setup lock (%s); "
                          "treating setup as not open", e)
        return False


def _hand_over_to_console() -> bool:
    """Start the console launcher, which runs the setup wizard. True if started.

    Installed: `<install root>\\Agent Friday.cmd`, the same file the
    post-install "Start Agent Friday now" box runs. A source checkout has no
    such file: `python -m agent_friday.cli` in a new console instead.
    """
    launcher = PROJECT_DIR.parent / "Agent Friday.cmd"  # brand: plain (a file name)
    try:
        if sys.platform == "win32" and launcher.exists():
            os.startfile(str(launcher))  # type: ignore[attr-defined]
            _open_log.info("tray --open: started %s for first-run setup", launcher)
            return True
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
            exe = exe.with_name("python.exe")  # pythonw cannot show the wizard
        flags = CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
        subprocess.Popen([str(exe), "-m", "agent_friday.cli"],
                         cwd=str(PROJECT_DIR), creationflags=flags)
        _open_log.info("tray --open: started '%s -m agent_friday.cli' for "
                       "first-run setup", exe)
        return True
    except Exception as e:
        _open_log.warning("tray --open: could not start the console launcher "
                          "for first-run setup (%s); starting the tray anyway",
                          e)
        return False


def _seed_skills() -> None:
    """Copy the bundled skills into ~/.friday/skills if that folder is absent.

    The console launcher does this on every start (cli.cmd_start); the tray
    icon must not be the one way in that skips it. A no-op once seeded.
    """
    try:
        from agent_friday.seed import ensure_seed_skills_installed
        ensure_seed_skills_installed()
    except Exception as e:
        _open_log.warning("tray --open: bundled skills were not seeded (%s)", e)


def _open_running_ui() -> None:
    """Another tray owns Friday: open her page instead of exiting silently.

    Waits for /api/health first (a sign-in start may still be booting). If it
    never answers, the page is opened anyway: this process has no tray icon
    to say so on, and a browser tab that cannot connect is a visible answer
    where a silent exit was none. The reason is in friday.log.
    """
    healthy, detail = _wait_for_health()
    url = _ui_url() if healthy else _server_url()
    if healthy:
        _open_log.info("tray --open: Friday is already running in the tray; "
                       "opening %s", url)
    else:
        _open_log.warning("tray --open: Friday is already running in the tray "
                          "but did not answer (%s); opening %s anyway", detail,
                          url)
    webbrowser.open(url)


# ── Push-to-transcribe ──────────────────────────────────────────────────────
# The tray is where a system-wide hotkey belongs: it is the one Friday process
# that is always running and has a desktop session to send keystrokes into.
# The server owns the ear, so the tray records and asks it for the words.

# A registration that fails is tried again after these intervals, doubling,
# for as long as the tray runs. There is no last attempt.
PTT_RETRY_FIRST_S = 2.0
PTT_RETRY_MAX_S = 60.0

class PushToTranscribe:
    """Holds the hotkey service and keeps it in step with the settings."""

    # What apply() leaves behind. ACTIVE and OFF are what the settings asked
    # for; RETRY and BLOCKED are not, and differ in whether trying again
    # unchanged could help (BLOCKED needs a different setting or install).
    ACTIVE, OFF, RETRY, BLOCKED = "active", "off", "retry", "blocked"

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
        """The settings, from the server, else from settings.json itself.

        The hotkey is registered while the server may still be starting, and
        a cold start takes minutes. Reading the file the server reads (as it
        reads it: utf-8-sig, since a BOM is a parse error) means a hotkey the
        user turned off or rebound is honoured from the first second, not
        replaced by the default until the server answers.
        """
        import json
        try:
            with urllib.request.urlopen(self.server_url + "/api/settings",
                                        timeout=5) as r:
                body = json.load(r)
            return body.get("settings", body) or {}
        except Exception as e:
            log.info("push-to-transcribe could not read settings from the "
                     "server: %s", e)
        try:
            path = friday_home() / "settings.json"
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            log.info("push-to-transcribe could not read settings.json: %s", e)
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
    def apply(self) -> str:
        """Start, stop or rebind to match the current settings.

        Returns ACTIVE, OFF, RETRY or BLOCKED; `detail` says why in words.
        """
        s = self._settings()
        want = bool(s.get("push_to_transcribe", True))
        hotkey = str(s.get("push_to_transcribe_hotkey") or "alt+t")
        hold_ms = int(s.get("push_to_transcribe_hold_ms") or 150)

        if not want:
            self.stop()
            self.detail = "off"
            return self.OFF

        try:
            from agent_friday.services import push_to_talk as ptt
        except Exception as e:
            self.detail = "unavailable (%s)" % e
            log.info("push-to-transcribe unavailable: %s", e)
            return self.BLOCKED

        try:
            ptt.parse_hotkey(hotkey)
        except ptt.HotkeyError as e:
            # Refuse rather than bind something the user did not ask for.
            self.detail = "bad hotkey: %s" % e
            log.warning("push-to-transcribe %s", self.detail)
            self.stop()
            return self.BLOCKED

        if self.service is not None:
            try:
                self.service.set_hotkey(hotkey)
                self.service.hold_ms = hold_ms
                self.detail = ptt.describe_hotkey(hotkey)
            except Exception as e:
                self.detail = "could not rebind: %s" % e
                return self.RETRY
            return self.ACTIVE

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
            return self.ACTIVE
        self.service = None
        self.detail = "could not install the keyboard hook"
        return self.RETRY

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


def recording_icon(base: Image.Image) -> Image.Image:
    """The tray icon with a red recording dot (white ring) in its lower right
    corner, drawn over the normal icon. A tooltip alone is not an indicator;
    this shows without hovering."""
    from PIL import ImageDraw
    img = base.convert("RGBA").copy()
    w, h = img.size
    r = max(4, min(w, h) // 4)
    x1, y1 = w - 1, h - 1
    x0, y0 = x1 - 2 * r, y1 - 2 * r
    d = ImageDraw.Draw(img)
    d.ellipse((x0 - 2, y0 - 2, x1 + 1, y1 + 1), fill=(255, 255, 255, 255))
    d.ellipse((x0, y0, x1 - 1, y1 - 1), fill=(220, 38, 38, 255))
    return img


class FridayTray:
    #: Set by main() for `--open` (the Desktop / Start menu icon): open the UI
    #: in the browser once the server is healthy. Off for the sign-in start.
    open_on_ready = False

    def __init__(self) -> None:
        self.server_proc: subprocess.Popen | None = None
        self._child_err = None
        # True while the server's log handle is append-only at the OS level:
        # only then may the live log be truncated under a running server.
        self._log_rotatable = False
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
            # seven invisible failures. The handle is append-only at the OS
            # level so the live file can be rotated while the server holds it.
            err_path = SERVER_STDERR_LOG
            err_path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_server_log()
            try:
                from agent_friday.services.capped_log import open_shared_append
                self._child_err = open_shared_append(err_path)
                self._log_rotatable = True
            except Exception:
                self._child_err = open(err_path, "ab", buffering=0)
                self._log_rotatable = False
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
        self._log_rotatable = False
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
        # Checked on a thread so a slow answer never freezes the menu.
        threading.Thread(target=lambda: webbrowser.open(_ui_url()),
                         name="friday-open-ui", daemon=True).start()

    def _open_when_ready(self) -> None:
        """--open: show Friday in the browser once, when she answers.

        A server that never became healthy is said out loud on the icon
        rather than answered with a browser tab that cannot connect.
        """
        healthy, detail = _wait_for_health()
        if healthy:
            url = _ui_url()
            _open_log.info("tray --open: Friday answered; opening %s", url)
            webbrowser.open(url)
            return
        _open_log.warning("tray --open: not opening the browser; the server "
                          "did not become healthy (%s)", detail)
        self._notify("Friday did not start (%s). Use Restart in the tray "
                     "menu." % detail)

    def _restart(self, _icon, _item) -> None:
        threading.Thread(target=self.restart_server, daemon=True).start()

    def _open_voice_log(self, _icon, _item) -> None:
        if VOICE_LOG.exists():
            os.startfile(str(VOICE_LOG))  # type: ignore[attr-defined]
        else:
            os.startfile(str(VOICE_LOG.parent))  # type: ignore[attr-defined]

    def _release_seats(self, reason: str) -> None:
        """Ask the server to evict its model seats before we stop it.

        It has to be asked, and asked FIRST. The seats are separate
        llama-server processes, so none of them dies with the server; and
        `stop_server` terminates it, which on Windows runs no atexit handler,
        so the server cannot do this on its own way out. Best-effort and
        short: a quit is not allowed to hang on a seat that will not go.
        """
        try:
            req = urllib.request.Request(
                self.server_url + "/api/residency/release-for-quit",
                data=json.dumps({"reason": reason}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=75) as r:
                r.read()
        except Exception as e:
            # Nothing to do about it here, and it must not stop the quit; the
            # server's own log carries the detail.
            log.warning("could not release the model seats on quit: %s: %s",
                        type(e).__name__, e)

    def _quit(self, _icon, _item) -> None:
        # A quit gives the machine its memory back. A restart does not: see
        # `restart_server`, which is a planned restart and keeps the seats.
        self._release_seats("tray_quit")
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
            pystray.MenuItem("Open " + brand.PRODUCT_NAME, self._open_ui, default=True),
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
        not that call; it's just telling them. The one exception is a server
        that exits with WEDGE_EXIT_CODE: it was alive but could not answer, it
        wrote a receipt, and the restart is capped per hour.
        """
        while True:
            time.sleep(5)
            if self._log_rotatable:
                _rotate_server_log()
            self._update_meeting_title()
            proc = self.server_proc
            alive = (proc is not None and proc.poll() is None) or _port_in_use(_port())
            if alive != self.running:
                crashed = self.running and not alive
                self.running = alive
                self._refresh_menu()
                if crashed and proc is not None and _is_wedge_exit(proc.returncode):
                    # The server proved it could not answer and left on
                    # purpose, with a receipt (services/hang_watchdog
                    # AcceptProbe). That is a wedged server, not a dead one:
                    # restarting it is the documented path, not a resurrection.
                    self._heal_wedged(proc.returncode)
                    continue
                if crashed and self.icon is not None:
                    try:
                        _notice_registry.tray_balloon(
                            "E66", self.icon,
                            "Friday's server stopped unexpectedly. "
                            "It has NOT been restarted automatically — "
                            "open the tray menu to restart it, or check "
                            "%s for what happened." % SERVER_STDERR_LOG,
                            brand.PRODUCT_NAME,
                        )
                    except Exception:
                        pass

    def _heal_wedged(self, code) -> None:
        """Restart after a wedge exit, at most WEDGE_HEALS_PER_HOUR times an
        hour: a server that wedges repeatedly is a fault to look at, and the
        tray then says so instead of looping."""
        now = time.time()
        self._wedge_heals = [t for t in getattr(self, "_wedge_heals", [])
                             if now - t < 3600]
        if len(self._wedge_heals) >= WEDGE_HEALS_PER_HOUR:
            msg = ("Friday's server has stopped answering %d times this hour. "
                   "I have stopped restarting it; check %s."
                   % (len(self._wedge_heals), SERVER_STDERR_LOG))
        else:
            self._wedge_heals.append(now)
            msg = ("Friday stopped answering, so I restarted her. "
                   "The details are in the logs folder.")
            threading.Thread(target=self.restart_server, daemon=True).start()
        if self.icon is not None:
            try:
                _notice_registry.tray_balloon("E67", self.icon, msg, brand.PRODUCT_NAME)
            except Exception:
                pass

    def _meeting_status(self) -> dict:
        try:
            import json
            with urllib.request.urlopen(_meetings_status_url(), timeout=2.0) as r:
                return json.load(r) or {}
        except Exception:
            return {}

    def _local_queue(self) -> dict:
        try:
            import json
            with urllib.request.urlopen(_local_queue_url(), timeout=2.0) as r:
                return json.load(r) or {}
        except Exception:
            return {}

    def _show_recording_icon(self, recording: bool) -> None:
        """Swap the tray image to the recording variant while a meeting records,
        and back when it stops. Does nothing before the icon image is known."""
        base = getattr(self, "_base_image", None)
        if self.icon is None or base is None:
            return
        if bool(getattr(self, "_icon_recording", False)) == recording:
            return
        try:
            if recording and getattr(self, "_rec_image", None) is None:
                self._rec_image = recording_icon(base)
            self.icon.icon = self._rec_image if recording else base
            self._icon_recording = recording
        except Exception:
            pass

    def _update_meeting_title(self) -> None:
        """While a meeting is recording, the tray tooltip says so, with the time.

        The UI shows the same thing as a red dot; this is the one place that
        stays visible when every Friday window is closed. Otherwise, while
        background work waits for the local model, the tooltip says that
        ("Local AI: 3 tasks queued · next: ... · waits for idle").
        """
        if self.icon is None:
            return
        try:
            from agent_friday.services.meeting_capture import tray_tooltip
            tip = tray_tooltip(self._meeting_status()) if self.running else None
        except Exception:
            tip = None
        # The dot comes on at once and goes off only after two polls in a row
        # say "not recording", so one slow or failed status poll cannot hide it
        # while a recording is still going. (The tooltip follows each poll.)
        if tip:
            self._not_recording_polls = 0
            self._show_recording_icon(True)
        else:
            n = int(getattr(self, "_not_recording_polls", 0)) + 1
            self._not_recording_polls = n
            if n >= 2:
                self._show_recording_icon(False)
        if not tip and self.running:
            try:
                from agent_friday.services.seat_supervisor import tray_tooltip as _queue_tip
                tip = _queue_tip(self._local_queue())
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
        self._base_image = image
        self._rec_image = None
        self._icon_recording = False
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
        if self.open_on_ready:
            threading.Thread(target=self._open_when_ready,
                             name="friday-open-when-ready", daemon=True).start()

        self.icon.run()

    def _start_push_to_transcribe(self) -> None:
        """Register the dictation hotkey now, and keep at it until it holds.

        The keyboard hook does not need the server; only transcription does.
        So registration does not wait for /api/health: a cold start can take
        minutes (SERVER_START_TIMEOUT_S), and a wait with a deadline is a
        hotkey that never registers on a slow machine. A hold that arrives
        before the server can transcribe says so on the indicator, which is
        what separates "not ready yet" from "it did nothing".

        A registration that fails is tried again, backing off to once a
        minute, for as long as the tray runs. A failure that survives a retry
        is said out loud, in a notification and on the menu line, and so is
        the recovery: under pythonw the log reaches no file.
        """
        delay = PTT_RETRY_FIRST_S
        failures = 0
        told = None
        while True:
            try:
                outcome = self.ptt.apply()
            except Exception as e:
                self.ptt.detail = "failed to start (%s)" % e
                outcome = PushToTranscribe.RETRY
            self._refresh_menu()
            if outcome in (PushToTranscribe.ACTIVE, PushToTranscribe.OFF):
                if told is not None and outcome == PushToTranscribe.ACTIVE:
                    self._notify("Push-to-Transcribe is working now: hold %s "
                                 "to dictate." % self.ptt.detail)
                return
            failures += 1
            log.warning("push-to-transcribe not registered (attempt %d): %s",
                        failures, self.ptt.detail)
            if failures >= 2 and self.ptt.detail != told:
                told = self.ptt.detail
                self._notify("Push-to-Transcribe is not working: %s. Friday "
                             "keeps trying." % told)
            time.sleep(delay)
            delay = min(delay * 2, PTT_RETRY_MAX_S)

    def _notify(self, message: str) -> None:
        if self.icon is not None:
            try:
                _notice_registry.tray_balloon("E68", self.icon, message, brand.PRODUCT_NAME)
            except Exception:
                pass


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


def main(argv: list[str] | None = None) -> None:
    # `--open` comes from `Agent Friday (tray).cmd` (the Desktop and Start
    # menu icon). Looked for as a token rather than parsed, so an unrelated
    # argument (a test runner's own argv) is never a reason not to start.
    args = list(sys.argv[1:] if argv is None else argv)
    open_ui = "--open" in args

    # Kiosk image (FRIDAY_OS_MODE=1): there is no desktop to put a tray icon
    # on — see core/os_mode.py. The sealed Linux image starts server.py
    # directly (systemd unit / `friday` CLI), never this tray, so skipping
    # here rather than in a caller is the one place that actually gates every
    # way this entry point could be invoked.
    if _os_mode_active():
        print("[FRIDAY] FRIDAY_OS_MODE is on — skipping the system tray "
              "(no desktop to put it on in kiosk mode).")
        return

    if open_ui:
        _attach_friday_log()

    # The single-instance guard comes first: a tray already running is
    # Friday, and a click opens her page rather than anything new.
    if not _acquire_single_instance():
        if open_ui:
            _open_running_ui()
        return

    if open_ui:
        # Setup asks questions, and pythonw has nowhere to ask them: the
        # console launcher runs the wizard (and then starts Friday).
        if _first_run_pending():
            # The console holds the setup lock while it is open; this tray
            # exits after handing over, so its own guard does not cover it.
            if _setup_already_open():
                _open_log.info("tray --open: setup is already open in its console "
                               "window (setup lock held); not starting a second one")
                return
            _open_log.info("tray --open: setup has not been completed on this "
                           "computer; handing over to the console launcher")
            if _hand_over_to_console():
                return
        _seed_skills()

    tray = FridayTray()
    if open_ui:
        tray.open_on_ready = True

    def _on_signal(_sig, _frm):
        # Being told to stop is a quit, not a restart: hand the memory back.
        try:
            tray._release_seats("tray_signal")
            tray.stop_server()
        except Exception:
            pass
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    except Exception:
        pass

    tray.run()


if __name__ == "__main__":
    main()
