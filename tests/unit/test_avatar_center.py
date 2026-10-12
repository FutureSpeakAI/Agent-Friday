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

#: How far the avatar's focal point may be from the middle of the visible
#: region, in CSS pixels, once everything has settled.
FOCUS_TOLERANCE = 4.0
#: The longest a frame may take while the avatar moves, relative to the frames
#: of the same page standing still: the move must not add a stall. Software
#: rendering runs far below 60 fps, so the bound is relative, with a floor.
LONG_FRAME_FACTOR = 2.5
LONG_FRAME_FLOOR_MS = 140.0
#: A single rendered frame may carry at most this share of a move, unless the
#: renderer itself stalled for longer than STALL_MS (a snap carries all of it).
MAX_FRAME_SHARE = 0.65
STALL_MS = 160.0


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


# ── the browser ──────────────────────────────────────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d/index.html" % httpd.server_address[1]
    try:
        with sync_api.sync_playwright() as pw:
            try:
                b = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                             "--enable-unsafe-swiftshader"])
            except Exception as exc:                            # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            yield b, base
            b.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


PROBE = r"""
window.__avc = {
  frame: () => __fridayRenderer.info.render.frame,
  state: () => {
    const holo = FridayHolographicWorkspace;
    return {frame: __fridayRenderer.info.render.frame, avatar: holo.avatarFrame, spatial: holo.layoutRect,
            bounds: holo.state.projectedAvatarBounds, cards: holo.state.cards,
            target: fridayDebugScene().targetStructure, progress: fridayDebugScene().transitionProgress};
  },
  chat: () => {
    const el = document.querySelector('.chat-panel');
    const r = el && el.getBoundingClientRect();
    return el && el.classList.contains('open') ? {x: r.x, y: r.y, w: r.width, h: r.height} : null;
  },
  barBottom: () => { const b = document.querySelector('.top-bar'); return b ? Math.ceil(b.getBoundingClientRect().bottom) : 0; },
  tray: (size, visible) => window.dispatchEvent(new CustomEvent('friday:chat-tray', {detail: {size, visible}})),
  dock: () => { const d = document.querySelector('.dock'); const r = d && d.getBoundingClientRect(); return d ? {hidden: d.classList.contains('hidden'), top: r.top} : null; },
  record: (ms) => new Promise(resolve => {
    const rows = [], t0 = performance.now();
    function tick(t) {
      const s = __avc.state();
      rows.push({t, frame: s.frame, shown: s.avatar.shown, focus: s.avatar.focus || null, moving: s.avatar.moving});
      if (t - t0 < ms) requestAnimationFrame(tick); else resolve(rows);
    }
    requestAnimationFrame(tick);
  })
};
"""


