"""
Agent Friday — the person's own customisations, kept where updates never reach.

WHY THIS EXISTS
    Local fixes used to live in the install folder (`app\\src\\agent_friday`)
    and in the generated `~/.friday/runtime/models/models.json`. The installer
    deletes and re-copies the install folder on every upgrade, and the model
    registry is rewritten whenever a model is downloaded or re-registered, so a
    working setup (a model served at the context its prompts need, a helper
    moved to the CPU, an HTTPS front door) silently vanished on the next update.

    `~/.friday/overrides/` (paths.overrides_dir) is the supported place instead:

      overrides/models.json   per-model serve overrides, merged OVER the
                              generated registry every time it is read
      overrides/static/       *.css the page loads after its own, once the
                              person turns overrides.static_css on (scripts
                              are not supported, see STATIC FILES below)
      overrides/replaced-*/   copies an update made of installed files that
                              had been changed (packaging/windows/lib/AppFiles.ps1,
                              services/update_guard.py)
      overrides/README.md     written once, on first creation

MERGE ORDER
    shipped defaults (model_shortlist.json's serving numbers, copied into the
    record when the model is registered)  <  the generated runtime record
    (models.json)  <  overrides/models.json.

    The merge happens on READ (`model_store.all_models` / `get`). Nothing ever
    writes an override into the registry, and nothing here ever writes into
    `overrides/models.json`: a regenerated registry is re-merged the next time
    anything asks, so an override survives every regeneration by construction.

WHAT AN OVERRIDE MAY SAY
    An allow-list, not a pass-through. `num_ctx`, `n_gpu_layers`, `placement`
    ("gpu" | "cpu") and `extra_args` drawn from a fixed table of llama-server
    flags, each with a validated value. Anything else is refused, named in
    friday.log with the model and the reason, and the rest of that model's
    override still applies. The server is spawned from an argument list (no
    shell), so the remaining risk is flag injection, and the table closes it:
    no `-m`, `--port`, `--host`, `--alias`, `--api-key`, no paths.

STATIC FILES
    Stylesheets only, and only after the person opts in (overrides.static_css,
    default off): a stylesheet can restyle anything on the page, approval
    cards included, so it is not applied until somebody asks for it.

    Scripts (*.js) are NOT supported, whatever settings.json says. A script in
    the page holds the session token, and the settings block can be changed by
    any POST to /api/settings, so a settings switch is not a consent boundary
    for code that runs with all of Friday's authority. A .js file found in the
    folder is listed in status() and logged as skipped with that reason; it is
    never injected and never served.

TRANSPARENCY
    Every applied override is logged once per change on the `friday.overrides`
    logger with the component, the model and the keys. `status()` reports what
    was applied and what was refused, for Settings and for Friday to say.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

from agent_friday.paths import overrides_dir

_log = logging.getLogger("friday.overrides")

README_NAME = "README.md"
MODELS_NAME = "models.json"
STATIC_DIR = "static"

#: The settings block (`settings.json` -> "overrides"). Layered over these
#: defaults key by key, so a partial block keeps the rest.
DEFAULTS = {
    "enabled": True,        # the whole folder; off = updates still back up, nothing is applied
    "models": True,         # overrides/models.json merged over the registry
    # overrides/static/*.css loaded after the page's own. Opt-in: a stylesheet
    # can restyle anything, approval cards included.
    "static_css": False,
    # There is deliberately no script switch. A "static_js" key in
    # settings.json is ignored (only the keys above are read): a script in the
    # page holds the session token, and the settings block is writable by any
    # /api/settings POST, so no setting may turn scripts on.
}

#: The settings key that turns stylesheets on, for the README and status().
CSS_HOW_TO = ('settings.json -> "overrides": {"static_css": true} '
              '(or POST {"overrides": {"static_css": true}} to /api/settings), '
              'then reload the page')
JS_NOT_SUPPORTED = ("scripts are not supported: a script in the page would hold "
                    "Friday's session token, so .js files here are never loaded")

NUM_CTX_RANGE = (2048, 262144)
NGL_RANGE = (0, 999)
PLACEMENTS = ("gpu", "cpu")
#: llama.cpp treats any -ngl at or above the layer count as "all layers".
ALL_LAYERS = 99

_KV_TYPES = ("f16", "bf16", "f32", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl")


def _int_rule(lo: int, hi: int):
    def check(v):
        if isinstance(v, bool):
            return None
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            return None
        return str(n) if lo <= n <= hi else None
    check.__doc__ = "an integer from %d to %d" % (lo, hi)
    return check


def _choice_rule(*choices):
    def check(v):
        s = str(v).strip().lower() if isinstance(v, (str, int)) else ""
        return s if s in choices else None
    check.__doc__ = "one of " + ", ".join(choices)
    return check


#: flag -> validator (returns the normalised value, or None to refuse). A
#: validator of None marks a flag that takes no value.
EXTRA_ARG_RULES = {
    "-b": _int_rule(32, 16384), "--batch-size": _int_rule(32, 16384),
    "-ub": _int_rule(32, 16384), "--ubatch-size": _int_rule(32, 16384),
    "-np": _int_rule(1, 8), "--parallel": _int_rule(1, 8),
    "-t": _int_rule(1, 256), "--threads": _int_rule(1, 256),
    "-tb": _int_rule(1, 256), "--threads-batch": _int_rule(1, 256),
    "-ctk": _choice_rule(*_KV_TYPES), "--cache-type-k": _choice_rule(*_KV_TYPES),
    "-ctv": _choice_rule(*_KV_TYPES), "--cache-type-v": _choice_rule(*_KV_TYPES),
    "-fa": _choice_rule("on", "off", "auto"),
    "--flash-attn": _choice_rule("on", "off", "auto"),
    "--cache-ram": _int_rule(0, 65536),
    "--ctx-checkpoints": _int_rule(0, 64),
    "--n-cpu-moe": _int_rule(0, 999), "-ncmoe": _int_rule(0, 999),
    "-mg": _int_rule(0, 15), "--main-gpu": _int_rule(0, 15),
    "--mlock": None, "--no-mmap": None, "--no-repack": None,
}

#: Flags with a named key of their own. Refused in `extra_args` with a pointer
#: to the key, so there is one way to say each thing.
_NAMED_ELSEWHERE = {"-c": "num_ctx", "--ctx-size": "num_ctx",
                    "-ngl": "n_gpu_layers", "--n-gpu-layers": "n_gpu_layers",
                    "--gpu-layers": "n_gpu_layers"}

ALLOWED_KEYS = ("num_ctx", "n_gpu_layers", "placement", "extra_args")

_LOCK = threading.Lock()
_CACHE: dict = {"sig": None, "parsed": None}
_LOGGED_APPLIED: set = set()


# ─────────────────────────────────────────────────────────────────────────────
#  Settings and the folder
# ─────────────────────────────────────────────────────────────────────────────

def settings() -> dict:
    """The `overrides` settings block over DEFAULTS. Never raises."""
    out = dict(DEFAULTS)
    try:
        from agent_friday.core import _load_settings
        blk = (_load_settings() or {}).get("overrides")
        if isinstance(blk, dict):
            for k in DEFAULTS:
                if isinstance(blk.get(k), bool):
                    out[k] = blk[k]
    except Exception:
        pass
    return out


README_TEXT = """# Your overrides

