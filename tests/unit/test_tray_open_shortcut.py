"""The Desktop and Start menu icons start the tray, not a console window.

Field report: the icons launched `Agent Friday.cmd`, a python.exe console that
has to stay open for Friday to keep running. They now launch
`Agent Friday (tray).cmd` (pythonw + tray icon) with `--open`:

* the browser opens once Friday answers;
* a click while a tray is already running (the sign-in start, say) opens the
  page instead of exiting silently, which is what the single-instance guard
  used to do;
* first-run setup still happens: the tray hands over to the console launcher
  when setup has not been completed, and seeds the bundled skills the way the
  console launcher does;
* the sign-in start (`Agent Friday (background).cmd`) is unchanged, and the
  post-install "Start Agent Friday now" box still runs the console launcher.

The installer half reads the scripts (Inno Setup and PowerShell cannot run in a
unit test). The tray half drives `friday_tray.main()` with the single-instance
guard, the health wait and `webbrowser.open` stubbed: nothing here starts a
server, binds a port or opens a browser.
"""
from __future__ import annotations

import logging
import re
import socket as socket_mod
import sys

import pytest

from tests.unit._installer_ps import ISS, LIB, WINDOWS_DIR, read_text

SCRIPT = read_text(ISS)
SHORTCUTS = read_text(LIB / "Shortcuts.ps1")
INSTALL = read_text(WINDOWS_DIR / "install.ps1")

TRAY_CMD = "Agent Friday (tray).cmd"


def _icons_section() -> str:
    return SCRIPT[SCRIPT.index("[Icons]"):SCRIPT.index("[InstallDelete]")]


def _icon_line(name: str) -> str:
    for ln in _icons_section().splitlines():
        if ln.startswith('Name: "%s"' % name):
            return ln
    raise AssertionError("[Icons] has no entry %s" % name)


def _function(name: str) -> str:
    start = SHORTCUTS.index("function %s" % name)
    nxt = SHORTCUTS.find("\nfunction ", start + 1)
    return SHORTCUTS[start:nxt if nxt != -1 else len(SHORTCUTS)]


# ── the installer points the icons at the tray launcher ────────────────────

def test_inno_desktop_and_start_menu_icons_run_the_tray_launcher():
    for name in (r"{userdesktop}\{#AppName}", r"{userprograms}\{#AppName}\{#AppName}"):
        ln = _icon_line(name)
        assert r'Filename: "{app}\%s"' % TRAY_CMD in ln, ln
        assert r'Filename: "{app}\Agent Friday.cmd"' not in ln, ln


def test_inno_sign_in_start_and_first_run_are_unchanged():
    assert r'Filename: "{app}\Agent Friday (background).cmd"' in _icon_line(r"{userstartup}\{#AppName}")
    run = SCRIPT[SCRIPT.index("[Run]"):]
    first = run.splitlines()[1]
    assert r'Filename: "{app}\Agent Friday.cmd"' in first and "postinstall" in first, \
        "the first start after setup stays on the console launcher, where the wizard can ask its questions"


def test_powershell_shortcuts_run_the_tray_launcher():
    body = _function("Install-Shortcuts")
    assert re.search(r"\$launcher\s*=\s*Join-Path \$InstallRoot '%s'" % re.escape(TRAY_CMD), body), \
        "the Desktop and Start menu 'Agent Friday.lnk' target the tray launcher"
    assert "Join-Path $InstallRoot 'Agent Friday.cmd'" not in body


def test_the_tray_launcher_is_written_and_verified_and_uses_pythonw_with_open():
    body = _function("Install-LauncherScripts")
    assert "'%s'" % TRAY_CMD in body, "Install-LauncherScripts writes the tray launcher"
    m = re.search(r"\$tray = @\"(.*?)\"@", body, flags=re.S)
    assert m, "the tray launcher's text is defined"
    text = m.group(1)
    assert "$pywRel" in text and "friday_tray.py" in text and "--open" in text
    assert "$pyRel\"" not in text, "no console python in the tray launcher"
    assert "'%s'" % TRAY_CMD in _function("Test-LauncherScripts"), \
        "an upgrade from a version without it re-runs the launcher step and writes it"


def test_an_old_desktop_shortcut_to_the_console_launcher_is_recreated_on_upgrade():
    body = _function("Test-ShortcutsInstalled")
    assert TRAY_CMD in body, "a shortcut still pointing at the console launcher is not 'installed'"


def test_autostart_still_uses_the_background_launcher():
    assert "'Agent Friday (background).cmd'" in _function("Enable-Autostart")


