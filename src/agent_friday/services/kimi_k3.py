"""Kimi K3 via the `kimi-k3-in-c` CLI engine — experimental, off by default.

https://github.com/FareedKhan-dev/kimi-k3-in-c — a 2.78-trillion-parameter
Kimi K3 running CPU-only inference from a C99 engine, streaming weights from
disk so peak RSS stays near 8 GB.

What this is, stated plainly, because every one of these shapes how it may be
used and none of them is the usual case:

  * **There is no server.** The README is explicit: "Version one is text-only:
    there are no tools, images, server, or context compaction." No HTTP
    endpoint, no OpenAI-compatible API, no daemon. The only way in is to run
    `bin/k3` and read its stdout, which is what this module does.
  * **There is no tool calling.** Same sentence. So this engine can never serve
    a seat that dispatches tools, which is most of Friday's work.
  * **It IS chat-capable**, contrary to a first reading. `--chat` uses "the
    official XTML segments and tokenizer control tokens" and "does not fall
    back to ChatML or a handwritten generic prompt"; the README says "The
    official Kimi K3 checkpoint is also chat-capable". Raw continuation is what
    you get WITHOUT `--chat`, not the only thing on offer.
  * **The engine is Apache-2.0. The weights are not covered by it.** "Kimi K3
    is created and released by Moonshot AI under its own license. This
    repository contains no model weights and grants no rights to them."
    Whether those terms permit a given use is a separate question that has to
    be answered against Moonshot AI's licence before any weights are fetched.
  * **The gate is storage:** ~1.7 TB free, and a slow disk is slow at every
    step because the first three presets re-read the model from disk per token.

Nothing here is wired into the router, the seat pickers, or the residency
arbiter, and the catalog row it produces carries ``roles: []`` and
``curated: False`` so it cannot be chosen as a seat or reached as a fallback.
``complete()`` refuses until a checkpoint is configured AND present on disk.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

_log = logging.getLogger("friday.kimi_k3")

#: The id the catalog and any future picker use. Not a Hugging Face repo id:
#: the weights live at `moonshotai/Kimi-K3`, but what Friday would run is this
#: engine reading them, and conflating the two is how a row ends up claiming a
#: transport that does not exist.
MODEL_ID = "kimi-k3"
ENGINE_URL = "https://github.com/FareedKhan-dev/kimi-k3-in-c"
WEIGHTS_REPO = "moonshotai/Kimi-K3"

#: Every figure below is quoted from the project README (read 2026-09-22), not
#: estimated. `docs/data/` in that repo holds the underlying measurements.
CHECKPOINT_GB = 1560.0        # "the checkpoint is 1.56 TB"
TRUNK_GB = 109.0              # "+ 109 GB packed trunk"
DISK_REQUIRED_GB = 1700.0     # README: "~1.7 TB free"

#: preset -> (peak RSS GB, README's note). Peak RSS is what the machine must
#: actually hold; the checkpoint stays on disk and is streamed.
PRESETS = {
    "laptop":      (8.2,   "the ordinary-path floor"),
    "desktop":     (31.9,  None),
    "workstation": (95.5,  "the expert cache starts to matter here"),
    "server":      (128.0, "90 of 93 trunk layers pinned; fastest"),
    "max":         (224.0, "not faster than `server` in the published runs"),
}
DEFAULT_PRESET = "laptop"

#: Observed seconds PER TOKEN, by available memory. This is the number that
#: decides whether the thing is usable for a given job, so it is stated in the
#: unit the README uses rather than converted into a friendlier-looking rate.
SECONDS_PER_TOKEN = {
    "8 GB": 26.5,
    "128 GB+": 5.6,
}

#: AVX2 + FMA required; AVX-512 explicitly unnecessary.
CPU_REQUIREMENT = "x86-64 with AVX2 and FMA"

#: Windows is a supported build target, but only through MSYS2's MinGW-w64 GCC
#: — the engine uses O_DIRECT, posix_memalign and getrusage, ported in
#: `src/io/k3_portable_io.h`. There is no MSVC build.
BUILD_NOTE = "Windows: build under MSYS2 MinGW-w64 GCC (no MSVC build)"

CAPABILITIES = {
    "chat": True,           # via --chat, official Kimi K3 XTML control tokens
    "raw_completion": True,  # the default mode, without --chat
    "tools": False,         # "there are no tools"
    "vision": False,        # "text-only"
    "server": False,        # "there are no ... server"
    "streaming": False,     # no token stream out of a one-shot CLI run
}

_SETTINGS_KEY = "kimi_k3"


def _popen_flags() -> int:
    """CREATE_NO_WINDOW on Windows. Friday's server runs under pythonw, where
    a child console is a console window flashing on the user's desktop."""
    try:
        from agent_friday.core import _POPEN_FLAGS
        return _POPEN_FLAGS
    except Exception:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def config() -> dict:
    """The `kimi_k3` settings block, with defaults filled in.

    Read through core's settings loader so it honours the same whitelist as
    everything else -- a top-level key absent from DEFAULT_SETTINGS is dropped
    on every read, silently.
    """
    out = {"enabled": False, "binary": "", "model_dir": "", "trunk_dir": "",
           "tokenizer_dir": "", "preset": DEFAULT_PRESET}
    try:
        from agent_friday.core import _load_settings
        block = (_load_settings() or {}).get(_SETTINGS_KEY) or {}
        for k in out:
            if block.get(k) not in (None, ""):
                out[k] = block[k]
    except Exception:
        pass
    return out


