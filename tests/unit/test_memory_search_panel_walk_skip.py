"""The memory search panel (Settings > Privacy & Data > Search what Friday remembers) is a
search, not a setting: its box, filter ticks, "Show corrected" switch and buttons save nothing
through /api/settings. The settings walk (tests/app/specs/settings_walk.spec.ts, NOT_A_SETTING)
lists a control marked data-walk-skip on the control itself and leaves it. This guard runs
without a browser: every control the panel renders carries the marker, in both UI files.

Red before the fix: the walk ticked the panel's filters as if they were settings and failed with
"Privacy & Data > Conversations / Memory card / ... / Show corrected: no POST /api/settings".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = ("index.html", "ui_parts/app.html")
CONTROL = re.compile(r"E\(\s*'(input|button|select|textarea)'\s*,\s*\{")
TOGGLE = re.compile(r"E\(\s*StToggle\s*,\s*\{")


def _panel(text: str) -> str:
    return text.split("function MemorySearchPanel(")[1].split("// End memory search panel")[0]


def _props(body: str, open_brace: int) -> str:
    """The object literal that starts at open_brace, braces matched."""
    depth = 0
    for i in range(open_brace, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                return body[open_brace:i + 1]
    raise AssertionError("unbalanced props at %d" % open_brace)


@pytest.mark.parametrize("name", FILES)
def test_every_control_in_the_panel_is_marked_not_a_setting(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    body = _panel(text)
    controls = [(m.group(1), _props(body, m.end() - 1)) for m in CONTROL.finditer(body)]
    toggles = [_props(body, m.end() - 1) for m in TOGGLE.finditer(body)]
    kinds = {k for k, _ in controls}
    # The panel has a search box, filter ticks and buttons, and a Show corrected switch.
    assert {"input", "button"} <= kinds and toggles, (name, kinds, len(toggles))
    unmarked = [k + " " + p[:80] for k, p in controls if "'data-walk-skip'" not in p]
    assert unmarked == [], (name, unmarked)
    assert all("walkSkip:" in p for p in toggles), (name, toggles)
    assert "<input" not in body and "<button" not in body and "<StToggle" not in body, name


@pytest.mark.parametrize("name", FILES)
def test_the_switch_puts_the_marker_on_the_control_itself(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    toggle = text.split("function StToggle(")[1].split("\nfunction ")[0]
    assert '"data-walk-skip": walkSkip || undefined' in toggle, name


def test_the_walk_honours_the_marker_on_the_control_only():
    spec = (ROOT / "tests/app/specs/settings_walk.spec.ts").read_text(encoding="utf-8")
    assert "e.getAttribute('data-walk-skip')" in spec
    assert "closest('[data-walk-skip]')" not in spec, "the marker must not be inherited from a container"
    assert "a setting cannot opt out of the walk" in spec
