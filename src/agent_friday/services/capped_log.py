"""A size-capped log file that a child process writes through an inherited handle.

The tray opens ``server_stderr.log`` and hands the handle to the server as its
stdout and stderr. Two facts shape the rotation:

  * On Windows a file another process holds open cannot be renamed, so the live
    file is rotated by copy-then-truncate: its bytes are copied to ``.1`` (older
    backups shift up to ``.N``) and the live file is truncated to empty.
  * A truncated file is only safe if every writer appends at the CURRENT end.
    ``open_shared_append`` therefore opens the file append-only at the OS level
    (``FILE_APPEND_DATA`` without ``FILE_WRITE_DATA`` on Windows, ``O_APPEND``
    elsewhere), shared for read, write and delete. A child that inherits that
    handle writes at the end of the file whatever its own offset says, so a
    rotation never leaves the live log padded with zeros.

Invariants:

  * The live file never grows much past ``max_bytes`` between checks, and at
    most ``backups`` numbered files exist beside it.
  * Rotation never renames or deletes the live file; it only truncates it.
  * Bytes written between the end of the copy and the truncate are lost; the
    copy reads to the end immediately before truncating to keep that window to
    one write.

Standard library only: the tray imports this before anything heavy loads.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

#: Default cap for the live log and the number of numbered backups kept.
MAX_BYTES = 50 * 1024 * 1024
BACKUPS = 3

_COPY_CHUNK = 1024 * 1024


def open_shared_append(path):
    """A binary, unbuffered file object on ``path`` whose OS handle appends.

    The handle is inheritable by ``subprocess.Popen`` (pass the file object as
    ``stdout``); every write through it, or through a duplicate of it, lands
    at the end of the file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        return open(path, "ab", buffering=0)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    FILE_APPEND_DATA = 0x0004
    SYNCHRONIZE = 0x00100000
    FILE_SHARE_ALL = 0x1 | 0x2 | 0x4  # read | write | delete
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                       wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), FILE_APPEND_DATA | SYNCHRONIZE, FILE_SHARE_ALL,
                    None, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)
    if handle is None or handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError()
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_APPEND)
    except Exception:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise
    return os.fdopen(fd, "ab", buffering=0)


def _backup(path: Path, n: int) -> Path:
    return path.with_name("%s.%d" % (path.name, n))


def rotate_if_over(path, max_bytes: int = MAX_BYTES, backups: int = BACKUPS) -> bool:
    """Rotate ``path`` when it is larger than ``max_bytes``; True if it did.

    ``.1`` .. ``.{backups}`` hold the previous generations, newest first; the
    oldest is dropped. The live file is copied, then truncated in place.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= max_bytes or backups < 1:
        return False
    oldest = _backup(path, backups)
    if oldest.exists():
        oldest.unlink()
    for n in range(backups - 1, 0, -1):
        src = _backup(path, n)
        if src.exists():
            os.replace(src, _backup(path, n + 1))
    with open(path, "r+b") as live, open(_backup(path, 1), "wb") as out:
        shutil.copyfileobj(live, out, _COPY_CHUNK)
        live.truncate(0)
    return True