This folder belongs to you. Agent Friday reads it and never changes, moves or
deletes anything in it, and an update never touches it. Put customisations
here instead of editing the installed files, which every update replaces.

## models.json: how a local model is served

    {
      "models": {
        "<model id as shown in Settings -> Models>": {
          "num_ctx": 32768,
          "n_gpu_layers": 99,
          "placement": "gpu",
          "extra_args": ["-ub", "512", "--cache-type-k", "q8_0"]
        }
      }
    }

- num_ctx: the context window, 2048 to 262144 tokens.
- n_gpu_layers: how many layers go on the graphics card (0 = none, 99 = all).
- placement: "gpu" or "cpu". "cpu" is the same as n_gpu_layers 0.
- extra_args: only these llama-server flags, with sensible values:
  -b/--batch-size, -ub/--ubatch-size, -np/--parallel, -t/--threads,
  -tb/--threads-batch, -ctk/--cache-type-k, -ctv/--cache-type-v,
  -fa/--flash-attn, --cache-ram, --ctx-checkpoints, --n-cpu-moe/-ncmoe,
  -mg/--main-gpu, --mlock, --no-mmap, --no-repack.

Anything else is refused and the reason is written to friday.log (look for
"overrides"); the rest of that model's entry still applies. Every override
that is applied is logged too. Order: Friday's shipped defaults, then the
generated ~/.friday/runtime/models/models.json, then this file.

