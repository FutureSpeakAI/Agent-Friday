"""Friday's own media player opens from every place a podcast or a recording is
opened, stays on screen while it plays, and the avatar follows the audio.

The player is the one `PodcastPlayer` the desktop mounts (index.html). The
Media workspace opens an episode card or an audio card in it (`fridayPlayMedia`,
the one open path), the News workspace's Listen opens it, and a podcast event
opens it: whichever way in, the same region with Pause, a position slider and
Close, never a bare browser control and never an external app.

The avatar's speaking state is driven by the playing audio element itself
(play, pause, ended, timeupdate), from the Media workspace, from News and from
the player, and a voice engine writing its own state every frame cannot blank it.

A real browser, the served page, stood-in reads, a real WAV.
"""
import functools
import http.server
import io
import json
import math
import os
import pathlib
import re
import socketserver
import struct
import threading
import urllib.parse
import wave

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX = REPO / "index.html"
APP = REPO / "ui_parts" / "app.html"
MEDIA_JS = REPO / "static" / "media_ws.js"
EID = "20261004T000000-test01"
FPID = "2031-03-12-evening"
RATE = 24000
SHOTS = os.environ.get("FRIDAY_SHOT_DIR")

PLAYER = '[aria-label="Podcast player"]'


def _wav(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = bytearray()
        frequencies = (220, 660, 1540)
        for i in range(int(RATE * seconds)):
            t = i / RATE
            sample = sum(math.sin(2 * math.pi * hz * t) for hz in frequencies) / len(frequencies)
            frames += struct.pack("<h", int(9000 * sample))
        w.writeframes(bytes(frames))
    return buf.getvalue()


EPISODE = {"id": EID, "title": "Test episode", "show": "The Front Page", "status": "ready",
           "duration_s": 40, "chapters": [{"title": "One", "start": 0}],
           "hosts": {"a": {"name": "Friday", "voice": "af_heart"}, "b": {"name": "Emma", "voice": "bf_emma"}},
           "lines": [{"speaker": "a", "start": 0.0, "end": 20.0, "text": "Friday's line.", "cites": []},
                     {"speaker": "b", "start": 20.0, "end": 40.0, "text": "Emma's line.", "cites": []}]}


def _card(**kw):
    base = {"status": "ready", "privacy": "private", "tags": [], "details": {}, "signed": True,
            "maker": "Local model · this PC", "when": "2031-03-12T18:00:00", "badges": [], "held": False,
            "kind": "episode", "origin": "create", "extra": {}}
    base.update(kw)
    return base


CARDS = [
    _card(id="ep-1", title="Create podcast", kind="episode", source_kind="episode", source_ref=EID),
    _card(id="ep-routine", title="The Front Page", kind="episode", source_kind="episode", source_ref=EID,
          origin="routine", extra={"routine": "front_page", "run_id": FPID}),
    _card(id="aud-1", title="Interview recording", kind="audio", source_kind="media", file_url="/api/media/aud-1/file"),
    _card(id="aud-short", title="A short clip", kind="audio", source_kind="media", file_url="/api/media/aud-short/file"),
]


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


def _audio(body):
    """The audio as the server sends it: the whole file, or the bytes asked for
    (range requests are how a browser seeks)."""
    def serve(route):
        m = re.match(r"bytes=(\d*)-(\d*)", route.request.headers.get("range", ""))
        if not m:
            return route.fulfill(status=200, content_type="audio/wav",
                                 headers={"Accept-Ranges": "bytes"}, body=body)
        start = int(m.group(1) or 0)
        end = min(int(m.group(2)) if m.group(2) else len(body) - 1, len(body) - 1)
        route.fulfill(status=206, content_type="audio/wav", body=body[start:end + 1],
                      headers={"Accept-Ranges": "bytes", "Content-Range": "bytes %d-%d/%d" % (start, end, len(body))})
    return serve


def _json(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(REPO)))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % httpd.server_address[1]
    long_wav, short_wav = _wav(40), _wav(1.5)
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                                   "--enable-unsafe-swiftshader",
                                                   "--autoplay-policy=no-user-gesture-required"])
            except Exception as exc:                           # pragma: no cover
                pytest.skip("no chromium for playwright: %s" % exc)
            pg = browser.new_page(viewport={"width": 1280, "height": 800})
            pg.route("**/api/settings**", lambda r: _json(r, {"status": "ok", "settings": {}}))
            pg.route("**/api/podcasts/now-playing", lambda r: _json(r, {"status": "ok"}))
            pg.route("**/api/podcasts/%s/audio" % EID, _audio(long_wav))
            pg.route("**/api/podcasts/%s/captions.vtt" % EID, lambda r: r.fulfill(status=200, content_type="text/vtt", body="WEBVTT\n\n"))
            pg.route("**/api/podcasts/%s" % EID, lambda r: _json(r, {"status": "ok", "episode": EPISODE}))
            pg.route("**/api/podcasts/listen", lambda r: _json(r, {"status": "ok", "episode": EPISODE}))
            pg.route("**/api/podcasts/for-run**", lambda r: _json(r, {"episode": EPISODE}))
            pg.route("**/api/news/front-page/latest", lambda r: _json(r, {
                "status": "ok", "edition": {"id": FPID, "created_at": "2031-03-12T18:00:00", "sections": [],
                                            "items": [], "headline": "Today"}, "editions": [], "routines": []}))
            pg.route("**/api/media/aud-1/file", _audio(long_wav))
            pg.route("**/api/media/aud-short/file", _audio(short_wav))

            def media_api(route):
                path = urllib.parse.urlparse(route.request.url).path
                if path == "/api/media":
                    return _json(route, {"status": "ok", "cards": CARDS, "counts": {"all": len(CARDS)},
                                         "projects": [], "collections": [], "indexing": None, "previews": None})
                cid = path.rsplit("/", 1)[-1]
                card = next((c for c in CARDS if c["id"] == cid), None)
                if card and route.request.method == "GET" and path.count("/") == 3:
                    return _json(route, dict(card, status="ok", card=card))
                return _json(route, {"status": "error", "message": "not stood in"}, 404)

            pg.route(re.compile(r".*/api/media(/[^/]+)?(\?.*)?$"), media_api)
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_function("() => window.fridayDebugScene && !!fridayDebugScene().camera", timeout=60000)
            pg.wait_for_function("() => typeof window.fridayOpenWorkspace === 'function'", timeout=60000)
            yield pg
            browser.close()
    finally:
        httpd.shutdown()


