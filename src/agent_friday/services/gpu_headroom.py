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
import time

_log = logging.getLogger("friday.gpu_headroom")

# LAST-RESORT FALLBACK ONLY -- not a display-reserve constant of record.
# `_resolve_default_reserve()` below reads the reconciled figure from
# `headroom_contract.resolve_display_reserve()` -- the same one
# `Arbiter.grant()`'s R-DISPLAY-RESERVE check now uses -- so `check()` and
# `display_at_risk()` stop disagreeing with the gate that actually stands
# between a lease and the display driver (`docs/design/headroom.md` §2.2,
# HR3: one reserve, defined once). This is what those two functions fall
# back to only when the profile or the contract module cannot be reached at
# all (e.g. before `hardware_profile` has ever detected a machine) --
# deliberately private and NOT named with "DISPLAY_RESERVE" so it reads as
# the emergency fallback it is, not a second definition.
#
# 1024 MiB covers the Windows compositor plus a browser's GPU process, which
# is what is actually running when Stephen is at the machine.
_FALLBACK_RESERVE_MIB = 1024

_CACHE: dict = {"ts": 0.0, "data": None}
_CACHE_TTL_S = 2.0


def _resolve_default_reserve() -> int:
    """The contract's reconciled display reserve; `_FALLBACK_RESERVE_MIB`
    only if it cannot be resolved. See the module-level comment above."""
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services.headroom_contract import (
            resolve_display_reserve)
        return int(resolve_display_reserve(hwp.get())["mib"])
    except Exception:
        return _FALLBACK_RESERVE_MIB


def gpu_memory() -> list[dict] | None:
    """Per-GPU {name, total_mib, used_mib, free_mib}, or None if unknowable.

    None is a real answer and callers must treat it as "cannot verify", not as
    "plenty free" — the whole point is to fail toward leaving the desktop alone.

    Reads `machine_monitor.gpu_rows()` rather than calling `nvidia-smi`
    itself (`docs/design/headroom.md` §4.3, §12 Phase 1: one nvidia-smi call
    for the whole tree, not two disagreeing ones). `machine_monitor` extends
    the same query string this function used to own with the four fields the
    monitor's thrash signature needs; this function keeps its own short
    cache on top so a caller that only wants the four original fields is not
    coupled to the monitor's cache lifetime.
    """
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL_S:
        return _CACHE["data"]
    try:
        from agent_friday.services import machine_monitor as mm
        rows = mm.gpu_rows()
    except Exception as e:
        _log.debug("machine_monitor unavailable: %s", e)
        return None
    if not rows:
        return None
    gpus = [{"name": r.get("name"), "total_mib": r.get("total_mib"),
            "used_mib": r.get("used_mib"), "free_mib": r.get("free_mib")}
           for r in rows]
    _CACHE.update({"ts": now, "data": gpus or None})
    return gpus or None


def check(need_mib: int, *, reserve_mib: int | None = None) -> dict:
    """Is there room for `need_mib` WITHOUT eating the display's reserve?

    Returns a dict that is meant to be shown to the user, not just branched on:
      ok        bool | None   — None means "could not verify"
      reason    str           — a sentence a person can read
      free_mib, usable_mib, reserve_mib, need_mib, gpu
    """
    reserve = _resolve_default_reserve() if reserve_mib is None else reserve_mib
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
    thr = _resolve_default_reserve() if threshold_mib is None else threshold_mib
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
