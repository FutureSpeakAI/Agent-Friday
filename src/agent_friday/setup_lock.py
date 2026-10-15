"""A lock held while the console setup is open, so a second click does not open a second one.

The Desktop and Start menu icon runs the tray with ``--open``. Before setup is
complete the tray hands over to the console launcher (``Agent Friday.cmd``),
which runs the setup wizard, and then exits: the tray's own single-instance
guard is gone with it. A second click while the wizard is still open would
hand over again and open a second wizard writing the same settings.

So the console launcher holds this lock from the moment it finds setup
pending until it exits (``cli.cmd_start`` / ``cli.cmd_setup``), and the tray
asks ``is_held()`` before handing over.

Windows: a named kernel mutex in the signed-in session (``Local\\``), released
by the OS when the holding process exits, however it exits. Elsewhere the
tray is not used, and ``claim`` / ``is_held`` are no-ops (True / False).
Never raises: a lock that cannot be taken or read must not stop setup.
"""
from __future__ import annotations

import sys

#: The mutex name. One per signed-in user, as the tray's own guard.
NAME = r"Local\AgentFridaySetup"

ERROR_ALREADY_EXISTS = 183

#: Handles held for the life of the process (a handle that falls out of
#: scope is a mutex released).
_HELD: list = []


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return ctypes, k32


def claim() -> bool:
    """Hold the setup lock until this process exits. True when this process
    holds it (or the platform has none), False when another process does."""
    if sys.platform != "win32":
        return True
    if _HELD:
        return True
    try:
        ctypes, k32 = _kernel32()
        handle = k32.CreateMutexW(None, False, NAME)
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            k32.CloseHandle(handle)
            return False
        _HELD.append(handle)
    except Exception:
        pass
    return True


def is_held() -> bool:
    """True when a process holds the setup lock (a console setup is open)."""
    if sys.platform != "win32":
        return False
    try:
        ctypes, k32 = _kernel32()
        handle = k32.CreateMutexW(None, False, NAME)
        if not handle:
            return False
        held = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        # Closing our own handle: when nobody else held the name, this
        # destroys the mutex we just made, leaving no trace.
        k32.CloseHandle(handle)
        return held
    except Exception:
        return False


def release_for_tests() -> None:
    """Drop this process's hold (tests only)."""
    if sys.platform != "win32":
        _HELD.clear()
        return
    try:
        _ctypes, k32 = _kernel32()
        for h in _HELD:
            k32.CloseHandle(h)
    except Exception:
        pass
    _HELD.clear()
