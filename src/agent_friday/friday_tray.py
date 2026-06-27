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

PROJECT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_DIR / "venv" / "Scripts" / "python.exe"
SERVER_SCRIPT = PROJECT_DIR / "server.py"
ICON_PATH = PROJECT_DIR / "assets" / "icons" / "futurespeak.png"
VOICE_LOG = Path.home() / ".friday" / "voice_debug.log"
SERVER_URL = "http://localhost:3000"
HEALTH_URL = f"{SERVER_URL}/api/health"
PORT = 3000

CREATE_NO_WINDOW = 0x08000000  # Windows: suppress child console


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_health(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


class FridayTray:
    def __init__(self) -> None:
        self.server_proc: subprocess.Popen | None = None
        self.running = False
        self.icon: pystray.Icon | None = None
        self._lock = threading.Lock()

    # ── Server lifecycle ──────────────────────────────────────────────
    def start_server(self) -> None:
        with self._lock:
            if self.server_proc and self.server_proc.poll() is None:
                return
            if _port_in_use(PORT):
                # Server already running externally — treat as healthy.
                self.running = True
                return
            python_exe = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
            self.server_proc = subprocess.Popen(
                [python_exe, str(SERVER_SCRIPT)],
                cwd=str(PROJECT_DIR),
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self.running = _wait_for_health()
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
        self.running = False

    def restart_server(self) -> None:
        self.stop_server()
        # Give the OS a moment to release the port.
        time.sleep(0.5)
        self.start_server()

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
        return "Server Status: Running" if self.running else "Server Status: Stopped"

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