Changes apply the next time the model is loaded (restart Friday, or reload the
model from Settings -> Models).

## static/: your own look

Stylesheets (*.css) in static/ are loaded by the page after its own, once
you turn them on. They are off until then, because a stylesheet can change
how anything on the page looks, approval cards included. To turn them on,
set this in ~/.friday/settings.json and reload the page:

    "overrides": {"static_css": true}

Scripts (*.js) are not supported and are never loaded, whatever the
settings say: a script in the page would hold Friday's session token. A .js
file left here is listed as skipped (friday.log, and GET /api/overrides/status).

Only plain file names directly inside static/ are served (no sub-folders,
and nothing that leads outside the folder, such as a link or shortcut).

## replaced-<version>/: what an update replaced

If an installed file was changed (by you or by a helper) and an update
replaces it, the changed copy is kept here first, under its path inside the
install folder, with a list in REPLACED.txt. Nothing is restored
automatically: compare it with the new release and move what you still need
into this folder's supported places.

## Turning it off

settings.json -> "overrides": {"enabled": false} stops Friday applying
anything here. Your files stay where they are.
"""


def ensure_dir() -> Optional[Path]:
    """Create the folder (and its README, first time only). Never raises.

    The README is written only when it does not exist: once the person has the
    folder it is theirs, including that file.
    """
    try:
        root = overrides_dir()
        created = not root.exists()
        (root / STATIC_DIR).mkdir(parents=True, exist_ok=True)
        readme = root / README_NAME
        stub = False
        try:
            # The installer leaves a three-line stub when it backs files up
            # before Friday has ever made this folder (lib/AppFiles.ps1); that
            # one, and only that one, is replaced by the full guide.
            stub = "Friday writes the full guide here" in readme.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            pass
        if not readme.exists() or stub:
            readme.write_text(README_TEXT, encoding="utf-8")
        if created:
            _log.info("overrides: created %s (README written)", root)
        return root
    except Exception as e:                                   # noqa: BLE001
        _log.warning("overrides: could not create the overrides folder (%s: %s)",
                     type(e).__name__, e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  overrides/models.json
# ─────────────────────────────────────────────────────────────────────────────

def models_path() -> Path:
    return overrides_dir() / MODELS_NAME


def _validate_extra_args(raw) -> tuple[list, list]:
    """(accepted argv, [reasons refused])."""
    if not isinstance(raw, (list, tuple)):
        return [], ["extra_args must be a list of strings"]
    toks = [str(t).strip() for t in raw if str(t).strip()]
    out, why = [], []
    i = 0
    while i < len(toks):
        flag = toks[i]
        if not flag.startswith("-"):
            why.append("extra_args: %r is a value with no flag before it" % flag)
            i += 1
            continue
        if flag in _NAMED_ELSEWHERE:
            why.append("extra_args: %s is set with the %r key instead"
                       % (flag, _NAMED_ELSEWHERE[flag]))
            i += 2 if (i + 1 < len(toks) and not toks[i + 1].startswith("-")) else 1
            continue
        if flag not in EXTRA_ARG_RULES:
            why.append("extra_args: %s is not on the allow-list" % flag)
            i += 2 if (i + 1 < len(toks) and not toks[i + 1].startswith("-")) else 1
            continue
        rule = EXTRA_ARG_RULES[flag]
        if rule is None:
            out.append(flag)
            i += 1
            continue
        value = toks[i + 1] if i + 1 < len(toks) else None
        norm = rule(value) if value is not None else None
        if norm is None:
            why.append("extra_args: %s needs %s (got %r)" % (flag, rule.__doc__, value))
            i += 2 if value is not None else 1
            continue
        out += [flag, norm]
        i += 2
    return out, why


def validate_entry(model_id: str, raw) -> tuple[dict, list]:
    """One model's override -> (normalised, [refusals]). Pure."""
    refused = []
    if not isinstance(raw, dict):
        return {}, [{"model": model_id, "key": None,
                     "why": "the entry must be an object of settings"}]
    norm: dict = {}
    for key, val in raw.items():
        if str(key).startswith("_"):
            continue                       # a comment key
        if key not in ALLOWED_KEYS:
            refused.append({"model": model_id, "key": key,
                            "why": "not an allowed key (allowed: %s)"
                                   % ", ".join(ALLOWED_KEYS)})
            continue
        if key == "num_ctx":
            n = _int_rule(*NUM_CTX_RANGE)(val)
            if n is None:
                refused.append({"model": model_id, "key": key,
                                "why": "must be an integer from %d to %d"
                                       % NUM_CTX_RANGE})
            else:
                norm["num_ctx"] = int(n)
        elif key == "n_gpu_layers":
            n = _int_rule(*NGL_RANGE)(val)
            if n is None:
                refused.append({"model": model_id, "key": key,
                                "why": "must be an integer from %d to %d"
                                       % NGL_RANGE})
            else:
                norm["n_gpu_layers"] = int(n)
        elif key == "placement":
            p = str(val).strip().lower() if isinstance(val, str) else ""
            if p not in PLACEMENTS:
                refused.append({"model": model_id, "key": key,
                                "why": "must be \"gpu\" or \"cpu\""})
            else:
                norm["placement"] = p
        elif key == "extra_args":
            args, why = _validate_extra_args(val)
            for w in why:
                refused.append({"model": model_id, "key": key, "why": w})
            if args:
                norm["extra_args"] = args
    if norm.get("placement") == "cpu" and norm.get("n_gpu_layers", 0) > 0:
        refused.append({"model": model_id, "key": "n_gpu_layers",
                        "why": "placement \"cpu\" means no GPU layers; "
                               "n_gpu_layers %d ignored" % norm["n_gpu_layers"]})
        norm.pop("n_gpu_layers")
    return norm, refused


