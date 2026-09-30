"""The UI half of "Improve this workspace" (salon spec §4.9.1, Phase 2b), as
the served files carry it: the header button, the Mine group, the bundle
frame, the swap card and the bundle history, in index.html and its mirror.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
SCENE = (ROOT / "ui_parts" / "styles_and_scene.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "friday_bundles.js").read_text(encoding="utf-8")
PANEL = (ROOT / "static" / "friday_artifacts.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("html", [INDEX, APP], ids=["index", "mirror"])
def test_every_window_offers_improve_this_workspace(html):
    assert "data-ws-improve" in html
    assert "fridayImproveWorkspace" in html


@pytest.mark.parametrize("html", [INDEX, APP], ids=["index", "mirror"])
def test_installed_bundles_join_the_dock_under_mine(html):
    assert "FRIDAY_INSTALLED_BUNDLES" in html
    assert "'Mine'" in html or '"Mine"' in html


@pytest.mark.parametrize("html", [INDEX, APP], ids=["index", "mirror"])
def test_a_bundle_workspace_renders_in_its_frame_not_coming_soon(html):
    assert "FridayBundleFrame" in html
    # Both places a workspace body is chosen: the desktop window and the standalone tab.
    assert html.count("fridayBundleBody(") >= 2


@pytest.mark.parametrize("html", [INDEX, APP], ids=["index", "mirror"])
def test_the_swap_card_and_the_bundle_history_are_hooked_in(html):
    assert "workspace_swap" in html and "FridayWorkspaceSwapCard" in html
    assert "FridayBundleHistory" in html


def test_the_script_is_loaded_after_the_panel_in_both_shells():
    for s in (INDEX, SCENE):
        a, b = s.index("friday_artifacts.js"), s.index("friday_bundles.js")
        assert a < b


def test_the_bundle_frame_holds_no_origin_and_talks_only_to_the_broker():
    m = re.search(r"const SANDBOX\s*=\s*'([^']*)'", JS)
    assert m and "allow-same-origin" not in m.group(1) and "allow-scripts" in m.group(1)
    assert "fridayArtifactFrameDoc" in JS and "fridayAttachFrame" in JS
    assert "srcDoc" in JS and "src:" not in JS.split("function FridayBundleFrame")[1].split("function")[0]


def test_the_static_file_defines_the_four_pieces_and_talks_to_the_right_routes():
    for name in ("window.fridayImproveWorkspace", "window.FridayBundleFrame", "window.FridayWorkspaceSwapCard", "window.FridayBundleHistory"):
        assert name in JS, name
    for route in ("/api/workspaces/", "/improve", "/bundle", "/versions", "/rollback"):
        assert route in JS, route
    # A native workspace's refusal is shown in the owner's words, not swallowed.
    assert "blocker" in JS and "needs_phase_7" in JS


def test_the_panel_offers_compare_and_swap_for_a_workspace_codebase():
    assert "/api/workspaces/swap" in PANEL
    assert "Compare" in PANEL and "Swap in" in PANEL and "Install as workspace" in PANEL
    assert "workspace_id" in PANEL
    assert "window.fridayBusSubscribe" in PANEL
    assert "open_conversation" in PANEL
