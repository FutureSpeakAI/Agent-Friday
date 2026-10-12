"""The avatar stays centred in the part of the desktop that is visible, and
moves there smoothly, in Classic and in Simple.

Two parts. The region maths, the spring and the controller run under node
(tests/ui/avatar_center.test.cjs, wrapped here so one command runs both). Then a
real browser loads the served page with a software renderer and measures the
avatar's focal point against the rectangles the page really drew: the chat tray
open and closed, a task tray, workspaces docked left and right, Simple's split
with the cards closed, and the frame timing of each move.

Conditions the measurements hold constant (tests/LESSONS-style, from the dock
and camera regressions): the native dock is held open so its idle hide cannot
change the work area between samples; the camera is settled across fresh
rendered frames (the renderer's own frame counter advances) before a position
is read; the head is neutral. Tolerance for "centred" is FOCUS_TOLERANCE pixels
and is stated in every assertion that uses it.

Synthetic: the page's API calls go nowhere, the camera is never started, and
nothing here is evidence about a physical camera or about how a person judges
the motion. Frames for a person to look at are written when
FRIDAY_AVATAR_CENTER_PROOFS names an existing directory outside the checkout.
"""
import functools
import http.server
import json
import os
import pathlib
import shutil
import socketserver
import statistics
import subprocess
import threading
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
node = shutil.which("node")

# ── node: the maths and the controller ───────────────────────────────────────

@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_region_spring_and_controller_pass_under_node():
    cp = subprocess.run([node, "--test", str(REPO / "tests" / "ui" / "avatar_center.test.cjs")],
                        capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(REPO))
    assert cp.returncode == 0, cp.stdout[-3000:] + cp.stderr[-1000:]


# ── the source the browser half exercises ────────────────────────────────────

def _text(rel):
    return (REPO / rel).read_text(encoding="utf-8-sig")


def test_both_pages_hand_the_chat_tray_event_to_the_cards_and_accept_a_remembered_width():
    for rel in ("index.html", "ui_parts/app.html"):
        text = _text(rel)
        assert "typeof d.frac === 'number' && d.frac > 0.1 && d.frac < 0.9" in text, rel
        assert "holo.setCards(d.cards === 'hide' ? 'hidden' : 'shown')" in text, rel
        assert "cards_reason" in text, rel


def test_the_render_loop_steps_the_frame_with_its_own_delta_and_no_timer():
    js = _text("static/friday_holographic_workspace.js")
    fit = js[js.index("function fitSceneCamera(sceneState)"):]
    fit = fit[:fit.index("function projectSceneStage")]
    assert "stepAvatarFrame(delta)" in fit and "stepRegion(delta)" in fit
    # The region is measured on events; nothing here samples it on an interval.
    region = js[js.index("function measureRegion()"):js.index("function stepRegion(")]
    assert "setInterval" not in region and "setTimeout" not in region


def test_the_native_dock_and_its_idle_timer_are_not_read_by_the_region():
    js = _text("static/friday_holographic_workspace.js")
    pieces = js[js.index("const REGION_PIECES"):js.index("function classicApplies")]
    assert ".dock" not in pieces


from avatar_center_page import *  # noqa: F401,F403  (fixtures and measurements)
from avatar_center_page import (FOCUS_TOLERANCE, assert_centred, browser, inject, open_page, remove, settled,  # noqa: F401
                                fresh_frames, hold_still)


# ── Classic ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def classic(browser):
    context, page = open_page(browser, "classic")
    yield page
    context.close()


def test_classic_centres_the_focal_point_below_the_bar_with_nothing_open(classic):
    classic.evaluate("__avc.tray(null, false)")
    state, region = assert_centred(classic, note="nothing open")
    assert state["avatar"]["scale"] == 1


@pytest.mark.parametrize("size", ["third", "half", "two_thirds"])
def test_classic_follows_the_chat_tray_open_and_closed(classic, size):
    classic.evaluate("__avc.tray(null, false)")
    assert_centred(classic, note="closed")
    classic.evaluate("s => __avc.tray(s, true)", size)
    classic.wait_for_function("() => __avc.chat() && __avc.chat().w > 100", timeout=15000)
    state, region = assert_centred(classic, note="open at " + size)
    assert region["w"] < 1280 - 300
    classic.evaluate("__avc.tray(null, false)")
    classic.wait_for_function("() => !__avc.chat()", timeout=15000)
    assert_centred(classic, note="closed again")


@pytest.mark.parametrize("side", ["left", "right"])
def test_classic_follows_a_workspace_docked_to_either_side(classic, side):
    classic.evaluate("__avc.tray(null, false)")
    left = 0 if side == "left" else 640
    inject(classic, '<div class="fwin" id="avc-ws" style="left:%dpx;top:60px;width:640px;height:640px"></div>' % left)
    try:
        extra = [{"x": left, "y": 60, "w": 640, "side": "right" if side == "right" else "left"}]
        assert_centred(classic, extra, note="workspace on the " + side)
    finally:
        remove(classic, "#avc-ws")
    assert_centred(classic, note="workspace closed")


