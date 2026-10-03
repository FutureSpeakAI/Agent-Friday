"""The curated shortlist the Models screen offers, and the runtime it needs.

The shortlist ships with the release as `resources/model_shortlist.json`.
Each file entry carries the publisher's byte count and sha256 as published on
Hugging Face; the downloader verifies against them, so a row here is a claim
the install can check. Nothing is fetched to read it.

The runtime section maps (OS family, backend) to the one release asset of
the PrismML llama.cpp fork that serves Bonsai 2's ternary tensor types,
which stock llama.cpp refuses. The asset's size and digest come from the
GitHub release at download time, not from this file, so a new release needs
no edit here.
"""
from __future__ import annotations

import json
import platform
import sys
from functools import lru_cache
from pathlib import Path

SHORTLIST_PATH = Path(__file__).resolve().parent.parent / "resources" / "model_shortlist.json"

HF_RESOLVE = "https://huggingface.co/{repo}/resolve/main/{file}"


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(SHORTLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def reload_for_tests() -> None:
    _load.cache_clear()


def entries() -> list[dict]:
    return [dict(m) for m in _load().get("models") or []]


def get(model_id: str) -> dict | None:
    for m in _load().get("models") or []:
        if m.get("id") == model_id:
            return dict(m)
    return None


def file_entry(model_id: str, packing: str | None = None) -> dict | None:
    """The file to fetch for a model: the named packing, else the default."""
    m = get(model_id)
    if not m:
        return None
    files = m.get("files") or []
    if packing:
        return next((dict(f) for f in files if f.get("packing") == packing), None)
    return next((dict(f) for f in files if f.get("default")), dict(files[0]) if files else None)


def file_url(model_id: str, entry: dict) -> str:
    m = get(model_id) or {}
    return HF_RESOLVE.format(repo=m.get("repo", ""), file=entry["file"])


def companions(model_id: str) -> list[dict]:
    return [dict(c) for c in (get(model_id) or {}).get("companions") or []]


def runtime_spec(name: str = "prism-fork") -> dict:
    return dict((_load().get("runtime") or {}).get(name) or {})


def os_family() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def backend_for(profile: dict | None) -> str:
    """Which build of the fork this machine should run.

    NVIDIA cards take the CUDA build; an AMD card the HIP build on Windows
    and ROCm on Linux; any other GPU the Vulkan build; Apple Silicon Metal;
    no GPU the CPU build. The profile's `vendor` field is what decides it,
    so an undetected card falls to CPU rather than to a build that cannot
    load.
    """
    fam = os_family()
    gpus = (profile or {}).get("gpus") or []
    vendor = str((gpus[0].get("vendor") if gpus else "") or "").lower()
    if fam == "darwin":
        return "metal" if platform.machine().startswith("arm") else "cpu"
    if vendor == "nvidia":
        return "cuda"
    if vendor == "amd":
        return "hip" if fam == "windows" else "rocm"
    if vendor:
        return "vulkan"
    return "cpu"


def runtime_asset_names(backend: str, fam: str | None = None) -> list[str]:
    """The asset name suffixes to fetch for (os, backend), main binary first."""
    fam = fam or os_family()
    spec = runtime_spec()
    assets = (spec.get("assets") or {}).get(fam) or {}
    main = assets.get(backend) or assets.get("cpu")
    out = [main] if main else []
    if backend == "cuda" and assets.get("cuda_runtime"):
        out.append(assets["cuda_runtime"])
    return out


def pick_release_assets(release: dict, backend: str, fam: str | None = None) -> list[dict]:
    """From a GitHub release payload, the assets this machine needs:
    `{name, url, size, sha256}` each, main binary first."""
    wanted = runtime_asset_names(backend, fam)
    out = []
    for suffix in wanted:
        for a in release.get("assets") or []:
            name = str(a.get("name") or "")
            if name.endswith(suffix):
                digest = str(a.get("digest") or "")
                out.append({"name": name,
                            "url": a.get("browser_download_url"),
                            "size": a.get("size"),
                            "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else None})
                break
    return out
