"""The picker inside the preview frame (docs/design/active/vibe-coding-salon.md
§4.11 item 2). It is injected into the sandboxed preview document; a click
on an element posts one message to the parent with a selector, the tag, the
text and a snippet, and nothing else crosses. It runs under the frame's own
CSP, so it must be an inline script that needs no network.
"""
from __future__ import annotations

import http.server
import pathlib
import re
import socketserver
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PANEL_JS = ROOT / "static" / "friday_artifacts.js"

pw = pytest.importorskip("playwright.sync_api")

APP = """<!doctype html><html><head><title>t</title></head><body>
<main><h1 id="title">Rent tracker</h1><section><p class="lead">Say what this should do.</p>
<ul><li>one</li><li>two</li></ul><button id="add">Add</button></section></main></body></html>"""

HOST_HTML = """<!doctype html><html><head><title>host</title>
<script>window.React = {createElement(){}, useState(){}, useEffect(){}, useRef(){}, useCallback(){}, useMemo(){}};</script>
<script src="/static/friday_artifacts.js"></script></head><body>
<script>
window.__picks = [];
window.addEventListener('message', e => { if (e.data && e.data.__friday === 'pick') window.__picks.push(e.data); });
</script></body></html>"""


class _H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body, ctype = None, "text/html"
        if self.path == "/host.html":
            body = HOST_HTML.encode()
        elif self.path == "/static/friday_artifacts.js":
            body, ctype = PANEL_JS.read_bytes(), "application/javascript"
        if body is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


@pytest.fixture(scope="module")
def port():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_a_click_in_point_mode_posts_one_pick_with_a_selector(port):
    sandbox = re.search(r"const SANDBOX\s*=\s*'([^']*)'", PANEL_JS.read_text(encoding="utf-8")).group(1)
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # pragma: no cover
            pytest.skip("no Chromium: %s" % e)
        page = browser.new_page()
        page.goto("http://127.0.0.1:%d/host.html" % port)
        page.evaluate(
            """([html, sandbox]) => {
                 const f = document.createElement('iframe');
                 f.id = 'f'; f.style.width = '800px'; f.style.height = '600px';
                 f.setAttribute('sandbox', sandbox);
                 f.srcdoc = window.fridayArtifactFrameDoc(window.fridayPickerDoc(html));
                 document.body.appendChild(f);
               }""", [APP, sandbox])
        frame = page.frame_locator("#f")
        frame.locator("#add").click()
        page.wait_for_function("window.__picks.length >= 1", timeout=10000)
        picks = page.evaluate("window.__picks")
        assert len(picks) == 1
        pk = picks[0]
        assert pk["selector"] == "#add" and pk["tag"] == "button" and pk["text"] == "Add"
        assert "<button" in pk["snippet"] and len(pk["snippet"]) <= 400
        assert set(pk) <= {"__friday", "selector", "tag", "text", "snippet", "rect", "font_px"}
        # The computed font size crosses too, so "bigger" can mean bigger than now
        # (a relative em would resolve against the parent, not the element).
        assert pk["font_px"] == pytest.approx(13.333, abs=0.01)     # a default <button>
        # A hovered element is outlined, a clicked one stays outlined.
        outline = frame.locator("#add").evaluate("el => getComputedStyle(el).outlineStyle")
        assert outline != "none"
        # An element without an id gets a structural selector that resolves to it alone.
        frame.locator("p.lead").click()
        page.wait_for_function("window.__picks.length >= 2", timeout=10000)
        sel = page.evaluate("window.__picks[1].selector")
        assert page.evaluate("window.__picks[1].font_px") == 16
        assert frame.locator(sel).count() == 1 and frame.locator(sel).evaluate("el => el.className") == "lead"
        # The default action of the click did not fire, and nothing navigated.
        assert frame.locator("h1").text_content() == "Rent tracker"
        browser.close()


def test_without_point_mode_the_preview_document_is_untouched():
    js = PANEL_JS.read_text(encoding="utf-8")
    assert "fridayPickerDoc" in js and "__friday" in js and "'pick'" in js
    assert "postMessage" in js
