"""The page's half of call mode: told a call started, it lets go of the webcam,
holds the scene on its last frame and shows the chip; told the call ended, it
takes the camera back and the scene moves again; asked, it shows the choice
and posts the answer. A real browser, a fake camera, fake solutions."""
import functools
import http.server
import json
import pathlib
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("_cam", REPO / "tests" / "unit" / "test_camera_lifecycle.py")
_cam = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cam)
FAKES = _cam.FAKES


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
    httpd = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % httpd.server_address[1]
    posts = []
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                                   "--enable-unsafe-swiftshader"])
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            pg = browser.new_page(viewport={"width": 1280, "height": 800})
            pg.route("**/api/settings**", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"status":"ok","settings":{}}'))
            pg.route("**/api/machine/level", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"status":"ok","state":{"active":false}}'))

            def call_api(route):
                posts.append((route.request.url.split("/api/")[1], route.request.post_data_json or {}))
                route.fulfill(status=200, content_type="application/json", body='{"status":"ok"}')
            pg.route("**/api/call/**", call_api)
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_function("() => window.fridayDebugScene && !!fridayDebugScene().camera"
                                 " && !!window.FridayCamera && !!window.fridayCallMode", timeout=60000)
            pg.evaluate(FAKES)
            pg.evaluate("""() => {
              window.__voice = { on: false, stops: 0 };
              window.fridayVoice = Object.assign(window.fridayVoice || {}, {
                isOn: () => window.__voice.on, stop: () => { window.__voice.on = false; window.__voice.stops++; } });
              window.__frame = () => window.__fridayRenderer.info.render.frame;
              window.__chip = () => { const c = document.getElementById('call-mode-chip');
                return { shown: c.classList.contains('shown'), text: c.textContent,
                         buttons: Array.from(c.querySelectorAll('button')).map(b => b.textContent) }; };
            }""")
            yield pg, posts
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _until(pg, js, what, secs=8):
    try:
        pg.wait_for_function(js, timeout=secs * 1000)
    except Exception as exc:
        raise AssertionError("%s\n  %s" % (what, str(exc).splitlines()[0][:160]))


def _frames_advance(pg, ms=600):
    """How many frames the scene drew in a fixed window: used to show it
    holds still (zero) during a call."""
    a = pg.evaluate("__frame()")
    pg.wait_for_timeout(ms)
    return pg.evaluate("__frame()") - a


def _drawing(pg, what, secs=5):
    """The scene is drawing: a few frames land within a few seconds. A fixed
    window would race a resume on a slow software-GL frame."""
    a = pg.evaluate("__frame()")
    _until(pg, "() => __frame() > %d + 3" % a, what, secs)


def test_a_call_starting_lets_go_of_the_camera_holds_the_scene_and_shows_the_chip(page):
    pg, posts = page
    pg.evaluate("toggleHologram()")
    _until(pg, _cam.LIVE_WITH_FACE, "the camera never opened")
    pg.evaluate("window.__voice.on = true")
    _drawing(pg, "the scene was not drawing before the call")
    pg.evaluate("fridayRunActions([{ type: 'call', op: 'start', app: 'Zoom', by: 'automatic' }])")
    _until(pg, "() => FridayCamera.state.status === 'off' && !FridayCamera.stream && !isHologramMode",
           "the webcam was not released for the call", 3)
    assert pg.evaluate("__cam.lastTrack().readyState") == "ended"
    assert pg.evaluate("window.__voice.stops") == 1, "the voice session (the mic) was not stopped"
    chip = pg.evaluate("__chip()")
    assert chip["shown"] and "On a call" in chip["text"] and "Zoom" in chip["text"], chip
    assert chip["buttons"] == ["Done"]
    assert _frames_advance(pg) == 0, "the scene kept drawing during the call"
    assert pg.evaluate("__cam.status()")["shown"] is False, "no camera complaint while standing back"


def test_the_call_ending_brings_the_camera_and_the_scene_back(page):
    pg, posts = page
    calls = pg.evaluate("__cam.calls")
    pg.evaluate("fridayRunActions([{ type: 'call', op: 'end', app: 'Zoom' }])")
    _until(pg, "() => __cam.calls > %d && isHologramMode && " % calls + _cam.LIVE_WITH_FACE[6:],
           "tracking that was on did not come back after the call", 8)
    assert not pg.evaluate("__chip()")["shown"]
    _drawing(pg, "the scene did not resume after the call")
    assert pg.evaluate("window.__voice.on") is False, "voice must not auto-start after a call"


def test_the_done_button_ends_it_and_tells_the_server(page):
    pg, posts = page
    pg.evaluate("fridayRunActions([{ type: 'call', op: 'start', app: 'Teams' }])")
    _until(pg, "() => __chip().shown", "no chip", 2)
    pg.click("#call-mode-chip button")
    _until(pg, "() => !__chip().shown && !fridayCallMode.isOn()", "Done did not end call mode", 3)
    _until(pg, "() => isHologramMode", "tracking did not come back", 6)
    assert ("call/end", {}) in posts


def test_ask_mode_shows_the_choice_and_posts_the_answer(page):
    pg, posts = page
    pg.evaluate("fridayRunActions([{ type: 'call', op: 'ask', app: 'Zoom' }])")
    chip = pg.evaluate("__chip()")
    assert chip["shown"] and "Stand back?" in chip["text"] and chip["buttons"] == ["Yes", "Not now"]
    pg.click("#call-mode-chip button:nth-of-type(2)")
    _until(pg, "() => !__chip().shown", "Not now did not clear the chip", 2)
    assert ("call/decide", {"accept": False, "app": "Zoom"}) in posts
    assert not pg.evaluate("fridayCallMode.isOn()")
    pg.evaluate("fridayRunActions([{ type: 'call', op: 'ask', app: 'Zoom' }])")
    pg.click("#call-mode-chip button:nth-of-type(1)")
    _until(pg, "() => fridayCallMode.isOn() && __chip().text.indexOf('On a call') >= 0", "Yes did not stand back", 3)
    assert ("call/decide", {"accept": True, "app": "Zoom"}) in posts
    pg.evaluate("fridayRunActions([{ type: 'call', op: 'end' }])")
    _until(pg, "() => !fridayCallMode.isOn()", "end", 3)


def test_the_page_reports_which_devices_it_holds(page):
    pg, posts = page
    pg.evaluate("toggleHologram()")                              # off (it came back after the call)
    _until(pg, "() => !isHologramMode && !FridayCamera.stream", "could not turn tracking off", 3)
    seen = []
    pg.route("**/api/desktop/state", lambda r: (seen.append(r.request.post_data_json), r.fulfill(
        status=200, content_type="application/json", body='{"status":"ok","want_manifest":false}')))
    pg.evaluate("window.fridayDeskSoon(0)")
    _until(pg, "() => true", "", 1)
    pg.wait_for_timeout(500)
    assert seen and seen[-1]["state"]["devices"] == {"camera": False, "mic": False}, seen[-1:]
    pg.evaluate("toggleHologram()")
    _until(pg, "() => !!FridayCamera.stream", "camera", 5)
    pg.wait_for_timeout(1200)                                    # the camera watcher reports within 500 ms
    assert seen[-1]["state"]["devices"]["camera"] is True, seen[-1]
    pg.evaluate("toggleHologram()")
