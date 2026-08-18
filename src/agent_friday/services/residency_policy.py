"""
Agent Friday — ResidencyPolicy

A pure, deterministic function:

    plan(HardwareProfile, [CatalogEntry], overrides) -> PlacementPlan

No I/O, no clock, no randomness, no network. The same three inputs always
produce the same plan, byte for byte — which is what makes a golden-plan test
meaningful and what lets a refusal be reproduced from a bug report.

The rules live in RULES as inspectable data with stable ids, so every refusal
cites one and a human can read the policy without reading the code. Thresholds
and their justifications are in docs/design/residency-policy.md.

Two shapes of output, and both matter equally:
  * a Placement — role, model, device, num_ctx, offload, pinned vs leased;
  * a Refusal — role, model, rule id, and the arithmetic that produced it.

A seat that cannot be filled is `None` WITH a refusal. It is never silently
omitted, because "no local heavy model on a 16 GB laptop" is a true and useful
answer, while an empty key is just a hole someone will later fill with a guess.
"""
from __future__ import annotations

POLICY_VERSION = 1

# `sidekick_heavy` is the second small seat: a more capable cheap model for
# small-but-harder work, distinct from `sidekick` (the fastest thing that fits).
# On the reference instance that is e4b (99.93 tok/s, 3081 MiB) beside e2b
# (166.13 tok/s, 1763 MiB) — both genuinely useful, for different jobs.
ROLES = ("interactive_brain", "heavy_hitter", "sidekick", "sidekick_heavy",
         "embedder", "stt", "tts", "image")

# ── Rules as data ────────────────────────────────────────────────────────────

VRAM_RESERVE_MIB = 1024          # R3
RAM_CEILING_HARD = 0.75          # R2
RAM_CEILING_TARGET = 0.65        # R2
DISK_FLOOR_MIB = 10 * 1024       # R8

# Expert layers held on CPU for an offloaded MoE. Measured, not guessed:
# the reference sweep peaked inside the VRAM budget at 20 (27.80 tok/s,
# 9802 MiB) and collapsed at 12 (14.94 tok/s, host RAM 31.5 of 31.9 GB).
MOE_CPU_LAYERS_DEFAULT = 20

# The sweep, as data: (n_cpu_moe, heavy_vram_mib, tok_s).
#
# Re-measured 2026-08-15 WITH THE SIDEKICK RESIDENT, which is the condition
# R10 created and therefore the condition the number has to hold under. The
# figures are the heavy model's own GPU footprint, so they are directly
# comparable to the lease budget.
#
# The earlier sweep — (16, 10170, 31.34) and (20, 9802, 27.80) — is not carried
# forward. It measured TOTAL GPU on an otherwise idle machine with no sidekick,
# which is a different quantity against a different budget; mixing the two
# bases in one table would produce a number that looks measured and is not.
#
# What this sweep overturned: the curve is NOT linear. A fit through the old
# two points asked for 32 layers; 28 turned out to put only 2462 MiB on the
# card against an 8186 MiB budget — wasting 5.7 GB to run slower. 18 fits the
# budget and is the best point on this evidence.
#
# What it did NOT establish, despite looking like it might: the sidekick
# answered in 1.24 s at 18 layers and 22-25 s at 20, 22, 24 and 28. That is not
# a CPU-contention gradient — 18 and 22 hold near-identical VRAM (7973 vs 7969)
# and differ by 23 s. Two things point the same way instead:
#   * 22-25 s matches the e2b's measured 20.97 s COLD LOAD almost exactly;
#   * the n_cpu_moe=20 row reads 6014 MiB where its neighbours read ~7900, and
#     6014 + 1810 (the sidekick) = 7824. The heavy figure is computed as
#     (used - baseline), so a row that is low by exactly one sidekick is a row
#     where the sidekick was NOT resident when the GPU was sampled.
# So the likeliest reading is that Ollama evicts the sidekick under memory
# pressure and each probe pays a reload. R10 stops the ARBITER evicting it; it
# cannot stop the daemon — the same degraded-pin problem that leaves the brain
# unresident (docs/audits/symphony-live-2026-08-15.md §4). Consistent with two
# independent signals, still not directly confirmed.
#
# One run per candidate: a direction, not a settled number.
MOE_SWEEP = [(18, 7973, 10.58), (20, 6014, 11.47), (22, 7969, 10.30),
             (24, 7895, 9.44), (28, 2462, 10.29)]


def n_cpu_moe_for_budget(budget_mib: int | None) -> tuple[int, str]:
    """(layers, basis) — how many expert layers must sit on the CPU to fit.

    This became a function rather than a constant when R10 took 1811 MiB off
    the lease budget: 20 layers lands at 9802 MiB, which fit the old 9997 MiB
    lease and does not fit the 8186 MiB one. Something had to give, and the
    honest thing is for the plan to say what.

    Interpolates the measured sweep and extrapolates beyond it at the same
    MiB-per-layer rate, reporting "extrapolated" when it has left the measured
    range. An extrapolated operating point is a starting guess for a sweep, not
    a result — the number to trust is the one the next sweep records.
    """
    if budget_mib is None:
        return MOE_CPU_LAYERS_DEFAULT, "measured"
    for layers, vram, _ in MOE_SWEEP:
        if vram <= budget_mib:
            return layers, "measured"
    (lo_l, lo_v, _), (hi_l, hi_v, _) = MOE_SWEEP[0], MOE_SWEEP[-1]
    per_layer = (lo_v - hi_v) / float(hi_l - lo_l) or 1.0
    extra = int(round((hi_v - budget_mib) / per_layer))
    return max(hi_l + extra, hi_l + 1), "extrapolated"