def status() -> dict:
    """Why this engine can or cannot run right now, in a sentence a person can
    act on. Never raises, never probes the network, never touches the weights.

    `reason` is None exactly when `available` is True — the same contract
    `services/tiers.Resolution` uses, so a caller can pass the reason straight
    through instead of inventing one.
    """
    cfg = config()
    binary, model_dir = cfg["binary"], cfg["model_dir"]
    reason = None

    if not cfg["enabled"]:
        reason = ("Kimi K3 is off. It is experimental, needs about 1.7 TB of "
                  "free disk for its checkpoint, and cannot call tools.")
    elif not binary:
        reason = ("No path to the `k3` binary. Build it from "
                  + ENGINE_URL + " (" + BUILD_NOTE + ").")
    elif not _exists(binary):
        reason = "The configured `k3` binary is not at %s." % binary
    elif not model_dir:
        reason = ("No checkpoint directory configured. The checkpoint is "
                  "1.56 TB plus a 109 GB trunk and is not included.")
    elif not _exists(model_dir):
        reason = "The configured checkpoint directory is not at %s." % model_dir

    return {
        "model_id": MODEL_ID,
        "engine_url": ENGINE_URL,
        "weights_repo": WEIGHTS_REPO,
        "enabled": bool(cfg["enabled"]),
        "configured": bool(binary and model_dir),
        "installed": bool(binary and model_dir
                          and _exists(binary) and _exists(model_dir)),
        "available": reason is None,
        "reason": reason,
        "preset": cfg["preset"],
        "requirements": requirements(),
        "capabilities": dict(CAPABILITIES),
    }


def _exists(path: str) -> bool:
    """Guarded existence check — a configured path on a disconnected drive
    raises rather than returning False on some Windows setups."""
    try:
        return bool(path) and os.path.exists(path)
    except Exception:
        return False


def requirements() -> dict:
    """What it would take to run, quoted rather than estimated."""
    peak, _note = PRESETS.get(config().get("preset") or DEFAULT_PRESET,
                              PRESETS[DEFAULT_PRESET])
    return {
        "disk_gb": DISK_REQUIRED_GB,
        "checkpoint_gb": CHECKPOINT_GB,
        "trunk_gb": TRUNK_GB,
        "ram_gb": peak,
        "ram_gb_min": PRESETS["laptop"][0],
        "seconds_per_token": dict(SECONDS_PER_TOKEN),
        "cpu": CPU_REQUIREMENT,
        "build": BUILD_NOTE,
        "weights_license": ("Moonshot AI's own licence — the engine is "
                            "Apache-2.0 and grants no rights to the weights"),
    }


def available() -> bool:
    return bool(status()["available"])


class KimiK3Unavailable(RuntimeError):
    """Raised by `complete()` when the engine cannot run. Carries the same
    human sentence `status()['reason']` does."""


