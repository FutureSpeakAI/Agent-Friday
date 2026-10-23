"""Write a file whole or not at all (north star NS-34.16-3).

A process killed half-way through `dest.write_bytes(...)` leaves a torn file
under the real name: a backup that looks like a backup and cannot be opened,
or a state file that reads as empty. Here the bytes go to a hidden partial
file beside the destination, are flushed to the disk, and only then take the
real name in one rename. A kill at any point leaves either the old file or the
new one, never a mix; the partial a killed writer leaves behind is removed by
the next writer to the same folder.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Callable, Optional, Union

_PARTIAL_RE = re.compile(r"^\.(?P<name>.+)\.partial-(?P<pid>\d+)(?:-\d+)?$")


def partial_path(dest: Path) -> Path:
    dest = Path(dest)
    return dest.with_name(".%s.partial-%d-%d" % (dest.name, os.getpid(), threading.get_ident()))


def _writer_alive(pid: int, partial: Path) -> bool:
    """Whether the process that named `partial` may still be writing it. A
    process with that number that started after the partial was last written
    is a different process reusing the number (Windows reuses them quickly)."""
    if pid == os.getpid():
        return True
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        return psutil.Process(pid).create_time() <= partial.stat().st_mtime + 1.0
    except Exception:
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            return True      # cannot tell: leave it alone


def sweep_partials(folder: Path, name_re: Optional[str] = None) -> list:
    """Remove partial files a killed writer left in `folder`. Only files this
    module names (".<name>.partial-<pid>") whose process is gone; `name_re`
    narrows them to the destinations a caller owns. Returns what was removed."""
    removed = []
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return removed
    want = re.compile(name_re) if name_re else None
    for p in entries:
        m = _PARTIAL_RE.match(p.name)
        if not m or (want and not want.search(m.group("name"))):
            continue
        if _writer_alive(int(m.group("pid")), p):
            continue
        try:
            p.unlink()
            removed.append(p)
        except OSError:
            pass
    return removed


def write_whole(dest: Union[str, Path], data: Optional[bytes] = None, *,
                writer: Optional[Callable] = None) -> Path:
    """Write `data` (or whatever `writer(fileobj)` writes) to `dest` whole.

    The parent folder is created. On any error the partial file is removed and
    `dest` is left as it was."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = partial_path(dest)
    try:
        with open(tmp, "wb") as f:
            if writer is not None:
                writer(f)
            else:
                f.write(data or b"")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return dest


def write_whole_text(dest: Union[str, Path], text: str, encoding: str = "utf-8") -> Path:
    return write_whole(dest, text.encode(encoding))
