"""Where a voice stage's start-up time goes, and the last starts on record.

Each voice stage (the whisper-cuda ear, the kokoro-cuda mouth, the voice
front) reports one timing breakdown when it starts: process spawn, the
interpreter and imports before the load, the CUDA initialisation, the model
load (its path, its size, the effective read rate, which says whether the
file came from the disk cache), the first warm-up inference, and the total.
One line goes to the log and the last ``KEEP`` records are kept in
``voice_start_timing.json`` beside the measured working sets, next to the
last whole-start duration the page quotes ("Warming up the voice - about N s").

Measurements only: nothing here estimates, and nothing here touches the
network or the GPU.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("friday.voice_start_profile")

#: How many stage records the file keeps.
KEEP = 20
#: A model file read faster than this was served from the OS file cache; a
#: slower rate is a cold read from disk (an NVMe cold read is ~1,500 MiB/s,
#: but a model load also spends its time on tensors, so this is a hint).
WARM_READ_MIB_S = 600.0

_LOCK = threading.Lock()


def timing_path() -> Path:
    from agent_friday.services import voice_workers as _vw
    return _vw._working_sets_path().parent / "voice_start_timing.json"


@contextmanager
def timed(into: dict, key: str):
    """``with timed(prof, "import_s"):`` stores the block's seconds in `into`."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        into[key] = round(time.perf_counter() - t0, 3)


def file_mib(path) -> float | None:
    """Size of a model file, or of a model directory's files, in MiB."""
    try:
        p = Path(path)
        if p.is_file():
            return round(p.stat().st_size / 1048576.0, 1)
        if p.is_dir():
            return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1048576.0, 1)
    except Exception:
        pass
    return None


def read_hint(size_mib, load_s) -> dict:
    """The effective rate the model loaded at, and whether that reads as the
    OS file cache (``warm``) or a cold disk read (``cold``)."""
    try:
        if size_mib and load_s and load_s > 0:
            rate = round(float(size_mib) / float(load_s), 1)
            return {"read_mib_s": rate,
                    "disk_cache": "warm" if rate >= WARM_READ_MIB_S else "cold"}
    except Exception:
        pass
    return {}


def finish(stage: str, engine: str, prof: dict, *, total_s=None) -> dict:
    """Complete a stage record (stage, engine, when, total) and return it."""
    rec = {"stage": stage, "engine": engine, "at": round(time.time(), 1)}
    rec.update({k: v for k, v in (prof or {}).items() if v is not None})
    if total_s is not None:
        rec["total_s"] = round(float(total_s), 3)
    if "read_mib_s" not in rec:
        rec.update(read_hint(rec.get("file_mib"), rec.get("model_load_s")))
    return rec


_ORDER = ("spawn_s", "startup_s", "import_s", "cuda_init_s", "model_load_s",
          "warmup_s", "total_s")


def line(rec: dict) -> str:
    """One log line: ``voice start ear/whisper-cuda: spawn 0.1 s, ...``."""
    names = {"spawn_s": "spawn", "startup_s": "interpreter+pipe", "import_s": "import",
             "cuda_init_s": "cuda init", "model_load_s": "model load",
             "warmup_s": "warm-up", "total_s": "total"}
    parts = ["%s %.1f s" % (names[k], rec[k]) for k in _ORDER if isinstance(rec.get(k), (int, float))]
    where = ""
    if rec.get("model_path"):
        where = " [%s%s%s]" % (
            rec["model_path"],
            (", %.0f MiB" % rec["file_mib"]) if rec.get("file_mib") else "",
            (", %s read %.0f MiB/s" % (rec["disk_cache"], rec["read_mib_s"]))
            if rec.get("read_mib_s") else "")
    return "voice start %s/%s: %s%s" % (rec.get("stage"), rec.get("engine"),
                                         ", ".join(parts) or "no timings", where)


def path_of(rec: dict) -> str:
    """Which way a stage was started: ``cold``, ``prepared`` (spawned and
    prepared during the park), ``warm`` (took a pre-imported worker) or
    ``prepared+warm``."""
    prepared = rec.get("prepared_s") is not None
    warm = rec.get("preimported_s") is not None
    return "prepared+warm" if prepared and warm else "prepared" if prepared else         "warm" if warm else "cold"


def recent_totals(stage: str, path: str, *, n: int = 5, file: Path | None = None) -> list:
    """The last `n` measured totals (seconds) of `stage` started the `path` way."""
    try:
        data = json.loads((file or timing_path()).read_text(encoding="utf-8"))
        rows = [r for r in (data.get("stages") or [])
                if isinstance(r, dict) and r.get("stage") == stage and path_of(r) == path
                and isinstance(r.get("total_s"), (int, float))]
        return [float(r["total_s"]) for r in rows[-n:]]
    except Exception:
        return []


def update(path: Path, **fields) -> None:
    """Merge `fields` into the timing file (read-modify-write, never raises)."""
    try:
        with _LOCK:
            data = {}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except Exception:
                data = {}
            data.update(fields)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        log.debug("voice start timing not saved: %s", e)


def record(rec: dict, path: Path | None = None) -> dict:
    """Log `rec` as one line and keep it among the last ``KEEP`` stage records."""
    log.info(line(rec))
    try:
        p = path or timing_path()
        with _LOCK:
            try:
                stages = list(json.loads(p.read_text(encoding="utf-8")).get("stages") or [])
            except Exception:
                stages = []
        update(p, stages=(stages + [rec])[-KEEP:])
    except Exception as e:  # noqa: BLE001
        log.debug("voice start record not kept: %s", e)
    return rec
