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
import-time side effects — it imports Flask, builds the app, and runs a legacy
`~/wiki` -> `~/.friday/wiki` migration on import. (That migration used to run
against the *real* home directory regardless of FRIDAY_HOME; it is now gated on
`is_redirected()` below. The import weight is unchanged, so the reason for
keeping this module outside `agent_friday.core` still stands.)
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

    FRIDAY_HOME **is** the state directory, not its parent: `FRIDAY_HOME=/x`
    puts `settings.json` at `/x/settings.json`, not `/x/.friday/settings.json`.
    Thirteen service modules used to read it the other way (`Path(FRIDAY_HOME or
    Path.home()) / ".friday"`). Both readings isolate, but holding both at once
    split one Friday across two directories — settings under `$FRIDAY_HOME`,
    soul and goals under `$FRIDAY_HOME/.friday`. They now all route through
    here; `tests/unit/test_friday_home_isolation.py` pins it.

    This is the ONLY place in the codebase permitted to compute Friday's state
    root. Anything that recomputes it independently will drift out of the
    override the moment someone sets FRIDAY_HOME — which is exactly how the gap
    audited in docs/audits/friday-home-isolation-gap-2026-08-31.md arose.
    """
    env = os.environ.get("FRIDAY_HOME")
    if env:
        return Path(os.path.expanduser(env))
    return Path.home() / ".friday"


def user_home() -> Path:
    """The operating system's home directory for the current user.

    Deliberately NOT affected by FRIDAY_HOME. Some paths are genuinely
    *about the human's machine* rather than about Friday's state, and
    redirecting them would break the feature instead of isolating it:

      - `~/Desktop` (where creations are surfaced for the user to find)
      - the sandbox root that bounds which files Friday may read
      - `~/Projects` (the default working directory for code tasks)
      - the legacy `~/wiki` directory the wiki migration reads from

    Callers that want Friday's *state* want `friday_home()`. Callers that
    want the human's home want this. Having both named makes the choice
    explicit at every call site instead of implicit in an `expanduser`.
    """
    return Path(os.path.expanduser("~"))


def is_redirected() -> bool:
    """True when FRIDAY_HOME is set, i.e. Friday's state has been pointed
    somewhere other than the current user's own `~/.friday`.

    This is the signal for "do not touch the host's home directory at all".
    Under a redirect (a test run, an eval harness, a kiosk image, an
    unattended agent) it is not enough for Friday to *store* its state
    elsewhere — anything that mutates the host home as a side effect must
    also stand down. The migration of a legacy `~/wiki` is the sharp case:
    it renames a directory the redirected process was promised it could not
    reach, and there is by definition nothing to migrate for an instance
    whose state lives somewhere else entirely.
    """
    return bool(os.environ.get("FRIDAY_HOME"))


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

    The gap this docstring used to flag is CLOSED. Core's default was
    `HOME / ".friday" / "runtime"` computed from its own
    `Path(os.path.expanduser("~"))`, so `FRIDAY_HOME=/x runtime_dir()` did
    not land under `/x` unless FRIDAY_RUNTIME_DIR or settings.json was also
    set. `agent_friday/core/__init__.py` now derives `FRIDAY_DIR` from
    `friday_home()`, so the delegated default honours FRIDAY_HOME like
    everything else. See tests/unit/test_friday_home_isolation.py.
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
