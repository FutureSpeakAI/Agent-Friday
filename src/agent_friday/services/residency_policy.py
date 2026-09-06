"""
Agent Friday — ResidencyPolicy

A pure, deterministic function:

    plan(HardwareProfile, [CatalogEntry], overrides) -> PlacementPlan

No I/O, no clock, no randomness, no network. The same three inputs always
produce the same plan, byte for byte — which is what makes a golden-plan test
meaningful and what lets a refusal be reproduced from a bug report.

The rules live in RULES as inspectable data with stable ids, so every refusal
cites one and a human can read the policy without reading the code. Thresholds
and their justifications are in docs/design/implemented/residency-policy.md.

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
         "embedder", "stt", "tts", "image",
         # The working roles the maintainer named. They are ROLES, not
         # models: one model may hold several, and several models may not hold
         # one. See ROLE_RESIDENCY for why seven roles fit a card that cannot
         # hold three copies of a 12B.
         "orchestrator", "sidekick_fast", "function_manager",
         "memory_manager", "researcher",
         # headroom.md §12 Phase 3.1 / D8: a permanently refused seat until a
         # local backend exists (no `ltx`/`wan`/`hunyuan`/`cogvideo` reference
         # anywhere under src/, §2.5 VERIFIED by absence). It is a ROLE so
         # `plan_chain` can name it in a stage and refuse it cleanly with a
         # reason, the same P6 pattern `plan()` already uses for a whole
         # unified-memory profile -- not so `plan()` ever seats it.
         "video")

# A role may be spelled more than one way without becoming two seats.
# `embeddings_manager` is what the embedder seat is called when you describe it
# by its job rather than its mechanism; they are the same seat and must resolve
# to one, or the budget would count the model twice.
ROLE_ALIASES = {
    "embeddings_manager": "embedder",
    "embedding_manager": "embedder",
    "brain": "interactive_brain",
    "heavy": "heavy_hitter",
}


def resolve_role(role: str) -> str:
    """Canonical role id. Unknown roles pass through to be refused by name."""
    return ROLE_ALIASES.get(role, role)


# ── Residency classes ────────────────────────────────────────────────────────
#
# The arithmetic that makes seven roles possible on a 12 GB card. Only what is
# RESIDENT costs VRAM all day. A leased seat costs VRAM while it runs and is
# then given back; an on-demand seat costs nothing until something calls it, and
# the nightly ones deliberately run when nothing else wants the card.
#
# Summing all seven as though each were resident is how a lineup that fits
# comfortably gets refused.
RESIDENT = "resident"
LEASED = "leased"
ON_DEMAND = "on-demand"

ROLE_RESIDENCY = {
    # The conversational path. These must be warm or Friday stutters.
    "orchestrator": RESIDENT,
    "sidekick": RESIDENT,
    "sidekick_fast": RESIDENT,
    "function_manager": RESIDENT,   # sits inside the tool loop
    # ── embedder: ON_DEMAND, not RESIDENT ────────────────────────────────
    # This said RESIDENT and "live recall happens mid-turn". Neither half is
    # true. `services/role_consumers.py` classifies the embedding
    # seat as DISPLAYS -- "Read, never obeyed": nothing selects a model from it,
    # because `conversation_memory.EMBED_MODEL` is a module constant pinned to
    # all-MiniLM-L6-v2 and runs in-process on CPU via sentence_transformers.
    #
    # So the plan pinned `embeddinggemma:300m` resident on a 12 GB card for a
    # consumer that does not exist. The live server reported it
    # `calls: 0, last_used: null` while the arbiter reserved room for it.
    # Declaring a seat nothing consumes is the same pattern as the dead
    # generator and the orphaned watchers: it costs VRAM, it invents work, and
    # it makes the seat map lie about what is running.
    #
    # ON_DEMAND keeps the seat assignable -- so wiring a real consumer later is
    # a one-line change back -- without reserving a card for nobody.
    "embedder": ON_DEMAND,
    "interactive_brain": RESIDENT,
    # Commissioned work. Big, occasional, and worth waiting for.
    "heavy_hitter": LEASED,
    "researcher": LEASED,
    "sidekick_heavy": LEASED,
    "image": LEASED,
    # Not served by any backend yet (D8), but the SHAPE it would take if one
    # existed is the same as image's -- an occasional, exclusive render.
    "video": LEASED,
    # Scheduled or reactive. Nothing is waiting on them in a conversation.
    "memory_manager": ON_DEMAND,    # nightly consolidation
    "stt": ON_DEMAND,
    "tts": ON_DEMAND,
}

_WARMTH = {RESIDENT: 2, LEASED: 1, ON_DEMAND: 0}

# Roles that do useful work without a GPU at all. This is not a claim that CPU
# is as good — it is that these jobs are either tiny (an embedder at 2048
# tokens) or unhurried (a consolidation pass that runs while he sleeps), and
# moving them off the card buys headroom the conversational seats need.
CPU_CAPABLE_ROLES = frozenset({"embedder", "memory_manager", "stt", "tts"})


def residency_of(role: str) -> str:
    return ROLE_RESIDENCY.get(resolve_role(role), RESIDENT)


# Roles the policy will never fill on its own. See the comment at the end of
# plan(): there is no "correct" orchestrator the way there is a correct brain.
ASSIGNED_ROLES = ("orchestrator", "sidekick_fast", "function_manager",
                  "memory_manager", "researcher")

# ── Rules as data ────────────────────────────────────────────────────────────

# NOT the display reserve `services/headroom_contract.resolve_display_reserve()`
# reconciles -- this is planner SLACK on top of the baseline (see
# `gpu_budgets()` below: `available_mib = total - VRAM_RESERVE_MIB - baseline`),
# a buffer against the margin §3.2 of `docs/design/implemented/headroom.md` measured
# (354 MiB was the gap between working and thrashing). Left at its existing
# value: closing the display-reserve hole does not, on its own, tell us
# whether 1,024 MiB of additional slack is still the right number -- that is
# entangled with D1 (the Contract's own VRAM-slack floor, not yet decided).
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
# Re-measured WITH THE SIDEKICK RESIDENT, which is the condition
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
# unresident (docs/history/audits/symphony-live-2026-08-15.md §4). Consistent with two
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
    {"id": "R11", "name": "assigned-role",
     "text": "The working roles (orchestrator, function manager, memory "
             "manager, researcher, fast sidekick) are chosen by the user, not "
             "inferred. There is no 'correct' orchestrator the way there is a "
             "correct brain, so an unassigned one stays empty and says so "
             "rather than being guessed at. One model may hold several roles "
             "and is counted ONCE against the budget.",
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
# A tool-using seat must hold the TOOL DEFINITIONS. Measured on the reference machine: the
# 52-tool registry serialises to ~8 534 tokens, so a seat at 8192 truncates the
# tools before the conversation even starts. gemma4:e2b scored 8/10 on the
# structural gate at 8192 and 10/10 at a larger context — a context below the
# tool floor manufactures exactly the tool-calling failure the gate exists to
# detect.
#
# CORRECTED. This constant used to be 32768, and the arithmetic
# behind it was wrong in a checkable way: it sized the window from the tool
# registry ALONE (~8534 tokens x4) and ignored the system prompt, which at
# ~11681 tokens is the LARGER of the two fixed costs. A 32768 seat therefore
# had ~12552 tokens of room — 38% of its own window. See services/
# context_budget.py, which measures both halves rather than assuming either.
#
# It stays as the floor rung and the dispatch fallback, not as the answer:
# `context_for()` computes the real number from overhead + room, per role.
# The context a tool-using seat needs. RAISED from 32768, and the
# old value was not merely tight -- it was insufficient, by a lot.
#
# Measured on the reference machine: one real turn against a 32,768 seat totalled 47,309 tokens
# (12,706 system prompt + 9,603 tools + ~25,000 injected memory and source
# context). That is 14,541 tokens MORE than the window holds, so every such turn
# was silently truncating its own oldest context and answering from a partial
# view. 32768 was chosen when overhead was believed to be ~22k, because injected
# context was not counted at all -- see context_budget.MEASURED_INJECTED_TOKENS.
#
# 65536 is the smallest ladder rung that holds a real turn, and on this family
# it is nearly free: gemma4:12b measures 7,718 MiB at 32768 and 7,750 MiB at
# 65536, because sliding-window attention keeps most of the KV cache capped.
# Thirty-two MiB to stop truncating every conversation is not a close call.
TOOL_SEAT_NUM_CTX = 65536

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
    "sidekick_fast": MIN_CONVERSATION_ROOM,
    # An orchestrator routes and calls tools; it does not hold the long
    # conversation, so it does not need the brain's window.
    "orchestrator": MIN_CONVERSATION_ROOM,
    "function_manager": MIN_CONVERSATION_ROOM,
    # These two read a lot in one pass -- a day of turns, or a research dossier
    # -- so they get real room even though neither is resident.
    "memory_manager": 24576,
    "researcher": 24576,
    "embedder": 0,
}

DEFAULT_NUM_CTX = {
    "interactive_brain": TOOL_SEAT_NUM_CTX,
    "heavy_hitter": TOOL_SEAT_NUM_CTX,
    "sidekick": TOOL_SEAT_NUM_CTX,
    "sidekick_heavy": TOOL_SEAT_NUM_CTX,
    "orchestrator": TOOL_SEAT_NUM_CTX,
    "sidekick_fast": TOOL_SEAT_NUM_CTX,
    "function_manager": TOOL_SEAT_NUM_CTX,
    "memory_manager": TOOL_SEAT_NUM_CTX,
    "researcher": TOOL_SEAT_NUM_CTX,
    "embedder": 2048,
}

# Seats that carry neither tool schemas nor a system prompt, and so are not
# subject to the overhead arithmetic at all.
NO_PROMPT_ROLES = frozenset({"embedder"})

# R10. Seats a lease may NOT take. The maintainer's ruling: "keep e2b awake so
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
        row = {
            "index": g["index"],
            "name": g.get("name"),
            "total_mib": g["vram_total_mib"],
            "baseline_mib": baseline,
            "available_mib": max(
                0, g["vram_total_mib"] - VRAM_RESERVE_MIB - baseline),
            "compute_class": g.get("compute_class"),
        }
        # When the live display-reserve reading was discarded as impossible,
        # say so HERE rather than only in the log. A budget computed from a
        # cached floor instead of a live measurement is a different claim from
        # one computed from a measurement, and the difference has to be visible
        # to whoever is asking why a seat did not fit.
        rejected = g.get("vram_display_reserve_rejected")
        if rejected:
            row["baseline_source"] = "cached-floor (live reading discarded)"
            row["baseline_rejected"] = rejected
        elif isinstance(g.get("vram_display_reserve_mib"), int):
            # NOT necessarily sampled this cycle. `refresh_display_reserve` is
            # the only writer and it does not always run -- it is skipped under
            # test, and it declines to overwrite when the probe cannot answer --
            # so this field may be carried over from an earlier plan. Calling it
            # "live" would be the same unverified claim this module keeps
            # catching elsewhere. The timestamp says how old it actually is.
            row["baseline_source"] = "display-reserve"
            row["baseline_sampled_at"] = g.get("vram_display_reserve_at")
        else:
            row["baseline_source"] = "measured-idle-floor"
        out.append(row)
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

    # DO NOT SIZE A SEAT WHERE THE COST IS EXTRAPOLATED.
    #
    # Above the largest measured context, `vram_estimate_at` extrapolates, and
    # extrapolation understates: the curve is flat across the measured range on
    # this family because sliding-window attention caps most of the KV cache,
    # and a straight line through flat points stays flat forever. It does not.
    #
    # Measured on the reference machine, and this is the whole reason for the rule: a seat
    # spawned at the architectural maximum of 262,144 left 448 MiB of 12,282 on
    # the card and took a monitor off the desktop. The largest MEASURED row for
    # that model is 131,072 at 7,814 MiB, so every rung above it was a guess
    # that happened to be catastrophic.
    #
    # Raising the overhead figure to include injected context (context_budget)
    # pushed the brain's target past 131,072 and would have re-armed
    # exactly that failure, which is how this rule came to be written.
    #
    # A model with no measurements at all is not capped -- there is nothing to
    # be conservative about yet, and refusing every unmeasured model would make
    # a fresh install unusable.
    measured_ctx = [m.get("num_ctx") for m in (e.get("measured") or [])
                    if m.get("num_ctx") and m.get("vram_mib")]
    if measured_ctx:
        ceiling = max(measured_ctx)
        within = [c for c in rungs if c <= ceiling]
        if within:
            rungs = within
        else:
            # Every rung that clears the floor is above what this model has
            # been measured at, so SOME extrapolation is unavoidable. Take the
            # least of it -- the smallest sufficient rung -- rather than the
            # largest affordable one.
            #
            # Leaving `rungs` untouched here was a hole that defeated the whole
            # cap: gemma4:e4b is measured only at 8,192, the floor is now above
            # that, and the seat sailed past every rung to 262,144 -- the exact
            # value this rule exists to prevent. A guess is sometimes necessary;
            # the biggest possible guess never is.
            rungs = [rungs[0]]

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

DEFAULT_IMAGE_MODEL = "z-image-turbo-fp8"


def plan(profile: dict, entries: list, overrides: dict | None = None,
         overhead_tokens: int | None = None,
         image_model: str | None = None,
         cloud_roles=()) -> dict:
    """(HardwareProfile, Catalog, overrides, overhead) -> PlacementPlan. Pure.

    `image_model` is a PARAMETER for the same reason `overhead_tokens` is. The
    image seat is a ComfyUI model, so it can never appear in `entries` — that
    catalog holds language models — and the normal override path would refuse
    it as "not installed". Naming it here keeps the plan honest about which
    model the exclusive lease is actually for, without this module going and
    reading settings or touching a disk.

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
            # Residency comes from ROLE_RESIDENCY, not a literal. This said
            # "resident" while the table said the same thing; when the table
            # changed to on-demand, a hardcoded string here would have kept
            # planning the seat resident anyway -- two sources of truth for
            # one fact, which is the bug this whole patch is about.
            seat = _placement(emb, "embedder", "cpu",
                              DEFAULT_NUM_CTX["embedder"],
                              ROLE_RESIDENCY["embedder"], 0)
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
            "role": "image", "model_id": image_model or DEFAULT_IMAGE_MODEL,
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
            "image", image_model or DEFAULT_IMAGE_MODEL, "R5",
            "no GPU to lease; local image generation is unavailable on this "
            "profile and escalates to cloud"))

    # ── video: D8, headroom.md §14.2. No local backend exists in the tree at
    # all -- not "does not fit this profile", which is what an R3/R5 refusal
    # elsewhere in this function means. `plan_chain` is where the seat
    # actually goes to cloud (§5.3: "the chain's video stage is cloud"); this
    # is `plan()`'s own placement question, and the honest answer is that
    # there is nothing to place, on every profile, always.
    refusals.append(_refusal(
        "video", None, "R5",
        "no local video backend exists in the tree (no ltx/wan/hunyuan/"
        "cogvideo reference anywhere under src/); video runs in the cloud "
        "on every machine today"))

    # ── CPU services, always.
    seats["stt"] = _cpu_seat("stt", "faster-whisper")
    seats["tts"] = _cpu_seat("tts", "kokoro")

    _apply_overrides(seats, refusals, overrides, entries, free, budgets,
                     overhead_tokens)

    # The working roles are ASSIGNED, not inferred.
    #
    # interactive_brain and sidekick are chosen by the policy because there is a
    # right answer: the biggest thing that fits, and the fastest thing left.
    # There is no such rule for "orchestrator" or "memory manager" -- which
    # model should route, or should read the day and decide what is worth
    # keeping, is a judgment about how the user wants to work, and guessing it
    # would be the policy inventing a preference and then hiding it.
    #
    # So an unassigned working role carries a refusal that says so, rather than
    # a silently empty seat. Assign one through `overrides` and it is placed
    # like any other.
    #
    # R11 keys on ASSIGNMENT, not on placement, and the difference is the whole
    # point of the rule. The test used to be `seats.get(role) is None`, which is
    # a placement test: a role the user HAD assigned, whose model then failed to
    # fit, collected an R3 explaining the failure and then an R11 saying "no
    # model assigned" on top. Both were shown. Measured on the reference machine: five
    # roles carried that second refusal -- orchestrator, sidekick_fast,
    # function_manager, memory_manager and researcher -- while Settings ->
    # Intelligence listed a model against every one of them two sections above.
    #
    # The text was simply false, and worse than false: it sends someone to
    # choose a model they have already chosen, instead of to the reason their
    # choice could not be seated. An unplaced assignment is never an unmade
    # choice, so the two are now separated at the source rather than
    # disambiguated downstream.
    #
    # `cloud_roles` are filled too, just not by anything on this card. They are
    # excluded from `overrides` so the planner does not try to place them (a
    # local VRAM planner cannot install claude-opus-5 and should not say so),
    # and they must be excluded here as well or the same seat is reported empty
    # instead — the identical false statement under a different rule number.
    _assigned = set((overrides or {}).keys()) | set(cloud_roles or ())
    for role in ASSIGNED_ROLES:
        if seats.get(role) is None and role not in _assigned:
            refusals.append(_refusal(
                role, None, "R11",
                "no model assigned; this seat is chosen by the user, not "
                "inferred, and stays empty until one is named"))
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


