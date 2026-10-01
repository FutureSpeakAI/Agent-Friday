"""Whatever the length of the dock, a dock button is hit where it is drawn.

The dock raises each button's icon, label and dot toward the viewer, and
Chromium hit-tests those raised layers through the dock's shared perspective
rather than the perspective of the button they belong to. So a raised icon took
clicks some way off where it was drawn: over the button to its left, by more
the further it sat from the dock's centre. On the shipped dock, at rest, a
third of News's width went to the icon of Messages, and the centre of News
missed that stretch by a few pixels. With Draft, Content and Studio retired the
row moved and the centre of News landed in it, so a click aimed at News by its
box (which is how Playwright aims, and how anything that measures before it
moves aims) reached Messages, and the two tests that open News failed.

The flat button is a plain 2D transform, which hit-tests where it is drawn, and
it holds everything drawn in it; the raised layers are drawing, not targets.
This checks the real page with the registry as shipped and shortened: at rest,
every point across every button hits that button, and a click on News opens
News.
"""
from __future__ import annotations

import functools
import http.server
import json
import pathlib
import re
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
REGISTRY = REPO / "static" / "workspace_registry.js"

#: Workspaces taken out of the registry, as a registry change would: none (as
#: shipped), the three the Media workspace retires, and a much shorter dock.
DOCKS = {
    "shipped": (),
    "media": ("draft", "content", "studio"),
    "short": ("draft", "content", "studio", "family", "health", "finance", "career", "futurespeak"),
}


def _registry_without(ids):
    text = REGISTRY.read_text(encoding="utf-8")
    for wid in ids:
        text, n = re.subn(r'\n    \{"id": "%s",.*?\]\},?' % re.escape(wid), "", text, flags=re.S)
        assert n == 1, "%s is not in the registry the way this test expects" % wid
    return text.replace("]},\n  ]", "]}\n  ]")


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with sync_api.sync_playwright() as pw:
            try:
                b = pw.chromium.launch()
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            yield b, "http://127.0.0.1:%d" % httpd.server_address[1]
            b.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _desktop(browser, retired):
    b, base = browser
    ctx = b.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    registry = _registry_without(retired)
    page.route(re.compile(r".*/static/workspace_registry\.js(\?.*)?$"), lambda r: r.fulfill(
        status=200, content_type="application/javascript", body=registry))
    page.route("**/api/desktop/events**", lambda r: r.fulfill(status=204, body=""))
    page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json",
                                                body=json.dumps({"status": "ok"})))
    page.route("**/api/settings", lambda r: r.fulfill(status=200, content_type="application/json",
                                                     body=json.dumps({"status": "ok", "settings": {}})))
    page.goto(base + "/index.html", wait_until="domcontentloaded")
    page.wait_for_selector('.dock-btn[data-ws="news"]', timeout=60000)
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(1000)
    return ctx, page, errors


# Every point across each button's row, at rest (the pointer has not moved, so
# nothing is magnified): which button does it hit?
_SCAN = """() => {
  const bs = Array.from(document.querySelectorAll('.dock-btn'));
  return bs.map(b => {
    const r = b.getBoundingClientRect(), y = r.top + r.height / 2, wrong = [];
    for (let x = Math.ceil(r.left) + 1; x < r.right - 1; x++) {
      const e = document.elementFromPoint(x, y), got = e && e.closest ? e.closest('.dock-btn') : null;
      if (got !== b) wrong.push(x + ':' + (got ? got.dataset.ws : (e ? e.tagName : 'nothing')));
    }
    return [b.dataset.ws, wrong.length, wrong.slice(0, 3)];
  });
}"""


@pytest.mark.parametrize("dock", sorted(DOCKS))
def test_every_point_of_every_dock_button_hits_that_button(browser, dock):
    ctx, page, errors = _desktop(browser, DOCKS[dock])
    try:
        rows = page.evaluate(_SCAN)
        assert len(rows) >= 10, rows
        wrong = ["%s: %d points hit something else, e.g. %s" % (w, n, ", ".join(eg)) for w, n, eg in rows if n]
        assert not wrong, "dock %r, at rest:\n  %s" % (dock, "\n  ".join(wrong))
        assert not errors, errors
    finally:
        ctx.close()


@pytest.mark.parametrize("dock", sorted(DOCKS))
def test_a_click_on_news_opens_news(browser, dock):
    ctx, page, errors = _desktop(browser, DOCKS[dock])
    try:
        page.click('.dock-btn[data-ws="news"]', timeout=10000)
        page.wait_for_selector('[data-fwin-max="news"]', timeout=10000)
        assert not errors, errors
    finally:
        ctx.close()
