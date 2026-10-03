"""The rows of the Models screen: every model Friday can offer, one row per
file, with a verdict for THIS machine and the arithmetic behind it.

Three sources, in this order: the curated shortlist, the models already in
Friday's store, and anything the user pasted (a Hugging Face id or a file
path). Each file is sized from its real bytes and its attention layout
(`model_fit`), placed beside what is resident, and given a speed with its
basis. A measurement row for this machine wins over everything.

A pasted Hugging Face id costs one anonymous metadata read; nothing about the
machine goes with it.
"""
from __future__ import annotations

import json
import time

import requests

from agent_friday.services import model_fit as mf
from agent_friday.services import model_shortlist as sl

HF_API = "https://huggingface.co/api/models/{repo}?blobs=true"
_http_get = requests.get

_LICENCE_CLASS = {
    "apache-2.0": "permissive", "mit": "permissive", "bsd-3-clause": "permissive",
    "cc-by-4.0": "attribution", "gemma": "restricted", "llama3": "restricted",
    "llama3.1": "restricted", "llama3.2": "restricted", "openrail++": "restricted",
    "cc-by-nc-4.0": "non-commercial", "cc-by-nc-sa-4.0": "non-commercial",
}

VERDICT_WORDS = {"runs_well": "Runs well", "tight": "Tight", "partial": "Partly on the processor",
                 "wont_fit": "Won't fit"}


def licence_class(licence: str | None) -> str:
    if not licence:
        return "unknown"
    return _LICENCE_CLASS.get(str(licence).lower(), "restricted" if "other" in str(licence).lower() else "unknown")


def machine_line(profile: dict) -> dict:
    """The one plain line at the top, and the numbers behind it."""
    b = mf.budgets(profile)
    gpu = b["gpus"][0] if b["gpus"] else None
    ram = b["ram"]
    disk = (profile.get("disk") or {}).get("free_mib")
    usable = int(gpu["available_mib"]) if gpu else 0
    if gpu and gpu.get("unified"):
        tier = "Apple unified memory"
    elif gpu and usable >= 28000:
        tier = "a workstation-class card"
    elif gpu and usable >= 20000:
        tier = "a 24 GB card"
    elif gpu and usable >= 13000:
        tier = "a 16 GB card"
    elif gpu and usable >= 9000:
        tier = "a 12 GB card"
    elif gpu and usable >= 6300:
        tier = "an 8 GB card"
    elif gpu and usable >= 3000:
        tier = "a small card: partial offload"
    elif gpu:
        tier = "a card too small to hold a brain"
    else:
        tier = "no graphics card: processor only"
    return {
        "gpu": gpu, "ram": ram, "disk_free_mib": disk, "tier": tier,
        "line": "%s · %s of %s GB usable · %s GB RAM for models · %s GB disk free" % (
            (gpu or {}).get("name", "No GPU"),
            round(usable / 1024, 1) if gpu else 0,
            round(int((gpu or {}).get("total_mib") or 0) / 1024, 1) if gpu else 0,
            round(int(ram.get("available_target_mib") or 0) / 1024, 1),
            round(int(disk or 0) / 1024, 1)),
        "simulated": bool(profile.get("simulated")),
        "profile_path": "runtime/residency/hardware-profile.json (never leaves this PC)",
    }


def _measured_tok_s(model_id: str, profile: dict) -> float | None:
    try:
        from agent_friday.services import residency_catalog as rc
        rows = rc.measurements(model_id, rc.profile_fingerprint(profile))
        rows = [r for r in rows if r.get("tok_s_median")]
        if not rows:
            return None
        return float(max(rows, key=lambda r: r.get("num_ctx") or 0)["tok_s_median"])
    except Exception:
        return None


def _measured_load_s(model_id: str, profile: dict) -> float | None:
    try:
        from agent_friday.services import residency_catalog as rc
        rows = [r for r in rc.measurements(model_id, rc.profile_fingerprint(profile)) if r.get("cold_load_s")]
        return float(rows[0]["cold_load_s"]) if rows else None
    except Exception:
        return None


def _published_tok_s(m: dict, packing: str | None, gpu_name: str | None) -> float | None:
    pub = m.get("published_speed") or {}
    n = (gpu_name or "").lower()
    for card, speeds in pub.items():
        if card == "source" or not isinstance(speeds, dict):
            continue
        if card.lower() in n:
            v = speeds.get(packing or "")
            return float(v) if v else None
    return None


def _layout_for(m: dict) -> dict | None:
    k = m.get("kv_layout")
    if not k:
        return None
    return {"blocks": int(k.get("blocks") or 0) or None, "kv_heads": int(k["kv_heads"]),
            "head_dim_k": int(k["head_dim"]), "head_dim_v": int(k["head_dim"]),
            "full_attention_layers": int(k["full_attention_layers"]), "window": None, "window_layers": 0}


