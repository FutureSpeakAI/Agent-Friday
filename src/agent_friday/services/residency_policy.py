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

ROLES = ("interactive_brain", "heavy_hitter", "sidekick", "embedder",
         "stt", "tts", "image")

# ── Rules as data ────────────────────────────────────────────────────────────

VRAM_RESERVE_MIB = 1024          # R3
RAM_CEILING_HARD = 0.75          # R2
RAM_CEILING_TARGET = 0.65        # R2
DISK_FLOOR_MIB = 10 * 1024       # R8

# Expert layers held on CPU for an offloaded MoE. Measured, not guessed:
# the reference sweep peaked inside the VRAM budget at 20 (27.80 tok/s,
# 9802 MiB) and collapsed at 12 (14.94 tok/s, host RAM 31.5 of 31.9 GB).
MOE_CPU_LAYERS_DEFAULT = 20

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
]

RULE_BY_ID = {r["id"]: r for r in RULES}

# Default contexts per role. R7: something explicit always wins over a
# backend default. Chosen from the measured KV curve -- on the gemma4 family
# context is nearly free (12b: 7690 MiB at 4k vs 8001 at 16k), so the
# interactive seat can afford a generous window.
DEFAULT_NUM_CTX = {
    "interactive_brain": 16384,
    "heavy_hitter": 16384,
    "sidekick": 8192,
    "embedder": 2048,
}


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

def plan(profile: dict, entries: list, overrides: dict | None = None) -> dict:
    """(HardwareProfile, Catalog, overrides) -> PlacementPlan. Pure."""
    overrides = dict(overrides or {})
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
            v = _required_vram(e, DEFAULT_NUM_CTX["interactive_brain"])
            if v is not None and v <= cap:
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
        """Fit `entry` on one GPU. Returns a Placement or None."""
        ctx = DEFAULT_NUM_CTX.get(role, 8192)
        v = _required_vram(entry, ctx)
        if v is None:
            return None
        idxs = ([prefer] if prefer is not None else
                [b["index"] for b in order])
        for i in idxs:
            if i is not None and free.get(i, 0) >= v:
                free[i] -= v
                return _placement(entry, role, "gpu:%d" % i, ctx, status, v)
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
        seats["heavy_hitter"], hr = _heavy(heavy, heavy_seat, budgets, free,
                                           ram, profile)
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
            "displaces": ("gpu:%d only" % idx) if multi_gpu else "all seats",
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

    _apply_overrides(seats, refusals, overrides, entries, free, budgets)
    return _finish(profile, seats, refusals, budgets, ram)


def _heavy(heavy, preplaced, budgets, free, ram, profile):
    """Place the heavy seat, or refuse it with arithmetic.

    The capacity question for a LEASED seat is the GPU's whole budget, not the
    residual after the pinned seats: a lease is exactly the moment the Arbiter
    is allowed to evict them. Asking only about the residual would offload a
    model that would have fit outright once the brain stepped aside.
    """
    ctx = DEFAULT_NUM_CTX["heavy_hitter"]
    measured_vram = _vram_for(heavy, ctx)
    total = _required_vram(heavy, ctx)
    if preplaced is not None:
        return preplaced, None

    if budgets and total is not None:
        best = max(budgets, key=lambda b: (b["available_mib"], -b["index"]))
        if total <= best["available_mib"]:
            i = best["index"]
            if free.get(i, 0) >= total:
                free[i] -= total
                seat = _placement(heavy, "heavy_hitter", "gpu:%d" % i, ctx,
                                  "pinned", total)
            else:
                seat = _placement(heavy, "heavy_hitter", "gpu:%d" % i, ctx,
                                  "leased", total)
                seat["displaces"] = "pinned seats on gpu:%d" % i
            return seat, None

    # Does not fit whole. The GPU keeps what it can hold; the rest is host RAM.
    if budgets:
        cap = max(b["available_mib"] for b in budgets)
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
    seat["offload"] = {
        "expert_offload": bool(heavy.get("is_moe")),
        "host_mib": host_mib,
        # The measured operating point, carried in the PLAN rather than left
        # implicit in the Arbiter. Swept on the reference instance: 27.80 tok/s
        # at 9802 MiB, against a collapse to 14.94 at --n-cpu-moe 12 where host
        # RAM hit 31.5 of 31.9 GB. 16 is faster (31.34) and exceeds the R3
        # ceiling by 173 MiB, so it is not offered.
        "n_cpu_moe": MOE_CPU_LAYERS_DEFAULT if (budgets and heavy.get("is_moe"))
        else None,
    }
    seat["over_target"] = need > ram["target_ceiling_mib"]
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


def _apply_overrides(seats, refusals, overrides, entries, free, budgets):
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
        ctx = DEFAULT_NUM_CTX.get(role, 8192)
        need = _vram_for(entry, ctx)
        cap = max(free.values()) if free else 0
        cur = seats.get(role)
        if budgets and need is not None and cur is not None and \
                cur.get("device", "").startswith("gpu") and need > cap + \
                (cur.get("vram_mib") or 0):
            refusals.append(_refusal(
                role, model_id, "R3",
                "override needs %d MiB but only %d MiB is available on the "
                "largest GPU after the other pinned seats; nearest permitted: "
                "%s" % (need, cap + (cur.get("vram_mib") or 0),
                        cur.get("model_id"))))
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
