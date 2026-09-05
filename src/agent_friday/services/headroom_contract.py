"""
headroom_contract — the one honest display-reserve figure.

WHAT THIS FILE IS. `docs/design/headroom.md` §4.2 specifies a full three-level
Headroom Contract: `working` / `away` / `yield`, each with a VRAM-slack floor
and a RAM-available floor, on top of the display reserve. That full contract
is **D1** (spec §13) — the default posture, and the exact slack/RAM numbers,
are a trade between Friday's quality and the user's machine that only Stephen
makes. **D1 is not decided, and this file does not build the Contract.**

What this file DOES build is the one piece of §4.2 that needs no new number
and is not blocked on D1: the **display reserve** alone. §2.2 of the spec
found six reserve constants for five concepts, VERIFIED at these exact
lines, at commit `f000f07`:

    gpu_headroom.DEFAULT_DISPLAY_RESERVE_MIB          1,024  (fixed default;
                                                                 now a private
                                                                 last-resort
                                                                 fallback, see
                                                                 gpu_headroom.py)
    hardware_profile.DEFAULT_VRAM_BASELINE_MIB[win]    1,024  (unmeasured floor)
    hardware_profile.MIN_DISPLAY_RESERVE_MIB[win]      2,560  (measured floor)
    hardware_profile.display_reserve_mib()               256  (base, unclamped)
    residency_policy.VRAM_RESERVE_MIB (R3)             1,024  (planner slack,
                                                                 a DIFFERENT concept)
    model_plan.DISPLAY_RESERVE_GIB                    2.5 GiB (installer rung,
                                                                 a DIFFERENT concept)

The bug this file fixes: `Arbiter.grant()`'s R-DISPLAY-RESERVE check — the
last gate between a lease and the display driver — called
`hardware_profile.vram_headroom()`, which computed its reserve from
`display_reserve_mib()` directly: 256 MiB on a single-monitor Windows box,
with NO clamp to `MIN_DISPLAY_RESERVE_MIB`. The planner, meanwhile, budgets
against `effective_baseline_mib()`, which DOES fold in the clamp (by way of
`live_display_mib()`'s own `max(val, MIN_DISPLAY_RESERVE_MIB[...])`) once a
live sample has been written into the profile. So the number the gate
actually enforces (256) and the number the planner assumes (>= 2,560) have
disagreed since the day both were written. The 2026-08-17 monitor loss
happened at 322 MiB free; the gate as written today would still pass at that
level.

`resolve_display_reserve()` below is that reconciliation, and nothing more:
it takes the SAME formula `hardware_profile.display_reserve_mib()` already
computes (256 base + per-monitor + HiDPI + indirect-adapter increments) and
clamps it up to `MIN_DISPLAY_RESERVE_MIB` for the machine's OS family — the
exact clamp `live_display_mib()` already applies to its own reading, just
applied to the OTHER of the two reserve computations that fed into
`vram_headroom()`'s hole. No new number is invented; nothing here is a
`working`/`away`/`yield` level, a VRAM slack, or a RAM-available floor.

`residency_policy.VRAM_RESERVE_MIB` (R3, planner slack ON TOP of the
baseline) and `model_plan.DISPLAY_RESERVE_GIB` (which installer rung to
offer, before the app — and therefore this module — exists) are NOT folded
in here. They are different concepts for different purposes; see the
cross-reference comment left at each site for why, and this module's own
docstring is the place a future reader lands to see all five sites named
together.
"""
from __future__ import annotations

import time

from agent_friday.services import hardware_profile as hwp

# `hardware_profile.display_reserve_mib()` calls `detect_displays()`, which
# spawns PowerShell on every call and has no cache of its own (unlike
# `live_display_mib()`'s 20s `_DISPLAY_CACHE`). This function is called from
# the monitor's 5s-under-lease cadence (spec §4.3) and from scheduler ticks,
# so it needs the same shape of cache or it reintroduces a subprocess spawn
# on a hot path. Keyed by OS family so a profile carried across machines
# (tests, a second box) cannot serve a stale reading for the wrong one.
_CACHE: dict = {"ts": 0.0, "os_family": None, "result": None}
_CACHE_TTL_S = 20.0


def resolve_display_reserve(profile: dict) -> dict:
    """The one honest display-reserve figure, in MiB, for `profile`.

    Returns `{mib, basis, sources}`:
      mib      int  — what `Arbiter.grant()`'s R-DISPLAY-RESERVE check (and
                       every other of the six sites) should require free
                       before it lets a lease take the card.
      basis    str  — "formula" when the live per-monitor/HiDPI/indirect
                       sample already meets or exceeds the OS floor;
                       "floor_clamp" when the floor is what's binding (the
                       single-monitor Windows case this whole reconciliation
                       exists for).
      sources  dict — the two inputs, so a refusal or a panel can show its
                       arithmetic (HR12) rather than just the answer.

    PURE with respect to `profile` (only `os.family` is read from it) but NOT
    pure with respect to the machine: it calls `hardware_profile.
    display_reserve_mib()`, which probes attached displays live. Cached for
    `_CACHE_TTL_S` for exactly that reason.
    """
    fam = (profile.get("os") or {}).get("family", "linux")
    now = time.time()
    cached = _CACHE["result"]
    if (cached is not None and _CACHE["os_family"] == fam
            and (now - _CACHE["ts"]) < _CACHE_TTL_S):
        return dict(cached)

    formula_mib = int(hwp.display_reserve_mib())
    floor_mib = int(hwp.MIN_DISPLAY_RESERVE_MIB.get(fam, 512))

    if formula_mib >= floor_mib:
        mib, basis = formula_mib, "formula"
    else:
        mib, basis = floor_mib, "floor_clamp"

    result = {
        "mib": mib,
        "basis": basis,
        "sources": {
            "display_reserve_mib_formula": formula_mib,
            "min_display_reserve_mib": floor_mib,
            "os_family": fam,
        },
    }
    _CACHE.update({"ts": now, "os_family": fam, "result": result})
    return dict(result)