RULES = [
    {"id": "R1", "name": "os-reserve",
     "text": "OS memory reserve subtracted before any RAM budget.",
     "thresholds": {"windows_mib": 6144, "linux_mib": 4096,
                    "darwin_mib": 4096}},
    {"id": "R2", "name": "ram-ceiling",
     "text": "Pinned host RAM + OS reserve stays under 75% of physical RAM; "
             "the planner targets 65%.",
     "thresholds": {"hard": RAM_CEILING_HARD, "target": RAM_CEILING_TARGET}},
    {"id": "R3", "name": "vram-budget",
     "text": "Per GPU: weights + KV at the configured num_ctx + buffers stay "
             "under VRAM minus 1 GB, with the measured idle baseline counted "
             "against the same budget.",
     "thresholds": {"reserve_mib": VRAM_RESERVE_MIB}},
    {"id": "R4", "name": "no-tensor-split",
     "text": "One model never spans two GPUs. Aggregate VRAM is not a "
             "resource.", "thresholds": {}},
    {"id": "R5", "name": "image-exclusive",
     "text": "Image generation takes an exclusive GPU lease, unless a second "
             "GPU exists, in which case it takes one and the others keep "
             "serving.", "thresholds": {}},
    {"id": "R6", "name": "moe-offload",
     "text": "MoE models may expert-offload. Dense models must fit or be "
             "demoted.", "thresholds": {}},
    {"id": "R7", "name": "explicit-num-ctx",
     "text": "num_ctx is explicit for every placement; never a backend "
             "default, in either direction.", "thresholds": {}},
    {"id": "R8", "name": "disk-headroom",
     "text": "Refuse a load if free disk afterwards would fall below "
             "max(10 GB, artifact size). On Windows the pagefile makes a "
             "large resident model a disk consumer.",
     "thresholds": {"floor_mib": DISK_FLOOR_MIB}},
    {"id": "R9", "name": "pinned-not-delegated",
     "text": "A pinned seat is not delegated to a backend scheduler that "
             "evicts on its own criteria.", "thresholds": {}},
    {"id": "R10", "name": "sidekick-always-resident",
     "text": "The sidekick seat survives every lease. Friday stays awake and "
             "answering while a heavy or image job holds the card, so a lease "
             "budget is the GPU budget MINUS the retained sidekick.",
     "thresholds": {}},
]

RULE_BY_ID = {r["id"]: r for r in RULES}

# Default contexts per role. R7: something explicit always wins over a
# backend default. Chosen from the measured KV curve -- on the gemma4 family
# context is nearly free (12b: 7690 MiB at 4k vs 8001 at 16k), so the
# interactive seat can afford a generous window.
# R7 says every placement gets an explicit context. It does NOT say the number
# may be picked from the VRAM curve alone — and that is the mistake this table
# used to make.
#
# A tool-using seat must hold the TOOL DEFINITIONS. Measured 2026-08-15: the
# 52-tool registry serialises to ~8 534 tokens, so a seat at 8192 truncates the
# tools before the conversation even starts. gemma4:e2b scored 8/10 on the
# structural gate at 8192 and 10/10 at a larger context — a context below the
# tool floor manufactures exactly the tool-calling failure the gate exists to
# detect.
#
# CORRECTED 2026-08-15. This constant used to be 32768, and the arithmetic
# behind it was wrong in a checkable way: it sized the window from the tool
# registry ALONE (~8534 tokens x4) and ignored the system prompt, which at
# ~11681 tokens is the LARGER of the two fixed costs. A 32768 seat therefore
# had ~12552 tokens of room — 38% of its own window. See services/
# context_budget.py, which measures both halves rather than assuming either.
#
# It stays as the floor rung and the dispatch fallback, not as the answer:
# `context_for()` computes the real number from overhead + room, per role.
TOOL_SEAT_NUM_CTX = 32768

# Rungs, not arbitrary integers: backends allocate KV in blocks, and a tidy
# number is one a human can recognise in `ollama ps` output or a bug report.
CONTEXT_LADDER = (8192, 16384, 32768, 65536, 131072, 262144)

# Below this a seat cannot hold the tools AND a short conversation, so it
# cannot do tool-using work at all. R7 says the number is explicit; this says
# it must also be sufficient, and a seat that cannot reach the floor is
# refused with the arithmetic rather than quietly given a window that will
# truncate its own tool definitions.
MIN_CONVERSATION_ROOM = 8192

# Conversation room wanted per role, on top of the fixed overhead. These are
# job descriptions, not sizes: the brain is the seat that reads documents and
# holds long conversations, so it gets the room; the sidekick answers reflexes
# and would only be spending budget the brain needs.
ROOM_TARGET = {
    "interactive_brain": 98304,
    "heavy_hitter": 24576,
    "sidekick_heavy": 24576,
    "sidekick": MIN_CONVERSATION_ROOM,
    "embedder": 0,
}

DEFAULT_NUM_CTX = {
    "interactive_brain": TOOL_SEAT_NUM_CTX,
    "heavy_hitter": TOOL_SEAT_NUM_CTX,
    "sidekick": TOOL_SEAT_NUM_CTX,
    "sidekick_heavy": TOOL_SEAT_NUM_CTX,
    "embedder": 2048,
}

# Seats that carry neither tool schemas nor a system prompt, and so are not
# subject to the overhead arithmetic at all.
NO_PROMPT_ROLES = frozenset({"embedder"})

