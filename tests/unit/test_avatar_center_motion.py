"""The avatar's moves, frame by frame, and the frames a person looks at.

These tests each open their own page, so they live apart from
test_avatar_center.py: its module-scoped pages keep rendering (software GL, one
shared CPU) and would starve a second scene.

A move is recorded with Playwright's fake clock: the page's clock advances one
step per call, the renderer draws once per step, and the page's own frame counter
proves a new frame was drawn before the state is read. So a move is always the
same sequence of frames at the same spacing, however slowly the software
renderer draws them, and nothing is read from a canvas or a stale rAF callback.
The cost of each step is timed on the wall clock: a move must not add a stall
to the render loop compared with the same page standing still.

Synthetic: no camera, no server; nothing here is evidence about how a person
judges the motion. Frames for a person are written when
FRIDAY_AVATAR_CENTER_PROOFS names an existing directory outside the checkout.
"""
import json
import os
import pathlib
import statistics
import time

import pytest

from avatar_center_page import (PROBE, LONG_FRAME_FACTOR, LONG_FRAME_FLOOR_MS, MAX_FRAME_SHARE, REPO, assert_centred, browser,  # noqa: F401
                                inject, open_page, remove, settled)

STEP_MS = 20.0

# Every test here finishes or fails well inside the suite's per-test timeout
# (300 s). A timeout there ends the whole worker process, which takes the rest
# of a parallel run with it; a budget here fails just this test, with a reason.
BUDGET_S = 150.0
_deadline = [float("inf")]

# Wall-clock frame costs depend on what else shares the CPU, so they are only
# judged in a dedicated timing run (FRIDAY_AVATAR_TIMING=1), never in the suite.
TIMING = os.environ.get("FRIDAY_AVATAR_TIMING") == "1"


@pytest.fixture(autouse=True)
def _budget():
    _deadline[0] = time.monotonic() + BUDGET_S
    yield
    _deadline[0] = float("inf")


def _left_ms():
    left = _deadline[0] - time.monotonic()
    if left <= 0:
        raise AssertionError("over this test's %d s budget: the software renderer is too slow on this machine right now" % BUDGET_S)
    return int(min(60000, left * 1000))


def step(page, ms=STEP_MS):
    """Advance the page's clock by `ms`, wait for the renderer to draw, and
    return (wall milliseconds the step took, the page's state)."""
    before = page.evaluate("__avc.frame()")
    started = time.monotonic()
    page.clock.run_for(int(round(ms)))
    page.wait_for_function("f => __avc.frame() > f", arg=before, timeout=_left_ms())
    wall = (time.monotonic() - started) * 1000
    return wall, page.evaluate("__avc.state()")


def freeze(page):
    """Take over the page's clock from here, after a real-time settle."""
    page.clock.install()


def step_until(page, done, limit=240, ms=STEP_MS):
    rows = []
    for _ in range(limit):
        wall, state = step(page, ms)
        rows.append((wall, state))
        if done(state):
            return rows
    raise AssertionError("never finished after %d steps: %s" % (limit, rows[-1][1]))


def steady(page, ms=STEP_MS, limit=300):
    """Step until the avatar frame has stopped and its position held still for
    three consecutive frames."""
    last, held = None, 0
    for _ in range(limit):
        _, state = step(page, ms)
        avatar = state["avatar"]
        point = avatar.get("focus") or avatar.get("shown")
        key = (point["x"], point["y"])
        if not avatar["moving"] and last and all(abs(a - b) < .25 for a, b in zip(key, last)):
            held += 1
            if held >= 3:
                return state
        else:
            held = 0
        last = key
    raise AssertionError("never held still: %s" % state)


def x_of(state, key):
    avatar = state["avatar"]
    return avatar["focus"]["x"] if key == "focus" else avatar["shown"]["x"]


def idle_cost(page, frames=12):
    return statistics.median(step(page)[0] for _ in range(frames))


def assert_smooth(rows, key, idle_ms, note):
    values = [x_of(state, key) for _, state in rows]
    walls = [wall for wall, _ in rows]
    assert len(values) >= 6, (note, "too few rendered frames to judge a move", len(values))
    total = abs(values[-1] - values[0])
    assert total > 40, (note, "the avatar did not move", values[0], values[-1])
    if TIMING:
        bound = max(LONG_FRAME_FLOOR_MS, LONG_FRAME_FACTOR * idle_ms)
        assert max(walls) <= bound, (note, "a frame stalled during the move", round(max(walls)), "ms; bound", round(bound), "ms; idle median", round(idle_ms), "ms")
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    assert max(steps) <= total * MAX_FRAME_SHARE, (note, "one frame carried most of the move", max(steps), total)
    direction = 1 if values[-1] > values[0] else -1
    back = [direction * (b - a) for a, b in zip(values, values[1:]) if direction * (b - a) < -1.0]
    assert not back, (note, "the avatar moved backwards", back[:3])
    # An eased move starts gently and ends gently: the first and last frames
    # carry less than the fastest.
    assert steps[0] < max(steps) and steps[-1] < max(steps), (note, steps[:3], steps[-3:])