def parse(data) -> tuple[dict, list]:
    """The whole file's JSON -> ({model_id: normalised}, [refusals]). Pure.

    Accepts `{"models": {...}}` or, as a convenience, the model map itself.
    Keys starting with `_` are comments at either level.
    """
    if not isinstance(data, dict):
        return {}, [{"model": None, "key": None,
                     "why": "the file must hold a JSON object"}]
    models = data.get("models") if isinstance(data.get("models"), dict) else \
        {k: v for k, v in data.items() if not str(k).startswith("_")
         and k != "models"}
    out, refused = {}, []
    for mid, raw in sorted(models.items()):
        if str(mid).startswith("_"):
            continue
        norm, why = validate_entry(str(mid), raw)
        refused += why
        if norm:
            out[str(mid)] = norm
    return out, refused


def load_model_overrides() -> dict:
    """`{"models": {...}, "rejected": [...], "path": str, "error": str|None}`.

    Re-read only when the file changes (mtime and size), and refusals are
    logged once per change, so a registry read on every routing decision does
    not flood friday.log.
    """
    p = models_path()
    try:
        st = p.stat()
        sig = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        sig = (str(p), None, None)
    with _LOCK:
        if _CACHE["sig"] == sig and _CACHE["parsed"] is not None:
            return _CACHE["parsed"]
    parsed = {"models": {}, "rejected": [], "path": str(p), "error": None}
    if sig[1] is not None:
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            parsed["models"], parsed["rejected"] = parse(data)
        except Exception as e:                               # noqa: BLE001
            parsed["error"] = "%s: %s" % (type(e).__name__, e)
            _log.warning("overrides: %s could not be read, so no model override "
                         "applies (%s)", p, parsed["error"])
        for r in parsed["rejected"]:
            _log.warning("overrides: refused component=model_store model=%s key=%s: %s",
                         r.get("model"), r.get("key"), r.get("why"))
    with _LOCK:
        _CACHE["sig"] = sig
        _CACHE["parsed"] = parsed
        _LOGGED_APPLIED.clear()
    return parsed


def reset_cache() -> None:
    with _LOCK:
        _CACHE["sig"] = None
        _CACHE["parsed"] = None
        _LOGGED_APPLIED.clear()


def _set_flag(args: list, flag: str, value: Optional[str]) -> list:
    """Replace `flag`'s value in an argv list, or append it. Aliases of the
    same flag are removed so llama.cpp never sees two answers."""
    aliases = {flag}
    for group in (("-ngl", "--n-gpu-layers", "--gpu-layers"),
                  ("-b", "--batch-size"), ("-ub", "--ubatch-size"),
                  ("-np", "--parallel"), ("-t", "--threads"),
                  ("-tb", "--threads-batch"), ("-ctk", "--cache-type-k"),
                  ("-ctv", "--cache-type-v"), ("-fa", "--flash-attn"),
                  ("--n-cpu-moe", "-ncmoe"), ("-mg", "--main-gpu")):
        if flag in group:
            aliases = set(group)
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a in aliases:
            has_value = i + 1 < len(args) and not str(args[i + 1]).startswith("-")
            i += 2 if has_value else 1
            continue
        out.append(a)
        i += 1
    out.append(flag)
    if value is not None:
        out.append(value)
    return out