def complete(prompt: str, *, max_tokens: int = 256, preset: str | None = None,
             chat: bool = False, system: str | None = None,
             timeout_s: float = 3600.0) -> str:
    """One-shot completion by running the CLI once. Returns its stdout text.

    Deliberately not a streaming interface: there is no server to stream from,
    and at 5.6-26.5 SECONDS per token a 256-token answer is 24 minutes to two
    hours. `timeout_s` defaults to an hour for that reason and is still likely
    to be too short for a long generation — the caller has to choose knowing
    the rate, which is why `requirements()` reports it.
    """
    st = status()
    if not st["available"]:
        raise KimiK3Unavailable(st["reason"] or "Kimi K3 is not available.")

    cfg = config()
    chosen = preset or cfg["preset"] or DEFAULT_PRESET
    if chosen not in PRESETS:
        chosen = DEFAULT_PRESET

    # THE PROMPT GOES IN A FILE, NOT ON argv.
    #
    # The README is explicit that `--prompt-file` is "preferred for anything
    # non-ASCII: the shell re-encodes argv, whereas a file is read verbatim".
    # An argv round-trip through a Windows code page is exactly how a correct
    # em dash arrives as three characters, so the bytes never go near argv.
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="k3-prompt-", suffix=".txt")
        with os.fdopen(fd, "wb") as fh:
            fh.write((prompt or "").encode("utf-8"))

        argv = [cfg["binary"], cfg["model_dir"],
                "--prompt-file", tmp,
                "--preset", chosen,
                "--gen", str(int(max_tokens))]
        # `--prompt`/`--prompt-file` require `--tok`; default it to the
        # checkpoint directory, which is where the README's own examples point.
        argv += ["--tok", cfg["tokenizer_dir"] or cfg["model_dir"]]
        if cfg["trunk_dir"]:
            argv += ["--trunk", cfg["trunk_dir"]]
        if chat:
            argv.append("--chat")
            if system:
                argv += ["--system", system]

        _log.info("kimi-k3: running %s preset=%s gen=%d",
                  os.path.basename(cfg["binary"]), chosen, max_tokens)
        # BYTES, not text=True. On Windows `text=True` decodes with the locale
        # code page rather than UTF-8, which corrupts every multi-byte
        # character in the output.
        proc = subprocess.run(argv, capture_output=True, timeout=timeout_s,
                              creationflags=_popen_flags())
    except subprocess.TimeoutExpired:
        raise KimiK3Unavailable(
            "Kimi K3 produced nothing within %.0fs. At %s-%s seconds per token "
            "that may simply be too short rather than a failure."
            % (timeout_s, SECONDS_PER_TOKEN["128 GB+"], SECONDS_PER_TOKEN["8 GB"]))
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise KimiK3Unavailable(
            "Kimi K3 exited %d: %s" % (proc.returncode, err[:400] or "no output"))
    return (proc.stdout or b"").decode("utf-8", "replace")


def catalog_entry() -> dict:
    """The model-catalog row.

    `roles: []` and `curated: False` together are what keep this out of every
    seat picker and role list — `build_catalog` only files an entry under a
    role when the entry is curated AND names that role. It is also absent from
    every provider's `models` list and from the router's fallback chain, so
    nothing can reach it by walking those either. That is deliberate: an engine
    with no tool calling and seconds-per-token latency must never be something
    Friday falls back TO.
    """
    st = status()
    req = st["requirements"]
    hint = st["reason"] or ("Ready · %s preset · ~%s s/token"
                            % (st["preset"], SECONDS_PER_TOKEN["8 GB"]))
    return {
        "id": MODEL_ID,
        "label": "Kimi K3 (experimental, CPU)",
        "short": "Kimi K3",
        "provider": "kimi-k3-cli",
        "provider_label": "Kimi K3 in C (CLI engine)",
        "roles": [],
        "modalities": ["text"],
        "local": True,
        "classification": "local",
        "available": bool(st["available"]),
        "needs_key": None,
        "hint": hint,
        "cost_per_1k": 0.0,
        "curated": False,
        "experimental": True,
        "resident": False,
        "seat": None,
        "source": "experimental-engine",
        "requirements": req,
        "capabilities": st["capabilities"],
        "note": ("2.78T params, CPU-only, streamed from disk. Base and chat "
                 "modes; no tool calling, no server, text only. Needs ~%.0f GB "
                 "free disk. Engine Apache-2.0; weights under Moonshot AI's "
                 "own licence." % req["disk_gb"]),
    }