# ── the tray's --open path ──────────────────────────────────────────────────

needs_tray = pytest.mark.skipif(
    sys.platform != "win32",
    reason="friday_tray is a Windows-only tray app (pystray is win32-only)")


class _FakeTray:
    instances: list = []

    def __init__(self):
        self.ran = False
        _FakeTray.instances.append(self)

    def run(self):
        self.ran = True


class _FakeGuardSocket:
    def __init__(self, *_a, **_k):
        pass

    def setsockopt(self, *_a, **_k):
        pass

    def bind(self, _addr):
        pass


@pytest.fixture
def tray(monkeypatch, tmp_path):
    friday_tray = pytest.importorskip("agent_friday.friday_tray")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    monkeypatch.delenv("FRIDAY_OS_MODE", raising=False)
    _FakeTray.instances.clear()
    monkeypatch.setattr(friday_tray, "FridayTray", _FakeTray)
    monkeypatch.setattr(socket_mod, "socket", _FakeGuardSocket)
    monkeypatch.setattr(friday_tray.signal, "signal", lambda *a, **k: None)
    friday_tray._INSTANCE_HANDLES.clear()
    # No console setup is open unless a test says so (the real lock is a
    # session-wide mutex; a test must not depend on the desktop it runs on).
    monkeypatch.setattr(friday_tray, "_setup_already_open", lambda: False, raising=False)

    opened: list = []
    monkeypatch.setattr(friday_tray.webbrowser, "open", lambda url, *a, **k: opened.append(url))
    monkeypatch.setattr(friday_tray, "_wait_for_health", lambda *a, **k: (True, "healthy"))
    monkeypatch.setattr(friday_tray, "_ui_url", lambda: "http://localhost:3999", raising=False)

    seeded: list = []
    import agent_friday.seed as seed_mod
    monkeypatch.setattr(seed_mod, "ensure_seed_skills_installed", lambda *a, **k: seeded.append(True))

    launched: list = []
    monkeypatch.setattr(friday_tray.os, "startfile", lambda p, *a, **k: launched.append(("startfile", p)),
                        raising=False)
    monkeypatch.setattr(friday_tray.subprocess, "Popen", lambda cmd, *a, **k: launched.append(("popen", cmd)))

    monkeypatch.setattr(friday_tray, "_test_state",
                        {"opened": opened, "seeded": seeded, "launched": launched, "home": home},
                        raising=False)
    yield friday_tray
    friday_tray._INSTANCE_HANDLES.clear()
    for h in list(logging.getLogger("friday.tray").handlers):
        logging.getLogger("friday.tray").removeHandler(h)
        h.close()


def _set_up_user(monkeypatch, done: bool):
    import agent_friday.cli as cli
    monkeypatch.setattr(cli, "_is_existing_user", lambda: done)


@needs_tray
def test_open_with_a_tray_already_running_opens_the_ui_instead_of_exiting_silently(tray, monkeypatch):
    _set_up_user(monkeypatch, True)
    monkeypatch.setattr(tray, "_claim_windows_mutex", lambda: False)  # another tray holds it
    monkeypatch.setattr(sys, "argv", ["friday_tray.py", "--open"])

    tray.main()

    assert tray._test_state["opened"] == ["http://localhost:3999"], \
        "clicking the icon while Friday runs in the tray opens her page"
    assert _FakeTray.instances == [], "no second tray is started"
    log = (tray._test_state["home"] / "friday.log").read_text(encoding="utf-8")
    assert "already running" in log, "the decision is in friday.log"


@needs_tray
def test_open_starting_the_first_tray_opens_the_ui_once_ready(tray, monkeypatch):
    _set_up_user(monkeypatch, True)
    monkeypatch.setattr(tray, "_claim_windows_mutex", lambda: True)
    monkeypatch.setattr(sys, "argv", ["friday_tray.py", "--open"])

    tray.main()

    assert len(_FakeTray.instances) == 1 and _FakeTray.instances[0].ran
    assert getattr(_FakeTray.instances[0], "open_on_ready", False) is True
    assert tray._test_state["seeded"], "bundled skills are seeded as the console launcher does"