def apply_to_record(model_id: str, rec: dict, override: Optional[dict] = None,
                    *, component: str = "model_store") -> dict:
    """The registry record with this model's override merged over it.

    Returns a NEW dict; `rec` is never mutated, so the generated registry and
    the merged view can never be confused. `user_override` on the result names
    exactly what was applied, for the planner (an override outranks its plan)
    and for anyone asking why a seat looks the way it does.
    """
    if override is None:
        if not settings_allow_models():
            return rec
        override = load_model_overrides()["models"].get(model_id)
    if not override or not isinstance(rec, dict):
        return rec
    out = dict(rec)
    args = [str(a) for a in (rec.get("serve_args") or [])]
    if "num_ctx" in override:
        out["serve_num_ctx"] = int(override["num_ctx"])
    ngl = override.get("n_gpu_layers")
    if ngl is None and override.get("placement") == "cpu":
        ngl = 0
    elif ngl is None and override.get("placement") == "gpu":
        ngl = ALL_LAYERS
    if ngl is not None:
        args = _set_flag(args, "-ngl", str(int(ngl)))
    extra = list(override.get("extra_args") or [])
    i = 0
    while i < len(extra):
        flag = extra[i]
        if EXTRA_ARG_RULES.get(flag) is None and flag in EXTRA_ARG_RULES:
            args = _set_flag(args, flag, None)
            i += 1
        else:
            args = _set_flag(args, flag, extra[i + 1])
            i += 2
    if args:
        out["serve_args"] = args
    if override.get("placement"):
        out["placement"] = override["placement"]
    keys = sorted(override)
    out["user_override"] = dict(override, keys=keys, source=str(models_path()))
    sig = (component, model_id, tuple(keys), json.dumps(override, sort_keys=True))
    with _LOCK:
        fresh = sig not in _LOGGED_APPLIED
        _LOGGED_APPLIED.add(sig)
    if fresh:
        _log.info("overrides: applied component=%s model=%s keys=%s (from %s)",
                  component, model_id, ",".join(keys), models_path())
    return out


def settings_allow_models() -> bool:
    s = settings()
    return bool(s.get("enabled") and s.get("models"))


def apply_to_registry(models: dict) -> dict:
    """`model_store.all_models()`'s view: every record with its override.

    An override naming a model the registry does not hold is reported once
    (status() lists it) and otherwise ignored; it starts applying the moment
    the model is registered.
    """
    if not isinstance(models, dict) or not settings_allow_models():
        return models
    ov = load_model_overrides()["models"]
    if not ov:
        return models
    return {mid: apply_to_record(mid, rec, ov.get(mid)) if mid in ov else rec
            for mid, rec in models.items()}


# ─────────────────────────────────────────────────────────────────────────────
#  overrides/static/
# ─────────────────────────────────────────────────────────────────────────────

_STATIC_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.css$")


def _inside(path: Path, folder: Path) -> bool:
    """True when `path`, with every link and `..` resolved, is a file inside
    `folder` (also resolved). A symlink, junction or shortcut-like reparse
    point that leads out of the folder fails this."""
    try:
        real = path.resolve(strict=True)
        base = folder.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return real.parent == base and real.is_file()


