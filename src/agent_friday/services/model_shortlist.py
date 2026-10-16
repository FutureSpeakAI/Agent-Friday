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
import logging
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
    """The shortlist entry for ``model_id``: the exact id first, then an
    equivalent spelling (``bonsai2-27b`` finds ``bonsai2:27b``; see
    ``model_ids.canonical``). The entry keeps its own ``id``."""
    models = _load().get("models") or []
    for m in models:
        if m.get("id") == model_id:
            return dict(m)
    from agent_friday.services.model_ids import canonical
    key = canonical(model_id)
    if key:
        for m in models:
            if canonical(m.get("id")) == key:
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


#: A release's CUDA runtime DLL pack. Its name ENDS with the same platform
#: suffix as the server archive (``cudart-llama-bin-win-cuda-12.4-x64.zip``
#: vs ``llama-prism-b10743-bin-win-cuda-12.4-x64.zip``), so a suffix match
#: alone cannot tell them apart.
CUDART_PREFIX = "cudart-"

#: What the server archive's name carries; every llama.cpp release names its
#: builds ``llama-<build>-bin-<platform>``.
SERVER_ASSET_MARK = "llama"


def _asset_row(a: dict, role: str) -> dict:
    digest = str(a.get("digest") or "")
    return {"name": str(a.get("name") or ""),
            "url": a.get("browser_download_url"),
            "size": a.get("size"),
            "sha256": digest.split(":", 1)[1] if digest.startswith("sha256:") else None,
            "role": role}


def _is_server_archive(name: str, suffix: str) -> bool:
    low = name.lower()
    return (not low.startswith(CUDART_PREFIX)
            and SERVER_ASSET_MARK in low
            and low.endswith(suffix.lower()))


def _is_cudart_pack(name: str, runtime_name: str) -> bool:
    low = name.lower()
    return low.startswith(CUDART_PREFIX) and low.endswith(runtime_name.lower())


def pick_release_assets(release: dict, backend: str, fam: str | None = None) -> list[dict]:
    """From a GitHub release payload, the assets this machine needs:
    `{name, url, size, sha256, role}` each, the server archive first.

    Each asset is chosen by an explicit rule, never by suffix alone:

      * the SERVER archive (role ``server``) does not start with ``cudart-``,
        carries ``llama`` in its name, and ends with the platform suffix for
        (os, backend); among several, one starting ``llama-`` is preferred;
      * the CUDA runtime DLL pack (role ``cuda_runtime``, CUDA builds only)
        starts with ``cudart-`` and ends with the spec's runtime name.

    The same asset is never returned twice. With no server archive the list
    is empty: a DLL pack alone serves nothing. Each choice and the reason for
    it is written to friday.log.
    """
    log = logging.getLogger("friday.model_shortlist")
    fam = fam or os_family()
    wanted = runtime_asset_names(backend, fam)
    assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
    if not wanted:
        log.warning("runtime assets: no build is listed for %s/%s", fam, backend)
        return []
    suffix = wanted[0]
    servers = [a for a in assets if _is_server_archive(str(a.get("name") or ""), suffix)]
    skipped = [str(a.get("name") or "") for a in assets
               if str(a.get("name") or "").lower().endswith(suffix.lower())
               and a not in servers]
    if not servers:
        log.warning("runtime assets: no server archive matches *%s for %s/%s "
                    "(not cudart-*, contains %r); skipped %s",
                    suffix, fam, backend, SERVER_ASSET_MARK, skipped or "nothing")
        return []
    servers.sort(key=lambda a: 0 if str(a.get("name") or "").lower().startswith("llama-") else 1)
    main = _asset_row(servers[0], "server")
    log.info("runtime assets: server archive %s for %s/%s (ends with %s, not cudart-*, "
             "contains %r)%s", main["name"], fam, backend, suffix, SERVER_ASSET_MARK,
             ("; skipped %s" % ", ".join(skipped)) if skipped else "")
    out = [main]
    for runtime_name in wanted[1:]:
        packs = [a for a in assets
                 if _is_cudart_pack(str(a.get("name") or ""), runtime_name)
                 and str(a.get("name") or "") != main["name"]]
        if not packs:
            log.warning("runtime assets: no CUDA runtime pack %s in the release; the server "
                        "archive may not start without its DLLs", runtime_name)
            continue
        exact = [a for a in packs if str(a.get("name") or "").lower() == runtime_name.lower()]
        pack = _asset_row((exact or packs)[0], "cuda_runtime")
        log.info("runtime assets: CUDA runtime pack %s (starts with %s, ends with %s)",
                 pack["name"], CUDART_PREFIX, runtime_name)
        out.append(pack)
    return out
