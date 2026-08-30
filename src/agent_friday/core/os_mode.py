"""OS mode — single source of truth for "is this running as the sealed Friday
Linux kiosk image" (PR-2 of the OS-mode sequence — see Friday-Linux
docs/SPEC.md Section 13).

WHY THIS EXISTS
----------------
Six behaviors (tray, log routing, computer-control tools, `_open_app`,
clipboard, voice-asset lookup) all need to answer the same yes/no question:
"are we running headless in a kiosk with no desktop?" Before this module,
that question had no single owner — the risk (explicitly called out in the
OS-mode spec) is the same one `services/capability_preflight.py` documents
for import-time capability checks: a behavior gated in one place and not
another drifts, and a scattering of `os.environ.get("FRIDAY_OS_MODE")`
checks is exactly that scatter. Every gated behavior in this PR imports
`is_os_mode` from here rather than reading the environment variable itself.

DELIBERATELY STANDALONE
------------------------
This module does NOT import `agent_friday.core` (the package `__init__.py`)
or anything else from the `agent_friday` tree, even though it lives inside
the `core` package. `agent_friday/core/__init__.py` is a ~2600-line Flask
application module with real import-time side effects (it builds the Flask
app and runs a legacy `~/wiki` migration against the real home directory on
import, independent of FRIDAY_HOME/FRIDAY_OS_MODE). Python always executes a
package's `__init__.py` before any of its submodules are first imported by
an outside caller, so by the time anything reaches
`from agent_friday.core.os_mode import is_os_mode`, `core/__init__.py` has
already run regardless of what this module does. But this module itself
importing back into `agent_friday.core` would create a needless coupling (and
a real risk of a circular import, since `core/__init__.py` itself imports
this module for the log-routing gate) for a function that only needs
`os.environ`. Keeping this leaf-level and dependency-free means every one of
the six call sites — including `friday_tray.py`, which must stay import-light
enough to run before the Flask app exists — can import it safely.

FRIDAY_OS_MODE has no pre-existing convention anywhere in this codebase
(verified by a repo-wide grep before writing this) — this PR originates it.
"""
from __future__ import annotations

import os

#: Values that mean "on" when read from FRIDAY_OS_MODE. Deliberately generous
#: (case-insensitive "1"/"true"/"yes"/"on") because this variable is meant to
#: be set once in a systemd unit or container ENV line by whoever builds the
#: sealed image, not typed by hand on every boot — matching how the rest of
#: this codebase reads boolean-ish env vars (see FRIDAY_TRUST_LOOPBACK's
#: inverse pattern in core/__init__.py) rather than accepting only "1".
_TRUTHY = {"1", "true", "yes", "on"}


def is_os_mode() -> bool:
    """True when Friday is running as the sealed Linux kiosk image.

    Reads the FRIDAY_OS_MODE environment variable fresh on every call —
    intentionally not cached — so tests (and a long-lived process whose
    environment is mutated, e.g. by a test harness using monkeypatch) always
    see the current value rather than whatever was set at import time.
    """
    return os.environ.get("FRIDAY_OS_MODE", "").strip().lower() in _TRUTHY
