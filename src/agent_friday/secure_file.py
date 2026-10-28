"""Create a secret file once, atomically, readable by this user only.

Kept free of other Friday imports on purpose: agent_friday.core uses it while
it is still being imported (the session secret), so it may depend on the
standard library only.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

_log = logging.getLogger("friday.secure_file")


def unique_tmp(path: Path) -> Path:
    """A temp name beside `path` that no other process or thread will pick."""
    path = Path(path)
    return path.with_name("%s.%d.%s.tmp" % (path.name, os.getpid(), os.urandom(4).hex()))


def harden(path: Path) -> bool:
    """Restrict a file to the current user. POSIX: mode 0600. Windows: drop
    inherited entries and grant only this user (icacls). True when applied."""
    path = Path(path)
    ok = True
    try:
        os.chmod(path, 0o600)
    except Exception:
        ok = os.name == "nt"
    if os.name == "nt":
        try:
            user = os.environ.get("USERNAME") or ""
            if not user:
                ok = False
            else:
                res = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                                     capture_output=True, timeout=10, check=False)
                ok = getattr(res, "returncode", 1) == 0
        except Exception:
            ok = False
    if not ok:
        _log.warning("could not restrict a protected file to this user (%s)", path.name)
    return ok


def create_exclusive(path: Path, data: bytes, *, harden_file: bool = True,
                     harden_fn: Optional[Callable[[Path], object]] = None) -> bool:
    """Write `data` to `path` only if nothing is there yet, atomically.

    True: this call created the file. False: another process or thread got
    there first, and its file is left as it is (the caller reads and adopts
    it). The content is written and flushed to a temp file of its own first,
    then linked into place, so the file never exists half-written. Where hard
    links are not available it falls back to an exclusive create.
    """
    path = Path(path)
    do_harden = harden_fn or harden
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = unique_tmp(path)
    try:
        # The temp file is created owner-only before the secret goes in, so no
        # copy is ever readable by others, even for the moment before harden().
        os.close(os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600))
        tmp.write_bytes(data)
        try:
            with open(tmp, "r+b") as fh:          # a writable handle: fsync needs one on Windows
                os.fsync(fh.fileno())
        except Exception:
            pass
        if harden_file:
            do_harden(tmp)
        try:
            os.link(tmp, path)
            linked = True
        except FileExistsError:
            return False
        except (OSError, NotImplementedError, AttributeError):
            linked = False
        if not linked:
            if os.name == "nt":
                # Windows rename never replaces an existing file and is atomic
                # on FAT, exFAT and network shares too.
                try:
                    os.rename(tmp, path)
                except FileExistsError:
                    return False
            else:
                try:
                    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    return False
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(data)
                        fh.flush()
                        os.fsync(fh.fileno())
                except BaseException:
                    # never leave a half-written key where a reader would find it
                    try:
                        path.unlink()
                    except Exception:
                        pass
                    raise
        if harden_file:
            do_harden(path)
        return True
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