def test_classic_follows_a_task_tray_hugging_the_edge(classic):
    classic.evaluate("__avc.tray(null, false)")
    inject(classic, '<div class="task-tray" id="avc-tray"><div style="height:280px"></div></div>')
    try:
        rect = classic.evaluate("(() => { const r = document.getElementById('avc-tray').getBoundingClientRect(); return {x: r.x, w: r.width}; })()")
        assert_centred(classic, [{"x": rect["x"], "w": rect["w"], "side": "right"}], note="task tray")
    finally:
        remove(classic, "#avc-tray")
    assert_centred(classic, note="task tray closed")


def test_the_dock_hiding_and_returning_does_not_move_the_classic_avatar(classic):
    classic.evaluate("__avc.tray(null, false)")
    before = assert_centred(classic, note="dock held open")[0]["avatar"]["focus"]
    classic.evaluate("document.querySelector('.dock')?.classList.add('hidden')")
    fresh_frames(classic, 4)
    hidden = settled(classic)["avatar"]["focus"]
    classic.evaluate("document.querySelector('.dock')?.classList.remove('hidden')")
    fresh_frames(classic, 4)
    after = settled(classic)["avatar"]["focus"]
    assert abs(hidden["x"] - before["x"]) < .5 and abs(hidden["y"] - before["y"]) < .5, (before, hidden)
    assert abs(after["x"] - before["x"]) < .5 and abs(after["y"] - before["y"]) < .5, (before, after)


def test_the_window_view_keeps_the_offset_and_the_parallax(classic):
    """Head tracking: the avatar holds its place behind the glass and the head
    moves only the eye. With the tray open the offset still lands the neutral
    view in the middle of what is left, and a head to one side still shifts the
    picture the same way, by the fit's scale."""
    classic.evaluate("__avc.tray(null, false)")
    classic.evaluate("FridayTracking.debugHead(0, 0, FridayTracking.head.baseline || .18)")
    open_view, _ = assert_centred(classic, note="neutral head, nothing open")
    classic.evaluate("FridayTracking.debugHead(.25, 0, FridayTracking.head.baseline || .18)")
    aside_open = settled(classic)["avatar"]["focus"]["x"] - open_view["avatar"]["focus"]["x"]
    classic.evaluate("__avc.tray('half', true)")
    classic.wait_for_function("() => __avc.chat() && __avc.chat().w > 100", timeout=15000)
    classic.evaluate("FridayTracking.debugHead(0, 0, FridayTracking.head.baseline || .18)")
    half_neutral, region = assert_centred(classic, note="neutral head, chat half")
    classic.evaluate("FridayTracking.debugHead(.25, 0, FridayTracking.head.baseline || .18)")
    aside_half = settled(classic)["avatar"]["focus"]["x"] - half_neutral["avatar"]["focus"]["x"]
    hold_still(classic)
    classic.evaluate("__avc.tray(null, false)")
    assert aside_open != 0 and aside_half != 0
    assert (aside_open > 0) == (aside_half > 0), "the head moves the picture the same way"
    scale = half_neutral["avatar"]["scale"]
    assert .55 <= scale < 1
    assert abs(aside_half / aside_open - scale) < .2, (aside_open, aside_half, scale)


# ── Simple ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def simple(browser):
    context, page = open_page(browser, "simple", width=1600, height=900)
    page.evaluate("FridayHolographicWorkspace.setArrangement('companion')")
    yield page
    context.close()


def stage_centre(state):
    stage = state["avatar"]["shown"]
    return {"x": stage["x"] + stage["w"] / 2, "y": stage["y"] + stage["h"] / 2}


def test_simple_keeps_the_avatar_in_the_middle_of_its_stage(simple):
    simple.evaluate("FridayHolographicWorkspace.setCards('shown')")
    simple.evaluate("__avc.tray(null, false)")
    state = settled(simple)
    bounds, centre = state["bounds"], stage_centre(state)
    assert bounds, state
    middle = {"x": bounds["x"] + bounds["w"] / 2, "y": bounds["y"] + bounds["h"] / 2}
    assert abs(middle["x"] - centre["x"]) <= 3 * FOCUS_TOLERANCE and abs(middle["y"] - centre["y"]) <= 3 * FOCUS_TOLERANCE, (bounds, centre)


