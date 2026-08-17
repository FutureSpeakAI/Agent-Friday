"""
gpu_headroom — do not take the card out from under the desktop.

WHY THIS EXISTS. Stephen lost a monitor to VRAM pressure on 2026-08-17: the
second display dropped off Windows entirely while the GPU was full. The
residency budget reserves VRAM for models; the current evidence is that it
reserves nothing for the compositor, which needs its own hundreds of megabytes
to keep a desktop drawn. A model that fits "the free VRAM" can therefore fit
by taking the space the screen was using.

Measured live on this machine while writing this module: 12,282 MiB total,
11,775 used, **238 free** — with one 12B seat resident at 7,718 MiB. That is
not a theoretical margin, it is a machine one allocation away from dropping a
display again.

So: any job that is about to claim a large amount of VRAM asks here first, and
a caller that cannot get headroom does not proceed silently. It says so and
runs somewhere else, or waits.

This module only ever REPORTS. It never evicts, never kills, and never takes
the decision away from the Arbiter — a headroom checker that started freeing
memory on its own would be a second, quieter allocator fighting the first.
"""
from __future__ import annotations

import logging
import subprocess
import time

_log = logging.getLogger("friday.gpu_headroom")

# What the desktop needs to stay drawn, over and above whatever it is using
# right now. A conservative floor rather than a measurement, because the cost
# of being wrong is asymmetric: too much reserve costs a slower seat, too
# little costs the user their screen.
#
# 1024 MiB covers the Windows compositor plus a browser's GPU process, which
# is what is actually running when Stephen is at the machine. Overridable, and
# the override is recorded wherever a decision cites it.
DEFAULT_DISPLAY_RESERVE_MIB = 1024

_CACHE: dict = {"ts": 0.0, "data": None}
_CACHE_TTL_S = 2.0


def gpu_memory() -> list[dict] | None:
    """Per-GPU {name, total_mib, used_mib, free_mib}, or None if unknowable.

    None is a real answer and callers must treat it as "cannot verify", not as
    "plenty free" — the whole point is to fail toward leaving the desktop alone.
    """
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL_S:
        return _CACHE["data"]
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return None
        gpus = []
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            gpus.append({"name": parts[0], "total_mib": int(parts[1]),
                         "used_mib": int(parts[2]), "free_mib": int(parts[3])})
        _CACHE.update({"ts": now, "data": gpus or None})
        return gpus or None
    except Exception as e:
        _log.debug("nvidia-smi unavailable: %s", e)
        return None


def check(need_mib: int, *, reserve_mib: int | None = None) -> dict:
    """Is there room for `need_mib` WITHOUT eating the display's reserve?

    Returns a dict that is meant to be shown to the user, not just branched on:
      ok        bool | None   — None means "could not verify"
      reason    str           — a sentence a person can read
      free_mib, usable_mib, reserve_mib, need_mib, gpu
    """
    reserve = DEFAULT_DISPLAY_RESERVE_MIB if reserve_mib is None else reserve_mib
    gpus = gpu_memory()
    if not gpus:
        return {"ok": None, "reason": ("I could not read GPU memory "
                                       "(nvidia-smi unavailable), so I cannot "
                                       "promise the display has room."),
                "need_mib": need_mib, "reserve_mib": reserve}
    g = max(gpus, key=lambda x: x["free_mib"])
    usable = g["free_mib"] - reserve
    ok = usable >= need_mib
    if ok:
        reason = (f"{g['free_mib']} MiB free on the {g['name']}; "
                  f"{need_mib} MiB needed with {reserve} MiB held back for the "
                  f"desktop — room to spare.")
    elif usable < 0:
        # The card is ALREADY inside the display's reserve. Saying "N MiB
        # short" here reads as a near miss; it is not one.
        reason = (f"Only {g['free_mib']} MiB free on the {g['name']} — already "
                  f"below the {reserve} MiB the desktop wants for itself, "
                  f"before anything is loaded. There is no room for the "
                  f"{need_mib} MiB this needs, and taking it is how a monitor "
                  f"drops off Windows.")
    else:
        reason = (f"Only {g['free_mib']} MiB free on the {g['name']}. After "
                  f"holding back {reserve} MiB so the display keeps its memory, "
                  f"that leaves {usable} MiB — {need_mib - usable} MiB short of "
                  f"the {need_mib} MiB this needs. Taking it anyway is how a "
                  f"monitor drops off Windows.")
    return {"ok": ok, "reason": reason, "free_mib": g["free_mib"],
            "usable_mib": usable, "reserve_mib": reserve,
            "need_mib": need_mib, "gpu": g["name"], "total_mib": g["total_mib"],
            "used_mib": g["used_mib"]}


def display_at_risk(threshold_mib: int | None = None) -> dict:
    """Is the card ALREADY too full for comfort, whatever we do next?

    Separate from check() because it answers a different question: check() asks
    "may I take more", this asks "is the machine already in the state that cost
    him a monitor". Worth surfacing even when Friday is about to take nothing.
    """
    thr = DEFAULT_DISPLAY_RESERVE_MIB if threshold_mib is None else threshold_mib
    gpus = gpu_memory()
    if not gpus:
        return {"at_risk": None, "reason": "GPU memory could not be read."}
    g = max(gpus, key=lambda x: x["free_mib"])
    at_risk = g["free_mib"] < thr
    return {
        "at_risk": at_risk,
        "free_mib": g["free_mib"], "total_mib": g["total_mib"],
        "gpu": g["name"], "threshold_mib": thr,
        "reason": (f"{g['free_mib']} MiB free of {g['total_mib']} on the "
                   f"{g['name']}" + (f" — below the {thr} MiB the desktop wants. "
                                     f"A display can drop at this level."
                                     if at_risk else " — the desktop has room.")),
    }
