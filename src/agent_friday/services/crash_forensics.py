"""Make a hard crash name itself, and remove the one native crash we can name.

THE FAILURE THIS ADDRESSES
--------------------------
Console windows flashing on the desktop look like a missing CREATE_NO_WINDOW
flag, but they can be something else: a process that gets a console in the
same second as `WerFault.exe` - Windows Error Reporting - is a crash reporter.
Fixes aimed at console flags do not stop those.

A representative day held seven Python crashes in 24 hours:

    5 x python313.dll, exception 0xc0000005 (access violation), at repeating
        offsets 0x2b0b98 / 0x2b0d13 / 0xf4dd8
    2 x hf_xet.pyd,    exception 0xc0000409 (fail-fast / Rust abort)

Every one of them loaded ~280 modules out of friday-desktop's own venv, so
they are Friday's processes, not something else on the machine.

A process that has died without unwinding also looks exactly like a nightly
job going `interrupted` mid-step, or a pytest run hung with a worker at 0% CPU.

WHY THIS MODULE EXISTS
----------------------
services/hang_watchdog.py already handles the case where Friday is ALIVE and
stuck - heartbeat plus `faulthandler.dump_traceback_later`. Nothing handled
the case where Friday DIES. `faulthandler.enable()` is the other half of that
library and was never called: it installs a handler for SIGSEGV and friends,
so an access violation writes a Python traceback instead of vanishing into a
WER dialog.

WER alone gives a fault offset in a DLL and nothing about which process it was or what it was doing. So the
header written here records pid, argv and start time: the next crash
identifies itself without anyone having to correlate timestamps by hand.

ONE SHARED FILE, on purpose. Friday runs dozens of Python processes; a file
per process would litter the logs directory with empty files. Appending means
occasional interleaving if two processes crash in the same instant, which is
a fair trade for crashes being rare and attribution being the point.

THE hf_xet CRASHES ARE DIRECTLY FIXABLE
---------------------------------------
`hf_xet` is huggingface_hub's Rust-backed Xet download accelerator, pulled in
transitively by transformers, sentence-transformers, faster-whisper, kokoro,
nemo and datasets. 0xc0000409 is what Rust's abort looks like from Windows.
`HF_HUB_DISABLE_XET=1` takes that code path out entirely; downloads fall back
to ordinary HTTP, which is slower for large pulls and does not crash. Set with
setdefault, so an explicit value in the environment still wins.

That accounts for two of the seven. The other five are an access violation in
CPython itself, which means a native extension corrupting memory - and naming
it needs the traceback this module now captures on the next occurrence.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
import time
from datetime import datetime

_log = logging.getLogger("friday.crash_forensics")

#: Kept module-global so the handle is never garbage-collected. faulthandler
#: writes through the raw fd at crash time; if Python closed this file the
#: handler would write into a dead descriptor precisely when it matters.
_FH = None
_INSTALLED = False

CRASH_LOG_NAME = "crashes.log"


def crash_log_path():
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "logs" / CRASH_LOG_NAME


def disable_hf_xet() -> bool:
    """Take the crashing Xet downloader out of the import graph.

    Must run BEFORE huggingface_hub is imported - it reads this at import
    time. Returns True if we set it, False if the environment already had an
    opinion (which wins, so this stays reversible without a code change).
    """
    if "HF_HUB_DISABLE_XET" in os.environ:
        return False
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    return True


def install(argv=None) -> bool:
    """Arm the crash handler. Idempotent; never raises.

    A diagnostic that can stop the app from booting is worse than the silence
    it replaces, so every failure here is swallowed and logged.
    """
    global _FH, _INSTALLED
    if _INSTALLED:
        return True
    try:
        p = crash_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = open(p, "a", encoding="utf-8", buffering=1)
        argv = list(argv if argv is not None else sys.argv)
        fh.write(
            "\n=== armed %s | pid %d | %s\n    exe: %s\n    argv: %s\n"
            % (datetime.now().isoformat(timespec="seconds"), os.getpid(),
               time.strftime("%Z"), sys.executable, " ".join(argv)[:500])
        )
        fh.flush()
        # all_threads because the faulting thread is rarely the interesting
        # one on its own - a crash in a native extension usually needs the
        # Python frames of whatever called it.
        faulthandler.enable(file=fh, all_threads=True)
        _FH = fh
        _INSTALLED = True
        _log.debug("crash handler armed -> %s", p)
        return True
    except Exception as e:
        _log.warning("could not arm the crash handler: %s", e)
        return False


def is_installed() -> bool:
    return _INSTALLED


def recent(limit_bytes: int = 20000) -> str:
    """Tail of the crash log, for the UI and for asking 'did we crash?'."""
    try:
        p = crash_log_path()
        if not p.exists():
            return ""
        data = p.read_text(encoding="utf-8", errors="replace")
        return data[-int(limit_bytes):]
    except Exception:
        return ""


def crash_count() -> int:
    """How many tracebacks the handler has written.

    Counts faulthandler's own banner rather than our 'armed' headers - every
    process writes a header, only a crashing one writes a fault.
    """
    text = recent(limit_bytes=10 ** 7)
    return sum(text.count(marker) for marker in
               ("Fatal Python error:", "Windows fatal exception:"))
