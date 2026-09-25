"""The desktop has to survive being made narrow, and shrink to a widget.

The dock is a single row of 22 buttons, about 1450px of them. Below that width
nothing handled the overflow, so the row was simply clipped at both ends:
measured at 1024px it ran from -253 to 1276, putting Edition, Home, News,
Messages, Workflows, System and Settings off the screen with no way to reach
them. Under 769px an existing scrollable strip saved it, so the bug lived
exactly in the middle — the sizes a window actually gets dragged to.

These tests therefore check the thing that matters rather than the rule that
happens to implement it: at every width, is every dock button inside the
window, and does anything stick out of it.

The condensed widget is checked the same way. It is a MODE, not a width: the
avatar plus a voice control, one line of caption, and whatever is waiting on a
decision. Everything else is gone, not shrunk.
"""
import functools
import http.server
import pathlib
import re
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
APP = REPO / "ui_parts" / "app.html"

# Deliberately includes the mid-range, which is where the clipping lived.
WIDTHS = [(1600, 900), (1280, 800), (1024, 768), (900, 700), (760, 680), (420, 740)]


# ─── structural: the widget must drive the app's own controls ────────────────

def test_the_widget_presses_the_real_voice_control():
    """One voice implementation, not two.

    The widget's mic button calls .click() on the desktop's own voice button,
    so there is a single place where voice starts and stops. That needs a
    stable handle in BOTH the served page and its mirror.
    """
    for page in (INDEX, APP):
        src = page.read_text(encoding="utf-8")
        assert "data-friday-voice-toggle" in src, (
            "%s has no handle on the voice button, so the widget would have to "
            "reimplement voice" % page.name
        )
        assert "data-voice-on" in src, (
            "%s does not expose whether voice is on, so the widget cannot show "
            "its own state honestly" % page.name
        )


def test_the_caption_is_broadcast_from_both_copies():
    for page in (INDEX, APP):
        src = page.read_text(encoding="utf-8")
        assert "friday-speech" in src, (
            "%s no longer broadcasts what Friday says, so the widget's caption "
            "goes silent" % page.name
        )


def test_the_widget_has_an_installable_window_as_a_fallback():
    """Document PiP is the floating window; the PWA is the fallback."""
    mani = REPO / "static" / "widget" / "manifest.json"
    assert mani.exists(), "the widget manifest is gone"
    import json
    m = json.loads(mani.read_text(encoding="utf-8"))
    assert m.get("display") == "standalone", (
        "the widget manifest must ask for its own window, not a browser tab")
    assert m.get("start_url", "").startswith("/widget")
    routes = (REPO / "src" / "agent_friday" / "routes" / "core_routes.py").read_text(encoding="utf-8")
    assert "/widget/manifest.json" in routes and "def serve_widget" in routes


def test_document_picture_in_picture_is_actually_attempted():
    """The whole point of the widget is that it floats above other windows."""
    src = INDEX.read_text(encoding="utf-8")
    assert "documentPictureInPicture" in src
    assert "requestWindow" in src
    # The avatar is a live WebGL canvas; it stays in its own document and the
    # floating window shows its stream.
    assert "captureStream" in src, (
        "the PiP window needs the avatar; moving a live WebGL canvas between "
        "documents is not something to rely on, so it streams instead")


# ─── behavioural: drive a browser and measure ───────────────────────────────

class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