# ---------------------------------------------------------------------------
#  Assignment arithmetic: what a set of role->model choices actually costs
# ---------------------------------------------------------------------------
#
# The whole point of this section is that it counts MODELS, not seats.
#
# A model held by three roles is one process holding one copy of one set of
# weights. Charging it three times is the bug that would make a comfortable
# lineup look impossible -- and it is an easy bug to write, because the natural
# shape of the code is a loop over roles.
#
# Two consequences fall out of counting per model:
#   * one context. Two roles sharing a model share its process, so the model is
#     sized at the LARGEST context any of its roles needs -- not the sum, and
#     not whichever role happened to be assigned last.
#   * one residency class, the warmest. A model held by the orchestrator
#     (resident) and the researcher (leased) is resident: it is already in
#     memory, and a lease cannot evict what the conversation is using.

def _role_ctx_want(role, overhead_tokens=None):
    """The context this role needs, sized from real demand where it is known.

    A constant cannot answer this: what a turn costs moves with the tool
    registry, the assembled system prompt and how much memory gets injected.
    So when the live overhead figure is available, this returns the smallest
    ladder rung that actually holds one turn plus room to answer in -- and the
    declared default becomes a floor rather than the answer.
    """
    want = DEFAULT_NUM_CTX.get(resolve_role(role), TOOL_SEAT_NUM_CTX)
    if not overhead_tokens or resolve_role(role) in NO_PROMPT_ROLES:
        return want
    need = overhead_tokens + MIN_CONVERSATION_ROOM
    for rung in CONTEXT_LADDER:
        if rung >= need:
            return max(want, rung)
    return max(want, CONTEXT_LADDER[-1])


