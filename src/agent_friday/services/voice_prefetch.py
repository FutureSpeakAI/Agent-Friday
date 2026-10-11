"""Read a model file once so the load that follows finds it in the OS file cache.

A cold model read is the slow part of a voice stage on this machine (the front's
441 MiB gguf at 66-85 MiB/s is 5-7 s; Kokoro's 312 MiB at the same rate is ~4 s).
The call spends ~10-14 s parking the brain first, during which the disk is idle,
so the files are read then. The cost is page cache: reclaimable, never VRAM, and
bounded here by a share of the RAM that is free right now. Nothing is kept by
this module and nothing is written.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger("friday.voice_prefetch")

CHUNK = 8 * 1024 * 1024
#: Never read more than this share of the RAM that is free now: the cache the
#: read fills is reclaimable, but evicting other programs' pages to fill it is
#: not a favour.
MAX_SHARE_OF_FREE = 0.5


def _free_mib():
    try:
        import psutil
        return int(psutil.virtual_memory().available // (1024 * 1024))
    except Exception:
        return None


#: Bounds on one call's read: bytes and wall time. The front's gguf is 441 MiB.
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_MAX_SECONDS = 20.0


def read_into_cache(paths, *, stop=None, max_share=MAX_SHARE_OF_FREE,
                    max_bytes=DEFAULT_MAX_BYTES, max_seconds=DEFAULT_MAX_SECONDS) -> dict:
    """Sequentially read each file (or each file under a directory) and drop the
    bytes. Returns ``{"files", "bytes", "seconds", "mib_s", "skipped"}``. Never
    raises. ``stop`` (a threading.Event) ends the read early."""
    t0 = time.perf_counter()
    files = []
    for p in paths:
        try:
            p = Path(p)
            if p.is_file():
                files.append(p)
            elif p.is_dir():
                files.extend(sorted(f for f in p.rglob("*") if f.is_file()))
        except Exception:
            continue
    total = 0
    try:
        total = sum(f.stat().st_size for f in files)
    except Exception:
        pass
    if total > max_bytes:
        return {"files": 0, "bytes": 0, "seconds": 0.0, "mib_s": None,
                "skipped": "more than %d MiB to read" % (max_bytes // 2**20)}
    free = _free_mib()
    if free is not None and total > free * 1024 * 1024 * max_share:
        log.info("prefetch skipped: %d MiB to read against %d MiB free", total // 2**20, free)
        return {"files": 0, "bytes": 0, "seconds": 0.0, "mib_s": None,
                "skipped": "not enough free RAM for the cache"}
    done = 0
    for f in files:
        try:
            with open(f, "rb", buffering=0) as fh:
                while not (stop is not None and stop.is_set()):
                    if time.perf_counter() - t0 > max_seconds:
                        break
                    b = fh.read(CHUNK)
                    if not b:
                        break
                    done += len(b)
        except Exception as e:  # noqa: BLE001
            log.debug("prefetch of %s stopped: %s", f, e)
    dt = max(1e-6, time.perf_counter() - t0)
    return {"files": len(files), "bytes": done, "seconds": round(dt, 3),
            "mib_s": round(done / 2**20 / dt, 1) if done else None, "skipped": ""}
