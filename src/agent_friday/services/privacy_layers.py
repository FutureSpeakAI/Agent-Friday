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
* IMPORTABLE IS NOT IN FORCE.  The Windows installer
  (``packaging/windows/requirements/recommended.txt``) DOES install
  presidio-analyzer, so ``find_spec`` succeeds and a fresh install would
  otherwise print "4/4 layers active" — while Presidio is deliberately inert,
  because ``classify()`` only consults it when FRIDAY_PRESIDIO_ENFORCE=1 and
  otherwise routes it to observe-only shadow mode. A layer that cannot change
  an outcome is not a protection, whatever `pip` thinks. So a layer counts as
  active only if it is BOTH importable AND able to influence a decision.
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


def _embedding_runtime(mod: str):
    """(active, reason) for Layer 3 from the classifier's own load state."""
    try:
        from agent_friday.services.sensitivity_classifier import layer3_state
        st = layer3_state()
    except Exception as e:  # noqa: BLE001
        return False, f"{mod} installed; state unreadable ({type(e).__name__})"
    if st["ready"]:
        return True, f"{mod} loaded and running"
    if st["state"] == "not loaded yet":
        # Loads on the first egress decision (and at boot, after the ML preload).
        return True, f"{mod} installed; loads on first use"
    return False, (f"{mod} installed but the model load failed, retrying "
                   f"({st['error'] or 'no detail'}); cloud egress fails closed meanwhile")


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _package_dir():
    """The installed `agent_friday` package directory. Split out so a test can
    stand somewhere else without moving the process."""
    from pathlib import Path
    return Path(__file__).resolve().parents[1]


def build_kind() -> str:
    """Which KIND of build this is: frozen, a source checkout, or installed.

    There are three, and the health line used to offer two -- a PyInstaller
    bundle or "source checkout" -- so a `pip install`, which is neither, was
    described to the owner of a clean install as somebody's working copy. That
    was the 5.14.2 walkthrough's complaint and it is a plain inaccuracy.

    A source checkout is recognised by its shape rather than by elimination: the
    package sits in `<repo>/src/agent_friday` with the project's own
    pyproject.toml above it. Anything else is an installed build, and an
    unreadable filesystem answers "installed build" too -- of the two wrong
    answers, calling an install a developer's checkout is the more misleading.
    """
    if is_frozen():
        return "frozen build"
    try:
        pkg = _package_dir()
        if pkg.parent.name == "src" and (pkg.parent.parent / "pyproject.toml").is_file():
            return "source checkout"
    except Exception:
        pass
    return "installed build"


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
                "tag, so an uninstalled model cannot make this layer 404 "
                "silently")
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
                        f"measurement on the reference machine)"
                    )
                elif name == "embedding":
                    # Installed is not running. A boot-time import race used to
                    # leave Layer 3 down while this probe, which only asks
                    # whether the name resolves, reported it active.
                    active, reason = _embedding_runtime(mod)
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
    inactive = [n for n in declared if not probed[n]["active"]]
    # A LAYER OFF ON PURPOSE IS NOT A FAULT.
    #
    # Presidio ships in the Windows installer's recommended tier and is
    # deliberately observe-only: classify() consults it only under
    # FRIDAY_PRESIDIO_ENFORCE=1. Not counting it as active is correct and stays
    # -- a layer that cannot change an outcome is not a protection, which is why
    # this module exists. But it was also listed as MISSING, which made
    # describe() say "DEGRADED" about a working install, and made boot log a
    # warning about a decision somebody made on purpose.
    #
    # So the two are separated: `missing` is a fault, `by_design` is a choice,
    # and `ok` tracks faults only. `describe()` and `report_at_startup()` both
    # read `missing`, so both stop crying wolf without either losing its teeth.
    by_design = [n for n in inactive if _off_by_design(n, probed[n])]
    missing = [n for n in inactive if n not in by_design]
    return {
        "ok": not missing,
        "frozen": is_frozen(),
        "build": build_kind(),
        "declared": declared,
        "active": [n for n in declared if probed[n]["active"]],
        "missing": missing,
        "by_design": by_design,
        "detail": probed,
    }