# R10. Seats a lease may NOT take. Stephen, 2026-08-15: "keep e2b awake so
# Friday is always alive." A lease used to stand down the whole pinned set,
# which meant asking for depth made Friday mute for the duration — the machine
# looked hung rather than busy.
#
# This is not free and the plan must not pretend otherwise: on the reference
# instance the sidekick holds 1811 MiB, so a lease sees 8186 MiB instead of
# 9997 and the heavy model pushes more experts to the CPU. That cost is
# subtracted here, in the plan, rather than discovered at load time.
RETAINED_THROUGH_LEASE = frozenset({"sidekick"})


def retained_mib(seats: dict) -> int:
    """VRAM a lease cannot reclaim, because R10 keeps those seats resident."""
    total = 0
    for role in RETAINED_THROUGH_LEASE:
        s = seats.get(role)
        if s and str(s.get("device", "")).startswith("gpu"):
            total += s.get("vram_mib") or 0
    return total


def _lease_budget(budgets: list, seats: dict) -> int | None:
    """What a lease actually gets: the largest GPU, less the retained seats."""
    if not budgets:
        return None
    return max(b["available_mib"] for b in budgets) - retained_mib(seats)


def _refusal(role, model, rule_id, explanation, **numbers):
    return {"role": role, "model": model, "rule_id": rule_id,
            "rule": RULE_BY_ID[rule_id]["name"], "explanation": explanation,
            **numbers}


# ── Budgets ──────────────────────────────────────────────────────────────────

def gpu_budgets(profile: dict) -> list:
    """Per-GPU MiB available to models, after the reserve and the idle floor."""
    from agent_friday.services.hardware_profile import effective_baseline_mib
    fam = (profile.get("os") or {}).get("family", "linux")
    out = []
    for g in profile.get("gpus") or []:
        baseline = effective_baseline_mib(g, fam)
        out.append({
            "index": g["index"],
            "name": g.get("name"),
            "total_mib": g["vram_total_mib"],
            "baseline_mib": baseline,
            "available_mib": max(
                0, g["vram_total_mib"] - VRAM_RESERVE_MIB - baseline),
            "compute_class": g.get("compute_class"),
        })
    return out


def ram_budget(profile: dict) -> dict:
    total = (profile.get("ram") or {}).get("total_mib", 0)
    reserve = profile.get("os_reserve_mib") or 4096
    return {
        "total_mib": total,
        "os_reserve_mib": reserve,
        "hard_ceiling_mib": int(total * RAM_CEILING_HARD),
        "target_ceiling_mib": int(total * RAM_CEILING_TARGET),
        "available_hard_mib": max(0, int(total * RAM_CEILING_HARD) - reserve),
        "available_target_mib": max(
            0, int(total * RAM_CEILING_TARGET) - reserve),
    }


# ── Candidate selection ──────────────────────────────────────────────────────

def _vram_for(e: dict, num_ctx: int) -> int | None:
    """Measured VRAM at num_ctx from the entry's own rows; pessimistic."""
    rows = [m for m in (e.get("measured") or []) if m.get("vram_mib")]
    if not rows:
        return None
    exact = [m for m in rows if m.get("num_ctx") == num_ctx]
    if exact:
        return exact[0]["vram_mib"]
    above = [m for m in rows if (m.get("num_ctx") or 0) >= num_ctx]
    if above:
        return min(above, key=lambda m: m["num_ctx"])["vram_mib"]
    return max(rows, key=lambda m: m["num_ctx"])["vram_mib"]


def _total_mib(e: dict, num_ctx: int) -> int | None:
    """Full resident footprint (VRAM + host), which is what MoE offload costs."""
    rows = [m for m in (e.get("measured") or []) if m.get("total_mib")]
    if not rows:
        return None
    exact = [m for m in rows if m.get("num_ctx") == num_ctx]
    if exact:
        return exact[0]["total_mib"]
    return max(rows, key=lambda m: m["num_ctx"])["total_mib"]


def _required_vram(e: dict, num_ctx: int) -> int | None:
    """VRAM needed to hold this model ENTIRELY on one GPU.

    This is deliberately `total_mib`, not the measured `vram_mib`. The measured
    figure is what the model *settled for* on the card it was measured on: the
    26b reports 8586 MiB of VRAM against a 17391 MiB total because it was
    measured on a 12 GB card that forced 51% of it onto the CPU. Carrying that
    number to a 24 GB fixture would claim the model needs 8.5 GB there, which
    is a property of the old card, not of the model.
    """
    total = _total_mib(e, num_ctx)
    return total if total is not None else _vram_for(e, num_ctx)


# ── Context sizing ───────────────────────────────────────────────────────────

def _rows(e: dict) -> list:
    """Measured rows as (num_ctx, required_mib), sorted.

    `required` is total_mib where it exists, matching `_required_vram`: the
    question a placement asks is "can one GPU hold this whole model", and the
    26b's 8586 MiB VRAM figure is what it settled for on a 12 GB card, not what
    it needs.
    """
    out = []
    for m in (e.get("measured") or []):
        mib = m.get("total_mib") or m.get("vram_mib")
        if mib and m.get("num_ctx"):
            out.append((m["num_ctx"], mib))
    return sorted(out)


