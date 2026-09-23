"""The tracking settings live in four places and must agree.

A tracking dial exists as a Python default, as a JavaScript default inside the
engine, and as a slider in each of the two UI files. Three of those four are
hand-maintained, and nothing at runtime complains when one of them is missing:
a dial absent from the Python defaults is simply never persisted, and a dial
absent from one HTML file silently disappears from that build of the panel.

check_settings_readers.py compares the two HTML files with each other. These
tests close the remaining gap, between the engine's own defaults and the stored
ones, and pin the claim that dock depth 0 leaves nothing of the effect behind.
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
MIRROR = REPO / "ui_parts" / "app.html"


def js_engine_defaults() -> set:
    """The DEFAULTS object inside window.FridayTracking."""
    src = INDEX.read_text(encoding="utf-8")
    start = src.index("window.FridayTracking = (function () {")
    body = src[start:src.index("const cfg = Object.assign({}, DEFAULTS);", start)]
    block = body[body.index("const DEFAULTS = {"):]
    block = block[: block.index("};")]
    return set(re.findall(r"(\w+)\s*:", block))


def python_defaults() -> set:
    import agent_friday
    from agent_friday.core import DEFAULT_SETTINGS

    # The venv pins the primary checkout's src on sys.path, so a worktree can
    # end up comparing its own index.html against another tree's Python. That
    # reads as a mysterious mismatch, or worse, as a pass against the wrong
    # file. Say so plainly instead.
    imported = pathlib.Path(agent_friday.__file__).resolve()
    assert REPO in imported.parents, (
        "agent_friday was imported from %s, which is not this checkout (%s). "
        "The comparison below would be against another tree's settings."
        % (imported, REPO)
    )
    return set(DEFAULT_SETTINGS["tracking"])


def test_engine_and_stored_defaults_name_the_same_dials():
    engine, stored = js_engine_defaults(), python_defaults()
    assert engine, "could not read the engine's DEFAULTS"
    assert engine == stored, (
        "tracking dials disagree.\n"
        "  only in index.html's engine: %s\n"
        "  only in DEFAULT_SETTINGS:    %s"
        % (sorted(engine - stored), sorted(stored - engine))
    )


@pytest.mark.parametrize("path", [INDEX, MIRROR], ids=["index.html", "app.html"])
def test_dock_depth_has_a_slider_in_both_ui_files(path):
    assert "'dock_depth'" in path.read_text(encoding="utf-8"), (
        "%s has no dock_depth slider, so the dock's depth would be "
        "unadjustable in that build of the settings panel" % path.name
    )


def test_dock_depth_zero_leaves_nothing_behind():
    """The setting is an off switch, not a smaller version of the effect.

    Both of the floor's own dimensions are multiplied by --dock-k, so at 0 the
    floor has no height and no opacity. Losing either multiplier would leave a
    band across the bottom of the screen that the flat dock never had.
    """
    src = INDEX.read_text(encoding="utf-8")
    floor = src[src.index("        .dock::before {"):]
    floor = floor[: floor.index("\n        }")]
    for prop in ("height", "opacity"):
        decl = re.search(r"\n\s*%s:\s*([^;]+);" % prop, floor)
        assert decl, "the dock floor has no %s declaration" % prop
        assert "var(--dock-k)" in decl.group(1), (
            "the dock floor's %s does not scale with --dock-k (%r), so "
            "dock_depth 0 would still draw it" % (prop, decl.group(1).strip())
        )