#: Layers this build does not commit to shipping or running. Inactive, they are
#: a CHOICE and not a fault, however they came to be inactive -- absent from the
#: build entirely, or installed and deliberately observe-only.
#:
#: Presidio is here because the capability report already describes it that way
#: ("presidio_analyzer absent by design -- the classifier runs Layers 1a+1b
#: only"), and two parts of one product must not disagree about whether the same
#: state is intended. Layer 3 is deliberately NOT here: the Windows installer
#: ships sentence_transformers by default, so its absence means something went
#: wrong with an install that meant to have it.
_BY_DESIGN_OPTIONAL = frozenset({"presidio"})


def _off_by_design(name: str, probed: dict) -> bool:
    """Is this layer inactive because somebody chose that, not because it broke?

    True for a layer the build never promised (`_BY_DESIGN_OPTIONAL`), whether it
    is absent or present-but-inert. False for everything else, so a layer that is
    installed or expected and is down still reads as a fault.

    The exception is asking for it: with FRIDAY_PRESIDIO_ENFORCE=1 somebody has
    said they want Presidio deciding, and a layer that was asked for and cannot
    run is a fault no matter what the default would have been.
    """
    if name not in _BY_DESIGN_OPTIONAL:
        return False
    if name == "presidio" and enforcement_enabled():
        return False
    return True


def describe() -> str:
    """One honest sentence about the protection actually in force.

    This is the string any UI / API / log line should use. It never says
    "four-layer" unless four layers are genuinely loaded.
    """
    chk = self_check()
    n = len(chk["active"])
    total = len(chk["declared"])
    where = build_kind()
    plain = {l["name"]: l for l in plain_layers()}
    # Said either way, because a layer nobody mentions is a layer nobody checks
    # -- but said as a choice, not as a failure.
    aside = ""
    if chk["by_design"]:
        # Say WHICH state it is in, because "by design" covers both a layer this
        # build leaves out and one it ships but keeps inert, and the reader is
        # entitled to know which.
        bits = []
        for m in chk["by_design"]:
            reason = str((chk["detail"].get(m) or {}).get("reason") or "")
            how = ("installed and observe-only" if "OBSERVE-ONLY" in reason.upper()
                   else "not installed in this build")
            bits.append("%s %s" % (m, how))
        aside = (" %s by design, so it is not counted as protection in force."
                 % "; ".join(bits))
    if chk["ok"]:
        return (f"Sensitivity classifier: {n}/{total} layers active "
                f"({where}).{aside}")
    miss = "; ".join(f"{m} - {plain[m]['label']} ({plain[m]['status']})"
                     for m in chk["missing"] if m in plain)
    return (
        f"Sensitivity classifier: {n}/{total} layers active ({where}). "
        f"DEGRADED - not running: {miss}.{aside}"
    )


#: What each layer is called where a person reads it (Settings, health).
PLAIN_NAMES = {
    "regex": "Pattern filters (card numbers, IDs, keys)",
    "keyword": "Sensitive keyword filters",
    "presidio": "Name and entity detection",
    "embedding": "Semantic check",
    "local_llm": "Local model review",
}


def plain_layers() -> List[dict]:
    """Every layer with a plain status, for the privacy panel and /api/health.

    "not installed in this build" is a design fact (the packaged .exe leaves
    the semantic check out); "starting" is a fault being retried, during
    which cloud-bound text with no other privacy signal is held.
    """
    out = []
    for name, v in probe_layers().items():
        reason = v["reason"]
        if v["active"]:
            status = "on (loads on first use)" if "loads on first use" in reason else "on"
        elif name in _OPT_IN_LAYERS:
            status = "off by default"
        elif "NOT INSTALLED" in reason:
            status = "not installed in this build"
        elif "OBSERVE-ONLY" in reason:
            status = "observe only (changes nothing that is sent)"
        elif name == "embedding":
            status = ("starting - cloud-bound text with no other privacy signal "
                      "is held until it is ready")
        else:
            status = "not running"
        out.append({"name": name, "label": PLAIN_NAMES.get(name, name),
                    "active": bool(v["active"]), "status": status, "detail": reason})
    return out


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