def kv_slope_mib_per_token(e: dict) -> float | None:
    """MiB of VRAM per token of context, from the model's OWN measurements.

    Never a family constant. The gemma4 KV curve is flat enough that a constant
    would be indistinguishable from noise on the small models and badly wrong on
    the large ones — measured on the 12b, 32768 -> 131072 costs 96 MiB across
    98304 tokens, or 0.00098 MiB/token, while a naive "1 KiB per token" rule
    would predict 96 MiB for the same span on the e2b, whose whole KV allocation
    at 32768 is 48 MiB above its 8192 figure.

    Fitted on the two LARGEST measured contexts, because that is the end of the
    curve we extrapolate from. Returns None when fewer than two contexts were
    measured, or when the fit comes out non-positive — which happens for real:
    the 12b measured 8001 MiB at 16384 under Ollama 0.32.9 and 7718 at 32768
    under 0.32.11, so allocator changes across versions can dominate the signal
    the fit is looking for. A None result means "extrapolation is not supported
    by evidence", and the caller falls back to pessimism rather than to a guess.
    """
    rows = _rows(e)
    if len(rows) < 2:
        return None
    (c_lo, v_lo), (c_hi, v_hi) = rows[-2], rows[-1]
    span = c_hi - c_lo
    if span <= 0:
        return None
    slope = (v_hi - v_lo) / float(span)
    return slope if slope > 0 else None


def vram_estimate_at(e: dict, num_ctx: int) -> tuple[int | None, str]:
    """(MiB, basis) for holding this model at `num_ctx`.

    Three bases, and the caller records which one it got, because a plan built
    on an extrapolation and a plan built on a measurement deserve different
    amounts of trust:

      * "measured"     — a row exists at exactly this context, or at a larger
                         one (using a larger context's figure is safe: KV only
                         grows).
      * "extrapolated" — projected along the model's own KV slope.
      * "below-range"  — every measured row is at a SMALLER context and there
                         is no slope to project along, so the largest measured
                         figure is a LOWER BOUND, not an estimate. The caller
                         must treat it as "at least this much" and the plan
                         records the basis so nobody later mistakes it for a
                         measurement. The fix is to measure, not to invent a
                         markup: a made-up safety factor would be indis-
                         tinguishable in the plan from a real number.
    """
    rows = _rows(e)
    if not rows:
        return None, "unknown"
    exact = [v for c, v in rows if c == num_ctx]
    if exact:
        return exact[0], "measured"
    slope = kv_slope_mib_per_token(e)
    if slope is not None:
        c_anchor, v_anchor = rows[-1]
        est = v_anchor + slope * (num_ctx - c_anchor)
        # Never below the largest measured figure when projecting upward.
        if num_ctx > c_anchor:
            est = max(est, v_anchor)
        return int(round(est)), "extrapolated"
    above = [v for c, v in rows if c >= num_ctx]
    if above:
        return min(above), "measured"
    return max(v for _, v in rows), "below-range"


def context_for(role: str, e: dict, budget_mib: int | None,
                overhead_tokens: int) -> dict:
    """The largest ladder rung this seat can afford, and why.

    Sized from the WHOLE prompt: overhead (system prompt + tool schemas) plus
    the conversation room the role's job actually needs. The previous rule sized
    from the tool schemas alone and produced a seat that spent 62% of its window
    on things the user never sees.

    Returns a dict, always — a refusal here is information, not an exception:
        {num_ctx, basis, vram_mib, want, floor, capped_by, room_tokens}
    `num_ctx` is None only when even the floor rung will not fit, and then
    `capped_by` says which constraint refused it.
    """
    if role in NO_PROMPT_ROLES:
        # An embedder carries neither tool schemas nor a system prompt, so the
        # overhead this function exists to account for simply is not there.
        c = DEFAULT_NUM_CTX.get(role, 2048)
        v, basis = vram_estimate_at(e, c)
        return {"num_ctx": c, "basis": basis, "vram_mib": v, "want": c,
                "floor": c, "capped_by": None, "room_tokens": c}

    want = overhead_tokens + ROOM_TARGET.get(role, MIN_CONVERSATION_ROOM)
    floor = overhead_tokens + MIN_CONVERSATION_ROOM
    declared = e.get("context_window") or 0

    rungs = [c for c in CONTEXT_LADDER if c >= floor]
    if not rungs:
        rungs = [CONTEXT_LADDER[-1]]
    if declared:
        allowed = [c for c in rungs if c <= declared]
        if allowed:
            rungs = allowed
        else:
            # The model's own window is below our floor. Take its window rather
            # than a rung it cannot serve, and say so.
            return {"num_ctx": min(declared, CONTEXT_LADDER[-1]),
                    "basis": "model-window", "vram_mib": None, "want": want,
                    "floor": floor, "capped_by": "model context window %d < "
                    "floor %d (overhead %d + minimum room %d)"
                    % (declared, floor, overhead_tokens, MIN_CONVERSATION_ROOM),
                    "room_tokens": declared - overhead_tokens}

    # Prefer the smallest rung that satisfies `want`; only go bigger if the
    # budget is generous, and never bigger than needed — spare VRAM belongs to
    # whichever seat has a job for it.
    target = next((c for c in rungs if c >= want), rungs[-1])

    if budget_mib is None:
        v, basis = vram_estimate_at(e, target)
        return {"num_ctx": target, "basis": basis, "vram_mib": v,
                "want": want, "floor": floor, "capped_by": None,
                "room_tokens": target - overhead_tokens}

    for c in [c for c in rungs if c <= target][::-1]:
        v, basis = vram_estimate_at(e, c)
        if v is None or v <= budget_mib:
            return {"num_ctx": c, "basis": basis, "vram_mib": v, "want": want,
                    "floor": floor, "room_tokens": c - overhead_tokens,
                    "capped_by": ("VRAM budget %d MiB" % budget_mib)
                    if c < target else None}

    smallest = rungs[0]
    v, basis = vram_estimate_at(e, smallest)
    return {"num_ctx": None, "basis": basis, "vram_mib": v, "want": want,
            "floor": floor, "room_tokens": smallest - overhead_tokens,
            "capped_by": "needs %s MiB at the floor rung %d, budget is %d MiB"
            % (v, smallest, budget_mib)}


