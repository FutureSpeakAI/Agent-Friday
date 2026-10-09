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
    dlg = page.get_by_role("dialog", name="Add to your Library", exact=True)
    if add.get_attribute("aria-expanded") == "true":
        dlg.get_by_role("button", name="Cancel", exact=True).click()
        dlg.wait_for(state="hidden")
    add.scroll_into_view_if_needed()
    add.click()
    dlg.wait_for(state="visible", timeout=5000)
    page.wait_for_timeout(150)
    r = page.evaluate("""() => { const e = document.querySelector("[role=dialog][aria-label='Add to your Library']");
        const b = e.getBoundingClientRect();
        return { l: b.left, t: b.top, r: b.right, b: b.bottom,
                 vw: document.documentElement.clientWidth, vh: document.documentElement.clientHeight }; }""")
    assert r["l"] >= 0 and r["t"] >= 0 and r["r"] <= r["vw"] and r["b"] <= r["vh"], (
        "at %dx%d the popup spans %r inside a %dx%d window" % (w, hgt, r, r["vw"], r["vh"]))
    # Its choices are on screen to be read and pressed.
    for label in ("Documents", "Downloads", "Desktop", "Cancel"):
        # Trial clicks retain the real hit/scrollability check without adding
        # a folder to the Library. A CSS-visible but covered choice must fail.
        dlg.get_by_role("button", name=label, exact=True).click(trial=True)
    dlg.get_by_role("button", name="Cancel", exact=True).click()
    dlg.wait_for(state="hidden")
    assert add.get_attribute("aria-expanded") == "false"
    # The fitted popup may legitimately cover its trigger. Cancel is the
    # explicit close action, and the original trigger must work again after it.
    add.click()
    dlg.wait_for(state="visible")
    page.keyboard.press("Escape")
    dlg.wait_for(state="hidden")
    assert add.get_attribute("aria-expanded") == "false"


def test_delayed_folder_choices_fit_before_observer_delivery(page):
    """A real content commit fits immediately, even before a rendering callback."""
    assistant_name = page.evaluate("window.fridayName()")
    context = page.context.browser.new_context()
    try:
        isolated = context.new_page()
        isolated.set_viewport_size({"width": 800, "height": 600})
        isolated.set_content("""<!doctype html><html><head><style>
          html,body {margin:0; font:14px/1.4 sans-serif;}
          #fixture {position:fixed; left:16px; top:450px; width:768px; height:140px;}
          button {font:inherit; min-height:32px;}
          .lb-head {flex-wrap:nowrap !important;}
        </style></head><body class="friday-experience-enabled">
          <div id="fixture" class="ws-custom-root"></div></body></html>""")
        isolated.add_style_tag(path=str(REPO / "static/friday_workspace_compositions.css"))
        for name in ("react-18.3.1.production.min.js", "react-dom-18.3.1.production.min.js"):
            isolated.add_script_tag(path=str(REPO / "static/vendor" / name))
        isolated.evaluate("""({fake, assistantName}) => {
          // Supply the naming helper from the real, served Library's app context.
          window.fridayName = () => assistantName;
          // Withhold rendering callbacks without replacing React or DOM layout.
          window.__heldFrames = new Map();
          let nextFrame = 0;
          window.requestAnimationFrame = callback => {
            window.__heldFrames.set(++nextFrame, callback); return nextFrame;
          };
          window.cancelAnimationFrame = id => window.__heldFrames.delete(id);
          window.__resizeObservers = [];
          window.ResizeObserver = class {
            constructor(callback) {this.callback = callback; this.targets = new Set();
              window.__resizeObservers.push(this);}
            observe(target) {this.targets.add(target);}
            unobserve(target) {this.targets.delete(target);}
            disconnect() {this.targets.clear();}
          };
          window.fetch = url => Promise.resolve({status:200, json:() => {
            if (url === '/api/studio-files/roots') {
              return new Promise(resolve => {window.__releaseRoots = resolve;});
            }
            const body = fake[String(url).split('?')[0]];
            if (!body) throw new Error('Unexpected component request');
            return Promise.resolve(body);
          }});
          window.__popupSnapshot = () => {
            const popup = document.querySelector('.lb-pop');
            const rect = popup.getBoundingClientRect();
            const anchor = popup.parentElement.getBoundingClientRect();
            return {l:rect.left, t:rect.top, r:rect.right, b:rect.bottom, h:rect.height,
              anchor:{x:anchor.x, y:anchor.y, w:anchor.width, h:anchor.height},
              managed:popup.style.getPropertyValue('--fr-overlay-height'),
              frames:window.__heldFrames.size,
              observed:window.__resizeObservers.some(observer => observer.targets.has(popup))};
          };
        }""", {"fake": FAKE, "assistantName": assistant_name})
        isolated.add_script_tag(path=str(REPO / "static/friday_workspace_compositions.js"))
        # The served page loads the shared See & Touch layer before any workspace (index.html); the Library
        # reads window.fridayStage when it renders.
        isolated.add_script_tag(path=str(REPO / "static/friday_stage.js"))
        isolated.add_script_tag(path=str(REPO / "static/library_ws.js"))
        isolated.evaluate("""() => {
          window.__libraryRoot = ReactDOM.createRoot(document.getElementById('fixture'));
          ReactDOM.flushSync(() => window.__libraryRoot.render(React.createElement(window.LibraryWS)));
        }""")
        # Settle initial Library status before measuring roots-driven growth.
        isolated.wait_for_function("""() =>
          document.querySelector('.lb-head [role="status"]')?.textContent === 'Nothing added yet'""",
                                   polling=10)
        isolated.evaluate("""() => ReactDOM.flushSync(() =>
          document.querySelector('button[aria-haspopup="dialog"]').click())""")
        isolated.wait_for_function("typeof window.__releaseRoots === 'function'", polling=10)
        before = isolated.evaluate("window.__popupSnapshot()")
        assert 0 <= before["l"] < before["r"] <= 800 and 0 <= before["t"] < before["b"] <= 600, before
        assert before["frames"] and before["observed"] and not before["managed"], before
        assert isolated.locator(".lb-pop button").all_text_contents() == ["Add to Library", "Cancel"]

        # This resolves AddPopup's actual request; its own state renders the roots.
        isolated.evaluate("roots => window.__releaseRoots(roots)", FAKE["/api/studio-files/roots"])
        isolated.wait_for_function("""() => document.querySelector('.lb-pop')?.textContent.includes('Desktop')""",
                                   polling=10)
        after = isolated.evaluate("window.__popupSnapshot()")
        assert isolated.locator(".lb-pop button").all_text_contents() == [
            "Documents", "Downloads", "Desktop", "Add to Library", "Cancel"]
        assert after["anchor"] == before["anchor"], (before, after)
        assert after["h"] > before["h"] and after["frames"] and after["observed"] and not after["managed"], (before, after)
        assert 0 <= after["l"] < after["r"] <= 800 and 0 <= after["t"] < after["b"] <= 600, (before, after)
    finally:
        context.close()
