"""Centralized filesystem path resolution for Agent Friday (PR-1 of the
OS-mode sequence — see Friday-Linux docs/SPEC.md Section 13).

This module is the single source of truth for the four directories most of
the codebase used to compute inline as `Path.home()` joined with the literal
".friday" segment (or a split form like `Path(friday_dir or <that
expression>)`). Consolidating them here means a later PR can gate the
*default* (not the override) on OS/mode without touching 20+ call sites
again.

Deliberately NOT placed under `agent_friday.core`: `agent_friday/core/__init__.py`
already exists in this repo as a ~2600-line Flask application module with real
import-time side effects — it imports Flask, builds the app, and (as of this
writing) runs a legacy `~/wiki` -> `~/.friday/wiki` migration against the
*real* home directory on import, independent of any FRIDAY_HOME override.
Since Python always executes a package's `__init__.py` before any of its
submodules, `agent_friday.core.paths` would drag every one of this PR's ~22
previously side-effect-free call sites (including `cli.py`, invoked on every
CLI startup) into that ~4s Flask bootstrap and that home-directory touch —
a behavior change and a safety risk this PR (a pure path refactor) must not
introduce. See the PR description for the full writeup; this is the one
deviation from the literal task spec.

FRIDAY_HOME is the only one of the four env vars with pre-existing, exercised
default behavior (see `friday_home()` below), which this module reproduces
byte-for-byte when the env var is unset. The other three functions are
documented individually below, including which pre-existing conventions (if
any) they build on.
"""
from __future__ import annotations

import os
from pathlib import Path


def friday_home() -> Path:
    """Root directory for all Friday state.

    Resolution order:
      1. FRIDAY_HOME environment variable, if set (expanded with `~`/`~user`).
      2. `Path.home() / ".friday"` — the pre-existing, unconditional default
         that every one of this PR's call sites used before. This branch must
         stay byte-identical to that expression; do not change it here without
         also updating every replaced call site.
    """
    env = os.environ.get("FRIDAY_HOME")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".friday"


def models_dir() -> Path:
    """Directory for Friday-managed model assets.

    No FRIDAY_MODELS_DIR convention exists anywhere in the codebase today
    (verified by a repo-wide grep before writing this). Two narrower
    "models" locations already exist for specific subsystems and this
    function intentionally does NOT alias either of them, to avoid
    conflating a general-purpose directory with subsystem-specific ones
    that have their own override precedence:
      - `agent_friday.core.runtime_dir() / "models" / "gguf"` — the D7
        local-inference weight store (llama.cpp GGUF files), itself
        overridable via FRIDAY_RUNTIME_DIR or settings.json.
      - `.friday/cache/models` (services/model_discovery.py) — a cache of
        model *catalog metadata*, unrelated to weight storage.

    Defaults to `friday_home() / "models"`, overridable via
    FRIDAY_MODELS_DIR. Fresh convention — no existing call site needs to
    change to accommodate this default.
    """
    env = os.environ.get("FRIDAY_MODELS_DIR")
    if env:
        return Path(os.path.expanduser(env))
    return friday_home() / "models"


def runtime_dir() -> Path:
    """Local runtime-stack root: GGUF weights, llama.cpp, ComfyUI, voice venvs
    (decision D7).

    Unlike the other three functions, this one is NOT a fresh convention —
    `agent_friday.core.runtime_dir()` already implements it, is already
    imported at module level by roughly a dozen call sites (model_store.py,
    gguf_extract.py, residency_arbiter.py, residency_catalog.py,
    context_budget.py, hardware_profile.py, local_image.py, model_catalog.py),
    and already has real three-tier precedence: FRIDAY_RUNTIME_DIR env var >
    settings.json["runtime_dir"] > `~/.friday/runtime`.

    To keep THIS module import-light (see the module docstring on why
    `agent_friday.core` must not be imported at module load time), this
    function delegates to that canonical implementation lazily, at call
    time, rather than duplicating or overriding its logic. If `agent_friday.
    core` cannot be imported for some reason, it falls back to the same
    env-var-or-default logic minus the settings.json layer (settings.json
    parsing lives in core and isn't worth duplicating for a fallback path).

    KNOWN GAP (pre-existing, not introduced by this PR): when the delegation
    succeeds, the default (`~/.friday/runtime`) is computed from core's own
    `HOME = Path(os.path.expanduser("~"))`, which does NOT read FRIDAY_HOME —
    only this module's other three functions do. So `FRIDAY_HOME=/x
    runtime_dir()` will NOT return a path under `/x` unless FRIDAY_RUNTIME_DIR
    or settings.json is also set. Fixing that means changing
    `agent_friday/core/__init__.py`'s HOME/FRIDAY_DIR computation, which is
    out of scope for a pure path-consolidation PR — flagged here and in the
    PR description for whoever picks up OS-mode gating next.
    """
    try:
        from agent_friday.core import runtime_dir as _core_runtime_dir
        return _core_runtime_dir()
    except Exception:
        env = os.environ.get("FRIDAY_RUNTIME_DIR")
        if env:
            return Path(os.path.expanduser(env))
        return friday_home() / "runtime"


def voice_assets_dir() -> Path:
    """Directory for voice-related assets Friday manages.

    No FRIDAY_VOICE_ASSETS convention exists anywhere in the codebase today
    (verified by a repo-wide grep before writing this). Two independent,
    engine-specific trees already exist and this function deliberately does
    not alias either — picking one would misrepresent the other:
      - `services/local_voice.py`'s `.friday/local_voice/{whisper,piper}` —
        the CPU Whisper+Piper path.
      - `services/nemo_voice.py`'s `.friday/models/nemo` — the NeMo path.

    Defaults to `friday_home() / "voice_assets"`, overridable via
    FRIDAY_VOICE_ASSETS. Fresh convention — no existing call site needs to
    change to accommodate this default.
    """
    env = os.environ.get("FRIDAY_VOICE_ASSETS")
    if env:
        return Path(os.path.expanduser(env))
    return friday_home() / "voice_assets"
