"""Decide which local models this machine can actually run, and say why not.

The installer's whole model story rests on this module, so it is deliberately a
PURE FUNCTION of a hardware profile: no downloads, no daemon calls, no writes.
That makes it testable without a GPU, an Ollama daemon, or a network — which
matters, because the alternative is an installer whose central decision is only
exercised on the machine that wrote it.

    plan = model_plan.plan(hardware_profile.get())

Design principles, all of them earned the hard way (see KNOWN_ISSUES.md §1):

* **Every refusal names the rule and the number.** "Not enough memory" is not an
  answer a user can act on. "8,192 MiB total, minus 6,144 MiB reserved for
  Windows, times the 0.75 ceiling = 0 MiB available (rule R2)" is.
* **Nothing is reported as available on the strength of a name.** A tier is
  ready only when its requirement is arithmetically satisfied by measured
  numbers.
* **If a model is not consumed, it is not installed.** No weights are fetched
  for a seat that is not live, however plausible the seat sounds.

## What the floor actually is

**Memory runs without a GPU.** It uses ``all-MiniLM-L6-v2`` through
sentence-transformers, which is a declared pip dependency — so it arrives with
the install rather than as a model download. That claim survived a correction;
the mechanism behind it did not.

**Tool calling on a local brain is a MODEL question, not a wiring question.**
Friday's chat path genuinely passes the tool registry to local providers and
executes what comes back — ``_via_ollama`` sends ``tools=``, ``_call_ollama``
converts the schema and runs ``_oai_agentic_loop``, and that loop calls
``_execute_tool`` under the same governance as the cloud path. So a model with
native tool calling needs no API key for tools: ``qwen3:8b`` and ``gemma4:12b``
work fully offline, ``gemma3:4b`` does not, and the difference is the model.

``function_manager`` — a role in the residency contract that nothing consults —
would let a model WITHOUT native tool calling delegate the decision to a small
specialist. Its absence is real and recorded in KNOWN_ISSUES.md, but it only
bites models that cannot call tools themselves.

An earlier version of this module recommended downloading ``embeddinggemma:300m``
and ``functiongemma:270m`` on the strength of real benchmarks — 57-328 ms per
embedding chunk, 358 ms per function call. The numbers were correct and the
recommendation was worthless: nothing in ``src/`` loads either model. Measuring
a component that is not in the path tells you nothing about the system.
"""
from __future__ import annotations

# ── Measured constants ───────────────────────────────────────────────────────
# Every figure below is either measured on the reference machine or read from
# the model registry. Where a number is inferred it says so, because the
# installer should not quietly present an inference as a measurement.

#: NOTHING. Deliberately empty, and the reason matters.
#:
#: This used to list `embeddinggemma:300m` and `functiongemma:270m` — 1.17 GiB
#: of Ollama models described as "indexes your vault" and "turns your requests
#: into tool calls". Neither claim was true. Grepping `src/` for either name
#: returns only comments; no runtime path loads them. The real embedder is
#: `all-MiniLM-L6-v2`, reached through sentence-transformers, and
#: `local_seats.py:48` sets `_MIN_USEFUL_GB = 1.5` specifically to EXCLUDE
#: functiongemma from seat selection.
#:
#: They were benchmarked, and the numbers were real: 57-328 ms per embedding
#: chunk, 358 ms per function call. But a measurement of a component that is
#: not in the path says nothing about the system. See KNOWN_ISSUES.md §1.
#:
#: The rule now: **if a model is not consumed, it is not installed.** Nobody
#: wants to download a gigabyte of weights staged for a seat that is not live.
VAULT_MODELS = ()

#: What the vault actually needs, and how it arrives.
#: `sentence-transformers` is a declared dependency (pyproject.toml:81), so the
#: library comes with `pip install -e .`. The ~90 MB of MiniLM weights are
#: fetched by it on FIRST USE — which, left alone, means a 90 MB download in
#: the middle of someone's first conversation. The installer pre-warms it
#: instead.
EMBEDDER = {"id": "all-MiniLM-L6-v2", "mib": 90,
            "via": "sentence-transformers (a pip dependency)"}

