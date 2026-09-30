"""The scrollbar Friday actually draws: its width is the token, a real drag
moves it, its thumb never shrinks to a sliver, and a frame Friday builds draws
the same one. Measured in a real browser with scrollbars SHOWN (Playwright
hides them unless told not to)."""
import functools
import http.server
import pathlib
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

PROBE = """size => {
  const d = document.createElement('div');
  d.id = 'sb-probe';
  d.style.cssText = 'position:fixed;left:40px;top:120px;width:300px;height:400px;overflow:auto;'
    + 'z-index:2147483647;background:#070b14';
  d.innerHTML = '<div style="height:' + size + 'px;width:900px"></div>';
  document.body.appendChild(d);
  const r = d.getBoundingClientRect();
  return {v: d.offsetWidth - d.clientWidth, h: d.offsetHeight - d.clientHeight,
          x: r.right - (d.offsetWidth - d.clientWidth) / 2, top: r.top, height: d.clientHeight};
}"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(ignore_default_args=["--hide-scrollbars"])
            except Exception as exc:                            # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            p = browser.new_page(viewport={"width": 1400, "height": 900})
            p.goto("http://127.0.0.1:%d/index.html" % httpd.server_address[1],
                   wait_until="domcontentloaded")
            p.wait_for_selector(".dock-btn", timeout=60000)
            yield p
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _drawn_run(p, x, top, height):
    """Rows of the bar column that are drawn in the thumb's cyan."""
    png = p.screenshot(clip={"x": int(x), "y": int(top), "width": 1, "height": int(height)})
    import base64
    return p.evaluate("""async b64 => {
      const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
      const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
      const g = c.getContext('2d'); g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, img.width, img.height).data; let best = 0, cur = 0;
      for (let y = 0; y < img.height; y++) { const i = y * 4;
        const on = d[i + 2] > 60 && d[i + 1] > 45 && d[i + 2] > d[i] + 30;
        cur = on ? cur + 1 : 0; best = Math.max(best, cur); }
      return best; }""", base64.b64encode(png).decode())


def test_the_bar_is_the_token_wide_and_follows_it(page):
    token = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--scrollbar-size').trim()")
    assert token == "12px"
    m = page.evaluate(PROBE, 3000)
    assert (m["v"], m["h"]) == (12, 12), m
    page.evaluate("document.documentElement.style.setProperty('--scrollbar-size', '20px')")
    try:
        wide = page.evaluate("(() => { const d = document.getElementById('sb-probe'); "
                             "return [d.offsetWidth - d.clientWidth, d.offsetHeight - d.clientHeight]; })()")
        assert wide == [20, 20], "the bar does not read the one token"
    finally:
        page.evaluate("document.documentElement.style.removeProperty('--scrollbar-size');"
                      "document.getElementById('sb-probe').remove()")


def test_a_real_drag_scrolls(page):
    m = page.evaluate(PROBE, 3000)
    try:
        drawn = _drawn_run(page, m["x"], m["top"], m["height"])
        assert drawn >= 30, "no thumb drawn in the bar (%d rows)" % drawn
        y = m["top"] + 4 + drawn / 2
        page.mouse.move(m["x"], y)
        page.mouse.down()
        page.mouse.move(m["x"], y + 120, steps=8)
        page.mouse.up()
        moved = page.evaluate("document.getElementById('sb-probe').scrollTop")
        assert moved > 300, "dragging the thumb 120px scrolled only %s px" % moved
    finally:
        page.evaluate("document.getElementById('sb-probe').remove()")


def test_the_thumb_is_never_a_sliver(page):
    m = page.evaluate(PROBE, 400000)            # 1000x taller than its window
    try:
        drawn = _drawn_run(page, m["x"], m["top"], m["height"])
        assert drawn >= 40, "the thumb shrank to %d px" % drawn
    finally:
        page.evaluate("document.getElementById('sb-probe').remove()")


def test_a_frame_friday_builds_draws_the_same_bar(page):
    width = page.evaluate("""() => new Promise(done => {
      const f = document.createElement('iframe');
      f.style.cssText = 'position:fixed;left:400px;top:120px;width:300px;height:200px;border:0';
      f.srcdoc = fridayFrameScrollbars('<!doctype html><html><head></head><body style="margin:0">'
        + '<div style="height:3000px"></div></body></html>');
      f.onload = () => { const d = f.contentDocument.documentElement;
        const w = f.contentWindow.innerWidth - d.clientWidth; f.remove(); done(w); };
      document.body.appendChild(f);
    })""")
    assert width == 12