def _ms(e: dict) -> float:
    """Lower is faster. Unmeasured sorts last, never first."""
    v = e.get("baseline_ms_per_token")
    return v if v is not None else float("inf")


def _params(e: dict) -> float:
    return e.get("params_total_b") or 0.0


def _generation_candidates(entries: list) -> list:
    """Deterministic order: quality desc, then model_id for a stable tie-break."""
    gen = [e for e in entries
           if e.get("can_generate") and not e.get("is_embedding")]
    return sorted(gen, key=lambda e: (-_params(e), e["model_id"]))


# ── The policy ───────────────────────────────────────────────────────────────

def plan(profile: dict, entries: list, overrides: dict | None = None,
         overhead_tokens: int | None = None) -> dict:
    """(HardwareProfile, Catalog, overrides, overhead) -> PlacementPlan. Pure.

    `overhead_tokens` is how much of every window the system prompt and tool
    schemas consume. It is a PARAMETER rather than something this module goes
    and measures, because measuring it means assembling the system prompt —
    file and vault reads — and this function's whole value is that it is pure
    and its output is reproducible from its inputs. The Arbiter passes the live
    figure; the default is the last measured one.
    """
    overrides = dict(overrides or {})
    if overhead_tokens is None:
        from agent_friday.services.context_budget import (
            MEASURED_OVERHEAD_TOKENS)
        overhead_tokens = MEASURED_OVERHEAD_TOKENS
    seats: dict = {r: None for r in ROLES}
    refusals: list = []

    budgets = gpu_budgets(profile)
    ram = ram_budget(profile)
    unified = (profile.get("memory_bandwidth") or {}).get("class") == "unified"

    # P6: a machine class we can detect and cannot yet serve. Refusing every
    # seat is the honest output -- a plan implies a backend exists.
    if unified:
        for role in ROLES:
            refusals.append(_refusal(
                role, None, "R3",
                "unified-memory backend not implemented: no MLX/Metal/ROCm "
                "backend exists in the tree, and the VRAM and RAM budgets are "
                "one pool on this class rather than two independent ones",
                backend_status="UNKNOWN"))
        return _finish(profile, seats, refusals, budgets, ram)

    gen = _generation_candidates(entries)
    embedders = sorted([e for e in entries if e.get("is_embedding")],
                       key=lambda e: e["model_id"])

    # ── heavy_hitter: the quality seat, chosen before the brain so the brain
    #    can be the best model that is NOT already carrying quality duty.
    heavy = gen[0] if gen else None
    remaining = [e for e in gen if heavy is None or
                 e["model_id"] != heavy["model_id"]]

    # ── interactive_brain: best remaining model that fits some GPU alone.
    brain = None
    if budgets:
        cap = max(b["available_mib"] for b in budgets)
        for e in remaining:
            # Qualification is asked at the FLOOR, not at the target: a model
            # that can only afford the smallest usable window is still a
            # candidate for the seat. Sizing happens in _place, against the
            # budget that is actually left by then.
            cb = context_for("interactive_brain", e, cap, overhead_tokens)
            if cb["num_ctx"] is not None and cb["vram_mib"] is not None and \
                    cb["vram_mib"] <= cap:
                brain = e
                break
    elif remaining:
        # CPU-only: the brain is the cheapest viable seat; nothing "fits" a GPU.
        brain = min(remaining, key=lambda e: (_ms(e), e["model_id"]))

    # ── sidekick: fastest thing left. On a CPU-only host it collapses into
    #    the brain rather than paying for a second copy of the same tier.
    side_pool = [e for e in remaining
                 if brain is None or e["model_id"] != brain["model_id"]]
    sidekick = (min(side_pool, key=lambda e: (_ms(e), e["model_id"]))
                if side_pool and budgets else None)

    # ── Device assignment. R4 throughout: a model goes on ONE GPU or none.
    free = {b["index"]: b["available_mib"] for b in budgets}
    order = sorted(budgets, key=lambda b: (-b["available_mib"], b["index"]))

    def _place(entry, role, status, prefer=None):
        """Fit `entry` on one GPU at the largest context it can afford there.

        Context and fit are decided together, not in sequence. Choosing a
        context first and then asking whether it fits is what produced a seat
        sized from the tool registry alone: the number was picked before
        anything knew what it would cost.
        """
        idxs = ([prefer] if prefer is not None else
                [b["index"] for b in order])
        for i in idxs:
            if i is None:
                continue
            cb = context_for(role, entry, free.get(i, 0), overhead_tokens)
            v = cb["vram_mib"]
            if cb["num_ctx"] is None or v is None or free.get(i, 0) < v:
                continue
            free[i] -= v
            p = _placement(entry, role, "gpu:%d" % i, cb["num_ctx"], status, v)
            p["context"] = {k: cb[k] for k in
                            ("basis", "want", "floor", "room_tokens",
                             "capped_by")}
            return p
        return None

    multi_gpu = len(budgets) >= 2

    # heavy first when there are two GPUs: it is the largest object to fit and
    # placing it last would strand it.
    heavy_seat = None
    if heavy is not None and multi_gpu:
        heavy_seat = _place(heavy, "heavy_hitter", "pinned",
                            prefer=order[0]["index"])

    if brain is not None:
        pref = None
        if multi_gpu and heavy_seat is not None:
            others = [b["index"] for b in order
                      if "gpu:%d" % b["index"] != heavy_seat["device"]]
            pref = others[0] if others else None
        seats["interactive_brain"] = _place(brain, "interactive_brain",
                                            "pinned", prefer=pref)
        if seats["interactive_brain"] is None and not budgets:
            seats["interactive_brain"] = _placement(
                brain, "interactive_brain", "cpu",
                DEFAULT_NUM_CTX["interactive_brain"], "resident", 0)

    # The next-best small model, for small-but-harder work. Leased rather than
    # pinned: on P1 the pinned pair already sits at 9764 of 9997 MiB, so a third
    # resident model would breach R3. It is a real, addressable seat that loads
    # on demand — not a model quietly left unbound.
    alt_pool = [e for e in side_pool
                if sidekick is None or e["model_id"] != sidekick["model_id"]]
    sidekick_heavy = (min(alt_pool, key=lambda e: (_ms(e), e["model_id"]))
                      if alt_pool else None)

    if sidekick is not None:
        seats["sidekick"] = _place(sidekick, "sidekick", "pinned")
        if seats["sidekick"] is None:
            refusals.append(_refusal(
                "sidekick", sidekick["model_id"], "R3",
                "no GPU has room beside the pinned brain: needs %s MiB, "
                "largest remaining budget is %d MiB"
                % (_vram_for(sidekick, DEFAULT_NUM_CTX["sidekick"]),
                   max(free.values()) if free else 0)))
    elif not budgets and brain is not None:
        # CPU-only: one seat serves both roles (see fixture P5).
        seats["sidekick"] = dict(seats["interactive_brain"] or {},
                                 role="sidekick", collapsed_into=
                                 "interactive_brain") \
            if seats["interactive_brain"] else None

    if sidekick_heavy is not None:
        # A leased seat is sized against the LEASE budget: the whole GPU minus
        # whatever R10 keeps resident. Not the residual after every pinned seat
        # (a lease is exactly when the brain may stand down) and not the whole
        # card either (the sidekick does not stand down).
        cb = context_for("sidekick_heavy", sidekick_heavy,
                         _lease_budget(budgets, seats), overhead_tokens)
        ctx = cb["num_ctx"] or DEFAULT_NUM_CTX["sidekick_heavy"]
        need = cb["vram_mib"]
        dev = ("gpu:%d" % order[0]["index"]) if budgets else "cpu"
        seat = _placement(sidekick_heavy, "sidekick_heavy", dev, ctx,
                          "leased", need or 0)
        seat["context"] = {k: cb[k] for k in
                           ("basis", "want", "floor", "room_tokens",
                            "capped_by")}
        seat["displaces"] = ("loaded on demand; may displace a pinned seat"
                             if budgets else None)
        seats["sidekick_heavy"] = seat

    # ── embedder: GPU when there is room after the pinned seats, else CPU.
    if embedders:
        emb = embedders[0]
        seat = _place(emb, "embedder", "pinned") if budgets else None
        if seat is None:
            need = _vram_for(emb, DEFAULT_NUM_CTX["embedder"])
            seat = _placement(emb, "embedder", "cpu",
                              DEFAULT_NUM_CTX["embedder"], "resident", 0)
            if budgets:
                seat["demoted_from"] = "gpu"
                seat["demotion_rule"] = "R3"
                seat["demotion_reason"] = (
                    "needs %s MiB, %d MiB left after the pinned seats"
                    % (need, max(free.values()) if free else 0))
        seats["embedder"] = seat

    # ── heavy_hitter placement and the RAM check.
    if heavy is not None:
        seats["heavy_hitter"], hr = _heavy(
            heavy, heavy_seat, budgets, free, ram, profile, overhead_tokens,
            _lease_budget(budgets, seats))
        if hr:
            refusals.append(hr)
    elif gen == []:
        refusals.append(_refusal("heavy_hitter", None, "R6",
                                 "no generation-capable model is installed"))

    # ── image: R5.
    if budgets:
        idx = order[-1]["index"] if multi_gpu else order[0]["index"]
        seats["image"] = {
            "role": "image", "model_id": "z-image-turbo-fp8",
            "backend": "comfyui", "device": "gpu:%d" % idx, "num_ctx": None,
            "offload": {}, "status": "leased", "exclusive": True,
            # R5 minus R10: exclusive of everything except the seat that keeps
            # Friday answering while the picture renders.
            "displaces": ("gpu:%d only" % idx) if multi_gpu else
            "all seats except %s" % ", ".join(sorted(RETAINED_THROUGH_LEASE)),
            "retained_mib": retained_mib(seats),
            "vram_mib": None, "est_load_s": None,
        }
    else:
        refusals.append(_refusal(
            "image", "z-image-turbo-fp8", "R5",
            "no GPU to lease; local image generation is unavailable on this "
            "profile and escalates to cloud"))

    # ── CPU services, always.
    seats["stt"] = _cpu_seat("stt", "faster-whisper")
    seats["tts"] = _cpu_seat("tts", "kokoro")

    _apply_overrides(seats, refusals, overrides, entries, free, budgets,
                     overhead_tokens)
    return _finish(profile, seats, refusals, budgets, ram)