#: What a loaded model costs on the card BEYOND its own weights: the KV cache at
#: the tool-seat context, the multimodal projector, and CUDA's own context.
#:
#: MEASURED, on the reference RTX 4070 (12,282 MiB) with `gemma4:12b`:
#:
#:     nvidia-smi card occupancy   8,745 MiB
#:     weights                     7,023 MiB
#:     difference                  1,722 MiB   = 1.68 GiB, rounded up to 1.7
#:
#: THIS CONSTANT REPLACES A DOUBLE COUNT, and the double count is why this
#: module used to refuse the configuration its own author was running.
#: `vram_gib` was hand-written per model and already carried ~2 GiB of unstated
#: padding: `gemma4:12b` was entered as 9.5 against a footprint the daemon's
#: own /api/ps measures at 7,718 MiB (7.54 GiB) at the 32k tool-seat context
#: (residency_catalog.SEED_MEASUREMENTS). Then `GPU_OVERHEAD_GIB = 1.0` was
#: subtracted from the CARD as well. Together they demanded a 13.0 GiB card for
#: a model measured to run in 11.0 — so a 12 GiB card was handed `qwen3:8b`
#: while `gemma4:12b` sat on that very card working. Overhead is now counted
#: ONCE, here, from a measurement.
RUNTIME_OVERHEAD_GIB = 1.7

#: A caution about generalising the KV half of that figure. `residency_catalog`
#: has `gemma4:12b` at 7,718 MiB with a 32k window and 7,814 MiB with a 131k
#: one — 96 MiB for four times the context. That flatness is a property of that
#: family's attention, NOT a general result. A dense full-attention model pays
#: materially more per token of context, so for every other row below the KV
#: term is DERIVED rather than measured, and `basis` says so.

#: Local conversational brains, smallest first. This is the whole ladder: what
#: `plan()` can offer, and nothing else.
#:
#: `gib`         download size, exactly as the Ollama registry manifest reports
#:               it (decimal GB — the field name is legacy; the conversion to
#:               real GiB happens in `_footprint_gib`, not here).
#: `min_ram_gib` what the model needs resident to run on the processor.
#: `vram_gib`    COMPUTED, never typed: the model's own footprint on the card,
#:               weights plus `RUNTIME_OVERHEAD_GIB`. Hand-writing this figure
#:               is the defect described above, so it is derived from the
#:               download size and cannot drift from it again.
#: `tools`       whether the model calls tools natively. A HARD REQUIREMENT for
#:               selection — see `_pickable()`. Not a preference, not a
#:               tie-break.
#: `basis`       where the numbers came from, so a reader can tell a
#:               measurement from an inference without leaving the file.
#:
#: Sizes and tool capability were read from the registry on 2026-08-26 WITHOUT
#: downloading any weights: the manifest gives exact layer sizes, and the
#: template blob is a few KB. Ollama decides tool capability by whether the
#: template consumes `.Tools`, so reading the template answers the same
#: question the daemon would. Where a model embeds its template in the GGUF
#: rather than carrying a template layer, that probe cannot see it and returns
#: a FALSE NEGATIVE; those rows were confirmed against a daemon's `/api/show`
#: `capabilities` array instead — the same check `verify_tool_capability()`
#: runs after every install, so a wrong flag here is caught rather than shipped.
#:
#: Ordered by `vram_gib` ascending, because the GPU pick is `[-1]` — the
#: largest that fits. The sort below enforces that rather than trusting whoever
#: edits the tuple next.
_BRAINS = (
    {"id": "qwen3:4b", "gib": 2.50, "min_ram_gib": 8, "tools": True,
     "basis": "size + tools from the registry; footprint derived",
     "note": "the smallest seat that keeps its tools. Real work, but a small "
             "agent: on published function-calling suites this size class "
             "holds up on single calls and falls apart across a multi-turn "
             "exchange, which is the failure you cannot see happening"},
    {"id": "gemma3:4b", "gib": 3.34, "min_ram_gib": 8, "tools": False,
     "basis": "size + tools from the registry",
     "note": "chat only — no native tool calling, so `_pickable()` can never "
             "select it. It stays in this table so the planner can still "
             "RECOGNISE it when it is already installed and say why it "
             "declined to use it"},
    {"id": "qwen3:8b", "gib": 5.23, "min_ram_gib": 16, "tools": True,
     "basis": "size + tools from the registry; footprint derived",
     "note": "the first seat with room to spare rather than room exactly"},
    {"id": "gemma4:12b", "gib": 7.56, "min_ram_gib": 24, "tools": True,
     "basis": "size from the registry; tools and footprint MEASURED on the "
              "reference 12 GiB card",
     "note": "measured 49-54 tok/s and a ~20.5 s cold load on a 12 GiB card, "
             "fully resident. The best-evidenced row in this table"},
    {"id": "qwen3:14b", "gib": 9.28, "min_ram_gib": 32, "tools": True,
     "basis": "size + tools from the registry; footprint derived, UNMEASURED",
     "note": "what a 16 GiB card is for. Nobody has run this one here, so its "
             "speed is unknown — the fit is arithmetic, not experience"},
    {"id": "qwen3:32b", "gib": 20.20, "min_ram_gib": 64, "tools": True,
     "basis": "size + tools from the registry; footprint derived, UNMEASURED",
     "note": "the top consumer rung: a 24 GiB card holds it fully resident. "
             "This is where multi-turn tool use stops being a gamble. "
             "Unmeasured here — the fit is arithmetic, not experience"},
)


