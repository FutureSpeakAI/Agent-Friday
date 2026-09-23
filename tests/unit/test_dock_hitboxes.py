"""Clicking a dock icon must open that icon's workspace. Not the one beside it.

The dock magnifies and lifts its buttons as the pointer nears them, so a tile's
box on screen is not the box the layout gave it. Three separate things could
therefore send a click to the wrong place, and all three did:

- an icon displaced out of its own button, so the thing you aim at is not
  inside the thing that receives the click;
- a neighbour pulled toward the viewer, which under the dock's shared
  perspective both widens it and slides it sideways, far enough to cover the
  button next door — and once it covers the target, the pointer has nothing to
  move onto, so the hover latches and the target can never be reached;
- a button lifting out from under the very point that was aimed at.

Against the code these tests were written for, at full dock depth, clicking
Studio hit nothing at all, clicking Content opened Studio, and clicking Code
opened Chat; with the head turned, most of the row was unreachable.

So this drives a real browser with a real pointer: the mouse is moved onto each
icon, which produces a genuine :hover and genuine magnification through the
same mousemove path the app uses, and then it clicks and asks which workspace
opened. The dock's stylesheet and its per-frame code are lifted verbatim out of
index.html, and the workspace list is read out of DOCK_GROUPS, so what is
exercised is what ships rather than a sample of it.
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
PAGE_PATH = "/__dock_hitbox_fixture.html"

# Depth 0 is the off switch, 1 the shipped default, 2 the top of the slider.
DEPTHS = (0, 1, 2)
# Head straight on, then turned hard to each corner: the perspective origin
# moves with the head, which is what made the displacement grow.
HEADS = ((0, 0), (-1, 0), (1, 0), (-1, -1), (1, 1))


def _dock_groups():
    """The real workspace list, read from DOCK_GROUPS rather than retyped.

    It matters that this is the shipping list and not a sample. The dock is a
    row, so how far a button sits from the centre decides how badly the shared
    perspective displaces it, and with every workspace shown that row is far
    wider than a handful of buttons. Studio sits in the middle of its group
    with neighbours on both sides, so nothing about paint order protects it.
    """
    src = APP.read_text(encoding="utf-8")
    try:
        blk = src[src.index("const DOCK_GROUPS=["):src.index("const WS=DOCK_GROUPS")]
    except ValueError:                                         # pragma: no cover
        pytest.fail("DOCK_GROUPS moved in ui_parts/app.html")
    groups = []
    for name, body in re.findall(r"\{name:'([^']+)',items:\[(.*?)\]\}", blk, re.S):
        items = re.findall(r"id:'([^']+)',ico:'([^']*)',label:'([^']+)'", body)
        if items:
            groups.append((name, items))
    assert sum(len(i) for _, i in groups) > 12, (
        "parsed only %r out of DOCK_GROUPS; its shape changed"
        % [(n, len(i)) for n, i in groups]
    )
    return groups


GROUPS = _dock_groups()
IDS = [wid for _, items in GROUPS for wid, _, _ in items]


def _dock_sources():
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    assert m, "index.html has no <style> block"
    try:
        start = html.index("        //  DOCK DEPTH — head-coupled perspective")
        start = html.rindex("        // ═", 0, start)
        end = html.index("        // Persisted tracking settings on boot;", start)
    except ValueError as exc:                                  # pragma: no cover
        pytest.fail("the dock's per-frame code moved in index.html: %s" % exc)
    return m.group(1), html[start:end]


def _button(wid, ico, label, cls):
    """Mirrors the app's markup, including its icon fallback.

    When an icon file is missing the <img> hides itself and reveals the glyph
    beside it, so .ico keeps a real size either way. A zero-size icon would
    make this test measure nothing and pass for the wrong reason.
    """
    onerr = ("this.style.display='none';"
             "this.nextSibling.style.display='inline'")
    return (
        '<button class="dock-btn {cls}" data-wid="{wid}" style="position:relative">'
        '<span class="ico">'
        '<img src="assets/icons/{wid}.svg" alt="{label}" onerror="{onerr}">'
        '<span style="display:none;font-size:18px;line-height:1">{ico}</span>'
        '</span>'
        '<span>{label}</span><span class="dot"></span>'
        '</button>'
    ).format(cls=cls, wid=wid, label=label, ico=ico or label[:1], onerr=onerr)


def _fixture_page():
    css, dockjs = _dock_sources()
    parts = []
    for gi, (name, items) in enumerate(GROUPS):
        if gi:
            parts.append('<div class="dock-sep"></div>')
        inner = "".join(
            _button(w, ico, l,
                    "active" if w == IDS[0] else
                    ("suggested" if w == "studio" else ""))
            for w, ico, l in items
        )
        parts.append('<div class="dock-group"><div class="dock-group-btns">%s</div>'
                     '<div class="dock-group-label">%s</div></div>' % (inner, name))
    dock = '<div class="dock">%s</div>' % "".join(parts)

    return """<!doctype html><html><head><meta charset="utf-8">
