"""Declared capabilities whose dependency may be absent — checked, not assumed.

WHY THIS EXISTS
---------------
Twice in two days a capability Friday tells the user she has turned out to rest
on a library nobody installed:

  * 2026-08-24 — the sensitivity classifier's docstring described four layers.
    Layer 2 (Presidio) had never been listed in ``requirements.txt``, so it had
    never run in any environment. The code was real; the packaging was not.
  * 2026-08-25 — an uploaded PDF came back "PDF received (223KB). Install
    pdfplumber for full analysis". ``pdfplumber`` appeared in no requirements
    file and in no PyInstaller hidden-import list. The PDF branch of
    ``/api/analyze-file`` had therefore never worked on this machine.

Both are the same defect, and it is the same defect as a tool named in the
system prompt that the model's actual surface does not contain: **something
declares a capability, and nothing checks that the capability exists.**

THE RULE THIS MODULE ENFORCES
-----------------------------
A capability whose dependency is missing must be *absent*, not
*present-but-broken*:

  * ``missing_tools()`` is consulted by the tool registry, which DROPS the
    affected tools. A tool that cannot run is not offered to any model, so no
    model can announce it.
  * ``report()`` logs the gap at startup and is served by
    ``/api/health/capabilities``, so the gap is loud rather than discovered by
    a user watching a file fail to appear on their desktop.

WHAT BELONGS HERE
-----------------
Only capabilities Friday *claims* — in the system prompt, in a tool
description, in a route's response, or in user-facing copy. A library that is
purely internal degrades quietly and correctly on its own; this table is for
the ones where silence becomes a false statement to the user.

The ``optional`` flag separates the two honest states. ``optional=False`` means
the capability is advertised unconditionally and a missing dependency is a
PACKAGING BUG. ``optional=True`` means the absence is a deliberate, documented
trade-off (Presidio's 590 MB model, Layer 3's 4.4 GB of torch) — still
reported, never called a bug.
"""

from __future__ import annotations

import importlib.util
import logging

from agent_friday.core.os_mode import is_os_mode

_log = logging.getLogger("friday.capabilities")


class Capability:
    """One declared capability and the import that has to succeed for it.

    Most capabilities are gated purely on whether a dependency imports. A
    capability may ALSO (or instead, when `module` is None) be gated on
    OS mode: `os_mode_reason`, when set, is a short human explanation for
    why the capability is unavailable specifically in the sealed Friday
    Linux kiosk image — e.g. "no desktop to control in kiosk mode" — which
    is a different fact from "the library isn't installed" and must not be
    reported with a `pip install` fix line.
    """

    __slots__ = ("key", "module", "claim", "breaks", "tools", "install",
                 "optional", "os_mode_reason")

    def __init__(self, key, module, claim, breaks, *, tools=(), install=None,
                 optional=False, os_mode_reason=None):
        self.key = key
        self.module = module          # importable name, checked without importing;
                                       # None means "no import dependency at all",
                                       # i.e. this capability is only ever gated by
                                       # os_mode_reason (see `present` below).
        self.claim = claim            # what Friday tells the user she can do
        self.breaks = breaks          # what actually happens when it is absent
        self.tools = tuple(tools)     # registry entries to drop when absent
        self.install = install or (f"pip install {module}" if module else "")
        self.optional = optional
        self.os_mode_reason = os_mode_reason

    @property
    def present(self) -> bool:
        """False when the dependency cannot import, OR — for a capability
        with `os_mode_reason` set — when FRIDAY_OS_MODE is on, regardless of
        whether the dependency imports. A machine running the kiosk image may
        have pyautogui installed; it still has no desktop to control."""
        if self.os_mode_reason and is_os_mode():
            return False
        if self.module is None:
            return True
        try:
            return importlib.util.find_spec(self.module) is not None
        except (ImportError, ValueError):
            return False

    @property
    def unavailable_reason(self) -> str:
        """Why this capability is absent right now — distinguishing an
        OS-mode gate (environment, not packaging) from a genuinely missing
        dependency. Only meaningful when `present` is False."""
        if self.os_mode_reason and is_os_mode():
            return self.os_mode_reason
        return self.breaks