def _footprint_gib(dl_gb: float) -> float:
    """What a model occupies on a card: weights plus measured runtime overhead.

    `dl_gb` is decimal GB, the registry's unit; VRAM is quoted in GiB. Treating
    one as the other is a 7% error in the direction that overpromises, which is
    the same class of mistake `_mib_to_gib` exists to prevent, so the
    conversion is written out rather than assumed.
    """
    return round(dl_gb / 1.073741824 + RUNTIME_OVERHEAD_GIB, 2)


BRAIN_MODELS = tuple(
    dict(m, vram_gib=_footprint_gib(m["gib"]))
    for m in sorted(_BRAINS, key=lambda m: _footprint_gib(m["gib"]))
)


def _pickable(models) -> list:
    """The subset a plan is ALLOWED to select. Tool calling is the gate.

    THE RULE, in one place so it cannot be forgotten in another: Friday hands
    the tool registry to whatever local model is seated — `_via_ollama` does
    not gate on capability — so a model that cannot call tools receives the
    registry anyway and can narrate a call it never made.
    `tool_integrity.find_pseudo_toolcalls` catches that only AFTER the fact.

    Which is why this is a filter and not a tie-break. `gemma3:4b` was the
    shipped default for exactly as long as it took a person to notice by hand.
    A rule that depends on someone noticing is not a rule.
    """
    return [m for m in models if m["tools"]]