@needs_tray
def test_open_when_setup_was_never_completed_hands_over_to_the_console_launcher(tray, monkeypatch, tmp_path):
    _set_up_user(monkeypatch, False)
    monkeypatch.setattr(tray, "_claim_windows_mutex", lambda: True)
    install_root = tmp_path / "install"
    (install_root / "app").mkdir(parents=True)
    console_cmd = install_root / "Agent Friday.cmd"
    console_cmd.write_text("@echo off\r\n", encoding="ascii")
    monkeypatch.setattr(tray, "PROJECT_DIR", install_root / "app")
    monkeypatch.setattr(sys, "argv", ["friday_tray.py", "--open"])

    tray.main()

    assert tray._test_state["launched"] == [("startfile", str(console_cmd))], \
        "first run goes through the console launcher, which runs the setup wizard"
    assert _FakeTray.instances == [], "the tray does not start a server before setup"
    assert tray._test_state["opened"] == []


@needs_tray
def test_the_sign_in_start_without_open_is_unchanged(tray, monkeypatch):
    _set_up_user(monkeypatch, False)  # even unset-up: the background start never hands over
    monkeypatch.setattr(tray, "_claim_windows_mutex", lambda: False)
    monkeypatch.setattr(sys, "argv", ["friday_tray.py"])

    tray.main()

    assert tray._test_state["opened"] == [], "a second sign-in start still exits quietly"
    assert tray._test_state["launched"] == []
    assert _FakeTray.instances == []


@needs_tray
def test_a_tray_already_running_is_checked_before_first_run_setup(tray, monkeypatch, tmp_path):
    """Setup is not complete, but a tray already holds the single-instance
    guard: the click opens the running Friday's page, not another wizard."""
    _set_up_user(monkeypatch, False)
    monkeypatch.setattr(tray, "_claim_windows_mutex", lambda: False)  # another tray holds it
    install_root = tmp_path / "install"
    (install_root / "app").mkdir(parents=True)
    (install_root / "Agent Friday.cmd").write_text("@echo off\r\n", encoding="ascii")
    monkeypatch.setattr(tray, "PROJECT_DIR", install_root / "app")
    monkeypatch.setattr(sys, "argv", ["friday_tray.py", "--open"])

    tray.main()

    assert tray._test_state["launched"] == [], "no second console setup is started"
    assert tray._test_state["opened"] == ["http://localhost:3999"]
    assert _FakeTray.instances == []


@needs_tray
def test_a_second_click_while_the_setup_console_is_open_does_not_open_another(tray, monkeypatch, tmp_path):
    """The first click handed over to the console and exited, so no tray
    holds the guard; the console's setup lock says the wizard is open."""
    _set_up_user(monkeypatch, False)
    monkeypatch.setattr(tray, "_claim_windows_mutex", lambda: True)
    monkeypatch.setattr(tray, "_setup_already_open", lambda: True, raising=False)
    install_root = tmp_path / "install"
    (install_root / "app").mkdir(parents=True)
    (install_root / "Agent Friday.cmd").write_text("@echo off\r\n", encoding="ascii")
    monkeypatch.setattr(tray, "PROJECT_DIR", install_root / "app")
    monkeypatch.setattr(sys, "argv", ["friday_tray.py", "--open"])

    tray.main()

    assert tray._test_state["launched"] == [], "the open wizard is not opened a second time"
    assert _FakeTray.instances == [], "no tray or server starts before setup"
    log = (tray._test_state["home"] / "friday.log").read_text(encoding="utf-8")
    assert "already open" in log, "the decision is in friday.log"


@pytest.mark.skipif(sys.platform != "win32", reason="the setup lock is a Windows named mutex")
def test_the_setup_lock_is_held_while_claimed(monkeypatch):
    import uuid
    from agent_friday import setup_lock
    monkeypatch.setattr(setup_lock, "NAME", "Local\\AgentFridaySetupTest-%s" % uuid.uuid4().hex)
    setup_lock.release_for_tests()
    try:
        assert setup_lock.is_held() is False
        assert setup_lock.claim() is True
        assert setup_lock.is_held() is True
    finally:
        setup_lock.release_for_tests()
    assert setup_lock.is_held() is False


def test_the_console_launcher_holds_the_setup_lock_while_setup_is_pending(monkeypatch):
    import agent_friday.cli as cli
    claimed: list = []
    from agent_friday import setup_lock
    monkeypatch.setattr(setup_lock, "claim", lambda: claimed.append(True) or True)
    monkeypatch.setattr(cli, "_is_existing_user", lambda: False)
    monkeypatch.setattr(cli, "ensure_seed_skills_installed", lambda *a, **k: None)
    monkeypatch.setattr(cli.Confirm, "ask", lambda *a, **k: False)   # decline setup now

    cli.cmd_start()

    assert claimed, "the console holds the setup lock before it asks to run setup"