def _shot(pg, name):
    if SHOTS:
        pathlib.Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(pathlib.Path(SHOTS) / name))


def _open(pg, workspace, ready):
    pg.evaluate("w => window.fridayOpenWorkspace({ workspace: w })", workspace)
    if workspace == "media":
        pg.wait_for_selector(".md-root", timeout=30000)
        if pg.locator(".md-ql").count():
            pg.keyboard.press("Escape")
        if pg.locator('.md-root[data-view="card"]').count():
            # An earlier test left a card open: back to the Library's cards.
            pg.locator('.md-seg button:has-text("Library")').first.click()
    pg.wait_for_selector(ready, state="visible", timeout=30000)


def _card_title(pg, card_id):
    # The title, not the picture: a click on the picture opens the quick look.
    return pg.locator('.md-card[data-id="%s"] .md-title' % card_id)


def _close_player(pg):
    pg.evaluate("() => window.dispatchEvent(new CustomEvent('friday-podcast', {detail: {op: 'stop'}}))")
    pg.wait_for_selector(PLAYER, state="detached", timeout=5000)


def _signals(pg):
    return pg.evaluate("() => ({ amp: window._fridayHoloAmplitude || 0,"
                       " tts: !!(window._fridayMoodSignals && window._fridayMoodSignals.ttsActive) })")