def _layout_from_file(path: str, arch: str | None) -> dict | None:
    try:
        from agent_friday.services.gguf_extract import gguf_metadata
        if not arch:
            arch = gguf_metadata(path).get("general.architecture")
        keys = tuple("%s.%s" % (arch, k) for k in (
            "block_count", "attention.head_count_kv", "attention.head_count", "attention.key_length",
            "attention.value_length", "embedding_length", "full_attention_interval",
            "attention.sliding_window", "attention.sliding_window_pattern", "context_length"))
        meta = gguf_metadata(path, keys=keys)
        lay = mf.kv_layout_from_header(meta, arch)
        if lay:
            lay["context_training"] = meta.get("%s.context_length" % arch)
        return lay
    except Exception:
        return None


def file_row(m: dict, f: dict, profile: dict, *, pinned_vram_mib=0, pinned_ram_mib=0,
             installed=False, measured_tok_s=None) -> dict:
    layout = _layout_for(m)
    gpu_name = ((profile.get("gpus") or [{}])[0] or {}).get("name")
    mm = next((c for c in m.get("companions") or [] if c.get("kind") == "mmproj"), None)
    verdict = mf.fit(int(f.get("bytes") or 0), profile, layout=layout,
                     context_cap=(m.get("serve") or {}).get("serve_num_ctx") or m.get("context_training"),
                     mmproj_bytes=int((mm or {}).get("bytes") or 0) if installed else 0,
                     pinned_vram_mib=pinned_vram_mib, pinned_ram_mib=pinned_ram_mib,
                     gpu_name=gpu_name, blocks=(layout or {}).get("blocks"),
                     published_tok_s=_published_tok_s(m, f.get("packing"), gpu_name),
                     measured_tok_s=measured_tok_s)
    return {
        "file": f.get("file"), "packing": f.get("packing"), "bytes": f.get("bytes"),
        "gib": round(int(f.get("bytes") or 0) / 2 ** 30, 2), "default": bool(f.get("default")),
        "runtime": f.get("runtime") or m.get("runtime"), "note": f.get("note"),
        "verdict": verdict["verdict"], "verdict_word": VERDICT_WORDS[verdict["verdict"]],
        "context": verdict["context"], "speed": verdict["speed"], "placement": verdict["placement"],
        "shortfall_mib": verdict["shortfall_mib"], "seat": verdict["seat"], "why": verdict["why"],
        "sha256": f.get("sha256"),
    }


def _bench_summary(model_id: str) -> dict | None:
    try:
        from agent_friday.services import model_bench
        r = model_bench.last_receipt(model_id)
        if not r:
            return None
        res = r.get("result") or {}
        return {"status": r.get("status"), "decode_tok_s": res.get("decode_tok_s"),
                "prompt_tok_s": res.get("prompt_tok_s"), "peak_vram_delta_mib": res.get("peak_vram_delta_mib"),
                "finished_at": r.get("finished_at"), "error": r.get("error")}
    except Exception:
        return None


def _previous_available(model_id: str) -> bool:
    try:
        from agent_friday.services import model_remove
        return model_remove.previous_version(model_id) is not None
    except Exception:
        return False


def model_row(m: dict, profile: dict, *, installed: bool, pinned_vram_mib=0, pinned_ram_mib=0) -> dict:
    measured = _measured_tok_s(m["id"], profile)
    files = [file_row(m, f, profile, pinned_vram_mib=pinned_vram_mib, pinned_ram_mib=pinned_ram_mib,
                      installed=installed, measured_tok_s=measured)
             for f in (m.get("files") or [])]
    best = max(files, key=lambda r: (["wont_fit", "partial", "tight", "runs_well"].index(r["verdict"]),
                                     r["context"] or 0), default=None)
    return {
        "id": m["id"], "label": m.get("label") or m["id"], "publisher": m.get("publisher"),
        "friday_standard": bool(m.get("friday_standard")), "roles": m.get("roles") or [],
        "licence": m.get("licence"), "licence_class": m.get("licence_class") or licence_class(m.get("licence")),
        "telemetry": m.get("telemetry") or "none", "repo_url": m.get("repo_url"), "runtime": m.get("runtime"),
        "generation_note": m.get("generation_note"), "installed": installed, "source": m.get("source", "shortlist"),
        "files": files, "best": best["verdict"] if best else None,
        "bench": _bench_summary(m["id"]) if installed else None,
        "previous_available": _previous_available(m["id"]),
        "roles_bound": _roles_bound(m["id"]) if installed else [],
        "companions": [{"file": c["file"], "kind": c.get("kind"), "gib": round((c.get("bytes") or 0) / 2 ** 30, 2)}
                       for c in m.get("companions") or []],
    }


def _roles_bound(model_id: str) -> list:
    try:
        from agent_friday.services import model_remove
        return model_remove.roles_bound_to(model_id)
    except Exception:
        return []