PROBE = """() => {
  const vw = innerWidth, vh = innerHeight;
  const out = {vw, vh, horizontalScroll: document.documentElement.scrollWidth > vw + 1};
  const btns = Array.from(document.querySelectorAll('.dock-btn'));
  out.dockButtons = btns.length;
  let worst = 0, off = 0;
  btns.forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 2) return;
    const over = Math.max(0, r.right - vw, -r.left);
    if (over > worst) worst = over;
    if (over > 2) off++;
  });
  out.dockWorstOverflowPx = Math.round(worst);
  out.dockButtonsOffScreen = off;
  const spill = [];
  document.querySelectorAll('body *').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    // What is VISIBLE, not what the box says. getBoundingClientRect reports an
    // element's full geometry even where an ancestor with overflow:hidden has
    // clipped it, so the raw rect flags things that nobody can actually see
    // sticking out. Intersect with every clipping ancestor first.
    let L = r.left, R = r.right, T = r.top, B = r.bottom;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const pcs = getComputedStyle(p);
      if (pcs.overflowX === 'visible' && pcs.overflowY === 'visible') continue;
      const pr = p.getBoundingClientRect();
      L = Math.max(L, pr.left); R = Math.min(R, pr.right);
      T = Math.max(T, pr.top); B = Math.min(B, pr.bottom);
    }
    if (R - L < 8 || B - T < 8) return;          // clipped away entirely
    // Only things STRADDLING an edge. A slide-in panel parked entirely
    // off-screen is how it is hidden, not a defect; half of one showing is.
    const straddles = (L < -2 && R > 2) || (L < vw - 2 && R > vw + 2);
    if (straddles)
      spill.push((el.id ? '#' + el.id : el.tagName.toLowerCase()) + '@' + Math.round(L) + '..' + Math.round(R));
  });
  out.spills = spill.slice(0, 8);
  const cv = document.getElementById('friday-scene-canvas');
  if (cv) { const r = cv.getBoundingClientRect();
    out.avatarOffCentre = Math.round(r.left + r.width / 2 - vw / 2);
    out.avatarVisible = r.width > 40 && r.height > 40; }
  return out;
}"""


@pytest.fixture(scope="module")
def desktop():
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="playwright is not installed")
    server = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % server.server_address[1]
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(
                    args=["--autoplay-policy=no-user-gesture-required"])
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            page = browser.new_page(viewport={"width": 1600, "height": 900})
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector(".dock-btn", timeout=60000)
            page.wait_for_timeout(6000)
            yield page
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("w,h", WIDTHS, ids=lambda v: str(v))
def test_every_dock_button_is_reachable_at_every_width(desktop, w, h):
    desktop.set_viewport_size({"width": w, "height": h})
    desktop.wait_for_timeout(1200)
    r = desktop.evaluate(PROBE)
    assert r["dockButtons"] > 12, "the dock lost its buttons: %r" % r
    assert r["dockButtonsOffScreen"] == 0, (
        "%d dock buttons hang off a %dpx window (worst %dpx). At 1024px this "
        "used to hide Settings among others, with no way to reach them."
        % (r["dockButtonsOffScreen"], w, r["dockWorstOverflowPx"])
    )


@pytest.mark.parametrize("w,h", WIDTHS, ids=lambda v: str(v))
def test_nothing_hangs_off_the_window(desktop, w, h):
    desktop.set_viewport_size({"width": w, "height": h})
    desktop.wait_for_timeout(1200)
    r = desktop.evaluate(PROBE)
    assert not r["horizontalScroll"], "the page scrolls sideways at %dpx" % w
    assert not r["spills"], (
        "these stick out of a %dpx window: %s" % (w, r["spills"]))


@pytest.mark.parametrize("w,h", WIDTHS, ids=lambda v: str(v))
def test_the_avatar_stays_centred_and_visible(desktop, w, h):
    desktop.set_viewport_size({"width": w, "height": h})
    desktop.wait_for_timeout(1200)
    r = desktop.evaluate(PROBE)
    assert r["avatarVisible"], "the avatar is gone at %dpx" % w
    assert abs(r["avatarOffCentre"]) <= 2, (
        "the avatar is %dpx off centre at %dpx wide" % (r["avatarOffCentre"], w))



MENU_PROBE = """label => {
  const btn = document.querySelector('button[aria-label="' + label + '"]');
  if (!btn) return {found: false};
  const menu = Array.from(btn.parentElement.children).find(c => c !== btn);
  if (!menu) return {found: true, opened: false};
  const bar = document.querySelector('.top-bar').getBoundingClientRect();
  const r = menu.getBoundingClientRect();
  const x = r.left + r.width / 2, y = Math.max(r.top, bar.bottom) + 12;
  const hit = document.elementFromPoint(x, y);
  return {found: true, opened: true, box: [Math.round(r.left), Math.round(r.top),
          Math.round(r.width), Math.round(r.height)], barBottom: Math.round(bar.bottom),
          dropsDown: r.top >= bar.bottom - 12, visible: !!hit && menu.contains(hit)};
}"""


