"""Root conftest — exists for ONE job: silence child consoles before xdist
spawns its workers.

WHY IT IS HERE AND NOT IN tests/conftest.py, which is where the same patch
first went and where it does not work.

Friday spawns `python -m pytest` with CREATE_NO_WINDOW, so the master has no
console. pytest-xdist then creates its workers through plain execnet Popen
calls with no creationflags (execnet/gateway_io.py:34), and a console-subsystem
child whose parent has no console to inherit gets a brand-new one — which on
Windows 11 means Windows Terminal opens a window. `-n auto` means one per core.

services/no_console.install() fixes that by defaulting Popen to
CREATE_NO_WINDOW, but only in the process that calls it. It has to be called in
the pytest MASTER, and it has to happen before xdist builds the workers.

`tests/conftest.py` is loaded early enough for `pytest tests/unit/foo.py` and
NOT early enough for `pytest tests/unit tests/api`, which is what the nightly
loop runs. Measured, not assumed: with the patch in tests/conftest.py, the
loop's own command line still showed a window. A conftest at the rootdir is
loaded by the master during config initialisation, before any plugin can
spawn anything, for every invocation. That is the property this file exists
to get, and the reason it is a separate file rather than three more lines in
the one below it.

Deliberately nothing else lives here. The hermetic-environment setup stays in
tests/conftest.py where it belongs; a rootdir conftest applies to anything
pytest is ever pointed at, so the less it does the better.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from agent_friday.services.no_console import install as _install_no_console

    _ok = _install_no_console()
    if os.environ.get("FRIDAY_CONSOLE_TRACE"):
        import subprocess as _sp

        print(
            "[rootconftest] no_console install=%s patched=%s"
            % (_ok, getattr(_sp.Popen.__init__, "__friday_no_console__", False)),
            flush=True,
        )
except Exception:  # noqa: BLE001
    # A cosmetic patch must never be able to stop the suite from starting.
    pass
