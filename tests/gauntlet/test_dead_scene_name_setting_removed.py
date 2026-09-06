"""Gauntlet finding (the maintainer's direct instruction, 2026-09-04): investigate
Settings > Appearance's "Scene" dropdown (writes `scene_name`, options
nebula/matrix/void/aurora/cosmos/midnight/prism/circuit/ocean/ember,
described as "Holographic background scene") and remove it if genuinely
wired to nothing -- but leave it alone and report back if it touches the
real holographic backgrounds at all, since those are developed work
The maintainer explicitly values.

Investigation confirmed `scene_name` is completely disconnected from the
real holo-structure system: `preferred_scene_index` (an index into
`EVOLUTION_PATH`, a list of 13 named 3D structures -- GENESIS LATTICE,
SACRED SPHERE, etc.) is the actual, extensively-wired mechanism that
selects what the hologram displays (routes/insights.py, core_routes.py,
`window.fridayVibe.setStructure()`, the real scene-picker UI). `scene_name`
is a separate settings key with an entirely different vocabulary (theme
names, not structure names) that is written by a dropdown in both
index.html and ui_parts/app.html and read NOWHERE -- not in either HTML
file's rendering logic, not in any Python backend file (grep-confirmed
across the whole repo; the one other "scene_name" hit, in
setup_wizard.py, is an unrelated local variable computed from
`preferred_scene_index`, not this settings key). The actual Three.js
scene setup (fog color, particle theme, bloom pass) is hardcoded and
never varies by this setting. Removed the dead dropdown from both files;
the real EVOLUTION_PATH/preferred_scene_index holographic-structure
system is completely untouched.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX_HTML = _REPO_ROOT / "index.html"
_APP_HTML = _REPO_ROOT / "ui_parts" / "app.html"


class TestDeadSceneNameSettingRemoved:
    def test_index_html_no_longer_writes_scene_name(self):
        text = _INDEX_HTML.read_text(encoding="utf-8")
        assert "scene_name" not in text, (
            "index.html still references the dead 'scene_name' setting -- "
            "confirmed to have zero readers anywhere and zero connection "
            "to the real holographic scene system (preferred_scene_index/"
            "EVOLUTION_PATH)"
        )

    def test_app_html_no_longer_writes_scene_name(self):
        text = _APP_HTML.read_text(encoding="utf-8")
        assert "scene_name" not in text, (
            "ui_parts/app.html still references the dead 'scene_name' "
            "setting -- same dead control as index.html"
        )

    def test_the_real_holographic_structure_system_is_untouched(self):
        """No-op-shaped grounding check: the ACTUAL, developed holographic
        scene mechanism (preferred_scene_index / EVOLUTION_PATH /
        fridayVibe.setStructure) must still be fully present and wired --
        this fix must not have touched it even incidentally."""
        text = _INDEX_HTML.read_text(encoding="utf-8")
        assert "preferred_scene_index" in text
        assert "EVOLUTION_PATH" in text
        assert "setStructure" in text
        app_text = _APP_HTML.read_text(encoding="utf-8")
        assert "preferred_scene_index" in app_text
