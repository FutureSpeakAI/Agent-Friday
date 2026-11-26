"""Fine-tuned adapters as their own seats, installed by the app (Dispatch 11-26: never by hand-editing models.json).

An adapter seat is a base record's weights and projector plus a different LoRA, under a new model id. The base
record is read, never written: FridayWeaver-1.0 stays exactly as it was, so choosing it again (or `uninstall`) is the
rollback. Only an adapter listed in resources/adapter_seats.json installs, and only the exact file its sha256 pins.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from agent_friday.services import model_store as ms

MANIFEST = Path(__file__).resolve().parent.parent / "resources" / "adapter_seats.json"


def _read_manifest() -> dict:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {e["model_id"]: e for e in data.get("adapters") or []}


def manifest() -> dict:
    return _read_manifest()


def _is_lora(path) -> bool:
    from agent_friday.services.gguf_extract import gguf_metadata
    try:
        meta = gguf_metadata(path, keys=("general.type", "adapter.type"))
    except Exception:
        return False
    return meta.get("general.type") == "adapter" and meta.get("adapter.type") == "lora"


def install(model_id: str, source) -> dict:
    """Copy the pinned adapter into the store and record the seat. Idempotent."""
    entry = manifest().get(model_id)
    if not entry:
        return {"ok": False, "error": "%s is not a known adapter seat" % model_id}
    base = ms.get(entry["base_from"])
    if not base:
        return {"ok": False, "error": "base record %s is not installed" % entry["base_from"]}
    files = ms.seat_files(base)
    if not files.get("gguf"):
        return {"ok": False, "error": "the base weights of %s are not reachable" % entry["base_from"]}
    dest = Path(ms.store_dir()) / entry["adapter_file"]
    have = ms.get(model_id)
    if have and dest.exists() and ms.sha256_of(dest) == entry["sha256"]:
        return {"ok": True, "already": True, "entry": have}

    src = Path(source) if source else dest
    # A local file only: a network path would be probed (and could stall, or authenticate to another host) before
    # the hash could refuse it.
    if str(src).startswith(("\\\\", "//")):
        return {"ok": False, "error": "the adapter must be a file on this computer"}
    if not src.exists():
        return {"ok": False, "error": "no adapter file at %s" % src}
    if ms.sha256_of(src) != entry["sha256"]:
        # The file's own hash is not echoed: the route must not become a way to hash arbitrary files.
        return {"ok": False, "error": "sha256 mismatch: that file is not the pinned %s adapter" % model_id}
    if not _is_lora(src):
        return {"ok": False, "error": "%s is not a GGUF LoRA adapter" % src}
    if src.resolve() != dest.resolve():
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(src, tmp)
        if ms.sha256_of(tmp) != entry["sha256"]:
            tmp.unlink()
            return {"ok": False, "error": "the copy did not verify"}
        tmp.replace(dest)

    # The projector the base really has on disk (FW1's record names a local mmproj copy that may not exist; the
    # store-dir fallback finds the one that does).
    mmproj = files.get("mmproj")
    origin = dict(entry.get("origin") or {})
    origin.update({"base_from": entry["base_from"], "adapter_sha256": entry["sha256"],
                   "installed_by": "adapter_seats", "installed_at": time.time()})
    local = {"path": files["gguf"], "lora": str(dest)}
    if mmproj:
        local["mmproj"] = mmproj
    rec = ms.register(model_id, files["gguf"], lora=str(dest), mmproj=mmproj, label=entry.get("label"),
                      origin=origin, local_files=local,
                      chat_template=base.get("chat_template"),
                      serve_args=base.get("serve_args"), serve_num_ctx=base.get("serve_num_ctx"))
    return {"ok": True, "entry": rec}


def uninstall(model_id: str) -> dict:
    """The rollback: drop the seat this module installed. The copied adapter stays on disk; the base is untouched."""
    rec = ms.get(model_id)
    if not rec:
        return {"ok": True, "already": True}
    if (rec.get("origin") or {}).get("installed_by") != "adapter_seats":
        return {"ok": False, "error": "%s was not installed as an adapter seat; not removing it" % model_id}
    ms.forget(model_id, delete_file=False)
    return {"ok": True}


def status() -> list:
    out = []
    for mid, e in manifest().items():
        rec = ms.get(mid)
        out.append({"model_id": mid, "label": e.get("label"), "installed": bool(rec),
                    "base_from": e["base_from"], "sha256": e["sha256"]})
    return out