def static_files() -> dict:
    """`{"css": [names], "skipped": [{name, why}], "how_to_enable": str}`,
    names in order.

    Plain .css names directly inside overrides/static, after the person has
    turned `static_css` on. Scripts are never loaded (JS_NOT_SUPPORTED). Every
    file not loaded is listed in `skipped` with the reason (and logged once
    per change), so "my user.css does nothing" has an answer.
    """
    out = {"css": [], "skipped": [], "how_to_enable": CSS_HOW_TO}
    s = settings()
    if not s.get("enabled"):
        return out
    try:
        d = overrides_dir() / STATIC_DIR
        entries = sorted(d.iterdir(), key=lambda p: p.name)
    except Exception:
        return out
    for p in entries:
        n = p.name
        if n.lower().endswith(".js"):
            out["skipped"].append({"name": n, "why": JS_NOT_SUPPORTED})
            continue
        if p.is_dir() and not p.is_symlink():
            continue                       # a sub-folder: never served
        if not _STATIC_NAME.match(n):
            if p.is_file() or p.is_symlink():
                out["skipped"].append({"name": n, "why": "only plain .css names are loaded"})
            continue
        if not _inside(p, d):
            out["skipped"].append({"name": n, "why": "it is a link that leads outside "
                                   "overrides/static, and only files inside it are served"})
            continue
        if not s.get("static_css"):
            out["skipped"].append({"name": n, "why": "overrides.static_css is off (to turn "
                                   "stylesheets on: %s)" % CSS_HOW_TO})
            continue
        out["css"].append(n)
    sig = ("static", tuple(out["css"]), tuple(x["name"] for x in out["skipped"]))
    with _LOCK:
        fresh = sig not in _LOGGED_APPLIED
        _LOGGED_APPLIED.add(sig)
    if fresh and (out["css"] or out["skipped"]):
        _log.info("overrides: component=ui static css=%s skipped=%s",
                  out["css"], ["%s (%s)" % (x["name"], x["why"]) for x in out["skipped"]])
    return out


def static_asset(name: str) -> Optional[tuple]:
    """`(folder, listed_name)` to serve for `/user-overrides/static/<name>`,
    or None.

    Only a name `static_files()` loads right now (so never a script, and
    nothing while stylesheets are off), and only when the resolved file is
    still inside overrides/static: a symlink or junction swapped in after the
    listing is refused here too.

    The requested `name` is only compared against the folder's own listing;
    the path is built from the LISTED entry name, never from the request.
    """
    if not isinstance(name, str) or name.lower().endswith(".js"):
        return None
    loaded = static_files()["css"]
    d = overrides_dir() / STATIC_DIR
    listed = None
    try:
        with os.scandir(d) as it:
            for entry in it:
                if entry.name == name and entry.name in loaded:
                    listed = entry.name
                    break
    except OSError:
        return None
    if listed is None:
        return None
    if not _inside(d / listed, d):
        _log.warning("overrides: component=ui refused to serve %s: it resolves outside %s",
                     listed, d)
        return None
    return d, listed


def static_path(name: str) -> Optional[Path]:
    """The file to serve for `/user-overrides/static/<name>`, or None (see
    `static_asset`; the path comes from the folder listing)."""
    found = static_asset(name)
    if found is None:
        return None
    d, listed = found
    return (d / listed).resolve()


_ENSURED = [False]


def page_tags() -> str:
    """Stylesheet links for the end of the page, after the page's own. Never a
    script tag (JS_NOT_SUPPORTED).

    The first page served also makes sure the folder and its README exist,
    so the supported place is there to find before anyone needs it.
    """
    from html import escape
    if not _ENSURED[0] and settings().get("enabled"):
        _ENSURED[0] = ensure_dir() is not None
    files = static_files()
    tags = ['<link rel="stylesheet" href="/user-overrides/static/%s">' % escape(n)
            for n in files["css"] if not n.lower().endswith(".js")]
    return "".join(tags)


# ─────────────────────────────────────────────────────────────────────────────
#  What Settings and Friday can say
# ─────────────────────────────────────────────────────────────────────────────

def status() -> dict[str, Any]:
    """Everything about the folder, read-only. Never raises."""
    out: dict = {"path": "", "exists": False, "settings": settings()}
    try:
        if out["settings"].get("enabled"):
            ensure_dir()
        root = overrides_dir()
        out["path"] = str(root)
        out["exists"] = root.exists()
        ov = load_model_overrides()
        out["models"] = ov["models"]
        out["rejected"] = ov["rejected"]
        out["models_error"] = ov["error"]
        try:
            from agent_friday.services import model_store as _ms
            have = set((_ms._load().get("models") or {}).keys())
        except Exception:
            have = set()
        out["unknown_models"] = sorted(m for m in ov["models"] if have and m not in have)
        out["static"] = static_files()
        out["static"]["css_enabled"] = bool(out["settings"].get("enabled")
                                            and out["settings"].get("static_css"))
        out["static"]["scripts"] = JS_NOT_SUPPORTED
        out["replaced"] = sorted(p.name for p in root.glob("replaced-*") if p.is_dir()) \
            if root.exists() else []
    except Exception as e:                                   # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out
