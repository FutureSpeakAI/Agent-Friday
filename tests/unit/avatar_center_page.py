"""Shared pieces for the avatar-centring browser tests: the served page, the
probe, and the measurements that hold layout conditions constant."""
import functools
import http.server
import json
import pathlib
import socketserver
import threading
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

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
        # "Settled" is judged on the frame (the stage, or the focal point), never on
        # the projected bounds: a structure animates on its own, so its bounds
        # keep moving with the frame fixed. Use envelope() to measure those.
        steady = point and previous and all(abs(point[k] - previous[k]) < .25 for k in point if k in previous)
        if not avatar["moving"] and steady:
            since = since or time.monotonic()
            if time.monotonic() - since >= quiet:
                return state
        else:
            since = None
        previous, last = point, state
    raise AssertionError("the avatar never settled: %s" % json.dumps(last)[:600])


def envelope(page, frames=24, timeout=60000):
    """The union of the avatar's projected bounds over `frames` distinct rendered
    frames: the structure's own idle animation breathes inside the frame, so
    centring and margins are judged on the space it sweeps, not on one sample."""
    box, last = None, page.evaluate("__avc.frame()")
    for _ in range(frames):
        page.wait_for_function("f => __avc.frame() > f", arg=last, timeout=timeout)
        state = page.evaluate("__avc.state()")
        last, b = state["frame"], state.get("bounds")
        if not b:
            continue
        if box is None:
            box = {"x0": b["x"], "y0": b["y"], "x1": b["x"] + b["w"], "y1": b["y"] + b["h"]}
        else:
            box = {"x0": min(box["x0"], b["x"]), "y0": min(box["y0"], b["y"]),
                   "x1": max(box["x1"], b["x"] + b["w"]), "y1": max(box["y1"], b["y"] + b["h"])}
    assert box, "the avatar never reported projected bounds"
    return {"x": box["x0"], "y": box["y0"], "w": box["x1"] - box["x0"], "h": box["y1"] - box["y0"]}


def region_of(page, extra=()):
    """What the page really drew: the desktop below the bar, less the tray and
    anything in `extra` (rectangles {x,y,w,h,side}), computed here from the
    rendered rectangles rather than read back from the controller."""
    width, height = page.evaluate("[innerWidth, innerHeight]")
    top = page.evaluate("__avc.barBottom()")
    left, right, bottom = 0, width, height
    chat = page.evaluate("__avc.chat()")
    if chat:
        if chat["x"] + chat["w"] / 2 > width / 2:      # by where its middle is: a half-width tray starts at the middle
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
    # `unshifted` is where the camera put the focal point before the layout offset;
    # the offset is exact relative to it, so any gap shows up there.
    why = (note, focus, region, "unshifted", avatar.get("unshifted"), "scale", avatar.get("scale"), "tolerance %s px" % FOCUS_TOLERANCE)
    assert abs(focus["x"] - region["cx"]) <= FOCUS_TOLERANCE, ("x",) + why
    assert abs(focus["y"] - region["cy"]) <= FOCUS_TOLERANCE, ("y",) + why
    return state, region


def inject(page, html):
    page.evaluate("html => { const host = document.createElement('div'); host.innerHTML = html;"
                  " document.body.appendChild(host.firstElementChild); }", html)


def remove(page, selector):
    page.evaluate("s => document.querySelectorAll(s).forEach(e => e.remove())", selector)


