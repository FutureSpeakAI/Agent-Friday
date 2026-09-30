"""The webcam behind face tracking survives being interrupted.

The served page runs in a real browser with a fake camera (a canvas stream)
and fake MediaPipe solutions, so no webcam and no CDN are needed. A track that
ends mid-session must be re-acquired with backoff and the face picked up
again; while it is gone the page says so and the head eases back to neutral.
An open that fails because another app holds the device is named as such,
with the holder the server reports; a hidden tab releases the camera and a
visible one takes it back; turning tracking off releases it for good.
"""
import functools
import http.server
import pathlib
import socketserver
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"


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


FAKES = r"""
window.__FRIDAY_MEDIAPIPE_SCRIPTS__ = [];
class FakeSolution {
  constructor() { FakeSolution.made++; }
  setOptions() {}
  onResults(cb) { this.cb = cb; }
  async send() {
    FakeSolution.sends++;
    if (window.__cam.failFrames) throw new Error('engine gone');
    if (this.cb) this.cb({ detections: [{ boundingBox: { xCenter: 0.5, yCenter: 0.5, width: 0.2, height: 0.2 } }],
                           multiHandLandmarks: [] });
  }
}
FakeSolution.made = 0; FakeSolution.sends = 0;
window.FaceDetection = FakeSolution; window.Hands = FakeSolution; window.FakeSolution = FakeSolution;
const cv = document.createElement('canvas'); cv.width = 320; cv.height = 240;
const ctx = cv.getContext('2d');
setInterval(() => { ctx.fillStyle = 'rgb(' + (Date.now() % 255) + ',40,80)'; ctx.fillRect(0, 0, 320, 240); }, 33);
window.__cam = { calls: 0, fail: null, failFrames: false, streams: [] };
navigator.mediaDevices.getUserMedia = async () => {
  window.__cam.calls++;
  if (window.__cam.fail) { const e = new Error('cannot start video source'); e.name = window.__cam.fail; throw e; }
  const s = cv.captureStream(30); window.__cam.streams.push(s); return s;
};
window.__cam.lastTrack = () => { const s = window.__cam.streams[window.__cam.streams.length - 1]; return s && s.getVideoTracks()[0]; };
window.__cam.status = () => ({ status: FridayCamera.state.status, wanted: FridayCamera.state.wanted,
  text: document.getElementById('camera-status').textContent,
  shown: document.getElementById('camera-status').classList.contains('shown'),
  hasStream: !!FridayCamera.stream, calls: window.__cam.calls,
  dot: document.getElementById('camera-indicator').className,
  seen: FridayTracking.head.seen, z: FridayTracking.head.z, holo: isHologramMode });
"""


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(
        ("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
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
            pg = browser.new_page(viewport={"width": 1280, "height": 800})
            pg.route("**/api/settings**", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"status":"ok","settings":{}}'))
            pg.route("**/api/camera/holders", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body='{"status":"ok","holders":["Zoom"],"candidates":[]}'))
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_function("() => window.fridayDebugScene && !!fridayDebugScene().camera"
                                 " && !!window.FridayCamera && typeof toggleHologram === 'function'",
                                 timeout=60000)
            pg.evaluate(FAKES)
            yield pg
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _until(pg, js, what, secs=8):
    try:
        pg.wait_for_function(js, timeout=secs * 1000)
    except Exception as exc:
        raise AssertionError("%s\n  %s\n  page: %s" % (
            what, str(exc).splitlines()[0][:160], pg.evaluate("JSON.stringify(__cam.status())")))


LIVE_WITH_FACE = "() => FridayCamera.state.status === 'live' && FridayTracking.head.seen"


def test_the_camera_opens_when_the_hologram_is_turned_on_and_a_face_is_tracked(page):
    page.evaluate("toggleHologram()")
    _until(page, LIVE_WITH_FACE, "the camera never opened with a face in it")
    st = page.evaluate("__cam.status()")
    assert st["calls"] == 1 and st["holo"] is True
    assert "holo-active" in st["dot"]
    assert st["text"] == "" and not st["shown"]                # nothing to say while it works


def test_a_track_that_ends_mid_session_is_reacquired_and_the_face_comes_back(page):
    page.evaluate("__cam.lastTrack().dispatchEvent(new Event('ended'))")
    _until(page, "() => FridayCamera.state.status === 'lost'", "the ended track was not noticed", 2)
    # The face is dropped on the next rendered frame, not in the event.
    _until(page, "() => !FridayTracking.head.seen", "the face was still counted as seen", 2)
    st = page.evaluate("__cam.status()")
    assert not st["hasStream"]
    assert st["shown"] and "camera stopped" in st["text"] and "retrying" in st["text"]
    assert "holo-active" not in st["dot"]
    # The head eases back toward neutral while nothing tracks it.
    page.evaluate("FridayTracking.head.z = 0.8")
    _until(page, "() => Math.abs(FridayTracking.head.z) < 0.05", "the head did not ease back to neutral", 3)
    # Then the camera is opened again, with backoff, and the face is seen.
    _until(page, "() => __cam.calls >= 2 && " + LIVE_WITH_FACE[6:], "the camera was never re-acquired", 6)
    st = page.evaluate("__cam.status()")
    assert st["text"] == "" and not st["shown"]
    assert st["calls"] == 2


def test_a_camera_held_by_another_app_is_said_so_and_named(page):
    page.evaluate("__cam.fail = 'NotReadableError'")
    page.evaluate("__cam.lastTrack().dispatchEvent(new Event('ended'))")
    _until(page, "() => FridayCamera.state.status === 'busy'", "a not-readable open was not classified as busy", 6)
    _until(page, "() => __cam.status().text.indexOf('Zoom') >= 0", "the holder the server named never appeared", 4)
    st = page.evaluate("__cam.status()")
    assert st["text"].startswith("camera busy in another app: Zoom"), st["text"]
    assert "retrying" in st["text"]
    # When the other app lets go, the next retry gets it back without help.
    page.evaluate("__cam.fail = null")
    _until(page, LIVE_WITH_FACE, "the camera did not come back once free", 12)
    assert page.evaluate("__cam.status()")["shown"] is False


def test_a_run_of_failed_tracking_passes_reacquires_rather_than_dying_quietly(page):
    calls = page.evaluate("__cam.calls")
    page.evaluate("__cam.failFrames = true")
    _until(page, "() => FridayCamera.state.status === 'lost'", "thirty failed passes did not drop the stream", 10)
    page.evaluate("__cam.failFrames = false")
    _until(page, "() => __cam.calls > %d && " % calls + LIVE_WITH_FACE[6:], "no re-acquire after the engine came back", 8)


def test_a_hidden_tab_releases_the_camera_and_a_visible_one_takes_it_back(page):
    calls = page.evaluate("__cam.calls")
    page.evaluate("""() => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    _until(page, "() => FridayCamera.state.status === 'paused' && !FridayCamera.stream", "a hidden tab kept the camera", 2)
    assert page.evaluate("__cam.lastTrack().readyState") == "ended"
    page.evaluate("""() => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    _until(page, "() => __cam.calls > %d && " % calls + LIVE_WITH_FACE[6:], "the camera was not taken back on return", 6)


def test_turning_the_hologram_off_releases_the_camera_for_other_apps(page):
    page.evaluate("toggleHologram()")
    _until(page, "() => FridayCamera.state.status === 'off' && !FridayCamera.stream && !FridayCamera.state.wanted",
           "turning tracking off left the camera open", 2)
    assert page.evaluate("__cam.lastTrack().readyState") == "ended"
    calls = page.evaluate("__cam.calls")
    page.wait_for_timeout(2500)                                # past the first backoff
    assert page.evaluate("__cam.calls") == calls, "the camera was re-opened after it was turned off"
    assert page.evaluate("__cam.status()")["shown"] is False


def test_a_block_in_site_settings_is_reported_and_not_retried(page):
    page.evaluate("__cam.fail = 'NotAllowedError'")
    calls = page.evaluate("__cam.calls")
    page.evaluate("toggleHologram()")
    _until(page, "() => FridayCamera.state.status === 'denied'", "a permission block was not reported", 4)
    st = page.evaluate("__cam.status()")
    assert "blocked" in st["text"] and "retrying" not in st["text"]
    page.wait_for_timeout(2500)
    assert page.evaluate("__cam.calls") == calls + 1
    page.evaluate("__cam.fail = null")
    page.evaluate("toggleHologram()")                          # off again; leaves the page clean
