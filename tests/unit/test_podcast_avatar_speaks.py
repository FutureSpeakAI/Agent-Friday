"""While a podcast plays one of Friday's own lines, the avatar shows her speaking:
the same two signals the voice and read-aloud paths set (the speaking mood and
the audio amplitude), taken from the episode's real audio. A co-host's line is
not her state, and when playback stops both signals go back to rest.
A real browser, the served page, a two-line episode with a real WAV."""
import functools
import http.server
import io
import json
import math
import pathlib
import socketserver
import struct
import threading
import wave

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
EID = "20261004T000000-test01"
RATE = 24000


def _wav() -> bytes:
    """Two 2.4 s lines of tone: Friday's, then the co-host's."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = bytearray()
        for i in range(int(RATE * 4.8)):
            t = i / RATE
            frames += struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * t) * (1 + math.sin(2 * math.pi * 3 * t)) / 2))
        w.writeframes(bytes(frames))
    return buf.getvalue()


EPISODE = {"id": EID, "title": "Test episode", "show": "The Front Page", "status": "ready",
           "duration_s": 4.8, "chapters": [{"title": "One", "start": 0}],
           "hosts": {"a": {"name": "Friday", "voice": "af_heart"}, "b": {"name": "Emma", "voice": "bf_emma"}},
           "lines": [{"speaker": "a", "start": 0.0, "end": 2.4, "text": "Friday's line.", "cites": []},
                     {"speaker": "b", "start": 2.4, "end": 4.8, "text": "Emma's line.", "cites": []}]}


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
    wav = _wav()
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                                   "--enable-unsafe-swiftshader",
                                                   "--autoplay-policy=no-user-gesture-required"])
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            pg = browser.new_page(viewport={"width": 1280, "height": 800})
            pg.route("**/api/settings**", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"status":"ok","settings":{}}'))
            pg.route("**/api/podcasts/now-playing", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"status":"ok"}'))
            pg.route("**/api/podcasts/%s/audio" % EID, lambda r: r.fulfill(
                status=200, content_type="audio/wav", body=wav))
            pg.route("**/api/podcasts/%s/captions.vtt" % EID, lambda r: r.fulfill(
                status=200, content_type="text/vtt", body="WEBVTT\n\n"))
            pg.route("**/api/podcasts/%s" % EID, lambda r: r.fulfill(
                status=200, content_type="application/json", body=json.dumps({"status": "ok", "episode": EPISODE})))
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_function("() => window.fridayDebugScene && !!fridayDebugScene().camera", timeout=60000)
            yield pg
            browser.close()
    finally:
        httpd.shutdown()


SAMPLE = """async ([ms]) => {
  const out = []; const t0 = performance.now();
  while (performance.now() - t0 < ms) {
    const a = document.querySelector('[aria-label="Podcast player"] audio');
    out.push({ t: a ? a.currentTime : -1, paused: a ? a.paused : true,
               amp: window._fridayHoloAmplitude || 0,
               tts: !!(window._fridayMoodSignals && window._fridayMoodSignals.ttsActive) });
    await new Promise(r => setTimeout(r, 50));
  }
  return out;
}"""


def test_friday_s_line_shows_her_speaking_and_the_co_host_s_does_not(page):
    page.evaluate("id => window.dispatchEvent(new CustomEvent('friday-podcast', {detail: {op: 'play', episode_id: id}}))", EID)
    page.wait_for_function("() => { const a = document.querySelector('[aria-label=\"Podcast player\"] audio');"
                           " return a && !a.paused && a.currentTime > 0.2; }", timeout=20000)
    s = page.evaluate(SAMPLE, [4200])
    hers = [x for x in s if 0.4 < x["t"] < 2.1 and not x["paused"]]
    cohost = [x for x in s if 2.9 < x["t"] < 4.5 and not x["paused"]]
    assert hers and cohost, "the episode played through both lines: %r" % s[::10]
    assert all(x["tts"] for x in hers), "Friday's line shows the speaking mood"
    assert max(x["amp"] for x in hers) > 0.05, "her speech moves the avatar: the amplitude follows the audio"
    assert not any(x["tts"] for x in cohost) and max(x["amp"] for x in cohost) == 0, \
        "the co-host's line is not Friday's state"


def test_stopping_the_episode_puts_the_avatar_back_at_rest(page):
    page.evaluate("id => window.dispatchEvent(new CustomEvent('friday-podcast', {detail: {op: 'seek', t: 0.3}}))", EID)
    page.evaluate("() => window.dispatchEvent(new CustomEvent('friday-podcast', {detail: {op: 'resume'}}))")
    page.wait_for_function("() => window._fridayMoodSignals && window._fridayMoodSignals.ttsActive", timeout=10000)
    page.evaluate("() => window.dispatchEvent(new CustomEvent('friday-podcast', {detail: {op: 'pause'}}))")
    page.wait_for_timeout(300)
    st = page.evaluate("() => ({ amp: window._fridayHoloAmplitude || 0,"
                       " tts: !!(window._fridayMoodSignals && window._fridayMoodSignals.ttsActive) })")
    assert st == {"amp": 0, "tts": False}