def open_page(browser_and_base, style, width=1280, height=800, reduced=False, storage=None):
    browser, base = browser_and_base
    context = browser.new_context(viewport={"width": width, "height": height},
                                  reduced_motion="reduce" if reduced else "no-preference")
    page = context.new_page()
    init = "localStorage.setItem('friday.display-style.v1', %s);" % json.dumps(style)
    for key, value in (storage or {}).items():
        init += "localStorage.setItem(%s, %s);" % (json.dumps(key), json.dumps(value))
    page.add_init_script(init)
    page.route("**/api/settings**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                        body='{"status":"ok","settings":{}}'))
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_function("() => window.fridayDebugScene && !!fridayDebugScene().camera"
                           " && !!window.FridayTracking && !!window.FridayHolographicWorkspace", timeout=60000)
    page.evaluate(PROBE)
    assert page.evaluate("FridayDisplayStyle.get()") == style
    hold_dock(page, height)
    wait_for_form(page)
    if style == "classic":
        hold_still(page)
    return context, page


def hold_dock(page, height):
    """The dock's idle timer hides it after 30 s and changes the work area."""
    page.mouse.move(1, height - 1)
    page.wait_for_function("""() => {
      const dock = document.querySelector('.dock');
      if (!dock || dock.classList.contains('hidden')) return false;
      const style = getComputedStyle(dock);
      if (style.display === 'none') return true;
      const rect = dock.getBoundingClientRect();
      return rect.height > 0 && rect.top < innerHeight && style.transform === 'none';
    }""", timeout=15000)


def hold_still(page):
    """The cinematic camera drifts: structures orbit and the look direction lags
    the orbit by a few percent, so the focal point wanders around the middle of
    the screen by design. A held, neutral head (the Window view's static pose)
    stops that drift without adding any parallax, which is what a position
    assertion needs."""
    page.evaluate("FridayTracking.debugHead(0, 0, FridayTracking.head.baseline || .18)")


def wait_for_form(page):
    page.wait_for_function("() => __avc.state().progress >= .999", timeout=60000)


def fresh_frames(page, count=3, timeout=30000):
    """Wait for `count` distinct rendered frames."""
    start = page.evaluate("__avc.frame()")
    page.wait_for_function("f => __avc.frame() >= f", arg=start + count, timeout=timeout)


def settled(page, quiet=0.25, timeout=45):
    """The avatar frame has stopped, the camera has settled, and the position
    is the same across fresh rendered frames for `quiet` seconds."""
    deadline = time.monotonic() + timeout
    previous, since, last = None, None, None
    while time.monotonic() < deadline:
        page.wait_for_function("f => __avc.frame() > f", arg=page.evaluate("__avc.frame()"), timeout=15000)
        state = page.evaluate("__avc.state()")
        avatar = state["avatar"]
        point = avatar.get("focus") or (avatar.get("shown") and {"x": avatar["shown"]["x"], "y": avatar["shown"]["y"]})
        if not avatar["moving"] and point and previous and abs(point["x"] - previous["x"]) < .25 and abs(point["y"] - previous["y"]) < .25:
            since = since or time.monotonic()
            if time.monotonic() - since >= quiet:
                return state
        else:
            since = None
        previous, last = point, state
    raise AssertionError("the avatar never settled: %s" % json.dumps(last)[:600])


def region_of(page, extra=()):
    """What the page really drew: the desktop below the bar, less the tray and
    anything in `extra` (rectangles {x,y,w,h,side}), computed here from the
    rendered rectangles rather than read back from the controller."""
    width, height = page.evaluate("[innerWidth, innerHeight]")
    top = page.evaluate("__avc.barBottom()")
    left, right, bottom = 0, width, height
    chat = page.evaluate("__avc.chat()")
    if chat:
        if chat["x"] > width / 2:
            right = min(right, chat["x"])
        else:
            left = max(left, chat["x"] + chat["w"])
    for piece in extra:
        if piece["side"] == "right":
            right = min(right, piece["x"])
        else:
            left = max(left, piece["x"] + piece["w"])
    return {"x": left, "y": top, "w": right - left, "h": bottom - top,
            "cx": (left + right) / 2, "cy": (top + bottom) / 2}


def assert_centred(page, extra=(), note=""):
    state = settled(page)
    avatar = state["avatar"]
    assert avatar["mode"] == "classic", avatar
    region = region_of(page, extra)
    focus = avatar["focus"]
    assert abs(focus["x"] - region["cx"]) <= FOCUS_TOLERANCE, (note, "x", focus, region, "tolerance %s px" % FOCUS_TOLERANCE)
    assert abs(focus["y"] - region["cy"]) <= FOCUS_TOLERANCE, (note, "y", focus, region, "tolerance %s px" % FOCUS_TOLERANCE)
    return state, region


def inject(page, html):
    page.evaluate("html => { const host = document.createElement('div'); host.innerHTML = html;"
                  " document.body.appendChild(host.firstElementChild); }", html)


def remove(page, selector):
    page.evaluate("s => document.querySelectorAll(s).forEach(e => e.remove())", selector)


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


def test_every_avatar_structure_is_centred_with_the_tray_open(browser):
    context, page = open_page(browser, "classic")
    try:
        names = page.evaluate("fridayVibe.getStructures().map(s => s.id)")
        assert len(names) >= 13
        page.evaluate("__avc.tray('third', true)")
        page.wait_for_function("() => __avc.chat() && __avc.chat().w > 100", timeout=15000)
        for index, name in enumerate(names):
            page.evaluate("i => fridayVibe.setStructure(i)", index)
            page.wait_for_function("n => __avc.state().target === n && __avc.state().progress >= .999", arg=name, timeout=60000)
            assert_centred(page, note=name)
    finally:
        context.close()


# ── Simple ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def simple(browser):
    context, page = open_page(browser, "simple", width=1920, height=1000)
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
    button.focus()
    shadow = simple.evaluate("getComputedStyle(document.activeElement).boxShadow")
    assert shadow and shadow != "none", "the focus ring is visible"
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
    simple.wait_for_function("() => __avc.chat() && __avc.chat().w > 800", timeout=15000)
    simple.evaluate("FridayHolographicWorkspace.setCards('shown')")
    simple.wait_for_function("() => !__avc.chat()", timeout=15000)
    settled(simple)


def test_the_choice_is_remembered_across_a_reload(browser):
    context, page = open_page(browser, "simple", width=1920, height=1000)
    try:
        page.evaluate("FridayHolographicWorkspace.setCards('hidden')")
        saved = page.evaluate("localStorage.getItem('friday_holographic_workspace_v1')")
        assert json.loads(saved)["cards"] == "hidden"
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => window.fridayDebugScene && !!fridayDebugScene().camera && !!window.FridayHolographicWorkspace", timeout=60000)
        page.evaluate(PROBE)
        page.wait_for_function("() => __avc.state().cards === 'hidden'", timeout=15000)
        page.wait_for_function("() => __avc.chat() && __avc.chat().w > 800", timeout=30000)
        page.evaluate("FridayHolographicWorkspace.setCards('shown')")
    finally:
        context.close()


def test_classic_has_no_cards_to_close(classic):
    result = classic.evaluate("FridayHolographicWorkspace.setCards('hidden')")
    assert result["applied"] is False and result["reason"] == "classic"
    assert classic.locator(".fr-cards-toggle").count() == 0


# ── motion ───────────────────────────────────────────────────────────────────

def idle_frame_ms(page, ms=1200):
    rows = page.evaluate("ms => __avc.record(ms)", ms)
    frames = [r for i, r in enumerate(rows) if i == 0 or r["frame"] != rows[i - 1]["frame"]]
    deltas = [b["t"] - a["t"] for a, b in zip(frames, frames[1:])]
    return statistics.median(deltas) if deltas else 100.0


def rendered(rows):
    """One row per rendered frame."""
    out = []
    for r in rows:
        if not out or r["frame"] != out[-1]["frame"]:
            out.append(r)
    return out


def move_and_record(page, start_js, ms=1600):
    """Record every rendered frame for `ms` while `start_js` starts the move."""
    page.evaluate("ms => { window.__avcRows = __avc.record(ms); }", ms)
    page.evaluate(start_js)
    return rendered(page.evaluate("() => window.__avcRows"))


def series(rows, key):
    """(time, x) for each rendered frame that has the value."""
    if key == "focus":
        return [(r["t"], r["focus"]["x"]) for r in rows if r["focus"]]
    return [(r["t"], r["shown"]["x"]) for r in rows if r["shown"]]


def assert_smooth(points, idle_ms, note):
    assert len(points) >= 4, (note, "too few rendered frames to judge a move", len(points))
    times, values = [p[0] for p in points], [p[1] for p in points]
    total = abs(values[-1] - values[0])
    assert total > 40, (note, "the avatar did not move", values[0], values[-1])
    deltas = [b - a for a, b in zip(times, times[1:])]
    bound = max(LONG_FRAME_FLOOR_MS, LONG_FRAME_FACTOR * idle_ms)
    assert max(deltas) <= bound * 3, (note, "a frame stalled during the move", max(deltas), "bound", bound)
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    for step, dt in zip(steps, deltas):
        if dt < STALL_MS:
            assert step <= total * MAX_FRAME_SHARE, (note, "one frame carried most of the move", step, total, dt)
    direction = 1 if values[-1] > values[0] else -1
    back = [direction * (b - a) for a, b in zip(values, values[1:]) if direction * (b - a) < -1.0]
    assert not back, (note, "the avatar moved backwards", back[:3])


def test_classic_move_is_eased_and_does_not_stall_the_render_loop(classic):
    classic.evaluate("__avc.tray(null, false)")
    settled(classic)
    idle = idle_frame_ms(classic)
    rows = move_and_record(classic, "__avc.tray('half', true)")
    assert_smooth(series(rows, "focus"), idle, "classic, chat opens")
    settled(classic)
    rows = move_and_record(classic, "__avc.tray(null, false)")
    assert_smooth(series(rows, "focus"), idle, "classic, chat closes")


def test_simple_move_is_eased_and_does_not_stall_the_render_loop(simple):
    simple.evaluate("FridayHolographicWorkspace.setCards('shown')")
    simple.evaluate("__avc.tray(null, false)")
    simple.wait_for_function("() => !__avc.chat()", timeout=15000)
    settled(simple)
    idle = idle_frame_ms(simple)
    rows = move_and_record(simple, "FridayHolographicWorkspace.setCards('hidden')")
    assert_smooth(series(rows, "shown"), idle, "simple, cards close")
    settled(simple)
    rows = move_and_record(simple, "FridayHolographicWorkspace.setCards('shown')")
    assert_smooth(series(rows, "shown"), idle, "simple, cards return")


def test_a_change_in_mid_flight_bends_the_move_instead_of_snapping(classic):
    classic.evaluate("__avc.tray(null, false)")
    settled(classic)
    classic.evaluate("ms => { window.__avcRows = __avc.record(ms); }", 1800)
    classic.evaluate("__avc.tray('half', true)")
    classic.wait_for_timeout(150)
    classic.evaluate("__avc.tray('third', true)")
    points = series(rendered(classic.evaluate("() => window.__avcRows")), "focus")
    worst = 0.0
    for (ta, a), (tb, b) in zip(points, points[1:]):
        if tb - ta < STALL_MS:
            worst = max(worst, abs(b - a))
    # Open at half moves the focal point 320 px; a snap would carry that in one frame.
    assert worst < 320 * MAX_FRAME_SHARE, ("one frame carried most of the move", worst)
    settled(classic)
    assert_centred(classic, note="after the change of mind")
    classic.evaluate("__avc.tray(null, false)")


def test_reduced_motion_moves_more_gently_than_full_motion(browser):
    def peak(reduced):
        context, page = open_page(browser, "classic", reduced=reduced)
        try:
            page.evaluate("__avc.tray(null, false)")
            settled(page)
            points = series(move_and_record(page, "__avc.tray('half', true)", ms=2200), "focus")
            speeds = [abs(b - a) / max(1.0, tb - ta) for (ta, a), (tb, b) in zip(points, points[1:])]
            assert len(set(round(v) for _, v in points)) >= 3, "it moves in steps, not in one jump"
            settled(page)
            return max(speeds)
        finally:
            context.close()
    normal, gentle = peak(False), peak(True)
    assert gentle < normal * .85, ("reduced motion has a lower peak speed", normal, gentle)


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
    """A transition one fake-clock step at a time, so the frames are evenly
    spaced however slowly the software renderer draws them."""
    page.clock.install()
    page.evaluate(trigger_js)
    for index in range(frames):
        page.clock.run_for(step_ms)
        fresh = page.evaluate("__avc.frame()")
        page.wait_for_function("f => __avc.frame() >= f", arg=fresh, timeout=15000)
        _shot(page, directory, "%s-%02d.png" % (prefix, index))


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
        page.wait_for_timeout(600)
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
    context, page = open_page(browser, "simple", width=1920, height=1000)
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
        page.clock.resume()
        settled(page); _shot(page, out, "simple-3-split.png")
        button = page.locator(".fr-cards-toggle")
        button.focus()
        _shot(page, out, "simple-4-split-focus.png")
    finally:
        context.close()