def assignment_cost(assignments, entries, *, overhead_tokens=None,
                    prefer_cpu=True):
    """What a {role: model_id} selection costs, deduplicated by model.

    Returns per-model rows and three separate totals, because they are charged
    against the card at different times:

      resident_vram_mib   held all day, every day
      peak_lease_vram_mib the LARGEST single leased model, not their sum --
                          leases are exclusive, so two leased models never
                          occupy the card at once
      on_demand_vram_mib  the largest scheduled model, which runs when the
                          conversational seats are idle

    `peak_vram_mib` is what the card must actually survive: everything resident,
    plus the biggest single thing that can land on top of it.
    """
    if overhead_tokens is None:
        # Imported at call time, same as plan() does: context_budget reads the
        # live system prompt, and importing it at module scope would make this
        # module's import order matter.
        from agent_friday.services.context_budget import (
            MEASURED_OVERHEAD_TOKENS)
        overhead_tokens = MEASURED_OVERHEAD_TOKENS
    # Model ids are canonicalised for the same reason role names are: the same
    # artifact can arrive under two names (Friday's store and Ollama's registry
    # disagreed about the 0.6B embedder), and two names would be two seats and
    # two charges for one copy of one set of weights.
    try:
        from agent_friday.services.residency_catalog import canonical_model_id
    except Exception:                                  # keep this module pure
        def canonical_model_id(m):
            return m

    by_id = {}
    for e in entries:
        by_id.setdefault(canonical_model_id(e["model_id"]), e)
        by_id.setdefault(e["model_id"], e)

    grouped = {}
    unknown = []
    for role, model_id in sorted((assignments or {}).items()):
        canon = resolve_role(role)
        if canon not in ROLES:
            unknown.append({"role": role, "model_id": model_id,
                            "why": "unknown role"})
            continue
        if not model_id:
            continue
        model_id = canonical_model_id(model_id)
        g = grouped.setdefault(model_id,
                               {"roles": [], "entry": by_id.get(model_id)})
        g["roles"].append(canon)

    models = []
    for model_id, g in sorted(grouped.items()):
        e = g["entry"]
        roles = sorted(set(g["roles"]))
        residency = max((residency_of(r) for r in roles),
                        key=lambda c: _WARMTH.get(c, 0))
        want_ctx = max(_role_ctx_want(r, overhead_tokens) for r in roles)
        # CPU placement only when EVERY role on this model tolerates it: a
        # model shared by the embedder and the orchestrator has to sit where
        # the orchestrator needs it.
        cpu_ok = prefer_cpu and all(r in CPU_CAPABLE_ROLES for r in roles)
        row = {"model_id": model_id, "roles": roles, "residency": residency,
               "num_ctx": want_ctx, "device": "cpu" if cpu_ok else "gpu",
               "installed": e is not None}
        if e is None:
            row.update({"vram_mib": None, "ram_mib": None,
                        "why": "not installed"})
        else:
            v, basis = vram_estimate_at(e, want_ctx)
            row["vram_basis"] = basis
            # AN UNKNOWN SIZE IS NOT ZERO.
            #
            # Coercing `None` to 0 makes an unmeasured model look free, and a
            # lineup then "fits" precisely because nobody knows what it costs
            # -- the most expensive kind of wrong answer this advisory could
            # give. So an unsized model is carried as None and reported, and
            # `fits` refuses to claim a fit it cannot support.
            row["sized"] = v is not None
            if v is None:
                row["vram_mib"] = None
                row["ram_mib"] = None
                row["why"] = ("size unknown: no measured row at %d ctx"
                              % want_ctx)
            else:
                row["vram_mib"] = 0 if cpu_ok else v
                row["ram_mib"] = v if cpu_ok else 0
            row["is_moe"] = bool(e.get("is_moe"))
            # An MoE's raw footprint is not what it costs once experts are held
            # on the CPU. Say so rather than quoting a number that will look
            # wrong to anyone who has seen the offloaded seat run.
            if row["is_moe"] and row["residency"] != RESIDENT:
                row["note"] = ("MoE: expert offload reduces this at load time; "
                               "figure shown is the full footprint")
        models.append(row)

    resident = sum(m.get("vram_mib") or 0
                   for m in models if m["residency"] == RESIDENT)
    leased = [m.get("vram_mib") or 0
              for m in models if m["residency"] == LEASED]
    ondemand = [m.get("vram_mib") or 0
                for m in models if m["residency"] == ON_DEMAND]
    peak_lease = max(leased) if leased else 0
    peak_ondemand = max(ondemand) if ondemand else 0

    # A LEASE DISPLACES THE RESIDENT SEATS (R5), all but those R10 keeps awake.
    # So the card never holds the resident set AND a leased model at once, and
    # adding them is the second way to refuse a lineup that fits. Getting this
    # wrong is what made a 26B heavy hitter look like it needed 27 GB beside a
    # 12B orchestrator, when in truth the 12B stands down while it runs.
    retained = sum(
        m.get("vram_mib") or 0 for m in models
        if m["residency"] == RESIDENT
        and set(m["roles"]) & RETAINED_THROUGH_LEASE)

    # Three states the card actually passes through. The worst is the peak.
    state_normal = resident
    state_leased = retained + peak_lease
    # Scheduled work is additive: nothing stands down for a nightly job today.
    state_scheduled = resident + peak_ondemand
    peak = max(state_normal, state_leased, state_scheduled)

    return {
        "models": models,
        "unknown_roles": unknown,
        "distinct_models": len(models),
        "roles_assigned": sum(len(m["roles"]) for m in models),
        "resident_vram_mib": resident,
        "peak_lease_vram_mib": peak_lease,
        "on_demand_vram_mib": peak_ondemand,
        "retained_through_lease_mib": retained,
        "peak_vram_mib": peak,
        "peak_state": ("leased" if peak == state_leased and peak_lease
                       else "scheduled" if peak == state_scheduled
                       and peak_ondemand else "resident"),
        "cpu_ram_mib": sum(m.get("ram_mib") or 0 for m in models),
    }


