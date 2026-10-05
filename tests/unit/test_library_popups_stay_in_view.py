"""The Library's "Add…" popup opens under its button, which sits at the right
end of the header: at any window size the popup is wholly inside the window,
never cut off past the right or bottom edge where its choices cannot be read.
A real browser, the served page, the Library's reads stood in."""
import functools
import http.server
import json
import pathlib
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
SIZES = [(1600, 900), (1280, 800), (1024, 700), (800, 600), (420, 760)]
FAKE = {
    # The real status shape (services/library/api.py status()), an empty Library.
    "/api/library/status": {"status": "ok", "counts": {}, "scopes": [], "failures": [], "skipped": [],
                            "reading": 0, "paused": False, "waiting_because": None,
                            "vault": {"documents": 0, "unlocked": False}, "index_encrypted": False,
                            "index_key_protection": "", "empty": True, "kg_learn": ""},
    "/api/library/tree": {"status": "ok", "nodes": []},
    "/api/studio-files/roots": {"status": "ok", "roots": [
        {"id": "documents", "label": "Documents", "available": True},
        {"id": "downloads", "label": "Downloads", "available": True},
        {"id": "desktop", "label": "Desktop", "available": True}]},
}


class _Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):                                          # noqa: N802
        if self.path.split("?")[0] == "/index.html":
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % httpd.server_address[1]
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                                   "--enable-unsafe-swiftshader"])
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            pg = browser.new_page(viewport={"width": SIZES[0][0], "height": SIZES[0][1]})
            pg.route("**/api/settings**", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"status":"ok","settings":{}}'))
            def answer(body):
                return lambda route: route.fulfill(status=200, content_type="application/json",
                                                   body=json.dumps(body))
            for path, body in FAKE.items():
                pg.route("**%s**" % path, answer(body))
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_function("() => typeof window.fridayOpenWorkspace === 'function'", timeout=60000)
            pg.evaluate("() => window.fridayOpenWorkspace({ workspace: 'library' })")
            pg.wait_for_selector("button[aria-haspopup=dialog]:has-text('Add')", timeout=30000)
            yield pg
            browser.close()
    finally:
        httpd.shutdown()


@pytest.mark.parametrize("w,hgt", SIZES)
def test_the_add_popup_is_wholly_inside_the_window(page, w, hgt):
    page.set_viewport_size({"width": w, "height": hgt})
    add = page.locator("button[aria-haspopup=dialog]:has-text('Add')").first
    if add.get_attribute("aria-expanded") == "true":
        add.click()
    add.scroll_into_view_if_needed()
    add.click()
    dlg = page.locator("[role=dialog][aria-label='Add to your Library']")
    dlg.wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(150)
    r = page.evaluate("""() => { const e = document.querySelector("[role=dialog][aria-label='Add to your Library']");
        const b = e.getBoundingClientRect();
        return { l: b.left, t: b.top, r: b.right, b: b.bottom,
                 vw: document.documentElement.clientWidth, vh: document.documentElement.clientHeight }; }""")
    assert r["l"] >= 0 and r["t"] >= 0 and r["r"] <= r["vw"] and r["b"] <= r["vh"], (
        "at %dx%d the popup spans %r inside a %dx%d window" % (w, hgt, r, r["vw"], r["vh"]))
    # Its choices are on screen to be read and pressed.
    assert page.locator("[role=dialog][aria-label='Add to your Library'] button:has-text('Cancel')").is_visible()
    add.click()
