"""These fit together: what a set of models costs side by side, before any
of them is downloaded.

One bar per memory pool. A seat that stays loaded is a solid block; a seat
that takes turns with the brain is a dashed block carrying the time the
swap costs; the display reserve is a labelled block so a 12 GB card is seen
to offer 9.5. The rules are the residency planner's: pinned seats fill the
card first, an image or video model always takes an exclusive lease (R5),
the sidekick survives every lease (R10), CPU seats live in processor RAM,
and nothing is placed inside the display reserve. The swap time is the
heavy-job sequence's cost: unload, load the job model, run, unload, reload
the brain, verify.
"""
from __future__ import annotations

from agent_friday.services import model_fit as mf
from agent_friday.services import residency_policy as rp

GPU_KINDS = {"text", "image", "video", "music"}
EXCLUSIVE_KINDS = {"image", "video", "music"}


def _load_s(pick: dict, profile: dict) -> float | None:
    if pick.get("measured_load_s"):
        return float(pick["measured_load_s"])
    try:
        from agent_friday.services import residency_catalog as rc
        return rc.est_load_s(int(pick.get("file_bytes") or 0), profile, bool(pick.get("is_moe")))
    except Exception:
        return None


def preview(profile: dict, picks: list[dict], *, overhead_tokens: int | None = None) -> dict:
    """`picks`: `{id, label, kind, role?, file_bytes, layout?, context_cap?,
    mmproj_bytes?, peak_vram_mib?, host_ram_mib?, measured_load_s?, retained?}`.

    Returns the pools with their blocks, the sentence, and the lists of
    what takes turns and what cannot run.
    """
    b = mf.budgets(profile)
    gpu = b["gpus"][0] if b["gpus"] else None
    ram = b["ram"]
    ram_avail = int(ram.get("available_target_mib") or 0)
    gpu_blocks, ram_blocks, turns, cannot = [], [], [], []
    vram_used = 0
    brain = None
    if gpu:
        total = int(gpu.get("total_mib") or 0)
        reserve = max(0, total - int(gpu["available_mib"]))
        gpu_blocks.append({"label": "display reserve and what others hold", "mib": reserve, "kind": "reserve"})

    def order(p):
        k = p.get("kind", "text")
        if k in EXCLUSIVE_KINDS:
            return 2
        return 0 if p.get("role") in ("interactive_brain", "brain") else 1

    for pick in sorted(picks, key=order):
        kind = pick.get("kind", "text")
        label = pick.get("label") or pick.get("id")
        if kind not in GPU_KINDS or not gpu:
            mib = int(pick.get("host_ram_mib") or round(int(pick.get("file_bytes") or 0) / 1048576 * 1.1))
            if mib <= ram_avail - sum(x["mib"] for x in ram_blocks):
                ram_blocks.append({"label": label, "mib": mib, "kind": "pinned", "model_id": pick.get("id")})
            else:
                cannot.append({"id": pick.get("id"), "label": label, "pool": "ram",
                               "shortfall_mib": mib - (ram_avail - sum(x["mib"] for x in ram_blocks))})
            continue
        if kind in EXCLUSIVE_KINDS:
            peak = int(pick.get("peak_vram_mib") or round(int(pick.get("file_bytes") or 0) / 1048576 * 1.3))
            if peak <= int(gpu["available_mib"]):
                displaced = [x for x in gpu_blocks if x["kind"] == "pinned" and not x.get("retained")]
                reload_s = sum(x.get("load_s") or 0 for x in displaced)
                own = _load_s(pick, profile) or 0
                swap_s = round(reload_s + own)
                gpu_blocks.append({"label": label, "mib": peak, "kind": "leased", "model_id": pick.get("id"),
                                   "swap_s": swap_s, "displaces": [x["label"] for x in displaced]})
                turns.append({"id": pick.get("id"), "label": label, "swap_s": swap_s,
                              "displaces": [x["label"] for x in displaced]})
            else:
                cannot.append({"id": pick.get("id"), "label": label, "pool": "gpu",
                               "shortfall_mib": peak - int(gpu["available_mib"])})
            continue
        fit = mf.fit(int(pick.get("file_bytes") or 0), profile, layout=pick.get("layout"),
                     role=pick.get("role") or "interactive_brain", context_cap=pick.get("context_cap"),
                     mmproj_bytes=int(pick.get("mmproj_bytes") or 0), pinned_vram_mib=vram_used,
                     overhead_tokens=overhead_tokens, blocks=(pick.get("layout") or {}).get("blocks"))
        seat = (fit.get("seat") or {}).get("total_mib")
        if fit["verdict"] in ("runs_well", "tight") and fit["placement"] and fit["placement"]["device"].startswith("gpu"):
            block = {"label": label, "mib": int(seat), "kind": "pinned", "model_id": pick.get("id"),
                     "context": fit["context"], "verdict": fit["verdict"], "load_s": _load_s(pick, profile),
                     "retained": bool(pick.get("retained"))}
            gpu_blocks.append(block)
            vram_used += int(seat)
            if brain is None:
                brain = block
            continue
        if fit["verdict"] == "partial" and "host_mib" in (fit.get("placement") or {}):
            block = {"label": label, "mib": int(seat - fit["placement"]["host_mib"]), "kind": "pinned",
                     "model_id": pick.get("id"), "context": fit["context"], "verdict": "partial",
                     "load_s": _load_s(pick, profile), "partial": True}
            gpu_blocks.append(block)
            ram_blocks.append({"label": label + " (the rest of the weights)", "mib": int(fit["placement"]["host_mib"]),
                               "kind": "pinned", "model_id": pick.get("id")})
            vram_used += block["mib"]
            continue
        alone = mf.fit(int(pick.get("file_bytes") or 0), profile, layout=pick.get("layout"),
                       role=pick.get("role") or "interactive_brain", context_cap=pick.get("context_cap"),
                       pinned_vram_mib=0, overhead_tokens=overhead_tokens)
        if alone["verdict"] in ("runs_well", "tight", "partial"):
            displaced = [x for x in gpu_blocks if x["kind"] == "pinned" and not x.get("retained")]
            swap_s = round(sum(x.get("load_s") or 0 for x in displaced) + (_load_s(pick, profile) or 0))
            gpu_blocks.append({"label": label, "mib": int((alone.get("seat") or {}).get("total_mib") or 0),
                               "kind": "leased", "model_id": pick.get("id"), "swap_s": swap_s,
                               "context": alone["context"], "displaces": [x["label"] for x in displaced]})
            turns.append({"id": pick.get("id"), "label": label, "swap_s": swap_s,
                          "displaces": [x["label"] for x in displaced]})
        elif fit["placement"] and fit["placement"].get("device") == "cpu":
            # Nothing fits it on the card, even alone: it runs on the
            # processor, slowly, and the bar shows it in RAM.
            mib = int(seat)
            ram_blocks.append({"label": label + " (on the processor)", "mib": mib, "kind": "pinned",
                               "model_id": pick.get("id"), "context": fit["context"]})
        else:
            cannot.append({"id": pick.get("id"), "label": label, "pool": "gpu",
                           "shortfall_mib": alone.get("shortfall_mib") or fit.get("shortfall_mib")})

    pools = []
    if gpu:
        total = int(gpu.get("total_mib") or 0)
        pinned = sum(x["mib"] for x in gpu_blocks if x["kind"] == "pinned")
        free = max(0, int(gpu["available_mib"]) - pinned)
        pools.append({"name": "Graphics memory", "device": gpu.get("name"), "total_mib": total,
                      "available_mib": int(gpu["available_mib"]), "blocks": gpu_blocks, "free_mib": free})
    used_ram = sum(x["mib"] for x in ram_blocks)
    pools.append({"name": "Processor RAM", "total_mib": int(ram.get("total_mib") or 0),
                  "available_mib": ram_avail, "blocks": ram_blocks, "free_mib": max(0, ram_avail - used_ram)})

    if cannot:
        c = cannot[0]
        sentence = "%s cannot run on this computer even taking turns: it needs %s MiB more." % (
            c["label"], c.get("shortfall_mib") or "?")
    elif turns:
        t = turns[0]
        who = " and ".join(t["displaces"]) if t["displaces"] else "the loaded seats"
        sentence = "These take turns: %s evicts %s for about %d s each way." % (t["label"], who, t["swap_s"])
    else:
        spare = pools[0]["free_mib"] if gpu else pools[-1]["free_mib"]
        sentence = "These fit together, %.1f GB spare." % (spare / 1024.0)
    return {"pools": pools, "sentence": sentence, "fits_together": not turns and not cannot,
            "turns": turns, "cannot": cannot, "simulated": bool(profile.get("simulated"))}