def _heavy(heavy, preplaced, budgets, free, ram, profile, overhead_tokens,
           lease_cap):
    """Place the heavy seat, or refuse it with arithmetic.

    The capacity question for a LEASED seat is the LEASE budget, not the
    residual after the pinned seats and not the whole card either. A lease is
    the moment the brain may be stood down — asking about the residual would
    offload a model that would have fit once the brain stepped aside. But R10
    holds the sidekick resident through the lease, so that VRAM is genuinely
    not available and pretending otherwise would place a model that then has to
    spill somewhere nobody planned for.
    """
    if preplaced is not None:
        return preplaced, None

    whole = lease_cap
    cb = context_for("heavy_hitter", heavy, whole, overhead_tokens)
    # When nothing on the ladder fits, the seat still needs an explicit context
    # (R7) — it takes the floor rung and offloads, rather than being handed the
    # backend default that R7 exists to prevent.
    ctx = cb["num_ctx"] or (overhead_tokens + MIN_CONVERSATION_ROOM)
    ctx = next((c for c in CONTEXT_LADDER if c >= ctx), CONTEXT_LADDER[-1])
    measured_vram = _vram_for(heavy, ctx)
    total = _required_vram(heavy, ctx)
    ctx_info = dict(cb, num_ctx=ctx, room_tokens=ctx - overhead_tokens,
                    capped_by=cb["capped_by"] or (
                        "no ladder rung fits the %s MiB GPU budget, so the "
                        "seat takes the floor rung and offloads" % whole
                        if cb["num_ctx"] is None else None))
    ctx_info = {k: ctx_info[k] for k in
                ("basis", "want", "floor", "room_tokens", "capped_by")}

    if budgets and total is not None:
        best = max(budgets, key=lambda b: (b["available_mib"], -b["index"]))
        if total <= (lease_cap if lease_cap is not None
                     else best["available_mib"]):
            i = best["index"]
            if free.get(i, 0) >= total:
                free[i] -= total
                seat = _placement(heavy, "heavy_hitter", "gpu:%d" % i, ctx,
                                  "pinned", total)
            else:
                seat = _placement(heavy, "heavy_hitter", "gpu:%d" % i, ctx,
                                  "leased", total)
                seat["displaces"] = "pinned seats on gpu:%d" % i
            seat["context"] = ctx_info
            return seat, None

    # Does not fit whole. The GPU keeps what it can hold BESIDE the retained
    # sidekick; the rest is host RAM.
    if budgets:
        cap = lease_cap if lease_cap is not None else \
            max(b["available_mib"] for b in budgets)
        gpu_portion = min(measured_vram or cap, cap)
    else:
        gpu_portion = 0
    host_mib = max(0, (total or 0) - gpu_portion)
    need = ram["os_reserve_mib"] + host_mib
    if need > ram["hard_ceiling_mib"]:
        return None, _refusal(
            "heavy_hitter", heavy["model_id"], "R2",
            "host RAM %d MiB + OS reserve %d MiB = %d MiB exceeds the "
            "%d MiB hard ceiling (75%% of %d MiB)"
            % (host_mib, ram["os_reserve_mib"], need, ram["hard_ceiling_mib"],
               ram["total_mib"]),
            host_mib=host_mib, ceiling_mib=ram["hard_ceiling_mib"])

    if not heavy.get("is_moe") and budgets:
        return None, _refusal(
            "heavy_hitter", heavy["model_id"], "R6",
            "dense model needs %d MiB and the largest GPU budget is %d MiB; "
            "dense models must fit or be demoted, only MoE may expert-offload"
            % (total or 0, max(b["available_mib"] for b in budgets)))

    seat = _placement(heavy, "heavy_hitter",
                      ("gpu:%d+cpu" % sorted(free)[0]) if budgets else "cpu",
                      ctx, "leased", gpu_portion)
    # The operating point, carried in the PLAN rather than left implicit in the
    # Arbiter, and derived from the lease budget rather than fixed — because
    # R10 changed that budget and a constant would silently overrun it.
    layers, layer_basis = (n_cpu_moe_for_budget(lease_cap)
                           if (budgets and heavy.get("is_moe"))
                           else (None, None))
    seat["offload"] = {
        "expert_offload": bool(heavy.get("is_moe")),
        "host_mib": host_mib,
        "n_cpu_moe": layers,
        "n_cpu_moe_basis": layer_basis,
        "lease_budget_mib": lease_cap,
    }
    seat["over_target"] = need > ram["target_ceiling_mib"]
    seat["context"] = ctx_info
    return seat, None