def test_classic_move_is_eased_and_does_not_stall_the_render_loop(browser):
    context, page = open_page(browser, "classic")
    try:
        page.evaluate("__avc.tray(null, false)")
        settled(page)
        freeze(page)
        idle = idle_cost(page)
        page.evaluate("__avc.tray('half', true)")
        rows = step_until(page, lambda s: not s["avatar"]["moving"] and s["avatar"]["scale"] < 1 and s["avatar"]["focus"]["x"] < 330)
        assert_smooth(rows, "focus", idle, "classic, chat opens")
        steady(page)
        page.evaluate("__avc.tray(null, false)")
        rows = step_until(page, lambda s: not s["avatar"]["moving"] and s["avatar"]["focus"]["x"] > 630)
        assert_smooth(rows, "focus", idle, "classic, chat closes")
    finally:
        context.close()


def test_simple_move_is_eased_and_does_not_stall_the_render_loop(browser):
    context, page = open_page(browser, "simple", width=1600, height=900)
    try:
        page.evaluate("FridayHolographicWorkspace.setCards('shown')")
        page.evaluate("__avc.tray(null, false)")
        page.wait_for_function("() => !__avc.chat()", timeout=15000)
        settled(page)
        freeze(page)
        idle = idle_cost(page)
        page.evaluate("FridayHolographicWorkspace.setCards('hidden')")
        rows = step_until(page, lambda s: not s["avatar"]["moving"] and s["cards"] == "hidden" and s["avatar"]["shown"]["x"] < 100)
        assert_smooth(rows, "shown", idle, "simple, cards close")
        steady(page)
        page.evaluate("FridayHolographicWorkspace.setCards('shown')")
        rows = step_until(page, lambda s: not s["avatar"]["moving"] and s["cards"] == "shown" and s["avatar"]["shown"]["x"] > 500)
        assert_smooth(rows, "shown", idle, "simple, cards return")
    finally:
        context.close()


def test_a_change_in_mid_flight_bends_the_move_instead_of_snapping(browser):
    context, page = open_page(browser, "classic")
    try:
        page.evaluate("__avc.tray(null, false)")
        settled(page)
        freeze(page)
        page.evaluate("__avc.tray('half', true)")
        values = [x_of(step(page)[1], "focus") for _ in range(9)]       # about 180 ms into the move
        page.evaluate("__avc.tray('third', true)")
        for _ in range(120):
            _, state = step(page)
            values.append(x_of(state, "focus"))
            if not state["avatar"]["moving"] and state["avatar"]["focus"]["x"] > 400:
                break
        worst = max(abs(b - a) for a, b in zip(values, values[1:]))
        # Open at half moves the focal point 320 px; a snap would carry it in one frame.
        assert worst < 320 * MAX_FRAME_SHARE, ("one frame carried most of the move", worst)
        # The path turns without a corner: the per-frame step changes smoothly.
        steps = [b - a for a, b in zip(values, values[1:])]
        assert max(abs(b - a) for a, b in zip(steps, steps[1:])) < 40, steps
        assert abs(values[-1] - (1280 - 1280 / 3) / 2) <= 4.0, values[-1]
    finally:
        context.close()


def test_reduced_motion_moves_more_gently_than_full_motion(browser):
    def peak(reduced):
        context, page = open_page(browser, "classic", reduced=reduced)
        try:
            page.evaluate("__avc.tray(null, false)")
            settled(page)
            freeze(page)
            page.evaluate("__avc.tray('half', true)")
            rows = step_until(page, lambda s: not s["avatar"]["moving"] and s["avatar"]["focus"]["x"] < 330, limit=400)
            values = [x_of(state, "focus") for _, state in rows]
            assert len(set(round(v) for v in values)) >= 6, "it moves in steps, not in one jump"
            assert abs(values[-1] - 320) <= 4.0
            return max(abs(b - a) for a, b in zip(values, values[1:]))
        finally:
            context.close()
    normal, gentle = peak(False), peak(True)
    assert gentle < normal * .85, ("reduced motion has a lower peak speed", normal, gentle)


STRUCTURE_COUNT = 13


def test_there_are_thirteen_structures_to_centre(browser):
    context, page = open_page(browser, "classic", width=1024, height=640)
    try:
        assert page.evaluate("fridayVibe.getStructures().length") == STRUCTURE_COUNT
    finally:
        context.close()


