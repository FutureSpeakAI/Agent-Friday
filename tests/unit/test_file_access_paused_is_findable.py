"""When every file permission is paused (an unverifiable line in the permissions
record), the way out is findable: Settings shows a banner at the top of every
tab whose button lands on Privacy & Data > File access, and the Library's
paused line links straight there. With permissions working, no banner shows.
A real browser, the served page, the permissions read stood in."""
import functools
import http.server
import json
import pathlib
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
STATE = {"suspended": True}
LIB_STATUS = {"status": "ok", "counts": {}, "scopes": [], "failures": [], "skipped": [], "reading": 0,
              "paused": True, "waiting_because": None, "vault": {"documents": 0, "unlocked": False},
              "index_encrypted": False, "index_key_protection": "", "empty": True, "kg_learn": ""}


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
            pg = browser.new_page(viewport={"width": 1440, "height": 900})
            pg.route("**/api/settings**", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"status":"ok","settings":{}}'))
            pg.route("**/api/privacy/file-grants", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"status": {"suspended": STATE["suspended"]}, "grants": [], "notices": []})))
            pg.route("**/api/library/status**", lambda r: r.fulfill(
                status=200, content_type="application/json", body=json.dumps(LIB_STATUS)))
            pg.route("**/api/library/tree**", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"status":"ok","nodes":[]}'))
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_function("() => typeof window.fridayOpenWorkspace === 'function'", timeout=60000)
            yield pg
            browser.close()
    finally:
        httpd.shutdown()


BANNER = "[data-testid=file-access-paused-banner]"


def test_paused_permissions_show_a_banner_that_lands_on_file_access(page):
    STATE["suspended"] = True
    page.evaluate("() => window.fridayOpenWorkspace({ workspace: 'settings', tab: 'general' })")
    page.wait_for_selector(BANNER, timeout=20000)
    assert "paused" in page.inner_text(BANNER).lower()
    page.click(BANNER + " button")
    page.wait_for_function("""() => { const s = document.querySelector('.st-root section[data-st-section="File access"]');
        if (!s) return false; const r = s.getBoundingClientRect(); return r.top >= 0 && r.top < window.innerHeight; }""",
                           timeout=10000)
    assert page.locator("[aria-label='Settings sections'] button", has_text="Privacy & Data").count() == 1


def test_working_permissions_show_no_banner(page):
    STATE["suspended"] = False
    page.evaluate("() => window.fridayOpenWorkspace({ workspace: 'settings', tab: 'appearance' })")
    page.wait_for_timeout(1500)
    page.evaluate("() => window.fridayOpenWorkspace({ workspace: 'settings', tab: 'general' })")
    page.wait_for_timeout(2500)
    assert page.locator(BANNER).count() == 0


def test_the_library_s_paused_line_links_to_file_access(page):
    page.evaluate("() => window.fridayOpenWorkspace({ workspace: 'library' })")
    page.wait_for_selector("[data-testid=library-open-file-access]", timeout=20000)
    page.evaluate("""() => { window.__opened = [];
        const real = window.fridayOpenWorkspace;
        window.fridayOpenWorkspace = t => { window.__opened.push(t); return real(t); }; }""")
    page.click("[data-testid=library-open-file-access]")
    opened = page.evaluate("() => window.__opened")
    assert opened and opened[-1] == {"workspace": "settings", "tab": "privacy", "section": "File access"}