def preview_assignment(assignments, entries, profile, *, overhead_tokens=None):
    """Advice for the picker, computed BEFORE the choice is committed.

    The maintainer's ruling: "always advise the user when they're going to
    overflow the memory with their selections." So this answers, at selection time, the
    three questions a refusal-after-the-fact never does: what does this cost,
    what is left, and what would have to give.

    It never refuses. A selection that does not fit comes back with
    `fits: False`, the overflow in MiB, and `would_evict` naming the seats that
    would make room. The choice stays his -- that has been the standing rule.
    """
    cost = assignment_cost(assignments, entries,
                           overhead_tokens=overhead_tokens)
    budgets = gpu_budgets(profile)
    vram_budget = max((b["available_mib"] for b in budgets), default=0)
    ram = ram_budget(profile)

    peak = cost["peak_vram_mib"]
    overflow = max(0, peak - vram_budget)

    # What would have to give: shed the most expensive resident seats first,
    # because that frees the most per seat surrendered.
    would_evict = []
    if overflow:
        shed = 0
        for m in sorted((m for m in cost["models"]
                         if m["residency"] == RESIDENT),
                        key=lambda m: -(m.get("vram_mib") or 0)):
            if shed >= overflow:
                break
            shed += m.get("vram_mib") or 0
            would_evict.append({"model_id": m["model_id"],
                                "roles": m["roles"],
                                "frees_mib": m.get("vram_mib") or 0})

    missing = [m["model_id"] for m in cost["models"] if not m["installed"]]
    unsized = [m["model_id"] for m in cost["models"]
               if m["installed"] and not m.get("sized", True)]
    ram_need = cost["cpu_ram_mib"] + ram["os_reserve_mib"]

    out = dict(cost)
    out.update({
        "vram_budget_mib": vram_budget,
        "vram_remaining_mib": max(0, vram_budget - peak),
        "ram_budget_mib": ram["hard_ceiling_mib"],
        "ram_projected_mib": ram_need,
        "fits": (overflow == 0 and not missing and not unsized
                 and ram_need <= ram["hard_ceiling_mib"]),
        "overflow_mib": overflow,
        "would_evict": would_evict,
        "not_installed": missing,
        "unsized": unsized,
    })
    out["advice"] = _advice_line(cost, vram_budget, overflow, would_evict,
                                 missing, unsized)
    return out


def _advice_line(cost, vram_budget, overflow, would_evict, missing,
                 unsized=()):
    """One sentence a person can act on, not a dump of numbers."""
    if missing:
        return ("%s is not installed on this machine, so that seat would be "
                "empty." % ", ".join(missing))
    if unsized:
        return ("%s has never been measured on this machine, so the cost of "
                "this lineup is not known. Run one timed load before trusting "
                "it." % ", ".join(unsized))
    shared = [m for m in cost["models"] if len(m["roles"]) > 1]
    note = ""
    if shared:
        note = (" %s covers %d roles and is counted once."
                % (shared[0]["model_id"], len(shared[0]["roles"])))
    if not overflow:
        return ("Fits: %d MiB of %d MiB at peak, %d MiB spare.%s"
                % (cost["peak_vram_mib"], vram_budget,
                   vram_budget - cost["peak_vram_mib"], note))
    give = (", ".join("%s (%d MiB)" % (w["model_id"], w["frees_mib"])
                      for w in would_evict) or "a resident seat")
    return ("Over by %d MiB: %d MiB needed against %d MiB usable. To fit, %s "
            "would have to give.%s"
            % (overflow, cost["peak_vram_mib"], vram_budget, give, note))


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
            # Say WHOSE inventory this is. "installed" was measured against the
            # residency catalogue -- the GGUFs this planner can serve through
            # llama-server, plus what the Ollama daemon reports -- and printed
            # as though it were the machine's whole truth. It can list
            # gemma4:12b/26b/e2b/e4b, none of which `ollama list`
            # returns, and omit every tag that daemon actually holds. Both
            # halves of that are correct about their own source and neither
            # says which source it is, which is how a true list misleads.
            _inv = ", ".join(sorted(by_id)) or "(none)"
            refusals.append(_refusal(
                role, model_id, "R6",
                "override names a model the local planner cannot serve. "
                "Locally placeable right now: %s" % _inv))
            continue
        if role in ("interactive_brain", "sidekick", "heavy_hitter") and \
                not entry.get("can_generate"):
            refusals.append(_refusal(
                role, model_id, "R6",
                "model cannot generate text (capabilities: %s), so it cannot "
                "fill %s" % (", ".join(entry.get("modalities") or []) or "none",
                             role)))
            continue
        # R11, second sentence: "One model may hold several roles and is
        # counted ONCE against the budget." The rule was documented and never
        # implemented. Each override was costed independently, so `gemma4:12b`
        # -- already pinned as interactive_brain -- was charged its full 7,750
        # MiB again for `orchestrator`, again for `sidekick`, and again for
        # `sidekick_fast`, against the 884 MiB left after itself. Three roles
        # the user had assigned were refused for want of memory that the model
        # they name is already occupying, and two of them then collected an R11
        # "no model assigned" on top, for a total of five refusals describing
        # one model that was loaded and working.
        #
        # A second role on an already-placed model loads nothing: it is the
        # same weights, on the same device, in the same process. It therefore
        # inherits that placement wholesale -- including its context, because
        # one loaded instance has one context and pretending otherwise would
        # budget for a window that does not exist -- and is charged 0 MiB so
        # `_finish` and `retained_mib` count the weights once.
        #
        # `sorted(seats)` rather than dict order so the seat that gets named as
        # the holder is the same one on every run, fixture and machine.
        holder = next((r for r in sorted(seats)
                       if r != role and (seats.get(r) or {}).get("model_id") == model_id),
                      None)
        if holder is not None:
            shared = dict(seats[holder])
            shared["vram_mib"] = 0
            shared["shares_model_with"] = holder
            # R7 still holds: the context is explicit. It is explicitly the
            # holder's, and says so rather than looking independently chosen.
            shared["num_ctx_from"] = holder
            shared["from_override"] = True
            seats[role] = shared
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
        # overrides were never supplied; feeding the user's choices in
        # walks straight through it, and over-committing this
        # card is what drops a second monitor. An unseated role has no
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


# ── Verdicts — three axes, never "compatible" (headroom.md §5.2) ────────────

def _unknown_verdict(why: str) -> dict:
    return {"status": "unknown", "basis": "unknown", "explanation": why}