def _assert_player_on_screen(pg, title=None):
    """The player is rendered, wholly inside the window and not covered, with
    its controls: Pause (playing), Position, Close."""
    pg.wait_for_selector(PLAYER, state="visible", timeout=20000)
    pg.wait_for_function("() => { const a = document.querySelector('%s audio');"
                         " return a && !a.paused && a.currentTime > 0.05; }" % PLAYER, timeout=20000)
    r = pg.evaluate("""() => { const p = document.querySelector('[aria-label="Podcast player"]');
        const b = p.getBoundingClientRect();
        const top = document.elementFromPoint(b.left + b.width / 2, b.top + 20);
        return { l: b.left, t: b.top, r: b.right, b: b.bottom, vw: innerWidth, vh: innerHeight,
                 hit: !!top && p.contains(top), text: p.innerText }; }""")
    assert r["l"] >= 0 and r["t"] >= 0 and r["r"] <= r["vw"] and r["b"] <= r["vh"], "player spans %r" % r
    assert r["hit"], "something else covers the player: %r" % r
    if title:
        assert title in r["text"]
    p = pg.locator(PLAYER)
    assert p.get_by_role("button", name="Pause", exact=True).is_visible()
    assert p.get_by_role("slider", name="Position").is_visible()
    assert p.get_by_role("button", name="Close the player").is_visible()


# ── Media: opening a podcast or a recording opens Friday's player ──────────────

@pytest.mark.parametrize("card_id,title,how", [("ep-1", "Test episode", "double-click"),
                                               ("ep-routine", "Test episode", "Enter"),
                                               ("aud-1", "Interview recording", "double-click")])
def test_opening_a_card_in_media_opens_the_player(page, card_id, title, how):
    _open(page, "media", ".md-card")
    if how == "Enter":
        # A routine show's episode also goes to its tab in News; the player opens too.
        page.locator('.md-card[data-id="%s"]' % card_id).focus()
        page.keyboard.press("Enter")
    else:
        _card_title(page, card_id).dblclick()
    _assert_player_on_screen(page, title)
    _close_player(page)


def test_the_player_opened_from_media_pauses_seeks_and_closes(page):
    _open(page, "media", ".md-card")
    _card_title(page, "aud-1").dblclick()
    _assert_player_on_screen(page)
    p = page.locator(PLAYER)
    p.get_by_role("button", name="Pause", exact=True).click()
    p.get_by_role("button", name="Play", exact=True).wait_for(state="visible", timeout=3000)
    assert page.evaluate("() => document.querySelector('%s audio').paused" % PLAYER)
    p.get_by_role("slider", name="Position").fill("3")
    page.wait_for_function("() => document.querySelector('%s audio').currentTime > 2.5" % PLAYER, timeout=5000)
    p.get_by_role("button", name="Close the player").click()
    page.wait_for_selector(PLAYER, state="detached", timeout=5000)


def test_the_quick_look_of_an_episode_offers_the_player_not_another_app(page):
    """A click on a card's picture opens its quick look; an episode has no file to
    preview, and used to say "open it in its app"."""
    _open(page, "media", ".md-card")
    page.locator('.md-card[data-id="ep-1"] .md-thumb').click()
    ql = page.get_by_role("dialog", name="Quick look: Create podcast")
    ql.wait_for(state="visible", timeout=10000)
    assert "open it in its app" not in ql.inner_text()
    ql.get_by_role("button", name="Play in Friday’s player").click()
    _assert_player_on_screen(page, "Test episode")
    page.keyboard.press("Escape")
    _close_player(page)


def test_enter_on_a_card_in_media_opens_the_player(page):
    _open(page, "media", ".md-card")
    page.locator('.md-card[data-id="aud-1"]').focus()
    page.keyboard.press("Enter")
    _assert_player_on_screen(page, "Interview recording")
    _close_player(page)


# ── News: Listen shows the player while the audio plays ───────────────────────

def test_news_listen_shows_the_player_while_the_audio_plays(page):
    _open(page, "news", "button:has-text('Listen to Front Page')")
    page.get_by_role("button", name="Listen to Front Page").click()
    _assert_player_on_screen(page, "Test episode")
    p = page.locator(PLAYER)
    p.get_by_role("button", name="Pause", exact=True).click()
    p.get_by_role("button", name="Play", exact=True).wait_for(state="visible", timeout=3000)
    p.get_by_role("button", name="Play", exact=True).click()
    p.get_by_role("button", name="Pause", exact=True).wait_for(state="visible", timeout=3000)
    p.get_by_role("button", name="Close the player").click()
    page.wait_for_selector(PLAYER, state="detached", timeout=5000)


