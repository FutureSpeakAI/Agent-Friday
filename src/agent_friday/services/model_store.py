"""
Agent Friday — her own model store. The source of truth for what she has.

Until now "what models exist" meant `ollama list`. That made a daemon a
prerequisite for Friday knowing her own capabilities: with Ollama stopped or
uninstalled, `residency_catalog.installed_entries` returned an empty list, the
residency plan had no seats to fill, and a machine holding 23 GB of perfectly
good weights reported that it had no local models.

This module owns that question instead. It keeps a registry at
`~/.friday/runtime/models/models.json` describing files Friday holds, and every
fact in it is READ FROM THE GGUF rather than inferred from a name or a
directory listing:

  * architecture, parameter count, quantization, context window — from the
    file's own key/value header
  * whether it can generate, or only embed — from the pooling type and the
    presence of an output layer, not from whether "embed" appears in the name
  * whether it has a chat template, and whether that template mentions tools

Names are the thing most worth not trusting. `qwen3-embedding:0.6b` is an
embedding model and says so in its name, which works right up until someone
ships `nomic-embed-text-v2-moe` (generates) or a chat model with "embed" in the
repo path. The header knows; the string does not.

Ollama is demoted here from source of truth to **import source** — one of
several ways a file can arrive, alongside a direct download. Nothing in this
module requires the daemon to be running, and `available()` reports what is
usable without it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path, PureWindowsPath

from agent_friday.core import runtime_dir
from agent_friday.services import path_probe

REGISTRY_VERSION = 1


def present(path) -> bool:
    """`Path(path).exists()` that cannot stall on a wedged network share.

    Every existence check in this module goes through here. See
    `path_probe` for why: a UNC path into a hung `\\\\wsl.localhost` share
    held `available()` for the SMB timeout, and `available()` sits under the
    chat path, the residency plan and `/api/intelligence`.
    """
    return path_probe.exists(path)

SOURCE_OLLAMA = "ollama-import"
SOURCE_DOWNLOAD = "download"
SOURCE_LOCAL = "local-file"


def store_dir() -> Path:
    return runtime_dir() / "models" / "gguf"


def registry_path() -> Path:
    return runtime_dir() / "models" / "models.json"


# ─────────────────────────────────────────────────────────────────────────────
#  Reading the file, not the name
# ─────────────────────────────────────────────────────────────────────────────

# Keys worth pulling out of a GGUF header. Architecture-scoped ones are
# resolved after `general.architecture` is known, since the prefix varies.
_WANTED = (
    "general.architecture", "general.name", "general.parameter_count",
    "general.size_label", "general.file_type", "general.quantization_version",
    "tokenizer.chat_template",
)

_FILE_TYPE_NAMES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0",
    9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
    14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
    30: "BF16",
}


def _params_from_label(label) -> float | None:
    """`12B` -> 12.0, `26B-A4B` -> 26.0, `0.6B` -> 0.6."""
    if not label:
        return None
    m = re.match(r"\s*([\d.]+)\s*([BMK])", str(label), re.I)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2).upper()
    return round({"B": val, "M": val / 1000.0, "K": val / 1e6}[unit], 3)


def describe(path) -> dict:
    """Everything the store knows about one GGUF, read from the file itself."""
    from agent_friday.services.gguf_extract import gguf_metadata

    path = Path(path)
    arch_probe = gguf_metadata(path, keys=_WANTED)
    arch = arch_probe.get("general.architecture")
    # A second pass now that the prefix is known. Cheap: the header is small
    # and sits at the front of the file.
    scoped = gguf_metadata(path, keys=(
        "%s.context_length" % arch, "%s.embedding_length" % arch,
        "%s.block_count" % arch, "%s.pooling_type" % arch,
    )) if arch else {}

    tmpl = arch_probe.get("tokenizer.chat_template") or ""
    ftype = arch_probe.get("general.file_type")

    # Size, from whichever of the two keys the publisher filled in. Neither is
    # reliably present: gemma4:12b and 26b declare only `general.size_label`
    # ("12B", "26B-A4B"), while e2b and e4b declare only
    # `general.parameter_count`. Reading one key left half the catalogue at
    # None, everything sorted as zero, and the residency plan seated the 2B
    # model as the interactive brain and the 4B as the heavy hitter.
    #
    # Note what the e-series parameter counts mean: 5.12B for e2b and 8.0B for
    # e4b are the FULL nested weights of a MatFormer, not the effective size
    # the name refers to. They still rank correctly against each other and
    # against the dense models, which is all the planner asks of them.
    params = arch_probe.get("general.parameter_count")
    params_b = round(params / 1e9, 2) if params else None
    if params_b is None:
        params_b = _params_from_label(arch_probe.get("general.size_label"))

    # Embedding-only is a STRUCTURAL fact: the model declares a POOLING TYPE.
    # Nothing else is needed and nothing else is reliable.
    #
    # Deciding it from the name puts anything with "embed" in its path in the
    # same bucket. Requiring the ABSENCE of a chat template — which this code
    # did first — is also wrong, and measurably so: Qwen3-Embedding-0.6B
    # declares `qwen3.pooling_type = 3` and still carries a chat template
    # inherited from Qwen3-0.6B-Base. It was classified as a chat model that
    # could take a seat and answer questions. It cannot; it has no output head.
    has_pooling = ("%s.pooling_type" % arch) in scoped if arch else False
    is_embedding = bool(has_pooling)

    return {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "architecture": arch,
        "name": arch_probe.get("general.name"),
        "params_total_b": params_b,
        "size_label": arch_probe.get("general.size_label"),
        "quantization": _FILE_TYPE_NAMES.get(ftype, ftype),
        "context_window": scoped.get("%s.context_length" % arch) if arch
        else None,
        "n_tensors": arch_probe.get("__n_tensors"),
        "has_chat_template": bool(tmpl),
        # A template that never mentions tools cannot express a tool call, and
        # a seat given one will look like a model that cannot call tools.
        "template_supports_tools": bool(tmpl) and "tool" in tmpl.lower(),
        "is_embedding": is_embedding,
        "can_generate": not is_embedding,
    }


def sha256_of(path, chunk=8 << 20, progress=None) -> str:
    h = hashlib.sha256()
    total = Path(path).stat().st_size
    done = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
            done += len(b)
            if progress:
                progress(done, total)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
#  The registry
# ─────────────────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        d = json.loads(registry_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def all_models() -> dict:
    """model_id -> entry, for every model in the registry (present or not)."""
    return (_load().get("models") or {})


def _usable(rec: dict) -> bool:
    """On disk (or reachable), and not retired."""
    if not isinstance(rec, dict) or rec.get("retired"):
        return False
    files = seat_files(rec)
    return bool(files.get("gguf"))


def available() -> dict:
    """Only models whose file is actually on disk and which are not retired.

    The distinction matters: a registry entry whose file was deleted must not
    make the planner believe it has a seat to fill, and it must not be silently
    forgotten either — `missing()` names it so the reason a seat vanished is
    answerable.

    A record that carries a `lora` whose file cannot be found is also not
    available: serving the base under the fine-tune's name is the silent
    wrong-model failure `Friday-Models/docs/DECISIONS.md` refuses, and the
    planner must not be handed that seat.

    A `retired` record is never available, whatever is on disk. That is how
    `gemma4:e2b-friday-v1` (a possibly no-op merge) is kept out
    of `local_seats.resolve()`'s smallest-candidate fallback while its file
    stays where it is.
    """
    out = {}
    for k, v in all_models().items():
        if _usable(v):
            files = seat_files(v)
            if v.get("lora") and not files.get("lora"):
                continue
            out[k] = v
    return out


def missing() -> dict:
    """Every record `available()` declines, with the reason in `why`."""
    out = {}
    for k, v in all_models().items():
        if not isinstance(v, dict):
            continue
        if v.get("retired"):
            out[k] = dict(v, why="retired: %s" % v["retired"])
            continue
        files = seat_files(v)
        if not files.get("gguf"):
            st = path_probe.probe_state(v.get("path"))
            why = ("weights not reachable: %s" % (st.get("reason") or "absent"))
            out[k] = dict(v, why=why)
        elif v.get("lora") and not files.get("lora"):
            out[k] = dict(v, why="adapter file not reachable: %s" % v["lora"])
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  A seat is up to three files, and each may live in two places
# ─────────────────────────────────────────────────────────────────────────────

#: Record keys that name a file, and the seat-file name each maps to.
_FILE_KEYS = (("path", "gguf"), ("lora", "lora"), ("mmproj", "mmproj"))


def _candidates(rec: dict, key: str) -> list:
    """Where to look for one of a record's files, best first.

    1. an explicit local copy named in `rec["local_files"][key]`;
    2. a file of the same name under `store_dir()` (`~/.friday/runtime/
       models/gguf/`), which is where the weights are meant to live and the
       only path `residency_arbiter._llama_server_pids()` recognises as
       Friday's own;
    3. the recorded path itself, which today is the WSL share.

    The moment a copy lands under `store_dir()` it is preferred; nothing has
    to be re-registered. The share is only consulted when no local copy
    exists, and then only through the guarded probe.
    """
    recorded = rec.get(key)
    out = []
    local = (rec.get("local_files") or {}).get(key)
    if local:
        out.append(str(local))
    if recorded:
        try:
            # The file NAME, split on either separator: the recorded path is
            # often a Windows share, and POSIX pathlib would take the whole
            # backslashed string as one name.
            out.append(str(store_dir() / PureWindowsPath(str(recorded)).name))
        except Exception:
            pass
        out.append(str(recorded))
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def seat_files(rec: dict) -> dict:
    """`{"gguf": path|None, "lora": path|None, "mmproj": path|None}` for a
    record, each resolved to the first candidate that is present.

    `None` for a file means "not reachable right now", never "the record
    did not name one"; `rec.get("lora")` says whether one was named.
    """
    out = {"gguf": None, "lora": None, "mmproj": None}
    if not isinstance(rec, dict):
        return out
    for key, name in _FILE_KEYS:
        for cand in _candidates(rec, key):
            if present(cand):
                out[name] = cand
                break
    return out


def seat_files_for(model_id: str) -> dict:
    return seat_files(get(model_id) or {})


def retire(model_id: str, reason: str) -> dict:
    """Keep the record, keep the file, take the model out of every picker
    and every fallback. Reversible by clearing `retired`."""
    data = _load()
    rec = (data.get("models") or {}).get(model_id)
    if rec is None:
        return {"ok": False, "error": "not in the store: %s" % model_id}
    rec["retired"] = str(reason)
    rec["retired_at"] = time.time()
    _save(data)
    return {"ok": True, "entry": rec}


def get(model_id: str) -> dict | None:
    return all_models().get(model_id)


def register(model_id: str, path, *, source: str = SOURCE_LOCAL,
             mmproj=None, lora=None, chat_template=None,
             sha256: str | None = None, origin: dict | None = None,
             verify: bool = False, label: str | None = None,
             local_files: dict | None = None) -> dict:
    """Record a model Friday holds, with its facts read from the file.

    A seat is up to three files: the weights (`path`), an adapter (`lora`)
    that `llama-server` applies at serve time with `--lora`, and a projector
    (`mmproj`) for vision and audio. Recording all three on one entry is what
    lets the Arbiter own a fine-tuned seat instead of spawning the base under
    the fine-tune's name. `local_files` names where each of those lives on
    local disk when the recorded path is a network share; see `seat_files`.

    `verify=True` hashes the file, which on a 9 GB artifact is not free — so it
    is opt-in for imports of files we just copied ourselves, and on by default
    for anything that arrived over a network.
    """
    path = Path(path)
    if not present(path):
        raise FileNotFoundError("no such file: %s" % path)
    if lora and not present(lora):
        raise FileNotFoundError("no such adapter: %s" % lora)
    facts = describe(path)
    # A borrowed template counts. gemma4:e2b and e4b ship with none, so the
    # file alone says "no chat template, no tool support" — which was true of
    # the weights and false of the seat, since gguf_extract borrows the 12b's.
    # Recording the file's answer here would have the store report that two
    # working tool-calling seats cannot call tools.
    if chat_template and present(chat_template):
        try:
            tmpl_text = Path(chat_template).read_text(encoding="utf-8")
            facts["has_chat_template"] = True
            facts["template_supports_tools"] = "tool" in tmpl_text.lower()
            facts["template_source"] = ("embedded"
                                        if facts.get("has_chat_template")
                                        and describe(path).get(
                                            "has_chat_template")
                                        else "borrowed")
        except Exception:
            pass
    entry = dict(facts)
    entry.update({
        "model_id": model_id,
        "source": source,
        "origin": origin or {},
        "mmproj": str(mmproj) if mmproj else None,
        "lora": str(lora) if lora else None,
        "chat_template": str(chat_template) if chat_template else None,
        "added_at": time.time(),
    })
    if local_files:
        entry["local_files"] = {str(k): str(v) for k, v in local_files.items()
                                if v}
    # A human-assigned display name (e.g. "FutureSpeakAI-FridayWeaver-SM-1.0")
    # that the picker shows verbatim instead of humanizing model_id — the
    # humanizer turns hyphens into spaces and title-cases only the first
    # letter, which mangles a name someone chose on purpose.
    if label:
        entry["label"] = str(label)
    if sha256:
        entry["sha256"] = sha256
    elif verify:
        entry["sha256"] = sha256_of(path)

    data = _load()
    data.setdefault("version", REGISTRY_VERSION)
    data.setdefault("models", {})[model_id] = entry
    _save(data)
    return entry


def forget(model_id: str, *, delete_file: bool = False) -> dict:
    """Remove a model from the registry, optionally deleting its weights.

    Deleting is opt-in and separate, because "Friday should stop offering this"
    and "erase nine gigabytes" are different intentions and conflating them is
    how people lose files.
    """
    data = _load()
    entry = (data.get("models") or {}).pop(model_id, None)
    if entry is None:
        return {"ok": False, "error": "not in the store: %s" % model_id}
    _save(data)
    removed = []
    if delete_file:
        for key in ("path", "mmproj", "lora", "chat_template"):
            p = entry.get(key)
            if p and present(p):
                try:
                    Path(p).unlink()
                    removed.append(p)
                except Exception:
                    pass
    return {"ok": True, "entry": entry, "deleted": removed}


def verify(model_id: str) -> dict:
    """Is the file still there, still the right size, still the right hash?"""
    e = get(model_id)
    if not e:
        return {"ok": False, "error": "not in the store"}
    p = Path(seat_files(e).get("gguf") or e.get("path") or "")
    if not present(p):
        return {"ok": False, "error": "file is gone: %s" % p, "missing": True}
    size = p.stat().st_size
    if e.get("size_bytes") and size != e["size_bytes"]:
        return {"ok": False, "error": "size changed: %d -> %d"
                % (e["size_bytes"], size)}
    if e.get("sha256"):
        actual = sha256_of(p)
        if actual != e["sha256"]:
            return {"ok": False, "error": "checksum mismatch",
                    "expected": e["sha256"], "actual": actual}
        return {"ok": True, "checked": "sha256"}
    return {"ok": True, "checked": "size only — no hash recorded"}


# ─────────────────────────────────────────────────────────────────────────────
#  Import from Ollama — one source among several, no longer THE source
# ─────────────────────────────────────────────────────────────────────────────

def import_from_ollama(model_id: str, *, progress=None) -> dict:
    """Copy a model out of Ollama's blob store into Friday's own.

    Requires Ollama's FILES, not its daemon: this reads manifests and blobs off
    disk. It keeps working with the service stopped, and keeps working long
    enough after an uninstall for anyone who kept `~/.ollama`.
    """
    from agent_friday.services import gguf_extract as gx

    res = gx.extract(model_id, progress=progress)
    if not res.get("ok"):
        return res
    tmpl = gx.ensure_chat_template(model_id, family_source=None)
    entry = register(
        model_id, res["path"], source=SOURCE_OLLAMA,
        mmproj=res.get("projector"),
        chat_template=tmpl.get("path") if tmpl.get("ok") else None,
        origin={"ollama_model": model_id,
                "params": res.get("params") or {}},
    )
    return {"ok": True, "entry": entry, "seconds": res.get("seconds"),
            "reused": res.get("reused")}


def import_all_from_ollama(model_ids=None, *, progress=None) -> list:
    if model_ids is None:
        from agent_friday.services import gguf_extract as gx
        root = gx.ollama_root() / "manifests" / "registry.ollama.ai"
        model_ids = []
        for tag in root.rglob("*"):
            if tag.is_file():
                model_ids.append("%s:%s" % (tag.parent.name, tag.name))
    return [import_from_ollama(m, progress=progress) for m in model_ids]


# ─────────────────────────────────────────────────────────────────────────────
#  What the rest of Friday asks
# ─────────────────────────────────────────────────────────────────────────────

def gguf_paths() -> dict:
    """model_id -> weights path, for callers that only want the weights.
    Present files only, local copy preferred."""
    return {k: seat_files(v)["gguf"] for k, v in available().items()}


def seat_file_map() -> dict:
    """model_id -> {"gguf", "lora", "mmproj"} for every available model.

    This is what the Arbiter loads seats from. A model whose record names a
    `lora` only appears here when the adapter is reachable, so the Arbiter
    can never spawn a fine-tune's base under the fine-tune's name.
    """
    return {k: seat_files(v) for k, v in available().items()}


def chat_template_for(model_id: str) -> str | None:
    e = get(model_id) or {}
    p = e.get("chat_template")
    return p if p and present(p) else None


def summary() -> dict:
    avail, gone = available(), missing()
    return {
        "store_dir": str(store_dir()),
        "registry": str(registry_path()),
        "count": len(avail),
        "missing": sorted(gone),
        "total_bytes": sum(v.get("size_bytes") or 0 for v in avail.values()),
        "models": {k: {"params_b": v.get("params_total_b"),
                       "quant": v.get("quantization"),
                       "context": v.get("context_window"),
                       "embedding": v.get("is_embedding"),
                       "tools": v.get("template_supports_tools"),
                       "source": v.get("source")}
                   for k, v in sorted(avail.items())},
    }