# ── The declared inventory ────────────────────────────────────────────────
CAPABILITIES = (
    Capability(
        "pdf_text", "pdfplumber",
        claim="read and summarise an uploaded PDF",
        breaks="routes/core_routes.py::analyze_file returns a stub telling the "
               "user to pip-install a library that is Friday's own dependency, "
               "not theirs",
        install="pip install pdfplumber",
    ),
    Capability(
        "os_control", "pyautogui",
        claim="take a screenshot, move the mouse, type, click, scroll",
        breaks="the ring-3 tools are declared to every model and every call "
               "returns 'pyautogui not installed' — the model announces the "
               "screenshot, then discovers it cannot take one",
        tools=("screenshot", "move_mouse", "click", "type_text", "press_key",
               "scroll"),
        install="pip install pyautogui",
        # Kiosk image (FRIDAY_OS_MODE=1): there is no desktop to control even
        # when pyautogui happens to be installed — see core/os_mode.py.
        os_mode_reason="no desktop to control in kiosk mode",
    ),
    Capability(
        "clipboard", None,
        claim="copy text to your clipboard",
        breaks="the write_clipboard tool is declared to every model and "
               "every call shells out to a PowerShell Set-Clipboard that "
               "has nothing to write to",
        tools=("write_clipboard",),
        # No import dependency (it shells out to PowerShell), so this
        # capability is otherwise always present — it is gated ONLY by
        # OS mode, which is why `module` is None above.
        os_mode_reason="no clipboard to control in kiosk mode",
    ),
    Capability(
        "pii_ner", "presidio_analyzer",
        claim="Layer 2 of the sensitivity classifier (named-entity PII)",
        breaks="the classifier runs Layers 1a+1b only; services/privacy_layers "
               "already reports the real count so nothing over-claims",
        install="pip install presidio-analyzer, then a spaCy English model",
        optional=True,
    ),
    Capability(
        "embeddings", "sentence_transformers",
        claim="Layer 3 of the sensitivity classifier, and semantic context pruning",
        breaks="excluded from the frozen build on purpose (torch is 4.4 GB "
               "against a 152 MB exe); privacy_layers reports the real count",
        optional=True,
    ),
)

_BY_KEY = {c.key: c for c in CAPABILITIES}


def missing(include_optional: bool = False):
    """Capabilities whose dependency is absent.

    Optional ones are excluded by default: they are a stated trade-off, not a
    gap, and callers that drop tools should not drop anything over them.
    """
    return [c for c in CAPABILITIES
            if not c.present and (include_optional or not c.optional)]


def missing_tools() -> frozenset:
    """Tool names that must NOT be registered in this environment.

    The registry calls this at import time. A tool listed here is removed
    outright rather than left in place to fail at call time, because a model
    cannot announce a tool it was never given.
    """
    names = set()
    for cap in missing():
        names.update(cap.tools)
    return frozenset(names)


def status() -> dict:
    """Serialisable snapshot for /api/health/capabilities and the UI."""
    return {
        "capabilities": [
            {"key": c.key, "module": c.module, "present": c.present,
             "optional": c.optional, "claim": c.claim,
             "breaks_when_absent": c.unavailable_reason, "install": c.install,
             "os_mode_gated": bool(c.os_mode_reason and is_os_mode())}
            for c in CAPABILITIES
        ],
        "missing_required": [c.key for c in missing()],
        "missing_optional": [c.key for c in CAPABILITIES
                             if not c.present and c.optional],
        "tools_withheld": sorted(missing_tools()),
    }


def report() -> list:
    """Log every gap at startup and return the lines, for the boot report.

    Required gaps are WARNINGs because they are packaging bugs. Optional ones
    are INFO because they are decisions. An OS-mode gate is neither — it is
    the environment behaving exactly as designed (a kiosk has no desktop), so
    it is logged at INFO with its own reason rather than a `pip install` fix
    line that would be actively wrong (the dependency may well be installed;
    the environment is what withholds it). Nothing here is silent: a
    capability that is quietly not there is exactly the failure this module
    exists to prevent.
    """
    lines = []
    for cap in CAPABILITIES:
        if cap.present:
            continue
        os_mode_gated = bool(cap.os_mode_reason and is_os_mode())
        if os_mode_gated:
            line = ("[capability] {0}: withheld by OS mode — {1}"
                    .format(cap.key, cap.os_mode_reason))
            _log.info("%s withheld by OS mode: %s", cap.key, cap.os_mode_reason)
        elif cap.optional:
            line = ("[capability] {0}: {1} absent by design — {2}"
                    .format(cap.key, cap.module, cap.breaks))
            _log.info("%s absent (optional): %s", cap.module, cap.breaks)
        else:
            line = ("[capability] {0}: MISSING {1} — Friday claims she can {2}. "
                    "She cannot. {3}. Fix: {4}"
                    .format(cap.key, cap.module, cap.claim, cap.breaks,
                            cap.install))
            _log.warning("MISSING %s — the %r capability is advertised but "
                         "cannot run. %s. Fix: %s",
                         cap.module, cap.key, cap.breaks, cap.install)
        lines.append(line)
    return lines


def explain(key: str) -> str:
    """One honest sentence about a capability, for a user-facing message.

    Returns "" when the capability is present (or unknown), so a caller can
    write ``explain(k) or normal_result`` without branching.
    """
    cap = _BY_KEY.get(key)
    if cap is None or cap.present:
        return ""
    if cap.os_mode_reason and is_os_mode():
        return "I can't {0} in this environment — {1}.".format(
            cap.claim, cap.os_mode_reason)
    return ("I can't {0} in this build — the {1} library isn't installed. "
            "That's a gap in how Friday is packaged, not something you did "
            "wrong.".format(cap.claim, cap.module))