HEADER_HITS = """() => {
  const p = document.querySelector('[aria-label="Podcast player"]').getBoundingClientRect();
  const roots = [...document.querySelectorAll('.ws-custom-root')].filter(r => r.getBoundingClientRect().width > 0);
  const out = [];
  roots.forEach(root => {
    const rr = root.getBoundingClientRect();
    const mid = document.elementFromPoint(rr.left + 40, rr.top + 40);
    if (!mid || !root.contains(mid)) return;   // not the workspace in front
    root.querySelectorAll('button, a, input, select, [role=tab]').forEach(b => {
      const r = b.getBoundingClientRect();
      if (!r.width || !r.height || getComputedStyle(b).visibility === 'hidden') return;
      // the header and the toolbar rows: the first 120px of the workspace root
      if (r.top > rr.top + 120) return;
      // only controls on screen: another workspace left behind is not the active one
      const at = document.elementFromPoint(Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1), Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1));
      if (at && !b.contains(at) && !at.contains(b) && !document.querySelector('[aria-label="Podcast player"]').contains(at)) return;
      const hit = r.left < p.right && r.right > p.left && r.top < p.bottom && r.bottom > p.top;
      if (hit) out.push(Math.round(r.top - rr.top) + ' ' + (b.innerText || b.getAttribute('aria-label') || b.tagName).trim().slice(0, 40));
    });
  });
  return out; }"""


def test_the_player_never_covers_the_workspace_header_or_toolbar(page):
    # News: the Listen button itself is in the toolbar.
    _entry_news(page)
    _assert_player_on_screen(page)
    assert page.evaluate(HEADER_HITS) == [], "the player covers News controls"
    page.wait_for_timeout(300)
    _shot(page, "news_player_visible.png")
    _close_player(page)
    # Media: an opened episode.
    _entry_media_episode(page)
    _assert_player_on_screen(page)
    assert page.evaluate(HEADER_HITS) == [], "the player covers Media controls"
    page.wait_for_timeout(300)
    _shot(page, "media_open_player.png")
    _close_player(page)


def test_news_listen_chip_and_nav_episode_use_the_player(page):
    """The other ways into News audio: the episode chip beside the Front Page
    toolbar, and an episode named in a News deep link."""
    _open(page, "news", "button:has-text('Listen to Front Page')")
    page.get_by_role("button", name=re.compile(r"Listen to this run")).click()
    _assert_player_on_screen(page, "Test episode")
    _close_player(page)
    page.evaluate("id => window.fridayNavigate({ workspace: 'news', tab: 'frontpage', episode: id })", EID)
    page.wait_for_selector(PLAYER, state="visible", timeout=20000)
    _close_player(page)


# ── The avatar follows the playing audio ──────────────────────────────────────

def _wait_speaking(pg, want=True):
    pg.wait_for_function("w => !!(window._fridayMoodSignals && window._fridayMoodSignals.ttsActive) === w",
                         arg=want, timeout=10000)


def _entry_media_episode(pg):
    _open(pg, "media", ".md-card")
    _card_title(pg, "ep-1").dblclick()


def _entry_media_audio(pg):
    _open(pg, "media", ".md-card")
    _card_title(pg, "aud-1").dblclick()


def _entry_news(pg):
    _open(pg, "news", "button:has-text('Listen to Front Page')")
    pg.get_by_role("button", name="Listen to Front Page").click()


def _entry_podcast_event(pg):
    pg.evaluate("id => window.dispatchEvent(new CustomEvent('friday-podcast', {detail: {op: 'play', episode_id: id}}))", EID)


@pytest.mark.parametrize("entry", [_entry_media_episode, _entry_media_audio, _entry_news, _entry_podcast_event],
                         ids=lambda f: f.__name__[7:])
def test_the_avatar_speaks_while_the_audio_plays_and_rests_when_it_pauses(page, entry):
    entry(page)
    pg = page
    pg.wait_for_selector(PLAYER, state="visible", timeout=20000)
    pg.wait_for_function("() => { const a = document.querySelector('%s audio');"
                         " return a && !a.paused && a.currentTime > 0.1 && a.currentTime < 15; }" % PLAYER, timeout=20000)
    _wait_speaking(pg, True)
    if entry is _entry_podcast_event:
        _shot(pg, "avatar_playing.png")
    pg.locator(PLAYER).get_by_role("button", name="Pause", exact=True).click()
    _wait_speaking(pg, False)
    assert _signals(pg) == {"amp": 0, "tts": False}
    pg.locator(PLAYER).get_by_role("button", name="Play", exact=True).click()
    _wait_speaking(pg, True)
    # the end of the audio puts the avatar back at rest
    pg.evaluate("() => { const a = document.querySelector('%s audio'); a.currentTime = a.duration - 0.15; }" % PLAYER)
    pg.wait_for_function("() => document.querySelector('%s audio').ended" % PLAYER, timeout=10000)
    _wait_speaking(pg, False)
    assert _signals(pg) == {"amp": 0, "tts": False}
    _close_player(pg)