def _placement(entry, role, device, num_ctx, status, vram_mib):
    return {
        "role": role, "model_id": entry["model_id"],
        "backend": entry.get("backend"), "device": device,
        "num_ctx": num_ctx,                       # R7: always explicit
        "offload": {}, "status": status, "vram_mib": vram_mib,
        "est_load_s": entry.get("est_load_s"),
        "is_moe": bool(entry.get("is_moe")),
        "needs_think_disabled": bool(entry.get("needs_think_disabled")),
    }


def _cpu_seat(role, model_id):
    return {"role": role, "model_id": model_id, "backend": "cpu-service",
            "device": "cpu", "num_ctx": None, "offload": {},
            "status": "on-demand", "vram_mib": 0, "est_load_s": None}


def _apply_overrides(seats, refusals, overrides, entries, free, budgets,
                     overhead_tokens):
    """A user override binds a model to a role, or is refused with its reason.

    Never a silent ignore: that is what made `preferred_model` and
    `capability_routing.embedding.model` dead settings people could change
    while nothing happened.
    """
    by_id = {e["model_id"]: e for e in entries}
    for role, model_id in sorted(overrides.items()):
        if role not in ROLES:
            refusals.append(_refusal(role, model_id, "R7",
                                     "unknown role %r" % role))
            continue
        entry = by_id.get(model_id)
        if entry is None:
            refusals.append(_refusal(
                role, model_id, "R6",
                "override names a model that is not installed; installed: %s"
                % ", ".join(sorted(by_id)) or "(none)"))
            continue
        if role in ("interactive_brain", "sidekick", "heavy_hitter") and \
                not entry.get("can_generate"):
            refusals.append(_refusal(
                role, model_id, "R6",
                "model cannot generate text (capabilities: %s), so it cannot "
                "fill %s" % (", ".join(entry.get("modalities") or []) or "none",
                             role)))
            continue
        cur = seats.get(role)
        cap = max(free.values()) if free else 0
        headroom = cap + ((cur or {}).get("vram_mib") or 0)
        cb = context_for(role, entry, headroom if budgets else None,
                         overhead_tokens)
        ctx = cb["num_ctx"] or DEFAULT_NUM_CTX.get(role, TOOL_SEAT_NUM_CTX)
        need = cb["vram_mib"]
        # `cur is not None` used to be part of this guard, so an override
        # onto an EMPTY seat skipped the VRAM check entirely and was placed
        # whether or not the card had room. Nothing exercised it while
        # overrides were never supplied; feeding Stephen's choices in
        # (2026-08-18) walks straight through it, and over-committing this
        # card is what drops his second monitor. An unseated role has no
        # VRAM of its own to reclaim, so its headroom is simply `cap`.
        if budgets and need is not None and need > headroom:
            # `cur` is None when the base plan left this role unseated, so
            # the message may not assume a model to fall back to.
            refusals.append(_refusal(
                role, model_id, "R3",
                "override needs %d MiB but only %d MiB is available on the "
                "largest GPU after the other pinned seats%s"
                % (need, headroom,
                   ("; nearest permitted: %s" % cur.get("model_id"))
                   if cur else "; this role stays empty")))
            continue
        seats[role] = _placement(entry, role,
                                 cur["device"] if cur else
                                 ("gpu:%d" % sorted(free)[0] if free else "cpu"),
                                 ctx, cur["status"] if cur else "pinned",
                                 need or 0)
        seats[role]["from_override"] = True


