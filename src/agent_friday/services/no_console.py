"""Stop Windows console windows flashing up when Friday runs a subprocess.

WHY
---
Friday is a desktop app. Every time it shells out — and it shells out a lot:
git, powershell, ffmpeg, ollama, nvidia-smi, the credential helpers, the MCP
clients — Windows opens a console window for the child unless it is told not
to. The result is a stream of terminal window popups while Friday runs, and
even after Friday is closed (the tray keeps running).

The flag that prevents it already exists in this tree, four separate times,
under three different names: `_POPEN_FLAGS` in `core/__init__.py` and
`routing/ollama_manager.py`, `_CREATE_FLAGS` in `mcp_client.py`,
`CREATE_NO_WINDOW` in `friday_tray.py`. It is applied at some call sites and
not at others. An audit found **46 subprocess calls with no `creationflags`
at all**, spread across the CLI, the tray, the setup wizard,
the agent, the connectors and the credential store.

WHY THIS IS A PATCH AND NOT 46 EDITS
------------------------------------
Because 46 edits fixes today and not tomorrow. The next subprocess call
somebody adds will flash a console too, and nothing will catch it: there is no
test that can see a window appear. A default applied once holds for code
nobody has written yet.

The escape hatch is deliberate and explicit. Anything that genuinely wants a
visible console passes `creationflags=` itself — the patch never overrides a
caller who has expressed an opinion — or passes
`friday_show_console=True`, which reads as a decision at the call site rather
than as an accident.

This is a no-op on every platform except Windows.
"""

from __future__ import annotations

import logging
import subprocess
import sys

_log = logging.getLogger("friday.no_console")

_INSTALLED = False
#: 0x08000000. Named rather than imported because it does not exist on POSIX.
CREATE_NO_WINDOW = 0x08000000


def install() -> bool:
    """Make CREATE_NO_WINDOW the default for child processes. Idempotent.

    Returns True when the patch is in force, False on non-Windows or if the
    patch could not be applied — never raises, because a cosmetic fix must not
    be able to stop the app from booting.
    """
    global _INSTALLED
    if _INSTALLED or sys.platform != "win32":
        return _INSTALLED
    try:
        _original_init = subprocess.Popen.__init__

        def _init(self, *args, **kwargs):
            # An explicit opinion always wins. `friday_show_console=True` is
            # the readable way to say "I want the window"; passing
            # `creationflags` yourself is the other.
            show = kwargs.pop("friday_show_console", False)
            if not show and "creationflags" not in kwargs:
                kwargs["creationflags"] = CREATE_NO_WINDOW
            return _original_init(self, *args, **kwargs)

        _init.__friday_no_console__ = True
        # Guard against double-wrapping if two entry points both call install().
        if not getattr(subprocess.Popen.__init__, "__friday_no_console__", False):
            subprocess.Popen.__init__ = _init
        _INSTALLED = True
        _log.debug("console suppression installed for child processes")
        return True
    except Exception as e:
        # A failure here costs flashing windows, which is exactly what we were
        # trying to avoid and still not worth a crash.
        _log.warning("could not install console suppression (%s)", e)
        return False


def is_installed() -> bool:
    return _INSTALLED