def store_models() -> list[dict]:
    """Installed models as shortlist-shaped entries, sized from their files."""
    out = []
    try:
        from agent_friday.services import model_store
        avail = model_store.available()
    except Exception:
        return out
    shortlist_ids = {m["id"] for m in sl.entries()}
    for mid, rec in avail.items():
        if mid in shortlist_ids:
            continue
        path = rec.get("path")
        lay = _layout_from_file(path, rec.get("architecture")) if path else None
        origin = rec.get("origin") or {}
        out.append({
            "id": mid, "label": rec.get("label") or mid, "publisher": origin.get("repo"),
            "roles": ["brain"], "runtime": "prism-fork" if rec.get("engine") else "llama.cpp",
            "repo_url": ("https://huggingface.co/" + origin["repo"]) if origin.get("repo") else None,
            "licence": origin.get("licence"), "source": "installed",
            "context_training": (lay or {}).get("context_training") or rec.get("context_window"),
            "serve": {"serve_num_ctx": rec.get("serve_num_ctx")} if rec.get("serve_num_ctx") else {},
            "kv_layout": ({"blocks": lay["blocks"], "kv_heads": lay["kv_heads"], "head_dim": lay["head_dim_k"],
                           "full_attention_layers": lay["full_attention_layers"]} if lay else None),
            "files": [{"file": rec.get("path", "").replace("\\", "/").split("/")[-1],
                       "packing": str(rec.get("quantization") or ""), "bytes": rec.get("size_bytes"),
                       "default": True, "sha256": rec.get("sha256")}],
            "companions": ([{"file": "mmproj", "kind": "mmproj", "bytes": 0}] if rec.get("mmproj") else []),
        })
    return out


def resident_mib() -> tuple[int, int]:
    """VRAM and RAM the pinned seats hold right now, from the arbiter's plan;
    a candidate is placed beside them."""
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        seats = ((arb.plan if arb else {}) or {}).get("seats") or {}
        vram = sum(int(s.get("vram_mib") or 0) for s in seats.values()
                   if isinstance(s, dict) and s.get("status") == "pinned" and str(s.get("device", "")).startswith("gpu"))
        ram = sum(int(s.get("vram_mib") or 0) for s in seats.values()
                  if isinstance(s, dict) and s.get("status") == "pinned" and s.get("device") == "cpu")
        return vram, ram
    except Exception:
        return 0, 0


def catalog_payload(profile: dict | None = None, *, beside_resident: bool = False) -> dict:
    from agent_friday.services import hardware_profile as hwp
    prof = profile or hwp.get()
    try:
        from agent_friday.services import model_store
        installed = set(model_store.available().keys())
    except Exception:
        installed = set()
    pv, pr = resident_mib() if beside_resident else (0, 0)
    rows = [model_row(m, prof, installed=m["id"] in installed, pinned_vram_mib=pv, pinned_ram_mib=pr)
            for m in sl.entries()]
    rows += [model_row(m, prof, installed=True, pinned_vram_mib=pv, pinned_ram_mib=pr) for m in store_models()]
    return {"status": "ok", "machine": machine_line(prof), "models": rows,
            "calibration": mf.calibration_factor(), "beside_resident": beside_resident,
            "pinned_vram_mib": pv, "generated_at": time.time()}


def check_hf_repo(repo: str, profile: dict | None = None) -> dict:
    """One anonymous metadata read for a pasted Hugging Face id: the GGUF
    files with their real bytes and sha256, each with a verdict. The
    attention layout is not read remotely, so the KV cache is not counted
    and the row says so."""
    repo = repo.strip().strip("/")
    if repo.startswith("https://huggingface.co/"):
        repo = repo[len("https://huggingface.co/"):].strip("/")
    if repo.count("/") != 1:
        return {"status": "error", "error": "a Hugging Face id looks like publisher/model"}
    resp = _http_get(HF_API.format(repo=repo), timeout=(10, 30))
    if int(getattr(resp, "status_code", 0) or 0) != 200:
        return {"status": "error", "error": "Hugging Face answered %s for %s" % (getattr(resp, "status_code", "?"), repo)}
    d = resp.json()
    card = d.get("cardData") or {}
    files = []
    for s in d.get("siblings") or []:
        name = s.get("rfilename") or ""
        if not name.lower().endswith(".gguf") or "mmproj" in name.lower():
            continue
        lfs = s.get("lfs") or {}
        packing = name.rsplit(".", 1)[0].split("-")[-1].split("_", 1)[-1] if "-" in name else name
        files.append({"file": name, "packing": name.rsplit(".", 1)[0].split("-")[-1],
                      "bytes": s.get("size") or lfs.get("size"), "sha256": lfs.get("sha256")})
    if not files:
        return {"status": "error", "error": "%s has no GGUF files; only llama.cpp models can be fetched here" % repo}
    lic = card.get("license")
    m = {"id": "hf:" + repo.split("/")[1].lower(), "label": repo.split("/")[1], "publisher": repo.split("/")[0],
         "roles": ["brain"], "runtime": "llama.cpp", "repo": repo, "repo_url": "https://huggingface.co/" + repo,
         "licence": lic, "licence_class": licence_class(lic), "telemetry": "none",
         "gated": d.get("gated"), "files": files, "companions": [], "source": "pasted"}
    from agent_friday.services import hardware_profile as hwp
    row = model_row(m, profile or hwp.get(), installed=False)
    row["kv_note"] = "the KV cache is not counted for a pasted model until its file is here; the verdict is weights-only"
    row["gated"] = d.get("gated")
    return {"status": "ok", "model": row}