def _finish(profile, seats, refusals, budgets, ram):
    used = {}
    for s in seats.values():
        if s and str(s.get("device", "")).startswith("gpu") and \
                s.get("status") == "pinned":
            used[s["device"]] = used.get(s["device"], 0) + (s.get("vram_mib") or 0)
    return {
        "policy_version": POLICY_VERSION,
        "profile_id": profile.get("profile_id"),
        "seats": seats,
        "refusals": refusals,
        "budgets": {"gpus": budgets, "ram": ram},
        "pinned_vram_mib": used,
    }


# ── Headroom checks the Arbiter calls before any load ────────────────────────

def check_ram_headroom(profile: dict, add_mib: int,
                       current_host_mib: int = 0) -> dict:
    ram = ram_budget(profile)
    projected = ram["os_reserve_mib"] + current_host_mib + add_mib
    ok = projected <= ram["hard_ceiling_mib"]
    return {
        "ok": ok, "rule_id": "R2", "projected_mib": projected,
        "ceiling_mib": ram["hard_ceiling_mib"],
        "explanation": (
            "" if ok else
            "refused: %d MiB projected (OS reserve %d + resident %d + %d to "
            "load) exceeds the %d MiB hard ceiling, 75%% of %d MiB physical"
            % (projected, ram["os_reserve_mib"], current_host_mib, add_mib,
               ram["hard_ceiling_mib"], ram["total_mib"])),
    }


def check_disk_headroom(profile: dict, artifact_mib: int) -> dict:
    """R8. On Windows a large resident model consumes disk via the pagefile."""
    free = (profile.get("disk") or {}).get("free_mib", 0)
    floor = max(DISK_FLOOR_MIB, artifact_mib)
    after = free - artifact_mib
    ok = after >= floor
    return {
        "ok": ok, "rule_id": "R8", "free_after_mib": after,
        "floor_mib": floor,
        "explanation": (
            "" if ok else
            "refused: %d MiB free after loading a %d MiB artifact is below "
            "the %d MiB floor; on Windows the pagefile grows with the "
            "resident set, so disk is a residency resource"
            % (after, artifact_mib, floor)),
    }


def num_ctx_for_model(model_id: str, default: int = TOOL_SEAT_NUM_CTX) -> int:
    """The context the PLAN specifies for whichever seat holds `model_id`.

    Dispatch must apply this, not just the Arbiter at boot. Until 2026-08-15 it
    did not: the Arbiter loaded a seat at the planned context, then the first
    ordinary chat request reloaded the same model at Ollama's default and the
    placement was silently lost. Measured consequence on the reference machine
    — `gemma4:12b` resident at 262144 with 71% of it on the CPU, minutes after
    booting to a plan that said 32768 and 100% GPU.

    Reads the live Arbiter's plan when one is governing this process, so a
    re-plan takes effect without a restart. Falls back to the tool-seat context
    rather than to the daemon default, because the daemon default is the thing
    being corrected.
    """
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        seats = ((arb.plan if arb else None) or {}).get("seats") or {}
        for seat in seats.values():
            if seat and seat.get("model_id") == model_id and seat.get("num_ctx"):
                return int(seat["num_ctx"])
    except Exception:
        pass
    return default