def test_closing_the_cards_gives_friday_one_half_and_the_chat_the_other(simple):
    simple.evaluate("FridayHolographicWorkspace.setCards('shown')")
    simple.evaluate("__avc.tray(null, false)")
    settled(simple)
    simple.evaluate("FridayHolographicWorkspace.setCards('hidden')")
    simple.wait_for_function("() => __avc.chat() && Math.abs(__avc.chat().w - innerWidth / 2) < 3", timeout=15000)
    state = settled(simple)
    width = simple.evaluate("innerWidth")
    chat, stage = simple.evaluate("__avc.chat()"), state["avatar"]["shown"]
    assert abs(chat["w"] - width / 2) <= 2, chat
    assert chat["x"] >= width / 2 - 2
    # The avatar's half: the left half less the gutters, ending where the chat begins.
    assert stage["x"] <= 20 and abs((stage["x"] + stage["w"]) - (chat["x"] - 16)) <= 3, (stage, chat)
    assert abs(stage["w"] - (width / 2 - 32)) <= 3, stage
    bounds = state["bounds"]
    assert bounds and bounds["x"] >= stage["x"] - .5 and bounds["x"] + bounds["w"] <= stage["x"] + stage["w"] + .5
    # Fitted to the reserved frame with margins, not to the whole half: at least 24 px
    # inside every edge of it, and clear of the state caption beneath it.
    margin = 24
    assert bounds["x"] >= stage["x"] + margin and bounds["x"] + bounds["w"] <= stage["x"] + stage["w"] - margin, (bounds, stage)
    assert bounds["y"] >= stage["y"] + margin and bounds["y"] + bounds["h"] <= stage["y"] + stage["h"] - margin, (bounds, stage)
    caption = simple.evaluate("(() => { const c = document.querySelector('.friday-avatar-caption'); if (!c) return null;"
                              " const r = c.getBoundingClientRect(); return {y: r.y, h: r.height}; })()")
    if caption:
        assert bounds["y"] + bounds["h"] <= caption["y"] - margin, (bounds, caption)
    centre = stage_centre(state)
    middle = {"x": bounds["x"] + bounds["w"] / 2, "y": bounds["y"] + bounds["h"] / 2}
    assert abs(middle["x"] - centre["x"]) <= 3 * FOCUS_TOLERANCE, (bounds, centre)
    # The cards are gone from view and from the tab order; the control is not.
    assert simple.evaluate("getComputedStyle(document.querySelector('.fx-shell')).visibility") == "hidden"
    button = simple.locator(".fr-cards-toggle")
    assert button.get_attribute("aria-label") == "Show the cards"
    assert button.inner_text() == "Show cards"


def test_the_control_works_from_the_keyboard_and_keeps_focus(simple):
    simple.evaluate("FridayHolographicWorkspace.setCards('shown')")
    simple.evaluate("__avc.tray(null, false)")
    settled(simple)
    button = simple.locator(".fr-cards-toggle")
    assert button.get_attribute("aria-label") == "Hide the cards"
    # Reach it the way a keyboard user does, so :focus-visible applies.
    simple.evaluate("document.activeElement && document.activeElement.blur()")
    for _ in range(120):
        simple.keyboard.press("Tab")
        if simple.evaluate("document.activeElement.classList.contains('fr-cards-toggle')"):
            break
    else:
        raise AssertionError("Tab never reached the cards control")
    ring = simple.evaluate("(() => { const s = getComputedStyle(document.activeElement);"
                           " return {outline: s.outlineStyle, width: parseFloat(s.outlineWidth), shadow: s.boxShadow}; })()")
    assert (ring["outline"] != "none" and ring["width"] >= 2) or ring["shadow"] != "none", ("the focus ring is visible", ring)
    simple.keyboard.press("Enter")
    simple.wait_for_function("() => __avc.state().cards === 'hidden'", timeout=10000)
    assert simple.evaluate("document.activeElement.classList.contains('fr-cards-toggle')")
    assert button.get_attribute("aria-label") == "Show the cards"
    simple.keyboard.press("Space")
    simple.wait_for_function("() => __avc.state().cards === 'shown'", timeout=10000)
    assert button.get_attribute("aria-label") == "Hide the cards"
    settled(simple)
    assert simple.evaluate("getComputedStyle(document.querySelector('.fx-shell')).visibility") == "visible"


def test_showing_the_cards_puts_the_chat_back_as_it_was(simple):
    simple.evaluate("FridayHolographicWorkspace.setCards('shown')")
    simple.evaluate("__avc.tray(null, false)")
    simple.wait_for_function("() => !__avc.chat()", timeout=15000)
    simple.evaluate("FridayHolographicWorkspace.setCards('hidden')")
    simple.wait_for_function("() => __avc.chat() && __avc.chat().w > 700", timeout=15000)
    simple.evaluate("FridayHolographicWorkspace.setCards('shown')")
    simple.wait_for_function("() => !__avc.chat()", timeout=15000)
    settled(simple)


def test_classic_has_no_cards_to_close(classic):
    result = classic.evaluate("FridayHolographicWorkspace.setCards('hidden')")
    assert result["applied"] is False and result["reason"] == "classic"
    assert classic.locator(".fr-cards-toggle").count() == 0