<title>dock hitboxes</title><style>
%s
html,body{margin:0;height:100%%;overflow:hidden;background:#06080f;}
</style></head><body>
%s
<script>
window.FridayTracking = {
  cfg: {parallax_strength:1.0, depth_strength:1.0, holo_cues:0.6, dock_depth:1.0},
  hand: {x:0, y:0, seen:false}
};
var isHandTrackingMode = false;
%s
// The same line the app runs on mousemove: the dock reads real pixels.
window.addEventListener('mousemove', function (e) {
  mousePxX = e.clientX; mousePxY = e.clientY; tick();
});
var HEAD = {x:0, y:0, z:0};
function tick() { updateDock3D(HEAD.x, HEAD.y, HEAD.z); }
window.setHead = function (x, y) { HEAD.x = x; HEAD.y = y; tick(); };
window.setDepth = function (k) {
  window.FridayTracking.cfg.dock_depth = k;
  document.documentElement.style.setProperty('--dock-k', String(dockDepthK()));
  if (!dockDepthK()) resetDockVars();
  tick();
};
// What a click actually opened.
window.__clicked = null;
Array.prototype.forEach.call(document.querySelectorAll('.dock-btn'), function (b) {
  b.addEventListener('click', function () { window.__clicked = b.dataset.wid; });
});
// The centre of the icon as drawn right now — what a person aims at.
window.iconCentre = function (wid) {
  var b = document.querySelector('.dock-btn[data-wid="' + wid + '"]');
  var r = b.querySelector('.ico').getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0)) return null;
  return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)};
};
document.documentElement.style.setProperty('--dock-k', '1');
tick();
</script></body></html>""" % (css, dock, dockjs)


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the repo (so assets/icons/*.svg resolve) plus the fixture page."""

    page = ""

    def do_GET(self):                                          # noqa: N802
        if self.path.split("?")[0] == PAGE_PATH:
            body = self.page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def dock_page():
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="playwright is not installed")
    _Handler.page = _fixture_page()
    handler = functools.partial(_Handler, directory=str(REPO))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d%s" % (server.server_address[1], PAGE_PATH)
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            # Wide enough for the whole row; a dock that wrapped would be
            # measuring something else.
            page = browser.new_page(viewport={"width": 1600, "height": 900})
            page.goto(url)
            page.wait_for_selector(".dock-btn")
            yield page
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def _click_icon(page, wid):
    """Aim at the icon, let the dock react, click, report what opened.

    Moving the pointer magnifies and lifts the button, which moves the icon, so
    the aim is re-taken until it settles — the same thing a hand does.
    """
    point = None
    for _ in range(4):
        point = page.evaluate("iconCentre(%r)" % wid)
        assert point, "%s has a zero-size icon, so nothing can be aimed at" % wid
        page.mouse.move(point["x"], point["y"])
    page.evaluate("window.__clicked = null")
    page.mouse.click(point["x"], point["y"])
    return page.evaluate("window.__clicked"), point


@pytest.mark.parametrize("depth", DEPTHS)
def test_every_icon_opens_its_own_workspace(dock_page, depth):
    dock_page.evaluate("setHead(0,0)")
    dock_page.evaluate("setDepth(%d)" % depth)
    wrong = []
    for wid in IDS:
        got, point = _click_icon(dock_page, wid)
        if got != wid:
            wrong.append("%s at (%d,%d) opened %r"
                         % (wid, point["x"], point["y"], got))
    assert not wrong, (
        "at dock_depth %g, clicking the centre of these icons did not open "
        "their own workspace:\n  %s" % (depth, "\n  ".join(wrong))
    )


@pytest.mark.parametrize("hx,hy", HEADS)
def test_icons_stay_clickable_while_the_head_moves(dock_page, hx, hy):
    dock_page.evaluate("setDepth(2)")
    dock_page.evaluate("setHead(%d,%d)" % (hx, hy))
    wrong = []
    for wid in IDS:
        got, _ = _click_icon(dock_page, wid)
        if got != wid:
            wrong.append("%s opened %r" % (wid, got))
    dock_page.evaluate("setHead(0,0)")
    assert not wrong, (
        "at full depth with the head at (%d,%d), the perspective origin moves "
        "and these icons stopped opening their own workspace:\n  %s"
        % (hx, hy, "\n  ".join(wrong))
    )


@pytest.mark.parametrize("depth", DEPTHS)
def test_no_icon_hangs_outside_its_own_button(dock_page, depth):
    """The aimed-at thing must be inside the thing that takes the click.

    Checked at rest, which is where a person picks their target before moving.
    """
    dock_page.evaluate("setHead(0,0)")
    dock_page.evaluate("setDepth(%d)" % depth)
    dock_page.mouse.move(5, 5)          # pointer away from the dock
    spill = dock_page.evaluate("""
      Array.prototype.map.call(document.querySelectorAll('.dock-btn'), function (b) {
        var t = b.getBoundingClientRect();
        var i = b.querySelector('.ico').getBoundingClientRect();
        return {wid: b.dataset.wid,
                left: +(t.left - i.left).toFixed(2),
                right: +(i.right - t.right).toFixed(2)};
      })""")
    bad = ["%s hangs %.2fpx off the %s edge of its button"
           % (s["wid"], max(s["left"], s["right"]),
              "left" if s["left"] >= s["right"] else "right")
           for s in spill if s["left"] > 0.5 or s["right"] > 0.5]
    assert not bad, "at dock_depth %g:\n  %s" % (depth, "\n  ".join(bad))


def test_decorative_dock_layers_take_no_clicks():
    """A click must reach the button, never a glow, a label or a separator."""
    css = re.sub(r"/\*.*?\*/", "", _dock_sources()[0], flags=re.S)
    rules = dict(
        (sel.strip(), body)
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    )
    for sel in (".dock-btn > *", ".dock-group-label, .dock-sep, .dock-divider"):
        assert sel in rules, "the rule for %r is gone" % sel
        assert "pointer-events" in rules[sel] and "none" in rules[sel], (
            "%r no longer sets pointer-events:none, so a decorative layer can "
            "take a click meant for a button" % sel
        )
    for sel in (".dock::before", ".dock::after", ".dock-btn::before"):
        assert sel in rules, "the rule for %r is gone" % sel
        assert "pointer-events: none" in rules[sel], (
            "%r is a decorative layer and must not be clickable" % sel
        )
