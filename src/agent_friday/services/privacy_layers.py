"""
Privacy-layer attestation — what protection is ACTUALLY running, right now.

Why this module exists
──────────────────────
``sensitivity_classifier`` advertises four layers. Two of them are optional
imports wrapped in bare ``except Exception: _X = None`` with no logging, so a
missing dependency produced a silent downgrade: the docstring kept promising
four layers while the process ran two. In the PyInstaller build that gap was
total — ``sentence_transformers`` sits in the spec's ``excludes`` list and
``presidio-analyzer`` was never in ``requirements.txt`` at all, so the shipped
.exe ran four regexes and two keyword lists while describing itself as a
four-layer defence.

A privacy product that overstates its protection is worse than one that never
claimed it, because the claim is what people rely on. So: this module is the
single place that answers "which layers are live?" and it answers by probing,
never by repeating the docstring.

Design rules
────────────
* PROBE, DON'T IMPORT.  ``importlib.util.find_spec`` costs microseconds.
  Actually importing ``sentence_transformers`` costs ~22 s of cold model load
  (measured), which must never happen on the startup path.
* NEVER FAIL THE APP.  A probe that raises is reported as "unknown", not fatal.
  The gate degrading is a problem; the gate crashing is a worse one.
* DECLARED vs LOADED.  ``self_check()`` compares what the classifier claims
  against what is importable and returns the discrepancy explicitly.
* IMPORTABLE IS NOT IN FORCE.  Added 2026-08-25 after this module reproduced,
  one level up, the exact bug it was written to prevent. The Windows installer
  (``packaging/windows/requirements/recommended.txt``) DOES install
  presidio-analyzer, so ``find_spec`` succeeded and a fresh install printed
  "4/4 layers active" — while Presidio was deliberately inert, because
  ``classify()`` only consults it when FRIDAY_PRESIDIO_ENFORCE=1 and otherwise
  routes it to observe-only shadow mode. A layer that cannot change an outcome
  is not a protection, whatever `pip` thinks. So a layer counts as active only
  if it is BOTH importable AND able to influence a decision.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from typing import Dict, List, Optional

log = logging.getLogger("friday.privacy.layers")

# ── Layer declarations ────────────────────────────────────────────────────────
# name → (human label, module that must import for the layer to run, always_on)
# ``module`` None means the layer has no external dependency and always runs.
LAYER_SPECS = (
    ("regex",     "Layer 1a - structured-token regex (SSN/CC/routing/API key)", None,                    True),
    ("keyword",   "Layer 1b - keyword tiers (strong phrases + context-gated)",  None,                    True),
    ("presidio",  "Layer 2 - Presidio NER (names, dates, medical/financial)",   "presidio_analyzer",     False),
    ("embedding", "Layer 3 - MiniLM semantic similarity to exemplars",          "sentence_transformers", False),
    ("local_llm", "Layer 4 - local LLM adjudication for ambiguous spans",       None,                    False),
)

# Layer 4 is opt-in per call (``use_llm=False`` is the default in classify()),
# so it is reported as "off by default" rather than missing.
_OPT_IN_LAYERS = {"local_llm"}


def _module_available(mod: str) -> Optional[bool]:
    """True/False if determinable, None if the probe itself failed."""
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return None


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def probe_layers() -> Dict[str, dict]:
    """Probe every declared layer. Cheap: no heavy imports, no model loads."""
    out: Dict[str, dict] = {}
    for name, label, mod, always in LAYER_SPECS:
        if always:
            active, reason = True, "built in"
        elif name in _OPT_IN_LAYERS:
            active, reason = False, (
                "opt-in per call (use_llm=True); off by default. No caller "
                "enables it today. Its model is resolved from the Ollama "
                "daemon's own inventory at call time - it is not a hardcoded "
                "tag, which is how this layer spent its life 404ing silently "
                "(fixed 2026-08-26)")
        else:
            avail = _module_available(mod)
            if avail is None:
                active, reason = False, f"probe failed for {mod!r}"
            elif avail:
                # Importable. But can it actually change a tier decision?
                # Presidio cannot unless enforcement is explicitly turned on:
                # classify() routes it to shadow (observe-only) by default,
                # having measured it escalating 6 of 12 benign prompts.
                if name == "presidio" and not enforcement_enabled():
                    active = False
                    reason = (
                        f"{mod} installed but OBSERVE-ONLY - it changes no "
                        f"outcome (set {ENFORCE_ENV}=1 to enforce; rejected by "
                        f"measurement 2026-08-24)"
                    )
                else:
                    active, reason = True, f"{mod} importable"
            else:
                active, reason = False, f"{mod} NOT INSTALLED"
        out[name] = {"label": label, "active": active, "module": mod, "reason": reason}
    return out


def active_layers() -> List[str]:
    return [n for n, v in probe_layers().items() if v["active"]]


def self_check() -> dict:
    """Compare DECLARED layers against LOADED ones.

    Returns a dict with ``ok`` False whenever a non-opt-in layer is declared but
    cannot load — i.e. whenever the classifier's docstring would be a lie.
    """
    probed = probe_layers()
    declared = [n for n, _l, _m, _a in LAYER_SPECS if n not in _OPT_IN_LAYERS]
    missing = [n for n in declared if not probed[n]["active"]]
    return {
        "ok": not missing,
        "frozen": is_frozen(),
        "declared": declared,
        "active": [n for n in declared if probed[n]["active"]],
        "missing": missing,
        "detail": probed,
    }


def describe() -> str:
    """One honest sentence about the protection actually in force.

    This is the string any UI / API / log line should use. It never says
    "four-layer" unless four layers are genuinely loaded.
    """
    chk = self_check()
    n = len(chk["active"])
    total = len(chk["declared"])
    where = "frozen build" if chk["frozen"] else "source checkout"
    if chk["ok"]:
        return f"Sensitivity classifier: {n}/{total} layers active ({where})."
    miss = ", ".join(chk["missing"])
    return (
        f"Sensitivity classifier: {n}/{total} layers active ({where}). "
        f"DEGRADED - not running: {miss}."
    )


def report_at_startup(logger: Optional[logging.Logger] = None) -> dict:
    """Log the actual layer state. Call once during boot.

    Missing layers log at WARNING so they cannot pass unnoticed in a normal
    run; this is the whole point of the module.
    """
    lg = logger or log
    chk = self_check()
    if chk["ok"]:
        lg.info(describe())
    else:
        lg.warning(describe())
        for name in chk["missing"]:
            d = chk["detail"][name]
            lg.warning("  privacy layer INACTIVE: %s - %s", d["label"], d["reason"])
        if chk["frozen"]:
            lg.warning(
                "  running from a frozen build: check AgentFriday.spec 'excludes' "
                "and requirements.txt before trusting any four-layer claim."
            )
    return chk


# ── Shadow mode ───────────────────────────────────────────────────────────────
# Presidio must NOT change any outcome on day one. PERSON / LOCATION /
# DATE_TIME are exactly the recognisers that fire on ordinary prose, and this
# codebase already carries three scars from over-broad classification
# ('courtesy' matching 'court', 'Sovereign Vault' nuking Friday's own system
# prompt, 'family picture-book aesthetic' killing a storybook turn). So the
# default is observe-only: log what Presidio WOULD have escalated, change
# nothing, and let a week of logs decide whether enforcement is warranted.
SHADOW_ENV = "FRIDAY_PRESIDIO_SHADOW"      # "1" to observe (log only)
ENFORCE_ENV = "FRIDAY_PRESIDIO_ENFORCE"    # "1" to actually act on Presidio


def shadow_enabled() -> bool:
    return os.environ.get(SHADOW_ENV, "").strip() in ("1", "true", "yes", "on")


def enforcement_enabled() -> bool:
    """Presidio only influences decisions when EXPLICITLY turned on."""
    return os.environ.get(ENFORCE_ENV, "").strip() in ("1", "true", "yes", "on")
