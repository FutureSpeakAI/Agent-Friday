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

**Tool calling does not work on a local brain today.** ``function_manager``
exists as a role in ``residency_policy`` with a residency class, a context
budget and a UI label — and nothing in ``agent.py``, ``routes/chat.py``,
``routing/`` or ``local_seats.py`` ever consults it. So a local-only Friday
converses and remembers; she does not act. Tools need a cloud key. See
KNOWN_ISSUES.md.

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

#: Local conversational brains, smallest first. `min_ram_gib` is what the model
#: needs resident; `vram_gib` is what it needs on a card to be fast rather than
#: merely possible.
#: `vram_gib` is what the MODEL needs (weights plus KV headroom), NOT the size of
#: card it wants. Getting that backwards made the first version of this planner
#: hand a 12 GiB card the 4B model, which is defect H3 in the release audit —
#: the exact bug this module exists to prevent, reproduced by the fix for it.
BRAIN_MODELS = (
    {"id": "gemma3:4b", "gib": 3.3, "min_ram_gib": 8, "vram_gib": 3.5,
     "tools": False,
     "note": "chat only — lacks native tool calling, so Friday disables tools "
             "for local turns rather than let it narrate calls it never made"},
    {"id": "qwen3:8b", "gib": 5.2, "min_ram_gib": 16, "vram_gib": 6.0,
     "tools": True,
     "note": "first tier where local turns keep their tools"},
    {"id": "gemma4:12b", "gib": 7.5, "min_ram_gib": 24, "vram_gib": 9.5,
     "tools": True,
     "note": "measured 49-54 tok/s on a 12 GiB card, ~20.5 s cold load"},
)

#: Windows reserves the most; residency_policy.ram_budget() is authoritative and
#: this mirrors it so the installer can explain a refusal before the app exists.
OS_RESERVE_GIB = {"windows": 6.0, "linux": 4.0, "darwin": 4.0}
RAM_CEILING = 0.75          # rule R2
FREE_DISK_FLOOR_GIB = 10.0  # rule R8
DISPLAY_RESERVE_GIB = 2.5   # a desktop needs its own VRAM; R3 adds ~1 GiB more
GPU_OVERHEAD_GIB = 1.0      # rule R3


def _ram_available_gib(total_gib: float, os_family: str) -> float:
    """Rule R2, restated so a refusal can be explained arithmetically."""
    reserve = OS_RESERVE_GIB.get(os_family, 4.0)
    return round(max(0.0, total_gib * RAM_CEILING - reserve), 1)