def test_a_recording_playing_anywhere_in_the_page_moves_the_avatar(page):
    """Any audio element that is really playing, the Media workspace's own inline
    controls included, shows the avatar speaking, and rests when it pauses or ends."""
    page.evaluate("""async (url) => {
      const a = document.createElement('audio'); a.id = 'bare-audio'; a.src = url; a.controls = true;
      document.body.appendChild(a); await a.play(); }""", "/api/media/aud-short/file")
    _wait_speaking(page, True)
    page.evaluate("() => document.getElementById('bare-audio').pause()")
    _wait_speaking(page, False)
    assert _signals(page) == {"amp": 0, "tts": False}
    page.evaluate("async () => { const a = document.getElementById('bare-audio'); a.currentTime = 0; await a.play(); }")
    _wait_speaking(page, True)
    page.wait_for_function("() => document.getElementById('bare-audio').ended", timeout=10000)
    _wait_speaking(page, False)
    assert _signals(page) == {"amp": 0, "tts": False}
    page.evaluate("() => document.getElementById('bare-audio').remove()")


def test_a_voice_engine_writing_its_own_state_every_frame_does_not_blank_the_media_state(page):
    """The voice engine reports its (silent) state on every frame it is connected.
    Media playing at the same time is still shown: the sources are combined."""
    _entry_podcast_event(page)
    page.wait_for_function("() => { const a = document.querySelector('%s audio');"
                           " return a && !a.paused && a.currentTime > 0.1 && a.currentTime < 15; }" % PLAYER, timeout=20000)
    page.evaluate("""() => {
      const set = window.fridayAvatarSignal || ((src, speaking, amp) => {
        window._fridayHoloAmplitude = amp; (window._fridayMoodSignals = window._fridayMoodSignals || {}).ttsActive = speaking; });
      window.__voiceIdle = setInterval(() => set('voice', false, 0), 16); }""")
    try:
        page.wait_for_timeout(600)
        samples = page.evaluate("""async () => { const out = [];
          for (let i = 0; i < 20; i++) { out.push(!!window._fridayMoodSignals.ttsActive);
            await new Promise(r => setTimeout(r, 40)); } return out; }""")
        assert samples.count(True) >= 18, samples
    finally:
        page.evaluate("() => clearInterval(window.__voiceIdle)")
    page.locator(PLAYER).get_by_role("button", name="Pause", exact=True).click()
    _wait_speaking(page, False)
    _close_player(page)


# ── the source holds the same rules in both copies ────────────────────────────

def test_the_voice_engine_reports_through_the_shared_avatar_signal_in_both_copies():
    for src in (INDEX.read_text(encoding="utf-8"), APP.read_text(encoding="utf-8")):
        start = src.index("const _voiceAmpLoop")
        body = src[start:src.index("_voiceAmpLoop();", start)]
        assert "fridayAvatarSignal(" in body
        assert "ttsActive" not in body, "the voice loop writes the speaking mood only through the shared signal"


def test_media_opens_cards_through_the_one_player_path_and_declares_it():
    js = MEDIA_JS.read_text(encoding="utf-8")
    assert "window.fridayPlayMedia" in js
    assert re.search(r"__fridayNavDecls[^\n]*push\(\['media'", js), "Media declares what it can be opened to"
    decl = js[js.index("push(['media'"):]
    decl = decl[:decl.index("}]);")]
    for key in ("card", "play", "at", "view", "q"):
        assert "'%s'" % key in decl, key
    for src in (INDEX.read_text(encoding="utf-8"), APP.read_text(encoding="utf-8")):
        assert "window.fridayPlayMedia = fridayPlayMedia" in src