@pytest.mark.parametrize("label", ["Scene selection", "Model quick switch"])
@pytest.mark.parametrize("w,h", [(1600, 900), (1024, 768)], ids=lambda v: str(v))
def test_the_top_bar_menus_open_where_they_can_be_seen(desktop, w, h, label):
    """A menu that drops out of the top bar is seen, not merely mounted.

    The bar's halves clip what does not fit on a narrow window. Clipping them
    vertically as well hid the scene selector's menu and the model quick
    switch's menu completely: the click opened each one and nothing appeared.
    """
    desktop.evaluate("() => { const c = window.fridayCondensed; if (c && c.exit) c.exit(); }")
    desktop.set_viewport_size({"width": w, "height": h})
    desktop.wait_for_timeout(900)
    desktop.locator('button[aria-label="%s"]' % label).click()
    desktop.wait_for_timeout(700)
    r = desktop.evaluate(MENU_PROBE, label)
    desktop.keyboard.press("Escape")
    desktop.mouse.click(w // 2, int(h * 0.45))
    desktop.wait_for_timeout(400)
    assert r.get("found"), "no %r button in the top bar" % label
    assert r.get("opened"), "%r did not open its menu at %dpx" % (label, w)
    assert r["dropsDown"], "%r's menu opens above the bar at %dpx: %r" % (label, w, r)
    assert r["visible"], "%r's menu opened but cannot be seen at %dpx: %r" % (label, w, r)

def test_condensed_mode_is_the_avatar_and_nothing_else(desktop):
    desktop.set_viewport_size({"width": 340, "height": 400})
    desktop.evaluate("window.fridayCondensed.enter()")
    desktop.wait_for_timeout(1500)
    s = desktop.evaluate("""() => {
      const shown = sel => Array.from(document.querySelectorAll(sel)).some(e => {
        const cs = getComputedStyle(e), r = e.getBoundingClientRect();
        return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 4 && r.height > 4; });
      const vis = id => { const e = document.getElementById(id);
        return !!e && getComputedStyle(e).display !== 'none'; };
      return {dock: shown('.dock'), windows: shown('.fwin'), topbar: shown('.top-bar'),
              widget: vis('condensed-ui'), bar: vis('condensed-bar'),
              lowCost: !!window.__fridayLowCost};
    }""")
    assert not s["dock"] and not s["windows"] and not s["topbar"], (
        "the widget still shows desktop furniture: %r" % s)
    assert s["widget"] and s["bar"], "the widget's own controls are missing: %r" % s
    assert s["lowCost"], (
        "the widget is still paying full render cost; it skips the "
        "post-processing pass and caps the pixel ratio")
    r = desktop.evaluate(PROBE)
    assert not r["spills"], "the widget spills: %s" % r["spills"]
    assert r["avatarVisible"] and abs(r["avatarOffCentre"]) <= 2
    desktop.evaluate("window.fridayCondensed.exit()")
    desktop.wait_for_timeout(600)


def test_a_window_dragged_to_widget_size_becomes_one(desktop):
    desktop.evaluate("window.fridayCondensed.exit()")
    desktop.set_viewport_size({"width": 1280, "height": 800})
    desktop.wait_for_timeout(900)
    assert not desktop.evaluate("() => document.body.classList.contains('condensed')")
    desktop.set_viewport_size({"width": 380, "height": 420})
    desktop.wait_for_timeout(1200)
    assert desktop.evaluate("() => document.body.classList.contains('condensed')"), (
        "a window dragged down to widget proportions should become the widget")
    # ...and give the desktop back when it grows again.
    desktop.set_viewport_size({"width": 1280, "height": 800})
    desktop.wait_for_timeout(1200)
    assert not desktop.evaluate("() => document.body.classList.contains('condensed')"), (
        "it stayed condensed after the window was made big again")