def _fits_verdict(fp: dict, basis: str, profile: dict, model_id: str) -> dict:
    """Can it be placed under the contract at all?

    R2/R3/R8 arithmetic today (no D1 Contract yet — see `verdicts()`'s own
    docstring). `ready-but` here specifically covers the case the table names
    it for on a DECLARED number: the model's own guidance is the only figure
    that exists, so whether it truly fits is contingent on evicting other
    seats and confirming it — exactly the thing only a real placement (the
    chain planner, headroom.md §6, not built in this phase) can settle. HR18:
    that contingency is rendered as `ready-but`, never `refused` — only a
    MEASURED shortfall refuses.
    """
    if fp.get("device") == "cpu":
        # CPU services take no VRAM; R2 (host RAM) is runs_well's question.
        return {"status": "ready", "basis": basis,
               "explanation": "%s runs on CPU; no VRAM to place" % model_id}

    vram_mib = fp.get("vram_mib")
    vram_min = (fp.get("requires") or {}).get("vram_min_mib")
    need = vram_mib if vram_mib is not None else vram_min
    if need is None:
        return _unknown_verdict(
            "%s has no measured or declared VRAM figure" % model_id)

    budgets = gpu_budgets(profile)
    if not budgets:
        # A GPU-only model with no GPU on the profile: this is a genuine,
        # rule-backed refusal (R5) regardless of basis — there is no card to
        # be wrong about, so HR18's "declared never refuses" does not apply.
        return {"status": "refused", "basis": basis, "rule_id": "R5",
               "explanation": "%s needs a GPU; none on this profile"
                              % model_id}
    free = max((b.get("available_mib") or 0) for b in budgets)

    if free >= need:
        return {"status": "ready", "basis": basis, "vram_mib": need,
               "free_mib": free,
               "explanation": "%d MiB needed, %d MiB free" % (need, free)}
    # HR18 — only a MEASURED shortfall refuses. Gated on `basis`, not on
    # which field carried the number: a `declared` footprint can still set
    # `vram_mib` directly (not only `requires.vram_min_mib`), and it must
    # not refuse either way.
    if basis != "measured":
        return {"status": "ready-but", "basis": basis, "vram_mib": need,
               "free_mib": free,
               "explanation": (
                   "the model's own guidance says at least %d MiB; not "
                   "confirmed on this machine (%d MiB free after the "
                   "existing seats) — a declared figure is never a refusal"
                   % (need, free))}
    return {"status": "refused", "basis": basis, "rule_id": "R3",
           "vram_mib": need, "free_mib": free,
           "explanation": "measured %d MiB needed, only %d MiB free"
                          % (need, free)}


def _runs_well_verdict(fp: dict, basis: str, profile: dict,
                       model_id: str) -> dict:
    """Will the machine stay usable and will it finish in reasonable time?

    Reads RAM against `requires.ram_recommended_mib` / `requires.ram_min_mib`
    (declared), `host_ram_mib` (measured, CPU services), and this model's
    recorded thrash history on this profile (`residency_catalog.
    thrash_degraded`, headroom.md §7). A `degraded` verdict is never a
    refusal on its own axis — `fits` already carried HR18's
    refuse-only-when-measured rule, and `runs_well` has no `refused` value
    at all (§5.2's table): the worst it says is `degraded`, with the reason
    named, and the fetch stays offered (D4).
    """
    requires = fp.get("requires") or {}
    ram_rec = requires.get("ram_recommended_mib")
    ram_min = requires.get("ram_min_mib")
    host_ram = fp.get("host_ram_mib")

    ram = profile.get("ram") or {}
    avail = ram.get("available_mib") or ram.get("total_mib")
    problems = []
    if avail:
        if ram_rec and avail < ram_rec:
            problems.append(
                "the model's own guidance recommends %d MiB RAM; this "
                "machine has %d" % (ram_rec, avail))
        if ram_min and avail < ram_min:
            problems.append(
                "needs at least %d MiB RAM; this machine has %d"
                % (ram_min, avail))
        if host_ram and avail < host_ram:
            problems.append(
                "measured %d MiB host RAM at load, %d MiB available"
                % (host_ram, avail))

    # §5.2's own "decided from" column for runs_well names "the thrash
    # history for this model on this profile" alongside the RAM figures
    # above. `residency_arbiter.Arbiter._on_thrash_breach` (headroom.md §7)
    # writes this the moment a sustained thrash signature fires against a
    # leased seat; reading it back here is what makes that a standing
    # verdict rather than a one-time log line the next `verdicts()` call
    # never sees.
    try:
        from agent_friday.services import residency_catalog as cat
        thrash = cat.thrash_degraded(model_id, cat.profile_fingerprint(profile))
    except Exception:
        thrash = None
    if thrash:
        problems.append(
            "thrashing observed on %s: %s"
            % (thrash.get("at") or "an earlier run",
               thrash.get("explanation")
               or "sustained low-power/high-utilisation GPU signature"))

    if problems:
        return {"status": "degraded", "basis": basis,
               "explanation": "; ".join(problems)}
    if ram_rec is None and ram_min is None and host_ram is None:
        return _unknown_verdict(
            "no RAM or throughput figure recorded for %s on this machine"
            % model_id)
    return {"status": "ready", "basis": basis,
           "explanation": "fits within the measured/declared RAM guidance"}


def _worth_it_verdict(fp: dict, basis: str) -> dict:
    """Is the output something the user wants? Licence and quality only —
    HR16: shown, never enforced. Absence of either is not a demerit; a row
    with neither reads plainly `ready` and, per §8.2, "licence not
    recorded" rather than a withheld verdict."""
    licence = fp.get("licence")
    quality = fp.get("quality_note")
    if licence is None and quality is None:
        return {"status": "ready", "basis": basis,
               "explanation": "no licence or quality note recorded"}
    bits = []
    if licence:
        bits.append(licence.get("note") or licence.get("name")
                    or "licence recorded — see the row for the URL")
    if quality:
        bits.append(quality)
    return {"status": "ready-but", "basis": basis, "explanation": "; ".join(bits)}


def verdicts(entry: dict, profile: dict, contract=None) -> dict:
    """`{fits, runs_well, worth_it}` — three axes, never a single
    "compatible" (headroom.md §5.2).

    `entry` is a CatalogEntry (`residency_catalog.entry()` / `store_entry()`)
    or any dict carrying at least `model_id`; the Footprint itself is looked
    up from `residency_catalog.footprint()`, not read off `entry`, because a
    Footprint outlives any one CatalogEntry snapshot.

    `contract` is accepted for the future Headroom Contract (D1 — not
    decided, see `headroom_contract.py`'s own docstring) and is NOT read:
    this degrades to the EXISTING R2/R3/R8 budget arithmetic
    (`gpu_budgets`, `ram_budget`) rather than inventing D1's slack/RAM-floor
    numbers, so `verdicts()` is usable today instead of waiting on it.

    **HR1** — a verdict without a basis is not a verdict. No footprint, or a
    footprint whose own `basis` is `"unknown"`, returns `unknown` on ALL
    THREE axes; nothing here ever renders `ready` from nothing.
    **HR2** — three axes are always returned; there is no combined verdict.
    **HR18** — a `declared` number never refuses. Only a `measured` shortfall
    produces `status: "refused"` (see `_fits_verdict`); a declared one
    becomes `ready-but`, and `runs_well` has no `refused` state at all.
    """
    from agent_friday.services import residency_catalog as cat

    model_id = (entry or {}).get("model_id") if isinstance(entry, dict) \
        else None
    fp = cat.footprint(model_id, profile) if model_id else None

    if fp is None:
        why = ("%s has not been measured on this machine" % model_id
              if model_id else "no model_id given")
        return {"fits": _unknown_verdict(why),
               "runs_well": _unknown_verdict(why),
               "worth_it": _unknown_verdict(why)}

    basis = fp.get("basis")
    if basis not in ("measured", "derived", "declared"):
        why = "%s's footprint carries no basis Friday can act on" % model_id
        return {"fits": _unknown_verdict(why),
               "runs_well": _unknown_verdict(why),
               "worth_it": _unknown_verdict(why)}

    return {
        "fits": _fits_verdict(fp, basis, profile, model_id),
        "runs_well": _runs_well_verdict(fp, basis, profile, model_id),
        "worth_it": _worth_it_verdict(fp, basis),
    }


