"""Resource guard for test runs on the owner's machine.

The live Friday, its local model seat (about 18 GB) and every test run share
one PC. Parallel full suites have taken free RAM to 0.4 GB, pushed the commit
charge to its ceiling, filled the system disk through the pagefile and left
the live server unable to answer. So a test run obeys two rules:

1. **At most two xdist workers.** ``-n auto``, ``-n logical`` and any larger
   ``-n N`` are capped at ``MAX_WORKERS``.
2. **A broad run needs room.** A run over directories (``pytest``,
   ``pytest tests/unit tests/api``) rather than named test files starts only
   with at least ``MIN_FREE_RAM_GB`` of free memory and ``MIN_FREE_DISK_GB`` of
   free space on the drive holding the repository. Below either floor it stops
   before collecting anything and says which floor, and by how much.

Targeted runs (``pytest tests/unit/test_x.py``) are never refused. The floors
are loaded from here and nowhere else; the tests in
``tests/unit/test_resource_guard.py`` pin them.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path

import pytest

MAX_WORKERS = 2
MIN_FREE_RAM_GB = 12.0
MIN_FREE_DISK_GB = 20.0
_ROOT = Path(__file__).resolve().parent


def free_ram_gb() -> float | None:
    """Free physical memory in GB, or None when it cannot be read."""
    if sys.platform == "win32":
        class _MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = _MS()
        ms.dwLength = ctypes.sizeof(_MS)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return ms.ullAvailPhys / 1024 ** 3
        except Exception:
            return None
        return None
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024 ** 3
    except (ValueError, OSError, AttributeError):
        return None


def free_disk_gb(path: Path = _ROOT) -> float | None:
    try:
        return shutil.disk_usage(str(path)).free / 1024 ** 3
    except OSError:
        return None


def capped_workers(requested):
    """The worker count a run may use: ``requested`` capped at MAX_WORKERS.

    ``requested`` is what ``-n`` parsed to: an int, or "auto"/"logical"
    (resolved by the hook below), or None when xdist is not in use.
    """
    if isinstance(requested, int):
        return min(requested, MAX_WORKERS)
    return requested


def is_broad_run(args, rootdir: Path = _ROOT) -> bool:
    """True when the run names a directory (or nothing, so testpaths) rather
    than specific test files or node ids."""
    targets = [a for a in args if not str(a).startswith("-")]
    if not targets:
        return True
    for a in targets:
        p = Path(str(a).split("::", 1)[0])
        if not p.is_absolute():
            p = rootdir / p
        if p.is_dir():
            return True
    return False


def refusal(ram, disk) -> str | None:
    """Why a broad run may not start now, or None when it may."""
    short = []
    if ram is not None and ram < MIN_FREE_RAM_GB:
        short.append(f"free memory is {ram:.1f} GB and a full run needs {MIN_FREE_RAM_GB:.0f} GB")
    if disk is not None and disk < MIN_FREE_DISK_GB:
        short.append(f"free disk is {disk:.1f} GB and a full run needs {MIN_FREE_DISK_GB:.0f} GB")
    if not short:
        return None
    return ("Not starting a full test run: " + "; ".join(short) + ". Run the test files you "
            "changed instead (pytest tests/unit/test_x.py), or wait for room.")


@pytest.hookimpl(tryfirst=True)
def pytest_xdist_auto_num_workers(config):
    return MAX_WORKERS


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    """Cap ``-n`` before xdist turns it into a worker count. xdist's own
    hook is tryfirst too; this plugin registers later, so it runs first."""
    n = getattr(config.option, "numprocesses", None)
    capped = capped_workers(n)
    if capped != n:
        config.option.numprocesses = capped


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    if hasattr(config, "workerinput"):
        return
    n = getattr(config.option, "numprocesses", None)
    capped = capped_workers(n)
    if capped != n:
        config.option.numprocesses = capped
    if is_broad_run(config.args, Path(str(config.rootpath))):
        why = refusal(free_ram_gb(), free_disk_gb(Path(str(config.rootpath))))
        if why:
            pytest.exit(why, returncode=4)
