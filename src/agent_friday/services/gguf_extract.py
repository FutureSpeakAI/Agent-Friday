"""
Agent Friday — extract real GGUF files out of Ollama's blob store.

This is what lets the Arbiter own the runtime. Rule R9 says a pinned seat is
not delegated to a backend scheduler that evicts on its own criteria — but
until now it had to be, because `llama-server` could not be pointed at an
Ollama model and the seats fell back to the "degraded pin" path where the
daemon decides. Measured consequence, 2026-08-15: the brain loaded, the
sidekick loaded, and Ollama then evicted the brain, so the seat the plan called
pinned was not resident and every first message of a session paid a ~13 s cold
load.

The earlier attempt failed with:

    wrong number of tensors; expected 2012, got 601

which was read as "Ollama stores models across several blobs and llama.cpp
cannot reassemble them". That was wrong. The manifest says so plainly — each
model has exactly ONE layer of mediaType `application/vnd.ollama.image.model`,
and it is a complete GGUF:

    gemma4:12b   model 7039.4 MB   projector 167.0 MB   license   params
    gemma4:e2b   model 6830.6 MB                        license   params
    gemma4:e4b   model 9163.2 MB                        license   params

My second guess was wrong too, and is worth recording because it was
plausible: I assumed the 601-tensor file had been the 167 MB projector picked
by mistake. Reading the GGUF headers settled it —

    gemma4-12b   arch=gemma4   667 tensors   chat template: EMBEDDED
    gemma4-e2b   arch=gemma4  2012 tensors   chat template: NONE
    gemma4-e4b   arch=gemma4  2131 tensors   chat template: NONE

The e2b file declares 2012 tensors, exactly the number llama.cpp expected. It
got 601 because **upstream's gemma4 reader recognises only 601 of the names in
it** — a genuine support gap for this variant, not a broken or mistaken file.
Ollama's own engine binary loads the identical extracted file and generates
from it, verified 2026-08-15 ("ready." in 0.90 s). So the file is portable; the
upstream *reader* is not. `residency_arbiter.LlamaServerBackend` tries upstream
first and falls back to Ollama's engine — as a process the Arbiter owns.

**Copy, not symlink or reference.** The blobs are content-addressed and Ollama
owns their lifecycle: `ollama rm` or a re-pull can remove one out from under a
running seat. Extracting costs disk (~23 GB for the three seats on the
reference instance, against 329 GB free) and buys a file whose lifetime we
control. That is the whole point of the exercise.

What this does NOT do is make Friday independent of Ollama. It removes the
daemon from the INFERENCE path. Discovery, capabilities and downloads still go
through it — see `ollama_dependencies()` at the bottom, which reports exactly
what is left rather than letting anyone assume.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import time
from pathlib import Path

from agent_friday.core import runtime_dir

MEDIA_MODEL = "application/vnd.ollama.image.model"
MEDIA_PROJECTOR = "application/vnd.ollama.image.projector"
MEDIA_PARAMS = "application/vnd.ollama.image.params"
MEDIA_TEMPLATE = "application/vnd.ollama.image.template"

# A GGUF starts with the four bytes "GGUF". Checked before and after copying:
# a blob that is not a GGUF must fail here rather than at llama-server start,
# where the error is a tensor-count mismatch that reads like corruption.
GGUF_MAGIC = b"GGUF"


def ollama_root() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        return Path(env)
    return Path.home() / ".ollama" / "models"


def target_dir() -> Path:
    return runtime_dir() / "models" / "gguf"


def _manifest_path(model_id: str) -> Path:
    """`gemma4:12b` -> .../manifests/registry.ollama.ai/library/gemma4/12b."""
    name, _, tag = model_id.partition(":")
    tag = tag or "latest"
    if "/" in name:
        namespace, _, name = name.rpartition("/")
    else:
        namespace = "library"
    return (ollama_root() / "manifests" / "registry.ollama.ai" / namespace /
            name / tag)


def _blob_path(digest: str) -> Path:
    return ollama_root() / "blobs" / digest.replace(":", "-")


def manifest(model_id: str) -> dict | None:
    try:
        return json.loads(_manifest_path(model_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def layers(model_id: str) -> dict:
    """mediaType -> {digest, size, path}. The map the extractor works from.

    Reading the manifest rather than scanning `blobs/` is the entire fix: the
    directory contains every layer of every model with content-addressed names
    and no types, so picking a file by size or date is a coin flip that lands
    on a projector often enough to look like a systemic incompatibility.
    """
    man = manifest(model_id) or {}
    out = {}
    for lay in man.get("layers") or []:
        mt = lay.get("mediaType")
        if not mt:
            continue
        out[mt] = {"digest": lay["digest"], "size": lay.get("size") or 0,
                   "path": _blob_path(lay["digest"])}
    return out


def _is_gguf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == GGUF_MAGIC
    except Exception:
        return False


def safe_filename(model_id: str) -> str:
    return model_id.replace(":", "-").replace("/", "-") + ".gguf"


def plan_extraction(model_ids: list) -> dict:
    """What extracting these would cost, and what is already done. No I/O cost.

    Separate from `extract` so the disk bill can be shown before it is spent —
    23 GB is not an amount to move without saying so first.
    """
    items, total, missing = [], 0, []
    for model_id in model_ids:
        lay = layers(model_id)
        model = lay.get(MEDIA_MODEL)
        if not model:
            missing.append(model_id)
            continue
        dest = target_dir() / safe_filename(model_id)
        done = dest.exists() and dest.stat().st_size == model["size"]
        items.append({
            "model_id": model_id,
            "size_mib": round(model["size"] / 1048576),
            "dest": str(dest),
            "already_extracted": done,
            "has_projector": MEDIA_PROJECTOR in lay,
            "source_exists": model["path"].exists(),
        })
        if not done:
            total += model["size"]
    free = shutil.disk_usage(str(target_dir().anchor)).free
    return {
        "items": items,
        "missing": missing,
        "to_copy_mib": round(total / 1048576),
        "free_mib": round(free / 1048576),
        "fits": free - total > 10 * 1024 ** 3,     # R8's 10 GB floor
    }


def extract(model_id: str, *, force: bool = False,
            progress=None) -> dict:
    """Copy one model's GGUF (and projector) into our own directory.

    Returns a result dict rather than raising, because a failed extraction for
    one seat must not stop the others — a machine with two of three seats owned
    is strictly better than one with none.
    """
    t0 = time.time()
    lay = layers(model_id)
    model = lay.get(MEDIA_MODEL)
    if not model:
        return {"ok": False, "model_id": model_id,
                "error": "no manifest, or no %s layer — is it installed?"
                         % MEDIA_MODEL}
    src = model["path"]
    if not src.exists():
        return {"ok": False, "model_id": model_id,
                "error": "manifest names a blob that is not on disk: %s" % src}
    if not _is_gguf(src):
        return {"ok": False, "model_id": model_id,
                "error": "the model layer is not a GGUF (bad magic bytes)"}

    dest = target_dir() / safe_filename(model_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    reused = False
    if dest.exists() and dest.stat().st_size == model["size"] and not force:
        reused = True
    else:
        tmp = dest.with_suffix(".gguf.part")
        _copy(src, tmp, progress=progress, label=model_id)
        if not _is_gguf(tmp):
            tmp.unlink(missing_ok=True)
            return {"ok": False, "model_id": model_id,
                    "error": "copy did not verify as a GGUF"}
        os.replace(tmp, dest)

    proj_dest = None
    proj = lay.get(MEDIA_PROJECTOR)
    if proj and proj["path"].exists():
        # The vision tower. Kept beside the weights and passed as --mmproj, not
        # confused for them — mistaking it for the model is what produced the
        # "expected 2012, got 601" that stalled this for a day.
        proj_dest = target_dir() / (safe_filename(model_id)[:-5] + ".mmproj.gguf")
        if not (proj_dest.exists() and
                proj_dest.stat().st_size == proj["size"]) or force:
            tmp = proj_dest.with_suffix(".gguf.part")
            _copy(proj["path"], tmp, progress=progress,
                  label=model_id + " (projector)")
            os.replace(tmp, proj_dest)

    from agent_friday.services import residency_catalog as rc
    rc.register_gguf(model_id, dest)

    return {
        "ok": True, "model_id": model_id, "path": str(dest),
        "projector": str(proj_dest) if proj_dest else None,
        "size_mib": round(model["size"] / 1048576),
        "reused": reused,
        "seconds": round(time.time() - t0, 1),
        "params": _read_params(lay),
    }


def gguf_metadata(path, keys=("general.architecture", "tokenizer.chat_template",
                             "general.name")) -> dict:
    """Read the GGUF key/value header. Enough of a parser to answer two
    questions the seats depend on: what architecture, and is there a chat
    template.

    Arrays are consumed in FULL even though nothing here wants their contents —
    reading a prefix and moving on desynchronises every offset that follows,
    which presents as a nonsense type id hundreds of keys later.
    """
    fmt_of = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?",
              10: "Q", 11: "q", 12: "d"}
    out = {}
    with open(path, "rb") as f:
        if f.read(4) != GGUF_MAGIC:
            return {}
        f.read(4)
        out["__n_tensors"] = struct.unpack("<Q", f.read(8))[0]
        n_kv = struct.unpack("<Q", f.read(8))[0]

        def rd_str():
            n = struct.unpack("<Q", f.read(8))[0]
            return f.read(n).decode("utf-8", "replace")

        def rd_val(t):
            if t == 8:
                return rd_str()
            if t == 9:
                et = struct.unpack("<I", f.read(4))[0]
                n = struct.unpack("<Q", f.read(8))[0]
                return [rd_val(et) for _ in range(n)]
            fmt = fmt_of[t]
            return struct.unpack("<" + fmt,
                                 f.read(struct.calcsize("<" + fmt)))[0]

        for _ in range(n_kv):
            k = rd_str()
            t = struct.unpack("<I", f.read(4))[0]
            v = rd_val(t)
            if k in keys:
                out[k] = v
    return out


def chat_template_path(model_id: str) -> Path:
    return target_dir() / (safe_filename(model_id)[:-5] + ".template.jinja")


def ensure_chat_template(model_id: str, family_source: str | None = None) -> dict:
    """Make sure this seat has a chat template, borrowing one if it has none.

    Measured 2026-08-15 on the extracted files:

        gemma4-12b   arch=gemma4  667 tensors   template: EMBEDDED (with the
                                                tool-calling macros)
        gemma4-e2b   arch=gemma4  2012 tensors  template: NONE
        gemma4-e4b   arch=gemma4  2131 tensors  template: NONE

    A model with no template makes llama-server fall back to ChatML, which is
    not a cosmetic problem. It leaked `<|im_end|>` into the sidekick's replies,
    and more seriously it would have handed the seat a template with **no tool
    definitions in it** — the seat would look like a model that cannot call
    tools, which is the exact misdiagnosis this codebase has now made twice.

    Borrowing across the family is safe here and narrow: same `general.
    architecture`, same tokenizer, same turn markers. The borrow is RECORDED in
    the result so it is never mistaken for the model's own.
    """
    dest = chat_template_path(model_id)
    own = gguf_metadata(target_dir() / safe_filename(model_id))
    tmpl = own.get("tokenizer.chat_template")
    if tmpl:
        dest.write_text(tmpl, encoding="utf-8")
        return {"ok": True, "model_id": model_id, "source": "embedded",
                "path": str(dest), "arch": own.get("general.architecture")}

    if not family_source:
        return {"ok": False, "model_id": model_id, "source": None,
                "error": "no embedded chat template and no family source given;"
                         " llama-server would fall back to ChatML and the seat "
                         "would silently lose its tool definitions",
                "arch": own.get("general.architecture")}

    src_file = target_dir() / safe_filename(family_source)
    src = gguf_metadata(src_file)
    if own.get("general.architecture") != src.get("general.architecture"):
        return {"ok": False, "model_id": model_id, "source": None,
                "error": "refusing to borrow a template across architectures "
                         "(%s vs %s)" % (own.get("general.architecture"),
                                         src.get("general.architecture"))}
    borrowed = src.get("tokenizer.chat_template")
    if not borrowed:
        return {"ok": False, "model_id": model_id, "source": None,
                "error": "%s has no embedded template either" % family_source}
    dest.write_text(borrowed, encoding="utf-8")
    return {"ok": True, "model_id": model_id, "source": "borrowed",
            "borrowed_from": family_source, "path": str(dest),
            "arch": own.get("general.architecture")}


def _read_params(lay: dict) -> dict:
    """Ollama's own sampling defaults, so a seat we serve behaves the same.

    Not applied automatically — recorded, so a difference between "on Ollama"
    and "on our llama-server" can be explained instead of guessed at.
    """
    p = lay.get(MEDIA_PARAMS)
    if not p or not p["path"].exists():
        return {}
    try:
        return json.loads(p["path"].read_text(encoding="utf-8"))
    except Exception:
        return {}


def _copy(src: Path, dest: Path, *, progress=None, label="", chunk=8 << 20):
    total = src.stat().st_size
    done = 0
    last = 0.0
    with open(src, "rb") as fi, open(dest, "wb") as fo:
        while True:
            buf = fi.read(chunk)
            if not buf:
                break
            fo.write(buf)
            done += len(buf)
            if progress and (time.time() - last) > 2.0:
                last = time.time()
                progress(label, done, total)
    if progress:
        progress(label, done, total)


def extract_all(model_ids: list, *, progress=None) -> list:
    return [extract(m, progress=progress) for m in model_ids]


# ─────────────────────────────────────────────────────────────────────────────
#  What is still Ollama's after this
# ─────────────────────────────────────────────────────────────────────────────

def ollama_dependencies() -> list:
    """An honest inventory, because "we don't need Ollama any more" is a claim
    that is easy to make and easy to be wrong about.

    Extraction removes the daemon from the INFERENCE path — every pinned seat
    becomes a process the Arbiter starts, health-checks and terminates, and the
    heavy seat already was one. These are what remain.
    """
    return [
        {"area": "model discovery",
         "where": "residency_catalog.installed_entries -> "
                  "ollama_manager.list_models",
         "needs_daemon": True,
         "replacement": "enumerate our own GGUF directory; the registry "
                        "already exists (gguf_models.json) and already carries "
                        "models Ollama does not have",
         "effort": "small"},
        {"area": "model capabilities (completion / tools / thinking / embedding)",
         "where": "residency_catalog._show -> /api/show",
         "needs_daemon": True,
         "replacement": "read the GGUF metadata header directly, or declare "
                        "capabilities in the registry. The catalog already has "
                        "a declared-fallback path for llama-server-only models",
         "effort": "small"},
        {"area": "embeddings",
         "where": "the embedder seat (qwen3-embedding:0.6b)",
         "needs_daemon": True,
         "replacement": "llama-server exposes /v1/embeddings; the seat would "
                        "become another owned process",
         "effort": "medium — it is a live data path, and D5 says the embedding "
                   "model must never change without a re-index"},
        {"area": "downloading new models",
         "where": "ollama pull",
         "needs_daemon": True,
         "replacement": "fetch GGUFs from Hugging Face directly",
         "effort": "medium, and it is the one place Ollama genuinely earns its "
                   "keep — curation, resumable downloads and a name people "
                   "recognise"},
    ]