@pytest.mark.parametrize("index", range(STRUCTURE_COUNT))
def test_each_avatar_structure_is_centred_with_the_tray_open(browser, index):
    """One structure per test, with the tray open, so each stays far inside the
    suite's per-test timeout. The page's clock is stepped in large strides so the
    structure's transition and camera glide finish in a few drawn frames; the
    position is read once it has held still."""
    context, page = open_page(browser, "classic", width=1024, height=640)
    try:
        name = page.evaluate("i => fridayVibe.getStructures()[i].id", index)
        page.evaluate("__avc.tray('third', true)")
        page.wait_for_function("() => __avc.chat() && __avc.chat().w > 100", timeout=15000)
        freeze(page)
        page.evaluate("i => fridayVibe.setStructure(i)", index)
        step_until(page, lambda s, n=name: s["target"] == n and s["progress"] >= .999, limit=200, ms=250)
        state = steady(page, ms=250)
        region = {"cx": (page.evaluate("__avc.chat().x") + 0) / 2, "cy": (page.evaluate("__avc.barBottom()") + 640) / 2}
        focus = state["avatar"]["focus"]
        assert abs(focus["x"] - region["cx"]) <= 4.0 and abs(focus["y"] - region["cy"]) <= 4.0, (name, focus, region, "tolerance 4 px")
    finally:
        context.close()


def test_the_choice_is_remembered_across_a_reload(browser):
    context, page = open_page(browser, "simple", width=1600, height=900)
    try:
        page.evaluate("FridayHolographicWorkspace.setCards('hidden')")
        saved = page.evaluate("localStorage.getItem('friday_holographic_workspace_v1')")
        assert json.loads(saved)["cards"] == "hidden"
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => window.fridayDebugScene && !!fridayDebugScene().camera && !!window.FridayHolographicWorkspace", timeout=90000)
        page.evaluate(PROBE)
        page.wait_for_function("() => __avc.state().cards === 'hidden'", timeout=15000)
        page.wait_for_function("() => __avc.chat() && __avc.chat().w > 700", timeout=30000)
        page.evaluate("FridayHolographicWorkspace.setCards('shown')")
    finally:
        context.close()


# ── frames for a person to look at ───────────────────────────────────────────

def _proofs_dir():
    value = os.environ.get("FRIDAY_AVATAR_CENTER_PROOFS")
    if not value:
        pytest.skip("FRIDAY_AVATAR_CENTER_PROOFS is not set")
    out = pathlib.Path(value)
    if not out.is_absolute() or not out.is_dir() or out.resolve().is_relative_to(REPO):
        raise ValueError("FRIDAY_AVATAR_CENTER_PROOFS must be an existing absolute directory outside the checkout")
    return out.resolve()


def _shot(page, directory, name):
    (directory / name).write_bytes(page.screenshot())


def _sequence(page, directory, prefix, trigger_js, frames=10, step_ms=40):
    """A transition `step_ms` of page time per picture."""
    freeze(page)
    page.evaluate(trigger_js)
    for index in range(frames):
        step(page, step_ms)
        _shot(page, directory, "%s-%02d.png" % (prefix, index))
    steady(page)


def test_write_the_frames_a_person_looks_at(browser):
    out = _proofs_dir()
    context, page = open_page(browser, "classic")
    try:
        page.evaluate("__avc.tray(null, false)")
        settled(page); _shot(page, out, "classic-1-idle.png")
        page.evaluate("__avc.tray('third', true)")
        page.wait_for_function("() => __avc.chat() && __avc.chat().w > 100", timeout=15000)
        settled(page); _shot(page, out, "classic-2-tray-third.png")
        page.evaluate("__avc.tray('half', true)")
        settled(page); _shot(page, out, "classic-3-tray-half.png")
        page.evaluate("__avc.tray(null, false)")
        page.wait_for_function("() => !__avc.chat()", timeout=15000)
        inject(page, '<div class="fwin" id="avc-ws" style="left:0;top:60px;width:640px;height:640px"><div class="fwin-bar"><span class="fwin-title">Workspace</span></div></div>')
        settled(page); _shot(page, out, "classic-4-workspace-left.png")
        remove(page, "#avc-ws")
        inject(page, '<div class="fwin" id="avc-ws" style="left:640px;top:60px;width:640px;height:640px"><div class="fwin-bar"><span class="fwin-title">Workspace</span></div></div>')
        settled(page); _shot(page, out, "classic-5-workspace-right.png")
        remove(page, "#avc-ws")
        settled(page)
        _sequence(page, out, "classic-seq-chat-opens", "__avc.tray('half', true)")
    finally:
        context.close()
    context, page = open_page(browser, "simple", width=1600, height=900)
    try:
        page.evaluate("FridayHolographicWorkspace.setCards('shown')")
        page.evaluate("__avc.tray(null, false)")
        settled(page); _shot(page, out, "simple-1-cards.png")
        page.evaluate("__avc.tray('third', true)")
        page.wait_for_function("() => __avc.chat() && __avc.chat().w > 100", timeout=15000)
        settled(page); _shot(page, out, "simple-2-cards-and-chat.png")
        page.evaluate("__avc.tray(null, false)")
        page.wait_for_function("() => !__avc.chat()", timeout=15000)
        settled(page)
        _sequence(page, out, "simple-seq-cards-close", "FridayHolographicWorkspace.setCards('hidden')")
        _shot(page, out, "simple-3-split.png")
        page.evaluate("document.activeElement && document.activeElement.blur()")
        for _ in range(120):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.classList.contains('fr-cards-toggle')"):
                break
        _shot(page, out, "simple-4-split-focus.png")
    finally:
        context.close()