def _usable_vram_gib(vram_gib: float) -> float:
    """VRAM left for a model once the desktop and R3 overhead are taken."""
    return round(max(0.0, vram_gib - DISPLAY_RESERVE_GIB - GPU_OVERHEAD_GIB), 1)


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
    gpu_fits = [m for m in BRAIN_MODELS if vram_usable >= m["vram_gib"]]
    ram_fits = [m for m in BRAIN_MODELS
                if ram_avail >= m["min_ram_gib"] * RAM_CEILING]
    affordable = gpu_fits or ram_fits
    if not affordable:
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
        tiers.append({
            "id": "brain", "name": "Local conversational brain",
            "status": "refused", "rule": "R2",
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
        if gpu_fits:
            pick, placement = gpu_fits[-1], "GPU"
            caveat = ""
        else:
            pick, placement = ram_fits[0], "CPU"
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
            # call tools is a materially different product — Friday disables
            # tools for the whole turn rather than let it narrate calls it never
            # made — and saving a download is not worth silently losing that.
            if pick["tools"] and not local_pick["tools"]:
                caveat += (f" ({local_pick['id']} is already installed and would "
                           f"save the download, but cannot call tools.)")
            else:
                pick = local_pick

        have_it = _have(installed, pick["id"])
        convo = (conversational if conversational is not None
                 else _conversational_fallback(installed))
        others = _local_alternatives(convo, pick["gib"])
        note_others = (f" Also already installed and possibly suitable: "
                       f"{', '.join(others[:4])}. Set one in Settings -> Models "
                       f"if you prefer it." if others else "")
        tiers.append({
            "id": "brain", "name": "Local conversational brain",
            "status": "ready" if have_it else "install",
            "reason": (f"{pick['id']} on {placement} — {pick['note']}. "
                       + ("already installed." if have_it
                          else f"{pick['gib']} GiB.")
                       + caveat + note_others),
            "models": [] if have_it else [
                {"id": pick["id"], "role": "brain", "gib": pick["gib"],
                 "why": "answers you without touching a cloud provider",
                 "tools": pick["tools"]}],
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
    elif vram_usable < 6.0:
        tiers.append({
            "id": "image", "name": "Local image generation", "status": "refused",
            "rule": "R5",
            "reason": (f"{n['vram_gib']:.0f} GiB card, but {DISPLAY_RESERVE_GIB} GiB "
                       f"goes to your desktop and {GPU_OVERHEAD_GIB} GiB to seat "
                       f"overhead, leaving {vram_usable:.1f} GiB. Image models need "
                       f"about 6 GiB."),
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
    """Can the local brain be someone's PRIMARY brain? Today: no, on any machine.

    THE THRESHOLD, stated so the next person knows when to move it:

      A local brain is primary-capable when Friday can use her TOOLS through
      it. Conversation and memory are not the bar — an assistant that talks and
      remembers but cannot read a file, search, or touch a calendar is a
      different product, not a slower one.

    Today that is false everywhere, and NOT because of hardware.
    ``function_manager`` exists as a role in ``residency_policy`` with a
    residency class, a context budget and a UI label, and nothing in
    ``agent.py``, ``routes/chat.py``, ``routing/`` or ``local_seats.py`` ever
    consults it. A local brain therefore has no function seat to delegate to,
    however capable it is. Presenting this as a hardware limit would send
    someone to buy a better machine for the same result.

    **When function_manager is wired, change this function**, and the
    hardware-shaped test underneath becomes the real one: a model that can call
    tools, running on the GPU (CPU generation throughput is unmeasured for
    every model in this table). The scaffolding for that is deliberately left
    below rather than deleted, so the next person can see what the test should
    become.

    Returns (capable, reason). The reason is written for a person, not a log.
    """
    _ = _hardware_brain_verdict(p)   # not the deciding factor yet — see above
    return False, ("Friday can only use her tools — reading files, searching, "
                   "your calendar — through a cloud model right now. Running "
                   "them from a local model isn't built yet, so this isn't "
                   "about your hardware. A local model handles conversation "
                   "and memory perfectly well on its own.")


def _hardware_brain_verdict(p: dict) -> tuple[bool, str]:
    """The hardware-only half of the question, kept live and tested.

    This is what brain_is_primary_capable() SHOULD return once a local brain
    can reach a function seat. It is called (and discarded) above so it cannot
    rot into something that no longer runs.
    """
    tier = next((t for t in p.get("tiers", []) if t["id"] == "brain"), None)
    if not tier or tier["status"] == "refused":
        return False, "This machine can't run a local conversational model at all."
    picked = tier["models"][0] if tier.get("models") else None
    on_gpu = " on GPU" in tier.get("reason", "")
    has_tools = (bool(picked.get("tools")) if picked
                 else "cannot call tools" not in tier.get("reason", ""))
    if not has_tools:
        return False, "The best local model here can't call tools natively."
    if not on_gpu:
        return False, ("A local model fits, but only on the processor, and CPU "
                       "generation speed is unmeasured.")
    return True, "This machine could run a capable local brain on its GPU."


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
    out.append("")
    if p["download"]:
        out.append(f"  Download: {p['download_gib']:.2f} GiB "
                   f"({len(p['download'])} model(s))")
        for m in p["download"]:
            out.append(f"    - {m['id']:24} {m['gib']:>5.2f} GiB  {m['why']}")
    else:
        out.append("  Nothing to download.")
    if p["disk_warning"]:
        out.append("")
        out.append(f"  ! After downloading you would have "
                   f"{p['disk_after_gib']:.1f} GiB free, below the "
                   f"{FREE_DISK_FLOOR_GIB:.0f} GiB Friday wants to keep clear.")
    out.append("")
    return "\n".join(out)