def num_ctx_for_model(model_id: str, default: int = TOOL_SEAT_NUM_CTX) -> int:
    """The context the PLAN specifies for whichever seat holds `model_id`.

    Dispatch must apply this, not just the Arbiter at boot. Otherwise the
    Arbiter loads a seat at the planned context, then the first
    ordinary chat request reloads the same model at Ollama's default and the
    placement is silently lost. Measured consequence on the reference machine
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


# ── Chains — planning the sequence the maintainer described (headroom.md §6) ─
#
# "speak, transcribe, reason, render, speak back" is not one lease, it is a
# SEQUENCE of them, and today each stage takes and releases its own lease
# independently -- the brain reloading between them, or refused outright by
# `_local_brain_ready()` while an image lease holds the card (§2.6). A chain
# is the plan for the sequence, not just the next single lease.

DEFAULT_STT_MODEL = "faster-whisper-small-int8"   # footprint_measure's own id
DEFAULT_TTS_MODEL = "piper-en_us-amy-medium"       # ditto, for the TTS half


def _chain_default_model(role: str, resident: dict) -> str | None:
    seat = (resident or {}).get(role)
    if seat and seat.get("model_id"):
        return seat["model_id"]
    return {"image": DEFAULT_IMAGE_MODEL, "stt": DEFAULT_STT_MODEL,
           "tts": DEFAULT_TTS_MODEL}.get(role)


def _chain_stage_gpu_idx(budgets: list, multi_gpu: bool) -> int | None:
    """Which GPU an exclusive (image/video-shaped) lease takes: R5's own
    choice in `plan()` -- the LAST GPU in availability order when there is
    more than one, so the language seats keep the biggest card and an
    exclusive render gets whichever is left (§6.3 P4)."""
    if not budgets:
        return None
    order = sorted(budgets, key=lambda b: (-b["available_mib"], b["index"]))
    return order[-1]["index"] if multi_gpu else order[0]["index"]


def _chain_gpu_available(budgets: list, idx: int | None) -> int:
    if idx is None:
        return 0
    for b in budgets:
        if b["index"] == idx:
            return b["available_mib"]
    return 0


def plan_chain(profile: dict, entries: list, stages: list,
               contract: dict | None = None, resident: dict | None = None,
               *, cloud_ok: bool = True, strict_vault: bool = False,
               _alt: int | None = None) -> dict:
    """(HardwareProfile, Catalog, [stage], Contract, current seats) -> ChainPlan.

    Pure, like `plan()` -- a function of its inputs only, `cloud_ok` and
    `strict_vault` included: the caller reads whether a cloud key exists
    (`work_plan._cloud_available()`'s own check, HR8) and whether D2's
    stricter setting is on, and hands the facts in rather than this module
    reaching for settings or the network itself.

    `resident` is the caller's own snapshot of what is ACTUALLY loaded right
    now -- typically `Arbiter.plan["seats"]` -- because a chain reasons about
    a real sequence of grant/release cycles starting from the machine as it
    is, not a freshly recomputed ideal lineup. `plan()` already answers "what
    SHOULD be resident"; this answers "what does running THIS sequence, from
    here, cost". Each entry is `{model_id, device, vram_mib, voice_bound?}`.

    `stages` is `[{role, model_id?, units?, touches_vault?}]` -- section 6.1's
    shape. A stage with no `model_id` takes whatever is already resident for
    that role, or the fixed default for image/stt/tts (section 5.3's own
    model ids).

    `entries` (the CatalogEntry list `plan()` also takes) is accepted for
    signature parity with that function and for a future caller that wants
    to pick a NEW role's model from the installed set, but is not read here:
    every number this function needs -- VRAM, load time, work rate --
    already lives in `residency_catalog.footprint()`, keyed by
    `(model_id, profile fingerprint)`, which covers every modality uniformly
    (Phase 2), where `entries` only ever described text.

    Rules, headroom.md section 6.2:

    1. One lease at a time. Stages are planned strictly in order; a leased
       stage's `transitions` are the only VRAM movement between it and its
       neighbours. `Arbiter.run_chain` executes one grant/release pair per
       leased stage -- this function does not relax that lock, it only
       reasons about what each pair would cost.
    2. Reload versus refuse decided by number, and reload usually wins. A
       stage refuses locally ONLY when it cannot fit even with everything
       non-retained evicted -- computed here as the target GPU's
       `gpu_budgets` figure minus whatever R10 (plus rule 4) keeps resident
       through the lease. Otherwise the plan carries the reload cost in
       `transitions`. `total_est_s` is exactly the number
       `workflow_plan.ASK_ABOVE_S` is compared against by the caller; this
       function does not itself decide to ask.
    3. HR1 -- no stage plans into `unknown`. A footprint that is missing, or
       whose own `basis` is "unknown", never renders as fit: it moves to
       `cloud` when one is available, or `refused` with the explanation
       naming what would measure it -- never placed on the strength of
       nothing.
    4. The retained set is a stage property. R10's sidekick, PLUS any
       `resident` entry carrying `voice_bound: True` -- the seat a live voice
       session is bound to survives every stage the same way, so section
       2.6 step 4's failure (voice refused mid-chain) cannot happen from a
       chain built here.
    5. Vault stages cannot move to cloud (D2's resolution). A stage whose own
       `touches_vault` is set, or that comes AFTER one in the same chain (the
       composites-inherit-provenance rule, `_route_vault`'s rule extended per
       section 6.2), has no cloud alternative and says why. This function
       does not consult the vault or the egress gate itself -- `touches_vault`
       is the caller's own answer from the router/egress layer, per the
       rule's own text: "the planner asks the egress gate, it does not
       decide." `strict_vault=True` is D2's stricter, default-off
       alternative: once ANY stage touches the vault, no stage in the chain
       (before or after it) may use cloud -- an inert setting until the
       maintainer picks between the two (D2 is that decision, not this
       function's).

    `contract` is accepted, like `verdicts()`'s own, for the future Headroom
    Contract (D1 -- not decided) and is NOT read for a VRAM-slack or
    RAM-available floor: `contract_ok` here is the SAME R3/gpu-budget
    arithmetic `plan()` and `verdicts()` already use -- every stage actually
    PLACED locally (not refused, not moved to cloud) fits what is really left
    after the retained set. A stage that correctly declined to overload the
    machine -- by going to cloud or by refusing -- is the contract WORKING,
    not a violation of it. D1's extra floors are not guessed at just to make
    this look more finished than it is.

    `video` is always `refused` locally (D8 -- no local backend exists) and
    renders `where: "cloud"` whenever `cloud_ok` -- the one-sentence surface
    section 5.3/8.2 describe, not a declared row per candidate model.
    """
    from agent_friday.services import residency_catalog as rc

    resident = dict(resident or {})
    budgets = gpu_budgets(profile)
    multi_gpu = len(budgets) >= 2
    exclusive_idx = _chain_stage_gpu_idx(budgets, multi_gpu)

    # R10 + rule 4 -- model ids that survive every leased stage.
    retained_ids: set = set()
    for role, seat in resident.items():
        if not seat:
            continue
        if role in RETAINED_THROUGH_LEASE and seat.get("model_id"):
            retained_ids.add(seat["model_id"])
        if seat.get("voice_bound") and seat.get("model_id"):
            retained_ids.add(seat["model_id"])

    # `current`: role -> model_id that BELONGS in that role, seeded from what
    # is really resident right now. Entries are never deleted, even while a
    # lease has that role's model standing down -- `displaced_now` /
    # `_restore_before` is what makes that temporary, and every stage calls
    # it before its own logic runs, so `current` is always an accurate
    # picture of "what is loaded right now" at the moment each stage reads
    # it.
    current = {role: seat.get("model_id") for role, seat in resident.items()
              if seat and seat.get("model_id")}
    current_device = {role: seat.get("device") for role, seat in
                      resident.items() if seat}
    current_vram = {role: seat.get("vram_mib") or 0
                    for role, seat in resident.items() if seat}

    # Per-GPU running total, so `peak_mib` is the largest CONCURRENT
    # commitment this chain ever makes -- a retained sidekick sitting beside
    # a render is the number that actually matters (section 3.2), not
    # either figure alone.
    gpu_used: dict = {}
    for role, dev in current_device.items():
        if dev and str(dev).startswith("gpu:"):
            idx = int(dev.split(":")[1])
            gpu_used[idx] = gpu_used.get(idx, 0) + current_vram.get(role, 0)

    out_stages: list = []
    transitions: list = []
    displaced_now: list = []      # roles the last leased stage stood down
    peak_mib = max(gpu_used.values()) if gpu_used else 0
    total_est_s = 0.0
    # Two flags, not one -- a stt/tts stage whose HOST RAM was never measured
    # (§2.5's "uncounted" voice figure, Phase 2's own U6) makes `total_est_s`
    # honestly unknown but says NOTHING about whether the GPU stages fit: a
    # CPU service's footprint_mib is always 0, known, not a VRAM guess. Only
    # collapsing `peak_mib`/`contract_ok` to None when a GPU-relevant figure
    # is actually missing keeps HR1 honest without making every real chain
    # (voice RAM is unmeasured on every machine until `friday measure voice`
    # runs, headroom.md §12 Phase 2.2) report "unknown" on axes that were
    # never in question.
    gpu_unknown = False
    time_unknown = False
    contract_ok = True

    any_vault = any(bool(s.get("touches_vault")) for s in stages)
    vault_from = None

    def _touch_peak():
        nonlocal peak_mib
        if gpu_used:
            peak_mib = max(peak_mib, max(gpu_used.values()))

    def _restore_before(idx: int):
        """The transition that reloads whatever the last leased stage stood
        down -- attached to the stage about to run, or to `len(stages)` when
        the chain ends still displaced (which `Arbiter.release()`'s own
        `_restore_pinned` performs for real, HR10)."""
        nonlocal displaced_now, time_unknown
        if not displaced_now:
            return
        loads, est, basis = [], 0.0, "measured"
        for role in displaced_now:
            mid = current.get(role)
            if not mid:
                continue
            fp = rc.footprint(mid, profile)
            load_s = (fp or {}).get("load_s")
            if load_s is None:
                time_unknown = True
                basis = "unknown"
            else:
                est += load_s
            loads.append(mid)
            dev = current_device.get(role)
            if dev and str(dev).startswith("gpu:"):
                gidx = int(dev.split(":")[1])
                gpu_used[gidx] = gpu_used.get(gidx, 0) + current_vram.get(
                    role, 0)
        if loads:
            transitions.append({
                "before_stage": idx, "evict": [], "load": loads,
                "est_s": round(est, 1) if basis != "unknown" else None,
                "basis": basis})
            _touch_peak()
        displaced_now = []

    def _cloud_stage(role, model_id, why):
        return {"role": role, "model_id": model_id, "where": "cloud",
               "footprint_mib": None, "basis": "declared",
               "est_work_s": None, "refusal": None, "note": why}

    def _refused_stage(role, model_id, rule_id, why, basis="unknown"):
        return {"role": role, "model_id": model_id, "where": "refused",
               "footprint_mib": None, "basis": basis, "est_work_s": None,
               "refusal": _refusal(role, model_id, rule_id, why)}

    for i, raw in enumerate(stages):
        role = resolve_role(raw.get("role"))
        model_id = raw.get("model_id") or _chain_default_model(role, resident)
        units = raw.get("units", 1)
        if bool(raw.get("touches_vault")) and vault_from is None:
            vault_from = i
        vault_blocked = (vault_from is not None and i >= vault_from) or \
            (strict_vault and any_vault)
        force_cloud = (_alt == i)

        _restore_before(i)   # whatever the PRIOR leased stage stood down

        # -- video: no local backend exists at all (D8). --------------------
        if role == "video":
            if cloud_ok and not vault_blocked:
                out_stages.append(_cloud_stage(
                    role, model_id, "video runs in the cloud on every "
                    "machine today -- no local backend exists"))
            else:
                out_stages.append(_refused_stage(
                    role, model_id, "R5",
                    "no local video backend exists" +
                    ("; this chain reads vault-tier material, so no cloud "
                     "alternative is offered for this stage" if vault_blocked
                     else "; no cloud provider is configured either"),
                    basis="declared"))
            continue

        # -- CPU services: always placeable, never gate on VRAM. ------------
        if role in ("stt", "tts"):
            fp = rc.footprint(model_id, profile) if model_id else None
            work_s = None
            if fp and fp.get("work_s_per_unit") is not None:
                work_s = round(fp["work_s_per_unit"] * units, 2)
            elif fp is None:
                time_unknown = True
            out_stages.append({
                "role": role, "model_id": model_id, "where": "cpu",
                "footprint_mib": 0, "basis": (fp or {}).get("basis")
                or "unknown", "est_work_s": work_s, "refusal": None})
            if work_s is not None:
                total_est_s += work_s
            continue

        # -- image: R5, exclusive lease, a real footprint per Phase 2. ------
        if role == "image":
            if force_cloud:
                out_stages.append(_cloud_stage(
                    role, model_id, "moved to the cloud for this "
                    "alternative"))
                continue
            if not budgets:
                if cloud_ok and not vault_blocked:
                    out_stages.append(_cloud_stage(
                        role, model_id,
                        "no GPU on this profile to lease locally"))
                else:
                    out_stages.append(_refused_stage(
                        role, model_id, "R5",
                        "no GPU to lease, and no cloud alternative "
                        "available", basis="declared"))
                continue
            fp = rc.footprint(model_id, profile) if model_id else None
            basis = (fp or {}).get("basis")
            need = (fp or {}).get("vram_mib")
            if fp is None or basis == "unknown" or need is None:
                gpu_unknown = True
                time_unknown = True
                if cloud_ok and not vault_blocked:
                    out_stages.append(_cloud_stage(
                        role, model_id,
                        "%s has not been measured on this machine yet"
                        % model_id))
                else:
                    out_stages.append(_refused_stage(
                        role, model_id, "R3",
                        "%s has no measured or declared footprint, and no "
                        "cloud alternative is available" % model_id))
                continue
            # A real number in hand (headroom.md section 12 Phase 2.3). R5:
            # exclusive of everything except the retained set on THIS GPU.
            retained_here = 0
            evict_roles = []
            for r, mid in current.items():
                if current_device.get(r) != ("gpu:%d" % exclusive_idx):
                    continue
                if mid in retained_ids:
                    retained_here += current_vram.get(r, 0)
                else:
                    evict_roles.append(r)
            avail = _chain_gpu_available(budgets, exclusive_idx)
            lease_budget = max(0, avail - retained_here)
            if need <= lease_budget:
                for r in evict_roles:
                    gpu_used[exclusive_idx] = gpu_used.get(
                        exclusive_idx, 0) - current_vram.get(r, 0)
                displaced_now = evict_roles
                load_s = fp.get("load_s")
                if evict_roles:
                    transitions.append({
                        "before_stage": i,
                        "evict": [current[r] for r in evict_roles],
                        "load": [], "est_s": load_s, "basis": basis})
                # Captures the render's own peak, then releases it: the
                # lease gives the GPU back at the end of THIS stage (the real
                # `Arbiter.release()` stops ComfyUI), so `gpu_used` must not
                # carry the image model's footprint into the NEXT stage's
                # accounting -- only `retained_here` (the sidekick) survives.
                gpu_used[exclusive_idx] = retained_here + need
                _touch_peak()
                gpu_used[exclusive_idx] = retained_here
                if load_s is None:
                    time_unknown = True
                out_stages.append({
                    "role": role, "model_id": model_id, "where": "leased",
                    "footprint_mib": need, "basis": basis,
                    "est_work_s": fp.get("work_s_per_unit"),
                    "refusal": None, "retained_mib": retained_here,
                    "exclusive_of": [current[r] for r in evict_roles]})
                total_est_s += (fp.get("work_s_per_unit") or 0) + \
                    (load_s or 0)
            elif basis == "measured":
                # HR18 -- only a MEASURED shortfall refuses. This is the
                # real measured number: it can fail to fit even with
                # everything but the retained set evicted (the golden
                # fixtures include a case where this happens).
                if cloud_ok and not vault_blocked:
                    out_stages.append(_cloud_stage(
                        role, model_id,
                        "measured %d MiB needed, only %d MiB free with the "
                        "retained set on this card -- refused locally (R3)"
                        % (need, lease_budget)))
                else:
                    out_stages.append(_refused_stage(
                        role, model_id, "R3",
                        "measured %d MiB needed, only %d MiB free even with "
                        "everything but the retained set evicted, and no "
                        "cloud alternative is available"
                        % (need, lease_budget), basis=basis))
                    contract_ok = False
            else:
                # declared/derived -- HR18 never refuses on its own; offered,
                # marked unconfirmed, per section 5.2's "ready-but".
                for r in evict_roles:
                    gpu_used[exclusive_idx] = gpu_used.get(
                        exclusive_idx, 0) - current_vram.get(r, 0)
                displaced_now = evict_roles
                if evict_roles:
                    transitions.append({
                        "before_stage": i,
                        "evict": [current[r] for r in evict_roles],
                        "load": [], "est_s": fp.get("load_s"),
                        "basis": basis})
                # Captures the render's own peak, then releases it: the
                # lease gives the GPU back at the end of THIS stage (the real
                # `Arbiter.release()` stops ComfyUI), so `gpu_used` must not
                # carry the image model's footprint into the NEXT stage's
                # accounting -- only `retained_here` (the sidekick) survives.
                gpu_used[exclusive_idx] = retained_here + need
                _touch_peak()
                gpu_used[exclusive_idx] = retained_here
                out_stages.append({
                    "role": role, "model_id": model_id, "where": "leased",
                    "footprint_mib": need, "basis": basis,
                    "est_work_s": fp.get("work_s_per_unit"),
                    "refusal": None, "retained_mib": retained_here,
                    "exclusive_of": [current[r] for r in evict_roles],
                    "note": "the model's own guidance says %d MiB; not "
                            "confirmed on this machine" % need})
                total_est_s += (fp.get("work_s_per_unit") or 0) + \
                    (fp.get("load_s") or 0)
            continue

        # -- every other role: text seats -- resident/leased/cloud/refused. -
        if force_cloud:
            out_stages.append(_cloud_stage(
                role, model_id, "moved to the cloud for this alternative"))
            continue
        if model_id and current.get(role) == model_id:
            # Already loaded (`_restore_before` already put it back if a
            # prior stage had stood it down) -- nothing to do.
            fp = rc.footprint(model_id, profile)
            out_stages.append({
                "role": role, "model_id": model_id,
                "where": ("resident" if residency_of(role) == RESIDENT
                          else "leased"),
                "footprint_mib": current_vram.get(role,
                                                  (fp or {}).get("vram_mib")),
                "basis": (fp or {}).get("basis") or "measured",
                "est_work_s": None, "refusal": None})
            continue
        if model_id is None:
            if role in ASSIGNED_ROLES:
                # R11's own meaning: a role the USER chooses, not the policy
                # -- unset is a configuration gap, not a hardware question,
                # so there is no cloud alternative to offer for it either.
                out_stages.append(_refused_stage(
                    role, None, "R11",
                    "no model assigned to %s for this chain" % role))
            elif cloud_ok and not vault_blocked:
                # interactive_brain/heavy_hitter/sidekick/sidekick_heavy: the
                # policy would normally choose one, and on this profile it
                # has nothing to choose (P6's unified-memory backend gap,
                # e.g.) -- the honest analogue of image's "no GPU -> cloud"
                # escalation, not a configuration refusal.
                out_stages.append(_cloud_stage(
                    role, None,
                    "no local model is available for %s on this profile"
                    % role))
            else:
                out_stages.append(_refused_stage(
                    role, None, "R3",
                    "no local model is available for %s on this profile, "
                    "and no cloud alternative is available" % role))
            continue
        # A new placement, or a reload of a role a leased stage stood down.
        fp = rc.footprint(model_id, profile)
        need = (fp or {}).get("vram_mib")
        basis = (fp or {}).get("basis")
        if fp is None or basis == "unknown":
            gpu_unknown = True
            time_unknown = True
            if cloud_ok and not vault_blocked:
                out_stages.append(_cloud_stage(
                    role, model_id,
                    "%s has not been measured on this machine yet"
                    % model_id))
            else:
                out_stages.append(_refused_stage(
                    role, model_id, "R3",
                    "%s has no measured or declared footprint" % model_id))
            continue
        idx = exclusive_idx
        used_elsewhere = sum(
            current_vram.get(r, 0) for r, dev in current_device.items()
            if dev == ("gpu:%d" % idx) and r != role)
        avail = max(0, _chain_gpu_available(budgets, idx) - used_elsewhere) \
            if idx is not None else 0
        if need is None or basis != "measured" or need <= avail:
            current[role] = model_id
            current_device[role] = ("gpu:%d" % idx) if idx is not None \
                else "cpu"
            current_vram[role] = need or 0
            if idx is not None:
                gpu_used[idx] = gpu_used.get(idx, 0) + (need or 0)
                _touch_peak()
            transitions.append({
                "before_stage": i, "evict": [], "load": [model_id],
                "est_s": (fp or {}).get("load_s"), "basis": basis})
            out_stages.append({
                "role": role, "model_id": model_id,
                "where": "leased" if idx is not None else "cpu",
                "footprint_mib": need, "basis": basis,
                "est_work_s": None, "refusal": None})
            if need is None:
                # Placed on a `declared`/`derived` figure with no VRAM
                # number at all (HR18 never refuses on it, but the GPU
                # commitment genuinely is not known — do not count it as a
                # confident 0 MiB).
                gpu_unknown = True
            if (fp or {}).get("load_s") is None:
                time_unknown = True
            else:
                total_est_s += fp["load_s"]
        else:
            if cloud_ok and not vault_blocked:
                out_stages.append(_cloud_stage(
                    role, model_id,
                    "measured %d MiB needed, only %d MiB free -- refused "
                    "locally (R3)" % (need, avail)))
            else:
                out_stages.append(_refused_stage(
                    role, model_id, "R3",
                    "measured %d MiB needed, only %d MiB free, and no "
                    "cloud alternative is available" % (need, avail),
                    basis=basis))
                contract_ok = False

    _restore_before(len(stages))

    plan_out = {
        "stages": out_stages,
        "transitions": transitions,
        "retained": sorted(retained_ids),
        "peak_mib": None if gpu_unknown else peak_mib,
        "total_est_s": None if time_unknown else round(total_est_s, 1),
        "contract_ok": None if gpu_unknown else contract_ok,
        "alternatives": [],
    }

    if _alt is None:
        alts = []
        for i, raw in enumerate(stages):
            role = resolve_role(raw.get("role"))
            st = out_stages[i]
            if st["where"] in ("cloud", "refused") or role in ("stt", "tts"):
                continue
            blocked = (vault_from is not None and i >= vault_from) or \
                (strict_vault and any_vault)
            if blocked or not cloud_ok:
                continue
            alt = plan_chain(profile, entries, stages, contract, resident,
                             cloud_ok=cloud_ok, strict_vault=strict_vault,
                             _alt=i)
            alts.append({"moved_stage": i, "role": role, "plan": alt})
        if cloud_ok:
            alts.append({
                "execution": "when_away",
                "note": "the whole chain, parked until the machine is idle, "
                        "then run under one lease -- same placement, no "
                        "urgency.",
                "stages": out_stages,
            })
        plan_out["alternatives"] = alts

    return plan_out