def verify_tool_capability(model_id: str, show_fn=None) -> tuple[bool | None, str]:
    """Ask the DAEMON whether an installed model can call tools.

    (True, why) capable · (False, why) not · (None, why) could not check.

    The table above carries a `tools` flag that a human typed. This asks the
    artifact instead: Ollama's `/api/show` returns a `capabilities` array, and
    `"tools"` in it is the daemon's own answer to the question Friday's chat
    path depends on. Same move as `local_seats` reading `is_embedding` and
    `residency_catalog.detect_moe` reading `expert_count` — ask the thing what
    it is rather than inferring from its name.

    None is deliberately distinct from False: no daemon is not a failed model,
    and reporting it as one would repeat the mistake this codebase keeps
    making, where an absent component reads as a negative result.
    """
    if show_fn is None:
        def show_fn(mid):
            import json
            import urllib.request
            req = urllib.request.Request(
                "http://127.0.0.1:11434/api/show",
                data=json.dumps({"model": mid}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
    try:
        info = show_fn(model_id) or {}
    except Exception as e:
        return None, (f"could not ask the daemon about {model_id} "
                      f"({type(e).__name__}), so tool calling is unverified")
    caps = info.get("capabilities")
    if caps is None:
        return None, (f"this daemon does not report capabilities for "
                      f"{model_id}, so tool calling is unverified")
    if "tools" in caps:
        return True, f"the daemon reports {model_id} can call tools ({', '.join(caps)})"
    return False, (f"the daemon reports {model_id} CANNOT call tools "
                   f"(capabilities: {', '.join(caps) or 'none'}). Friday would "
                   f"still hand it the tool registry, so it could describe "
                   f"actions it never took.")


#: Windows reserves the most; residency_policy.ram_budget() is authoritative and
#: this mirrors it so the installer can explain a refusal before the app exists.
OS_RESERVE_GIB = {"windows": 6.0, "linux": 4.0, "darwin": 4.0}
RAM_CEILING = 0.75          # rule R2
FREE_DISK_FLOOR_GIB = 10.0  # rule R8

#: A desktop needs its own VRAM: compositor, browser, whatever is on screen.
#: This comes off the CARD, and it is now the only thing that does — a model's
#: own overhead lives inside its `vram_gib` (see RUNTIME_OVERHEAD_GIB), where it
#: is counted exactly once.
DISPLAY_RESERVE_GIB = 2.5   # rule R3

#: What a local image model needs on the card, on top of the display reserve.
IMAGE_MODEL_GIB = 6.0       # rule R5


def _ram_available_gib(total_gib: float, os_family: str) -> float:
    """Rule R2, restated so a refusal can be explained arithmetically."""
    reserve = OS_RESERVE_GIB.get(os_family, 4.0)
    return round(max(0.0, total_gib * RAM_CEILING - reserve), 1)


def _usable_vram_gib(vram_gib: float) -> float:
    """VRAM left for a model once the desktop has taken its share.

    Only the display reserve comes off here. A model's own KV cache, projector
    and CUDA context are already inside its `vram_gib`; taking them off the
    card as well is the double count this module used to make.
    """
    return round(max(0.0, vram_gib - DISPLAY_RESERVE_GIB), 1)


def _mib_to_gib(d: dict, *names) -> float | None:
    """First present key, converted. None when the profile does not carry it.

    Returns None rather than 0.0 on a miss, and that distinction is the whole
    point of this function. The first version of this module read
    ``disk["free_gib"]`` with a ``0.0`` default while hardware_profile writes
    ``disk["free_mib"]`` — so a machine with 247,905 MiB free was told it had
    0 GiB, the vault tier was refused on a box with 242 GB of space, and the
    installer offered to leave the user with "-5.2 GiB free". A missing input
    is not a zero. It is a missing input, and the honest response is to say so.

    Note the mismatch was in the UNIT as well as the name, so renaming the key
    alone would have reported 247,905 "GiB" — confidently wrong in the other
    direction.
    """
    for n in names:
        if n in d and d[n] is not None:
            v = float(d[n])
            return round(v / 1024.0, 1) if n.endswith("_mib") else round(v, 1)
    return None


def _profile_numbers(profile: dict) -> dict:
    """Pull the numbers we need out of a hardware profile, defensively."""
    os_family = ((profile.get("os") or {}).get("family") or "").lower() or "linux"
    ram_gib = _mib_to_gib(profile.get("ram") or {}, "total_mib", "total_gib")
    disk_free_gib = _mib_to_gib(profile.get("disk") or {}, "free_mib", "free_gib")

    gpus = profile.get("gpus") or []
    # Only NVIDIA is detectable today — detect_gpus() shells nvidia-smi and
    # nothing else, so an AMD card reads as no card. Say that rather than
    # letting the user think their GPU was evaluated and rejected.
    best = max((g.get("vram_total_mib", 0) for g in gpus), default=0)
    return {
        "os_family": os_family,
        "ram_gib": ram_gib,
        "disk_free_gib": disk_free_gib,
        "ram_known": ram_gib is not None,
        "disk_known": disk_free_gib is not None,
        "gpu_count": len(gpus),
        "vram_gib": round(best / 1024.0, 1),
    }


def _have(installed, model_id: str) -> bool:
    """Is this tag already on the machine? Exact match, family-aware.

    Same rule as cli._has_model and model_setup._resolves: a bare family name
    matches any tag of the family, a specific tag must match exactly. The
    version that compared only the family prefix reported gemma4:e2b present
    when gemma4:12b was.
    """
    tags = set(installed or ())
    if ":" not in model_id:
        return any(t.split(":")[0] == model_id for t in tags)
    return any(t == model_id or t.startswith(model_id + "-") for t in tags)


def _conversational_fallback(names) -> list:
    """Last-resort filter for models that can hold a conversation.

    `services/local_seats.installed()` is the AUTHORITY on this and callers
    should pass its output as `conversational=` — it merges Friday's own model
    store with the daemon's, and drops anything flagged `is_embedding`, which
    is a capability answer rather than a guess about a name. Its docstring
    already says why: "Embedding models are excluded: they cannot answer, and
    offering one as a fallback would turn a missing-model problem into a
    baffling one."

    This function exists only for callers who cannot reach the daemon. It is a
    heuristic and is labelled as one.

    History worth keeping, because it is the point: the first version of this
    filter excluded models by family token, which let `qwen3-embedding:0.6b`
    through — an embedding model offered as something to talk to. That is the
    mirror image of the bug immediately before it, where a prefix match hid
    `qwen3.5:9b`. One over-matched, one under-filtered, both compared the shape
    of a name. The fix was never a better name rule; it was to ask the module
    that already knew the answer.
    """
    return [n for n in (names or ()) if "embed" not in n.lower()]


def _local_alternatives(conversational, need_gib: float) -> list:
    """Installed models that could plausibly serve as a brain.

    The point is not to pick one — it is to avoid telling someone to download
    5 GiB while a perfectly good model sits on their disk.
    """
    known = {m["id"].split(":")[0] for m in BRAIN_MODELS}
    return sorted({t for t in (conversational or ())
                   if ":" in t and t.split(":")[0] not in known})


def plan(profile: dict, installed=None, conversational=None) -> dict:
    """Plan against `profile`.

    `installed`     — every tag on the machine. Used to avoid re-proposing a
                      download, INCLUDING the embedding models the vault needs,
                      so it must not be pre-filtered.
    `conversational` — the subset that can actually hold a conversation, from
                      `local_seats.installed()`. Falls back to a labelled
                      heuristic when the daemon is unreachable.
    """
    """What this machine can run, what to download, and what it cannot do.

    Returns a dict with a `tiers` list. Every tier carries `status` in
    {"ready", "install", "refused"}, a human `reason`, and — when refused — the
    `rule` that refused it. Nothing is ever "available" without the arithmetic
    to back it.
    """
    n = _profile_numbers(profile)

    # Refuse to plan on numbers we do not have. Guessing here produces a
    # confident recommendation built on a default, which is worse than an
    # honest "I could not detect this" — the user can act on the second.
    missing = [k for k, ok in (("RAM", n["ram_known"]), ("disk", n["disk_known"]))
               if not ok]
    if missing:
        return {
            "hardware": n, "ram_available_gib": 0.0, "vram_usable_gib": 0.0,
            "tiers": [{
                "id": "detect", "name": "Hardware detection", "status": "refused",
                "rule": "detect",
                "reason": (f"could not read {' and '.join(missing)} from the "
                           f"hardware profile, so nothing can be planned "
                           f"honestly. Run `friday doctor`, and if that also "
                           f"fails please open an issue with its output — this "
                           f"is a bug in detection, not in your machine."),
                "models": [],
            }],
            "download": [], "download_gib": 0.0,
            "disk_after_gib": None, "disk_warning": False,
            "vault_ready": False, "undetected": missing,
        }

    ram_avail = _ram_available_gib(n["ram_gib"], n["os_family"])
    vram_usable = _usable_vram_gib(n["vram_gib"])
    tiers = []

    # ── Tier 1: the vault. The minimum requirement, and it runs on CPU. ──
    # Refuse if installing would leave the machine without room to run. 2 GiB is
    # a hard floor, not a comfortable one — R8 wants 10 GiB and disk_warning
    # fires below that. Installing 1.17 GiB into 3 GiB free technically succeeds
    # and leaves a machine that cannot write a log file.
    vault_need = 2.0
    if n["disk_free_gib"] < vault_need:
        tiers.append({
            "id": "vault", "name": "Memory", "status": "refused",
            "rule": "disk",
            "reason": (f"needs about {vault_need:.0f} GiB free to work in, found "
                       f"{n['disk_free_gib']:.1f} GiB. Free some space and "
                       f"re-run — this is the one tier worth making room for."),
            "models": [],
        })
    else:
        tiers.append({
            "id": "vault", "name": "Memory", "status": "ready",
            "reason": (f"Runs on your processor, no graphics card needed. Uses "
                       f"{EMBEDDER['id']} (~{EMBEDDER['mib']} MB) via "
                       f"{EMBEDDER['via']}, so it arrives with the install "
                       f"rather than as a separate download."),
            "models": [],
        })

    # ── Tier 2: a local conversational brain. ──
    # Prefer VRAM, fall back to RAM, refuse with the arithmetic if neither.
    # Largest that FITS, not smallest that is affordable. GPU is preferred over
    # RAM because a model spilling to CPU is a different product experience, and
    # the user should be told which one they are getting.
    #
    # TWO DIFFERENT QUESTIONS, kept apart on purpose:
    #   *_fits     — what this machine could hold. Includes tool-incapable
    #                models, because the planner still needs to RECOGNISE one
    #                that is already installed and say why it declined it.
    #   *_pickable — what may actually be chosen. Tool calling is a hard gate;
    #                see `_pickable()`.
    # Collapsing these two is how `gemma3:4b` became a shipped default.
    gpu_fits = [m for m in BRAIN_MODELS if vram_usable >= m["vram_gib"]]
    ram_fits = [m for m in BRAIN_MODELS
                if ram_avail >= m["min_ram_gib"] * RAM_CEILING]
    gpu_pickable = _pickable(gpu_fits)
    ram_pickable = _pickable(ram_fits)
    affordable = gpu_fits or ram_fits
    if not (gpu_pickable or ram_pickable):
        if n["ram_gib"] and ram_avail <= 0:
            reason = (f"{n['ram_gib']:.0f} GiB RAM x {RAM_CEILING} ceiling "
                      f"- {OS_RESERVE_GIB.get(n['os_family'], 4.0):.0f} GiB reserved "
                      f"for {n['os_family']} = {ram_avail:.0f} GiB available. "
                      f"Friday refuses every model seat at this figure. "
                      f"16 GiB is the floor.")
        else:
            reason = (f"{ram_avail:.1f} GiB usable RAM and {vram_usable:.1f} GiB "
                      f"usable VRAM; the smallest local brain needs "
                      f"{BRAIN_MODELS[0]['min_ram_gib'] * RAM_CEILING:.1f} GiB RAM "
                      f"or {BRAIN_MODELS[0]['vram_gib']} GiB VRAM.")
        # A machine that can hold a model but no TOOL-CAPABLE model is refused
        # for a different reason than one that can hold nothing, and saying so
        # is the difference between "buy more card" and "this is fine".
        if affordable:
            reason = (f"the only local models this machine can hold "
                      f"({', '.join(m['id'] for m in affordable)}) cannot call "
                      f"tools, and Friday will not seat one that cannot: she "
                      f"hands the tool registry to whatever is seated, so such "
                      f"a model can describe actions it never took.")
        tiers.append({
            "id": "brain", "name": "Local conversational brain",
            "status": "refused", "rule": "tools" if affordable else "R2",
            "reason": reason + " Friday still works: vault memory and tools run "
                               "locally, and conversation uses a cloud provider "
                               "if you add a key.",
            "models": [],
        })
    else:
        # On a GPU, take the LARGEST that fits: VRAM is the binding constraint
        # and a bigger model is simply better inside it.
        #
        # On CPU, take the SMALLEST useful one. Size costs latency directly
        # there, and CPU generation throughput is unmeasured for every model in
        # this table (see KNOWN_ISSUES.md §4) — so handing someone a 12B on CPU
        # because their RAM technically holds it would be picking the option
        # most likely to be unusable, on the strength of a number nobody has.
        if gpu_pickable:
            pick, placement = gpu_pickable[-1], "GPU"
            # Headroom is worth stating on a big card: it is the difference
            # between "your hardware was used" and "a number went past".
            head = round(vram_usable - pick["vram_gib"], 1)
            caveat = (f" Leaves about {head:.1f} GiB of the card spare, which "
                      f"goes to a larger context window." if head >= 2.0 else "")
        else:
            # Smallest useful — but at the SAME RAM tier prefer the model that
            # can call tools. qwen3:4b and gemma3:4b are both min_ram_gib 8 and
            # qwen3:4b is the smaller download, so taking ram_fits[0] on tuple
            # order alone handed every GPU-less machine the one model in this
            # table that cannot call tools. An AMD card reads as no card
            # (detect_gpus shells nvidia-smi and nothing else), so this was not
            # a rare path.
            pick = min(ram_pickable, key=lambda m: (m["min_ram_gib"], m["gib"]))
            placement = "CPU"
            caveat = (" Generation speed on CPU is unmeasured — expect this to "
                      "be usable for short exchanges and slow for long ones.")
        # Prefer something already on disk that fits, over anything we would
        # have to fetch. Proposing a 5 GiB download beside an equally suitable
        # local model is the installer wasting a stranger's bandwidth to satisfy
        # its own table.
        local_fit = [m for m in affordable if _have(installed, m["id"])]
        if local_fit:
            local_pick = local_fit[-1] if gpu_fits else local_fit[0]
            # ...but not if going local costs tool calling. A model that cannot
            # call tools is a materially different product — `_via_ollama` does
            # NOT gate on capability, so it is handed the registry anyway and can
            # narrate a call it never made, with tool_integrity.find_pseudo_toolcalls
            # catching that only after the fact. Saving a download is not worth
            # silently buying that. (An earlier version of this comment claimed
            # Friday disables tools for the turn. She does not; see the note on
            # gemma3:4b in BRAIN_MODELS, and KNOWN_ISSUES.md §3.)
            if not local_pick["tools"]:
                caveat += (f" ({local_pick['id']} is already installed and would "
                           f"save the download, but cannot call tools.)")
            else:
                pick = local_pick

        have_it = _have(installed, pick["id"])
        convo = (conversational if conversational is not None
                 else _conversational_fallback(installed))
        others = _local_alternatives(convo, pick["gib"])
        note_others = (f" Also already installed and possibly suitable: "
                       f"{', '.join(others[:4])}. Set one in Settings -> Intelligence "
                       f"if you prefer it." if others else "")
        # Every OTHER rung this machine could also run, so the caller can offer
        # a choice instead of announcing a decision. Smaller means faster and a
        # shorter download; the default stays the largest that fits.
        fitting = gpu_pickable if gpu_pickable else ram_pickable
        alternatives = [
            {"id": m["id"], "gib": m["gib"], "vram_gib": m["vram_gib"],
             "tools": m["tools"], "basis": m["basis"], "note": m["note"],
             "default": m["id"] == pick["id"]}
            for m in fitting]
        tiers.append({
            "id": "brain", "name": "Local conversational brain",
            "status": "ready" if have_it else "install",
            "reason": (f"{pick['id']} on {placement} — {pick['note']}. "
                       + ("already installed." if have_it
                          else f"{pick['gib']} GiB.")
                       + caveat + note_others),
            "basis": pick["basis"],
            "alternatives": alternatives,
            "models": [] if have_it else [
                {"id": pick["id"], "role": "brain", "gib": pick["gib"],
                 "why": "answers you without touching a cloud provider",
                 "tools": pick["tools"], "basis": pick["basis"]}],
        })

    # ── Tier 3: local image generation. GPU or nothing. ──
    if n["gpu_count"] == 0:
        tiers.append({
            "id": "image", "name": "Local image generation", "status": "refused",
            "rule": "R5",
            "reason": ("no NVIDIA GPU detected. Note Friday only probes "
                       "nvidia-smi, so an AMD or Intel card reads as no card — "
                       "your GPU may exist and simply not be visible to her. "
                       "Cloud image generation still works with a key."),
            "models": [],
        })
    elif vram_usable < IMAGE_MODEL_GIB:
        tiers.append({
            "id": "image", "name": "Local image generation", "status": "refused",
            "rule": "R5",
            "reason": (f"{n['vram_gib']:.0f} GiB card, but {DISPLAY_RESERVE_GIB} GiB "
                       f"goes to your desktop, leaving {vram_usable:.1f} GiB. "
                       f"Image models need about {IMAGE_MODEL_GIB:.0f} GiB."),
            "models": [],
        })
    else:
        tiers.append({
            "id": "image", "name": "Local image generation", "status": "install",
            "reason": (f"{vram_usable:.1f} GiB usable VRAM. Note the image models "
                       f"are fetched separately and Stable Diffusion 3.5 carries a "
                       f"revenue-conditioned licence — see NOTICE."),
            "models": [],
        })

    # ── Tier 4: arbiter-managed seats. Windows + NVIDIA only, today. ──
    if n["os_family"] != "windows":
        tiers.append({
            "id": "seats", "name": "Managed model seats (residency layer)",
            "status": "refused", "rule": "platform",
            "reason": (f"Windows-only today. On {n['os_family']} the seat engine "
                       f"is not present, so local inference goes through Ollama "
                       f"instead. Everything else works."),
            "models": [],
        })
    elif n["ram_gib"] < 16:
        tiers.append({
            "id": "seats", "name": "Managed model seats (residency layer)",
            "status": "refused", "rule": "R2",
            "reason": (f"{n['ram_gib']:.0f} GiB RAM. Rule R2 gives "
                       f"{ram_avail:.0f} GiB available after the "
                       f"{OS_RESERVE_GIB['windows']:.0f} GiB Windows reserve, and "
                       f"a seat cannot be placed in that. 16 GiB is the floor."),
            "models": [],
        })
    else:
        tiers.append({
            "id": "seats", "name": "Managed model seats (residency layer)",
            "status": "ready",
            "reason": f"{n['ram_gib']:.0f} GiB RAM, {ram_avail:.0f} GiB available.",
            "models": [],
        })

    to_download = [m for t in tiers if t["status"] == "install" for m in t["models"]]
    total_gib = round(sum(m["gib"] for m in to_download), 2)

    return {
        "hardware": n,
        "ram_available_gib": ram_avail,
        "vram_usable_gib": vram_usable,
        "tiers": tiers,
        "download": to_download,
        "download_gib": total_gib,
        # GB subtracted from GiB. Deliberate, and stated rather than hidden:
        # the download figures are decimal GB and free disk is GiB, so this
        # over-states what the download costs by about 7%. That errs toward
        # warning too early, which is the right direction for a disk check —
        # unlike `_mib_to_gib`'s original bug, which erred the other way.
        "disk_after_gib": round(n["disk_free_gib"] - total_gib, 1),
        # Only meaningful if something is actually going to be downloaded —
        # otherwise it warns about the consequences of an install that isn't
        # happening, which reads as a threat rather than information.
        "disk_warning": bool(to_download)
                        and (n["disk_free_gib"] - total_gib) < FREE_DISK_FLOOR_GIB,
        "vault_ready": any(t["id"] == "vault" and t["status"] != "refused"
                           for t in tiers),
    }


def brain_is_primary_capable(p: dict) -> tuple[bool, str]:
    """Can the local brain be someone's PRIMARY brain — tools and all?

    THE THRESHOLD, stated so the next person knows when to move it:

      A local brain is primary-capable when Friday can use her TOOLS through
      it. Conversation and memory are not the bar — an assistant that talks and
      remembers but cannot read a file, search, or touch a calendar is a
      different product, not a slower one.

      That reduces to two questions, both about the MODEL:
        1. Does it support native tool calling?
        2. Does it fit on the GPU? (CPU generation throughput is unmeasured
           for every model in this table, so RAM-fits is not a usability claim.)

    **This is a model question, not a wiring question.** Friday's chat path
    genuinely passes the tool registry to local providers and executes what
    comes back: `_via_ollama` (agent.py) sends `tools=`, `_call_ollama`
    converts to OpenAI tool schema and runs `_oai_agentic_loop`, and that loop
    calls `_execute_tool` under the same governance as the cloud path. Verified
    by reading the chain 2026-08-22.

    So a tool-capable local model needs no API key for tools. `gemma3:4b`
    cannot, which is why an 8 GiB card ends up wanting a key while a 12 GiB
    card does not.

    A NOTE ON `function_manager`, because it is easy to conflate: that role
    exists in the residency contract and nothing consults it. It would let a
    model WITHOUT native tool calling delegate the decision to a small
    specialist. Its absence is real and recorded in KNOWN_ISSUES.md, but it
    does not affect models that call tools themselves — and an earlier version
    of this function wrongly generalised from "the delegation path is missing"
    to "no local model can use tools", which is a much larger claim than the
    evidence supported.

    Returns (capable, reason), written for a person rather than a log.
    """
    tier = next((t for t in p.get("tiers", []) if t["id"] == "brain"), None)
    if not tier or tier["status"] == "refused":
        return False, ("This machine can't run a local conversational model at "
                       "all, so Friday needs a cloud key to be able to talk.")

    picked = tier["models"][0] if tier.get("models") else None
    on_gpu = " on GPU" in tier.get("reason", "")
    has_tools = (bool(picked.get("tools")) if picked
                 else "cannot call tools" not in tier.get("reason", ""))

    if not has_tools:
        return False, ("The best local model this machine can run can't call "
                       "tools — it chats and remembers, but it can't read "
                       "files, search, or use your calendar. A bigger graphics "
                       "card would let Friday run a model that can; a cloud "
                       "key gets you there today.")
    if not on_gpu:
        return False, ("A tool-capable local model fits, but only on the "
                       "processor rather than the graphics card, and Friday "
                       "has no measurements for how fast that is. A cloud key "
                       "is the reliable option here.")
    return True, ("This machine can run a tool-capable model on its graphics "
                  "card. Friday works completely offline — conversation, "
                  "memory and tools — with no API key at all. A key is only "
                  "needed for voice and image generation.")


def render(p: dict) -> str:
    """The plan as the installer prints it. Short by design."""
    h = p["hardware"]
    gpu = (f"{h['vram_gib']:.0f} GiB NVIDIA" if h["gpu_count"]
           else "none detected (nvidia-smi only)")
    out = [
        "",
        f"  This machine:  {h['ram_gib']:.0f} GiB RAM   "
        f"{h['disk_free_gib']:.0f} GiB free   GPU: {gpu}",
        "",
    ]
    # NOT "[ok]" / "[no]": the CLI renders through Rich, which parses square
    # brackets as markup and silently deletes anything that looks like a tag.
    # The first version of this printed rows with no status marker at all.
    mark = {"ready": "OK  ", "install": "GET ", "refused": "NO  "}
    for t in p["tiers"]:
        out.append(f"  {mark[t['status']]} {t['name']}")
        out.append(f"        {t['reason']}")
        # Show the whole rung list, not just the pick. Someone who would rather
        # have speed than size cannot choose an option they were never shown,
        # and the default is only a default if the alternatives are visible.
        alts = t.get("alternatives") or []
        if len(alts) > 1:
            out.append("        This machine can also run:")
            for a in reversed(alts):
                flag = "*" if a["default"] else " "
                out.append(f"        {flag} {a['id']:<14}{a['gib']:>6.2f} GB "
                           f"download   needs {a['vram_gib']:.2f} GiB on the card")
            out.append("        (* = what Friday will install. Change it in "
                       "Settings -> Intelligence.)")
    out.append("")
    if p["download"]:
        out.append(f"  Download: {p['download_gib']:.2f} GB "
                   f"({len(p['download'])} model(s))")
        for m in p["download"]:
            out.append(f"    - {m['id']:24} {m['gib']:>5.2f} GB  {m['why']}")
    else:
        out.append("  Nothing to download.")
    if p["disk_warning"]:
        out.append("")
        out.append(f"  ! After downloading you would have "
                   f"{p['disk_after_gib']:.1f} GiB free, below the "
                   f"{FREE_DISK_FLOOR_GIB:.0f} GiB Friday wants to keep clear.")
    out.append("")
    return "\n".join(out)
