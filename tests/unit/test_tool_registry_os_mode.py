"""PR-2 (OS mode switch, OS-mode sequence) — acceptance criterion: "the tool
registry sent to the model contains no computer-control tools" under
FRIDAY_OS_MODE=1.

services/agent.py filters CLAUDE_TOOLS/CLAUDE_TOOL_HANDLERS/TOOL_RINGS
against capability_preflight.missing_tools() ONCE, at agent.py's own import
time (see the "CAPABILITY PREFLIGHT" section near the bottom of that file).
Every other test file in this suite imports agent_friday.services.agent
exactly once and keeps using that same cached module object for the rest of
the pytest session; `importlib.reload()` on it here would rebind
module-level names (CLAUDE_TOOLS, CLAUDE_TOOL_HANDLERS, the _CC_PERMISSION /
_CC_KILL threading.Event objects, etc.) to brand-new objects that OTHER
already-imported modules (routes/control.py, routes/tasks.py, and others
that did `from agent_friday.services.agent import X` at their own import
time) would NOT see — a desync that was confirmed experimentally to make an
unrelated task-registry test fail later in the same session.

So these tests spawn a FRESH child interpreter per env-var state instead of
reloading the shared module in-process — the only way to exercise "what does
a freshly started process's registry look like under FRIDAY_OS_MODE=1"
without corrupting every other test that runs in this pytest session. Each
child inherits this process's already-redirected HOME/USERPROFILE (see
tests/conftest.py), so it never touches the real ~/.friday either.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"

_PROBE_SCRIPT = """
import json
from agent_friday.services import agent as agent_mod

print(json.dumps({
    "tool_names": sorted(t["name"] for t in agent_mod.CLAUDE_TOOLS),
    "handler_names": sorted(agent_mod.CLAUDE_TOOL_HANDLERS.keys()),
    "ring_names": sorted(agent_mod.TOOL_RINGS.keys()),
}))
"""

_COMPUTER_CONTROL_TOOLS = ("screenshot", "move_mouse", "click", "type_text",
                          "press_key", "scroll")


def _probe_registry(os_mode: str | None) -> dict:
    """Run _PROBE_SCRIPT in a fresh child interpreter and return its parsed
    JSON — the tool registry as a truly freshly-started process would build
    it, with FRIDAY_OS_MODE set (or removed) for that child only."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if os_mode is None:
        env.pop("FRIDAY_OS_MODE", None)
    else:
        env["FRIDAY_OS_MODE"] = os_mode

    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"probe subprocess failed (FRIDAY_OS_MODE={os_mode!r}):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def registry_os_mode_on():
    return _probe_registry("1")


@pytest.fixture(scope="module")
def registry_os_mode_unset():
    return _probe_registry(None)


def test_no_computer_control_tools_in_registry_under_os_mode(registry_os_mode_on):
    for name in _COMPUTER_CONTROL_TOOLS:
        assert name not in registry_os_mode_on["tool_names"], (
            f"{name!r} must not appear in the tool registry sent to the "
            "model under FRIDAY_OS_MODE=1"
        )
        assert name not in registry_os_mode_on["handler_names"]
        assert name not in registry_os_mode_on["ring_names"]


def test_clipboard_tool_not_in_registry_under_os_mode(registry_os_mode_on):
    assert "write_clipboard" not in registry_os_mode_on["tool_names"]
    assert "write_clipboard" not in registry_os_mode_on["handler_names"]
    assert "write_clipboard" not in registry_os_mode_on["ring_names"]


def test_clipboard_tool_present_when_os_mode_unset(registry_os_mode_unset):
    """Regression guard: write_clipboard was never gated before this PR —
    with OS mode off (a freshly started, Windows-default process) it must
    always be registered."""
    assert "write_clipboard" in registry_os_mode_unset["tool_names"]
    assert "write_clipboard" in registry_os_mode_unset["handler_names"]


def test_computer_control_tools_unaffected_by_os_mode_var_itself(
        registry_os_mode_on, registry_os_mode_unset):
    """Regression guard, phrased without assuming pyautogui either way is
    installed in this environment: whichever computer-control tools ARE
    present with OS mode unset must be a SUPERSET of (in fact, disjoint
    from) what's present with OS mode on — OS mode can only remove tools
    relative to the unset baseline, never add or otherwise change them."""
    on_names = set(registry_os_mode_on["tool_names"])
    off_names = set(registry_os_mode_unset["tool_names"])
    cc_removed_by_os_mode = set(_COMPUTER_CONTROL_TOOLS) & off_names
    assert cc_removed_by_os_mode.isdisjoint(on_names)
