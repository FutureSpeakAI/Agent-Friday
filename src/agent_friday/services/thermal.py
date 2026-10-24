"""Is the machine too hot for heavy local work? (BD-25, NS-33.8-1 / NS-11.10-6)

Heavy work Friday starts on her own (indexing the Library, the nightly practice trials, the
SkillOpt night run, Laya's personal refit) already waits while the PC is on battery. It now
also waits while a GPU is too hot, and says so in the same place: "paused: the GPU is too
hot (88 °C); waiting until it is under 80 °C". Work the owner starts in a conversation is
not held here.

What is read: the GPU temperature `nvidia-smi` reports (services/machine_monitor.gpu_rows,
the one cached nvidia-smi call). Where no temperature can be read (no NVIDIA GPU, an old
driver, a virtual card), nothing is claimed and nothing is held: a hold needs a reading.

Hysteresis: a GPU counts as hot from LIMIT_C, and stays hot until it is back under
RESUME_BELOW_C, so a job does not start and stop at every degree around the line.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Tuple

#: Hold heavy background work from this GPU temperature (°C). NVIDIA desktop cards start
#: slowing themselves at about 83-87 °C; heavy background work waits before that happens.
LIMIT_C = 85
#: ...and resume once every GPU is back under this.
RESUME_BELOW_C = 80

#: Tests replace this with a function returning the hottest reading (°C) or None.
reading_override: Optional[Callable[[], Optional[int]]] = None

_LOCK = threading.Lock()
_STATE = {"hot": False, "temp_c": None}

#: A machine that cannot report a temperature is asked again only this often, so the check
#: stays cheap where it is polled (a Library hold looks every second): no nvidia-smi, or one
#: that fails or hangs, is not run again and again.
UNREADABLE_RETRY_S = 60.0
_UNREADABLE = {"until": 0.0}


def hottest_gpu_c() -> Optional[int]:
    """The hottest GPU temperature in °C, or None when none can be read."""
    if reading_override is not None:
        return reading_override()
    if time.monotonic() < _UNREADABLE["until"]:
        return None
    try:
        from agent_friday.services import machine_monitor
        rows = machine_monitor.gpu_rows() or []
    except Exception:
        rows = []
    temps = [r.get("temp_c") for r in rows if isinstance(r, dict) and r.get("temp_c") is not None]
    if not temps:
        _UNREADABLE["until"] = time.monotonic() + UNREADABLE_RETRY_S
        return None
    return max(temps)


def too_hot() -> Tuple[bool, str]:
    """(hot, reason). The reason is shown to the owner where the job waits."""
    t = hottest_gpu_c()
    with _LOCK:
        if t is None:
            # Nothing is held without a reading; a reading missed while cooling down does not
            # end the cool-down (the next reading decides against the resume line).
            return False, ""
        hot = (t >= RESUME_BELOW_C) if _STATE["hot"] else (t >= LIMIT_C)
        _STATE.update(hot=hot, temp_c=t)
    if hot:
        return True, "the GPU is too hot (%d °C); waiting until it is under %d °C" % (t, RESUME_BELOW_C)
    return False, ""


def reset_for_tests() -> None:
    global reading_override
    reading_override = None
    _UNREADABLE["until"] = 0.0
    with _LOCK:
        _STATE.update(hot=False, temp_c=None)
